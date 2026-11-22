"""'왜 이 색' 문장 — 엔진이 실제로 낸 Metric 으로 확인한다."""

from __future__ import annotations

import pytest

from fin_checkup.explain import banned
from fin_checkup.explain._korean import because, if_, topic
from fin_checkup.explain.why import why_color
from fin_checkup.metrics.engine import checkup
from fin_checkup.metrics.peers import PeerScope, PeerStat, SelfDelta
from fin_checkup.metrics.sector import Sector
from fin_checkup.metrics.signals import Signal
from fin_checkup.models import (
    CapexSource,
    Financials,
    FsDiv,
    InterestSource,
    ReceivableSource,
    ReportCode,
)


def fin(year: int = 2024, **kwargs) -> Financials:
    return Financials(
        corp_code="00000000", bsns_year=year, reprt_code=ReportCode.ANNUAL, fs_div=FsDiv.CFS, **kwargs
    )


def metric(key: str, cur: dict, prior: dict | None = None, sector: Sector = Sector.GENERAL):
    result = checkup(fin(**cur), fin(2023, **prior) if prior is not None else None, sector=sector)
    return result.by_key(key)


BANK = dict(
    total_assets=500_000,
    total_liabilities=460_000,
    total_equity=40_000,
    revenue=30_000,
    operating_income=5_000,
    net_income=4_000,
    interest_expense=8_000,
    current_assets=100,
    current_liabilities=100,
)

