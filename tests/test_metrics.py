from __future__ import annotations

import pytest

from fin_checkup.dart.normalize import normalize_statements
from fin_checkup.metrics.engine import checkup
from fin_checkup.metrics.signals import Signal
from fin_checkup.models import (
    AccountLine,
    CapexSource,
    Financials,
    FsDiv,
    InterestSource,
    RawStatements,
    ReceivableSource,
    ReportCode,
)


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
    # 값은 남기지 않는다. -500% 가 대조군에 들어가면 자본잠식 기업이 '최상위'로 정렬되고
    # 업종 중앙값과 경계값 근거(분위수)를 끌어내린다.
    assert m.value is None


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


def test_growth_compares_against_this_reports_prior_column_first():
    """작년 보고서 숫자보다 올해 보고서의 전기 열이 우선이다 — 재작성·연결 범위가 올해와 같다."""
    current = fin(revenue=1200, comparative=fin(bsns_year=2023, revenue=1000))
    stale = fin(bsns_year=2023, fs_div=FsDiv.OFS, revenue=500)
    assert get(checkup(current, prior=stale), "revenue_growth").value == pytest.approx(20.0)
    assert get(checkup(current), "revenue_growth").value == pytest.approx(20.0)


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


def test_turn_from_profit_to_loss_is_labelled():
    """전년 흑자 → 올해 적자. 증가율(−150%)은 그대로 두고 적자 전환을 비고로 밝힌다.

    2024 상장사 영업이익 203곳 · 순이익 250곳이 여기 해당하는데 숫자만 나갔다.
    """
    result = checkup(fin(operating_income=-50), prior=fin(bsns_year=2023, operating_income=100))
    metric = get(result, "operating_income_growth")
    assert metric.value == pytest.approx(-150.0)
    assert metric.signal is Signal.RED
    assert "흑자에서 적자로 돌아섰다" in metric.note


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


# ── 분모 0 ────────────────────────────────────────────────────────


def test_zero_inventory_is_not_applicable_rather_than_missing():
    """재고가 없는 서비스업은 재고자산을 0 으로 공시한다. 계정은 있었는데 '계정을 찾지
    못했다'(⚫)로 적으면 거짓말이다. 해당 없음(⊘)이 맞다."""
    m = get(checkup(fin(revenue=1000, inventories=0)), "inventory_turnover")
    assert m.signal is Signal.NOT_APPLICABLE
    assert "재고" in m.note and "찾지 못했다" not in m.note

    m = get(checkup(fin(revenue=1000, trade_receivables=0)), "receivable_turnover")
    assert m.signal is Signal.NOT_APPLICABLE


def test_missing_inventory_is_still_unknown():
    m = get(checkup(fin(revenue=1000)), "inventory_turnover")
    assert m.signal is Signal.UNKNOWN


def test_missing_revenue_with_zero_inventory_is_unknown_not_applicable():
    # 매출액 계정을 못 찾은 게 진짜 문제다. 재고 0 을 탓하면 정규화 결함이 가려진다.
    m = get(checkup(fin(inventories=0)), "inventory_turnover")
    assert m.signal is Signal.UNKNOWN


# ── 이자보상배율: 분모의 출처 ───────────────────────────────────────
# 금융원가에는 외환·파생 손실이 섞인다. 그걸 이자로 놓고 판정하면 삼성전자가
# 2.52배 🟡 가 된다(실제 이자 지급 기준 48배).


def test_finance_cost_proxy_is_measured_but_not_judged():
    m = get(
        checkup(fin(operating_income=50, interest_expense=100,
                    interest_expense_source=InterestSource.FINANCE_COSTS)),
        "interest_coverage",
    )
    assert m.value == pytest.approx(0.5), "값은 보여 준다"
    assert m.signal is Signal.NEUTRAL, "대체값으로 🔴 를 내지 않는다"
    assert m.proxy is True
    assert "금융원가" in m.note and "판정하지 않음" in m.note


