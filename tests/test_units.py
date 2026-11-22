"""보고서 금액 단위 오류(×1000) 의심 — (주)대산에프앤비 2023 사업보고서가 모든 줄을 1,000배로 공시했다."""

from __future__ import annotations

import pytest

from fin_checkup.metrics.engine import checkup
from fin_checkup.metrics.redflags import detect_red_flags
from fin_checkup.metrics.signals import Signal
from fin_checkup.metrics.units import (
    PRIOR_UNIT_SUSPECT_NOTE,
    UNIT_SUSPECT_NOTE,
    UNIT_SUSPECT_PREVIOUS_NOTE,
    mark_unit_suspects,
    pair_scale,
    unit_suspects,
)
from fin_checkup.models import Financials, FsDiv, ReportCode

TRUE = {2021: 50e9, 2022: 52e9, 2023: 56.4e9, 2024: 60e9, 2025: 61e9}


def report(year: int, scale: float = 1.0, revenue_restated: float = 1.0) -> Financials:
    """그해 사업보고서. scale 은 그 보고서가 당기·전기 열을 함께 잘못 적은 배수."""

    def values(y: int, k: float) -> dict:
        assets = TRUE[y] * k
        return dict(
            total_assets=assets,
            total_equity=assets * 0.6,
            revenue=assets * 0.8,
            operating_income=-assets * 0.01,
            net_income=-assets * 0.01,
        )

    base = dict(reprt_code=ReportCode.ANNUAL, fs_div=FsDiv.CFS, corp_code="00390860")
    comparative = None
    if year - 1 in TRUE:
        prior = values(year - 1, scale)
        prior["revenue"] *= revenue_restated
        comparative = Financials(bsns_year=year - 1, **base, **prior)
    return Financials(bsns_year=year, **base, **values(year, scale), comparative=comparative)


def reports(**scales: float) -> dict[int, Financials]:
    return {y: report(y, scales.get(f"y{y}", 1.0)) for y in TRUE}


def test_matching_reports_are_not_suspect():
    assert pair_scale(report(2023), report(2024)) == 1.0
    assert unit_suspects(reports()) == {}


def test_report_written_in_thousands_is_caught_and_not_its_neighbour():
    """대산에프앤비: 2023 보고서가 당기·전기 열 모두 ×1000. 2022 는 2023 보고서의 전기 열과 1/1000 로
    어긋나지만 2021 보고서와 맞으니 멀쩡하다 — '1,000배 쌍의 앞 해' 를 기계적으로 의심하면 2022 도 걸린다."""
    found = unit_suspects(reports(y2023=1000.0))
    assert found == {2023: UNIT_SUSPECT_NOTE}


def test_first_year_neighbour_decides_by_the_following_pair():
    """네오크레마: 수집 첫해(2021) 보고서는 앞 이웃이 없다. 2022 보고서가 ×1000 — 2022·2023 쌍이 갈라 준다."""
    found = unit_suspects({y: r for y, r in reports(y2022=1000.0).items() if y <= 2023})
    assert found == {2022: UNIT_SUSPECT_NOTE}


def test_latest_report_is_caught_from_the_previous_report():
    found = unit_suspects(reports(y2025=1000.0))
    assert found == {2025: UNIT_SUSPECT_PREVIOUS_NOTE}


def test_report_written_a_thousand_times_too_small_is_caught_too():
    assert unit_suspects(reports(y2023=0.001)) == {2023: UNIT_SUSPECT_NOTE}


def test_two_anchors_are_enough_when_revenue_was_also_restated():
    """대산에프앤비 2023: 자산 1,005배·자본 1,014배·매출 1,097배(재작성까지 겹침)."""
    following = report(2024)
    own = report(2023, scale=1000.0)
    own = own.model_copy(update={"revenue": own.revenue * 1.097})
    assert pair_scale(own, following) == 1000.0


def test_restatement_alone_is_not_a_unit_error():
    own = report(2023).model_copy(update={"revenue": report(2023).revenue * 1.4})
    assert pair_scale(own, report(2024)) == 1.0


def test_no_following_report_means_nothing_to_compare():
    assert pair_scale(report(2025), None) is None
    assert unit_suspects({2025: report(2025)}) == {}


# ── 의심 연도는 판정·견주기에서 빠진다 ─────────────────────────────


def test_suspect_year_metrics_are_unknown_with_the_reason_and_no_auto_division():
    marked = mark_unit_suspects(reports(y2023=1000.0))
    result = checkup(marked[2023], marked[2022])
    assert all(m.signal is Signal.UNKNOWN and m.value is None for m in result.metrics)
    assert all(UNIT_SUSPECT_NOTE in m.note for m in result.metrics)
    assert marked[2023].total_assets == pytest.approx(56.4e12), "자동으로 1,000 으로 나누지 않는다"


