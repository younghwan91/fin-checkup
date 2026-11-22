"""회사 페이지 인라인 SVG — 구조(접근성), 빈 해 처리(0 으로 메우지 않음), 이스케이프."""

from __future__ import annotations

import dataclasses
import xml.etree.ElementTree as ET
from datetime import date

import pytest
from hub_seed import KB, SAMSUNG, seed_site_cache

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.metrics.engine import MONEY, Category, Metric
from fin_checkup.metrics.peers import PeerScope, PeerStat, peer_stats
from fin_checkup.metrics.signals import Band, Signal
from fin_checkup.site.build import build_site, collect_companies
from fin_checkup.site.charts import (
    Gap,
    TrendPoint,
    TrendSeries,
    badge_label,
    badge_svg,
    nice_ticks,
    peer_strip,
    peer_strips,
    trend_series,
    trend_svg,
)
from fin_checkup.storage import Cache

SVG = "{http://www.w3.org/2000/svg}"


@pytest.fixture
def cache(tmp_path):
    with Cache(tmp_path / "charts.duckdb") as c:
        seed_site_cache(c)
        yield c


@pytest.fixture
async def checkups(cache):
    entries, _ = await collect_companies(cache, 2024)
    return {e.stock_code: e.checkup for e in entries}


def parse(svg: str) -> ET.Element:
    """잘 짜인 XML 이어야 한다 — 이스케이프가 새면 여기서 깨진다."""
    return ET.fromstring(str(svg))


def series(values: list[float | None], label: str = "매출액", unit: str = MONEY) -> TrendSeries:
    points = tuple(
        TrendPoint(2020 + i, v, f"{v}")
        if v is not None
        else TrendPoint(2020 + i, None, "⚫ 데이터 없음", Gap.NO_YEAR)
        for i, v in enumerate(values)
    )
    return TrendSeries("revenue", label, unit, "KRW", points)


# ── 추이 데이터 ───────────────────────────────────────────────────


async def test_trend_keeps_five_year_slots_and_marks_uncollected_years(checkups):
    revenue = next(s for s in trend_series(checkups[SAMSUNG.stock_code]) if s.key == "revenue")
    assert [p.year for p in revenue.points] == [2020, 2021, 2022, 2023, 2024]
    assert [p.gap for p in revenue.points[:2]] == [Gap.NO_YEAR, Gap.NO_YEAR]
    assert all(p.value is None and p.text == "⚫ 데이터 없음" for p in revenue.points[:2])
    assert [p.value for p in revenue.points[2:]] == [1100.0, 1200.0, 900.0]


async def test_missing_account_is_a_gap_not_zero(checkups):
    data = checkups[SAMSUNG.stock_code]
    history = list(data.history)
    history[-2] = history[-2].model_copy(update={"revenue": None})
    data = dataclasses.replace(data, history=history)

    revenue = next(s for s in trend_series(data) if s.key == "revenue")
    assert revenue.points[3].gap is Gap.NO_ACCOUNT and revenue.points[3].value is None
    margin = next(s for s in trend_series(data) if s.key == "operating_margin")
    assert margin.points[3].gap is Gap.NO_ACCOUNT, "매출이 없으면 영업이익률도 0 이 아니라 ⚫"


async def test_ratio_that_is_not_applicable_for_the_sector_is_not_drawn(checkups):
    keys = {s.key for s in trend_series(checkups[KB.stock_code])}
    assert "debt_ratio" not in keys and "current_ratio" not in keys
    assert {"revenue", "operating_income", "net_income", "operating_cash_flow"} <= keys
    assert "debt_ratio" in {s.key for s in trend_series(checkups[SAMSUNG.stock_code])}


# ── SVG ───────────────────────────────────────────────────────────


def test_trend_svg_is_accessible_and_uses_css_classes_only():
    root = parse(trend_svg(series([None, None, 1.1e14, 1.2e14, 9e13]), "t1"))
    assert root.get("role") == "img"
    assert root.get("aria-labelledby") == "t1-t t1-d"
    assert root.find(f"{SVG}title").get("id") == "t1-t"
    desc = root.find(f"{SVG}desc").text
    assert "2020년 ⚫ 데이터 없음" in desc and "2024년" in desc
    text = str(trend_svg(series([1.0, 2.0]), "t2"))
    assert "style=" not in text and "<script" not in text, "CSP 가 막는 인라인 스타일·스크립트 없음"
    assert 'fill="#' not in text, "색은 CSS 변수(클래스)로 — 다크 모드가 따라오게"


