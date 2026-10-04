from __future__ import annotations

import httpx
import pytest
import respx

from fin_checkup.config import Settings
from fin_checkup.metrics.engine import checkup
from fin_checkup.sec.client import SecClient, SecError
from fin_checkup.sec.normalize import normalize_company_facts


@pytest.fixture
def sec_settings() -> Settings:
    return Settings(sec_user_agent="fin-checkup test test@example.com")


@pytest.fixture
def client(sec_settings) -> SecClient:
    return SecClient(settings=sec_settings)


def units(*entries: dict) -> dict:
    return {"units": {"USD": list(entries)}}


def entry(end: str, val: float, *, start: str | None = None, fy: int = 2023,
          form: str = "10-K", filed: str = "2023-11-03") -> dict:
    e = {"end": end, "val": val, "fy": fy, "fp": "FY", "form": form, "filed": filed}
    if start:
        e["start"] = start
    return e


def facts(**tags) -> dict:
    return {"cik": 320193, "entityName": "Apple Inc.", "facts": {"us-gaap": tags}}


# ── 정규화 ────────────────────────────────────────────────────────


def test_picks_the_value_matching_the_fiscal_year_end():
    # 10-K 하나에 전년도 값이 함께 들어 있다. 2023을 요청하면 2023-09-30을 골라야 한다.
    data = facts(
        Assets=units(
            entry("2022-09-24", 352755000000),
            entry("2023-09-30", 352583000000),
        )
    )
    fin = normalize_company_facts(data, 2023)
    assert fin is not None
    assert fin.total_assets == 352583000000

    prior = normalize_company_facts(data, 2022)
    assert prior is not None
    assert prior.total_assets == 352755000000


def test_quarterly_flows_are_excluded():
    data = facts(
        Revenues=units(
            entry("2023-09-30", 90000000, start="2023-07-01"),   # 분기
            entry("2023-09-30", 383285000000, start="2022-10-01"),  # 연간
        )
    )
    fin = normalize_company_facts(data, 2023)
    assert fin is not None
    assert fin.revenue == 383285000000


def test_point_in_time_items_do_not_require_a_start_date():
    fin = normalize_company_facts(facts(Assets=units(entry("2023-09-30", 100.0))), 2023)
    assert fin is not None and fin.total_assets == 100.0


def test_non_10k_forms_are_ignored():
    data = facts(Assets=units(entry("2023-09-30", 100.0, form="10-Q")))
    assert normalize_company_facts(data, 2023) is None


def test_10ka_amendment_is_accepted():
    data = facts(Assets=units(entry("2023-09-30", 100.0, form="10-K/A")))
    fin = normalize_company_facts(data, 2023)
    assert fin is not None and fin.total_assets == 100.0


def test_latest_filing_wins_on_restatement():
    data = facts(
        Assets=units(
            entry("2023-09-30", 100.0, filed="2023-11-03"),
            entry("2023-09-30", 111.0, filed="2024-02-01"),
        )
    )
    fin = normalize_company_facts(data, 2023)
    assert fin is not None and fin.total_assets == 111.0


def test_tag_priority_order():
    data = facts(
        Revenues=units(entry("2023-09-30", 111.0, start="2022-10-01")),
        RevenueFromContractWithCustomerExcludingAssessedTax=units(
            entry("2023-09-30", 999.0, start="2022-10-01")
        ),
    )
    fin = normalize_company_facts(data, 2023)
    assert fin is not None and fin.revenue == 999.0


def test_net_interest_income_is_not_treated_as_interest_expense():
    """InterestIncomeExpenseNet 은 양수면 순이자수익이다. abs() 로 비용 취급하면 현금이 많아
    이자수익이 큰 기업일수록 이자보상배율이 낮아져 좀비로 찍힌다."""
    income = facts(
        Assets=units(entry("2023-09-30", 1)),
        InterestIncomeExpenseNet=units(entry("2023-09-30", 2_000_000_000, start="2022-10-01")),
    )
    fin = normalize_company_facts(income, 2023)
    assert fin is not None
    assert fin.interest_expense is None

    expense = facts(
        Assets=units(entry("2023-09-30", 1)),
        InterestIncomeExpenseNet=units(entry("2023-09-30", -500_000_000, start="2022-10-01")),
    )
    fin = normalize_company_facts(expense, 2023)
    assert fin is not None
    assert fin.interest_expense == 500_000_000


