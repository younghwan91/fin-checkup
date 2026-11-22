"""개인 DART 인증키로 그때그때 받아 오는 출처 — 사이트에 없는 회사·사업연도용.

공개 사이트는 회사마다 최신 사업연도 하나만 싣는다. 사용자가 `fin-checkup setup` 으로 자기 키를 넣었으면,
사이트에 없는 것만 DART 에서 받아(`CheckupService(allow_fetch=True)`) 개인 캐시(DuckDB)에 넣고, 사이트와 같은
모양의 JSON(`site.pages.api.company_json`)으로 돌려준다 — 판정을 새로 만들지 않는다. 결과에는
`source: "dart-live"` 가 붙는다.

- 키는 환경변수 / `.env` / 사용자 설정 파일(`Settings`)에서만 읽는다. 도구 입력으로 받지 않고, 응답·오류 문장에
  싣지 않는다(DART 오류는 status 와 DART 가 준 문장만, 네트워크 오류는 종류 이름만).
- 일별 호출 한도를 지킨다: 오늘 쓴 양(개인 캐시의 호출 기록) + 이번에 쓸 수 있는 최대치가 한도를 넘으면 부르지 않고,
  020 이 오면 그대로 멈춘다(`RateLimitExceeded`, 재시도 없음).
- DuckDB 는 쓰는 프로세스가 하나뿐이다. 받을 때만 열고 곧 닫는다 — CLI·수집과 오래 다투지 않게.
- 동종업계 중앙값은 개인 캐시에 다른 회사가 있어야 나온다. 없으면 null 그대로(지어내지 않는다).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from fin_checkup.mcp.client import Fetched, NotFound, SiteSource, SourceError

logger = logging.getLogger(__name__)

#: 한 번에 받는 사업연도 수 — 사이트 빌드와 같다(성장성·위험 신호가 지난 해들을 본다).
LIVE_YEARS = 5
#: 회사 하나를 받을 때 최대 호출 수: 연도마다 연결·개별 2번 + 기업개황 1 + 고유번호 목록 1.
LIVE_WORST_CALLS = LIVE_YEARS * 2 + 2
#: DART 전체 재무제표가 있는 첫 사업연도.
FIRST_YEAR = 2015
LIVE_NOTE = (
    "이 진단은 개인 DART 인증키로 DART 에서 바로 받아 계산했습니다(source: dart-live). 판정 규칙은 사이트와 같습니다. "
    "동종업계 중앙값(peer)은 개인 캐시에 같은 업종 회사가 있어야 나오며, 없으면 비어 있습니다."
)


class DartLiveSource:
    """사이트를 먼저 보고, 없는 회사·연도는 DART 에서 받는다. `always_live` 면 진단은 늘 DART 에서."""

    def __init__(
        self,
        site: SiteSource,
        settings: Any,
        db_path: Path | str,
        *,
        always_live: bool = False,
    ) -> None:
        self.site = site
        self.settings = settings
        self.db_path = Path(db_path)
        self.always_live = always_live
        self.mode = "dart" if always_live else "site+dart"
        self.base_url = site.base_url
        self._lock = asyncio.Lock()
        self._index: Fetched | None = None
        self._refreshed = False

    @property
    def has_key(self) -> bool:
        return bool(self.settings.has_api_key)

    # ── Source 프로토콜 ───────────────────────────────────────────

    async def search_index(self) -> Fetched:
        """사이트 색인 + (키가 있으면) 개인 캐시의 고유번호 목록 중 사이트에 없는 회사."""
        if self._index is not None and time.time() - self._index.fetched_at < self.site.ttl:
            return self._index
        site_index: Fetched | None = None
        try:
            site_index = await self.site.search_index()
        except SourceError:
            if not self.has_key:
                raise
        companies: list[dict[str, Any]] = []
        if site_index is not None and isinstance(site_index.data, dict):
            companies = [c for c in site_index.data.get("companies") or [] if isinstance(c, dict)]
        extra: list[dict[str, Any]] = []
        if self.has_key:
            try:
                extra = await self._extra_companies({str(c.get("code")) for c in companies})
            except SourceError as exc:
                if site_index is None:
                    raise
                logger.warning("[mcp] 개인 캐시 회사 목록을 더하지 못했다: %s", exc)
        data = {
            **(site_index.data if site_index and isinstance(site_index.data, dict) else {}),
            "companies": companies + extra,
        }
        fetched_at = site_index.fetched_at if site_index else time.time()
        self._index = Fetched(data, fetched_at, stale=bool(site_index and site_index.stale))
        return self._index

    async def company(self, stock_code: str, year: int | None = None) -> Fetched:
        if not self.always_live:
            try:
                fetched = await self.site.company(stock_code, year)
            except NotFound:
                fetched = None
            except SourceError:
                if not self.has_key:
                    raise
                fetched = None
            if fetched is not None and (year is None or fetched.data.get("fiscal_year") == year):
                return fetched
        return await self.live_company(stock_code, year)

    async def build_info(self) -> Fetched:
        return await self.site.build_info()

    async def disclosures(self) -> Fetched:
        return await self.site.disclosures()

    async def aclose(self) -> None:
        await self.site.aclose()

    # ── DART ────────────────────────────────────────────────────

    def _require_key(self) -> None:
        if not self.has_key:
            raise SourceError(
                "DART 인증키가 설정되지 않아 DART 에서 받아 올 수 없습니다. 사용자가 직접 터미널에서 "
                "`fin-checkup setup` 을 실행해 키를 넣고 클라이언트를 다시 시작하면 됩니다 (setup_guide 도구 참고). "
                "키를 채팅으로 받지 마세요."
            )

    def _open(self) -> Any:
        from fin_checkup.storage import Cache
        from fin_checkup.storage.db import CacheLocked

        try:
            return Cache(self.db_path)
        except CacheLocked as exc:
            raise SourceError(
                f"개인 캐시({self.db_path})를 다른 프로세스가 쓰고 있어 열지 못했습니다. 그 작업이 끝난 뒤 다시 시도하세요."
            ) from exc

    def _service(self, cache: Any) -> Any:
        from fin_checkup.service import CheckupService

        return CheckupService(cache, settings=self.settings)

    def _check_budget(self, service: Any) -> None:
        used, quota = service.dart_budget()
        if used + LIVE_WORST_CALLS > quota:
            raise SourceError(
                f"오늘 DART 호출을 {used:,}건 써서 한도({quota:,}건)에 가깝습니다. 더 부르지 않습니다 — "
                "한도는 자정(KST)에 다시 찹니다."
            )

    async def _guarded(self, work: Any) -> Any:
        from fin_checkup.dart.client import DartError, RateLimitExceeded

        try:
            return await work
        except RateLimitExceeded as exc:
            raise SourceError(
                "DART 가 오늘 호출 한도를 넘었다고 답했습니다 (status 020). 한도는 자정(KST)에 다시 찹니다."
            ) from exc
        except DartError as exc:
            hint = (
                " 인증키를 확인하세요 — 사용자가 직접 `fin-checkup setup --check` 를 실행하면 됩니다."
                if exc.status in {"010", "011", "012", "901"}
                else ""
            )
            raise SourceError(f"DART 오류 (status {exc.status}): {exc.message}.{hint}") from exc
        except httpx.HTTPError as exc:
            # 예외 문장은 요청 URL(=crtfc_key)을 담을 수 있다 — 종류 이름만.
            raise SourceError(
                f"DART 에 연결하지 못했습니다 ({type(exc).__name__}). 잠시 뒤 다시 시도하세요."
            ) from exc

    async def _extra_companies(self, known: set[str]) -> list[dict[str, Any]]:
        """개인 캐시에 이미 있는 고유번호 목록 중 사이트에 없는 회사. DART 를 부르지 않는다."""
        from fin_checkup.site.hangul import chosung

        if not self.db_path.is_file():
            return []
        async with self._lock:
            cache = self._open()
            try:
                corps = cache.listed_corp_codes()
            finally:
                cache.close()
        return [
            {
                "name": c.corp_name,
                "code": c.stock_code,
                "market": None,
                "sector": None,
                "chosung": chosung(c.corp_name).lower(),
                "on_site": False,
            }
            for c in corps
            if c.stock_code not in known
        ]

    async def refresh_companies(self) -> bool:
        """사이트 색인에서 못 찾았을 때만 부른다 — DART 고유번호 목록(7일마다 1번)을 받아 색인에 더한다.

        새로 더한 게 있으면 True. 프로세스마다 한 번만 시도한다(못 찾는 이름마다 다시 받지 않게).
        """
        if not self.has_key or self._refreshed:
            return False
        self._refreshed = True
        async with self._lock:
            cache = self._open()
            try:
                service = self._service(cache)
                if cache.count_corp_codes() == 0:
                    self._check_budget(service)
                await self._guarded(service.ensure_corp_codes())
            finally:
                cache.close()
        self._index = None
        return True

    async def live_company(self, stock_code: str, year: int | None = None) -> Fetched:
        """DART 에서 받아 계산한 회사 JSON (사이트 JSON 과 같은 스키마 + source/live_note)."""
        from fin_checkup.clock import kst_today
        from fin_checkup.fiscal import default_fiscal_year
        from fin_checkup.site.context import CompanyEntry
        from fin_checkup.site.pages.api import company_json

        self._require_key()
        latest = default_fiscal_year()
        if year is not None and not FIRST_YEAR <= year <= latest + 1:
            raise NotFound(
                f"DART 전체 재무제표는 {FIRST_YEAR} 사업연도부터입니다 ({year} 사업연도는 받을 수 없습니다)"
            )
        candidates = [year] if year is not None else [latest, latest - 1]

        async with self._lock:
            cache = self._open()
            try:
                service = self._service(cache)
                self._check_budget(service)
                await self._guarded(service.ensure_corp_codes())
                corp = cache.find_by_stock_code(stock_code)
                if corp is None:
                    raise NotFound(f"DART 고유번호 목록에 종목코드 {stock_code} 가 없다")
                data = None
                for end_year in candidates:
                    self._check_budget(service)
                    data = await self._guarded(
                        service.run(corp, end_year=end_year, years=LIVE_YEARS, allow_fetch=True)
                    )
                    if data is not None and data.history[-1].bsns_year == end_year:
                        break
                    data = None
                if data is None:
                    raise NotFound(
                        f"DART 에 {corp.corp_name} 의 {'/'.join(map(str, candidates))} 사업연도 사업보고서 재무가 없습니다"
                    )
            finally:
                cache.close()

        doc = company_json(CompanyEntry(corp=corp, checkup=data), SimpleNamespace(built_on=kst_today()))  # type: ignore[arg-type]
        doc["source"] = "dart-live"
        doc["live_note"] = LIVE_NOTE
        return Fetched(doc, time.time(), url="https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json")

    def cache_years(self) -> list[int]:
        if not self.db_path.is_file():
            return []
        from fin_checkup.storage import Cache
        from fin_checkup.storage.db import CacheLocked

        try:
            with Cache(self.db_path, read_only=True) as cache:
                return cache.statement_years()
        except (CacheLocked, Exception):  # noqa: BLE001 — 상태 보고용, 못 열면 빈 목록
            return []