def test_finance_cost_proxy_above_good_band_is_still_not_judged():
    m = get(
        checkup(fin(operating_income=500, interest_expense=100,
                    interest_expense_source=InterestSource.FINANCE_COSTS)),
        "interest_coverage",
    )
    assert m.signal is Signal.NEUTRAL


def test_zero_finance_costs_still_means_no_interest():
    # 금융원가 ⊇ 이자비용. 금융원가가 0 이면 이자도 0 이다.
    m = get(
        checkup(fin(operating_income=50, interest_expense=0,
                    interest_expense_source=InterestSource.FINANCE_COSTS)),
        "interest_coverage",
    )
    assert m.signal is Signal.GREEN
    assert m.proxy is False


def test_cash_flow_interest_paid_is_graded_with_a_note():
    m = get(
        checkup(fin(operating_income=500, interest_expense=100,
                    interest_expense_source=InterestSource.CASH_FLOW)),
        "interest_coverage",
    )
    assert m.signal is Signal.GREEN
    assert m.proxy is False
    assert "현금흐름표의 이자 지급액 기준" in m.note


def test_cash_flow_interest_paid_below_one_is_red_and_keeps_the_source_note():
    m = get(
        checkup(fin(operating_income=50, interest_expense=100,
                    interest_expense_source=InterestSource.CASH_FLOW)),
        "interest_coverage",
    )
    assert m.signal is Signal.RED
    assert "갚지 못하는" in m.note and "현금흐름표의 이자 지급액 기준" in m.note


def test_income_statement_interest_has_no_source_note():
    m = get(
        checkup(fin(operating_income=500, interest_expense=100,
                    interest_expense_source=InterestSource.INCOME_STATEMENT)),
        "interest_coverage",
    )
    assert m.signal is Signal.GREEN
    assert m.note == ""


def _samsung_2024(*, with_cash_flow: bool) -> Financials:
    """삼성전자 2024 연결 실공시 값(원). 손익계산서엔 이자비용 단독 계정이 없다."""
    lines = [
        AccountLine(sj_div="IS", account_id="dart_OperatingIncomeLoss", account_nm="영업이익",
                    thstrm_amount=32_725_961_000_000.0),
        AccountLine(sj_div="IS", account_id="ifrs-full_FinanceCosts", account_nm="금융비용",
                    thstrm_amount=12_985_684_000_000.0),
    ]
    if with_cash_flow:
        lines.append(AccountLine(
            sj_div="CF", account_id="ifrs-full_InterestPaidClassifiedAsOperatingActivities",
            account_nm="이자의 지급", thstrm_amount=675_049_000_000.0,
        ))
    return normalize_statements(RawStatements(
        corp_code="00126380", bsns_year=2024, reprt_code=ReportCode.ANNUAL,
        fs_div=FsDiv.CFS, lines=lines,
    ))


def test_samsung_regression_uses_interest_paid_not_finance_costs():
    m = get(checkup(_samsung_2024(with_cash_flow=True)), "interest_coverage")
    assert m.value == pytest.approx(48.48, abs=0.01)
    assert m.signal is Signal.GREEN, "2.52배 🟡 는 외환손실을 이자로 센 결과였다"


def test_samsung_without_cash_flow_line_is_not_judged():
    m = get(checkup(_samsung_2024(with_cash_flow=False)), "interest_coverage")
    assert m.value == pytest.approx(2.52, abs=0.01)
    assert m.signal is Signal.NEUTRAL


# ── 매출채권회전율: 분모의 출처 ─────────────────────────────────────


def test_receivable_turnover_on_aggregate_is_kept_with_a_basis_note():
    m = get(
        checkup(fin(revenue=1000, trade_receivables=250,
                    trade_receivables_source=ReceivableSource.TRADE_AND_OTHER)),
        "receivable_turnover",
    )
    assert m.value == pytest.approx(4.0)
    assert m.signal is Signal.NEUTRAL
    assert m.proxy is False, "중앙값 4% 차이 — 대조군에서 빼지 않는다"
    assert "매출채권및기타채권" in m.note and "낮게" in m.note


