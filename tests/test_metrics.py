from __future__ import annotations

import pytest

from fin_checkup.metrics.engine import checkup
from fin_checkup.metrics.signals import Signal
from fin_checkup.models import Financials, FsDiv, ReportCode


def fin(**kwargs) -> Financials:
    base = dict(
        corp_code="00126380",
        bsns_year=2024,
        reprt_code=ReportCode.ANNUAL,
        fs_div=FsDiv.CFS,
    )
    return Financials(**{**base, **kwargs})


def get(result, key):
    metric = result.by_key(key)
    assert metric is not None, f"지표 {key}가 없다"
    return metric


# ── 수익성 ────────────────────────────────────────────────────────


def test_operating_margin():
    m = get(checkup(fin(revenue=1000, operating_income=150)), "operating_margin")
    assert m.value == pytest.approx(15.0)
    assert m.signal is Signal.GREEN


def test_roe_uses_equity():
    m = get(checkup(fin(net_income=120, total_equity=1000)), "roe")
    assert m.value == pytest.approx(12.0)


def test_roe_is_unknown_when_equity_is_negative():
    # 자본잠식 상태에서 ROE는 수학적으로는 나오지만 의미가 없다.
    m = get(checkup(fin(net_income=-50, total_equity=-200)), "roe")
    assert m.signal is Signal.UNKNOWN
    assert "자본잠식" in m.note


# ── 안정성 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("liabilities", "equity", "expected"),
    [
        (500, 1000, Signal.GREEN),  # 50%
        (1500, 1000, Signal.YELLOW),  # 150%
        (2500, 1000, Signal.RED),  # 250% — 계획서: 200%↑ 주의
    ],
)
def test_debt_ratio_signals(liabilities, equity, expected):
    m = get(checkup(fin(total_liabilities=liabilities, total_equity=equity)), "debt_ratio")
    assert m.signal is expected


def test_debt_ratio_is_red_on_capital_impairment():
    m = get(checkup(fin(total_liabilities=500, total_equity=-100)), "debt_ratio")
    assert m.signal is Signal.RED
    assert "자본잠식" in m.note


@pytest.mark.parametrize(
    ("ca", "cl", "expected"),
    [(2000, 1000, Signal.GREEN), (1200, 1000, Signal.YELLOW), (800, 1000, Signal.RED)],
)
def test_current_ratio_signals(ca, cl, expected):
    m = get(checkup(fin(current_assets=ca, current_liabilities=cl)), "current_ratio")
    assert m.signal is expected


def test_interest_coverage_below_one_is_red_zombie():
    m = get(checkup(fin(operating_income=50, interest_expense=100)), "interest_coverage")
    assert m.value == pytest.approx(0.5)
    assert m.signal is Signal.RED
    assert "이자" in m.note


def test_interest_coverage_with_no_interest_expense_is_green():
    # 이자비용이 0이면 무차입. 나눗셈 오류가 아니라 최상 상태다.
    m = get(checkup(fin(operating_income=50, interest_expense=0)), "interest_coverage")
    assert m.signal is Signal.GREEN
    assert m.value is None


def test_interest_coverage_is_red_when_operating_loss():
    m = get(checkup(fin(operating_income=-10, interest_expense=100)), "interest_coverage")
    assert m.signal is Signal.RED


def test_equity_ratio_and_capital_impairment_rate():
    result = checkup(fin(total_equity=-50, total_assets=1000))
    assert get(result, "equity_ratio").signal is Signal.RED


# ── 성장성 ────────────────────────────────────────────────────────


def test_revenue_growth_needs_prior_year():
    result = checkup(fin(revenue=1200), prior=fin(bsns_year=2023, revenue=1000))
    m = get(result, "revenue_growth")
    assert m.value == pytest.approx(20.0)
    assert m.signal is Signal.GREEN


def test_growth_is_unknown_without_prior_year():
    m = get(checkup(fin(revenue=1200)), "revenue_growth")
    assert m.signal is Signal.UNKNOWN


