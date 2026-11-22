"""전체 재무제표(fnlttSinglAcntAll)의 계정 줄을 지표 계산용 필드로 정규화.

DART는 기업마다 계정 표기가 제각각이다. 표준 XBRL 태그(account_id)를 먼저 보고,
없으면 한글 계정명(account_nm)으로 떨어진다. 어느 쪽으로도 못 찾으면 None으로 둔다 —
0으로 채우면 "부채 0원"처럼 읽혀 신호등이 거짓말을 하게 된다.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from fin_checkup.models import (
    AccountLine,
    CapexSource,
    Financials,
    InterestSource,
    RawStatements,
    ReceivableSource,
)

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


#: 매출액. 금융업은 '영업수익', 보험은 '보험수익'으로 공시한다.
REVENUE = FieldSpec(
    "revenue", INCOME,
    account_ids=(
        "ifrs-full_Revenue",
        "ifrs-full_RevenueFromContractsWithCustomers",
        "ifrs_Revenue",
    ),
    account_names=("매출액", "수익(매출액)", "영업수익", "매출", "영업수익합계", "보험수익"),
)
#: 영업활동현금흐름.
OPERATING_CASH_FLOW = FieldSpec(
    "operating_cash_flow", CF,
    account_ids=(
        "ifrs-full_CashFlowsFromUsedInOperatingActivities",
        "ifrs_CashFlowsFromUsedInOperatingActivities",
    ),
    account_names=(
        "영업활동현금흐름",
        "영업활동으로인한현금흐름",
        "영업활동으로인한순현금흐름",
        "영업활동순현금흐름",
        "영업활동으로부터의순현금유입(유출)",
    ),
)
#: '영업(활동)에서 창출된 현금' 은 이자·배당·법인세 전 소계다. 영업활동현금흐름 태그가 붙어 와도
#: 다른 영업활동현금흐름 줄이 있으면 그쪽이다(인팩 2023·2024, 젝시믹스 2023).
_CASH_GENERATED = "창출"
#: 당기순이익 합계 줄 없이 지배기업·비지배 귀속 두 줄만 적는 회사(일진전기·웹케시 등)의 두 태그.
PROFIT_TO_OWNERS_ID = "ifrs-full_ProfitLossAttributableToOwnersOfParent"
PROFIT_TO_NONCONTROLLING_ID = "ifrs-full_ProfitLossAttributableToNoncontrollingInterests"

#: 이자수익. '보험수익' 이 이보다 작으면 보험사가 아니라 은행·금융지주의 보험 자회사 한 부문이다.
INTEREST_REVENUE_IDS = frozenset({
    "ifrs-full_RevenueFromInterest",
    "ifrs-full_InterestRevenueCalculatedUsingEffectiveInterestMethod",
})
INSURANCE_REVENUE_NAME = "보험수익"


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
    # 매출채권은 순수 줄 → 합계 줄 순서가 필요해 _trade_receivables 가 따로 찾는다.
    FieldSpec(
        "capital_stock", BS,
        account_ids=("ifrs-full_IssuedCapital", "ifrs_IssuedCapital"),
        account_names=("자본금", "보통주자본금"),
    ),
    # ── 손익계산서 ──────────────────────────────────────────────
    # 매출액은 금융지주의 보험 부문 줄을 걸러야 해서 normalize 가 _revenue 로 다시 찾는다.
    REVENUE,
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
            "연결당기순이익", "연결당기순이익(손실)", "당기연결순이익", "당기연결순이익(손실)",
        ),
    ),
    # 이자비용은 단독 계정만 여기서 찾는다. 없을 때의 대체 순서는 _interest_expense 가 맡는다.
    FieldSpec(
        "interest_expense", INCOME,
        account_ids=("ifrs-full_InterestExpense", "dart_InterestExpense"),
        account_names=("이자비용",),
        absolute=True,
    ),
    # ── 현금흐름표 ──────────────────────────────────────────────
    # 영업활동현금흐름은 '창출된 현금' 줄을 걸러야 해서 normalize 가 _operating_cash_flow 로 다시 찾는다.
    OPERATING_CASH_FLOW,
    # 설비투자는 합계 줄 → 종류별 합 → 유·무형 합계 순서가 필요해 _capex 가 따로 찾는다.
)


#: 고객 돈 부채 — 은행·증권의 예수부채, 보험사의 보험계약부채·투자계약부채. 업종 판별 근거
#: (`metrics.sector`): 부채 대부분이 이 줄이면 부채비율은 빚이 아니라 맡은 돈을 잰다.
CUSTOMER_FUND_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("deposits", BS, account_ids=("ifrs-full_DepositsFromCustomers",), account_names=("예수부채",)),
    FieldSpec(
        "insurance_contracts", BS,
        account_ids=(
            "ifrs-full_InsuranceContractsIssuedThatAreLiabilities",
            "ifrs-full_InsuranceContractsThatAreLiabilities",
        ),
        account_names=("보험계약부채",),
    ),
    FieldSpec(
        "investment_contracts", BS,
        account_ids=("ifrs-full_InvestmentContractsLiabilities",),
        account_names=("투자계약부채",),
    ),
)


#: 유형자산 취득 합계 태그. 회사가 종류 한 줄('시설장치의 취득')에 붙이기도 해서 이름도 본다.
PPE_PURCHASE_ID = "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
#: 합계 줄의 이름(공백·앞 번호 제거 뒤 완전일치). '처분이익 조정' 같은 간접법 조정 항목은 CapEx가
#: 아니다 — 완전일치라 걸리지 않는다.
PPE_TOTAL_NAMES: tuple[str, ...] = (
    "유형자산의취득", "유형자산취득", "유형자산의증가", "투자활동으로분류된유형자산의취득", "설비투자",
)
#: 유형자산 종류별 취득 태그. 합계 줄이 없을 때 더한다(2024 상장사 205곳). 옛 규칙은 이 중
#: '기타유형자산의취득' 한 줄만 설비투자로 써서 56곳의 설비투자가 일부만 잡혔다.
PPE_CLASS_IDS = frozenset({
    "dart_PurchaseOfLand", "dart_PurchaseOfBuildings", "dart_PurchaseOfStructure",
    "dart_PurchaseOfMachinery", "dart_PurchaseOfVehicles", "dart_PurchaseOfMotorVehicles",
    "dart_PurchaseOfShips", "dart_PurchaseOfConstructionInProgress",
    "dart_PurchaseOfOtherPropertyPlantAndEquipment", "dart_PurchaseOfOfficeEquipment",
    "dart_PurchaseOfFixturesAndFittings",
})
#: 표준 태그 없이 적힌 종류별 취득 줄의 이름.
PPE_CLASS_NAMES = frozenset({
    "토지의취득", "건물의취득", "구축물의취득", "기계장치의취득", "차량운반구의취득",
    "건설중인자산의취득", "기타유형자산의취득", "비품의취득", "사무용비품의취득", "집기의취득",
    "공구와기구의취득", "공구기구의취득", "시설장치의취득",
})
#: 처분은 유입이다. 사용권자산 '취득' 한 줄은 리스 계약이지 현금 설비투자가 아니다 — 다만
#: '유형자산 및 사용권자산의 취득'(롯데하이마트) 같은 합계 줄은 설비투자 줄이라 앞머리로만 거른다.
_NOT_CAPEX = ("처분",)
_NOT_CAPEX_PREFIX = ("사용권",)


#: 순수 매출채권. 합계 태그(TradeAndOtherCurrentReceivables)보다 먼저 본다 — 합계 태그로
#: 먼저 잡으면 매출채권 줄이 따로 있어도 합계가, 미수금 묶음에 그 태그를 붙인 회사는
#: '기타수취채권'이 분모가 됐다(2024 상장사 약 150곳).
TRADE_RECEIVABLES = FieldSpec(
    "trade_receivables", BS,
    account_ids=(
        "dart_ShortTermTradeReceivable",
        "ifrs-full_CurrentTradeReceivables",
        "ifrs-full_TradeReceivables",
    ),
    # 실데이터에서 '유동매출채권' 표기가 가장 흔한 누락 원인이었다.
    account_names=("매출채권", "유동매출채권", "단기매출채권"),
)
#: 매출채권및기타채권 합계. 매출채권 단독 줄이 없을 때만 쓰고 출처를 남긴다.
#: 이 태그만으로는 고르지 않는다 — 이름에 '매출채권'이 든 줄만 후보다(_trade_receivables).
TRADE_AND_OTHER_RECEIVABLES = FieldSpec(
    "trade_receivables", BS,
    account_ids=(
        "ifrs-full_TradeAndOtherCurrentReceivables",
        "ifrs_TradeAndOtherCurrentReceivables",
    ),
    account_names=(
        "매출채권및기타유동채권", "매출채권및기타채권", "매출채권및기타수취채권",
        "매출채권및기타채권(유동)", "유동매출채권및기타채권", "매출채권및수취채권",
        "매출채권및상각후원가측정금융자산", "매출채권및계약자산",
    ),
)
#: 비유동(장기) 매출채권 줄. 회전율의 분모는 1년 안에 받을 외상값이다.
_NONCURRENT_IDS = ("Noncurrent", "LongTerm")
_NONCURRENT_NAMES = ("장기", "비유동")

#: 금융원가 — 이자비용 단독 계정도, 현금흐름표 이자 지급도 없을 때의 마지막 대체값.
#: 외환·파생 손실이 섞여 이자보다 크다(판정하지 않는다 — metrics.engine).
FINANCE_COSTS = FieldSpec(
    "finance_costs", INCOME,
    account_ids=("ifrs-full_FinanceCosts",),
    account_names=("금융원가", "금융비용"),
    absolute=True,
)

#: 현금흐름표의 이자 지급. 영업·투자(자본화 이자)·재무로 나눠 적는 회사가 있어 합산한다.
INTEREST_PAID_IDS: tuple[str, ...] = (
    "ifrs-full_InterestPaidClassifiedAsOperatingActivities",
    "ifrs-full_InterestPaidClassifiedAsInvestingActivities",
    "ifrs-full_InterestPaidClassifiedAsFinancingActivities",
    "ifrs-full_InterestPaid",
)
#: 표준 태그가 없을 때의 이름(공백·앞 번호 제거 뒤 완전일치). 간접법의 '이자비용 조정'은
#: 현금 지급이 아니라 걸리지 않게 '지급'·'납부'가 든 이름만 둔다.
INTEREST_PAID_NAMES: tuple[str, ...] = (
    "이자의지급", "이자지급", "이자지급(영업)", "이자의지급(영업)", "이자지급액",
    "이자비용의지급", "이자비용지급", "이자비용지급액", "이자의납부", "이자납부",
)
#: 신종자본증권은 자본이라 그 '이자'는 분배다. 이자 지급 태그가 붙어 있어도 뺀다.
_NOT_INTEREST = ("신종자본", "혼성자본")
#: '3. 이자의 지급', '(3) 이자지급', '３.이자의지급' 의 앞 번호.
_NUMBERING = re.compile(r"^[(（]?[0-9０-９]+[.)．）]")


#: 표 맨 윗단의 로마 숫자 머리('Ⅰ.매출액', 'VIII. 당기순이익'). 아라비아 숫자('1. 매출액')는
#: 'Ⅰ. 영업수익' 아래 내역일 수 있어 떼지 않는다.
_SECTION = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+|[IVX]+)[.．]")
#: 이름 끝의 주석 번호('재고자산 (주7)', '자본금(주1,17)')와 마침표('당기순이익(손실).').
_FOOTNOTE = re.compile(r"(?:\(주[0-9,]*\))+$|\.$")


def _key(text: str) -> str:
    """공백을 제거해 표기 흔들림('매출채권 및 기타유동채권')을 흡수한다."""
    return "".join(text.split())


def _name_key(text: str) -> str:
    """계정명 비교용 열쇠: 공백 · 로마 숫자 머리 · 주석 번호를 뗀다."""
    return _FOOTNOTE.sub("", _SECTION.sub("", _key(text)))


def _find(lines: Iterable[AccountLine], spec: FieldSpec) -> float | None:
    candidates = [ln for ln in lines if ln.sj_div in spec.sections and ln.thstrm_amount is not None]
    if not candidates:
        return None

    by_id: dict[str, float] = {}
    by_name: dict[str, float] = {}
    for ln in candidates:
        by_id.setdefault(_key(ln.account_id), ln.thstrm_amount)  # type: ignore[arg-type]
        by_name.setdefault(_name_key(ln.account_nm), ln.thstrm_amount)  # type: ignore[arg-type]

    found = [by_id.get(_key(account_id)) for account_id in spec.account_ids]
    found += [by_name.get(_key(account_nm)) for account_nm in spec.account_names]
    matches = [value for value in found if value is not None]
    if not matches:
        return None
    # 0 으로 온 머리 줄('영업수익' 0 아래에 '보험수익' 9.79조 — 한화생명 2023)이 실제 줄을 가리지
    # 않게, 0 이 아닌 후보가 있으면 그쪽이다. 후보가 전부 0 이면 0 이다(정말 0 인 회사).
    value = next((v for v in matches if v != 0), matches[0])
    return abs(value) if spec.absolute else value


def _net_income(lines: Iterable[AccountLine], total: float | None) -> float | None:
    """당기순이익. 합계 줄이 없으면 지배기업 귀속 + 비지배 귀속 — 두 줄이 다 있을 때만."""
    if total is not None:
        return total
    parts = {
        ln.account_id: ln.thstrm_amount
        for ln in lines
        if ln.sj_div in INCOME and ln.thstrm_amount is not None
        and ln.account_id in (PROFIT_TO_OWNERS_ID, PROFIT_TO_NONCONTROLLING_ID)
    }
    if len(parts) < 2:
        return None
    return sum(parts.values())  # type: ignore[arg-type]


def _operating_cash_flow(lines: list[AccountLine]) -> float | None:
    """영업활동현금흐름. '창출된 현금' 소계 줄은 다른 후보가 없을 때만 쓴다."""
    proper = [ln for ln in lines if _CASH_GENERATED not in ln.account_nm]
    found = _find(proper, OPERATING_CASH_FLOW)
    return found if found is not None else _find(lines, OPERATING_CASH_FLOW)


def _revenue(lines: Iterable[AccountLine]) -> float | None:
    """매출액. 이자수익보다 작은 '보험수익' 줄은 은행·금융지주의 보험 부문이라 후보에서 뺀다.

    중소기업은행·KB·신한·하나·iM 금융지주(2023·2024)가 보험 자회사의 보험수익을 매출로 써서
    영업이익률이 70~3,845% 🟢 로 나갔다. 보험사는 보험수익이 이자수익보다 크다(2023·2024 보험사 11곳 모두).
    """
    income = [ln for ln in lines if ln.sj_div in INCOME and ln.thstrm_amount is not None]
    interest = max(
        (
            abs(ln.thstrm_amount)  # type: ignore[arg-type]
            for ln in income
            if ln.account_id in INTEREST_REVENUE_IDS or _key(ln.account_nm) == "이자수익"
        ),
        default=0.0,
    )
    candidates = [
        ln for ln in income
        if not (
            _key(ln.account_nm) == INSURANCE_REVENUE_NAME
            and abs(ln.thstrm_amount) < interest  # type: ignore[arg-type]
        )
    ]
    return _find(candidates, REVENUE)


def _trade_receivables(
    lines: Iterable[AccountLine],
) -> tuple[float | None, ReceivableSource | None]:
    """매출채권과 그 출처: 매출채권 단독 줄 → 매출채권및기타채권 합계 줄."""
    current = [
        ln for ln in lines
        if ln.sj_div in BS
        and not any(word in ln.account_id for word in _NONCURRENT_IDS)
        and not any(word in _key(ln.account_nm) for word in _NONCURRENT_NAMES)
    ]
    if (pure := _find(current, TRADE_RECEIVABLES)) is not None:
        return pure, ReceivableSource.TRADE
    aggregates = [ln for ln in current if "매출채권" in _key(ln.account_nm)]
    if (total := _find(aggregates, TRADE_AND_OTHER_RECEIVABLES)) is not None:
        return total, ReceivableSource.TRADE_AND_OTHER
    return None, None


def _capex(lines: Iterable[AccountLine]) -> tuple[float | None, CapexSource | None]:
    """설비투자(절댓값)와 그 출처: 유형자산 취득 합계 → 종류별 취득의 합 → 유·무형 합계.

    합계 줄과 종류별 줄을 함께 적은 회사(2024 29곳)는 합계만 쓴다 — 종류별 줄이 합계의
    내역인지 별도 항목인지 공시만으로는 가를 수 없다(셋 중 하나꼴로 합이 합계와 같았다).
    """
    totals: list[AccountLine] = []
    classes: list[AccountLine] = []
    combined: list[AccountLine] = []
    for ln in lines:
        if ln.sj_div not in CF or ln.thstrm_amount is None:
            continue
        name = _NUMBERING.sub("", _key(ln.account_nm))
        if any(word in name for word in _NOT_CAPEX) or name.startswith(_NOT_CAPEX_PREFIX):
            continue
        if "무형" in name:
            # '유ㆍ무형자산의 취득' 은 합계 태그를 달고 나와도 무형자산이 섞였다. 유(형)으로 시작하는
            # 줄만 — '선급금의 증가(유형자산및무형자산취득)' 같은 운전자본 줄은 설비투자가 아니다.
            if name.startswith("유") and ("취득" in name or "증가" in name):
                combined.append(ln)
            continue
        if ln.account_id == PPE_PURCHASE_ID or name in PPE_TOTAL_NAMES:
            # 합계 태그를 단 종류 한 줄('시설장치의 취득', '기타유형자산의 취득')은 종류별 줄이다.
            if name in PPE_TOTAL_NAMES or ("유형자산" in name and not name.startswith("기타유형자산")):
                totals.append(ln)
            else:
                classes.append(ln)
        elif ln.account_id in PPE_CLASS_IDS or name in PPE_CLASS_NAMES:
            classes.append(ln)
    if totals:
        # 태그 줄이 이름 줄보다 먼저다(옛 FieldSpec 순서).
        best = next((ln for ln in totals if ln.account_id == PPE_PURCHASE_ID), totals[0])
        return abs(best.thstrm_amount), CapexSource.PPE  # type: ignore[arg-type]
    if classes:
        return sum(abs(ln.thstrm_amount) for ln in classes), CapexSource.PPE_ITEMIZED  # type: ignore[arg-type,misc]
    if combined:
        return abs(combined[0].thstrm_amount), CapexSource.PPE_AND_INTANGIBLE  # type: ignore[arg-type]
    return None, None


def _interest_paid(lines: Iterable[AccountLine]) -> float | None:
    """현금흐름표 이자 지급액(절댓값). 표준 태그 줄은 활동별로 합하고, 없으면 이름으로 한 줄."""
    candidates = [
        ln for ln in lines
        if ln.sj_div in CF and ln.thstrm_amount is not None
        and not any(word in _key(ln.account_nm) for word in _NOT_INTEREST)
    ]
    by_id: dict[str, float] = {}
    for ln in candidates:
        if ln.account_id in INTEREST_PAID_IDS:
            by_id.setdefault(ln.account_id, abs(ln.thstrm_amount))  # type: ignore[arg-type]
    if by_id:
        return sum(by_id.values())
    by_name: dict[str, float] = {}
    for ln in candidates:
        by_name.setdefault(_NUMBERING.sub("", _key(ln.account_nm)), abs(ln.thstrm_amount))  # type: ignore[arg-type]
    return next((by_name[n] for n in INTEREST_PAID_NAMES if n in by_name), None)


def _interest_expense(
    lines: list[AccountLine], standalone: float | None
) -> tuple[float | None, InterestSource | None]:
    """이자비용과 그 출처: 손익계산서 이자비용 → 현금흐름표 이자 지급 → 금융원가.

    현금 지급이 0 이면 금융원가로 내려간다. 지급 0 은 무차입의 증거가 아니다 — 전환사채
    상각이자처럼 비용은 났는데 현금은 안 나간 이자가 흔하다.
    """
    if standalone is not None:
        return standalone, InterestSource.INCOME_STATEMENT
    if (paid := _interest_paid(lines)) is not None and paid > 0:
        return paid, InterestSource.CASH_FLOW
    if (proxy := _find(lines, FINANCE_COSTS)) is not None:
        return proxy, InterestSource.FINANCE_COSTS
    return None, None


def _customer_fund_liabilities(lines: list[AccountLine]) -> float | None:
    found = [v for v in (_find(lines, spec) for spec in CUSTOMER_FUND_SPECS) if v is not None]
    return sum(abs(v) for v in found) if found else None


def _currency_of(lines: Iterable[AccountLine]) -> str:
    """계정 줄의 통화 최빈값. DART 에 USD·JPY·CNY 로 공시하는 기업이 24곳 있다 —
    KRW 로 두면 두산밥캣 자산 82억달러가 '82억원'으로 찍힌다."""
    counts = Counter(ln.currency for ln in lines if ln.thstrm_amount is not None and ln.currency)
    return counts.most_common(1)[0][0] if counts else "KRW"


def _prior_column(raw: RawStatements) -> RawStatements | None:
    """같은 보고서의 전기 열(frmtrm)을 당기 자리에 놓은 작년 보고서. 전기 열이 비었으면 None."""
    if all(ln.frmtrm_amount is None for ln in raw.lines):
        return None
    lines = [ln.model_copy(update={"thstrm_amount": ln.frmtrm_amount}) for ln in raw.lines]
    return raw.model_copy(update={"bsns_year": raw.bsns_year - 1, "lines": lines})


def normalize_statements(raw: RawStatements) -> Financials:
    """원본 계정 줄 → 지표 계산용 Financials. 전기 열이 있으면 `comparative` 로 함께 낸다."""
    prior = _prior_column(raw)
    return _normalize(raw).model_copy(
        update={"comparative": _normalize(prior) if prior is not None else None}
    )


def _normalize(raw: RawStatements) -> Financials:
    values = {spec.name: _find(raw.lines, spec) for spec in FIELD_SPECS}
    values["revenue"] = _revenue(raw.lines)
    values["net_income"] = _net_income(raw.lines, values["net_income"])
    values["operating_cash_flow"] = _operating_cash_flow(raw.lines)
    values["interest_expense"], source = _interest_expense(raw.lines, values["interest_expense"])
    values["trade_receivables"], receivable_source = _trade_receivables(raw.lines)
    values["capex"], capex_source = _capex(raw.lines)
    values["customer_fund_liabilities"] = _customer_fund_liabilities(raw.lines)
    return Financials(
        corp_code=raw.corp_code,
        bsns_year=raw.bsns_year,
        reprt_code=raw.reprt_code,
        fs_div=raw.fs_div,
        currency=_currency_of(raw.lines),
        interest_expense_source=source,
        trade_receivables_source=receivable_source,
        capex_source=capex_source,
        **values,
    )
