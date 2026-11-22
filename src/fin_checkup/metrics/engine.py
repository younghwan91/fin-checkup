"""재무 건강검진 지표 계산.

설계 원칙 두 가지.
1. 없는 값은 None으로 남긴다. 0으로 메우면 "부채 0"처럼 읽혀 신호등이 거짓말을 한다.
2. 판정 근거(공식·경계값)를 전부 노출한다. 블랙박스 점수는 만들지 않는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from fin_checkup.metrics.sector import Sector
from fin_checkup.metrics.signals import SEVERITY, Band, Signal
from fin_checkup.metrics.units import PRIOR_UNIT_SUSPECT_NOTE
from fin_checkup.models import CapexSource, Financials, InterestSource, ReceivableSource

#: 금액 지표의 unit. 실제 통화(원/달러)는 CheckupResult.currency에서 결정된다.
#: 여기에 "원"을 박아두면 미국 기업 금액에도 원이 붙는다.
MONEY = "money"


class Category(str, Enum):
    """계획서 4절의 지표 묶음."""

    PROFITABILITY = "수익성"
    STABILITY = "안정성"
    GROWTH = "성장성"
    EARNINGS_QUALITY = "이익의 질"
    EFFICIENCY = "효율성"


@dataclass
class Metric:
    key: str
    label: str
    category: Category
    value: float | None
    unit: str
    signal: Signal
    description: str
    formula: str
    note: str = ""
    #: 판정에 쓰인 경계값. UI에서 "왜 이 색인지" 보여주기 위해 노출한다.
    band: Band | None = None
    #: 지표가 요구하는 계정 대신 대체 계정으로 계산했다(금융원가로 낸 이자보상배율).
    #: 값은 보여 주되 판정하지 않고, 대조군·전년 비교에도 쓰지 않는다 — 분모가 다른 값끼리다.
    proxy: bool = False


@dataclass
class CheckupResult:
    corp_code: str
    bsns_year: int
    metrics: list[Metric] = field(default_factory=list)
    currency: str = "KRW"
    sector: Sector = Sector.GENERAL

    def by_key(self, key: str) -> Metric | None:
        return next((m for m in self.metrics if m.key == key), None)

    def by_category(self, category: Category) -> list[Metric]:
        return [m for m in self.metrics if m.category is category]

    @property
    def counts(self) -> Counter[Signal]:
        return Counter(m.signal for m in self.metrics)

    @property
    def worst(self) -> Signal:
        return max(
            (m.signal for m in self.metrics),
            key=lambda s: SEVERITY[s],
            default=Signal.UNKNOWN,
        )


# ── 계산 헬퍼 ──────────────────────────────────────────────────────


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    """분모가 0이거나 값이 없으면 None. 0으로 나누지 않는다."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    value = _ratio(numerator, denominator)
    return None if value is None else value * 100


def _growth_pct(current: float | None, prior: float | None) -> float | None:
    """전년 대비 증가율. 기준 연도가 0 이하면 퍼센트가 의미를 잃으므로 None."""
    if current is None or prior is None or prior <= 0:
        return None
    return (current - prior) / prior * 100


# ── 지표 정의 ──────────────────────────────────────────────────────

#: (현재, 전년) → (값, 비고). 전년이 없으면 prior는 None.
Compute = Callable[[Financials, Financials | None], tuple[float | None, str]]

#: (현재, 전년, 계산된 값) → 덮어쓸 (신호, 비고). 덮어쓸 게 없으면 None.
Override = Callable[
    [Financials, Financials | None, float | None], tuple[Signal, str] | None
]


#: 경계값의 근거 (2026-08, 2024 사업연도 상장사 880사 실측 · scripts/analyze_universe.py)
#:
#: **수익성**은 절대적 건강 기준이 없다. 영업이익률 5%가 좋은지는 업종에 따라 다르다.
#: 처음 쓰던 절대 기준(10/5)으로 재보니 상장사 64.5%가 🔴였다 — 과반이 위험이면
#: 신호등에 정보가 없다. 그래서 **시장 상위 25%(p75)를 🟢, 흑자/적자를 🟡/🔴 경계**로
#: 잡았다. "적자면 위험"은 업종과 무관하게 참이라 warn=0은 절대 기준으로 남긴다.
#:
#: **안정성**은 반대다. 부채비율 200%나 이자보상배율 1은 "갚을 수 있는가"라는 절대적
#: 의미가 있어 시장 분포에 맞추지 않는다. 실측 분포도 합리적이었다(부채비율 🔴 12.6%).
#: 이자보상배율이 🔴 49%로 나왔던 데엔 분모 탓도 있었다 — 상장사 94%가 이자비용 단독 계정이
#: 없어 금융원가(외환손실 포함)로 계산되고 있었다. 이자 지급액을 먼저 쓰도록 고친 뒤(2026-11)
#: 2024 상장사 판정분 2,417곳 중 🔴 1,018곳(42%), 그중 843곳이 영업적자다. 경계는 그대로다.
#:
#: 지표별 실측 분위수 (일반업종 880사, 2024):
#:   영업이익률 p50=2.4 p75=7.0 | 순이익률 p50=2.4 p75=7.7
#:   ROE p50=3.2 p75=8.1 | ROA p50=1.7 p75=5.2
#:   부채비율 p50=63.5 p90=220.3 | 유동비율 p50=167.4 | 자기자본비율 p50=61.0

