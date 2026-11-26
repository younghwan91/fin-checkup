"""캐시 → 회사별 건강검진 → 등록된 페이지 전부 → 출력 폴더.

**DART 를 부르지 않는다.** 인증키를 비운 설정으로 `CheckupService` 를 만들고
`allow_fetch=False` 로 돌린다 — 캐시에 없는 회사·연도는 페이지가 생기지 않을 뿐이다.
데이터 수집은 `scripts/collect_universe.py` 의 일이다.

출력 폴더는 매번 비우고 다시 쓴다(지난 빌드의 회사가 남지 않게). 실수로 다른 폴더를
지우지 않도록, 비어 있지 않은 폴더는 빌드가 남긴 표식(`MARKER`)이 있을 때만 비운다.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
from multiprocessing import get_context
from pathlib import Path

from fin_checkup.clock import kst_now, kst_today
from fin_checkup.config import Settings
from fin_checkup.models import CorpCode
from fin_checkup.quarter import latest_quarters
from fin_checkup.service import Checkup, CheckupService
from fin_checkup.site import incremental
from fin_checkup.site.buildinfo import (
    BUILD_JSON,
    SnapshotInfo,
    build_json,
    data_years,
    dump,
    freshness_warning,
    git_sha,
)
from fin_checkup.site.context import OG_IMAGE_PAGE, CompanyEntry, Site, safe_output_path
from fin_checkup.site.disclosures import DisclosureFeed, as_feed
from fin_checkup.site.images.fonts import INSTALL_HINT, FontSet, resolve_fonts
from fin_checkup.site.incremental import BuildCache
from fin_checkup.site.registry import Output, load_pages
from fin_checkup.site.render import make_env
from fin_checkup.site.sitemap import crawl_outputs, is_indexable
from fin_checkup.storage import Cache

MARKER = ".fin-checkup-site"
#: 추이·급변에 쓰는 연수. 캐시에 있는 해만큼만 쓰인다.
HISTORY_YEARS = 5
#: `--jobs` 를 쓰는 최소 일감 (일꾼당 회사 수). 프로세스를 띄우는 값(~1초)보다 계산이 짧으면 손해다.
PARALLEL_MIN_PER_JOB = 25

Progress = Callable[[int, int], None]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuildReport:
    companies: int
    #: 상장사·재무가 있는데 건강검진을 못 만든 회사 (종목코드 형식 이상 포함).
    skipped: int
    files: int
    load_seconds: float
    render_seconds: float
    #: 페이지 이름 → (파일 수, 걸린 초). 공유 카드 PNG 처럼 무거운 페이지의 시간을 따로 본다.
    pages: dict[str, tuple[int, float]] = field(default_factory=dict)
    #: PNG 를 못 만든 이유 (글꼴 없음). 만들었거나 --no-images 면 빈 문자열.
    image_warning: str = ""
    #: 빌드는 됐지만 사람이 봐야 할 것 (오래된 사업연도 등 — buildinfo.freshness_warning).
    warnings: list[str] = field(default_factory=list)
    #: `--incremental` 일 때 캐시 재사용 요약. 아니면 빈 문자열.
    cache_summary: str = ""

    @property
    def per_company_ms(self) -> float:
        if not self.companies:
            return 0.0
        return (self.load_seconds + self.render_seconds) / self.companies * 1000


def _offline_service(cache: Cache) -> CheckupService:
    """인증키 없는 서비스. 캐시 미스가 DART 호출로 번질 길을 아예 없앤다."""
    return CheckupService(cache, settings=Settings(_env_file=None, dart_api_key=""))


def _valid_stock_code(code: str) -> bool:
    # KRX 단축코드는 6자리 영숫자(신형은 영문 포함). 경로에 들어가므로 엄격히 본다.
    return len(code) == 6 and code.isascii() and code.isalnum() and code.upper() == code


def _checkup_key(fingerprint: str, year: int, corp: CorpCode) -> str:
    return incremental.key(
        "checkup", incremental.code_version(), fingerprint, year, HISTORY_YEARS,
        corp.corp_code, corp.stock_code, corp.corp_name,
    )


async def _run_corps(service: CheckupService, corps: list[CorpCode], year: int) -> list[Checkup | None]:
    results: list[Checkup | None] = []
    for corp in corps:
        data = None
        if _valid_stock_code(corp.stock_code):
            data = await service.run(corp, end_year=year, years=HISTORY_YEARS, allow_fetch=False)
        results.append(data)
    return results


def _run_chunk(db_path: str, corps: list[CorpCode], year: int) -> list[Checkup | None]:
    """`--jobs` 일꾼 프로세스. 같은 DB 를 읽기 전용으로 따로 연다 (읽기 전용이면 여럿이 함께 열 수 있다)."""
    with Cache(db_path, read_only=True) as cache:
        return asyncio.run(_run_corps(_offline_service(cache), corps, year))


def _run_parallel(
    cache: Cache, corps: list[CorpCode], year: int, jobs: int, done: Callable[[int], None]
) -> list[Checkup | None]:
    # 덩어리를 일꾼 수보다 잘게 — 늦게 끝나는 덩어리 하나가 전체를 붙잡지 않게.
    size = max(1, len(corps) // (jobs * 4))
    chunks = [corps[i : i + size] for i in range(0, len(corps), size)]
    results: list[list[Checkup | None] | None] = [None] * len(chunks)
    with ProcessPoolExecutor(max_workers=jobs, mp_context=get_context("spawn")) as pool:
        futures = {
            pool.submit(_run_chunk, str(cache.db_path), chunk, year): i for i, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            i = futures[future]
            results[i] = future.result()
            done(len(chunks[i]))
    return [data for chunk in results for data in chunk or []]


async def collect_companies(
    cache: Cache,
    year: int,
    limit: int | None = None,
    progress: Progress | None = None,
    jobs: int = 1,
    build_cache: BuildCache | None = None,
) -> tuple[list[CompanyEntry], int]:
    """(페이지를 만들 회사, 건너뛴 수). 캐시만 읽는다.

    `build_cache` 가 있으면 열쇠(코드 판·데이터 지문·회사)가 같은 건강검진은 다시 계산하지 않는다.
    `jobs` > 1 이고 DB 를 읽기 전용으로 열었으면 남은 회사를 프로세스 여러 개로 나눠 계산한다.
    """
    corps = cache.list_listed_with_statements(year)
    if limit is not None:
        corps = corps[:limit]

    finished = 0

    def done(n: int) -> None:
        nonlocal finished
        for _ in range(n):
            finished += 1
            if progress is not None:
                progress(finished, len(corps))

    results: dict[str, Checkup | None] = {}
    keys: dict[str, str] = {}
    if build_cache is not None:
        fingerprint = incremental.data_fingerprint(cache)
        for corp in corps:
            keys[corp.corp_code] = _checkup_key(fingerprint, year, corp)
            cached = build_cache.get_object(keys[corp.corp_code])
            if cached is not incremental.MISSING:
                results[corp.corp_code] = cached
                done(1)

    todo = [c for c in corps if c.corp_code not in results]
    if jobs > 1 and cache.read_only and len(todo) >= jobs * PARALLEL_MIN_PER_JOB:
        computed = _run_parallel(cache, todo, year, jobs, done)
    else:
        if jobs > 1 and todo and not cache.read_only:
            log.info("DB 를 쓰기 모드로 열어 --jobs 를 쓰지 않는다 (다른 프로세스가 열 수 없다)")
        service = _offline_service(cache)
        computed = []
        for corp in todo:
            computed += await _run_corps(service, [corp], year)
            done(1)
    for corp, data in zip(todo, computed, strict=True):
        results[corp.corp_code] = data
        if build_cache is not None:
            build_cache.put_object(keys[corp.corp_code], data)

    entries: list[CompanyEntry] = []
    skipped = 0
    for corp in corps:
        data = results[corp.corp_code]
        if data is None or data.history[-1].bsns_year != year:
            skipped += 1
        else:
            entries.append(CompanyEntry(corp=corp, checkup=data))
    return entries, skipped


def _prepare_out(out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        if not (out / MARKER).exists():
            raise FileExistsError(
                f"{out} 가 비어 있지 않고 사이트 빌드 폴더 표식({MARKER})이 없다. "
                "다른 폴더를 지우지 않도록 멈춘다 — 빈 폴더나 새 경로를 지정하세요."
            )
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER).write_text("fin-checkup site build output — 빌드마다 통째로 지워진다.\n")


def _write(out: Path, output: Output) -> None:
    target = out / safe_output_path(output.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(output.content, bytes):
        target.write_bytes(output.content)
    else:
        target.write_text(output.content, encoding="utf-8")


def _image_fonts(images: bool, fonts: FontSet | None) -> tuple[FontSet | None, str]:
    """(PNG 에 쓸 글꼴, 못 만드는 이유). --no-images 면 이유 없이 None."""
    if not images:
        return None, ""
    fonts = fonts or resolve_fonts()
    if fonts is None:
        return None, f"공유 카드 PNG 를 건너뛴다(og:image 도 내지 않는다) — {INSTALL_HINT}"
    return fonts, ""


async def build_site(
    cache: Cache,
    out: Path,
    year: int,
    limit: int | None = None,
    base_url: str = "/",
    built_on: date | None = None,
    progress: Progress | None = None,
    images: bool = True,
    image_workers: int = 1,
    fonts: FontSet | None = None,
    disclosures: DisclosureFeed | dict | None = None,
    built_at: datetime | None = None,
    snapshot: SnapshotInfo | None = None,
    jobs: int = 1,
    incremental_dir: Path | None = None,
) -> BuildReport:
    """images=False 거나 한국어 글꼴이 없으면 PNG 페이지(`OG_IMAGE_PAGE`)를 이번 빌드에서 뺀다 — 두부(□)
    그림을 내느니 없는 편이 낫고, 그림이 없으면 og:image 도 내지 않는다."""
    started = time.perf_counter()
    built_at = built_at or kst_now()
    build_cache = BuildCache(incremental_dir) if incremental_dir is not None else None
    entries, skipped = await collect_companies(cache, year, limit, progress, jobs, build_cache)
    loaded = time.perf_counter()

    pages = dict(load_pages())
    image_fonts, image_warning = _image_fonts(images, fonts)
    if image_fonts is None:
        pages.pop(OG_IMAGE_PAGE, None)
    if image_warning:
        log.warning(image_warning)
    site = Site(
        year=year,
        built_on=built_on or kst_today(),
        env=make_env(),
        companies=entries,
        base_url=base_url,
        og_images=OG_IMAGE_PAGE in pages,
        fonts=image_fonts,
        image_workers=image_workers,
        disclosures=as_feed(disclosures),
        snapshot=snapshot,
        build_cache=build_cache,
        quarters=latest_quarters(cache.key_account_rows([e.corp.corp_code for e in entries])),
    )
    _prepare_out(out)
    files = 0
    indexable: list[str] = []
    timings: dict[str, tuple[int, float]] = {}
    for name, builder in sorted(pages.items()):
        page_started, page_files = time.perf_counter(), 0
        for output in builder(site):
            _write(out, output)
            if is_indexable(output):
                indexable.append(output.path)
            page_files += 1
        timings[name] = (page_files, time.perf_counter() - page_started)
        files += page_files
    # sitemap 은 다른 페이지가 전부 나온 뒤에야 만들 수 있다 — 레지스트리 밖, 마지막에.
    for output in crawl_outputs(site, indexable):
        _write(out, output)
        files += 1
    # build.json 은 맨 마지막 — 자기 자신까지 센 개수를 싣는다.
    files += 1
    counts = {"companies": len(entries), "skipped": skipped, "pages": pages_written(out), "files": files}
    _write(out, Output(BUILD_JSON, dump(build_json(site, built_at, git_sha(), counts))))
    stale = freshness_warning(max(data_years(entries), default=None), site.built_on)
    cache_summary = ""
    if build_cache is not None:
        removed = build_cache.prune()
        cache_summary = f"{build_cache.summary()} · 안 쓴 객체 {removed:,}개 지움"

    return BuildReport(
        companies=len(entries),
        skipped=skipped,
        files=files,
        load_seconds=loaded - started,
        render_seconds=time.perf_counter() - loaded,
        pages=timings,
        image_warning=image_warning,
        warnings=[stale] if stale else [],
        cache_summary=cache_summary,
    )


def pages_written(out: Path) -> int:
    return sum(1 for _ in out.rglob("*.html"))