def test_each_year_has_a_hover_title():
    root = parse(trend_svg(series([1.0, None, 3.0]), "t"))
    years = root.findall(f"{SVG}g")
    assert len(years) == 3
    assert all(g.find(f"{SVG}title") is not None for g in years)
    assert "2021년 매출액: ⚫ 데이터 없음" in years[1].find(f"{SVG}title").text


def test_gap_years_draw_a_labelled_empty_slot_and_no_bar():
    root = parse(trend_svg(series([None, 5.0, None, 7.0, None]), "t"))
    bars = root.findall(f".//{SVG}path[@class='fc-ch-bar']")
    gaps = root.findall(f".//{SVG}rect[@class='fc-ch-gap']")
    assert len(bars) == 2 and len(gaps) == 3
    marks = [t.text for t in root.iter(f"{SVG}text") if t.get("class") == "fc-ch-gap-mark"]
    assert marks == ["⚫"] * 3
    words = [t.text for t in root.iter(f"{SVG}text") if t.get("class") == "fc-ch-gap-text"]
    assert words == ["데이터", "없음"] * 3


def test_not_applicable_gap_is_labelled_differently():
    s = TrendSeries(
        "debt_ratio",
        "부채비율",
        "%",
        "KRW",
        (TrendPoint(2023, None, "⊘ 해당 없음", Gap.NOT_APPLICABLE), TrendPoint(2024, 80.0, "80.00%")),
    )
    root = parse(trend_svg(s, "t"))
    words = [t.text for t in root.iter(f"{SVG}text") if t.get("class", "").startswith("fc-ch-gap")]
    assert words == ["⊘", "해당", "없음"]


def test_all_missing_still_renders_every_slot_as_a_gap():
    root = parse(trend_svg(series([None] * 5), "t"))
    assert not root.findall(f".//{SVG}path")
    assert len(root.findall(f".//{SVG}rect[@class='fc-ch-gap']")) == 5


def _bar_extent(path: str) -> tuple[float, float]:
    # "M{x},{y_zero}V{...}" — 시작 y 가 기준선, 꼭짓점 y 들 중 기준선에서 먼 쪽이 끝.
    import re

    ys = [float(n) for n in re.findall(r"[,V](-?\d+\.\d)", path)]
    return ys[0], (min(ys) if min(ys) < ys[0] else max(ys))


def test_negative_values_grow_down_from_the_zero_line():
    root = parse(trend_svg(series([3.0e11, -2.0e11]), "t"))
    zero = root.find(f".//{SVG}line[@class='fc-ch-zero']")
    y_zero = float(zero.get("y1"))
    up, down = root.findall(f".//{SVG}path[@class='fc-ch-bar']")
    assert _bar_extent(up.get("d"))[0] == pytest.approx(y_zero, abs=0.1)
    assert _bar_extent(up.get("d"))[1] < y_zero
    assert _bar_extent(down.get("d"))[1] > y_zero


def test_only_the_latest_year_gets_a_direct_value_label():
    root = parse(trend_svg(series([1.0e12, 2.0e12, 3.5e12]), "t"))
    labels = [t.text for t in root.iter(f"{SVG}text") if t.get("class") == "fc-ch-value"]
    assert labels == ["3.5조"]


def test_text_is_escaped():
    nasty = '<script>alert("x")</script> & "따옴표"'
    s = series([1.0, None], label=nasty)
    text = str(trend_svg(s, 'id"><script>'))
    assert "<script>" not in text
    root = parse(text)
    assert nasty in root.find(f"{SVG}title").text


@pytest.mark.parametrize(
    ("lo", "hi"),
    [(0, 1.0), (0, 3.1e14), (-2e11, 3e11), (-5.0, -1.0), (0, 0), (12.3, 456.7)],
)
def test_nice_ticks_cover_the_range_and_include_zero(lo, hi):
    ticks = nice_ticks(lo, hi)
    assert 0 in ticks
    assert ticks[0] <= min(lo, 0) and ticks[-1] >= max(hi, 0)
    assert 2 <= len(ticks) <= 6
    steps = {round(b - a, 6) for a, b in zip(ticks, ticks[1:], strict=False)}
    assert len(steps) == 1


