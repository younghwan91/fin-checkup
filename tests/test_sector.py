"""업종별 판정 — 실데이터에서 KB금융이 🔴 4개로 나온 것을 계기로 추가.

건전한 은행을 위험하다고 표시하는 건 단순한 계산 오류가 아니라 사용자를 잘못된
판단으로 이끄는 문제다. 여기 테스트는 그 재발을 막는 게 목적이다.
"""

from __future__ import annotations

import pytest

from fin_checkup.metrics.engine import checkup
from fin_checkup.metrics.redflags import detect_red_flags
from fin_checkup.metrics.sector import Sector, sector_for
from fin_checkup.metrics.signals import Signal
from fin_checkup.models import Financials, FsDiv, ReportCode


def fin(**kwargs) -> Financials:
    return Financials(
        corp_code="105560", bsns_year=2024, reprt_code=ReportCode.ANNUAL,
        fs_div=FsDiv.CFS, **kwargs
    )


#: KB금융 2024년 실제 값에 가깝게 구성한 표본.
BANK = dict(
    total_assets=766_000_000_000_000,
    total_liabilities=705_000_000_000_000,
    total_equity=60_400_000_000_000,
    net_income=5_078_000_000_000,
    operating_income=6_700_000_000_000,
    interest_expense=14_600_000_000_000,
)


# ── 업종 판별 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("64992", Sector.FINANCIAL),   # KB금융 (실제 코드)
        ("64", Sector.FINANCIAL),
        ("65110", Sector.FINANCIAL),   # 보험
        ("66199", Sector.FINANCIAL),   # 금융 지원 서비스
        ("68100", Sector.REAL_ESTATE),
        ("264", Sector.GENERAL),       # 삼성전자 (실제 코드)
        ("26410", Sector.GENERAL),
        ("", Sector.GENERAL),
        (None, Sector.GENERAL),
        ("6", Sector.GENERAL),         # 판별 불가능한 길이
    ],
)
def test_sector_detection(code, expected):
    assert sector_for(code) is expected


# ── 핵심: 은행이 위험하게 표시되지 않는다 ─────────────────────────


def test_healthy_bank_is_not_marked_dangerous():
    result = checkup(fin(**BANK), sector=Sector.FINANCIAL)
    reds = [m.key for m in result.metrics if m.signal is Signal.RED]
    assert reds == [], f"건전한 은행에 🔴가 붙었다: {reds}"


def test_same_bank_would_be_misjudged_as_a_general_company():
    # 이 테스트는 수정 전 동작을 기록해둔다 — 왜 업종 구분이 필요한지의 근거다.
    general = checkup(fin(**BANK), sector=Sector.GENERAL)
    reds = {m.key for m in general.metrics if m.signal is Signal.RED}
    assert {"debt_ratio", "equity_ratio", "interest_coverage"} <= reds


@pytest.mark.parametrize(
    "key",
    [
        "debt_ratio", "equity_ratio", "current_ratio", "interest_coverage",
        "inventory_turnover", "receivable_turnover", "asset_turnover",
    ],
)
def test_inapplicable_metrics_are_marked_not_applicable_for_banks(key):
    metric = checkup(fin(**BANK), sector=Sector.FINANCIAL).by_key(key)
    assert metric is not None
    assert metric.signal is Signal.NOT_APPLICABLE
    assert "금융업에는 적용하지 않는다" in metric.note


def test_not_applicable_explains_what_to_look_at_instead():
    metric = checkup(fin(**BANK), sector=Sector.FINANCIAL).by_key("debt_ratio")
    assert "BIS" in metric.note


def test_bank_roa_uses_a_banking_band():
    """은행 ROA는 자릿수가 달라 별도 밴드를 쓴다.

    일반 밴드는 🟢 5% 이상, 금융 밴드는 🟢 1% 이상이다. 그래서 우량 은행 수준인
    1.2%가 금융 기준으로는 🟢, 일반 기준으로는 🟡이 된다.
    """
    good_bank = dict(BANK) | {"net_income": 9_200_000_000_000}  # ROA 약 1.2%
    financial = checkup(fin(**good_bank), sector=Sector.FINANCIAL).by_key("roa")
    general = checkup(fin(**good_bank), sector=Sector.GENERAL).by_key("roa")

    assert financial.value == pytest.approx(1.2, abs=0.05)
    assert financial.signal is Signal.GREEN
    assert general.signal is Signal.YELLOW