def test_receivable_turnover_on_pure_line_has_no_note():
    m = get(
        checkup(fin(revenue=1000, trade_receivables=250,
                    trade_receivables_source=ReceivableSource.TRADE)),
        "receivable_turnover",
    )
    assert "매출채권및기타채권" not in m.note


# ── 잉여현금흐름: 설비투자의 출처 ────────────────────────────────────


@pytest.mark.parametrize("source,piece", [
    (CapexSource.PPE_ITEMIZED, "종류별"),
    (CapexSource.PPE_AND_INTANGIBLE, "무형자산"),
])
def test_fcf_states_its_capex_basis_and_is_still_judged(source, piece):
    m = get(checkup(fin(operating_cash_flow=500, capex=200, capex_source=source)), "fcf")
    assert m.value == 300
    assert m.signal is Signal.GREEN, "유·무형 합계로 부호가 바뀌는 곳은 2% — 판정은 그대로"
    assert m.proxy is False
    assert piece in m.note


def test_fcf_on_ppe_total_has_no_basis_note():
    m = get(checkup(fin(operating_cash_flow=500, capex=200, capex_source=CapexSource.PPE)), "fcf")
    assert m.note == ""


# ── 분모가 아주 작은 값 (Q14): 값·판정은 그대로, 사실만 덧붙인다 ─────────


def test_inventory_smaller_than_a_days_revenue_gets_a_note_not_a_cap():
    """엔비티 2024 재고자산회전율 18만 회 — 계산은 맞다. 상한도 ⊘ 도 없이 이유만 적는다."""
    from fin_checkup.metrics.engine import TINY_INVENTORY_NOTE

    m = get(checkup(fin(revenue=188_349_000, inventories=1_000)), "inventory_turnover")
    assert m.value == pytest.approx(188_349)
    assert m.signal is Signal.NEUTRAL
    assert TINY_INVENTORY_NOTE in m.note
    assert "하루 매출보다 적어" in TINY_INVENTORY_NOTE


def test_inventory_note_starts_above_365_turns():
    from fin_checkup.metrics.engine import TINY_INVENTORY_NOTE

    at_one_day = get(checkup(fin(revenue=365, inventories=1)), "inventory_turnover")
    assert TINY_INVENTORY_NOTE not in at_one_day.note
    above = get(checkup(fin(revenue=366, inventories=1)), "inventory_turnover")
    assert TINY_INVENTORY_NOTE in above.note


def test_receivables_smaller_than_a_days_revenue_keep_the_aggregate_note_too():
    from fin_checkup.metrics.engine import RECEIVABLE_AGGREGATE_NOTE, TINY_RECEIVABLE_NOTE

    m = get(
        checkup(fin(revenue=1_000_000, trade_receivables=100,
                    trade_receivables_source=ReceivableSource.TRADE_AND_OTHER)),
        "receivable_turnover",
    )
    assert m.value == pytest.approx(10_000) and m.signal is Signal.NEUTRAL
    assert TINY_RECEIVABLE_NOTE in m.note and RECEIVABLE_AGGREGATE_NOTE in m.note


@pytest.mark.parametrize("key", ["operating_margin", "net_margin"])
def test_margin_on_a_tiny_revenue_keeps_its_value_and_signal_with_a_note(key):
    """매출 1억 미만 바이오의 영업이익률 −20만 % — 적자라 🔴 는 맞다. 큰 숫자의 이유를 적는다."""
    from fin_checkup.metrics.engine import TINY_REVENUE_NOTE

    m = get(checkup(fin(revenue=14_708_000, operating_income=-32_905_000_000,
                        net_income=-32_000_000_000)), key)
    assert m.value < -100_000 and m.signal is Signal.RED
    assert TINY_REVENUE_NOTE in m.note


def test_ordinary_margin_has_no_tiny_revenue_note():
    from fin_checkup.metrics.engine import TINY_REVENUE_NOTE

    m = get(checkup(fin(revenue=100, operating_income=-900, net_income=-999)), "operating_margin")
    assert m.value == pytest.approx(-900)
    assert TINY_REVENUE_NOTE not in m.note