def test_growth_percentage_is_not_reported_from_a_negative_base():
    # 적자 -100 → -50은 '50% 개선'이 아니다. 퍼센트 자체를 내지 않는다.
    result = checkup(
        fin(operating_income=-50), prior=fin(bsns_year=2023, operating_income=-100)
    )
    assert get(result, "operating_income_growth").value is None


def test_continued_loss_is_red_not_hidden():
    """전년 적자라고 ⚫로 묻지 않는다.

    실측에서 상장사의 40%가 전년 적자였다. 퍼센트는 못 내도 '적자가 이어진다'는
    사실은 말할 수 있고, 그게 사용자가 알아야 하는 정보다.
    """
    result = checkup(
        fin(operating_income=-50), prior=fin(bsns_year=2023, operating_income=-100)
    )
    metric = get(result, "operating_income_growth")
    assert metric.signal is Signal.RED
    assert "적자가 이어지고" in metric.note


def test_turnaround_to_profit_is_green():
    result = checkup(
        fin(operating_income=30), prior=fin(bsns_year=2023, operating_income=-100)
    )
    metric = get(result, "operating_income_growth")
    assert metric.signal is Signal.GREEN
    assert "흑자로 돌아섰다" in metric.note


def test_widening_loss_is_distinguished_from_narrowing_loss():
    narrowing = get(
        checkup(fin(net_income=-50), prior=fin(bsns_year=2023, net_income=-100)),
        "net_income_growth",
    )
    widening = get(
        checkup(fin(net_income=-200), prior=fin(bsns_year=2023, net_income=-100)),
        "net_income_growth",
    )
    assert narrowing.signal is widening.signal is Signal.RED
    assert "손실 폭은 줄었다" in narrowing.note
    assert "손실 폭이 커졌다" in widening.note


def test_zero_base_stays_unknown():
    result = checkup(fin(net_income=50), prior=fin(bsns_year=2023, net_income=0))
    assert get(result, "net_income_growth").signal is Signal.UNKNOWN


# ── 이익의 질 ─────────────────────────────────────────────────────


def test_positive_profit_with_negative_ocf_is_red():
    result = checkup(fin(net_income=100, operating_cash_flow=-50))
    m = get(result, "operating_cash_flow")
    assert m.signal is Signal.RED
    assert "흑자" in m.note


def test_ocf_to_net_income_ratio():
    m = get(checkup(fin(net_income=100, operating_cash_flow=130)), "ocf_to_net_income")
    assert m.value == pytest.approx(1.3)
    assert m.signal is Signal.GREEN


def test_ocf_to_net_income_far_below_one_is_red():
    m = get(checkup(fin(net_income=100, operating_cash_flow=20)), "ocf_to_net_income")
    assert m.signal is Signal.RED


def test_fcf():
    m = get(checkup(fin(operating_cash_flow=500, capex=200)), "fcf")
    assert m.value == pytest.approx(300.0)
    assert m.signal is Signal.GREEN


# ── 효율성 ────────────────────────────────────────────────────────


def test_asset_turnover():
    m = get(checkup(fin(revenue=1000, total_assets=2000)), "asset_turnover")
    assert m.value == pytest.approx(0.5)


def test_inventory_turnover():
    m = get(checkup(fin(revenue=1000, inventories=200)), "inventory_turnover")
    assert m.value == pytest.approx(5.0)


# ── 누락 데이터 ───────────────────────────────────────────────────


def test_missing_inputs_yield_unknown_not_zero():
    result = checkup(fin())
    for metric in result.metrics:
        assert metric.signal is Signal.UNKNOWN, f"{metric.key}는 데이터가 없으므로 UNKNOWN이어야 한다"
        assert metric.value is None


def test_zero_denominator_is_unknown_not_crash():
    m = get(checkup(fin(revenue=0, operating_income=100)), "operating_margin")
    assert m.signal is Signal.UNKNOWN
    assert m.value is None


