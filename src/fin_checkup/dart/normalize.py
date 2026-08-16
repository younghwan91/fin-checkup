"""전체 재무제표(fnlttSinglAcntAll)의 계정 줄을 지표 계산용 필드로 정규화.

DART는 기업마다 계정 표기가 제각각이다. 표준 XBRL 태그(account_id)를 먼저 보고,
없으면 한글 계정명(account_nm)으로 떨어진다. 어느 쪽으로도 못 찾으면 None으로 둔다 —
0으로 채우면 "부채 0원"처럼 읽혀 신호등이 거짓말을 하게 된다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from fin_checkup.models import AccountLine, Financials, RawStatements

BS = frozenset({"BS"})
INCOME = frozenset({"IS", "CIS"})
CF = frozenset({"CF"})


@dataclass(frozen=True)
class FieldSpec:
    """한 필드를 찾는 방법. 후보는 우선순위 순서대로 나열한다."""

    name: str
    sections: frozenset[str]
    account_ids: tuple[str, ...] = ()
    account_names: tuple[str, ...] = ()
    #: 현금 유출이 음수로 표기되는 계정은 절댓값으로 정규화한다.
    absolute: bool = False
    aliases: tuple[str, ...] = field(default=(), repr=False)


FIELD_SPECS: tuple[FieldSpec, ...] = (
    # ── 재무상태표 ──────────────────────────────────────────────
    FieldSpec(
        "total_assets", BS,
        account_ids=("ifrs-full_Assets", "ifrs_Assets"),
        account_names=("자산총계",),
    ),
    FieldSpec(
        "total_liabilities", BS,
        account_ids=("ifrs-full_Liabilities", "ifrs_Liabilities"),
        account_names=("부채총계",),
    ),
    FieldSpec(
        "total_equity", BS,
        account_ids=("ifrs-full_Equity", "ifrs_Equity"),
        account_names=("자본총계",),
    ),
    FieldSpec(
        "current_assets", BS,
        account_ids=("ifrs-full_CurrentAssets", "ifrs_CurrentAssets"),
        account_names=("유동자산",),
    ),
    FieldSpec(
        "current_liabilities", BS,
        account_ids=("ifrs-full_CurrentLiabilities", "ifrs_CurrentLiabilities"),
        account_names=("유동부채",),
    ),
    FieldSpec(
        "inventories", BS,
        account_ids=("ifrs-full_Inventories", "ifrs_Inventories"),
        account_names=("재고자산",),
    ),
    FieldSpec(
        "trade_receivables", BS,
        account_ids=(
            "ifrs-full_TradeAndOtherCurrentReceivables",
            "ifrs_TradeAndOtherCurrentReceivables",
        ),
        # 실데이터에서 '유동매출채권' 표기가 가장 흔한 누락 원인이었다.
        account_names=(
            "매출채권", "유동매출채권", "매출채권및기타유동채권", "매출채권및기타채권",
            "매출채권및상각후원가측정금융자산", "매출채권및계약자산",
        ),
    ),
    FieldSpec(
        "capital_stock", BS,
        account_ids=("ifrs-full_IssuedCapital", "ifrs_IssuedCapital"),
        account_names=("자본금", "보통주자본금"),
    ),
    # ── 손익계산서 ──────────────────────────────────────────────
    FieldSpec(
        "revenue", INCOME,
        account_ids=(
            "ifrs-full_Revenue",
            "ifrs-full_RevenueFromContractsWithCustomers",
            "ifrs_Revenue",
        ),
        # 금융업은 '영업수익', 보험은 '보험수익'으로 공시한다.
        account_names=("매출액", "수익(매출액)", "영업수익", "매출", "영업수익합계", "보험수익"),
    ),
    FieldSpec(
        "operating_income", INCOME,
        account_ids=(
            "dart_OperatingIncomeLoss",
            "ifrs-full_ProfitLossFromOperatingActivities",
        ),
        account_names=("영업이익", "영업이익(손실)", "영업손익", "영업손실"),
    ),
    FieldSpec(
        "net_income", INCOME,
        account_ids=("ifrs-full_ProfitLoss", "ifrs_ProfitLoss"),
        account_names=(
            "당기순이익", "당기순이익(손실)", "당기순손익", "당기순손실",
            "반기순이익", "분기순이익",
        ),
    ),
    FieldSpec(
        "interest_expense", INCOME,
        account_ids=("ifrs-full_InterestExpense", "dart_InterestExpense"),
        # 이자비용 단독 표기가 없으면 금융원가로 대체한다(과대 추정 가능 — UI에서 표기).
        account_names=("이자비용", "금융원가", "금융비용"),
        absolute=True,
    ),
    # ── 현금흐름표 ──────────────────────────────────────────────
    FieldSpec(
        "operating_cash_flow", CF,
        account_ids=(
            "ifrs-full_CashFlowsFromUsedInOperatingActivities",
            "ifrs_CashFlowsFromUsedInOperatingActivities",
        ),
        account_names=(
            "영업활동현금흐름",
            "영업활동으로인한현금흐름",
            "영업활동으로인한순현금흐름",
        ),
    ),
    FieldSpec(
        "capex", CF,
        account_ids=(
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        ),
        # '처분이익 조정' 같은 간접법 조정 항목은 CapEx가 아니다. 완전일치라 걸리지 않는다.
        account_names=(
            "유형자산의취득", "유형자산취득", "유형자산의증가",
            "기타유형자산의취득", "유형자산및무형자산의취득", "설비투자",
        ),
        absolute=True,
    ),
)


def _key(text: str) -> str:
    """공백을 제거해 표기 흔들림('매출채권 및 기타유동채권')을 흡수한다."""
    return "".join(text.split())


def _find(lines: Iterable[AccountLine], spec: FieldSpec) -> float | None:
    candidates = [ln for ln in lines if ln.sj_div in spec.sections and ln.thstrm_amount is not None]
    if not candidates:
        return None

    by_id: dict[str, float] = {}
    by_name: dict[str, float] = {}
    for ln in candidates:
        by_id.setdefault(_key(ln.account_id), ln.thstrm_amount)  # type: ignore[arg-type]
        by_name.setdefault(_key(ln.account_nm), ln.thstrm_amount)  # type: ignore[arg-type]

    for account_id in spec.account_ids:
        if (value := by_id.get(_key(account_id))) is not None:
            return abs(value) if spec.absolute else value
    for account_nm in spec.account_names:
        if (value := by_name.get(_key(account_nm))) is not None:
            return abs(value) if spec.absolute else value
    return None


def normalize_statements(raw: RawStatements) -> Financials:
    """원본 계정 줄 → 지표 계산용 Financials."""
    values = {spec.name: _find(raw.lines, spec) for spec in FIELD_SPECS}
    return Financials(
        corp_code=raw.corp_code,
        bsns_year=raw.bsns_year,
        reprt_code=raw.reprt_code,
        fs_div=raw.fs_div,
        **values,
    )