def test_following_year_growth_uses_its_own_prior_column():
    """2024 보고서의 전기 열(2023, 바른 단위)이 있으니 2024 증가율은 그대로 낸다."""
    marked = mark_unit_suspects(reports(y2023=1000.0))
    growth = checkup(marked[2024], marked[2023]).by_key("revenue_growth")
    assert growth.signal is not Signal.UNKNOWN and growth.value == pytest.approx((60 / 56.4 - 1) * 100)


def test_growth_against_a_suspect_prior_without_prior_column_is_unknown():
    marked = mark_unit_suspects(reports(y2023=1000.0))
    current = marked[2024].model_copy(update={"comparative": None})
    growth = checkup(current, marked[2023]).by_key("revenue_growth")
    assert growth.signal is Signal.UNKNOWN and growth.note == PRIOR_UNIT_SUSPECT_NOTE


def test_suspect_year_breaks_red_flag_streaks():
    """영업손실 2021~2025 다섯 해 중 2023 이 의심 — 연속은 2024~2025 두 해로 센다."""
    marked = mark_unit_suspects(reports(y2023=1000.0))
    history = [marked[y] for y in sorted(marked)]
    assert "consecutive_operating_loss" in {f.key for f in detect_red_flags(list(reports().values()))}
    assert "consecutive_operating_loss" not in {f.key for f in detect_red_flags(history)}


def test_why_color_names_the_unit_suspect_reason():
    from fin_checkup.explain.why import why_color

    marked = mark_unit_suspects(reports(y2023=1000.0))
    roe = checkup(marked[2023]).by_key("roe")
    assert UNIT_SUSPECT_NOTE in why_color(roe)


# ── 캐시 → 건강검진 → 대조군·추이 ────────────────────────────────


def _raw(year: int, scale: float = 1.0):
    from fin_checkup.models import AccountLine, RawStatements

    def line(sj, aid, name, base, ord_):
        return AccountLine(
            sj_div=sj, account_id=aid, account_nm=name, ord=ord_,
            thstrm_amount=base(year) * scale, frmtrm_amount=base(year - 1) * scale,
        )

    return RawStatements(
        corp_code="00390860", bsns_year=year, reprt_code=ReportCode.ANNUAL, fs_div=FsDiv.CFS,
        lines=[
            line("BS", "ifrs-full_Assets", "자산총계", lambda y: 1000.0 + y, 1),
            line("BS", "ifrs-full_Equity", "자본총계", lambda y: 600.0 + y, 2),
            line("BS", "ifrs-full_Liabilities", "부채총계", lambda y: 400.0, 3),
            line("IS", "ifrs-full_Revenue", "매출액", lambda y: 800.0 + y, 4),
            line("IS", "dart_OperatingIncomeLoss", "영업이익", lambda y: 80.0, 5),
        ],
    )


@pytest.fixture
def service(tmp_path, test_settings):
    from fin_checkup.models import Company, Market
    from fin_checkup.service import CheckupService
    from fin_checkup.storage import Cache

    with Cache(tmp_path / "u.duckdb") as cache:
        cache.save_company(Company(corp_code="00390860", corp_name="대산에프앤비", stock_code="290380",
                                   industry_code="10", market=Market.KOSDAQ))
        for year in (2021, 2022, 2024, 2025):
            cache.save_statements(_raw(year))
        cache.save_statements(_raw(2023, scale=1000.0))
        yield CheckupService(cache, settings=test_settings)


async def test_service_marks_only_the_suspect_year_and_the_trend_shows_a_gap(service):
    from fin_checkup.models import CorpCode
    from fin_checkup.site.charts import Gap, trend_series

    corp = CorpCode(corp_code="00390860", corp_name="대산에프앤비", stock_code="290380")
    data = await service.run(corp, end_year=2025, years=5, allow_fetch=False)
    assert [f.bsns_year for f in data.history if f.unit_suspect] == [2023]
    revenue = next(s for s in trend_series(data) if s.key == "revenue")
    gaps = {p.year: p.gap for p in revenue.points}
    assert gaps[2023] is Gap.UNIT_SUSPECT and gaps[2022] is None and gaps[2024] is None
    margin = next(s for s in trend_series(data) if s.key == "operating_margin")
    assert {p.year: p.gap for p in margin.points}[2023] is Gap.UNIT_SUSPECT

    # 2023 을 끝 해로 보면(다음 해 보고서는 캐시에서만 읽는다) 그해 지표는 전부 ⚫, 작년 대비도 없다.
    then = await service.run(corp, end_year=2023, years=3, allow_fetch=False)
    assert all(m.signal is Signal.UNKNOWN for m in then.result.metrics)
    assert then.deltas == {} and then.red_flags == []


async def test_backfill_leaves_the_suspect_year_out_of_peer_values(service):
    # 지난 계산(의심을 몰랐던 때)이 남긴 행도 지워야 한다.
    service.cache.save_metric_values("00390860", 2023, {"operating_margin": 10.0})
    service.backfill_metric_values(2023)
    service.backfill_metric_values(2024)
    assert service.cache.peer_metric_values(["00390860"], 2023) == {}
    assert "operating_margin" in service.cache.peer_metric_values(["00390860"], 2024)