def test_fiscal_years_ending_in_early_january_belong_to_the_prior_year():
    """52/53주 결산 기업은 토요일에 마감해 FY2021 이 2022-01-01 에 끝나기도 한다.
    end.year 로 고르면 그 해가 통째로 사라져 다음 해의 전년 비교(성장성 3개)가 ⚫가 된다."""
    data = facts(
        Assets=units(
            entry("2022-01-01", 100, fy=2021, filed="2022-02-20"),
            entry("2022-12-31", 120, fy=2022, filed="2023-02-20"),
        )
    )
    assert normalize_company_facts(data, 2021).total_assets == 100
    assert normalize_company_facts(data, 2022).total_assets == 120


def test_capex_is_positive():
    data = facts(
        PaymentsToAcquirePropertyPlantAndEquipment=units(
            entry("2023-09-30", -10949000000, start="2022-10-01")
        ),
        NetCashProvidedByUsedInOperatingActivities=units(
            entry("2023-09-30", 110543000000, start="2022-10-01")
        ),
    )
    fin = normalize_company_facts(data, 2023)
    assert fin is not None
    assert fin.capex == 10949000000
    assert fin.free_cash_flow == 110543000000 - 10949000000


def test_capital_stock_is_left_empty_for_us_filers():
    # 자본잠식률은 한국 규정 개념이라 미국 기업에 붙이지 않는다.
    data = facts(CommonStockValue=units(entry("2023-09-30", 73812000000)))
    fin = normalize_company_facts(data, 2023)
    assert fin is None or fin.capital_stock is None


def test_missing_year_returns_none():
    assert normalize_company_facts(facts(Assets=units(entry("2023-09-30", 1.0))), 2019) is None


def test_empty_facts_returns_none():
    assert normalize_company_facts({"facts": {}}, 2023) is None


def test_non_numeric_values_are_skipped():
    data = facts(Assets=units({"end": "2023-09-30", "val": None, "form": "10-K"}))
    assert normalize_company_facts(data, 2023) is None


# ── 지표 엔진 재사용 ──────────────────────────────────────────────


def test_metrics_engine_works_unchanged_on_us_data():
    data = facts(
        Assets=units(entry("2023-09-30", 352583000000)),
        Liabilities=units(entry("2023-09-30", 290437000000)),
        StockholdersEquity=units(entry("2023-09-30", 62146000000)),
        RevenueFromContractWithCustomerExcludingAssessedTax=units(
            entry("2023-09-30", 383285000000, start="2022-10-01")
        ),
        OperatingIncomeLoss=units(entry("2023-09-30", 114301000000, start="2022-10-01")),
        NetIncomeLoss=units(entry("2023-09-30", 96995000000, start="2022-10-01")),
    )
    fin = normalize_company_facts(data, 2023)
    assert fin is not None

    result = checkup(fin)
    margin = result.by_key("operating_margin")
    assert margin is not None
    assert margin.value == pytest.approx(29.82, abs=0.1)

    debt = result.by_key("debt_ratio")
    assert debt is not None
    assert debt.value == pytest.approx(467.3, abs=1.0)


# ── 클라이언트 ────────────────────────────────────────────────────


def test_user_agent_is_required():
    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        SecClient(settings=Settings(sec_user_agent=""))


@respx.mock
async def test_fetch_tickers(client: SecClient):
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
                "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            },
        )
    )
    tickers = await client.fetch_tickers()
    assert [t.ticker for t in tickers] == ["NVDA", "AAPL"]
    assert tickers[1].cik_padded == "0000320193"
    await client.aclose()


@respx.mock
async def test_user_agent_header_is_sent(client: SecClient):
    route = respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(200, json={})
    )
    await client.fetch_tickers()
    assert "test@example.com" in route.calls.last.request.headers["user-agent"]
    await client.aclose()


@respx.mock
async def test_company_facts_404_returns_none(client: SecClient):
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json").mock(
        return_value=httpx.Response(404)
    )
    assert await client.fetch_company_facts("1") is None
    await client.aclose()


@respx.mock
async def test_company_facts_error_raises(client: SecClient):
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json").mock(
        return_value=httpx.Response(403, text="denied")
    )
    with pytest.raises(SecError):
        await client.fetch_company_facts("1")
    await client.aclose()


@respx.mock
async def test_fetch_history_makes_one_request_for_many_years(client: SecClient):
    data = facts(
        Assets=units(
            entry("2021-09-25", 351002000000),
            entry("2022-09-24", 352755000000),
            entry("2023-09-30", 352583000000),
        )
    )
    route = respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").mock(
        return_value=httpx.Response(200, json=data)
    )
    history = await client.fetch_history("320193", end_year=2023, years=5)
    assert [f.bsns_year for f in history] == [2021, 2022, 2023]
    assert route.call_count == 1, "여러 해를 위해 한 번만 호출해야 한다"
    await client.aclose()