#: 금융업(은행·보험·금융지주)에 적용할 수 없는 지표와 그 이유.
#:
#: 계산은 되지만 뜻이 없다. 은행에 유동비율을 들이대는 건 자를 잘못 대는 것이고,
#: 그 결과 🔴가 뜨면 건전한 은행을 위험하다고 말하는 셈이 된다.
FINANCIAL_NOT_APPLICABLE: dict[str, str] = {
    "debt_ratio": (
        "예금·보험부채가 부채로 잡혀 1,000%를 넘는 게 정상이다. "
        "건전성은 BIS 자기자본비율·지급여력비율(K-ICS)로 본다."
    ),
    "equity_ratio": "레버리지로 영업하는 업종이라 한 자릿수가 정상이다. 규제 자본비율로 봐야 한다.",
    "current_ratio": "금융업 재무상태표에는 유동·비유동 구분이 없다.",
    "interest_coverage": "이자비용이 차입 원가가 아니라 영업의 원가다. 상환 능력을 나타내지 않는다.",
    "inventory_turnover": "재고자산이 없는 업종이다.",
    "receivable_turnover": "매출채권 대신 대출채권으로 영업한다.",
    "asset_turnover": "자산 규모가 영업의 결과가 아니라 전제인 업종이다.",
}

#: 은행·증권·금융지주에 더 적용하지 않는 매출 기반 지표. 계산식의 분모(매출액)가 이 업종에는 없다.
#:
#: 은행·은행지주는 매출 줄이 없어 셋 다 ⚫(데이터 없음)로 나갔다 — 2023~2025 상장 34곳 100 회사-연도 중
#: 47. 없는 게 아니라 개념이 없는 것이라 ⚫ 는 거짓말이다. 증권사·증권지주는 '영업수익' 줄이 있지만
#: 금융상품 평가·처분이익과 외환거래이익을 손실과 따로 총액으로 담는다(2023~2025 증권사 45 회사-연도,
#: 표준 태그만 센 하한으로 영업수익의 중앙값 49% · 미래에셋증권 2024 은 22.2조 중 63%) — 이익률이
#: 수익성이 아니라 거래 규모에 따라 움직인다. 보험사의 매출은 보험수익(IFRS 17) 줄이라 이런 총액이 아니어서
#: 금융업 규칙 그대로 판정한다(같은 이익 ÷ 보험수익의 중앙값 12%, 2023~2025 보험사 30 회사-연도).
NO_SALES_REASON = (
    "은행·금융지주에는 '매출' 개념이 없어 매출 기반 지표가 뜻이 없다. "
    "증권사의 영업수익도 금융상품 평가·처분이익을 손실과 따로 총액으로 담아 매출이 아니다. "
    "ROE·ROA와 사업보고서의 순이자마진(NIM)·영업이익경비율(CIR)로 본다."
)
BANKING_NOT_APPLICABLE: dict[str, str] = {
    "operating_margin": NO_SALES_REASON,
    "net_margin": NO_SALES_REASON,
    **FINANCIAL_NOT_APPLICABLE,
    "revenue_growth": NO_SALES_REASON,
}

#: 부동산업은 재고자산이 분양용 토지·건물이라 회전율이 몇 년 단위로 움직인다.
REAL_ESTATE_NOT_APPLICABLE: dict[str, str] = {
    "inventory_turnover": "재고자산이 분양용 토지·건물이라 회전 주기가 사업 단위(수년)다.",
}

#: 계정을 못 찾아 ⚫가 될 때의 기본 메모. 해설(explain.why)이 다른 ⚫ 사유와 가르는 데 쓴다.
MISSING_ACCOUNT_NOTE = "공시에서 필요한 계정을 찾지 못했다."

