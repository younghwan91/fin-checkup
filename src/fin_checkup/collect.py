"""증분 수집 — 상장사 중 아직 재무가 없는 (회사, 사업연도)만 DART 에서 받는다.

GitHub Actions(`collect.yml`)가 매일 돌린다. 한 번에 전부 받는 게 아니라 "빈 곳만, 정한 호출
수 안에서" 받는다 — DART 인증키 하나의 일별 허용량(20,000건)을 로컬 사용·Pages 의 공시 수집과
나눠 쓰기 때문이다.

1. corp_code 매핑이 오래됐으면 갱신 (`CheckupService.ensure_corp_codes`, 1건).
2. 종목코드는 있는데 기업개황이 없는 회사(새 상장사)는 개황부터 받는다 — 시장 구분을 봐야
   상장사인지 안다(상장폐지 회사의 옛 종목코드가 corpCode.xml 에 남는다).
3. 상장사 × 대상 연도(기본 `fiscal.collect_years` — 새 보고서가 들어올 해와 그 전 해) 중 재무가
   없는 조합만 묻는다. 빈 응답 기록이 TTL(`fiscal.miss_ttl_days`, 시즌엔 짧다) 안이면 건너뛴다.

멈추는 경우 (전부 받은 데까지는 캐시에 남는다):
- 예산 — 다음 조합에 필요한 최대 호출 수가 남은 예산보다 크면 시작하지 않는다. 그래도 넘치는 요청
  (재시도 등)은 `CheckupService.call_limit` 이 보내기 전에 막는다.
- 020 (일별 허용량 초과) — 오늘은 풀리지 않는다. 재시도하지 않는다.
- 시간 — `max_seconds` 를 넘으면 (Actions 작업 시간 제한에 잘려 저장을 못 하는 일이 없게).
- 오류 — 인증키 문제(010·011·012·101)는 바로, 그 밖의 오류는 연달아 `MAX_ERRORS_IN_ROW` 번이면.

메시지에 인증키를 담지 않는다(DART 약관 제19조) — 예외 문구도 `_safe` 로 가린 뒤 찍는다.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date

import httpx

from fin_checkup.clock import kst_today
from fin_checkup.config import Settings
from fin_checkup.dart.client import DartError, RateLimitExceeded
from fin_checkup.fiscal import collect_years
from fin_checkup.models import LISTED_MARKETS, CorpCode, FsDiv, ReportCode
from fin_checkup.service import CallBudgetExhausted, CheckupService
from fin_checkup.storage import Cache, today_key

STOP_BUDGET = "budget"
STOP_QUOTA = "quota"
STOP_TIME = "time"
STOP_ERRORS = "errors"

#: 인증키·접근 문제. 몇 번을 다시 해도 같다 — 첫 번째에서 멈춘다.
KEY_STATUSES = frozenset({"010", "011", "012", "101"})
#: 그 밖의 오류가 연달아 이만큼이면 DART 장애로 보고 멈춘다.
MAX_ERRORS_IN_ROW = 5

_LISTED = frozenset(m.value for m in LISTED_MARKETS)
_KEY_IN_URL = re.compile(r"(crtfc_key=)[^&\s'\"]+")


@dataclass
class CollectSummary:
    """수집 결과. `to_dict()` 가 워크플로가 읽는 JSON 이다 (키 이름을 바꾸면 collect.yml 도 고친다)."""

    years: list[int]
    #: 이번 실행에 쓸 수 있었던 호출 수 (요청 예산과 오늘 남은 일별 허용량 중 작은 쪽).
    budget: int
    miss_ttl_days: int
    corp_codes_refreshed: int = 0
    #: 상장사 수 (시장 구분이 KOSPI·KOSDAQ·KONEX).
    listed: int = 0
    #: 시작할 때 재무가 없던 (상장사, 연도) 조합.
    missing: int = 0
    #: 새로 받은 재무.
    fetched: int = 0
    #: 물어봤지만 없었다 (빈 응답으로 기록).
    empty: int = 0
    #: 빈 응답 기록이 TTL 안이라 묻지 않았다.
    skipped: int = 0
    failed: int = 0
    #: 새로 받은 기업개황 (새 상장사).
    companies: int = 0
    #: 멈춰서 이번엔 보지 못한 조합.
    remaining: int = 0
    calls: int = 0
    stopped: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def new_data(self) -> bool:
        """사이트에 보이는 데이터가 늘었나 (재무·기업개황)."""
        return bool(self.fetched or self.companies)

    @property
    def changed(self) -> bool:
        """스냅샷으로 남길 게 있나. 빈 응답 기록도 넣는다 — 안 남기면 다음 실행이 같은 회사를 또 묻는다."""
        return self.new_data or bool(self.empty)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["errors"] = data["errors"][:20]
        data["new_data"] = self.new_data
        data["changed"] = self.changed
        return data


def _safe(message: str, settings: Settings) -> str:
    key = settings.dart_api_key.strip()
    if key:
        message = message.replace(key, "***")
    return _KEY_IN_URL.sub(r"\1***", message)


async def collect(
    cache: Cache,
    settings: Settings,
    budget: int,
    years: list[int] | None = None,
    today: date | None = None,
    max_seconds: float | None = None,
    limit: int = 0,
    progress: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
) -> CollectSummary:
    """예산 안에서 빈 곳을 채운다. 예외로 끝나지 않는다 — 멈춘 이유는 `summary.stopped`."""
    today = today or kst_today()
    years = list(years) if years else collect_years(today)
    service = CheckupService(cache, settings)
    used_today = cache.dart_calls_today(today_key(today))
    allowed = max(0, min(budget, settings.dart_daily_quota - used_today))
    service.call_limit = allowed
    ttl = service.miss_ttl(today)
    summary = CollectSummary(years=years, budget=allowed, miss_ttl_days=ttl.days)
    deadline = None if not max_seconds else clock() + max_seconds
    errors_in_row = 0

    def fits(calls: int) -> bool:
        return service.calls_made + calls <= allowed

    def failed(label: str, exc: Exception) -> bool:
        """오류 하나를 세고, 멈춰야 하면 True."""
        nonlocal errors_in_row
        summary.failed += 1
        errors_in_row += 1
        line = f"{label}: {type(exc).__name__} {_safe(str(exc), settings)}"
        summary.errors.append(line)
        progress(f"  !! {line}")
        status = getattr(exc, "status", "")
        if status in KEY_STATUSES or errors_in_row >= MAX_ERRORS_IN_ROW:
            summary.stopped = STOP_ERRORS
            return True
        return False

    try:
        if fits(1):
            try:
                summary.corp_codes_refreshed = await service.ensure_corp_codes()
            except RateLimitExceeded:
                raise
            except (DartError, httpx.HTTPError) as exc:
                # 매핑 갱신이 실패해도 가진 목록으로 계속한다 (새 상장사만 하루 늦어진다).
                if failed("corp_code 갱신", exc):
                    return summary

        stock = cache.list_stock_corp_codes()
        listed: list[CorpCode] = [corp for corp, market in stock if market in _LISTED]
        for corp, market in stock:
            if market is not None:
                continue
            if not fits(1):
                summary.stopped = STOP_BUDGET
                break
            try:
                company = await service.ensure_company(corp.corp_code)
            except (RateLimitExceeded, CallBudgetExhausted):
                raise
            except (DartError, httpx.HTTPError) as exc:
                if failed(f"{corp.corp_name} 기업개황", exc):
                    break
                continue
            errors_in_row = 0
            if company is not None:
                summary.companies += 1
                if company.market is not None and company.market.value in _LISTED:
                    listed.append(corp)
        listed.sort(key=lambda c: (c.stock_code, c.corp_code))
        if limit:
            listed = listed[:limit]
        summary.listed = len(listed)

        pairs = [
            (corp, year)
            for year in years
            for have in [cache.corp_codes_with_statements(year)]
            for corp in listed
            if corp.corp_code not in have
        ]
        summary.missing = len(pairs)
        progress(
            f"상장사 {len(listed):,}곳 · {', '.join(map(str, years))}년 중 재무 없는 조합 "
            f"{len(pairs):,}건 · 예산 {allowed:,}건 · 빈 응답 {ttl.days}일 믿음"
        )
        if summary.stopped:
            return summary

        for i, (corp, year) in enumerate(pairs, start=1):
            if deadline is not None and clock() >= deadline:
                summary.stopped = STOP_TIME
                break
            ask = [
                fs
                for fs in (FsDiv.CFS, FsDiv.OFS)
                if not cache.is_known_miss(corp.corp_code, year, ReportCode.ANNUAL, fs, ttl=ttl)
            ]
            if not ask:
                summary.skipped += 1
                continue
            if not fits(len(ask)):
                summary.stopped = STOP_BUDGET
                break
            try:
                fin = await service.get_financials(corp.corp_code, year, miss_ttl=ttl)
            except (RateLimitExceeded, CallBudgetExhausted):
                raise
            except (DartError, httpx.HTTPError) as exc:
                if failed(f"{corp.corp_name} {year}", exc):
                    break
                continue
            errors_in_row = 0
            if fin is None:
                summary.empty += 1
            else:
                summary.fetched += 1
            if i % 100 == 0:
                progress(
                    f"[{i:,}/{len(pairs):,}] 수집 {summary.fetched} · 없음 {summary.empty} · "
                    f"건너뜀 {summary.skipped} · 실패 {summary.failed} · 호출 {service.calls_made:,}"
                )
    except RateLimitExceeded:
        summary.stopped = STOP_QUOTA
    except CallBudgetExhausted:
        summary.stopped = STOP_BUDGET
    finally:
        summary.calls = service.calls_made
        done = summary.fetched + summary.empty + summary.skipped + summary.failed
        summary.remaining = max(0, summary.missing - done)
    return summary
