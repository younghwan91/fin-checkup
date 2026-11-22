"""MCP 서버의 데이터 출처 — 공개 정적 사이트(기본) 또는 로컬 DuckDB 캐시.

기본(`SiteSource`)은 설정이 필요 없다. 사이트가 매일 빌드해 올리는 JSON 을 읽는다:
`/search-index.json`(회사 찾기) · `/api/c/<코드>.json`(진단) · `/build.json`(신선도) ·
`/data/disclosures.json`(최근 위험 공시). DART 인증키가 없어도 된다.

캐시는 두 겹이다. 메모리(같은 프로세스) + 디스크(`~/.cache/fin-checkup-mcp`, 프로세스를 다시 띄워도).
TTL(기본 600초, 사이트의 `Cache-Control: max-age=600` 과 같다)이 지나면 `If-None-Match`(ETag) 로
다시 묻고 304 면 그대로 쓴다. 네트워크가 끊겼는데 예전에 받은 사본이 있으면 그 사본을 쓰되 `stale` 로
알린다 — 없는 값을 지어내지 않으려면 '오래된 사실' 과 '모름' 을 구분해야 한다.

개인 DART 인증키가 설정돼 있으면(`fin-checkup setup`) 사이트에 없는 회사·연도만 `mcp.live.DartLiveSource` 가
DART 에서 받는다. `FIN_CHECKUP_MCP_SOURCE=local` 이면 `LocalSource` 가 `CheckupService(allow_fetch=False)` 로 같은 모양의
JSON 을 만든다 (`site.pages.api.company_json` 그대로 — 사이트와 다른 판정을 만들지 않게).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://younghwan91.github.io/fin-checkup/"
#: 사이트의 `Cache-Control: max-age=600` 과 같은 값.
DEFAULT_TTL_SECONDS = 600.0
#: 디스크 캐시 파일 상한. 회사 JSON 하나가 ~13KB 라 이 정도면 수 MB 다.
DISK_CACHE_MAX_FILES = 400
USER_AGENT = "fin-checkup-mcp/0.1 (+https://github.com/younghwan91/fin-checkup)"


class SourceError(RuntimeError):
    """데이터를 가져오지 못했다. 메시지는 사용자에게 그대로 보여 줄 수 있는 문장이다."""


class NotFound(SourceError):
    """그 경로(회사 JSON 등)가 출처에 없다 — 네트워크 장애와 구분한다."""


@dataclass(frozen=True)
class Fetched:
    data: Any
    #: 받은 시각(epoch 초).
    fetched_at: float
    #: 새로 확인하지 못해 예전 사본을 쓴 경우.
    stale: bool = False
    url: str = ""


class Source(Protocol):
    """도구가 쓰는 출처. 모든 문서는 사이트 JSON 과 같은 스키마다 (`docs/api-static.md`)."""

    mode: str
    base_url: str

    async def search_index(self) -> Fetched: ...

    async def company(self, stock_code: str, year: int | None = None) -> Fetched: ...

    async def build_info(self) -> Fetched: ...

    async def disclosures(self) -> Fetched: ...

    async def aclose(self) -> None: ...


def default_cache_dir(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ if env is None else env)
    explicit = env.get("FIN_CHECKUP_MCP_CACHE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    base = env.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "fin-checkup-mcp"


def _normalize_base(url: str) -> str:
    return url if url.endswith("/") else url + "/"


class SiteSource:
    """공개 정적 사이트에서 JSON 을 읽는다. 메모리+디스크 캐시, ETag 재검증."""

    mode = "site"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        cache_dir: Path | None = None,
        ttl: float = DEFAULT_TTL_SECONDS,
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = _normalize_base(base_url)
        self.cache_dir = cache_dir
        self.ttl = ttl
        self._clock = clock
        self._timeout = timeout
        self._http = http
        self._owns_http = http is None
        self._memory: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ── 공개 메서드 ────────────────────────────────────────────────

    async def search_index(self) -> Fetched:
        return await self.get_json("search-index.json")

    async def company(self, stock_code: str, year: int | None = None) -> Fetched:
        # 사이트는 최신 사업연도 하나만 싣는다. 다른 연도는 core 가 '없다' 고 답한다.
        return await self.get_json(f"api/c/{stock_code}.json")

    async def build_info(self) -> Fetched:
        return await self.get_json("build.json")

    async def disclosures(self) -> Fetched:
        return await self.get_json("data/disclosures.json")

    async def aclose(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    def url_for(self, path: str) -> str:
        return self.base_url + path.lstrip("/")

    # ── 캐시 ─────────────────────────────────────────────────────

    async def get_json(self, path: str) -> Fetched:
        url = self.url_for(path)
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            return await self._get(url)

    async def _get(self, url: str) -> Fetched:
        now = self._clock()
        entry = self._memory.get(url) or self._read_disk(url)
        if entry is not None:
            self._memory[url] = entry
            if now - entry["fetched_at"] < self.ttl:
                return Fetched(entry["data"], entry["fetched_at"], url=url)

        headers = {"Accept": "application/json"}
        if entry is not None:
            if entry.get("etag"):
                headers["If-None-Match"] = entry["etag"]
            if entry.get("last_modified"):
                headers["If-Modified-Since"] = entry["last_modified"]

        try:
            response = await self._client().get(url, headers=headers)
        except httpx.HTTPError as exc:
            return self._fallback(url, entry, f"연결하지 못했다 ({type(exc).__name__})")

        if response.status_code == 304 and entry is not None:
            entry = {**entry, "fetched_at": now}
            self._remember(url, entry)
            return Fetched(entry["data"], now, url=url)
        if response.status_code == 404:
            raise NotFound(f"{url} 이(가) 없다 (404)")
        if response.status_code >= 400:
            return self._fallback(url, entry, f"HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            return self._fallback(url, entry, "JSON 이 아닌 응답")

        entry = {
            "url": url,
            "etag": response.headers.get("etag"),
            "last_modified": response.headers.get("last-modified"),
            "fetched_at": now,
            "data": data,
        }
        self._remember(url, entry)
        return Fetched(data, now, url=url)

    def _fallback(self, url: str, entry: dict[str, Any] | None, reason: str) -> Fetched:
        if entry is not None:
            logger.warning("[mcp] %s 새로 받지 못해 예전 사본을 쓴다: %s", url, reason)
            return Fetched(entry["data"], entry["fetched_at"], stale=True, url=url)
        raise SourceError(
            f"fin-checkup 공개 사이트({self.base_url})에서 데이터를 받지 못했습니다: {reason}. "
            "네트워크 연결을 확인하거나 잠시 뒤 다시 시도하세요. 값을 추정해 채우지 않습니다."
        )

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        return self._http

    def _disk_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _read_disk(self, url: str) -> dict[str, Any] | None:
        path = self._disk_path(url)
        if path is None or not path.is_file():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(entry, dict) or entry.get("url") != url or "data" not in entry:
            return None
        return entry

    def _remember(self, url: str, entry: dict[str, Any]) -> None:
        self._memory[url] = entry
        path = self._disk_path(url)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
            self._prune()
        except OSError:
            # 디스크 캐시는 덤이다. 못 써도 조회는 된다.
            logger.debug("[mcp] 디스크 캐시 쓰기 실패", exc_info=True)

    def _prune(self) -> None:
        assert self.cache_dir is not None
        files = sorted(self.cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for old in files[: max(0, len(files) - DISK_CACHE_MAX_FILES)]:
            old.unlink(missing_ok=True)


class LocalSource:
    """로컬 DuckDB 캐시에서 사이트와 같은 모양의 JSON 을 만든다. DART 를 부르지 않는다.

    읽기 전용으로 연다 — DuckDB 는 writer 가 하나라, `serve`·수집이 돌고 있으면 열리지 않는다(그때 메시지로 알린다).
    """

    mode = "local"

    def __init__(self, db_path: Path | str, *, base_url: str = DEFAULT_BASE_URL, cache: Any = None) -> None:
        self.db_path = Path(db_path)
        self.base_url = _normalize_base(base_url)
        self._cache = cache
        self._owns_cache = cache is None
        self._year: int | None = None
        self._index: Fetched | None = None

    def _open(self) -> Any:
        if self._cache is None:
            from fin_checkup.storage import Cache

            if not self.db_path.is_file():
                raise SourceError(
                    f"로컬 캐시 DB 가 없습니다: {self.db_path}. FIN_CHECKUP_DB_PATH 로 위치를 알려 주거나, "
                    "FIN_CHECKUP_MCP_SOURCE 를 비워 공개 사이트를 쓰세요."
                )
            try:
                self._cache = Cache(self.db_path, read_only=True)
            except Exception as exc:  # duckdb.IOException 등 — 다른 프로세스가 쓰는 중
                raise SourceError(
                    f"로컬 캐시 DB 를 열지 못했습니다({type(exc).__name__}). 다른 프로세스(serve·수집)가 "
                    "쓰고 있으면 끝난 뒤 다시 시도하세요."
                ) from exc
        return self._cache

    def _service(self) -> Any:
        from fin_checkup.config import Settings
        from fin_checkup.service import CheckupService

        # 인증키를 비운 설정 — 캐시에 없는 회사·연도는 '없다' 로 끝난다 (site.build._offline_service 와 같다).
        return CheckupService(self._open(), settings=Settings(_env_file=None, dart_api_key=""))

    def latest_year(self) -> int:
        if self._year is None:
            from fin_checkup.fiscal import default_fiscal_year

            cache = self._open()
            start = default_fiscal_year()
            self._year = next(
                (y for y in range(start, start - 4, -1) if cache.list_listed_with_statements(y)),
                start,
            )
        return self._year

    async def search_index(self) -> Fetched:
        if self._index is None:
            from fin_checkup.site.hangul import chosung

            year = self.latest_year()
            corps = self._open().list_listed_with_statements(year)
            companies = [
                {
                    "name": c.corp_name,
                    "code": c.stock_code,
                    "market": None,
                    "sector": None,
                    "chosung": chosung(c.corp_name).lower(),
                }
                for c in sorted(corps, key=lambda c: c.corp_name)
            ]
            self._index = Fetched({"year": year, "companies": companies}, time.time())
        return self._index

    async def company(self, stock_code: str, year: int | None = None) -> Fetched:
        from fin_checkup.clock import kst_today
        from fin_checkup.site.context import CompanyEntry
        from fin_checkup.site.pages.api import company_json

        cache = self._open()
        corp = cache.find_by_stock_code(stock_code)
        if corp is None:
            raise NotFound(f"로컬 캐시에 종목코드 {stock_code} 가 없다")
        end_year = year or self.latest_year()
        data = await self._service().run(corp, end_year=end_year, years=5, allow_fetch=False)
        if data is None or data.history[-1].bsns_year != end_year:
            raise NotFound(f"로컬 캐시에 {corp.corp_name} {end_year} 사업연도 재무가 없다")
        entry = CompanyEntry(corp=corp, checkup=data)
        doc = company_json(entry, SimpleNamespace(built_on=kst_today()))  # type: ignore[arg-type]
        return Fetched(doc, time.time())

    async def build_info(self) -> Fetched:
        year = self.latest_year()
        years = [y for y in range(year - 4, year + 1) if self._open().list_listed_with_statements(y)]
        return Fetched(
            {
                "source": "local",
                "db_path": str(self.db_path),
                "fiscal_year": year,
                "fiscal_years_in_data": years,
            },
            time.time(),
        )

    async def disclosures(self) -> Fetched:
        raise NotFound("로컬 모드는 최근 위험 공시 목록을 갖고 있지 않다 — 공개 사이트 모드에서 쓸 수 있다")

    async def aclose(self) -> None:
        if self._cache is not None and self._owns_cache:
            self._cache.close()
            self._cache = None


def _settings() -> Any:
    from fin_checkup.config import Settings

    return Settings()


def source_from_env(env: dict[str, str] | None = None, settings: Any = None) -> Source:
    """환경변수로 출처를 고른다.

    - `FIN_CHECKUP_MCP_SOURCE=local` 로컬 DuckDB 캐시(DART 호출 0).
    - `site` 공개 사이트만(키가 있어도 DART 를 부르지 않는다).
    - `dart` 진단은 늘 개인 키로 DART 에서 받는다.
    - 비우면: 공개 사이트. DART 인증키가 설정돼 있으면 사이트에 없는 회사·연도만 DART 에서 받는다(`site+dart`).
    """
    env = dict(os.environ if env is None else env)
    base_url = env.get("FIN_CHECKUP_MCP_BASE_URL") or DEFAULT_BASE_URL
    choice = (env.get("FIN_CHECKUP_MCP_SOURCE") or "").strip().lower()
    if choice == "local":
        db_path = env.get("FIN_CHECKUP_DB_PATH") or "data/fin_checkup.duckdb"
        return LocalSource(db_path, base_url=base_url)
    ttl = float(env.get("FIN_CHECKUP_MCP_TTL") or DEFAULT_TTL_SECONDS)
    no_disk = (env.get("FIN_CHECKUP_MCP_CACHE_DIR") or "").strip().lower() in {"off", "none", "0"}
    cache_dir = None if no_disk else default_cache_dir(env)
    site = SiteSource(base_url, cache_dir=cache_dir, ttl=ttl)
    if choice == "site":
        return site
    settings = settings if settings is not None else _settings()
    if choice == "dart" or settings.has_api_key:
        from fin_checkup.mcp.live import DartLiveSource

        live_dir = default_cache_dir({k: v for k, v in env.items() if k != "FIN_CHECKUP_MCP_CACHE_DIR"}) if no_disk else cache_dir
        db_path = env.get("FIN_CHECKUP_DB_PATH") or str(Path(live_dir) / "dart.duckdb")
        return DartLiveSource(site, settings, db_path, always_live=choice == "dart")
    return site
