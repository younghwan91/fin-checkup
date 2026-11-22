from __future__ import annotations

import dataclasses

import pytest

from fin_checkup.metrics.engine import checkup
from fin_checkup.metrics.peers import peer_stats
from fin_checkup.models import Financials, FsDiv, ReportCode


def metric(key: str, **kwargs):
    result = checkup(
        Financials(corp_code="1", bsns_year=2024, reprt_code=ReportCode.ANNUAL,
                   fs_div=FsDiv.CFS, **kwargs)
    )
    m = result.by_key(key)
    assert m is not None
    return m


def test_too_few_peers_returns_none():
    m = metric("operating_margin", revenue=1000, operating_income=100)
    assert peer_stats(m, [1.0, 2.0, 3.0]) is None


def test_median_and_sample_size():
    m = metric("operating_margin", revenue=1000, operating_income=100)  # 10%
    stat = peer_stats(m, [2.0, 4.0, 6.0, 8.0, 10.0])
    assert stat is not None
    assert stat.median == pytest.approx(6.0)
    assert stat.sample_size == 5


def test_percentile_for_higher_is_better_metric():
    # 영업이익률 10%는 [2,4,6,8,20] 중 4개보다 높다 → 80 백분위
    m = metric("operating_margin", revenue=1000, operating_income=100)
    stat = peer_stats(m, [2.0, 4.0, 6.0, 8.0, 20.0])
    assert stat is not None
    assert stat.percentile == pytest.approx(80.0)


def test_percentile_is_inverted_for_lower_is_better_metric():
    # 부채비율 50%는 낮을수록 좋다. 동종 [100,200,300,400,500] 중 최저 → 상위 100 백분위
    m = metric("debt_ratio", total_liabilities=500, total_equity=1000)
    stat = peer_stats(m, [100.0, 200.0, 300.0, 400.0, 500.0])
    assert stat is not None
    assert stat.higher_is_better is False
    assert stat.percentile == pytest.approx(100.0)


def test_ties_count_as_half():
    m = metric("operating_margin", revenue=1000, operating_income=100)  # 10%
    stat = peer_stats(m, [10.0] * 5)
    assert stat is not None
    assert stat.percentile == pytest.approx(50.0)


def test_percentile_is_none_when_target_value_missing():
    m = metric("operating_margin")  # 데이터 없음
    stat = peer_stats(m, [1.0, 2.0, 3.0, 4.0, 5.0])
    assert stat is not None
    assert stat.percentile is None
    assert "중앙값" in stat.summary and "동종업계" in stat.summary


def test_min_peers_is_configurable():
    m = metric("operating_margin", revenue=1000, operating_income=100)
    assert peer_stats(m, [1.0, 2.0], min_peers=2) is not None


# ── 표시 형식 (화면을 실제로 보고 발견) ───────────────────────────


def test_money_median_is_formatted_not_dumped_raw():
    """금액을 그대로 찍으면 '-846,681,792.00'이 화면에 나온다."""
    m = metric("operating_cash_flow", operating_cash_flow=73_000_000_000_000)
    stat = peer_stats(m, [-846_681_792.0] * 9, currency="KRW")
    assert stat is not None
    assert "-8억원" in stat.median_text
    assert "846,681,792" not in stat.summary


def test_usd_median_uses_dollar_format():
    m = metric("operating_cash_flow", operating_cash_flow=118_000_000_000)
    stat = peer_stats(m, [5_000_000_000.0] * 9, currency="USD")
    assert "$5.0B" == stat.median_text


def test_ratio_median_keeps_its_unit():
    m = metric("debt_ratio", total_liabilities=500, total_equity=1000)
    stat = peer_stats(m, [30.0, 34.0, 35.0, 40.0, 50.0])
    assert stat.median_text == "35.00%"


def test_being_best_reads_as_best_not_zero():
    """'상위 0%'는 1등이라는 뜻인데 꼴찌처럼 읽힌다."""
    m = metric("operating_margin", revenue=1000, operating_income=500)  # 50%
    stat = peer_stats(m, [1.0, 2.0, 3.0, 4.0, 5.0])
    assert stat.rank_text == "최상위"
    assert "상위 0%" not in stat.summary


def test_being_worst_reads_as_worst():
    m = metric("operating_margin", revenue=1000, operating_income=-500)
    stat = peer_stats(m, [1.0, 2.0, 3.0, 4.0, 5.0])
    assert stat.rank_text == "최하위"


def test_middle_rank_shows_a_percentage():
    m = metric("operating_margin", revenue=1000, operating_income=30)  # 3%
    stat = peer_stats(m, [1.0, 2.0, 4.0, 5.0, 6.0])
    assert stat.rank_text is not None
    assert stat.rank_text.startswith("상위 ")


# ── 대조군 표기 ───────────────────────────────────────────────────


def test_summary_states_what_it_compared_against():
    """무엇과 견줬는지 숨기면 사용자가 근거를 확인할 수 없다."""
    from fin_checkup.metrics.peers import PeerScope

    m = metric("operating_margin", revenue=1000, operating_income=100)
    industry = peer_stats(m, [2.0, 4.0, 6.0, 8.0, 20.0], scope=PeerScope.INDUSTRY)
    market = peer_stats(m, [2.0, 4.0, 6.0, 8.0, 20.0], scope=PeerScope.MARKET)

    assert "동종업계 5개사" in industry.summary
    assert "전체 상장사 5개사" in market.summary
    assert "상위 20%" in industry.summary  # 80 백분위 = 상위 20%
    assert "중앙값 6.00%" in industry.summary


# ── 자기 과거 대비 ────────────────────────────────────────────────