def test_weak_bank_roa_is_caught_by_the_banking_band():
    # ROA 0.3%는 은행으로선 부진하다. 일반 밴드는 흑자라는 이유로 🟡에 그친다.
    weak = dict(BANK) | {"net_income": 2_300_000_000_000}  # ROA 약 0.3%
    assert checkup(fin(**weak), sector=Sector.FINANCIAL).by_key("roa").signal is Signal.RED
    assert checkup(fin(**weak), sector=Sector.GENERAL).by_key("roa").signal is Signal.YELLOW


def test_roe_still_applies_to_banks():
    roe = checkup(fin(**BANK), sector=Sector.FINANCIAL).by_key("roe")
    assert roe.signal in (Signal.GREEN, Signal.YELLOW, Signal.RED)
    assert roe.value == pytest.approx(8.41, abs=0.1)


def test_growth_and_cash_quality_still_apply_to_banks():
    current = fin(**BANK, operating_cash_flow=3_000_000_000_000)
    prior = Financials(
        corp_code="105560", bsns_year=2023, reprt_code=ReportCode.ANNUAL,
        fs_div=FsDiv.CFS, net_income=4_600_000_000_000,
    )
    result = checkup(current, prior, sector=Sector.FINANCIAL)
    assert result.by_key("net_income_growth").signal is not Signal.NOT_APPLICABLE
    assert result.by_key("operating_cash_flow").signal is not Signal.NOT_APPLICABLE


# ── 위험 신호도 업종을 본다 ───────────────────────────────────────


def test_zombie_flag_is_not_raised_for_banks():
    history = [
        Financials(corp_code="105560", bsns_year=y, reprt_code=ReportCode.ANNUAL,
                   fs_div=FsDiv.CFS, operating_income=6_700_000_000_000,
                   interest_expense=14_600_000_000_000)
        for y in (2022, 2023, 2024)
    ]
    general = {f.key for f in detect_red_flags(history, sector=Sector.GENERAL)}
    financial = {f.key for f in detect_red_flags(history, sector=Sector.FINANCIAL)}
    assert "zombie_streak" in general
    assert "zombie_streak" not in financial, "은행은 이자비용이 영업의 원가다"


def test_real_operating_loss_is_still_flagged_for_banks():
    history = [
        Financials(corp_code="105560", bsns_year=y, reprt_code=ReportCode.ANNUAL,
                   fs_div=FsDiv.CFS, operating_income=-1_000_000_000)
        for y in (2022, 2023, 2024)
    ]
    keys = {f.key for f in detect_red_flags(history, sector=Sector.FINANCIAL)}
    assert "consecutive_operating_loss" in keys, "실제 적자는 업종과 무관하게 알려야 한다"


def test_capital_impairment_still_applies_to_banks():
    history = [fin(total_equity=-100, capital_stock=500)]
    keys = {f.key for f in detect_red_flags(history, sector=Sector.FINANCIAL)}
    assert "full_capital_impairment" in keys


# ── 일반 기업은 그대로 ────────────────────────────────────────────


def test_general_companies_are_unaffected():
    manufacturer = fin(
        total_liabilities=500, total_equity=1000, total_assets=1500,
        current_assets=2000, current_liabilities=1000,
        revenue=1000, operating_income=150, net_income=100,
    )
    result = checkup(manufacturer, sector=Sector.GENERAL)
    assert result.by_key("debt_ratio").signal is Signal.GREEN
    assert result.by_key("current_ratio").signal is Signal.GREEN
    assert not any(m.signal is Signal.NOT_APPLICABLE for m in result.metrics)


def test_real_estate_only_suppresses_inventory_turnover():
    result = checkup(fin(revenue=1000, inventories=200, total_assets=2000),
                     sector=Sector.REAL_ESTATE)
    assert result.by_key("inventory_turnover").signal is Signal.NOT_APPLICABLE
    assert result.by_key("asset_turnover").signal is not Signal.NOT_APPLICABLE


def test_default_sector_is_general():
    assert checkup(fin(revenue=1000, operating_income=100)).sector is Sector.GENERAL


def test_not_applicable_does_not_count_as_worst():
    result = checkup(fin(**BANK), sector=Sector.FINANCIAL)
    assert result.worst is not Signal.NOT_APPLICABLE
    assert result.counts[Signal.NOT_APPLICABLE] == 7