# ── 페이지 ─────────────────────────────────────────────────────────


async def test_company_page_has_trend_section_with_table_fallback(cache, tmp_path):
    out = tmp_path / "out"
    await build_site(cache, out, 2024, built_on=date(2026, 11, 21))
    html = (out / "c" / SAMSUNG.stock_code / "index.html").read_text(encoding="utf-8")
    assert 'id="trend"' in html and "5개년 추이" in html
    assert html.count('class="fc-ch fc-ch-trend"') == 7  # 금액 4 + 비율 3
    assert "<details" in html and "표로 보기" in html
    assert "⚫ 데이터 없음" in html
    kb = (out / "c" / KB.stock_code / "index.html").read_text(encoding="utf-8")
    assert kb.count('class="fc-ch fc-ch-trend"') == 5  # 금융지주: 부채비율·유동비율 ⊘ 은 그리지 않는다


# ── 동종업계 분포 ─────────────────────────────────────────────────


MARGIN_BAND = Band(good=7, warn=0)


def metric(key="operating_margin", value=12.0, signal=Signal.GREEN, band=MARGIN_BAND, unit="%"):
    return Metric(
        key=key,
        label="영업이익률",
        category=Category.PROFITABILITY,
        value=value,
        unit=unit,
        signal=signal,
        description="",
        formula="영업이익 ÷ 매출액 × 100",
        band=band,
    )


def stat(scope=PeerScope.INDUSTRY, n=34, **kw):
    values = dict(median=4.0, p10=-3.0, q1=1.0, q3=8.0, p90=15.0) | kw
    return PeerStat("operating_margin", sample_size=n, scope=scope, unit="%", percentile=91.0, **values)


def test_peer_quartiles_are_computed_with_the_median():
    values = [float(v) for v in range(1, 21)]  # 1..20
    s = peer_stats(metric(value=10.0), values)
    assert s.median == 10.5
    assert s.q1 == pytest.approx(5.75) and s.q3 == pytest.approx(15.25)
    assert s.p10 == pytest.approx(2.9) and s.p90 == pytest.approx(18.1)


def test_peer_strip_is_labelled_as_a_distribution_not_a_rank():
    strip = peer_strip(metric(), stat(), "KRW", "p")
    assert strip.title == "동종업계 34개사 분포"
    text = str(strip.svg) + strip.spread_text + strip.value_text
    for ranking in ("상위", "하위", "등", "순위", "최상위", "91"):
        assert ranking not in text, ranking
    assert "가운데 50% 1% ~ 8%" in strip.spread_text and "중앙값 4%" in strip.spread_text
    market = peer_strip(metric(), stat(scope=PeerScope.MARKET, n=880), "KRW", "p")
    assert market.title == "전체 상장사 880개사 분포"


def test_peer_strip_structure_box_median_marker_and_engine_bands():
    root = parse(peer_strip(metric(), stat(), "KRW", "p").svg)
    assert root.get("role") == "img" and root.find(f"{SVG}desc") is not None
    assert root.find(f".//{SVG}rect[@class='fc-ch-box']") is not None
    assert root.find(f".//{SVG}line[@class='fc-ch-median']") is not None
    assert root.find(f".//{SVG}circle[@class='fc-ch-marker']") is not None
    bands = [r.get("class") for r in root.iter(f"{SVG}rect") if "fc-ch-band" in r.get("class")]
    assert bands == ["fc-ch-band sig-red", "fc-ch-band sig-yellow", "fc-ch-band sig-green"]
    ticks = [t.text for t in root.iter(f"{SVG}text") if t.get("class") == "fc-ch-tick"]
    assert ticks == ["0%", "7%"], "경계값은 engine 의 Band 그대로"