NOT_APPLICABLE_BY_SECTOR: dict[Sector, dict[str, str]] = {
    Sector.FINANCIAL: FINANCIAL_NOT_APPLICABLE,
    Sector.BANKING: BANKING_NOT_APPLICABLE,
    Sector.REAL_ESTATE: REAL_ESTATE_NOT_APPLICABLE,
    Sector.GENERAL: {},
}


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    category: Category
    unit: str
    description: str
    formula: str
    compute: Compute
    band: Band | None = None
    #: 값과 무관하게 신호를 덮어쓸 때 사용 (자본잠식, 무차입, 흑자전환 등).
    override: Override | None = None
    #: 업종별로 다른 경계값. 없으면 band를 쓴다.
    sector_bands: dict[Sector, Band] | None = None
    #: 이 재무로는 대체 계정으로 계산되는가 (→ Metric.proxy).
    is_proxy: Callable[[Financials], bool] | None = None

    def band_for(self, sector: Sector) -> Band | None:
        if self.sector_bands and sector in self.sector_bands:
            return self.sector_bands[sector]
        return self.band


def _roe(cur: Financials, _prior: Financials | None) -> tuple[float | None, str]:
    if cur.total_equity is not None and cur.total_equity <= 0:
        return None, ""
    return _pct(cur.net_income, cur.total_equity), ""


def _roe_override(cur: Financials, _p: Financials | None, _v: float | None) -> tuple[Signal, str] | None:
    if cur.total_equity is not None and cur.total_equity <= 0:
        return Signal.UNKNOWN, "자본잠식 상태여서 ROE는 의미를 갖지 않는다."
    return None


def _debt_ratio(cur: Financials, _prior: Financials | None) -> tuple[float | None, str]:
    # 자기자본이 0 이하면 비율이 음수가 된다. 그 값을 남기면 대조군에서 '최상위'로 정렬된다.
    if cur.total_equity is not None and cur.total_equity <= 0:
        return None, ""
    return _pct(cur.total_liabilities, cur.total_equity), ""


def _debt_ratio_override(
    cur: Financials, _p: Financials | None, _v: float | None
) -> tuple[Signal, str] | None:
    if cur.total_equity is not None and cur.total_equity <= 0:
        return Signal.RED, "자기자본이 0 이하 — 자본잠식 상태."
    return None


#: 손익계산서에 이자비용 단독 계정이 없어 현금흐름표 이자 지급액으로 계산했을 때.
INTEREST_PAID_NOTE = (
    "현금흐름표의 이자 지급액 기준(손익계산서에 이자비용 단독 계정 없음). "
    "전환사채 상각이자처럼 현금으로 나가지 않은 이자는 빠진다."
)
#: 금융원가로밖에 계산할 수 없을 때. 금융원가 ≥ 이자비용이라 배율은 실제보다 낮게만 틀린다.
FINANCE_COSTS_PROXY_NOTE = (
    "이자비용 단독 계정이 없어 금융원가(외환손실 등 포함)로 계산 — "
    "실제보다 낮게 나올 수 있어 판정하지 않음."
)
#: SEC 에 총액 이자비용도 이자 지급액도 없고 순이자비용만 있을 때. 순액 ≤ 총액이라 배율은
#: 실제보다 높게만 틀린다(frames CY2023 135곳: 순액이 총액의 80% 미만 24%).
NET_INTEREST_PROXY_NOTE = (
    "이자비용 총액이 없어 순이자비용(이자수익 차감)으로 계산 — "
    "실제보다 높게 나올 수 있어 판정하지 않음."
)
_PROXY_NOTES = {
    InterestSource.FINANCE_COSTS: FINANCE_COSTS_PROXY_NOTE,
    InterestSource.NET_INTEREST: NET_INTEREST_PROXY_NOTE,
}


def _interest_is_proxy(cur: Financials) -> bool:
    """대체값(금융원가·순이자비용)으로 낸 배율인가. 금융원가 0 은 '이자도 0' 이라 대체가 아니다."""
    source = cur.interest_expense_source
    return source is not None and source.is_proxy and bool(cur.interest_expense)


def _interest_coverage(cur: Financials, _prior: Financials | None) -> tuple[float | None, str]:
    if cur.interest_expense == 0:
        return None, ""
    value = _ratio(cur.operating_income, cur.interest_expense)
    if value is not None and cur.interest_expense_source is InterestSource.CASH_FLOW:
        return value, INTEREST_PAID_NOTE
    return value, ""