# ── 재작성 표시 (Q16) ─────────────────────────────────────────────


def _restated_raw(year: int, revenue: float, prior_revenue: float):
    from fin_checkup.models import AccountLine, RawStatements

    def line(sj, aid, name, cur, prev, ord_):
        return AccountLine(sj_div=sj, account_id=aid, account_nm=name, ord=ord_,
                           thstrm_amount=cur, frmtrm_amount=prev)

    return RawStatements(
        corp_code="00117188", bsns_year=year, reprt_code=ReportCode.ANNUAL, fs_div=FsDiv.CFS,
        lines=[
            line("BS", "ifrs-full_Assets", "자산총계", 10_000.0, 10_000.0, 1),
            line("BS", "ifrs-full_Equity", "자본총계", 6_000.0, 6_000.0, 2),
            line("BS", "ifrs-full_Liabilities", "부채총계", 4_000.0, 4_000.0, 3),
            line("IS", "ifrs-full_Revenue", "매출액", revenue, prior_revenue, 4),
            line("IS", "dart_OperatingIncomeLoss", "영업이익", revenue * 0.1, prior_revenue * 0.1, 5),
        ],
    )


@pytest.fixture
async def hyosung(tmp_path, test_settings):
    """(주)효성: 2023 보고서 매출 3.44조, 2024 보고서 전기 열 1.85조(분할로 중단영업 재분류)."""
    from fin_checkup.models import Company, CorpCode, Market
    from fin_checkup.service import CheckupService
    from fin_checkup.storage import Cache

    with Cache(tmp_path / "h.duckdb") as cache:
        cache.save_company(Company(corp_code="00117188", corp_name="(주)효성", stock_code="004800",
                                   industry_code="20", market=Market.KOSPI))
        cache.save_statements(_restated_raw(2022, 3.2e12, 3.0e12))
        cache.save_statements(_restated_raw(2023, 3.44e12, 3.2e12))
        cache.save_statements(_restated_raw(2024, 2.27e12, 1.85e12))
        service = CheckupService(cache, settings=test_settings)
        corp = CorpCode(corp_code="00117188", corp_name="(주)효성", stock_code="004800")
        yield await service.run(corp, end_year=2024, years=3, allow_fetch=False)


async def test_restated_year_is_marked_with_the_following_reports_value(hyosung):
    from fin_checkup.site.charts import trend_series, trend_svg

    revenue = next(s for s in trend_series(hyosung, years=3) if s.key == "revenue")
    points = {p.year: p for p in revenue.points}
    assert points[2023].value == pytest.approx(3.44e12), "막대는 그해 보고서 값 그대로"
    assert points[2023].restated == pytest.approx(1.85e12)
    assert "다음 해 보고서 기준 값: 1.9조원" in points[2023].restated_line
    assert points[2022].restated is None, "2023 보고서 전기 열(3.2조)과 같다"
    assert points[2024].restated is None, "최신 연도는 다음 해 보고서가 없다"
    svg = str(trend_svg(revenue, "t"))
    assert svg.count(">재작성</text>") == 1 and "다음 해 보고서 기준 값: 1.9조원" in svg


async def test_ratio_trend_marks_restatement_only_when_the_ratio_moved(hyosung):
    from fin_checkup.site.charts import trend_series

    margin = next(s for s in trend_series(hyosung, years=3) if s.key == "operating_margin")
    assert all(p.restated is None for p in margin.points), "같은 비율로 다시 적혀 이익률은 같다"


def test_restatement_compares_with_the_next_reports_prior_column_unless_unit_suspect():
    from fin_checkup.models import Financials
    from fin_checkup.site.charts import _following_comparative

    base = dict(corp_code="1", reprt_code=ReportCode.ANNUAL, fs_div=FsDiv.CFS)
    later = Financials(bsns_year=2023, **base, revenue=3.39e12)
    by_year = {2023: Financials(bsns_year=2023, **base, revenue=3.44e12),
               2024: Financials(bsns_year=2024, **base, revenue=3.6e12, comparative=later)}
    assert _following_comparative(by_year, 2023) is later
    suspect = {**by_year, 2024: by_year[2024].model_copy(update={"unit_suspect": UNIT_SUSPECT_NOTE})}
    assert _following_comparative(suspect, 2023) is None, "×1000 보고서의 전기 열은 재작성이 아니다"


def test_restatement_tolerance_is_one_percent():
    from fin_checkup.site.charts import is_restated

    assert not is_restated(100.0, 100.9)
    assert is_restated(100.0, 101.5)
    assert not is_restated(None, 100.0) and not is_restated(0.0, 0.0)


async def test_api_json_exposes_restated_values(hyosung):

    from fin_checkup.site.pages.api import restated_values

    assert restated_values(hyosung) == {"revenue": {"2023": pytest.approx(1.85e12)},
                                        "operating_income": {"2023": pytest.approx(1.85e11)}}