def test_lower_is_better_bands_run_green_to_red():
    m = metric("debt_ratio", 80.0, band=Band(good=100, warn=200, higher_is_better=False))
    root = parse(peer_strip(m, stat(median=120.0, p10=30.0, q1=70.0, q3=180.0, p90=400.0), "KRW", "p").svg)
    bands = [r.get("class").split()[-1] for r in root.iter(f"{SVG}rect") if "fc-ch-band" in r.get("class")]
    assert bands == ["sig-green", "sig-yellow", "sig-red"]


def test_neutral_metric_has_distribution_but_no_bands():
    m = metric("asset_turnover", 0.8, signal=Signal.NEUTRAL, band=None, unit="회")
    strip = peer_strip(m, stat(median=0.7, p10=0.2, q1=0.5, q3=1.0, p90=1.5), "KRW", "p")
    assert not strip.has_band
    assert "fc-ch-band" not in str(strip.svg)


@pytest.mark.parametrize("signal", [Signal.NOT_APPLICABLE, Signal.UNKNOWN])
def test_peer_strip_is_hidden_for_not_applicable_and_missing(signal):
    assert peer_strip(metric(signal=signal), stat(), "KRW", "p") is None
    assert peer_strip(metric(value=None, signal=Signal.UNKNOWN), stat(), "KRW", "p") is None
    assert peer_strip(metric(), None, "KRW", "p") is None
    assert peer_strip(metric(), stat(q1=None), "KRW", "p") is None


def test_far_outlier_is_pinned_to_the_edge_not_squashing_the_distribution():
    root = parse(peer_strip(metric(value=900.0), stat(), "KRW", "p").svg)
    assert root.find(f".//{SVG}circle[@class='fc-ch-marker']") is None
    marker = root.find(f".//{SVG}path[@class='fc-ch-marker']")
    assert marker is not None
    box = root.find(f".//{SVG}rect[@class='fc-ch-box']")
    assert float(box.get("width")) > 30, "분포 상자가 한 점으로 짜부라지지 않는다"
    assert "이 회사 900%" in root.find(f".//{SVG}g[@class='fc-ch-mine']/{SVG}title").text


def test_money_metric_strip_uses_korean_units():
    m = metric("fcf", 3.2e11, band=Band(good=0, warn=0), unit=MONEY)
    s = stat(median=1e10, p10=-5e10, q1=-1e9, q3=8e10, p90=4e11)
    strip = peer_strip(m, s, "KRW", "p")
    assert "억원" in strip.spread_text and strip.value_text == "이 회사 3,200억원"
    root = parse(strip.svg)
    bands = [r.get("class").split()[-1] for r in root.iter(f"{SVG}rect") if "fc-ch-band" in r.get("class")]
    assert bands == ["sig-red", "sig-green"], "good == warn 이면 주의 구간은 폭 0 이라 그리지 않는다"


# 경계값 표시: 그린 범위 밖의 경계값은 끝에 "경계 200% →", 가까이 붙은 두 경계값은 한 이름표로.
# 예전엔 두 번째 경계값 글자를 소리 없이 뺐다 — 실데이터 FY2025 에서 유동비율 띠 2,487개 중 2,010개,
# 이자보상배율 2,344개 중 2,256개가 경계값 하나를 잃었다.

DEBT_BAND = Band(good=100, warn=200, higher_is_better=False)


def strip_texts(svg, cls):
    return [t.text for t in parse(svg).iter(f"{SVG}text") if t.get("class") == cls]


def edge_lines(svg):
    return [float(ln.get("x1")) for ln in parse(svg).iter(f"{SVG}line") if ln.get("class") == "fc-ch-edge"]


def test_thresholds_in_range_get_a_line_and_a_tick_each():
    svg = peer_strip(metric(), stat(), "KRW", "p").svg
    assert strip_texts(svg, "fc-ch-tick") == ["0%", "7%"]
    assert len(edge_lines(svg)) == 2
    assert strip_texts(svg, "fc-ch-off") == []