def _interest_coverage_override(
    cur: Financials, _p: Financials | None, value: float | None
) -> tuple[Signal, str] | None:
    if cur.interest_expense == 0:
        if cur.interest_expense_source is InterestSource.FINANCE_COSTS:
            return Signal.GREEN, "금융원가가 0으로 공시됨 — 이자비용도 없다(사실상 무차입)."
        return Signal.GREEN, "이자비용이 0으로 공시됨 — 사실상 무차입."
    if value is None:
        return None
    if _interest_is_proxy(cur):
        # 외환·파생 손실이 분모에 섞였다. 삼성전자 2024 가 이 값으로 2.52배 🟡 였다(이자 지급 기준 48배).
        # 미국 순이자비용은 반대로 이자수익이 빠져 분모가 작다.
        return Signal.NEUTRAL, _PROXY_NOTES[cur.interest_expense_source]
    if value < 1:
        note = "영업이익으로 이자비용을 다 갚지 못하는 상태다."
        if cur.interest_expense_source is InterestSource.CASH_FLOW:
            note = f"{note} {INTEREST_PAID_NOTE}"
        return Signal.RED, note
    return None


def _ocf_override(cur: Financials, _p: Financials | None, value: float | None) -> tuple[Signal, str] | None:
    if value is None:
        return None
    if value < 0 and cur.net_income is not None and cur.net_income > 0:
        return Signal.RED, "장부상 흑자인데 영업활동 현금은 유출됐다."
    return None


def _ocf_ratio(cur: Financials, _prior: Financials | None) -> tuple[float | None, str]:
    """적자면 계산하지 않는다.

    순이익이 음수일 때 이 비율은 뜻이 뒤집힌다. 순손실 -100에 영업현금 +50이면
    비율은 -0.5지만, 적자인데 현금이 들어온 건 나쁜 신호가 아니라 좋은 신호다.
    """
    if cur.net_income is not None and cur.net_income <= 0:
        return None, ""
    return _ratio(cur.operating_cash_flow, cur.net_income), ""


def _ocf_ratio_override(
    cur: Financials, _p: Financials | None, _v: float | None
) -> tuple[Signal, str] | None:
    if cur.net_income is not None and cur.net_income <= 0:
        return Signal.NOT_APPLICABLE, (
            "적자 구간에서는 이 비율이 뜻을 잃는다. 영업활동현금흐름 자체를 볼 것."
        )
    return None


def _turnaround(
    current: float | None, prior: float | None
) -> tuple[Signal, str] | None:
    """전년이 0 이하이거나, 흑자에서 적자로 돌아섰을 때의 판정.

    증가율 퍼센트는 뜻을 잃지만(적자 -100 → -50이 '50% 개선'이 아니다), **적자에서
    흑자로 돌아섰는지**는 그 자체로 중요한 정보다. 실측에서 상장사의 40%가 여기
    해당해 ⚫로 묻히고 있었다.
    """
    if prior is None or current is None:
        return None
    if prior > 0:
        if current < 0:
            # 증가율(−150% 등)은 그대로 낸다 — 경계값으로도 🔴 다. 숫자만으로는 '줄었다' 로 읽혀서
            # 적자로 돌아섰다는 사실을 따로 적는다(2024 상장사 영업이익 203곳 · 순이익 250곳).
            return Signal.RED, "전년 흑자에서 적자로 돌아섰다."
        return None
    if prior == 0:
        return Signal.UNKNOWN, "전년 값이 0이라 증가율을 낼 수 없다."
    if current > 0:
        return Signal.GREEN, "전년 적자에서 흑자로 돌아섰다."
    if current > prior:
        return Signal.RED, "적자가 이어지고 있다(손실 폭은 줄었다)."
    return Signal.RED, "적자가 이어지고 손실 폭이 커졌다."


def _growth_override(field: str) -> Override:
    """성장성 지표의 전년 적자 처리."""

    def override(
        cur: Financials, prior: Financials | None, _value: float | None
    ) -> tuple[Signal, str] | None:
        if prior is None:
            return None
        return _turnaround(getattr(cur, field), getattr(prior, field))

    return override