# ── 결과 집계 ─────────────────────────────────────────────────────


def test_every_metric_carries_a_plain_language_explanation():
    for metric in checkup(fin()).metrics:
        assert metric.description, f"{metric.key}에 설명이 없다"


def test_summary_counts_and_worst_signal():
    result = checkup(
        fin(
            revenue=1000, operating_income=150, net_income=100,
            total_liabilities=2500, total_equity=1000,
        )
    )
    assert result.counts[Signal.RED] >= 1
    assert result.worst is Signal.RED


def test_result_reports_no_recommendation():
    # 규제 안전선: 어떤 지표도 매수/매도 문구를 담지 않는다.
    banned = ("매수", "매도", "추천", "사세요", "파세요", "적기")
    for metric in checkup(fin(revenue=1000, operating_income=150)).metrics:
        text = f"{metric.label}{metric.description}{metric.note}"
        for word in banned:
            assert word not in text, f"{metric.key}에 투자권유 문구 '{word}'"


# ── 적자 구간의 현금흐름 비율 (실데이터에서 발견) ─────────────────


def test_ocf_ratio_is_not_applicable_when_loss_making():
    """순손실 -100에 영업현금 +50이면 비율은 -0.5지만 나쁜 신호가 아니다.

    적자인데 현금이 들어온 건 오히려 좋다. 뜻이 뒤집히는 구간이라 판정하지 않는다.
    """
    m = get(checkup(fin(net_income=-100, operating_cash_flow=50)), "ocf_to_net_income")
    assert m.signal is Signal.NOT_APPLICABLE
    assert m.value is None
    assert "적자" in m.note


def test_ocf_ratio_still_applies_when_profitable():
    m = get(checkup(fin(net_income=100, operating_cash_flow=130)), "ocf_to_net_income")
    assert m.signal is Signal.GREEN
    assert m.value == pytest.approx(1.3)


def test_ocf_ratio_at_break_even_is_not_applicable():
    m = get(checkup(fin(net_income=0, operating_cash_flow=50)), "ocf_to_net_income")
    assert m.signal is Signal.NOT_APPLICABLE


def test_operating_cash_flow_itself_still_judges_loss_makers():
    # 비율은 못 봐도 영업현금흐름 자체는 계속 본다.
    m = get(checkup(fin(net_income=-100, operating_cash_flow=50)), "operating_cash_flow")
    assert m.signal is Signal.GREEN


# ── 재조정된 경계값 (2024년 상장사 880사 실측 기준) ───────────────


@pytest.mark.parametrize(
    ("margin", "expected"),
    [
        (10.0, Signal.GREEN),    # 시장 상위 25% (p75=7.0)
        (7.0, Signal.GREEN),
        (3.0, Signal.YELLOW),    # 흑자지만 평범
        (0.1, Signal.YELLOW),
        (-1.0, Signal.RED),      # 적자는 업종과 무관하게 위험
    ],
)
def test_operating_margin_band_matches_the_market(margin, expected):
    revenue = 1000.0
    m = get(checkup(fin(revenue=revenue, operating_income=revenue * margin / 100)), "operating_margin")
    assert m.signal is expected


def test_loss_making_is_red_across_profitability_metrics():
    result = checkup(
        fin(revenue=1000, operating_income=-50, net_income=-80, total_assets=2000, total_equity=1000)
    )
    for key in ("operating_margin", "net_margin", "roe", "roa"):
        assert get(result, key).signal is Signal.RED, f"{key}가 적자인데 🔴가 아니다"


def test_stability_bands_stay_absolute():
    # 안정성은 시장 분포가 아니라 "갚을 수 있는가"라는 절대 기준을 따른다.
    from fin_checkup.metrics.engine import METRIC_DEFS

    bands = {d.key: d.band for d in METRIC_DEFS}
    assert bands["debt_ratio"].good == 100 and bands["debt_ratio"].warn == 200
    assert bands["interest_coverage"].warn == 1, "이자를 갚을 수 있는 경계는 1이다"
