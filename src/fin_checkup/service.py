"""캐시와 DART를 묶어 건강검진 결과를 만드는 조율 계층.

UI(Streamlit)와 향후 FastAPI가 공통으로 쓰는 진입점. 여기까지가 순수 라이브러리라
나중에 SaaS로 감쌀 때 이 아래는 그대로 재사용된다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from fin_checkup.clock import kst_today
from fin_checkup.config import Settings
from fin_checkup.config import settings as default_settings
from fin_checkup.dart.client import DartClient
from fin_checkup.dart.normalize import normalize_statements
from fin_checkup.fiscal import miss_ttl_days
from fin_checkup.metrics.engine import MONEY, CheckupResult, checkup, comparison_base
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
from fin_checkup.metrics.signals import Signal
from fin_checkup.metrics.units import mark_unit_suspects, pair_scale
from fin_checkup.models import Company, CorpCode, Financials, FsDiv, RawStatements, ReportCode
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


class CallBudgetExhausted(RuntimeError):
    """이번 실행에 허락한 DART 호출 수(`CheckupService.call_limit`)를 다 썼다. 요청은 보내지 않았다."""


class CheckupService:
    def __init__(self, cache: Cache, settings: Settings | None = None) -> None:
        self.cache = cache
        self.settings = settings or default_settings
        #: 이 서비스가 보낸 DART 요청 수 (재시도 포함). 증분 수집이 예산을 센다.
        self.calls_made = 0
        #: 정하면 이 수를 넘는 요청은 보내기 전에 `CallBudgetExhausted` 로 멈춘다.
        self.call_limit: int | None = None

    def _client(self) -> DartClient | None:
        if not self.settings.has_api_key:
            return None
        return DartClient(settings=self.settings, on_call=self._record_call)

    def _record_call(self, endpoint: str) -> None:
        """DART 호출을 세고 캐시에 기록한다. 기록이 실패해도 조회를 막지는 않는다.

        DartClient 가 요청을 보내기 **전에** 부른다 — 여기서 올린 예외는 요청을 막는다.
        """
        if self.call_limit is not None and self.calls_made >= self.call_limit:
            raise CallBudgetExhausted(f"DART 호출 예산 {self.call_limit:,}건을 다 썼다")
        self.calls_made += 1
        try:
            self.cache.record_dart_call(endpoint, today_key())
        except Exception:  # noqa: BLE001
            logger.debug("[service] 호출량 기록 실패", exc_info=True)

    def dart_budget(self) -> tuple[int, int]:
        """(오늘 사용한 호출 수, 일별 허용량). 허용량은 설정값이다."""
        return self.cache.dart_calls_today(today_key()), self.settings.dart_daily_quota

    def miss_ttl(self, today=None) -> timedelta:
        """빈 응답 기록을 믿는 기간. 사업보고서 시즌엔 짧다 (`fiscal.miss_ttl_days`)."""
        days = miss_ttl_days(
            today or kst_today(), self.settings.miss_ttl_days, self.settings.miss_ttl_season_days
        )
        return timedelta(days=days)

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
        miss_ttl: timedelta | None = None,
    ) -> Financials | None:
        """캐시 우선. 없으면 DART에서 받아 캐시에 넣고 정규화해 반환.

        빈 응답 기록이 `miss_ttl`(기본 `self.miss_ttl()`) 안이면 그 구분(CFS/OFS)은 다시 묻지 않는다.
        """
        raw = self.cache.get_statements(corp_code, bsns_year, reprt_code)
        if raw is not None:
            return normalize_statements(raw)

        if not allow_fetch:
            return None
        client = self._client()
        if client is None:
            return None

        ttl = miss_ttl if miss_ttl is not None else self.miss_ttl()
        async with client:
            for fs_div in (FsDiv.CFS, FsDiv.OFS):
                if self.cache.is_known_miss(corp_code, bsns_year, reprt_code, fs_div, ttl=ttl):
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
        """최근 N개 사업연도의 재무를 연도 오름차순으로.

        단위 오류(×1000) 의심 연도에는 `unit_suspect` 를 단다(`metrics.units`). 가르려면 앞뒤 보고서가
        필요해 범위 밖 두 해씩을 캐시에서만 더 읽는다 — DART 는 부르지 않는다.
        """
        # DART 전체 재무제표는 2015 사업연도부터 제공된다.
        start = max(2015, end_year - years + 1)
        collected: dict[int, Financials] = {}
        for year in range(start, end_year + 1):
            fin = await self.get_financials(corp_code, year, allow_fetch=allow_fetch)
            if fin is not None:
                collected[year] = fin
        context = self._cached_reports(corp_code, [*range(start - 2, start), end_year + 1, end_year + 2])
        marked = mark_unit_suspects({**context, **collected})
        return [marked[year] for year in sorted(collected)]

    def _cached_reports(self, corp_code: str, years: list[int]) -> dict[int, Financials]:
        """캐시에 있는 해의 사업보고서만 (DART 호출 없음)."""
        found: dict[int, Financials] = {}
        for year in years:
            raw = self.cache.get_statements(corp_code, year)
            if raw is not None:
                found[year] = normalize_statements(raw)
        return found

    def _marked_year(
        self, corp_code: str, year: int, known: dict[int, Financials]
    ) -> dict[int, Financials]:
        """대조군·백필용: 한 회사의 그해 앞뒤 보고서로 단위 오류 의심을 단다.

        앞뒤 한 해씩은 호출자가 한꺼번에 읽어 온다(`known`). 1,000배 쌍이 보일 때만(드물다 — 실데이터
        12,903 회사-연도 중 2) 어느 쪽인지 가르려고 두 해 밖을 더 읽는다.
        """
        if not any(
            pair_scale(known.get(y), known.get(y + 1)) not in (None, 1.0) for y in (year - 1, year)
        ):
            return known
        extra = self._cached_reports(corp_code, [y for y in (year - 2, year + 2) if y not in known])
        return mark_unit_suspects({**known, **extra})

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
        prior = comparison_base(
            latest, next((f for f in history if f.bsns_year == latest.bsns_year - 1), None)
        )

        company = await self.ensure_company(corp.corp_code, allow_fetch=allow_fetch)
        sector = sector_for(
            company.industry_code if company else None,
            company.corp_name if company else None,
            latest,
        )

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

        # 대체 계정으로 낸 값(금융원가 이자보상배율)은 견주지 않는다 — 분모가 다른 값끼리다.
        previous = {m.key: m.value for m in checkup(prior, sector=sector).metrics if not m.proxy}
        deltas: dict[str, SelfDelta] = {}
        for metric in result.metrics:
            # ⊘ 지표에 "개선/악화" 를 붙이면 판정하지 않겠다던 지표를 판정하게 된다.
            if metric.signal is Signal.NOT_APPLICABLE or metric.proxy:
                continue
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

        # 판정한 업종 그대로 — 64 는 재무제표를 봐야 가를 수 있다(`sector_for`).
        sector = result.sector
        stats: dict[str, PeerStat] = {}

        group = peer_group_for(company.industry_code)
        industry_codes = [
            code for code in self.cache.list_by_industry(group) if code != company.corp_code
        ]
        # 대조군 금액은 원화다. 달러로 공시하는 기업의 FCF 를 거기 견주면 자릿수가 다르다.
        comparable = [m for m in result.metrics if _comparable(m, result.currency)]

        if len(industry_codes) >= MIN_PEERS:
            values = self._collect_peer_values(industry_codes, bsns_year, sector)
            for metric in comparable:
                stat = peer_stats(
                    metric, values.get(metric.key, []), currency=result.currency,
                    scope=PeerScope.INDUSTRY,
                )
                if stat is not None:
                    stats[metric.key] = stat

        missing = [m for m in comparable if m.key not in stats]
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
        before = self.cache.get_statements_bulk(corp_codes, bsns_year - 1)
        after = self.cache.get_statements_bulk(corp_codes, bsns_year + 1)
        for code, raw in self.cache.get_statements_bulk(corp_codes, bsns_year).items():
            if code == exclude:
                continue
            known = _neighbours(code, bsns_year, raw, before, after)
            fin = self._marked_year(code, bsns_year, known)[bsns_year]
            # 대조군 회사는 자기 업종 기준으로 잰다(backfill 과 같다). 보는 회사의 기준을 들이대면
            # 같은 묶음의 증권사 ⊘ 값이 보험사 대조군에 그대로 섞인다.
            peer = self.cache.get_company(code)
            own = sector_for(peer.industry_code, peer.corp_name, fin) if peer else sector
            for metric in checkup(fin, sector=own).metrics:
                if metric.value is not None and _comparable(metric, fin.currency):
                    values.setdefault(metric.key, []).append(metric.value)
        return values

    def backfill_metric_values(self, bsns_year: int, batch: int = 300) -> int:
        """캐시에 있는 재무로 지표 값을 채운다. DART 호출은 하지 않는다.

        전년 재무를 함께 넘긴다 — 안 넘기면 성장성 지표가 통째로 빠져서 대조군에
        나타나지 않는다. 화면에서 성장성 카드만 휑하게 비는 걸로 드러났다.
        """
        corp_codes = self.cache.list_with_statements(bsns_year, listed_only=False)
        done = 0
        for start in range(0, len(corp_codes), batch):
            chunk = corp_codes[start : start + batch]
            statements = self.cache.get_statements_bulk(chunk, bsns_year)
            prior_statements = self.cache.get_statements_bulk(chunk, bsns_year - 1)
            # 다음 해 보고서의 전기 열과 견줘 단위 오류(×1000) 의심 연도를 대조군에서 뺀다.
            next_statements = self.cache.get_statements_bulk(chunk, bsns_year + 1)
            for code, raw in statements.items():
                company = self.cache.get_company(code)
                known = self._marked_year(
                    code, bsns_year, _neighbours(code, bsns_year, raw, prior_statements, next_statements)
                )
                fin, prior = known[bsns_year], known.get(bsns_year - 1)
                sector = sector_for(
                    company.industry_code if company else None,
                    company.corp_name if company else None,
                    fin,
                )
                result = checkup(fin, prior, sector=sector)
                values = {
                    m.key: m.value
                    for m in result.metrics
                    if m.value is not None and _comparable(m, fin.currency)
                }
                # 값이 하나도 없어도 저장한다 — 지난 계산의 행을 지워야 한다(단위 오류 의심 연도는 전부 ⚫).
                self.cache.save_metric_values(code, bsns_year, values)
                if values:
                    done += 1
        return done


def _neighbours(
    code: str, year: int, raw: RawStatements, before: dict, after: dict
) -> dict[int, Financials]:
    """그해와, 한꺼번에 읽어 둔 앞뒤 해 보고서(있으면)를 정규화한 것."""
    known = {year: normalize_statements(raw)}
    for other, bulk in ((year - 1, before), (year + 1, after)):
        if code in bulk:
            known[other] = normalize_statements(bulk[code])
    return known


def _comparable(metric, currency: str) -> bool:
    """비율은 통화와 무관하다. 금액 지표는 대조군(원화)과 같은 통화일 때만 견준다.

    대체 계정으로 낸 값(금융원가 이자보상배율)은 대조군에 넣지도, 대조군에 견주지도 않는다.
    2024 상장사 94% 가 금융원가로 계산되던 때의 분포는 외환손실 크기의 분포였다.

    ⊘(그 업종엔 뜻이 없는 값)도 같다 — 은행 부채비율 1,000%·증권사 영업이익률(영업수익이 평가·처분이익
    총액)이 같은 묶음(64·66)이나 시장 전체 분포에 섞이면 다른 회사의 대조군이 거짓말을 한다. 사이트의 업종
    분포(`site.groups`)는 처음부터 ⊘ 를 뺐다.
    """
    if metric.proxy or metric.signal is Signal.NOT_APPLICABLE:
        return False
    return metric.unit != MONEY or currency == "KRW"


# ── Streamlit 등 동기 호출자를 위한 얇은 래퍼 ─────────────────────────


def run_sync(coro):
    return asyncio.run(coro)
