"""공유 카드(Open Graph 이미지) `og/<종목코드>.png` — 1200×630.

링크를 붙였을 때 메신저·SNS 가 보여 주는 그림이다. 싣는 것:
회사명·종목코드·사업연도, 신호 개수(정상·주의·위험은 늘, 측정값·해당 없음·데이터 없음은 있을 때만),
대표 지표 4개와 그 신호, 바닥글 "출처: DART · 측정값이며 투자권유가 아닙니다".
**종합 점수·등급은 없다** (REGULATORY.md) — 개수와 지표별 측정값뿐이다.

`og_card(entry)` 가 그릴 값만 담은 `OgCard` 를 만들고(프로세스 사이로 넘길 수 있게 문자열뿐),
`render_og(card, fonts)` 가 PNG 바이트를 낸다. 빌드는 이 둘을 여러 프로세스로 나눠 돌린다(`render_many`).
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import partial
from multiprocessing import get_context

from fin_checkup.metrics.signals import Signal
from fin_checkup.site.charts import badge_counts
from fin_checkup.site.images.fonts import FontSet
from fin_checkup.site.images.layout import Box, Canvas, text_width
from fin_checkup.site.view import CompanyView, MetricRow, company_view
from fin_checkup.theme import BORDER, INK, INK_MUTED, SIGNAL_COLORS, SURFACE, SURFACE_SUNK

WIDTH, HEIGHT = 1200, 630
MARGIN = 64
#: 카드에 싣는 대표 지표 — 수익성 둘·안정성 하나·현금흐름 하나. 은행 등에서 ⊘ 면 ⊘ 로 그대로 싣는다.
HEADLINE_METRICS: tuple[str, ...] = ("operating_margin", "roe", "debt_ratio", "operating_cash_flow")
FOOTER = "출처: DART · 측정값이며 투자권유가 아닙니다"


def og_path(stock_code: str) -> str:
    return f"og/{stock_code}.png"


@dataclass(frozen=True)
class OgMetric:
    label: str
    value_text: str
    signal: Signal


@dataclass(frozen=True)
class OgCard:
    name: str
    stock_code: str
    year: int
    subtitle: str
    #: (신호, 개수) — `charts.badge_counts` 순서(정상→주의→위험→측정값→해당 없음→데이터 없음).
    chips: tuple[tuple[Signal, int], ...]
    total: int
    metrics: tuple[OgMetric, ...]
    #: 바닥글 오른쪽 사이트 주소(스킴 없이). 절대주소가 없는 빌드면 빈 문자열.
    site_label: str = ""


def _rows_by_key(view: CompanyView) -> dict[str, MetricRow]:
    return {row.key: row for group in view.groups for row in group.rows}


def subtitle(view: CompanyView) -> str:
    parts = [view.stock_code, view.market, f"{view.year} {view.report_label}", view.statement_label]
    return " · ".join(p for p in parts if p)


def og_card(view: CompanyView, counts: dict[Signal, int], site_label: str = "") -> OgCard:
    rows = _rows_by_key(view)
    metrics = tuple(
        OgMetric(label=rows[key].label, value_text=rows[key].value_text, signal=Signal(rows[key].signal.key))
        for key in HEADLINE_METRICS
        if key in rows
    )
    return OgCard(
        name=view.name,
        stock_code=view.stock_code,
        year=view.year,
        subtitle=subtitle(view),
        chips=tuple(badge_counts(counts)),
        total=sum(counts.values()),
        metrics=metrics,
        site_label=site_label,
    )


def og_card_for(entry, site_label: str = "") -> OgCard:
    """`CompanyEntry` → `OgCard`."""
    return og_card(company_view(entry.checkup), dict(entry.checkup.result.counts), site_label)


# ── 그리기 ────────────────────────────────────────────────────


def _metric_tile(canvas: Canvas, box: Box, metric: OgMetric) -> None:
    canvas.rect(box, fill=SURFACE, outline=BORDER, radius=12, width=2)
    # 왼쪽 띠는 신호색 — 그 아래 기호·글자가 같은 뜻을 한 번 더 말한다.
    canvas.rect(
        Box(box.x0, box.y0 + 14, box.x0 + 6, box.y1 - 14), fill=SIGNAL_COLORS[metric.signal], radius=3
    )
    inner = box.width - 24 - 20
    x = box.x0 + 24
    canvas.fit_text(x, box.y0 + 20, metric.label, (24, 22, 20), inner, fill=INK_MUTED)
    canvas.fit_text(x, box.y0 + 56, metric.value_text, (40, 36, 32, 28, 24, 22), inner, bold=True, fill=INK)
    cy = box.y1 - 32
    canvas.signal_mark(x + 10, cy, 10, metric.signal)
    canvas.fit_text(x + 28, cy, metric.signal.label, (24, 22), inner - 28, fill=INK, anchor="lm")


def draw_og(card: OgCard, fonts: FontSet, scale: int = 2) -> Canvas:
    canvas = Canvas(WIDTH, HEIGHT, fonts, background=SURFACE_SUNK, scale=scale)
    content = WIDTH - MARGIN * 2

    # 머리 — 마크·도구 이름과 기준 연도
    logo = canvas.logo(MARGIN, 42, 38)
    brand = canvas.text(logo.x1 + 12, 48, "fin-checkup", 26, bold=True, fill=INK)
    canvas.text(brand.x1 + 12, 48, "재무제표 신호등", 26, fill=INK_MUTED)
    canvas.text(WIDTH - MARGIN, 48, f"{card.year} 사업연도", 26, fill=INK_MUTED, anchor="ra")

    # 회사
    canvas.fit_text(MARGIN, 104, card.name, (68, 60, 52, 44), content, bold=True, fill=INK)
    canvas.fit_text(MARGIN, 192, card.subtitle, (28, 26, 24), content, fill=INK_MUTED)

    # 신호 개수 — 점수가 아니라 개수
    canvas.text(MARGIN, 252, f"지표 {card.total}개의 신호 개수 (점수 아님)", 22, fill=INK_MUTED)
    canvas.signal_chips(MARGIN, 286, card.chips, content, height=60)

    # 대표 지표
    if card.metrics:
        gap = 16
        n = len(card.metrics)
        tile_w = (content - gap * (n - 1)) / n
        for i, metric in enumerate(card.metrics):
            x0 = MARGIN + i * (tile_w + gap)
            _metric_tile(canvas, Box(x0, 368, x0 + tile_w, 534), metric)

    # 바닥글
    canvas.line(MARGIN, 556, WIDTH - MARGIN, 556, fill=BORDER, width=2)
    canvas.text(MARGIN, 586, FOOTER, 22, fill=INK_MUTED, anchor="lm")
    if card.site_label:
        footer_w = text_width(FOOTER, canvas.font(22))
        canvas.fit_text(
            WIDTH - MARGIN,
            586,
            card.site_label,
            (22, 20, 18),
            content - footer_w - 40,
            fill=INK_MUTED,
            anchor="rm",
        )
    return canvas


def render_og(card: OgCard, fonts: FontSet) -> bytes:
    return draw_og(card, fonts).png()


def render_many(cards: Sequence[OgCard], fonts: FontSet, workers: int = 1) -> Iterator[bytes]:
    """순서대로 PNG 를 낸다. workers>1 이면 프로세스 여러 개 (spawn — 부모의 DuckDB 연결을 물려받지 않게)."""
    workers = max(1, min(workers, os.cpu_count() or 1, len(cards) // 50 or 1))
    render = partial(render_og, fonts=fonts)
    if workers == 1:
        yield from map(render, cards)
        return
    chunk = max(1, len(cards) // (workers * 8))
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
        yield from pool.map(render, cards, chunksize=chunk)