#: 분모가 하루 매출보다 작으면(회전율 365회 초과) 회전율이 수천~수십만 회로 나온다 — 계산은 맞다.
#: 상한을 두거나 ⊘ 로 빼지 않는다(⚪ 측정값이라 판정도 없다). 큰 숫자의 이유만 사실로 적는다.
#: 2024 상장사 재고자산 74곳(엔비티 18만 회)·매출채권 7곳, 2025 75곳·6곳.
DAYS_PER_YEAR = 365
TINY_INVENTORY_NOTE = (
    "재고자산이 하루 매출보다 적어 회전율이 크게 나온다 — 재고를 거의 두지 않는 사업일 수 있다."
)
TINY_RECEIVABLE_NOTE = (
    "매출채권이 하루 매출보다 적어 회전율이 크게 나온다 — 외상 없이 바로 대금을 받는 사업일 수 있다."
)
#: 이익(손실)이 매출의 10배를 넘으면(이익률 ±1,000% 밖) 매출이 아주 작은 해다 — 매출 1억 원 미만 바이오의
#: 영업이익률 −20만 % 등 2024 상장사 영업이익률 22곳·순이익률 21곳. 값·판정(적자 🔴)은 그대로 둔다.
TINY_REVENUE_MARGIN = 1000.0
TINY_REVENUE_NOTE = (
    "매출액이 이익(손실)의 10분의 1보다 작아 이익률이 ±1,000% 밖으로 나온다 — 매출이 아주 작은 해의 값이다."
)


def _with(note: str, extra: str) -> str:
    return f"{note} {extra}" if note else extra


def _margin(field: str) -> Compute:
    def compute(cur: Financials, _p: Financials | None) -> tuple[float | None, str]:
        value = _pct(getattr(cur, field), cur.revenue)
        tiny = value is not None and abs(value) >= TINY_REVENUE_MARGIN
        return value, TINY_REVENUE_NOTE if tiny else ""

    return compute


def _turnover_note(_cur: Financials, _p: Financials | None, value: float | None) -> tuple[Signal, str] | None:
    if value is None:
        return None
    return Signal.NEUTRAL, "업종별 편차가 커서 절대 기준을 두지 않는다. 업종 평균·과거 추이로 비교할 것."


def _zero_denominator_is_not_applicable(field: str, what: str, tiny_note: str = ""):
    """분모가 0 으로 공시된 회전율은 ⚫(계정 없음)가 아니라 ⊘(해당 없음)다.

    재고 없는 서비스업은 재고자산을 0 으로 공시한다. 계정은 있었는데 "찾지 못했다"고
    적으면 거짓말이고, 사용자는 정규화 결함을 의심하게 된다.
    """

    def override(cur: Financials, _p: Financials | None, value: float | None) -> tuple[Signal, str] | None:
        # 분자(매출액)가 없으면 그건 계정 누락(⚫)이다. 분모 0 을 탓하면 정규화 결함이 가려진다.
        if value is None and cur.revenue is not None and getattr(cur, field) == 0:
            return Signal.NOT_APPLICABLE, f"{what}이(가) 0 으로 공시돼 회전율을 계산하지 않는다."
        forced = _turnover_note(cur, _p, value)
        if forced is not None and tiny_note and value is not None and value > DAYS_PER_YEAR:
            return forced[0], _with(forced[1], tiny_note)
        return forced

    return override


#: 매출채권 단독 줄이 없어 매출채권및기타채권 합계로 나눴을 때. 합계 ≥ 매출채권이라 회전율은
#: 낮게만 틀린다. 둘 다 공시한 2024 상장사 99곳에서 매출채권은 합계의 중앙값 95.7%(p25 85%)라
#: 대조군·전년 비교에는 그대로 둔다 — 회사 사이 회전율 차이(사분위 범위 몇 배)보다 훨씬 작다.
RECEIVABLE_AGGREGATE_NOTE = (
    "매출채권 단독 계정이 없어 매출채권및기타채권 합계(미수금 등 포함)로 계산 — "
    "실제보다 낮게 나올 수 있다."
)


def _receivable_turnover_override(
    cur: Financials, p: Financials | None, value: float | None
) -> tuple[Signal, str] | None:
    forced = _zero_denominator_is_not_applicable(
        "trade_receivables", "매출채권", TINY_RECEIVABLE_NOTE
    )(cur, p, value)
    if (
        forced is not None
        and forced[0] is Signal.NEUTRAL
        and cur.trade_receivables_source is ReceivableSource.TRADE_AND_OTHER
    ):
        return Signal.NEUTRAL, f"{forced[1]} {RECEIVABLE_AGGREGATE_NOTE}"
    return forced


