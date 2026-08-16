from __future__ import annotations

from fin_checkup.metrics.redflags import detect_red_flags
from fin_checkup.models import Financials, FsDiv, ReportCode


def fin(year: int, **kwargs) -> Financials:
    return Financials(
        corp_code="00126380", bsns_year=year,
        reprt_code=ReportCode.ANNUAL, fs_div=FsDiv.CFS, **kwargs
    )


def keys(flags) -> set[str]:
    return {f.key for f in flags}


def test_no_history_no_flags():
    assert detect_red_flags([]) == []


def test_healthy_company_has_no_flags():
    history = [
        fin(y, total_equity=1000, capital_stock=500, operating_income=100,
            net_income=80, operating_cash_flow=120, interest_expense=10)
        for y in (2022, 2023, 2024)
    ]
    assert detect_red_flags(history) == []


def test_full_capital_impairment():
    flags = detect_red_flags([fin(2024, total_equity=-100, capital_stock=500)])
    assert "full_capital_impairment" in keys(flags)


def test_partial_capital_impairment_over_50pct():
    # 자본금 1000, 자기자본 400 → 잠식률 60%
    flags = detect_red_flags([fin(2024, total_equity=400, capital_stock=1000)])
    assert "partial_capital_impairment" in keys(flags)


def test_no_impairment_flag_when_equity_exceeds_capital():
    flags = detect_red_flags([fin(2024, total_equity=1500, capital_stock=1000)])
    assert not keys(flags) & {"full_capital_impairment", "partial_capital_impairment"}


def test_impairment_is_silent_without_capital_stock():
    # 자본금을 못 찾으면 잠식률을 추정하지 않는다.
    flags = detect_red_flags([fin(2024, total_equity=400)])
    assert "partial_capital_impairment" not in keys(flags)


def test_three_consecutive_operating_losses():
    history = [fin(y, operating_income=-10) for y in (2022, 2023, 2024)]
    flags = detect_red_flags(history)
    assert "consecutive_operating_loss" in keys(flags)


def test_two_losses_are_not_flagged_yet():
    history = [fin(2022, operating_income=50), fin(2023, operating_income=-10),
               fin(2024, operating_income=-10)]
    assert "consecutive_operating_loss" not in keys(detect_red_flags(history))


def test_streak_must_be_trailing():
    # 과거에 3년 연속 적자였어도 최근에 흑자로 돌아섰으면 해당 없음.
    history = [fin(y, operating_income=-10) for y in (2020, 2021, 2022)]
    history.append(fin(2023, operating_income=100))
    assert "consecutive_operating_loss" not in keys(detect_red_flags(history))


def test_profit_without_cash():
    flags = detect_red_flags([fin(2024, net_income=100, operating_cash_flow=-30)])
    assert "profit_without_cash" in keys(flags)


def test_zombie_streak():
    history = [fin(y, operating_income=5, interest_expense=100) for y in (2022, 2023, 2024)]
    assert "zombie_streak" in keys(detect_red_flags(history))


def test_zombie_streak_ignores_debt_free_years():
    history = [fin(y, operating_income=5, interest_expense=0) for y in (2022, 2023, 2024)]
    assert "zombie_streak" not in keys(detect_red_flags(history))


def test_history_order_does_not_matter():
    history = [fin(2024, operating_income=-10), fin(2022, operating_income=-10),
               fin(2023, operating_income=-10)]
    assert "consecutive_operating_loss" in keys(detect_red_flags(history))


def test_flags_state_facts_without_recommendation():
    flags = detect_red_flags([fin(2024, total_equity=-100, capital_stock=500,
                                  net_income=100, operating_cash_flow=-30)])
    assert flags
    for flag in flags:
        text = flag.label + flag.detail + flag.reference
        for word in ("매수", "매도", "추천", "사세요", "파세요"):
            assert word not in text