#: (지표, 당해, 전년, 업종, 기대 신호, 문장에 반드시 들어갈 조각)
CASES = [
    (
        "operating_margin",
        dict(revenue=1000, operating_income=150),
        None,
        Sector.GENERAL,
        Signal.GREEN,
        ["15.00%는", "7% 이상이라", "🟢 정상이에요"],
    ),
    (
        "operating_margin",
        dict(revenue=1000, operating_income=30),
        None,
        Sector.GENERAL,
        Signal.YELLOW,
        ["3.00%는", "0% 이상 7% 미만이라", "🟡 주의예요."],
    ),
    (
        "operating_margin",
        dict(revenue=1000, operating_income=-30),
        None,
        Sector.GENERAL,
        Signal.RED,
        ["-3.00%는", "0% 미만이라", "🔴 위험"],
    ),
    (
        "debt_ratio",
        dict(total_liabilities=150, total_equity=100),
        None,
        Sector.GENERAL,
        Signal.YELLOW,
        ["150.00%는", "100% 초과 200% 이하라"],
    ),
    (
        "debt_ratio",
        dict(total_liabilities=300, total_equity=100),
        None,
        Sector.GENERAL,
        Signal.RED,
        ["200% 초과라"],
    ),
    (
        "interest_coverage",
        dict(operating_income=50, interest_expense=10),
        None,
        Sector.GENERAL,
        Signal.GREEN,
        ["5.00배는", "3배 이상이라"],
    ),
    # 은행 — 일반 기준이면 🔴 인 지표가 ⊘ + 대신 볼 것
    (
        "debt_ratio",
        BANK,
        None,
        Sector.FINANCIAL,
        Signal.NOT_APPLICABLE,
        ["⊘ 해당 없음", "금융업에는 맞지 않아", "대신 볼 것: 은행은 BIS 자기자본비율"],
    ),
    ("interest_coverage", BANK, None, Sector.FINANCIAL, Signal.NOT_APPLICABLE, ["⊘", "순이자마진"]),
    ("current_ratio", BANK, None, Sector.FINANCIAL, Signal.NOT_APPLICABLE, ["⊘", "유동성커버리지비율"]),
    # 은행 ROA 는 금융업 밴드(1/0.5)
    ("roa", BANK, None, Sector.FINANCIAL, Signal.YELLOW, ["0.80%는", "0.5% 이상 1% 미만이라", "🟡 주의"]),
    # 부동산업 재고
    (
        "inventory_turnover",
        dict(revenue=100, inventories=500),
        None,
        Sector.REAL_ESTATE,
        Signal.NOT_APPLICABLE,
        ["부동산업에는 맞지 않아", "분양"],
    ),
    # ⚫ — 계정 없음
    (
        "operating_margin",
        dict(revenue=1000),
        None,
        Sector.GENERAL,
        Signal.UNKNOWN,
        ["⚫ 데이터 없음", "영업이익 ÷ 매출액 × 100", "0으로 채우지 않고"],
    ),
    (
        "revenue_growth",
        dict(revenue=1000),
        None,
        Sector.GENERAL,
        Signal.UNKNOWN,
        ["⚫", "작년 재무가 없거나"],
    ),
    ("roe", dict(net_income=10, total_equity=-5), None, Sector.GENERAL, Signal.UNKNOWN, ["⚫", "자본잠식"]),
    (
        "net_income_growth",
        dict(net_income=5),
        dict(net_income=0),
        Sector.GENERAL,
        Signal.UNKNOWN,
        ["⚫", "정확히 0"],
    ),
    # 값 없이 규칙으로 색이 정해짐
    (
        "operating_income_growth",
        dict(operating_income=10),
        dict(operating_income=-5),
        Sector.GENERAL,
        Signal.GREEN,
        ["🟢 정상이에요", "흑자 전환"],
    ),
    (
        "net_income_growth",
        dict(net_income=-8),
        dict(net_income=-5),
        Sector.GENERAL,
        Signal.RED,
        ["🔴 위험이에요", "적자 지속"],
    ),
    (
        "interest_coverage",
        dict(operating_income=10, interest_expense=0),
        None,
        Sector.GENERAL,
        Signal.GREEN,
        ["🟢", "빚이 없다고"],
    ),
    (
        "debt_ratio",
        dict(total_liabilities=100, total_equity=0),
        None,
        Sector.GENERAL,
        Signal.RED,
        ["🔴", "자본잠식"],
    ),
    # ⊘ — 업종이 아니라 값 때문에
    (
        "ocf_to_net_income",
        dict(operating_cash_flow=5, net_income=-1),
        None,
        Sector.GENERAL,
        Signal.NOT_APPLICABLE,
        ["⊘", "올해 적자라"],
    ),
    (
        "receivable_turnover",
        dict(revenue=100, trade_receivables=0),
        None,
        Sector.GENERAL,
        Signal.NOT_APPLICABLE,
        ["⊘", "매출채권이 0으로"],
    ),
    # ⚪ — 경계값 없음
    (
        "asset_turnover",
        dict(revenue=85, total_assets=100),
        None,
        Sector.GENERAL,
        Signal.NEUTRAL,
        ["0.85회는", "⚪ 측정값"],
    ),
    # 금액 지표
    (
        "operating_cash_flow",
        dict(operating_cash_flow=120_000_000_000),
        None,
        Sector.GENERAL,
        Signal.GREEN,
        ["1,200억원은", "0 이상이라"],
    ),
]


@pytest.mark.parametrize("key,cur,prior,sector,expected,pieces", CASES)
def test_why_color_table(key, cur, prior, sector, expected, pieces):
    m = metric(key, cur, prior, sector)
    assert m.signal is expected
    text = why_color(m)
    for piece in pieces:
        assert piece in text, (piece, text)
    assert banned.find(text) == []
    for judgment in ("좋", "나쁘", "나쁜", "개선", "악화", "건전"):
        assert judgment not in text, (judgment, text)


def test_sector_can_be_given_explicitly():
    m = metric("debt_ratio", BANK, sector=Sector.FINANCIAL)
    assert why_color(m, sector=Sector.FINANCIAL) == why_color(m)


def test_banded_sentence_is_one_sentence_with_peer_and_delta():
    m = metric("operating_margin", dict(revenue=1000, operating_income=150))
    peer = PeerStat("operating_margin", median=3.1, sample_size=34, percentile=80.0, unit="%")
    delta = SelfDelta("operating_margin", previous=8.0, current=15.0, higher_is_better=True, unit="%")
    text = why_color(m, peer, delta)
    assert text == (
        "영업이익률 15.00%는 7% 이상이라 🟢 정상이에요(동종업계 34개사 중앙값 3.10%·상위 20%, 작년 8.00%)."
    )