#: 유형자산 취득 합계 줄이 없어 종류별 취득 줄을 더했을 때(2024 상장사 205곳).
CAPEX_ITEMIZED_NOTE = "유형자산 취득 합계 줄이 없어 종류별 취득(토지·건물·기계장치 등)을 더해 계산."
#: 유형·무형자산 취득이 한 줄일 때. 설비투자가 무형자산 취득만큼 커서 FCF 는 낮게만 틀린다.
#: 판정은 그대로 둔다 — 두 줄을 따로 적은 2024 상장사 1,816곳에 무형자산을 더해 보면 FCF 부호가
#: 바뀌는 곳이 36곳(2.0%)이었고, 무형자산 비중 중앙값은 5.7%였다.
CAPEX_WITH_INTANGIBLES_NOTE = (
    "유형·무형자산 취득이 한 줄로 공시돼 무형자산 취득까지 빼서 계산 — 공식보다 낮게 나올 수 있다."
)
_CAPEX_NOTES = {
    CapexSource.PPE_ITEMIZED: CAPEX_ITEMIZED_NOTE,
    CapexSource.PPE_AND_INTANGIBLE: CAPEX_WITH_INTANGIBLES_NOTE,
}


def _fcf(cur: Financials, _p: Financials | None) -> tuple[float | None, str]:
    value = cur.free_cash_flow
    note = _CAPEX_NOTES.get(cur.capex_source, "") if value is not None and cur.capex_source else ""
    return value, note


#: 값의 계산 근거(분모를 어느 계정에서 가져왔나)를 밝히는 비고. 판정을 바꾸지 않는 사실이라
#: '왜 이 색' 문장에 그대로 덧붙는다(explain.why).
BASIS_NOTES: tuple[str, ...] = (
    INTEREST_PAID_NOTE, RECEIVABLE_AGGREGATE_NOTE, CAPEX_ITEMIZED_NOTE, CAPEX_WITH_INTANGIBLES_NOTE,
    TINY_INVENTORY_NOTE, TINY_RECEIVABLE_NOTE, TINY_REVENUE_NOTE,
)