def delta_of(key, previous, **kwargs):
    from fin_checkup.metrics.peers import self_delta

    return self_delta(metric(key, **kwargs), previous)


# 값이 어느 쪽으로 움직였나(높아짐·낮아짐)와 그게 엔진 기준으로 어느 쪽인가(좋아진·나빠진 쪽)를
# 따로 적는다. "작년 118.70% ↑ 악화" 는 화살표(값이 올랐다)와 낱말(나빠졌다)이 서로 반대로 읽혔다.
LOWER_BETTER_UP = "낮을수록 좋게 보는 지표라 나빠진 쪽"
DELTA_CASES = [
    # (지표, 작년, 올해 재무, 문장, improved)
    ("operating_margin", 5.0, dict(revenue=1000, operating_income=100),
     "작년 5.00% → 올해 10.00% (5.00%p 높아짐 · 높을수록 좋게 보는 지표라 좋아진 쪽)", True),
    ("operating_margin", 20.0, dict(revenue=1000, operating_income=100),
     "작년 20.00% → 올해 10.00% (10.00%p 낮아짐 · 높을수록 좋게 보는 지표라 나빠진 쪽)", False),
    # 부채비율은 내려가야 좋다. 방향을 뒤집지 않으면 정반대로 말한다.
    ("debt_ratio", 20.0, dict(total_liabilities=500, total_equity=1000),
     f"작년 20.00% → 올해 50.00% (30.00%p 높아짐 · {LOWER_BETTER_UP})", False),
    ("debt_ratio", 200.0, dict(total_liabilities=500, total_equity=1000),
     "작년 200.00% → 올해 50.00% (150.00%p 낮아짐 · 낮을수록 좋게 보는 지표라 좋아진 쪽)", True),
    # 배: "1.50배 높아짐" 은 '1.5배가 됐다' 로 읽힌다 — 차이는 단위 없이.
    ("interest_coverage", 1.5, dict(operating_income=300, interest_expense=100),
     "작년 1.50배 → 올해 3.00배 (1.50 높아짐 · 높을수록 좋게 보는 지표라 좋아진 쪽)", True),
    # 경계값이 없는 지표(회전율)는 방향만 — 좋아졌다·나빠졌다를 지어내지 않는다.
    ("asset_turnover", 0.7, dict(revenue=1000, total_assets=2000),
     "작년 0.70회 → 올해 0.50회 (0.20회 낮아짐)", None),
    # 차이는 보이는 두 숫자로 — 0.5847 → 0.5884 가 "(0.00회 높아짐)" 이 되지 않게.
    ("asset_turnover", 0.584747, dict(revenue=588.43, total_assets=1000),
     "작년 0.58회 → 올해 0.59회 (0.01회 높아짐)", None),
    # 금액은 통화 단위로, 0 을 가로질러도 차이 그대로.
    ("operating_cash_flow", -5_000_000_000, dict(operating_cash_flow=3_000_000_000),
     "작년 -50억원 → 올해 30억원 (80억원 늘어남 · 높을수록 좋게 보는 지표라 좋아진 쪽)", True),
    ("fcf", 3_000_000_000, dict(operating_cash_flow=1_000_000_000, capex=0),
     "작년 30억원 → 올해 10억원 (20억원 줄어듦 · 높을수록 좋게 보는 지표라 나빠진 쪽)", False),
    # 화면에 같은 숫자로 보이면 '같음' — "10.00% → 10.00% (0.00%p 낮아짐)" 을 쓰지 않는다.
    ("operating_margin", 10.001, dict(revenue=1000, operating_income=100), "작년과 같음 (10.00%)", None),
]


@pytest.mark.parametrize(
    "key,previous,fin,expected,improved", DELTA_CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(DELTA_CASES)]
)
def test_delta_separates_value_change_from_evaluation(key, previous, fin, expected, improved):
    d = delta_of(key, previous, **fin)
    assert d.summary == expected
    assert d.improved is improved
    for contradicting in ("↑", "↓", "개선", "악화"):
        assert contradicting not in d.summary


@pytest.mark.parametrize("key,fin", [
    ("roe", dict(net_income=100, total_equity=-10)),  # ⚫ 자본잠식 — 값이 없다
    ("debt_ratio", dict(total_liabilities=500)),  # ⚫ 계정 없음
])
def test_no_delta_for_missing_value(key, fin):
    assert delta_of(key, 10.0, **fin) is None


def test_delta_direction_comes_from_band_not_a_list():
    """방향은 엔진의 Band.higher_is_better 에서 — 업종 경계값이 바뀌면 문장도 따라간다."""
    m = metric("debt_ratio", total_liabilities=500, total_equity=1000)
    from fin_checkup.metrics.peers import self_delta
    from fin_checkup.metrics.signals import Band

    flipped = dataclasses.replace(m, band=Band(good=1, warn=0))
    assert "높을수록 좋게 보는 지표라 좋아진 쪽" in self_delta(flipped, 20.0).summary
    assert self_delta(dataclasses.replace(m, band=None), 20.0).improved is None


def test_unchanged_is_neither():
    d = delta_of("operating_margin", 10.0, revenue=1000, operating_income=100)
    assert d.unchanged is True
    assert "작년과 같음" in d.summary


def test_no_delta_without_both_sides():
    assert delta_of("operating_margin", None, revenue=1000, operating_income=100) is None
    assert delta_of("operating_margin", 5.0) is None


def test_money_delta_is_formatted():
    d = delta_of("operating_cash_flow", 35_000_000_000, operating_cash_flow=73_000_000_000_000)
    assert "350억원" in d.summary
