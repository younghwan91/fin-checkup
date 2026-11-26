"""시장 전체 신호 비율 그림 — `/market/`·`/m/<지표>/` 의 공유 카드(1200×630)와 카드뉴스(1080×1350, 7장).

숫자는 `/market/` 페이지와 **같은 계산**이다: 모집단은 이 빌드에 페이지가 생기는 회사(`site.companies`),
비율은 `site.groups.distributions` — 회사마다 자기 업종 기준으로 판정된 신호를 센 것. 여기서 다시 세지 않는다.

넘지 않는 선 (REGULATORY.md · docs/card-news.md)
- 회사 이름·목록·순위를 싣지 않는다. 시장 전체의 개수와 비율뿐이다.
- 무엇을 실을지는 정해진 규칙으로 고른다 — 공유 카드는 엔진 순서 첫 지표(`/market/`)나 그 페이지의 지표,
  카드뉴스는 표지 → 엔진의 지표 묶음마다 한 장(묶음 안 지표 전부) → 읽는 법.
  숫자가 눈에 띄는 지표를 고르지 않는다.
- 신호 여섯 가지를 늘 다 적는다 — ⚫ 데이터 없음·⊘ 해당 없음을 0 으로 접거나 빼지 않는다.
- 일반 기준 경계값과 계산식을 함께 적고, 자기 업종 기준을 쓰는 업종(금융·부동산 등)을 밝힌다.
- 출처(DART)와 면책 문구. 카드뉴스는 모든 장에 면책 전문, 공유 카드는 짧은 문구(회사 공유 카드와 같다).
- 그린 글자는 금지 표현 검사(`explain.banned`)를 거친다 — 걸리면 그림을 내지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from fin_checkup.explain import banned
from fin_checkup.explain._korean import topic
from fin_checkup.metrics.engine import NOT_APPLICABLE_BY_SECTOR, Category, MetricDef
from fin_checkup.metrics.sector import Sector
from fin_checkup.metrics.signals import Signal
from fin_checkup.presentation import band_phrase
from fin_checkup.site.groups import SIGNAL_ORDER, Distribution, distributions
from fin_checkup.site.images import cards
from fin_checkup.site.images.cards import (
    CONTENT,
    DEFAULT_SITE_URL,
    MARGIN,
    SOURCE_NAME,
    BannedPhraseError,
    _frame,
    _title,
)
from fin_checkup.site.images.fonts import FontSet
from fin_checkup.site.images.layout import Box, Canvas, text_width, wrap_text
from fin_checkup.theme import BORDER, INK, INK_MUTED, SIGNAL_COLORS, SURFACE, SURFACE_SUNK

#: `/market/` 공유 카드 · 지표 페이지 공유 카드의 출력 경로.
MARKET_OG_PATH = "og/market.png"
OG_WIDTH, OG_HEIGHT = 1200, 630
OG_MARGIN = 64
#: 공유 카드의 짧은 면책 — 회사 공유 카드(`og.FOOTER`)와 같은 말.
SHORT_DISCLAIMER = "측정값이며 투자권유가 아닙니다"


def metric_og_path(key: str) -> str:
    return f"og/m/{key}.png"


# ── 그릴 값 ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Share:
    signal: Signal
    count: int
    percent: float


@dataclass(frozen=True)
class MarketMetric:
    key: str
    label: str
    category: Category
    formula: str
    #: 값이 있어 분포에 든 회사 수 (⚫·⊘·비원화 금액을 뺀 수).
    measured: int
    #: 모집단 회사 수 — 비율의 분모. 신호 여섯 개의 개수를 더하면 이 수다.
    total: int
    #: 여섯 신호 전부, `SIGNAL_ORDER` 순서. 0 곳도 싣는다.
    shares: tuple[Share, ...]
    #: 일반 기준 경계값 한 줄.
    standard: str
    #: 일반과 다른 기준을 쓰는 업종 — 빈 문자열이면 모든 업종이 같은 기준.
    sector_note: str


@dataclass(frozen=True)
class MarketDeck:
    year: int
    total: int
    #: 실제로 적용된 판정 기준 이름 (일반·금융업 …), Sector 정의 순서.
    sector_labels: tuple[str, ...]
    metrics: tuple[MarketMetric, ...]
    page_url: str

    #: 카드뉴스 머리의 '누구의 숫자인가' 자리 (`cards._frame`) — 회사 이름 대신.
    name: str = "시장 전체"

    @property
    def source_line(self) -> str:
        return f"출처: {SOURCE_NAME} · {self.year} 사업보고서 · 상장사 {self.total:,}곳"

    def by_category(self) -> list[tuple[Category, tuple[MarketMetric, ...]]]:
        """엔진의 지표 묶음 순서, 묶음 안은 엔진 정의 순서."""
        out = []
        for category in Category:
            metrics = tuple(m for m in self.metrics if m.category is category)
            if metrics:
                out.append((category, metrics))
        return out


def standard_text(spec: MetricDef) -> str:
    band = spec.band_for(Sector.GENERAL)
    if band is None:
        return "없음 — 업종마다 차이가 커서 값만 측정값으로 보여 줘요"
    return band_phrase(band, spec.unit)


def sector_note(spec: MetricDef, sectors: Sequence[Sector]) -> str:
    """일반과 다른 기준을 쓰는 업종. 이 빌드에 실제로 있는 기준만 (`/market/` 의 경계값 줄과 같은 범위)."""
    others = [s for s in sectors if s is not Sector.GENERAL]
    skipped = [s.label for s in others if spec.key in NOT_APPLICABLE_BY_SECTOR.get(s, {})]
    own = [
        s.label
        for s in others
        if s.label not in skipped and spec.sector_bands and s in spec.sector_bands
    ]
    parts = []
    if own:
        parts.append(f"{topic('·'.join(own))} 자기 업종 경계값으로 판정")
    if skipped:
        parts.append(f"{'·'.join(skipped)}에는 판정하지 않음(해당 없음)")
    return " · ".join(parts)


def market_metric(dist: Distribution, sectors: Sequence[Sector]) -> MarketMetric:
    by_signal = {s.signal: s for s in dist.signals}
    return MarketMetric(
        key=dist.spec.key,
        label=dist.spec.label,
        category=dist.spec.category,
        formula=dist.spec.formula,
        measured=dist.count,
        total=dist.total,
        shares=tuple(
            Share(s, by_signal[s].count, by_signal[s].percent) if s in by_signal else Share(s, 0, 0.0)
            for s in SIGNAL_ORDER
        ),
        standard=standard_text(dist.spec),
        sector_note=sector_note(dist.spec, sectors),
    )


def market_deck(entries, year: int, site_url: str = DEFAULT_SITE_URL) -> MarketDeck:
    """`entries` 는 `CompanyEntry` 목록 — `/market/` 과 같은 모집단. 분포는 `site.groups` 로만 센다."""
    from fin_checkup.site.pages.sector import member_sectors

    sectors = member_sectors(entries)
    base = site_url if site_url.endswith("/") else site_url + "/"
    return MarketDeck(
        year=year,
        total=len(entries),
        sector_labels=tuple(s.label for s in sectors),
        metrics=tuple(market_metric(d, sectors) for d in distributions(entries)),
        page_url=f"{base}market/",
    )


def share_text(share: Share) -> str:
    return f"{share.signal.label} {share.percent:.1f}% · {share.count:,}곳"


def _check(canvas: Canvas, where: str) -> None:
    found = banned.find_outside_allowed(canvas.all_text(), [])
    if found:
        raise BannedPhraseError(f"{where} 에 금지 표현 {found} — 그림을 내지 않는다.")


# ── 공통 조각 ─────────────────────────────────────────────────


def share_bar(canvas: Canvas, box: Box, metric: MarketMetric) -> None:
    """신호 개수만큼 나눈 띠. 0 곳은 폭이 없고, 아주 작은 몫도 2px 은 보이게 둔다."""
    canvas.rect(box, fill=SURFACE, outline=BORDER, radius=6, width=2)
    if not metric.total:
        return
    nonzero = [s for s in metric.shares if s.count]
    floor = 2.0
    spare = box.width - floor * len(nonzero)
    x = box.x0
    for i, share in enumerate(nonzero):
        width = floor + spare * share.count / metric.total
        x1 = box.x1 if i == len(nonzero) - 1 else x + width
        canvas.rect(Box(x, box.y0, x1, box.y1), fill=SIGNAL_COLORS[share.signal])
        x = x1


def share_legend(
    canvas: Canvas, x: float, y: float, metric: MarketMetric, width: float, size: int, step: int
) -> float:
    """여섯 신호를 세 칸 두 줄로 — 기호·이름·비율·곳 수. 아래 y 를 낸다."""
    col_w = width / 3
    for i, share in enumerate(metric.shares):
        row, col = divmod(i, 3)
        cx = x + col * col_w
        cy = y + row * step + size * 0.6
        canvas.signal_mark(cx + size * 0.4, cy, size * 0.36, share.signal)
        canvas.fit_text(
            cx + size * 1.05, cy, share_text(share), (size, size - 2, size - 4), col_w - size * 1.2,
            fill=INK, anchor="lm",
        )
    return y + 2 * step


def share_columns(canvas: Canvas, x: float, y: float, metric: MarketMetric, width: float, size: int) -> float:
    """여섯 신호를 여섯 칸 한 줄로 — 칸마다 기호·이름, 그 아래 비율(곳 수). 아래 y 를 낸다."""
    col_w = width / 6
    step = round(size * 1.35)
    for i, share in enumerate(metric.shares):
        cx = x + i * col_w
        cy = y + size * 0.6
        canvas.signal_mark(cx + size * 0.4, cy, size * 0.36, share.signal)
        canvas.fit_text(
            cx + size * 1.05, cy, share.signal.label, (size, size - 2, size - 4), col_w - size * 1.2,
            fill=INK, anchor="lm",
        )
        canvas.fit_text(
            cx, cy + step, f"{share.percent:.1f}% ({share.count:,}곳)", (size, size - 2, size - 4),
            col_w - 8, fill=INK, anchor="lm",
        )
    return y + 2 * step


# ── 공유 카드 1200×630 ────────────────────────────────────────


def draw_market_og(
    deck: MarketDeck, metric: MarketMetric, fonts: FontSet, kicker: str, site_label: str = "", scale: int = 2
) -> Canvas:
    canvas = Canvas(OG_WIDTH, OG_HEIGHT, fonts, background=SURFACE_SUNK, scale=scale)
    content = OG_WIDTH - OG_MARGIN * 2

    logo = canvas.logo(OG_MARGIN, 40, 36)
    brand = canvas.text(logo.x1 + 12, 46, "fin-checkup", 24, bold=True, fill=INK)
    canvas.text(brand.x1 + 12, 46, "재무제표 신호등", 24, fill=INK_MUTED)
    canvas.text(OG_WIDTH - OG_MARGIN, 46, f"{deck.year} 사업연도", 24, fill=INK_MUTED, anchor="ra")

    canvas.fit_text(
        OG_MARGIN, 96, f"{kicker} · 상장사 {deck.total:,}곳", (26, 24, 22), content, fill=INK_MUTED
    )
    canvas.fit_text(OG_MARGIN, 132, metric.label, (54, 48, 42), content, bold=True, fill=INK)
    canvas.fit_text(
        OG_MARGIN,
        204,
        f"값이 있는 곳 {metric.measured:,}곳 · 신호는 회사마다 자기 업종 기준으로 판정",
        (22, 20),
        content,
        fill=INK_MUTED,
    )
    share_bar(canvas, Box(OG_MARGIN, 246, OG_WIDTH - OG_MARGIN, 280), metric)
    share_legend(canvas, OG_MARGIN, 296, metric, content, 24, 38)

    lines = [f"경계값(일반): {metric.standard}"]
    if metric.sector_note:
        lines.append(f"업종 기준: {metric.sector_note}")
    lines.append(f"계산식: {metric.formula}")
    y = 386
    for line in lines:
        canvas.fit_text(OG_MARGIN, y, line, (22, 20, 18), content, fill=INK)
        y += 34

    canvas.line(OG_MARGIN, 522, OG_WIDTH - OG_MARGIN, 522, fill=BORDER, width=2)
    canvas.text(OG_MARGIN, 550, f"출처: {SOURCE_NAME}", 20, fill=INK_MUTED, anchor="lm")
    canvas.text(OG_MARGIN, 580, SHORT_DISCLAIMER, 20, fill=INK_MUTED, anchor="lm")
    if site_label:
        used = text_width(SHORT_DISCLAIMER, canvas.font(20))
        if text_width(site_label, canvas.font(20)) <= content - used - 40:
            canvas.text(OG_WIDTH - OG_MARGIN, 580, site_label, 20, fill=INK_MUTED, anchor="rm")
    _check(canvas, f"공유 카드({metric.key})")
    return canvas


def market_og_specs(deck: MarketDeck) -> list[tuple[str, MarketMetric, str]]:
    """(출력 경로, 지표, 머리말). `/market/` 은 엔진 순서 첫 지표 — 값이 눈에 띄는 지표를 고르지 않는다."""
    if not deck.metrics:
        return []
    first = f"시장 전체 분포 (지표 {len(deck.metrics)}개 중 첫째)"
    specs = [(MARKET_OG_PATH, deck.metrics[0], first)]
    specs += [(metric_og_path(m.key), m, "시장 전체 분포") for m in deck.metrics]
    return specs


def market_og_pngs(deck: MarketDeck, fonts: FontSet, site_label: str = "") -> list[tuple[str, bytes]]:
    """(출력 경로, PNG) — `market_og_specs` 순서."""
    return [
        (path, draw_market_og(deck, metric, fonts, kicker, site_label).png())
        for path, metric, kicker in market_og_specs(deck)
    ]


# ── 카드뉴스 1080×1350 ────────────────────────────────────────

SUBTITLES: dict[Category, str] = {
    Category.PROFITABILITY: "매출과 자본에서 이익이 얼마나 남았나",
    Category.STABILITY: "빚과 이자를 감당할 여력",
    Category.GROWTH: "작년보다 얼마나 늘었나",
    Category.EARNINGS_QUALITY: "이익이 실제 현금으로 들어왔나",
    Category.EFFICIENCY: "자산을 얼마나 돌렸나",
}

HOW_TO_READ: tuple[str, ...] = (
    "비율은 회사마다 자기 업종 기준으로 판정한 신호를 센 거예요. "
    "장마다 일반 기준 경계값과 계산식을 함께 적었어요.",
    "‘데이터 없음’은 공시에서 계정을 찾지 못한 회사, ‘해당 없음’은 업종에 맞지 않는 지표예요. "
    "0으로 세지 않고 따로 적었어요.",
    "‘측정값’은 업종마다 차이가 커서 경계값 없이 값만 보여 주는 지표예요.",
    "시장 전체의 개수와 비율만 담았어요. 회사 이름이나 순위는 싣지 않았어요.",
    "점수나 등급을 매기지 않아요. 비율은 비율일 뿐이에요.",
)


@dataclass(frozen=True)
class _Size:
    label: int
    bar: int
    legend: int
    small: int
    gap: int


#: 큰 것부터. 묶음 하나(지표 3~4개)가 한 장에 다 들어가는 첫 크기를 쓴다 — 지표를 빼지 않는다.
_SIZES: tuple[_Size, ...] = (
    _Size(32, 24, 22, 22, 26),
    _Size(30, 20, 21, 21, 20),
    _Size(28, 18, 20, 20, 16),
    _Size(26, 16, 19, 19, 12),
    _Size(24, 14, 18, 19, 10),
)
_SMALL_LEADING = 1.4


def _detail_lines(metric: MarketMetric) -> list[str]:
    lines = [f"경계값(일반): {metric.standard}"]
    if metric.sector_note:
        lines.append(f"업종 기준: {metric.sector_note}")
    lines.append(f"계산식: {metric.formula}")
    return lines


def _metric_height(canvas: Canvas, metric: MarketMetric, size: _Size) -> float:
    """`_draw_metric` 이 쓰는 높이와 똑같이 잰다."""
    height = size.label * 1.5 + size.bar + 12 + 2 * round(size.legend * 1.35) + 8
    font = canvas.font(size.small)
    lines = sum(len(wrap_text(t, font, CONTENT)) for t in _detail_lines(metric))
    return height + lines * round(size.small * _SMALL_LEADING)


def _draw_metric(canvas: Canvas, y: float, metric: MarketMetric, size: _Size) -> float:
    mid = y + size.label * 0.75
    count = f"값이 있는 곳 {metric.measured:,}곳"
    count_w = text_width(count, canvas.font(size.small))
    canvas.text(cards.WIDTH - MARGIN, mid, count, size.small, fill=INK_MUTED, anchor="rm")
    canvas.fit_text(
        MARGIN, mid, metric.label, (size.label, size.label - 4), CONTENT - count_w - 24,
        bold=True, fill=INK, anchor="lm",
    )
    y += size.label * 1.5
    share_bar(canvas, Box(MARGIN, y, cards.WIDTH - MARGIN, y + size.bar), metric)
    y = share_columns(canvas, MARGIN, y + size.bar + 12, metric, CONTENT, size.legend) + 8
    for line in _detail_lines(metric):
        y = canvas.paragraph(MARGIN, y, line, size.small, CONTENT, fill=INK_MUTED, leading=_SMALL_LEADING)
    return y


def draw_market_cover(canvas: Canvas, deck: MarketDeck, page: int, pages: int) -> None:
    top, bottom = _frame(canvas, deck, page, pages, cover=True)
    y = top + 120
    canvas.text(MARGIN, y, cards.TAGLINE, 36, bold=True, fill=INK_MUTED)
    canvas.fit_text(MARGIN, y + 64, "시장 전체 신호 비율", (96, 84, 72), CONTENT, bold=True, fill=INK)
    canvas.fit_text(
        MARGIN, y + 200, f"{deck.year} 사업연도 · 상장사 {deck.total:,}곳", (36, 32, 28), CONTENT,
        fill=INK_MUTED,
    )
    y = canvas.paragraph(
        MARGIN,
        y + 264,
        f"지표 {len(deck.metrics)}개마다, 회사를 자기 업종 기준({', '.join(deck.sector_labels)})으로 "
        "판정한 신호를 세어 비율로 적었어요. 회사 이름은 싣지 않아요.",
        28,
        CONTENT,
        fill=INK,
        leading=1.45,
    )
    groups = deck.by_category()
    box = Box(MARGIN, y + 40, cards.WIDTH - MARGIN, min(bottom, y + 40 + 110 + 56 * (len(groups) + 1)))
    canvas.rect(box, fill=SURFACE, outline=BORDER, radius=20, width=2)
    canvas.text(box.x0 + 36, box.y0 + 30, "이 카드에 담은 것", 26, bold=True, fill=INK_MUTED)
    names = [f"{c.value} (지표 {len(m)}개)" for c, m in groups] + ["읽는 법 · 출처"]
    for i, name in enumerate(names, start=2):
        ty = box.y0 + 30 + 56 * (i - 1)
        canvas.text(box.x0 + 36, ty, f"{i}", 30, bold=True, fill=INK_MUTED)
        canvas.text(box.x0 + 84, ty, name, 30, fill=INK)


def draw_market_category(
    canvas: Canvas,
    deck: MarketDeck,
    category: Category,
    metrics: tuple[MarketMetric, ...],
    page: int,
    pages: int,
) -> None:
    top, bottom = _frame(canvas, deck, page, pages)
    # 제목 옆에 부제 — 지표 4개 장에 자리를 남기려고 두 줄 대신 한 줄에.
    title = canvas.text(MARGIN, top, category.value, 52, bold=True, fill=INK)
    canvas.fit_text(
        title.x1 + 24, title.y1, SUBTITLES[category], (28, 26, 24, 22), cards.WIDTH - MARGIN - title.x1 - 24,
        fill=INK_MUTED, anchor="ls",
    )
    y = top + 88
    room = bottom - y
    size = _SIZES[-1]
    for candidate in _SIZES:
        heights = sum(_metric_height(canvas, m, candidate) for m in metrics)
        if heights + candidate.gap * 2 * (len(metrics) - 1) <= room:
            size = candidate
            break
    for i, metric in enumerate(metrics):
        if i:
            canvas.line(MARGIN, y + size.gap, cards.WIDTH - MARGIN, y + size.gap, fill=BORDER, width=1)
            y += size.gap * 2
        y = _draw_metric(canvas, y, metric, size)


def draw_market_read(canvas: Canvas, deck: MarketDeck, page: int, pages: int) -> None:
    top, bottom = _frame(canvas, deck, page, pages)
    y = _title(canvas, top, "읽는 법 · 출처", "이 카드가 말하는 것과 말하지 않는 것")
    for item in HOW_TO_READ:
        canvas.text(MARGIN, y, "·", 28, bold=True, fill=INK_MUTED)
        y = canvas.paragraph(MARGIN + 28, y, item, 26, CONTENT - 28, fill=INK, leading=1.42) + 12
    y += 14
    canvas.text(MARGIN, y, "출처", 28, bold=True, fill=INK)
    y = canvas.paragraph(
        MARGIN,
        y + 46,
        f"{SOURCE_NAME} dart.fss.or.kr — 상장사 {deck.total:,}곳의 {deck.year} 사업보고서 "
        "재무 수치로 계산했어요.",
        26,
        CONTENT,
        fill=INK,
        leading=1.42,
    )
    y += 26
    box = Box(MARGIN, y, cards.WIDTH - MARGIN, min(bottom, y + 130))
    canvas.rect(box, fill=SURFACE, outline=BORDER, radius=18, width=2)
    canvas.text(box.x0 + 32, box.y0 + 24, "지표마다 분포(사분위)·업종별 경계값 전체", 24, fill=INK_MUTED)
    canvas.fit_text(
        box.x0 + 32, box.y0 + 66, deck.page_url, (30, 26, 22, 20), box.width - 64, bold=True, fill=INK
    )


Drawer = Callable[[Canvas, int, int], None]


def market_deck_pages(deck: MarketDeck) -> list[tuple[str, Drawer]]:
    """(파일 이름 꼬리, 그리기). 표지 → 묶음마다 한 장(엔진 순서) → 읽는 법. 7장을 넘지 않는다."""
    pages: list[tuple[str, Drawer]] = [("cover", lambda c, p, n: draw_market_cover(c, deck, p, n))]
    for category, metrics in deck.by_category():
        pages.append(
            (
                category.name.lower().replace("_", "-"),
                lambda c, p, n, cat=category, ms=metrics: draw_market_category(c, deck, cat, ms, p, n),
            )
        )
    pages.append(("read", lambda c, p, n: draw_market_read(c, deck, p, n)))
    return pages


def draw_market_deck(deck: MarketDeck, fonts: FontSet, scale: int = 2) -> list[tuple[str, Canvas]]:
    """장마다 (파일 이름, 다 그린 판). 파일은 `market-<번호>-<꼬리>.png`."""
    pages = market_deck_pages(deck)
    drawn = []
    for number, (tail, draw) in enumerate(pages, start=1):
        canvas = Canvas(cards.WIDTH, cards.HEIGHT, fonts, background=SURFACE_SUNK, scale=scale)
        draw(canvas, number, len(pages))
        _check(canvas, f"{number}번째 카드")
        drawn.append((f"market-{number}-{tail}.png", canvas))
    return drawn
