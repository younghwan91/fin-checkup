"""캐시와 DART를 묶어 건강검진 결과를 만드는 조율 계층.

UI(Streamlit)와 향후 FastAPI가 공통으로 쓰는 진입점. 여기까지가 순수 라이브러리라
나중에 SaaS로 감쌀 때 이 아래는 그대로 재사용된다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from fin_checkup.config import Settings
from fin_checkup.config import settings as default_settings
from fin_checkup.dart.client import DartClient
from fin_checkup.dart.normalize import normalize_statements
from fin_checkup.metrics.engine import CheckupResult, checkup
from fin_checkup.metrics.peers import (
    MIN_PEERS,
    PeerScope,
    PeerStat,
    SelfDelta,
    peer_stats,
    self_delta,
)
from fin_checkup.metrics.redflags import RedFlag, detect_red_flags
from fin_checkup.metrics.sector import Sector, peer_group_for, sector_for
from fin_checkup.models import Company, CorpCode, Financials, FsDiv, ReportCode
from fin_checkup.storage import Cache, today_key

logger = logging.getLogger(__name__)


@dataclass
class Checkup:
    """한 기업의 건강검진 결과 묶음."""

    company: Company | None
    corp: CorpCode
    result: CheckupResult
    red_flags: list[RedFlag]
    history: list[Financials]
    #: 캐시에만 의존해 만들어졌는지 (DART 호출 없이).
    from_cache_only: bool = True
    #: 지표 key → 대조군 분포(동종업계 우선, 없으면 전체 상장사).
    peers: dict[str, PeerStat] = field(default_factory=dict)
    #: 지표 key → 작년의 자기 자신과 견준 결과.
    deltas: dict[str, SelfDelta] = field(default_factory=dict)


class CheckupService:
    def __init__(self, cache: Cache, settings: Settings | None = None) -> None:
        self.cache = cache
        self.settings = settings or default_settings

    def _client(self) -> DartClient | None:
        if not self.settings.has_api_key:
            return None
        return DartClient(settings=self.settings, on_call=self._record_call)

    def _record_call(self, endpoint: str) -> None:
        """DART 호출을 캐시에 기록한다. 실패해도 조회를 막지는 않는다."""
        try:
            self.cache.record_dart_call(endpoint, today_key())
        except Exception:  # noqa: BLE001
            logger.debug("[service] 호출량 기록 실패", exc_info=True)

    def dart_budget(self) -> tuple[int, int]:
        """(오늘 사용한 호출 수, 일별 허용량). 허용량은 설정값이다."""
        return self.cache.dart_calls_today(today_key()), self.settings.dart_daily_quota

    # ------------------------------------------------------------------
    # corp_code
    # ------------------------------------------------------------------

    async def ensure_corp_codes(self, force: bool = False) -> int:
        """corp_code 매핑이 없거나 오래됐으면 갱신. 갱신한 건수를 반환(0이면 그대로)."""
        age = self.cache.corp_codes_age()
        ttl = timedelta(days=self.settings.corp_code_ttl_days)
        if not force and age is not None and age < ttl:
            return 0

        client = self._client()
        if client is None:
            logger.warning("[service] DART 인증키가 없어 corp_code를 갱신할 수 없다")
            return 0
        async with client:
            codes = await client.fetch_corp_codes()
        return self.cache.save_corp_codes(codes)

    def search(self, keyword: str, limit: int = 20) -> list[CorpCode]:
        keyword = keyword.strip()
        if not keyword:
            return []
        # 6자리 숫자면 종목코드로 본다.
        if keyword.isdigit() and len(keyword) == 6:
            found = self.cache.find_by_stock_code(keyword)
            return [found] if found else []
        return self.cache.search_by_name(keyword, limit)

    # ------------------------------------------------------------------
    # 재무제표
    # ------------------------------------------------------------------

    async def get_financials(
        self,
        corp_code: str,
        bsns_year: int,
        reprt_code: ReportCode = ReportCode.ANNUAL,
        allow_fetch: bool = True,
    ) -> Financials | None:
        """캐시 우선. 없으면 DART에서 받아 캐시에 넣고 정규화해 반환."""
        raw = self.cache.get_statements(corp_code, bsns_year, reprt_code)
        if raw is not None:
            return normalize_statements(raw)

        if not allow_fetch:
            return None
        client = self._client()
        if client is None:
            return None

        async with client:
            for fs_div in (FsDiv.CFS, FsDiv.OFS):
                if self.cache.is_known_miss(corp_code, bsns_year, reprt_code, fs_div):
                    continue
                fetched = await client.fetch_statements(corp_code, bsns_year, reprt_code, fs_div)
                if fetched is None:
                    self.cache.record_miss(corp_code, bsns_year, reprt_code, fs_div)
                    continue
                self.cache.save_statements(fetched)
                return normalize_statements(fetched)
        return None

    async def load_history(
        self,
        corp_code: str,
        end_year: int,
        years: int = 5,
        allow_fetch: bool = True,
    ) -> list[Financials]:
        """최근 N개 사업연도의 재무를 연도 오름차순으로."""
        # DART 전체 재무제표는 2015 사업연도부터 제공된다.
        start = max(2015, end_year - years + 1)
        collected: list[Financials] = []
        for year in range(start, end_year + 1):
            fin = await self.get_financials(corp_code, year, allow_fetch=allow_fetch)
            if fin is not None:
                collected.append(fin)
        return collected

    async def ensure_company(self, corp_code: str, allow_fetch: bool = True) -> Company | None:
        """기업개황을 캐시 우선으로 확보한다. 업종 비교에 industry_code가 필요하다."""
        cached = self.cache.get_company(corp_code)
        if cached is not None or not allow_fetch:
            return cached

        client = self._client()
        if client is None:
            return None
        async with client:
            company = await client.fetch_company(corp_code)
        if company is not None:
            self.cache.save_company(company)
        return company

    # ------------------------------------------------------------------
    # 건강검진
    # ------------------------------------------------------------------

    async def run(
        self,
        corp: CorpCode,
        end_year: int,
        years: int = 5,
        allow_fetch: bool = True,
    ) -> Checkup | None:
        had_cache = self.cache.get_statements(corp.corp_code, end_year) is not None
        history = await self.load_history(
            corp.corp_code, end_year, years, allow_fetch=allow_fetch
        )
        if not history:
            return None

        latest = history[-1]
        prior = next((f for f in history if f.bsns_year == latest.bsns_year - 1), None)

        company = await self.ensure_company(corp.corp_code, allow_fetch=allow_fetch)
        sector = sector_for(company.industry_code if company else None)

        result = checkup(latest, prior, sector=sector)
        return Checkup(
            company=company,
            corp=corp,
            result=result,
            red_flags=detect_red_flags(history, sector=sector),
            history=history,
            from_cache_only=had_cache,
            peers=self.peer_comparison(company, result, latest.bsns_year),
            deltas=self.self_comparison(result, prior, sector),
        )

    def self_comparison(
        self,
        result: CheckupResult,
        prior: Financials | None,
        sector: Sector,
    ) -> dict[str, SelfDelta]:
        """작년의 자기 자신과 견준다.

        대조군만으로는 부족하다 — 업종 전체가 나빠진 해에는 상위 20%여도 작년보다
        나쁠 수 있고, 사용자가 알고 싶은 건 그쪽이다.
        """
        if prior is None:
            return {}

        previous = {m.key: m.value for m in checkup(prior, sector=sector).metrics}
        deltas: dict[str, SelfDelta] = {}
        for metric in result.metrics:
            delta = self_delta(metric, previous.get(metric.key), currency=result.currency)
            if delta is not None:
                deltas[metric.key] = delta
        return deltas

    # ------------------------------------------------------------------
    # 업종 비교
    # ------------------------------------------------------------------

    def peer_comparison(
        self,
        company: Company | None,
        result: CheckupResult,
        bsns_year: int,
    ) -> dict[str, PeerStat]:
        """대조군 분포. 동종업계를 먼저 보고, 표본이 모자라면 전체 상장사로 넓힌다.

        "영업이익률 10.88%가 좋은 건가"에 답하려면 견줄 대상이 있어야 한다. 업종
        표본이 없다고 아무것도 안 보여주면 절반의 기업이 답을 못 받는다(실측 49.8%).

        캐시에 있는 기업만 쓴다 — 화면을 여는 동안 수십 개사를 DART에서 새로
        받아오지 않는다. 그건 `cli.py collect`로 미리 채워두는 일이다.
        """
        if company is None:
            return {}

        sector = sector_for(company.industry_code)
        stats: dict[str, PeerStat] = {}

        group = peer_group_for(company.industry_code)
        industry_codes = [
            code for code in self.cache.list_by_industry(group) if code != company.corp_code
        ]
        if len(industry_codes) >= MIN_PEERS:
            values = self._collect_peer_values(industry_codes, bsns_year, sector)
            for metric in result.metrics:
                stat = peer_stats(
                    metric, values.get(metric.key, []), currency=result.currency,
                    scope=PeerScope.INDUSTRY,
                )
                if stat is not None:
                    stats[metric.key] = stat

        missing = [m for m in result.metrics if m.key not in stats]
        if not missing:
            return stats

        # 업종에서 못 채운 지표만 전체 상장사로 넓힌다. 업종 비교가 있으면 그게 낫다.
        market_codes = [
            code
            for code in self.cache.list_with_statements(bsns_year)
            if code != company.corp_code
        ]
        if len(market_codes) < MIN_PEERS:
            return stats

        market_values = self._collect_peer_values(market_codes, bsns_year, sector)
        for metric in missing:
            stat = peer_stats(
                metric, market_values.get(metric.key, []), currency=result.currency,
                scope=PeerScope.MARKET,
            )
            if stat is not None:
                stats[metric.key] = stat
        return stats

    def _collect_peer_values(
        self,
        corp_codes: list[str],
        bsns_year: int,
        sector: Sector,
        exclude: str = "",
    ) -> dict[str, list[float]]:
        """대조군의 지표 값 모음.

        미리 계산해둔 metric_values를 먼저 본다. 원본에서 매번 다시 계산하면
        대조군 한 번에 12만 줄을 파싱하게 되고 실제로 16초가 걸렸다.

        아직 안 채워진 캐시에서도 동작해야 하므로 비어 있으면 원본에서 계산한다
        (`cli.py backfill`로 채우면 그 경로는 안 탄다).
        """
        precomputed = self.cache.peer_metric_values(corp_codes, bsns_year, exclude=exclude)
        if precomputed:
            return precomputed

        values: dict[str, list[float]] = {}
        for code, raw in self.cache.get_statements_bulk(corp_codes, bsns_year).items():
            if code == exclude:
                continue
            for metric in checkup(normalize_statements(raw), sector=sector).metrics:
                if metric.value is not None:
                    values.setdefault(metric.key, []).append(metric.value)
        return values

    def backfill_metric_values(self, bsns_year: int, batch: int = 300) -> int:
        """캐시에 있는 재무로 지표 값을 채운다. DART 호출은 하지 않는다.

        전년 재무를 함께 넘긴다 — 안 넘기면 성장성 지표가 통째로 빠져서 대조군에
        나타나지 않는다. 화면에서 성장성 카드만 휑하게 비는 걸로 드러났다.
        """
        corp_codes = self.cache.list_with_statements(bsns_year, limit=100_000)
        done = 0
        for start in range(0, len(corp_codes), batch):
            chunk = corp_codes[start : start + batch]
            statements = self.cache.get_statements_bulk(chunk, bsns_year)
            prior_statements = self.cache.get_statements_bulk(chunk, bsns_year - 1)
            for code, raw in statements.items():
                company = self.cache.get_company(code)
                sector = sector_for(company.industry_code if company else None)
                prior_raw = prior_statements.get(code)
                prior = normalize_statements(prior_raw) if prior_raw is not None else None
                result = checkup(normalize_statements(raw), prior, sector=sector)
                values = {m.key: m.value for m in result.metrics if m.value is not None}
                if values:
                    self.cache.save_metric_values(code, bsns_year, values)
                    done += 1
        return done


# ── Streamlit 등 동기 호출자를 위한 얇은 래퍼 ─────────────────────────


def run_sync(coro):
    return asyncio.run(coro)
