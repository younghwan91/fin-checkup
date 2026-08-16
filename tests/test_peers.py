from __future__ import annotations

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


def test_higher_is_better_metric_improves_when_it_rises():
    d = delta_of("operating_margin", 5.0, revenue=1000, operating_income=100)  # 10%
    assert d.improved is True
    assert "개선" in d.summary and "↑" in d.summary


def test_higher_is_better_metric_worsens_when_it_falls():
    d = delta_of("operating_margin", 20.0, revenue=1000, operating_income=100)
    assert d.improved is False
    assert "악화" in d.summary and "↓" in d.summary


def test_lower_is_better_metric_improves_when_it_falls():
    """부채비율은 내려가야 좋다. 방향을 뒤집지 않으면 정반대로 말한다."""
    d = delta_of("debt_ratio", 200.0, total_liabilities=500, total_equity=1000)  # 50%
    assert d.improved is True
    assert "개선" in d.summary


def test_lower_is_better_metric_worsens_when_it_rises():
    d = delta_of("debt_ratio", 20.0, total_liabilities=500, total_equity=1000)
    assert d.improved is False
    assert "악화" in d.summary


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