METRIC_DEFS: tuple[MetricDef, ...] = (
    # ── ① 수익성 ────────────────────────────────────────────────
    MetricDef(
        "operating_margin", "영업이익률", Category.PROFITABILITY, "%",
        "본업으로 100원을 팔아 얼마가 이익으로 남는지. 장사 자체의 힘.",
        "영업이익 ÷ 매출액 × 100",
        _margin("operating_income"),
        # 시장 상위 25% 기준(p75=7.0). 경계 0은 흑자/적자를 가른다.
        Band(good=7, warn=0),
    ),
    MetricDef(
        "net_margin", "순이익률", Category.PROFITABILITY, "%",
        "이자·세금까지 다 떼고 최종으로 남는 몫.",
        "당기순이익 ÷ 매출액 × 100",
        _margin("net_income"),
        Band(good=8, warn=0),  # p75=7.7
    ),
    MetricDef(
        "roe", "ROE (자기자본이익률)", Category.PROFITABILITY, "%",
        "주주가 넣은 돈으로 한 해에 얼마를 벌었는지.",
        "당기순이익 ÷ 자기자본 × 100",
        _roe, Band(good=8, warn=0), _roe_override,  # p75=8.1
    ),
    MetricDef(
        "roa", "ROA (총자산이익률)", Category.PROFITABILITY, "%",
        "빌린 돈까지 포함한 전체 자산으로 얼마를 벌었는지.",
        "당기순이익 ÷ 총자산 × 100",
        lambda c, p: (_pct(c.net_income, c.total_assets), ""),
        Band(good=5, warn=0),  # p75=5.2
        # 은행은 자산 규모가 커서 ROA 1%면 우량하다. 일반 기업 기준(5%)을 들이대면
        # 모든 은행이 🔴가 된다. 감독당국·업계가 쓰는 통상 기준으로 갈아 끼운다.
        sector_bands={
            Sector.FINANCIAL: Band(good=1.0, warn=0.5),
            Sector.BANKING: Band(good=1.0, warn=0.5),
        },
    ),
    # ── ② 안정성 ────────────────────────────────────────────────
    MetricDef(
        "debt_ratio", "부채비율", Category.STABILITY, "%",
        "자기 돈 대비 빌린 돈의 크기. 높을수록 외부 충격에 약하다.",
        "부채총계 ÷ 자기자본 × 100",
        _debt_ratio,
        Band(good=100, warn=200, higher_is_better=False),
        _debt_ratio_override,
    ),
    MetricDef(
        "current_ratio", "유동비율", Category.STABILITY, "%",
        "1년 안에 갚아야 할 빚을, 1년 안에 현금화되는 자산으로 감당할 수 있는지.",
        "유동자산 ÷ 유동부채 × 100",
        lambda c, p: (_pct(c.current_assets, c.current_liabilities), ""),
        Band(good=150, warn=100),
    ),
    MetricDef(
        "interest_coverage", "이자보상배율", Category.STABILITY, "배",
        "번 돈으로 이자를 몇 번 갚을 수 있는지. 1 미만이 이어지면 이른바 좀비기업이다.",
        "영업이익 ÷ 이자비용",
        _interest_coverage, Band(good=3, warn=1), _interest_coverage_override,
        is_proxy=_interest_is_proxy,
    ),
    MetricDef(
        "equity_ratio", "자기자본비율", Category.STABILITY, "%",
        "전체 자산 중 진짜 내 돈의 비중. 낮아질수록 자본잠식에 가까워진다.",
        "자기자본 ÷ 총자산 × 100",
        lambda c, p: (_pct(c.total_equity, c.total_assets), ""),
        Band(good=50, warn=30),
    ),
    # ── ③ 성장성 ────────────────────────────────────────────────
    MetricDef(
        "revenue_growth", "매출액 증가율", Category.GROWTH, "%",
        "작년 대비 매출이 늘었는지 줄었는지.",
        "(당해 매출액 − 전년 매출액) ÷ 전년 매출액 × 100",
        lambda c, p: (_growth_pct(c.revenue, p.revenue if p else None), ""),
        Band(good=10, warn=0), _growth_override("revenue"),
    ),
    MetricDef(
        "operating_income_growth", "영업이익 증가율", Category.GROWTH, "%",
        "본업의 이익이 작년보다 늘었는지.",
        "(당해 영업이익 − 전년 영업이익) ÷ 전년 영업이익 × 100",
        lambda c, p: (_growth_pct(c.operating_income, p.operating_income if p else None), ""),
        Band(good=10, warn=0), _growth_override("operating_income"),
    ),
    MetricDef(
        "net_income_growth", "순이익 증가율", Category.GROWTH, "%",
        "최종 이익이 작년보다 늘었는지.",
        "(당해 순이익 − 전년 순이익) ÷ 전년 순이익 × 100",
        lambda c, p: (_growth_pct(c.net_income, p.net_income if p else None), ""),
        Band(good=10, warn=0), _growth_override("net_income"),
    ),
    # ── ④ 이익의 질 ─────────────────────────────────────────────
    MetricDef(
        "operating_cash_flow", "영업활동현금흐름", Category.EARNINGS_QUALITY, MONEY,
        "장부상 이익과 별개로, 본업에서 실제로 들어온 현금.",
        "현금흐름표의 영업활동현금흐름",
        lambda c, p: (c.operating_cash_flow, ""),
        Band(good=0, warn=0),
        _ocf_override,
    ),
    MetricDef(
        "ocf_to_net_income", "현금흐름 / 순이익", Category.EARNINGS_QUALITY, "배",
        "장부이익이 실제 현금으로 들어오는 비율. 1보다 크게 낮으면 이익과 현금의 괴리가 크다.",
        "영업활동현금흐름 ÷ 당기순이익",
        _ocf_ratio, Band(good=1.0, warn=0.5), _ocf_ratio_override,
    ),
    MetricDef(
        "fcf", "잉여현금흐름 (FCF)", Category.EARNINGS_QUALITY, MONEY,
        "영업으로 번 현금에서 설비투자를 뺀 나머지. 배당·재투자에 쓸 수 있는 여윳돈.",
        "영업활동현금흐름 − 유형자산 취득(CapEx)",
        _fcf,
        Band(good=0, warn=0),
    ),
    # ── ⑤ 효율성 ────────────────────────────────────────────────
    MetricDef(
        "asset_turnover", "총자산회전율", Category.EFFICIENCY, "회",
        "가진 자산으로 매출을 몇 번 돌렸는지.",
        "매출액 ÷ 총자산",
        lambda c, p: (_ratio(c.revenue, c.total_assets), ""),
        None, _turnover_note,
    ),
    MetricDef(
        "inventory_turnover", "재고자산회전율", Category.EFFICIENCY, "회",
        "재고가 얼마나 빨리 팔려나가는지. 낮아지면 재고가 쌓이고 있다는 뜻.",
        "매출액 ÷ 재고자산",
        lambda c, p: (_ratio(c.revenue, c.inventories), ""),
        None, _zero_denominator_is_not_applicable("inventories", "재고자산", TINY_INVENTORY_NOTE),
    ),
    MetricDef(
        "receivable_turnover", "매출채권회전율", Category.EFFICIENCY, "회",
        "외상값을 얼마나 빨리 회수하는지. 낮아지면 못 받는 돈이 늘고 있을 수 있다.",
        "매출액 ÷ 매출채권",
        lambda c, p: (_ratio(c.revenue, c.trade_receivables), ""),
        None, _receivable_turnover_override,
    ),
)