def test_thresholds_left_of_the_range_are_named_at_the_left_edge():
    """모두가 40~60% 인 업종 — 0%·7% 를 억지로 넣으면 분포가 한쪽에 짜부라진다. 끝에 이름만."""
    m = metric(value=50.0)
    svg = peer_strip(m, stat(median=50.0, p10=40.0, q1=45.0, q3=55.0, p90=60.0), "KRW", "p").svg
    assert strip_texts(svg, "fc-ch-off") == ["← 경계 0%·7%"]
    assert strip_texts(svg, "fc-ch-tick") == [] and edge_lines(svg) == []
    off = next(t for t in parse(svg).iter(f"{SVG}text") if t.get("class") == "fc-ch-off")
    assert off.get("text-anchor") == "start"
    box = parse(svg).find(f".//{SVG}rect[@class='fc-ch-box']")
    assert float(box.get("width")) > 100, "분포가 넓게 보인다"


def test_thresholds_right_of_the_range_are_named_at_the_right_edge():
    m = metric("debt_ratio", 20.0, band=DEBT_BAND)
    svg = peer_strip(m, stat(median=20.0, p10=5.0, q1=12.0, q3=30.0, p90=40.0), "KRW", "p").svg
    assert strip_texts(svg, "fc-ch-off") == ["경계 100%·200% →"]
    off = next(t for t in parse(svg).iter(f"{SVG}text") if t.get("class") == "fc-ch-off")
    assert off.get("text-anchor") == "end"
    rects = parse(svg).iter(f"{SVG}rect")
    bands = [r.get("class").split()[-1] for r in rects if "fc-ch-band" in r.get("class")]
    assert bands == ["sig-green"], "그린 범위 안은 전부 정상 구간"


def test_thresholds_on_both_sides_of_the_range():
    m = metric(value=50.0, band=Band(good=100, warn=0))
    svg = peer_strip(m, stat(median=50.0, p10=45.0, q1=48.0, q3=52.0, p90=55.0), "KRW", "p").svg
    assert strip_texts(svg, "fc-ch-off") == ["← 경계 0%", "경계 100% →"]


def test_threshold_between_peers_and_the_company_stays_in_range():
    """동종업계는 10% 안팎, 이 회사는 -2% — 둘 사이의 0% 선이 이 회사가 위험 구간이라는 걸 보여 준다."""
    m = metric("roe", -2.0, signal=Signal.RED, band=Band(good=8, warn=0))
    svg = peer_strip(m, stat(median=10.0, p10=8.6, q1=9.8, q3=11.0, p90=11.5), "KRW", "p").svg
    assert strip_texts(svg, "fc-ch-tick") == ["0%", "8%"] and strip_texts(svg, "fc-ch-off") == []
    assert parse(svg).find(f".//{SVG}circle[@class='fc-ch-marker']") is not None, "이 회사 값도 범위 안"


def test_one_threshold_in_range_one_off_range_do_not_overlap():
    """부채비율 100% 는 오른쪽 끝 가까이, 200% 는 밖 — 두 이름표가 겹치면 아랫줄로 내린다."""
    m = metric("debt_ratio", 50.0, band=DEBT_BAND)
    strip = peer_strip(m, stat(median=50.0, p10=20.0, q1=35.0, q3=70.0, p90=100.0), "KRW", "p")
    assert strip_texts(strip.svg, "fc-ch-tick") == ["100%"]
    assert strip_texts(strip.svg, "fc-ch-off") == ["경계 200% →"]
    texts = {t.get("class"): float(t.get("y")) for t in parse(strip.svg).iter(f"{SVG}text")}
    assert texts["fc-ch-off"] != texts["fc-ch-tick"]
    height = float(parse(strip.svg).get("viewBox").split()[3])
    assert texts["fc-ch-off"] < height, "아랫줄도 그림 안에"


def test_close_thresholds_share_one_label_instead_of_dropping_one():
    """이자보상배율 1배·3배 — 분포가 -10~100배면 두 선이 몇 px 차이. 둘 다 그리고 이름표는 하나로."""
    m = metric("interest_coverage", 90.0, band=Band(good=3, warn=1), unit="배")
    svg = peer_strip(m, stat(median=1.5, p10=-10.0, q1=-2.0, q3=12.0, p90=100.0), "KRW", "p").svg
    assert len(edge_lines(svg)) == 2
    assert strip_texts(svg, "fc-ch-tick") == ["1배·3배"]


