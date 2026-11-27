"""회사 페이지 차트 — JS·CDN 없이 서버에서 만드는 인라인 SVG 문자열.

원칙
- **없는 해는 그리지 않는다.** 이어 긋거나 0 막대로 메우지 않고, 그 칸에 ⚫ 데이터 없음(또는 ⊘ 해당 없음)을
  글자로 적는다. 선 그래프 대신 연도별 막대를 쓰는 것도 그래서다 — 선은 빈 해를 건너 이어 보인다.
- **색은 CSS 변수로만.** 모양은 클래스(`fc-ch-*`)만 달고 색·글꼴은 `static/site.css` 가 정한다.
  다크 모드가 따라오고, CSP(`style-src 'self'`) 가 막는 인라인 style 을 쓰지 않는다.
- **글자는 전부 이스케이프.** 지표 이름은 코드에서 오지만 회사명·값 문자열이 섞여도 SVG 가 깨지지 않게
  `_esc` 를 거친다. 결과는 `Markup` 이라 템플릿이 다시 이스케이프하지 않는다.
- **색만으로 말하지 않는다.** 모든 그림에 `<title>`·`<desc>`, 막대마다 `<title>`(마우스를 올리면 값),
  템플릿은 같은 값을 `<details>` 표로 한 번 더 싣는다.

확장 지점: 새 그림은 `(데이터) -> Markup` 함수 하나 + `tests/test_site_charts.py`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from html import escape

from markupsafe import Markup

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.format import chart_scale, format_metric, format_money
from fin_checkup.metrics.engine import METRIC_DEFS, MONEY, Metric, checkup
from fin_checkup.metrics.peers import PeerStat
from fin_checkup.metrics.signals import Band, Signal
from fin_checkup.metrics.units import UNIT_SUSPECT_NOTE
from fin_checkup.models import Financials
from fin_checkup.service import Checkup
from fin_checkup.theme import SIGNAL_COLORS

#: 추이에 그릴 금액 계정 (Financials 필드, 이름). 순서가 화면 순서다.
AMOUNT_SERIES: tuple[tuple[str, str], ...] = (
    ("revenue", "매출액"),
    ("operating_income", "영업이익"),
    ("net_income", "당기순이익"),
    ("operating_cash_flow", "영업활동현금흐름"),
)
#: 추이에 그릴 비율 지표 (engine 의 key). 이름·단위·⊘ 여부는 엔진이 정한다.
RATIO_SERIES: tuple[str, ...] = (
    "operating_margin",
    "net_margin",
    "roe",
    "debt_ratio",
    "current_ratio",
    "equity_ratio",
)


def _esc(text: object) -> str:
    return escape(str(text), quote=True)


# ── 추이 데이터 ───────────────────────────────────────────────────


class Gap(str, Enum):
    """그 해 값이 없는 이유. 칸을 비울 때 무엇 때문인지 감추지 않는다."""

    NO_YEAR = "no_year"
    NO_ACCOUNT = "no_account"
    NOT_APPLICABLE = "not_applicable"
    #: 그해 보고서 금액이 단위 오류(×1000) 의심이라 그리지 않는다(`metrics.units`).
    UNIT_SUSPECT = "unit_suspect"

    @property
    def mark(self) -> str:
        return "⊘" if self is Gap.NOT_APPLICABLE else "⚫"

    @property
    def label(self) -> str:
        if self is Gap.UNIT_SUSPECT:
            return "단위 의심"
        return "해당 없음" if self is Gap.NOT_APPLICABLE else "데이터 없음"

    @property
    def reason(self) -> str:
        return {
            "no_year": "이 해의 사업보고서 재무가 아직 수집되지 않았습니다",
            "no_account": "공시에서 이 계정을 찾지 못했습니다",
            "not_applicable": "이 업종에는 뜻이 없는 지표라 판정하지 않습니다",
            "unit_suspect": UNIT_SUSPECT_NOTE,
        }[self.value]


@dataclass(frozen=True)
class TrendPoint:
    year: int
    value: float | None
    #: 표시 문자열. 값이 없으면 "⚫ 데이터 없음" / "⊘ 해당 없음".
    text: str
    gap: Gap | None = None
    #: 다음 해 보고서(전기 열)가 같은 해를 1% 넘게 다르게 적었을 때 그 값 — 재작성(분할·합병 재분류 등).
    #: 막대는 그해 보고서 값 그대로 그리고 "재작성" 표시만 단다.
    restated: float | None = None
    restated_text: str = ""

    @property
    def restated_line(self) -> str:
        return f"재작성 — 다음 해 보고서 기준 값: {self.restated_text}" if self.restated is not None else ""


@dataclass(frozen=True)
class TrendSeries:
    key: str
    label: str
    #: `MONEY` 또는 엔진 지표의 단위(%, 배 …).
    unit: str
    currency: str
    points: tuple[TrendPoint, ...]

    @property
    def present(self) -> list[TrendPoint]:
        return [p for p in self.points if p.gap is None]

    @property
    def has_restated(self) -> bool:
        return any(p.restated is not None for p in self.points)

    @property
    def has_unit_suspect(self) -> bool:
        return any(p.gap is Gap.UNIT_SUSPECT for p in self.points)


def _gap_point(year: int, gap: Gap) -> TrendPoint:
    return TrendPoint(year=year, value=None, text=f"{gap.mark} {gap.label}", gap=gap)


#: 그해 보고서 값과 다음 해 보고서의 같은 해 값이 이만큼(상대) 넘게 다르면 재작성으로 본다.
RESTATED_TOLERANCE = 0.01


def is_restated(own: float | None, later: float | None) -> bool:
    if own is None or later is None:
        return False
    return abs(own - later) > RESTATED_TOLERANCE * max(abs(own), abs(later))


def _following_comparative(by_year: dict[int, Financials], year: int) -> Financials | None:
    """다음 해 보고서의 전기 열(같은 해). 어느 쪽이든 단위 오류 의심이면 견주지 않는다."""
    own, following = by_year.get(year), by_year.get(year + 1)
    if own is None or following is None or own.unit_suspect or following.unit_suspect:
        return None
    return following.comparative


def trend_series(data: Checkup, years: int = 5) -> list[TrendSeries]:
    """최근 `years` 개 사업연도(최신 연도 포함)의 금액·비율 추이.

    캐시에 없는 해도 칸으로 남긴다 — 5칸 중 2칸만 차 있으면 그렇게 보여야 한다.
    비율 지표가 이 업종에 ⊘ 이면(최신 연도 기준) 그 추이는 통째로 뺀다.
    """
    latest = data.history[-1]
    currency = latest.currency
    by_year: dict[int, Financials] = {f.bsns_year: f for f in data.history}
    span = range(latest.bsns_year - years + 1, latest.bsns_year + 1)
    sector = data.result.sector

    series: list[TrendSeries] = []
    for field, label in AMOUNT_SERIES:
        points = []
        for year in span:
            fin = by_year.get(year)
            value = getattr(fin, field) if fin is not None else None
            if fin is None:
                points.append(_gap_point(year, Gap.NO_YEAR))
            elif fin.unit_suspect:
                points.append(_gap_point(year, Gap.UNIT_SUSPECT))
            elif value is None:
                points.append(_gap_point(year, Gap.NO_ACCOUNT))
            else:
                later_fin = _following_comparative(by_year, year)
                later = getattr(later_fin, field) if later_fin is not None else None
                restated = later if is_restated(value, later) else None
                text = format_money(value, currency)
                later_text = format_money(restated, currency) if restated is not None else ""
                if later_text == text:  # 1~5% 차이는 축약하면 같은 글자다 — 원 단위로 적는다
                    later_text = format_money(restated, currency, compact=False)
                points.append(TrendPoint(year, value, text, restated=restated, restated_text=later_text))
        series.append(TrendSeries(field, label, MONEY, currency, tuple(points)))

    yearly = {year: checkup(fin, by_year.get(year - 1), sector=sector) for year, fin in by_year.items()}
    for key in RATIO_SERIES:
        current = data.result.by_key(key)
        if current is None or current.signal is Signal.NOT_APPLICABLE:
            continue
        points = []
        for year in span:
            result = yearly.get(year)
            metric = result.by_key(key) if result is not None else None
            if result is None:
                points.append(_gap_point(year, Gap.NO_YEAR))
            elif by_year[year].unit_suspect:
                points.append(_gap_point(year, Gap.UNIT_SUSPECT))
            elif metric is None or metric.signal is Signal.NOT_APPLICABLE:
                points.append(_gap_point(year, Gap.NOT_APPLICABLE))
            elif metric.value is None:
                points.append(_gap_point(year, Gap.NO_ACCOUNT))
            else:
                later_fin = _following_comparative(by_year, year)
                later = (
                    checkup(later_fin, sector=sector).by_key(key) if later_fin is not None else None
                )
                later_value = later.value if later is not None else None
                restated = later_value if is_restated(metric.value, later_value) else None
                points.append(TrendPoint(
                    year, metric.value, format_metric(metric, currency), restated=restated,
                    restated_text=format_metric(later, currency) if restated is not None else "",
                ))
        series.append(TrendSeries(key, current.label, current.unit, currency, tuple(points)))
    return series


# ── 눈금 ──────────────────────────────────────────────────────────


def nice_ticks(lo: float, hi: float, count: int = 3) -> list[float]:
    """0 을 지나는 깔끔한 눈금(1·2·2.5·5 × 10ⁿ 간격). 막대는 0 에서 자라므로 0 은 늘 포함."""
    lo, hi = min(lo, 0.0), max(hi, 0.0)
    if hi == lo:
        hi = lo + 1.0
    raw = (hi - lo) / max(count, 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = math.floor(lo / step) * step
    ticks: list[float] = []
    value = start
    while value < hi + step * 0.999:
        ticks.append(round(value, 10))
        value += step
    return ticks


_SHORT_KRW = {"조원": "조", "억원": "억", "만원": "만"}
_SHORT_USD = {"십억 달러": "B", "백만 달러": "M", "천 달러": "K"}


def _tick_text(value: float, unit: str, divisor: float, scale_label: str) -> str:
    if unit == MONEY:
        scaled = value / divisor
        digits = 0 if abs(scaled) >= 10 or scaled == int(scaled) else 1
        short = _SHORT_KRW.get(scale_label) or _SHORT_USD.get(scale_label, "")
        return f"{scaled:,.{digits}f}{short}"
    digits = 0 if abs(value) >= 10 or value == int(value) else 1
    return f"{value:,.{digits}f}{unit}"


def scale_note(series: TrendSeries) -> str:
    """패널 캡션에 붙일 단위 설명."""
    if series.unit == MONEY:
        _, label = chart_scale([p.value for p in series.points], series.currency)
        return f"단위 {label}"
    return f"단위 {series.unit}"


# ── 추이 SVG ──────────────────────────────────────────────────────

#: viewBox 크기. 폭 100% 로 늘어나므로 360px 화면에서는 약 0.9배, 넓은 화면 2단에서는 약 1.1배.
TREND_W, TREND_H = 320, 128
_LEFT, _RIGHT, _TOP, _BOTTOM = 46, 6, 20, 24
_BAR_MAX = 24
_RADIUS = 4


def _bar_path(x: float, width: float, y_zero: float, y_end: float) -> str:
    """0 에서 자라는 막대. 데이터 끝만 4px 둥글게, 기준선 쪽은 각지게."""
    height = abs(y_end - y_zero)
    r = min(_RADIUS, height, width / 2)
    left, right = x, x + width
    if y_end <= y_zero:  # 양수 — 위로
        return (
            f"M{left:.1f},{y_zero:.1f}V{y_end + r:.1f}"
            f"Q{left:.1f},{y_end:.1f} {left + r:.1f},{y_end:.1f}"
            f"H{right - r:.1f}Q{right:.1f},{y_end:.1f} {right:.1f},{y_end + r:.1f}"
            f"V{y_zero:.1f}Z"
        )
    return (  # 음수 — 아래로
        f"M{left:.1f},{y_zero:.1f}V{y_end - r:.1f}"
        f"Q{left:.1f},{y_end:.1f} {left + r:.1f},{y_end:.1f}"
        f"H{right - r:.1f}Q{right:.1f},{y_end:.1f} {right:.1f},{y_end - r:.1f}"
        f"V{y_zero:.1f}Z"
    )


def _desc(series: TrendSeries) -> str:
    return " · ".join(f"{p.year}년 {p.text}" for p in series.points)


def trend_svg(series: TrendSeries, chart_id: str) -> Markup:
    """연도별 막대 한 칸 그림(작은 배수 한 장). 값이 없는 해는 ⚫/⊘ 칸."""
    cid = _esc(chart_id)
    values = [p.value for p in series.present if p.value is not None]
    divisor, scale_label = chart_scale(values, series.currency) if series.unit == MONEY else (1.0, "")
    ticks = nice_ticks(min(values, default=0.0), max(values, default=0.0))
    lo, hi = ticks[0], ticks[-1]

    plot_top, plot_bottom = _TOP, TREND_H - _BOTTOM
    # 음수가 있으면 맨 아래 막대 끝 밑에 값 글자가 들어갈 자리를 남긴다.
    floor = plot_bottom - (14 if lo < 0 else 0)

    def y(value: float) -> float:
        return floor - (value - lo) / (hi - lo) * (floor - plot_top)

    slot = (TREND_W - _LEFT - _RIGHT) / len(series.points)
    bar = min(_BAR_MAX, slot * 0.55)
    y_zero = y(0.0)
    latest = series.points[-1]

    parts = [
        f'<svg class="fc-ch fc-ch-trend" viewBox="0 0 {TREND_W} {TREND_H}" role="img" '
        f'aria-labelledby="{cid}-t {cid}-d" xmlns="http://www.w3.org/2000/svg">',
        f'<title id="{cid}-t">{_esc(series.label)} {len(series.points)}개년 추이</title>',
        f'<desc id="{cid}-d">{_esc(_desc(series))}</desc>',
    ]
    for tick in ticks:
        ty = y(tick)
        cls = "fc-ch-zero" if tick == 0 else "fc-ch-grid"
        parts.append(
            f'<line class="{cls}" x1="{_LEFT}" x2="{TREND_W - _RIGHT}" y1="{ty:.1f}" y2="{ty:.1f}"/>'
        )
        parts.append(
            f'<text class="fc-ch-tick" x="{_LEFT - 6}" y="{ty + 4:.1f}" text-anchor="end">'
            f"{_esc(_tick_text(tick, series.unit, divisor, scale_label))}</text>"
        )

    for i, point in enumerate(series.points):
        cx = _LEFT + slot * (i + 0.5)
        is_latest = point is latest
        parts.append(f'<g class="fc-ch-year{" fc-ch-latest" if is_latest else ""}">')
        tooltip = f"{point.year}년 {series.label}: {point.text}"
        if point.restated is not None:
            tooltip += f" · {point.restated_line}"
        parts.append(f"<title>{_esc(tooltip)}</title>")
        if point.gap is not None or point.value is None:
            gap = point.gap or Gap.NO_ACCOUNT
            gx = cx - slot / 2 + 3
            mid = (plot_top + plot_bottom) / 2
            parts.append(
                f'<rect class="fc-ch-gap" x="{gx:.1f}" y="{plot_top}" width="{slot - 6:.1f}" '
                f'height="{plot_bottom - plot_top}" rx="4"/>'
            )
            parts.append(
                f'<text class="fc-ch-gap-mark" x="{cx:.1f}" y="{mid - 6:.1f}" text-anchor="middle">'
                f"{gap.mark}</text>"
            )
            for line, text in enumerate(gap.label.split(" ")):
                parts.append(
                    f'<text class="fc-ch-gap-text" x="{cx:.1f}" y="{mid + 10 + line * 12:.1f}" '
                    f'text-anchor="middle">{_esc(text)}</text>'
                )
        else:
            y_end = y(point.value)
            parts.append(f'<path class="fc-ch-bar" d="{_bar_path(cx - bar / 2, bar, y_zero, y_end)}"/>')
            if point.restated is not None:
                # 막대는 그해 보고서 값 그대로. 그림 맨 위 줄에 작게 표시만 한다(최신 연도는 다음 해가 없다).
                parts.append(
                    f'<text class="fc-ch-gap-text fc-ch-restated" x="{cx:.1f}" y="{plot_top - 8}" '
                    'text-anchor="middle">재작성</text>'
                )
            if is_latest:
                label_y = y_end - 5 if point.value >= 0 else y_end + 12
                parts.append(
                    f'<text class="fc-ch-value" x="{cx:.1f}" y="{label_y:.1f}" text-anchor="middle">'
                    f"{_esc(_tick_text(point.value, series.unit, divisor, scale_label))}</text>"
                )
        parts.append(
            f'<text class="fc-ch-x" x="{cx:.1f}" y="{TREND_H - 6}" text-anchor="middle">'
            f"{_esc(point.year)}</text>"
        )
        parts.append("</g>")
    parts.append("</svg>")
    return Markup("".join(parts))


@dataclass(frozen=True)
class TrendPanel:
    series: TrendSeries
    svg: Markup
    scale_note: str


def trend_panels(data: Checkup, years: int = 5) -> list[TrendPanel]:
    """회사 페이지 '5개년 추이' 섹션이 쓰는 패널 목록."""
    code = data.corp.stock_code or data.corp.corp_code
    return [
        TrendPanel(s, trend_svg(s, f"trend-{code}-{s.key}"), scale_note(s)) for s in trend_series(data, years)
    ]


def years_of(panels: Sequence[TrendPanel]) -> list[int]:
    """표 머리에 쓸 연도. 모든 패널이 같은 연도 칸을 쓴다."""
    return [p.year for p in panels[0].series.points] if panels else []


# ── 동종업계 분포 위 이 회사의 위치 ──────────────────────────────

#: viewBox 크기. 지표 카드 안에 들어가므로 낮고 넓게.
STRIP_W, STRIP_H = 320, 76
_STRIP_X0, _STRIP_X1 = 10, 310
_DIST_Y = 24  # 상자·수염·표식의 가운데 줄
_BAND_Y = 42  # 경계값 띠
#: 띠 아래 구간 이름(정상·주의·위험) 줄. 색만으로 구간을 가르지 않게 글자로도 적는다.
#: 경계값 숫자는 그 아래 줄.
_BAND_LABEL_Y = 58
#: 구간 이름을 적을 최소 폭(px). 더 좁으면 뺀다 — 그 구간은 경계값 숫자와 카드의 경계값 문장이 말해 준다.
BAND_LABEL_MIN_WIDTH = 30
_LABEL_GAP = 36  # 경계값 선 사이가 이보다 가까우면 두 선의 이름표를 하나("1배·3배")로 합친다.
#: 분포(10~90%)에서 폭의 이만큼 넘게 떨어진 값은 범위에 넣지 않는다 — 이 회사 값은 끝에 화살표로,
#: 경계값은 끝에 "경계 200% →" 로. 억지로 넣으면 분포가 한 점으로 짜부라진다.
_NEAR = 0.6
_TICK_ROW = STRIP_H - 2
_TICK_ROW_2 = STRIP_H + 11  # 이름표가 겹치면 쓰는 아랫줄. 그때만 viewBox 를 늘린다.


def _label_width(text: str, size: float = 11) -> float:
    """11px 산세리프 글자 폭 어림 — 이름표끼리 겹치는지만 본다."""
    return sum(size * (0.95 if ord(ch) >= 0x1100 else 0.6) for ch in text)


def _value_text(value: float, unit: str, currency: str) -> str:
    if unit == MONEY:
        return format_money(value, currency)
    if value == int(value) or abs(value) >= 100:
        return f"{value:,.0f}{unit}"
    return f"{value:,.1f}{unit}"


def _band_segments(band: Band, lo: float, hi: float) -> list[tuple[float, float, Signal]]:
    """경계값이 나누는 구간을 [lo, hi] 안으로 자른 것. 폭이 0 인 구간(good == warn)은 뺀다."""
    if band.higher_is_better:
        raw = [
            (-math.inf, band.warn, Signal.RED),
            (band.warn, band.good, Signal.YELLOW),
            (band.good, math.inf, Signal.GREEN),
        ]
    else:
        raw = [
            (-math.inf, band.good, Signal.GREEN),
            (band.good, band.warn, Signal.YELLOW),
            (band.warn, math.inf, Signal.RED),
        ]
    out = []
    for a, b, signal in raw:
        a, b = max(a, lo), min(b, hi)
        if b > a:
            out.append((a, b, signal))
    return out


def _edge_labels(
    edges: list[float], lo: float, hi: float, x: Callable[[float], float], unit: str, currency: str
) -> list[tuple[str, str, float, str, int]]:
    """경계값 이름표 (class, 글자, x, text-anchor, y). 경계값은 하나도 빼지 않는다.

    - 그린 범위 안: 선 아래 숫자. 두 선이 `_LABEL_GAP` 보다 가까우면 "1배·3배" 한 이름표로.
    - 범위 밖: 그쪽 끝에 "← 경계 0%" / "경계 100%·200% →".
    - 이름표끼리 겹치면 뒤의 것을 아랫줄로.
    """
    def text(values: list[float]) -> str:
        return "·".join(_value_text(v, unit, currency) for v in values)

    inside = [e for e in edges if lo < e < hi]
    groups: list[list[float]] = []
    for edge in inside:
        if groups and x(edge) - x(groups[-1][-1]) < _LABEL_GAP:
            groups[-1].append(edge)
        else:
            groups.append([edge])
    labels: list[tuple[str, str, float, str, int]] = []
    left = [e for e in edges if e <= lo]
    if left:
        labels.append(("fc-ch-off", f"← 경계 {text(left)}", 0.0, "start", _TICK_ROW))
    for group in groups:
        label = text(group)
        half = _label_width(label) / 2
        centre = sum(x(e) for e in group) / len(group)
        labels.append(("fc-ch-tick", label, min(max(centre, half), STRIP_W - half), "middle", _TICK_ROW))
    right = [e for e in edges if e >= hi]
    if right:
        labels.append(("fc-ch-off", f"경계 {text(right)} →", float(STRIP_W), "end", _TICK_ROW))

    def span(label: tuple[str, str, float, str, int]) -> tuple[float, float]:
        _, t, lx, anchor, _ = label
        w = _label_width(t)
        return {"start": (lx, lx + w), "end": (lx - w, lx)}.get(anchor, (lx - w / 2, lx + w / 2))

    placed: list[tuple[str, str, float, str, int]] = []
    for label in labels:
        a, b = span(label)
        clash = any(
            p[4] == _TICK_ROW and span(p)[0] < b + 4 and a - 4 < span(p)[1] for p in placed
        )
        placed.append((*label[:4], _TICK_ROW_2) if clash else label)
    return placed


@dataclass(frozen=True)
class PeerStrip:
    svg: Markup
    #: "동종업계 34개사 분포" — 순위가 아니라 분포라는 걸 이름부터 말한다.
    title: str
    #: "가운데 50% 2.1% ~ 8.4% · 중앙값 4.3%"
    spread_text: str
    value_text: str
    #: "가운데 50% 위" · "가운데 50% 안" · "가운데 50% 아래" (순위·백분위 없이 위치만)
    position_text: str
    #: 경계값 띠를 그렸는지 (⚪ 측정값 지표는 경계값이 없다).
    has_band: bool


def peer_strip(metric: Metric, stat: PeerStat | None, currency: str, chart_id: str) -> PeerStrip | None:
    """한 지표의 대조군 분포(10·25·50·75·90 백분위수) 위에 이 회사 값을 찍은 띠.

    ⊘·⚫ 이거나 값·분포가 없으면 None — 판정하지 않는 지표를 분포 위에 올리면 판정처럼 읽힌다.
    순위·백분위 숫자는 쓰지 않는다. 경계값 띠는 `metric.band`(엔진이 실제로 쓴 것) 그대로.
    """
    if (
        stat is None
        or metric.value is None
        or metric.signal in (Signal.NOT_APPLICABLE, Signal.UNKNOWN)
        or None in (stat.p10, stat.q1, stat.q3, stat.p90)
    ):
        return None
    p10, q1, q3, p90, median = stat.p10, stat.q1, stat.q3, stat.p90, stat.median
    band = metric.band
    unit = metric.unit
    value = metric.value

    def near(v: float, lo: float, hi: float) -> bool:
        span = hi - lo or max(abs(hi), 1.0)
        return lo - span * _NEAR <= v <= hi + span * _NEAR

    # 동떨어진 값 하나가 분포를 한 점으로 짜부라뜨리지 않게, 너무 먼 값은 끝에 붙이고 화살표로 표시한다.
    lo, hi = p10, p90
    if near(value, lo, hi):
        lo, hi = min(lo, value), max(hi, value)
    # 경계값은 분포·이 회사 값 가까이 있거나 그 둘 사이에 있을 때만 범위에 넣는다 — 사이에 있는 선이
    # 이 회사가 어느 구간에 떨어졌는지를 보여 준다. 나머지는 끝에 이름표로("경계 200% →").
    edges = sorted({band.warn, band.good}) if band else []
    kept = [e for e in edges if near(e, lo, hi) or value < e < p10 or p90 < e < value]
    lo, hi = min([lo, *kept]), max([hi, *kept])
    if near(value, lo, hi):
        lo, hi = min(lo, value), max(hi, value)
    pad = (hi - lo or 1.0) * 0.06
    lo, hi = lo - pad, hi + pad

    def x(v: float) -> float:
        v = min(max(v, lo), hi)
        return _STRIP_X0 + (v - lo) / (hi - lo) * (_STRIP_X1 - _STRIP_X0)

    title = f"{stat.scope.label} {stat.sample_size}개사 분포"
    spread = (
        f"가운데 50% {_value_text(q1, unit, currency)} ~ {_value_text(q3, unit, currency)}"
        f" · 중앙값 {_value_text(median, unit, currency)}"
    )
    mine = f"이 회사 {_value_text(value, unit, currency)}"
    cid = _esc(chart_id)
    outer = f"{_value_text(p10, unit, currency)} ~ {_value_text(p90, unit, currency)}"
    desc = f"{title}. 10~90% 구간 {outer}, {spread}. {mine}."

    parts = [
        f'<svg class="fc-ch fc-ch-peer" viewBox="0 0 {STRIP_W} {STRIP_H}" role="img" '
        f'aria-labelledby="{cid}-t {cid}-d" xmlns="http://www.w3.org/2000/svg">',
        f'<title id="{cid}-t">{_esc(metric.label)} — {_esc(title)}</title>',
        f'<desc id="{cid}-d">{_esc(desc)}</desc>',
    ]
    if band is not None:
        parts.append('<g class="fc-ch-bands">')
        segments = _band_segments(band, lo, hi)
        for i, (a, b, signal) in enumerate(segments):
            x0 = x(a) + (1 if i else 0)
            x1 = x(b) - (1 if i < len(segments) - 1 else 0)
            parts.append(
                f'<rect class="fc-ch-band sig-{signal.value}" x="{x0:.1f}" y="{_BAND_Y}" '
                f'width="{max(x1 - x0, 0):.1f}" height="4" rx="2">'
                f"<title>{_esc(signal.emoji)} {_esc(signal.label)} 구간</title></rect>"
            )
            if x1 - x0 >= BAND_LABEL_MIN_WIDTH:
                parts.append(
                    f'<text class="fc-ch-band-label" x="{(x0 + x1) / 2:.1f}" y="{_BAND_LABEL_Y}" '
                    f'text-anchor="middle">{_esc(signal.label)}</text>'
                )
        labels = _edge_labels(edges, lo, hi, x, unit, currency)
        for edge in edges:
            if lo < edge < hi:
                parts.append(
                    f'<line class="fc-ch-edge" x1="{x(edge):.1f}" x2="{x(edge):.1f}" '
                    f'y1="{_BAND_Y - 3}" y2="{_BAND_Y + 7}"/>'
                )
        for cls, text, lx, anchor, row in labels:
            parts.append(
                f'<text class="{cls}" x="{lx:.1f}" y="{row}" text-anchor="{anchor}">{_esc(text)}</text>'
            )
        if any(row == _TICK_ROW_2 for *_, row in labels):
            parts[0] = parts[0].replace(f"0 0 {STRIP_W} {STRIP_H}", f"0 0 {STRIP_W} {_TICK_ROW_2 + 2}")
        parts.append("</g>")

    parts.append(f'<g class="fc-ch-dist"><title>{_esc(title)}: {_esc(spread)}</title>')
    parts.append(
        f'<line class="fc-ch-whisker" x1="{x(p10):.1f}" x2="{x(p90):.1f}" y1="{_DIST_Y}" y2="{_DIST_Y}"/>'
    )
    parts.append(
        f'<rect class="fc-ch-box" x="{x(q1):.1f}" y="{_DIST_Y - 7}" '
        f'width="{max(x(q3) - x(q1), 1.5):.1f}" height="14" rx="3"/>'
    )
    parts.append(
        f'<line class="fc-ch-median" x1="{x(median):.1f}" x2="{x(median):.1f}" '
        f'y1="{_DIST_Y - 9}" y2="{_DIST_Y + 9}"/>'
    )
    parts.append("</g>")

    mx = x(value)
    parts.append(f'<g class="fc-ch-mine"><title>{_esc(mine)}</title>')
    if value < lo:
        parts.append(f'<path class="fc-ch-marker" d="M{mx - 1:.1f},{_DIST_Y}l9,-6v12z"/>')
    elif value > hi:
        parts.append(f'<path class="fc-ch-marker" d="M{mx + 1:.1f},{_DIST_Y}l-9,-6v12z"/>')
    else:
        parts.append(f'<circle class="fc-ch-marker" cx="{mx:.1f}" cy="{_DIST_Y}" r="5"/>')
    val_label = _value_text(value, unit, currency)
    anchor = "start" if mx < 55 else "end" if mx > STRIP_W - 55 else "middle"
    parts.append(
        f'<text class="fc-ch-mine-label" x="{mx:.1f}" y="{_DIST_Y - 12}" text-anchor="{anchor}">'
        f"{_esc(val_label)}</text>"
    )
    parts.append("</g></svg>")
    if value > q3:
        pos = "가운데 50% 위"
    elif value < q1:
        pos = "가운데 50% 아래"
    else:
        pos = "가운데 50% 안"
    return PeerStrip(Markup("".join(parts)), title, spread, mine, pos, band is not None)


def peer_strips(data: Checkup) -> dict[str, PeerStrip]:
    """지표 key → 분포 띠. ⊘·⚫·대조군 없음은 빠진다."""
    code = data.corp.stock_code or data.corp.corp_code
    strips: dict[str, PeerStrip] = {}
    for metric in data.result.metrics:
        strip = peer_strip(
            metric, data.peers.get(metric.key), data.result.currency, f"peer-{code}-{metric.key}"
        )
        if strip is not None:
            strips[metric.key] = strip
    return strips


# ── 임베드 배지 ───────────────────────────────────────────────────

#: 배지는 다른 사이트에 <img> 로 붙는 독립 SVG 파일이라 CSS 변수를 못 쓴다. 색을 박는다.
#: 신호색은 theme.SIGNAL_COLORS(밝은 모드)와 같다.
_BADGE_LEFT_BG = "#3b3b38"
_BADGE_RIGHT_BG = "#f7f7f5"
_BADGE_INK = "#1b1b19"
_BADGE_BORDER = "#d4d4cf"
#: 배지에 싣는 순서 — 정상·주의·위험은 늘, 측정값·해당 없음·데이터 없음은 있을 때만.
BADGE_ORDER = (
    Signal.GREEN,
    Signal.YELLOW,
    Signal.RED,
    Signal.NEUTRAL,
    Signal.NOT_APPLICABLE,
    Signal.UNKNOWN,
)
_ALWAYS = {Signal.GREEN, Signal.YELLOW, Signal.RED}


def _badge_text_width(text: str) -> float:
    """11px 산세리프 글자 폭 어림. textLength 로 실제 렌더를 이 폭에 맞추므로 크게 어긋나지 않는다."""
    width = 0.0
    for ch in text:
        if ch.isdigit():
            width += 6.6
        elif ch == " ":
            width += 3.3
        elif ch in "·.-|":
            width += 3.6
        elif ord(ch) > 0x2E80:  # 한글·한자
            width += 11.0
        else:
            width += 6.4
    return width


def badge_counts(counts: dict[Signal, int]) -> list[tuple[Signal, int]]:
    return [(s, counts.get(s, 0)) for s in BADGE_ORDER if s in _ALWAYS or counts.get(s, 0)]


def badge_label(counts: dict[Signal, int], year: int) -> str:
    """'fin-checkup | 🟢9 🟡4 🔴2 ⊘1 ⚫1 · 2024' — 배지가 말하는 전부. 점수가 아니라 개수다."""
    chips = " ".join(f"{s.emoji}{n}" for s, n in badge_counts(counts))
    return f"fin-checkup | {chips} · {year}"


def _badge_dot(signal: Signal, cx: float, cy: float, colors: dict[Signal, str]) -> str:
    r = 4.5
    if signal is Signal.NOT_APPLICABLE:  # ⊘ — 빈 원 + 사선
        return (
            f'<circle cx="{cx:.1f}" cy="{cy}" r="{r - 0.6}" fill="none" stroke="{colors[signal]}" '
            f'stroke-width="1.2"/><path d="M{cx - 3:.1f},{cy + 3}L{cx + 3:.1f},{cy - 3}" '
            f'stroke="{colors[signal]}" stroke-width="1.2"/>'
        )
    if signal is Signal.NEUTRAL:  # ⚪ — 흰 원 + 테두리
        return (
            f'<circle cx="{cx:.1f}" cy="{cy}" r="{r - 0.5}" fill="#ffffff" '
            f'stroke="{colors[signal]}" stroke-width="1"/>'
        )
    return f'<circle cx="{cx:.1f}" cy="{cy}" r="{r}" fill="{colors[signal]}"/>'


def badge_svg(counts: dict[Signal, int], year: int, company: str = "") -> str:
    """블로그에 붙이는 배지 — 신호 개수와 사업연도. 종합 점수·등급·순위는 없다(REGULATORY.md).

    `<img>` 로 다른 사이트에 붙으므로 그 자체로 완결된 SVG 문서이고, 면책 문구와 출처를 `<desc>` 에 싣는다.
    """
    colors = {s: SIGNAL_COLORS[s] for s in BADGE_ORDER}
    height, pad, cy = 20, 7, 10
    left_text = "fin-checkup"
    left_w = _badge_text_width(left_text) + pad * 2

    pieces: list[str] = []
    x = left_w + pad
    for signal, n in badge_counts(counts):
        pieces.append(_badge_dot(signal, x + 4.5, cy, colors))
        x += 9 + 2
        # 정상·주의·위험 점은 모양이 같고 색만 다르다 — 색을 못 가르는 사람도 읽게 이름을 붙인다.
        # ⚪·⊘·⚫ 는 점 모양(흰 원·사선 원)과 이름 없는 숫자로 갈린다.
        text = f"{signal.label} {n}" if signal in _ALWAYS else str(n)
        tw = _badge_text_width(text)
        pieces.append(
            f'<text x="{x:.1f}" y="14" textLength="{tw:.1f}" lengthAdjust="spacingAndGlyphs">'
            f"{_esc(text)}</text>"
        )
        x += tw + 6
    tail = f"· DART {year}"
    tw = _badge_text_width(tail)
    pieces.append(
        f'<text x="{x:.1f}" y="14" textLength="{tw:.1f}" lengthAdjust="spacingAndGlyphs">{_esc(tail)}</text>'
    )
    width = x + tw + pad

    label = badge_label(counts, year)
    title = f"{company} {year} 재무 신호등 — {label}" if company else label
    desc = f"{year} 사업연도 신호 개수(점수 아님). 출처: 금융감독원 전자공시시스템(DART). {DISCLAIMER}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height}" '
        f'viewBox="0 0 {width:.1f} {height}" role="img" aria-label="{_esc(title)}">'
        f"<title>{_esc(title)}</title><desc>{_esc(desc)}</desc>"
        f'<rect width="{width:.1f}" height="{height}" rx="3" fill="{_BADGE_RIGHT_BG}" '
        f'stroke="{_BADGE_BORDER}"/>'
        f'<path d="M3,0H{left_w:.1f}V{height}H3a3,3 0 0 1-3-3V3a3,3 0 0 1 3-3z" fill="{_BADGE_LEFT_BG}"/>'
        f'<g font-family="Verdana,DejaVu Sans,Geneva,sans-serif" font-size="11">'
        f'<text x="{pad}" y="14" fill="#ffffff" textLength="{left_w - pad * 2:.1f}" '
        f'lengthAdjust="spacingAndGlyphs">{left_text}</text>'
        f'<g fill="{_BADGE_INK}">{"".join(pieces)}</g></g></svg>\n'
    )


def delta_bar_svg(before: float, after: float, before_text: str, after_text: str) -> Markup:
    """작년(위·회색) vs 올해(아래·파랑) 두 줄 가로 막대 — 카드 안 '작년 대비' 미니 그래프."""
    w, h = 160.0, 22.0
    lo = min(before, after, 0.0)
    hi = max(before, after, 0.0)
    span = hi - lo
    if span <= 0:
        return Markup("")
    zero_x = (-lo / span) * w if lo < 0 else 0.0

    def bar(val: float, y: float, cls: str) -> str:
        x_end = zero_x + (val / span) * w
        x0 = min(zero_x, x_end)
        bw = abs(x_end - zero_x)
        if val != 0 and bw < 2.5:
            bw = 2.5
            x0 = zero_x if val > 0 else zero_x - bw
        return f'<rect class="{cls}" x="{x0:.1f}" y="{y:.1f}" width="{bw:.1f}" height="7" rx="2"/>'

    zero_line = (
        f'<line class="fc-ch-zero" x1="{zero_x:.1f}" y1="0" x2="{zero_x:.1f}" y2="{h:.0f}"/>'
        if lo < 0
        else ""
    )
    title = f"작년 {before_text}, 올해 {after_text}"
    return Markup(
        f'<svg class="fc-ch fc-ch-delta" viewBox="0 0 {w:.0f} {h:.0f}" aria-hidden="true">'
        f"<title>{_esc(title)}</title>"
        f"{zero_line}"
        f'{bar(before, 2.0, "fc-ch-delta-prev")}'
        f'{bar(after, 12.0, "fc-ch-bar")}'
        f"</svg>"
    )


def signal_meter_svg(chips: Sequence[tuple[str, str, int]]) -> Markup:
    """신호 비율 가로 막대 — 한눈에 보기 상단에서 정상·주의·위험·기타 비중을 한 줄로 시각화."""
    total = sum(count for _, _, count in chips)
    if total <= 0:
        return Markup("")
    w, h = 400.0, 10.0
    gap = 2.0
    active = [(k, lbl, c) for k, lbl, c in chips if c > 0]
    usable = w - gap * max(len(active) - 1, 0)
    x = 0.0
    rects: list[str] = []
    for i, (key, label, count) in enumerate(active):
        seg_w = (count / total) * usable if i < len(active) - 1 else max(w - x, 1.0)
        rects.append(
            f'<rect class="fc-ch-meter-seg seg-{_esc(key)}" x="{x:.1f}" y="0" '
            f'width="{max(seg_w, 1.0):.1f}" height="{h:.0f}" rx="3">'
            f"<title>{_esc(label)} {count}개</title></rect>"
        )
        x += seg_w + gap
    return Markup(
        f'<svg class="fc-ch fc-ch-meter" viewBox="0 0 {w:.0f} {h:.0f}" '
        f'preserveAspectRatio="none" aria-hidden="true">{"".join(rects)}</svg>'
    )


def dist_strip_svg(
    *,
    label: str,
    count: int,
    p10: float | None,
    q1: float | None,
    median: float | None,
    q3: float | None,
    p90: float | None,
    unit: str,
    band: Band | None,
    chart_id: str,
) -> Markup | None:
    """업종·시장 분포 카드용 박스앤위스커 그림 — 10~90% 수염, Q1~Q3 상자, 중앙값 선, 경계값 띠."""
    if None in (p10, q1, median, q3, p90):
        return None
    assert p10 is not None and q1 is not None and median is not None and q3 is not None and p90 is not None
    lo, hi = p10, p90
    edges = sorted({band.warn, band.good}) if band else []

    def near(v: float, a: float, b: float) -> bool:
        span = b - a or max(abs(b), 1.0)
        return a - span * _NEAR <= v <= b + span * _NEAR

    kept = [e for e in edges if near(e, lo, hi)]
    lo, hi = min([lo, *kept]), max([hi, *kept])
    pad = (hi - lo or 1.0) * 0.06
    lo, hi = lo - pad, hi + pad

    def x(v: float) -> float:
        v = min(max(v, lo), hi)
        return _STRIP_X0 + (v - lo) / (hi - lo) * (_STRIP_X1 - _STRIP_X0)

    cid = _esc(chart_id)
    title = f"{label} 분포 ({count:,}곳)"
    spread = (
        f"가운데 50% {_value_text(q1, unit, 'KRW')} ~ {_value_text(q3, unit, 'KRW')}"
        f" · 중앙값 {_value_text(median, unit, 'KRW')}"
    )
    parts = [
        f'<svg class="fc-ch fc-ch-peer" viewBox="0 0 {STRIP_W} {STRIP_H}" role="img" '
        f'aria-labelledby="{cid}-t {cid}-d" xmlns="http://www.w3.org/2000/svg">',
        f'<title id="{cid}-t">{_esc(title)}</title>',
        f'<desc id="{cid}-d">{_esc(spread)}</desc>',
    ]
    if band is not None:
        parts.append('<g class="fc-ch-bands">')
        segments = _band_segments(band, lo, hi)
        for i, (a, b, signal) in enumerate(segments):
            x0 = x(a) + (1 if i else 0)
            x1 = x(b) - (1 if i < len(segments) - 1 else 0)
            parts.append(
                f'<rect class="fc-ch-band sig-{signal.value}" x="{x0:.1f}" y="{_BAND_Y}" '
                f'width="{max(x1 - x0, 0):.1f}" height="4" rx="2">'
                f"<title>{_esc(signal.emoji)} {_esc(signal.label)} 구간</title></rect>"
            )
            if x1 - x0 >= BAND_LABEL_MIN_WIDTH:
                parts.append(
                    f'<text class="fc-ch-band-label" x="{(x0 + x1) / 2:.1f}" y="{_BAND_LABEL_Y}" '
                    f'text-anchor="middle">{_esc(signal.label)}</text>'
                )
        labels = _edge_labels(edges, lo, hi, x, unit, "KRW")
        for edge in edges:
            if lo < edge < hi:
                parts.append(
                    f'<line class="fc-ch-edge" x1="{x(edge):.1f}" x2="{x(edge):.1f}" '
                    f'y1="{_BAND_Y - 3}" y2="{_BAND_Y + 7}"/>'
                )
        for cls, text, lx, anchor, row in labels:
            parts.append(
                f'<text class="{cls}" x="{lx:.1f}" y="{row}" text-anchor="{anchor}">{_esc(text)}</text>'
            )
        if any(row == _TICK_ROW_2 for *_, row in labels):
            parts[0] = parts[0].replace(f"0 0 {STRIP_W} {STRIP_H}", f"0 0 {STRIP_W} {_TICK_ROW_2 + 2}")
        parts.append("</g>")

    parts.append(f'<g class="fc-ch-dist"><title>{_esc(spread)}</title>')
    parts.append(
        f'<line class="fc-ch-whisker" x1="{x(p10):.1f}" x2="{x(p90):.1f}" y1="{_DIST_Y}" y2="{_DIST_Y}"/>'
    )
    parts.append(
        f'<rect class="fc-ch-box" x="{x(q1):.1f}" y="{_DIST_Y - 7}" '
        f'width="{max(x(q3) - x(q1), 1.5):.1f}" height="14" rx="3"/>'
    )
    parts.append(
        f'<line class="fc-ch-median" x1="{x(median):.1f}" x2="{x(median):.1f}" '
        f'y1="{_DIST_Y - 9}" y2="{_DIST_Y + 9}"/>'
    )
    parts.append("</g></svg>")
    return Markup("".join(parts))


_HM_GLYPH = {
    Signal.GREEN: "●",
    Signal.YELLOW: "▲",
    Signal.RED: "■",
    Signal.NEUTRAL: "▬",
    Signal.NOT_APPLICABLE: "⊘",
    Signal.UNKNOWN: "·",
}


def signal_heatmap_svg(data: Checkup, years: int = 5) -> Markup:
    """5개년 × 17개 지표 신호 히트맵 — 일회성 변동과 지속적 위험/정상 구간을 한눈에."""
    if not data.history:
        return Markup("")
    by_year = {f.bsns_year: f for f in data.history}
    latest = max(by_year)
    span = list(range(latest - years + 1, latest + 1))
    sector = data.result.sector
    currency = data.result.currency
    yearly = {
        y: (None if by_year[y].unit_suspect else checkup(by_year[y], by_year.get(y - 1), sector=sector))
        for y in span
        if y in by_year
    }

    label_w, col_w, gap, row_h, head_h = 168.0, 44.0, 4.0, 22.0, 24.0
    total_w = label_w + len(span) * (col_w + gap)
    total_h = head_h + len(METRIC_DEFS) * row_h + 4.0

    parts = [
        f'<svg class="fc-ch fc-ch-heatmap" viewBox="0 0 {total_w:.0f} {total_h:.0f}" '
        f'role="img" aria-label="5개년 재무 지표 신호 히트맵" xmlns="http://www.w3.org/2000/svg">',
        "<title>5개년 × 17개 지표 신호 히트맵</title>",
    ]
    for ci, yr in enumerate(span):
        cx = label_w + ci * (col_w + gap) + col_w / 2
        cls = "fc-ch-x fc-hm-latest" if yr == latest else "fc-ch-x"
        parts.append(f'<text class="{cls}" x="{cx:.1f}" y="16" text-anchor="middle">{yr}</text>')

    for ri, spec in enumerate(METRIC_DEFS):
        ry = head_h + ri * row_h
        parts.append(
            f'<a class="fc-hm-link" href="#m-{_esc(spec.key)}">'
            f'<text class="fc-hm-label" x="0" y="{ry + 14:.1f}">{_esc(spec.label)}</text></a>'
        )
        for ci, yr in enumerate(span):
            cx = label_w + ci * (col_w + gap)
            res = yearly.get(yr)
            if yr not in by_year:
                sig = Signal.UNKNOWN
                note = f"{sig.emoji} {sig.label} (사업보고서 미수집)"
            elif res is None:
                sig = Signal.UNKNOWN
                note = f"{sig.emoji} {sig.label} (단위 오류 의심)"
            else:
                m = res.by_key(spec.key)
                if m is None:
                    sig, note = Signal.UNKNOWN, f"{Signal.UNKNOWN.emoji} {Signal.UNKNOWN.label}"
                else:
                    sig = m.signal
                    val_txt = format_metric(m, currency)
                    note = f"{sig.emoji} {sig.label} ({val_txt})"
            glyph = _HM_GLYPH[sig]
            tip = f"{yr}년 {spec.label}: {note}"
            parts.append(
                f'<g class="fc-hm-cell seg-{sig.value}"><title>{_esc(tip)}</title>'
                f'<rect class="fc-hm-rect" x="{cx:.1f}" y="{ry + 2:.1f}" '
                f'width="{col_w:.0f}" height="{row_h - 4:.0f}" rx="4"/>'
                f'<text class="fc-hm-glyph" x="{cx + col_w / 2:.1f}" y="{ry + 14.5:.1f}" '
                f'text-anchor="middle">{glyph}</text></g>'
            )
    parts.append("</svg>")
    return Markup("".join(parts))


def signal_overview_svg(
    rows: Sequence[tuple[str, str, Sequence[tuple[str, str, int]]]],
    chart_id: str,
) -> Markup:
    """17개 지표 신호 구성 100% 누적 막대 — 섹터·시장 전체의 신호 분포를 한 축에서 비교."""
    if not rows:
        return Markup("")
    label_w, bar_w, right_w, row_h = 168.0, 240.0, 124.0, 21.0
    total_w = label_w + bar_w + right_w
    total_h = len(rows) * row_h + 6.0
    cid = _esc(chart_id)
    parts = [
        f'<svg class="fc-ch fc-ch-overview" viewBox="0 0 {total_w:.0f} {total_h:.0f}" '
        f'role="img" aria-labelledby="{cid}-t" xmlns="http://www.w3.org/2000/svg">',
        f'<title id="{cid}-t">17개 지표 신호 구성 비율</title>',
    ]
    for ri, (key, label, shares) in enumerate(rows):
        ry = 3.0 + ri * row_h
        parts.append(
            f'<a class="fc-hm-link" href="#d-{_esc(key)}">'
            f'<text class="fc-hm-label" x="0" y="{ry + 13:.1f}">{_esc(label)}</text></a>'
        )
        total = sum(cnt for _, _, cnt in shares)
        if total > 0:
            x = label_w
            active = [(k, lbl, cnt) for k, lbl, cnt in shares if cnt > 0]
            for i, (k, lbl, cnt) in enumerate(active):
                w = (cnt / total) * bar_w if i < len(active) - 1 else max(label_w + bar_w - x, 0.5)
                pct = round(cnt / total * 100)
                parts.append(
                    f'<rect class="fc-ch-meter-seg seg-{_esc(k)}" x="{x:.1f}" y="{ry + 3:.1f}" '
                    f'width="{max(w, 0.5):.1f}" height="12" rx="2">'
                    f"<title>{_esc(label)} · {_esc(lbl)} {pct}% ({cnt:,}곳)</title></rect>"
                )
                x += w
            green_cnt = sum(cnt for k, _, cnt in shares if k == "green")
            red_cnt = sum(cnt for k, _, cnt in shares if k == "red")
            g_pct = round(green_cnt / total * 100)
            r_pct = round(red_cnt / total * 100)
            parts.append(
                f'<text class="fc-ch-x" x="{label_w + bar_w + 8:.1f}" y="{ry + 13:.1f}">'
                f"정상 {g_pct}% · 위험 {r_pct}%</text>"
            )
    parts.append("</svg>")
    return Markup("".join(parts))


def sector_heatmap_svg(
    rows: Sequence[tuple[str, str, int, Sequence[tuple[str, str, float, bool, int]]]],
) -> Markup:
    """섹터 × 17개 지표 위험 비중 히트맵 — KSIC 코드순 고정, 색+수치 병행."""
    if not rows:
        return Markup("")
    metrics = rows[0][3]
    label_w, col_w, gap, row_h, head_h = 180.0, 34.0, 2.0, 22.0, 96.0
    total_w = label_w + len(metrics) * (col_w + gap) + 28.0
    total_h = head_h + len(rows) * row_h + 6.0
    parts = [
        f'<svg class="fc-ch fc-ch-sector-hm" viewBox="0 0 {total_w:.0f} {total_h:.0f}" '
        f'role="img" aria-label="섹터별 지표 위험 구간 비중 히트맵" xmlns="http://www.w3.org/2000/svg">',
        "<title>섹터별 17개 지표 위험(🔴) 비중 히트맵</title>",
    ]
    for ci, (m_label, _key, _pct, _na, _cnt) in enumerate(metrics):
        cx = label_w + ci * (col_w + gap) + col_w / 2
        short = m_label.split(" (")[0]
        parts.append(
            f'<text class="fc-hm-col" x="{cx:.1f}" y="{head_h - 6:.1f}" '
            f'transform="rotate(-45 {cx:.1f} {head_h - 6:.1f})" text-anchor="start">{_esc(short)}</text>'
        )
    for ri, (code, name, total, cells) in enumerate(rows):
        ry = head_h + ri * row_h
        short_name = name[:14] + ("…" if len(name) > 14 else "")
        parts.append(
            f'<a class="fc-hm-link" href="{_esc(code)}/">'
            f'<text class="fc-hm-label" x="0" y="{ry + 14:.1f}">'
            f"{_esc(code)} {_esc(short_name)} ({total})</text></a>"
        )
        for ci, (m_label, _key, red_pct, all_na, red_cnt) in enumerate(cells):
            cx = label_w + ci * (col_w + gap)
            if all_na:
                cls, txt = "fc-hm-na", "⊘"
                tip = f"{name} · {m_label}: 해당 없음(⊘)"
            else:
                pct_i = round(red_pct)
                bucket = 0 if pct_i == 0 else 1 if pct_i < 20 else 2 if pct_i < 35 else 3 if pct_i < 50 else 4
                cls, txt = f"fc-hm-rate-{bucket}", f"{pct_i}%"
                tip = f"{name} · {m_label}: 위험 {red_pct:.1f}% ({red_cnt}/{total}곳)"
            parts.append(
                f'<g class="fc-hm-cell {cls}"><title>{_esc(tip)}</title>'
                f'<rect class="fc-hm-rect" x="{cx:.1f}" y="{ry + 2:.1f}" '
                f'width="{col_w:.0f}" height="{row_h - 4:.0f}" rx="3"/>'
                f'<text class="fc-hm-glyph" x="{cx + col_w / 2:.1f}" y="{ry + 14:.1f}" '
                f'text-anchor="middle">{txt}</text></g>'
            )
    parts.append("</svg>")
    return Markup("".join(parts))