def comparison_base(current: Financials, prior: Financials | None) -> Financials | None:
    """작년과 견줄 때 쓸 작년. 올해 보고서의 전기 열이 있으면 그것, 없으면 작년 보고서.

    작년 보고서의 당기 숫자는 그 뒤의 재작성을 모르고, 연결/개별이 올해와 다를 수 있다.
    2024 상장사 2,404곳(두 해 모두 있음) 중 83곳이 두 해의 연결/개별이 갈렸고, 같은 기준이어도
    전기 매출이 10% 넘게 다시 적힌 곳이 57곳, 영업이익은 114곳이었다 — (주)효성은 분할로 전기
    매출이 3.44조 → 1.85조로 재분류돼 매출액 증가율이 −33.9% 🔴 로 나가고 있었다(전기 열 기준 +23.0%).
    """
    return current.comparative if current.comparative is not None else prior


def checkup(
    current: Financials,
    prior: Financials | None = None,
    sector: Sector = Sector.GENERAL,
) -> CheckupResult:
    """한 회계기간의 재무 건강검진 결과를 만든다.

    prior를 주면 성장성 지표까지 채워진다 — 단 올해 보고서에 전기 열이 있으면 그쪽이 먼저다
    (`comparison_base`). sector를 주면 그 업종에 적용할 수 없는 지표를 판정하지 않는다 —
    은행에 유동비율을 들이대지 않기 위한 것이다.
    """
    prior = comparison_base(current, prior)
    not_applicable = NOT_APPLICABLE_BY_SECTOR.get(sector, {})
    metrics: list[Metric] = []
    # 작년 보고서가 단위 오류 의심이면(올해 보고서에 전기 열이 없을 때만 여기까지 온다) 작년과 견주지 않는다.
    prior_suspect = prior is not None and bool(prior.unit_suspect)

    for spec in METRIC_DEFS:
        band = spec.band_for(sector)
        proxy = spec.is_proxy(current) if spec.is_proxy is not None else False

        # 그해 보고서 금액이 단위 오류 의심이면 아무것도 계산하지 않는다 — 1,000 으로 나눠 '고치지' 않는다.
        # 비율은 단위와 무관해 보여도, 어느 줄이 틀렸는지 원문을 보기 전엔 모른다.
        unit_note = current.unit_suspect or (
            PRIOR_UNIT_SUSPECT_NOTE if prior_suspect and spec.category is Category.GROWTH else ""
        )
        if unit_note:
            metrics.append(
                Metric(
                    key=spec.key, label=spec.label, category=spec.category,
                    value=None, unit=spec.unit, signal=Signal.UNKNOWN,
                    description=spec.description, formula=spec.formula,
                    note=unit_note, band=band, proxy=False,
                )
            )
            continue

        # 적용 불가 판정이 가장 먼저다. 값이 나오더라도 판정하지 않는다.
        if spec.key in not_applicable:
            value, _ = spec.compute(current, prior)
            metrics.append(
                Metric(
                    key=spec.key, label=spec.label, category=spec.category,
                    value=value, unit=spec.unit, signal=Signal.NOT_APPLICABLE,
                    description=spec.description, formula=spec.formula,
                    note=f"{sector.label}에는 적용하지 않는다 — {not_applicable[spec.key]}",
                    band=None, proxy=proxy,
                )
            )
            continue

        value, note = spec.compute(current, prior)

        if value is None:
            signal = Signal.UNKNOWN
        elif band is not None:
            signal = band.evaluate(value)
        else:
            signal = Signal.NEUTRAL

        if spec.override is not None:
            forced = spec.override(current, prior, value)
            if forced is not None:
                signal, note = forced

        if signal is Signal.UNKNOWN and not note:
            note = MISSING_ACCOUNT_NOTE

        metrics.append(
            Metric(
                key=spec.key,
                label=spec.label,
                category=spec.category,
                value=value,
                unit=spec.unit,
                signal=signal,
                description=spec.description,
                formula=spec.formula,
                note=note,
                band=band,
                proxy=proxy,
            )
        )

    return CheckupResult(
        corp_code=current.corp_code,
        bsns_year=current.bsns_year,
        metrics=metrics,
        currency=current.currency,
        sector=sector,
    )