async def test_peer_strips_skip_metrics_without_peers_or_judgement(checkups):
    data = checkups[KB.stock_code]
    peers = {m.key: stat() for m in data.result.metrics}
    strips = peer_strips(dataclasses.replace(data, peers=peers))
    na = {m.key for m in data.result.metrics if m.signal in (Signal.NOT_APPLICABLE, Signal.UNKNOWN)}
    assert na and not (na & set(strips))
    assert set(strips) == {m.key for m in data.result.metrics} - na
    assert peer_strips(dataclasses.replace(data, peers={})) == {}


async def test_metric_card_renders_the_strip_inside_it(checkups):
    from fin_checkup.site.render import make_env

    data = checkups[SAMSUNG.stock_code]
    strips = peer_strips(dataclasses.replace(data, peers={"operating_margin": stat()}))
    html = (
        make_env()
        .get_template("company/_peer_strip.html")
        .render(m=type("M", (), {"key": "operating_margin"}), peer_strips=strips)
    )
    assert "동종업계 34개사 분포" in html and "<svg" in html and "&lt;svg" not in html
    empty = (
        make_env()
        .get_template("company/_peer_strip.html")
        .render(m=type("M", (), {"key": "roe"}), peer_strips=strips)
    )
    assert empty.strip() == ""


# ── 임베드 배지 ───────────────────────────────────────────────────


COUNTS = {Signal.GREEN: 9, Signal.YELLOW: 4, Signal.RED: 2, Signal.NOT_APPLICABLE: 1, Signal.UNKNOWN: 1}


def test_badge_shows_signal_counts_and_fiscal_year_not_a_score():
    svg = badge_svg(COUNTS, 2025, company="기아")
    root = parse(svg)
    assert badge_label(COUNTS, 2025) == "fin-checkup | 🟢9 🟡4 🔴2 ⊘1 ⚫1 · 2025"
    assert "🟢9 🟡4 🔴2 ⊘1 ⚫1 · 2025" in root.find(f"{SVG}title").text
    visible = [t.text for t in root.iter(f"{SVG}text")]
    assert visible == ["fin-checkup", "정상 9", "주의 4", "위험 2", "1", "1", "· DART 2025"]
    lowered = svg.lower()
    for score in ("점수:", "점)", "/100", "score", "등급", "grade", "평균", "종합"):
        assert score not in lowered, f"배지에 종합 점수처럼 읽히는 '{score}'"
    assert "점수 아님" in root.find(f"{SVG}desc").text


def test_badge_is_self_contained_with_disclaimer_and_source():
    svg = badge_svg(COUNTS, 2024)
    desc = parse(svg).find(f"{SVG}desc").text
    assert DISCLAIMER in desc and "DART" in desc
    assert "<script" not in svg and "href" not in svg and "var(" not in svg


def test_badge_hides_empty_secondary_signals_but_keeps_primary_zero():
    labels = badge_label({Signal.GREEN: 17}, 2024)
    assert labels == "fin-checkup | 🟢17 🟡0 🔴0 · 2024"


def test_badge_escapes_company_name():
    svg = badge_svg(COUNTS, 2024, company='<b>"악성" & co</b>')
    assert "<b>" not in svg
    assert '<b>"악성" & co</b>' in parse(svg).find(f"{SVG}title").text


async def test_build_writes_a_badge_per_company_and_an_embed_snippet(cache, tmp_path):
    out = tmp_path / "out"
    await build_site(cache, out, 2024, built_on=date(2026, 11, 21), base_url="https://example.org/fc/")
    badge = (out / "badge" / f"{SAMSUNG.stock_code}.svg").read_text(encoding="utf-8")
    parse(badge)
    assert "· DART 2024" in badge
    html = (out / "c" / SAMSUNG.stock_code / "index.html").read_text(encoding="utf-8")
    assert 'id="embed"' in html and "블로그에 붙이기" in html
    assert 'src="../../badge/005930.svg"' in html
    md = "[![fin-checkup 삼성전자 2024 재무 신호등](https://example.org/fc/badge/005930.svg)](https://example.org/fc/c/005930/)"
    assert md in html
    assert "&lt;a href=&#34;https://example.org/fc/c/005930/&#34;&gt;" in html or (
        "&lt;a href=&quot;https://example.org/fc/c/005930/&quot;&gt;" in html
    )
