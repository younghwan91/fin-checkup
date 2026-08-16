"""US-GAAP companyfacts → Financials.

두 가지가 까다롭다.

1. 한 번의 10-K 공시(fy=2023)에 **전년도 값이 함께 들어 있다.** Apple의 FY2023
   10-K에는 end=2022-09-24와 end=2023-09-30이 모두 담긴다. 그래서 fy로 고르면
   안 되고 회계연도 종료일(end)로 골라야 한다.

2. 손익·현금흐름 항목에는 분기 값도 섞여 있다. start~end 기간이 1년에 가까운
   것만 연간으로 인정한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from fin_checkup.models import Financials, FsDiv, ReportCode

#: 이 일수 이상이어야 연간 값으로 본다. 분기(약 90일)를 걸러내기 위한 것.
MIN_ANNUAL_DAYS = 300


@dataclass(frozen=True)
class TagSpec:
    field: str
    #: 우선순위 순서. 앞에 있는 태그가 먼저 잡힌다.
    tags: tuple[str, ...]
    #: 손익·현금흐름처럼 기간 값이면 True, 재무상태표처럼 시점 값이면 False.
    is_flow: bool
    #: 현금 유출이 음수로 잡히는 항목은 절댓값으로.
    absolute: bool = False


TAG_SPECS: tuple[TagSpec, ...] = (
    # 재무상태표 (시점)
    TagSpec("total_assets", ("Assets",), False),
    TagSpec("total_liabilities", ("Liabilities",), False),
    TagSpec(
        "total_equity",
        ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        False,
    ),
    TagSpec("current_assets", ("AssetsCurrent",), False),
    TagSpec("current_liabilities", ("LiabilitiesCurrent",), False),
    TagSpec("inventories", ("InventoryNet",), False),
    TagSpec(
        "trade_receivables",
        ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
        False,
    ),
    # 손익계산서 (기간)
    TagSpec(
        "revenue",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ),
        True,
    ),
    TagSpec("operating_income", ("OperatingIncomeLoss",), True),
    TagSpec("net_income", ("NetIncomeLoss", "ProfitLoss"), True),
    TagSpec(
        "interest_expense",
        ("InterestExpense", "InterestExpenseDebt", "InterestIncomeExpenseNet"),
        True,
        absolute=True,
    ),
    # 현금흐름표 (기간)
    TagSpec(
        "operating_cash_flow",
        (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
        True,
    ),
    TagSpec(
        "capex",
        (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
        ),
        True,
        absolute=True,
    ),
)

#: 자본금(capital_stock)은 채우지 않는다. 한국의 자본잠식률은 상법·거래소 규정에
#: 묶인 개념이라 US-GAAP의 CommonStockValue와 대응하지 않는다. 억지로 맞추면
#: 미국 기업에 엉뚱한 "자본잠식" 딱지가 붙는다.


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_annual_form(entry: dict) -> bool:
    return str(entry.get("form", "")).startswith("10-K")


def _pick(units: list[dict], fiscal_year: int, is_flow: bool) -> float | None:
    """해당 회계연도의 연간 값 하나를 고른다."""
    candidates: list[dict] = []
    for entry in units:
        if not _is_annual_form(entry):
            continue
        end = _parse_date(entry.get("end"))
        if end is None or end.year != fiscal_year:
            continue
        if is_flow:
            start = _parse_date(entry.get("start"))
            if start is None or (end - start).days < MIN_ANNUAL_DAYS:
                continue
        if not isinstance(entry.get("val"), int | float):
            continue
        candidates.append(entry)

    if not candidates:
        return None

    # 정정 공시가 있으면 나중에 제출된 것을 쓴다.
    candidates.sort(key=lambda e: (str(e.get("filed", "")), str(e.get("end", ""))))
    return float(candidates[-1]["val"])


def normalize_company_facts(
    facts: dict,
    fiscal_year: int,
    cik: str = "",
) -> Financials | None:
    """companyfacts 응답에서 한 회계연도를 뽑아 Financials로.

    필요한 계정을 하나도 못 찾으면 None (그 해 10-K가 없다는 뜻).
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        return None

    values: dict[str, float | None] = {}
    for spec in TAG_SPECS:
        picked: float | None = None
        for tag in spec.tags:
            units = us_gaap.get(tag, {}).get("units", {}).get("USD")
            if not units:
                continue
            picked = _pick(units, fiscal_year, spec.is_flow)
            if picked is not None:
                break
        values[spec.field] = abs(picked) if (picked is not None and spec.absolute) else picked

    if all(v is None for v in values.values()):
        return None

    return Financials(
        corp_code=cik or str(facts.get("cik", "")),
        bsns_year=fiscal_year,
        # 미장에는 DART의 보고서 코드·연결구분 개념이 없다. 10-K는 연간·연결이라
        # 가장 가까운 값으로 채운다.
        reprt_code=ReportCode.ANNUAL,
        fs_div=FsDiv.CFS,
        currency="USD",
        **values,
    )