def test_market_scope_and_no_percentile():
    m = metric("operating_margin", dict(revenue=1000, operating_income=150))
    peer = PeerStat("operating_margin", median=2.4, sample_size=880, scope=PeerScope.MARKET, unit="%")
    assert "(전체 상장사 880개사 중앙값 2.40%)" in why_color(m, peer)


def test_money_delta_is_formatted():
    m = metric("fcf", dict(operating_cash_flow=300e8, capex=100e8))
    delta = SelfDelta("fcf", previous=-50e8, current=200e8, higher_is_better=True, unit="money")
    assert "작년 -50억원" in why_color(m, delta=delta)


def test_every_metric_and_signal_from_engine_gets_a_sentence():
    """엔진이 낼 수 있는 상태마다 문장이 비지 않고 금지 문구가 없다."""
    samples = [checkup(fin(**BANK), sector=s) for s in Sector] + [checkup(fin())]
    for result in samples:
        for m in result.metrics:
            text = why_color(m)
            assert text and text.endswith((".", "요")), (m.key, text)
            assert banned.find(text) == []


@pytest.mark.parametrize(
    "func,word,expected",
    [
        (topic, "영업이익률", "영업이익률은"),
        (topic, "ROE (자기자본이익률)", "ROE (자기자본이익률)는"),
        (topic, "잉여현금흐름 (FCF)", "잉여현금흐름 (FCF)은"),
        (topic, "15.00%", "15.00%는"),
        (topic, "1,200억원", "1,200억원은"),
        (topic, "-$3.4M", "-$3.4M은"),
        (because, "7% 이상", "7% 이상이라"),
        (because, "200% 초과", "200% 초과라"),
        (if_, "100% 이하", "100% 이하면"),
        (if_, "0 미만", "0 미만이면"),
    ],
)
def test_josa(func, word, expected):
    assert func(word) == expected


def test_profit_without_cash_fact_is_appended():
    m = metric("operating_cash_flow", dict(operating_cash_flow=-10e8, net_income=5e8))
    text = why_color(m)
    assert text == (
        "영업활동현금흐름 -10억원은 0 미만이라 🔴 위험이에요. "
        "장부상으로는 흑자인데 영업현금은 빠져나갔어요."
    )


def test_loss_with_cash_outflow_has_no_extra_fact():
    m = metric("operating_cash_flow", dict(operating_cash_flow=-10e8, net_income=-5e8))
    assert why_color(m) == "영업활동현금흐름 -10억원은 0 미만이라 🔴 위험이에요."


# ── 이자보상배율의 분모 출처 ───────────────────────────────────────


def test_finance_cost_proxy_says_why_it_is_not_judged():
    m = metric("interest_coverage", dict(operating_income=50, interest_expense=100,
                                         interest_expense_source=InterestSource.FINANCE_COSTS))
    text = why_color(m)
    assert m.signal is Signal.NEUTRAL
    assert "0.50배는" in text and "⚪ 측정값" in text and "금융원가" in text
    assert "업종마다 차이가 커서" not in text, "회전율용 문장을 쓰면 이유가 거짓이 된다"
    assert banned.find(text) == []


def test_cash_flow_source_is_stated_and_debt_free_rule_is_not():
    m = metric("interest_coverage", dict(operating_income=500, interest_expense=100,
                                         interest_expense_source=InterestSource.CASH_FLOW))
    text = why_color(m)
    assert m.signal is Signal.GREEN
    assert "3배 이상이라" in text and "현금흐름표의 이자 지급액 기준" in text
    assert "빚이 없다" not in text, "무차입 규칙은 이자비용 0 일 때만이다"


def test_cash_flow_source_is_stated_when_red():
    m = metric("interest_coverage", dict(operating_income=50, interest_expense=100,
                                         interest_expense_source=InterestSource.CASH_FLOW))
    text = why_color(m)
    assert m.signal is Signal.RED
    assert "1배 미만이라" in text and "현금흐름표의 이자 지급액 기준" in text


def test_net_interest_proxy_gives_its_own_reason_not_the_finance_cost_one():
    m = metric("interest_coverage", dict(operating_income=50, interest_expense=100,
                                         interest_expense_source=InterestSource.NET_INTEREST))
    text = why_color(m)
    assert m.signal is Signal.NEUTRAL
    assert "순이자" in text and "이자수익" in text
    assert "금융원가" not in text, "미국 기업에 금융원가 이유를 대면 거짓이다"
    assert banned.find(text) == []


def test_receivable_aggregate_basis_is_stated_on_a_neutral_metric():
    m = metric("receivable_turnover", dict(revenue=1000, trade_receivables=250,
                                           trade_receivables_source=ReceivableSource.TRADE_AND_OTHER))
    text = why_color(m)
    assert "⚪ 측정값" in text and "매출채권및기타채권" in text
    assert banned.find(text) == []


def test_fcf_combined_capex_basis_is_stated_after_the_band_sentence():
    m = metric("fcf", dict(operating_cash_flow=-10e8, capex=5e8,
                           capex_source=CapexSource.PPE_AND_INTANGIBLE))
    text = why_color(m)
    assert m.signal is Signal.RED
    assert "0 미만이라" in text and "무형자산" in text
    assert banned.find(text) == []


def test_profit_to_loss_is_called_a_turn_to_loss_not_a_continued_loss():
    """흑자 → 적자는 '적자 지속' 이 아니다. 증가율 −150% 옆에 그 사실을 따로 적는다."""
    m = metric("operating_income_growth", dict(operating_income=-50), dict(operating_income=100))
    assert m.signal is Signal.RED and m.value == pytest.approx(-150.0)
    sentence = why_color(m)
    assert "적자 전환" in sentence
    assert "적자 지속" not in sentence


def test_ordinary_decline_gets_no_turnaround_fact():
    m = metric("operating_income_growth", dict(operating_income=50), dict(operating_income=100))
    assert m.signal is Signal.RED
    sentence = why_color(m)
    assert "전환" not in sentence and "지속" not in sentence


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("금융업", "금융업으로"), ("은행·증권·금융지주", "은행·증권·금융지주로"),
        ("서울", "서울로"), ("641", "641로"), ("63", "63으로"),
    ],
)
def test_ro_particle_follows_the_last_sound(word, expected):
    from fin_checkup.explain._korean import ro

    assert ro(word) == expected


def test_tiny_denominator_notes_are_appended_as_facts():
    """Q14: 하루 매출보다 작은 재고·채권, 아주 작은 매출 — 값과 색은 그대로, 왜 큰 숫자인지를 덧붙인다."""
    from fin_checkup.metrics.engine import (
        RECEIVABLE_AGGREGATE_NOTE,
        TINY_RECEIVABLE_NOTE,
        TINY_REVENUE_NOTE,
        checkup,
    )
    from fin_checkup.models import Financials, FsDiv, ReceivableSource, ReportCode

    base = dict(corp_code="1", bsns_year=2024, reprt_code=ReportCode.ANNUAL, fs_div=FsDiv.CFS)
    result = checkup(Financials(
        **base, revenue=1_000_000, trade_receivables=100, operating_income=-20_000_000,
        trade_receivables_source=ReceivableSource.TRADE_AND_OTHER,
    ))
    turnover = why_color(result.by_key("receivable_turnover"))
    assert "⚪" in turnover and TINY_RECEIVABLE_NOTE in turnover and RECEIVABLE_AGGREGATE_NOTE in turnover
    margin = why_color(result.by_key("operating_margin"))
    assert "🔴" in margin and TINY_REVENUE_NOTE in margin
