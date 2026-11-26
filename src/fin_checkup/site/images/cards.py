"""카드뉴스 — 회사 한 곳의 신호등을 인스타그램 넘김 카드(1080×1350) 7장 이하로.

운영자가 손으로 올리는 용도다. 여기서는 그림만 만들고 어디에도 올리지 않는다 (`docs/card-news.md`).

① 표지 ② 신호등 요약(개수) ③ 수익성 ④ 안정성 ⑤ 현금흐름·이익의 질 ⑥ 성장성·효율성
⑦ 읽는 법·출처·사이트 주소. 지표가 하나도 없는 묶음의 장은 빠진다.
요약 장에서 센 지표는 **모두** 어느 장엔가 있다 — 세어 놓고 못 찾는 지표가 없게 (블록을 빼지 않는다).

넘지 않는 선 (REGULATORY.md)
- 측정값과 계산식·경계값만. 쉬운 말 설명은 `explain.why_color` 그대로 — 새 판정 문장을 만들지 않는다.
- **모든 장**의 바닥에 출처(DART·보고서)와 면책 문구(`DISCLAIMER`) 전문.
- 그린 글자 전체를 금지 표현 검사(`explain.banned`)에 통과시키고, 걸리면 그림을 내지 않는다
  (`BannedPhraseError`).
- 업종 비교의 '상위 N%' 는 싣지 않는다 — 퍼 가는 그림에서 순위로 읽히기 쉽다. 작년 값만 곁들인다.
- 글꼴에 컬러 이모지가 없어 🟢 를 글자로 찍으면 두부(□)다. 신호는 도형(`Canvas.signal_mark`)+글자로 그리고
  설명 문장의 신호 기호는 걷어낸다(`plain`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain import banned, get_explainer, why_color
from fin_checkup.explain._korean import ro
from fin_checkup.explain.why import last_year_text
from fin_checkup.metrics.engine import MISSING_ACCOUNT_NOTE, Category, Metric
from fin_checkup.metrics.peers import SelfDelta
from fin_checkup.metrics.sector import Sector
from fin_checkup.metrics.signals import Signal
from fin_checkup.presentation import band_text, metrics_by_category
from fin_checkup.service import Checkup
from fin_checkup.site.charts import badge_counts
from fin_checkup.site.images.fonts import FontSet
from fin_checkup.site.images.layout import Box, Canvas, line_height, text_width, wrap_text
from fin_checkup.site.view import CompanyView, company_view
from fin_checkup.theme import BORDER, INK, INK_MUTED, SURFACE, SURFACE_SUNK

WIDTH, HEIGHT = 1080, 1350
MARGIN = 72
CONTENT = WIDTH - MARGIN * 2
DEFAULT_SITE_URL = "https://younghwan91.github.io/fin-checkup/"
SOURCE_NAME = "금융감독원 전자공시시스템(DART)"
TAGLINE = "재무제표 신호등으로 읽기"

#: (묶음, 장 제목, 한 줄 부제). 순서가 장 순서다. 모든 묶음이 어느 장엔가 들어가야 한다 — 요약 장이 17개를
#: 다 세므로, 빠진 묶음이 있으면 '주의 3개' 중 하나를 독자가 찾지 못한다 (2026-11-21 KB금융 카드).
SECTIONS: tuple[tuple[tuple[Category, ...], str, str], ...] = (
    ((Category.PROFITABILITY,), "수익성", "매출과 자본에서 이익이 얼마나 남았나"),
    ((Category.STABILITY,), "안정성", "빚과 이자를 감당할 여력"),
    ((Category.EARNINGS_QUALITY,), "현금흐름·이익의 질", "이익이 실제 현금으로 들어왔나"),
    ((Category.GROWTH, Category.EFFICIENCY), "성장성·효율성", "작년보다 얼마나 늘었나, 자산을 얼마나 돌렸나"),
)

#: 신호마다 그 뜻 한 줄 — 판정 규칙을 옮긴 말이지 좋다·나쁘다가 아니다.
SIGNAL_MEANINGS: dict[Signal, str] = {
    Signal.GREEN: "값이 경계값의 '정상' 구간에 든 지표",
    Signal.YELLOW: "값이 경계값의 '주의' 구간에 든 지표",
    Signal.RED: "값이 경계값의 '위험' 구간에 든 지표",
    Signal.NEUTRAL: "업종마다 차이가 커서 경계값 없이 값만 보여 주는 지표",
    Signal.NOT_APPLICABLE: "이 업종에는 뜻이 없어 판정하지 않는 지표 (대신 볼 것을 함께 적어요)",
    Signal.UNKNOWN: "공시에서 계정을 찾지 못한 지표 (0으로 채우지 않아요)",
}

HOW_TO_READ: tuple[str, ...] = (
    "색은 지표 값이 경계값의 어느 구간에 드는지를 부르는 이름이에요. 장마다 계산식과 경계값을 함께 적었어요.",
    "경계값은 국내 상장사 실제 분포에 맞춘 것이고, 업종에 따라 다른 기준을 쓰는 지표가 있어요.",
    "‘해당 없음’은 그 업종에 맞지 않는 지표, ‘데이터 없음’은 공시에서 계정을 찾지 못한 지표예요.",
    "한 사업연도의 숫자만 담았어요. 5개년 추이와 같은 업종 분포는 사이트에서 볼 수 있어요.",
    "점수나 등급을 매기지 않아요. 신호 개수는 개수일 뿐이에요.",
)

_SIGNAL_SYMBOLS = tuple(s.emoji for s in Signal)


class BannedPhraseError(ValueError):
    """카드 글자에 금지 표현이 들어갔다 — 그림을 내지 않는다."""


def plain(text: str) -> str:
    """신호 기호(🟢🟡🔴⚪⊘⚫)를 걷어낸다. 바로 뒤 글자(정상·주의 …)가 같은 뜻을 이미 말한다."""
    text = text.replace(f"{Signal.NOT_APPLICABLE.emoji}로", "‘해당 없음’으로")
    for symbol in _SIGNAL_SYMBOLS:
        text = text.replace(f"{symbol} ", "").replace(symbol, "")
    return re.sub(r" {2,}", " ", text)


# ── 그릴 값 ───────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricBlock:
    label: str
    value_text: str
    signal: Signal
    band: str
    formula: str
    #: 쉬운 말 한두 문장(작년 값 곁들임). 자리가 모자라면 `why_short` → `why_tiny` 로 바꿔 그린다 —
    #: 설명은 문장째 바꾸지, 문장 가운데서 자르지 않는다.
    why: str
    why_short: str
    #: 한 줄짜리: ⊘ 는 대신 볼 것, 작년 값이 있으면 '작년 …', 계정 못 찾은 ⚫ 는 그 이유. 없으면 빈 문자열.
    why_tiny: str = ""
    #: 자리가 아무리 모자라도 남기는 설명 — ⊘ 의 '대신 볼 것'. 나머지는 빈 문자열
    #: (신호·값·경계값·계산식은 늘 남는다).
    why_keep: str = ""


@dataclass(frozen=True)
class Section:
    title: str
    subtitle: str
    blocks: tuple[MetricBlock, ...]


@dataclass(frozen=True)
class CardDeck:
    name: str
    stock_code: str
    market: str
    year: int
    report_label: str
    statement_label: str
    sector_label: str
    sector_notice: str
    chips: tuple[tuple[Signal, int], ...]
    #: (지표 이름, 신호) — 엔진 순서. 요약 장의 '지표별 신호' 목록.
    metric_signals: tuple[tuple[str, Signal], ...]
    total: int
    sections: tuple[Section, ...]
    page_url: str

    @property
    def source_line(self) -> str:
        return f"출처: {SOURCE_NAME} · {self.year} {self.report_label} · {self.statement_label}"


def _caveat(metric: Metric, sector: Sector):
    if metric.signal is not Signal.NOT_APPLICABLE:
        return None
    return get_explainer(metric.key).caveat_for(sector)


_SENTENCE_END = re.compile(r"(?<=\.)\s+")


def first_sentence(text: str) -> str:
    """첫 문장. 숫자 속 마침표(0.73%)는 뒤에 띄어쓰기가 없어 끊지 않는다."""
    return _SENTENCE_END.split(text, maxsplit=1)[0]


def _short_why(metric: Metric, sector: Sector, why: str) -> str:
    """자리가 모자랄 때의 설명. ⊘ 는 '대신 볼 것' 만 — 이유 문장은 줄여도 대신 볼 것은 남긴다.

    나머지는 긴 설명의 첫 문장이다 — 값·구간·신호·(작년 …) 이 다 들어 있고 덧붙인 근거 비고만 빠진다.
    ⚫ 는 '…계정을 찾지 못했어요.' 까지라 짧은 이유가 된다. 예전엔 작년 값 없이 다시 만들어서, 장 하나가
    짧은 설명으로 내려가면 ROA·ROE 의 '(작년 …)' 까지 사라졌다 (2026-11-21 KB금융 수익성 장)."""
    caveat = _caveat(metric, sector)
    if caveat is not None:
        return f"대신 볼 것: {caveat.instead}."
    return first_sentence(why)


#: 계정을 못 찾은 ⚫ 의 한 줄 이유. 계산식은 블록에 따로 적혀 있어 뺀다.
_MISSING_TINY = "공시에서 계산에 필요한 계정을 찾지 못했어요."
_MISSING_GROWTH_TINY = "작년 재무가 없거나 계산에 필요한 계정을 찾지 못했어요."


def _tiny_why(metric: Metric, sector: Sector, short: str, delta: SelfDelta | None, currency: str) -> str:
    if _caveat(metric, sector) is not None:
        return short
    if delta is not None:
        return f"작년 {last_year_text(delta, currency)}"
    if metric.signal is Signal.UNKNOWN and metric.note in ("", MISSING_ACCOUNT_NOTE):
        growth = get_explainer(metric.key).category is Category.GROWTH
        return _MISSING_GROWTH_TINY if growth else _MISSING_TINY
    return ""


def _band(metric: Metric, sector: Sector) -> str:
    if _caveat(metric, sector) is not None:
        return f"{metric.signal.label} — {sector.label}에는 판정 기준을 적용하지 않음"
    return f"{metric.signal.label} — 기준: {band_text(metric)}"


def _block(metric: Metric, view_value: str, data: Checkup) -> MetricBlock:
    sector, currency = data.result.sector, data.result.currency
    delta = data.deltas.get(metric.key)
    why = plain(why_color(metric, delta=delta, sector=sector, currency=currency))
    short = _short_why(metric, sector, why)
    return MetricBlock(
        label=metric.label,
        value_text=view_value,
        signal=metric.signal,
        band=_band(metric, sector),
        formula=metric.formula,
        why=why,
        why_short=short,
        why_tiny=_tiny_why(metric, sector, short, delta, currency),
        why_keep=short if _caveat(metric, sector) is not None else "",
    )


def card_sector_notice(sector: Sector) -> str:
    """요약 장의 업종 안내. 카드는 해요체 하나로 쓴다 — `presentation.sector_notice`(합니다체)를 그대로
    옮기면 바로 아래 신호 뜻(…적어요)과 말투가 갈린다."""
    if sector is Sector.GENERAL:
        return ""
    return (
        f"이 회사는 {ro(sector.label)} 분류돼, 업종에 맞지 않는 지표는 ‘해당 없음’으로 두고 판정하지 않아요. "
        "그 지표마다 ‘대신 볼 것’을 함께 적었어요."
    )


def card_deck(data: Checkup, site_url: str = DEFAULT_SITE_URL) -> CardDeck:
    view: CompanyView = company_view(data)
    values = {row.key: row.value_text for group in view.groups for row in group.rows}
    by_category = dict(metrics_by_category(data.result))
    sections = []
    for categories, title, subtitle in SECTIONS:
        metrics = [m for c in categories for m in by_category.get(c, [])]
        if metrics:
            blocks = tuple(_block(m, values[m.key], data) for m in metrics)
            sections.append(Section(title, subtitle, blocks))
    counts = dict(data.result.counts)
    base = site_url if site_url.endswith("/") else site_url + "/"
    return CardDeck(
        name=view.name,
        stock_code=view.stock_code,
        market=view.market,
        year=view.year,
        report_label=view.report_label,
        statement_label=view.statement_label,
        sector_label=view.sector_label,
        sector_notice=card_sector_notice(data.result.sector),
        chips=tuple(badge_counts(counts)),
        metric_signals=tuple((m.label, m.signal) for m in data.result.metrics),
        total=sum(counts.values()),
        sections=tuple(sections),
        page_url=f"{base}c/{view.stock_code}/",
    )


# ── 공통 틀 ───────────────────────────────────────────────────

FOOTER_SIZE = 22
FOOTER_LEADING = 1.45


def footer_height(canvas: Canvas, deck: CardDeck) -> int:
    font = canvas.font(FOOTER_SIZE)
    lines = len(wrap_text(deck.source_line, font, CONTENT)) + len(wrap_text(DISCLAIMER, font, CONTENT))
    return 28 + lines * line_height(FOOTER_SIZE, FOOTER_LEADING)


def _frame(canvas: Canvas, deck: CardDeck, page: int, pages: int, cover: bool = False) -> tuple[float, float]:
    """머리(도구 이름·쪽수·회사)와 바닥(출처·면책). 본문이 쓸 (위, 아래) y 를 돌려준다."""
    logo = canvas.logo(MARGIN, 54, 38)
    brand = canvas.text(logo.x1 + 12, 60, "fin-checkup", 26, bold=True, fill=INK)
    canvas.text(brand.x1 + 12, 60, "재무제표 신호등", 26, fill=INK_MUTED)
    canvas.text(WIDTH - MARGIN, 60, f"{page} / {pages}", 26, fill=INK_MUTED, anchor="ra")
    top = 120.0
    if not cover:
        canvas.fit_text(
            MARGIN, 112, f"{deck.name} · {deck.year} 사업연도", (28, 26, 24), CONTENT, fill=INK_MUTED
        )
        top = 170.0

    bottom = HEIGHT - 56 - footer_height(canvas, deck)
    canvas.line(MARGIN, bottom, WIDTH - MARGIN, bottom, fill=BORDER, width=2)
    y = canvas.paragraph(
        MARGIN, bottom + 24, deck.source_line, FOOTER_SIZE, CONTENT, fill=INK_MUTED, leading=FOOTER_LEADING
    )
    canvas.paragraph(MARGIN, y, DISCLAIMER, FOOTER_SIZE, CONTENT, fill=INK_MUTED, leading=FOOTER_LEADING)
    return top, bottom - 28


def _title(canvas: Canvas, y: float, title: str, subtitle: str) -> float:
    canvas.fit_text(MARGIN, y, title, (56, 50, 44), CONTENT, bold=True, fill=INK)
    canvas.fit_text(MARGIN, y + 80, subtitle, (30, 28, 26), CONTENT, fill=INK_MUTED)
    return y + 140


# ── 장마다 ───────────────────────────────────────────────────


def draw_cover(canvas: Canvas, deck: CardDeck, page: int, pages: int) -> None:
    top, bottom = _frame(canvas, deck, page, pages, cover=True)
    y = top + 120
    canvas.text(MARGIN, y, TAGLINE, 36, bold=True, fill=INK_MUTED)
    canvas.fit_text(MARGIN, y + 64, deck.name, (104, 92, 80, 68, 56), CONTENT, bold=True, fill=INK)
    meta = " · ".join(p for p in (deck.stock_code, deck.market) if p)
    canvas.text(MARGIN, y + 210, meta, 34, fill=INK_MUTED)
    canvas.fit_text(
        MARGIN,
        y + 264,
        f"{deck.year} 사업연도 · {deck.report_label} · {deck.statement_label}",
        (32, 28, 26),
        CONTENT,
        fill=INK_MUTED,
    )
    if deck.sector_label and deck.sector_label != Sector.GENERAL.label:
        canvas.text(MARGIN, y + 314, f"업종 구분: {deck.sector_label}", 30, fill=INK_MUTED)

    # 차례 — 몇 장에 무엇이 있는지
    box = Box(MARGIN, y + 400, WIDTH - MARGIN, min(bottom, y + 400 + 110 + 56 * (pages - 1)))
    canvas.rect(box, fill=SURFACE, outline=BORDER, radius=20, width=2)
    canvas.text(box.x0 + 36, box.y0 + 30, "이 카드에 담은 것", 26, bold=True, fill=INK_MUTED)
    names = ["신호등 요약", *(s.title for s in deck.sections), "읽는 법 · 출처"]
    for i, name in enumerate(names, start=2):
        ty = box.y0 + 30 + 56 * (i - 1)
        canvas.text(box.x0 + 36, ty, f"{i}", 30, bold=True, fill=INK_MUTED)
        canvas.text(box.x0 + 84, ty, name, 30, fill=INK)


def draw_summary(canvas: Canvas, deck: CardDeck, page: int, pages: int) -> None:
    top, bottom = _frame(canvas, deck, page, pages)
    y = _title(canvas, top, "신호등 요약", f"지표 {deck.total}개를 신호별로 센 개수 — 점수가 아니에요")
    row_h = 104 if len(deck.chips) <= 4 else 94
    for signal, count in deck.chips:
        canvas.signal_mark(MARGIN + 22, y + 28, 20, signal)
        canvas.text(MARGIN + 62, y + 28, signal.label, 38, bold=True, fill=INK, anchor="lm")
        canvas.text(WIDTH - MARGIN, y + 28, f"{count}개", 48, bold=True, fill=INK, anchor="rm")
        canvas.fit_text(
            MARGIN + 62, y + 58, SIGNAL_MEANINGS[signal], (22, 20), CONTENT - 62 - 130, fill=INK_MUTED
        )
        y += row_h
    if deck.sector_notice:
        y = canvas.paragraph(MARGIN, y + 12, deck.sector_notice, 24, CONTENT, fill=INK, max_lines=3) + 8

    # 지표별 신호 — 엔진 순서 그대로(나쁜 순·좋은 순으로 줄 세우지 않는다). 자리가 없으면 싣지 않는다.
    columns, row_step = 3, 36
    rows = -(-len(deck.metric_signals) // columns)
    if deck.metric_signals and y + 22 + 48 + rows * row_step <= bottom:
        y += 22
        canvas.line(MARGIN, y, WIDTH - MARGIN, y, fill=BORDER, width=2)
        canvas.text(MARGIN, y + 18, "지표별 신호", 24, bold=True, fill=INK_MUTED)
        col_w = (CONTENT - 24 * (columns - 1)) / columns
        for i, (label, signal) in enumerate(deck.metric_signals):
            col, row = divmod(i, rows)
            x = MARGIN + col * (col_w + 24)
            cy = y + 70 + row * row_step
            canvas.signal_mark(x + 9, cy, 8, signal)
            canvas.fit_text(x + 26, cy, label, (22, 20, 18), col_w - 26, fill=INK, anchor="lm")


@dataclass(frozen=True)
class Scale:
    """블록 글자 크기(머리·값·작은 글자·설명)와 블록 사이 간격의 절반, 계산식과 설명 사이 간격."""

    head: int
    value: int
    small: int
    body: int
    gap: int
    why_gap: int = 10


#: 큰 것부터. 마지막이 최소 — 설명은 바닥글(22) 보다 작게 줄이지 않는다. 최소 크기는 지표 6개 장
#: (성장성·효율성)에 은행의 ⊘ '대신 볼 것' 세 줄까지 들어가게 맞췄다.
_SCALES: tuple[Scale, ...] = (
    Scale(34, 40, 22, 27, 22),
    Scale(32, 38, 21, 25, 20),
    Scale(30, 36, 20, 24, 18),
    Scale(28, 34, 20, 23, 12),
    Scale(26, 32, 19, 22, 10),
)
#: 간격만 좁힌 최소 크기 — 짧은 설명(한 줄)만 쓴다. 지표 6개 장에 한 줄 설명을 모두 싣는 자리다. 없을 땐
#: 6개 장이 늘 설명 없이 나갔다(2026-11-21 두 회사 모두, 🟡 영업이익 증가율에 이유 문장이 없었다).
#: 긴 설명에는 쓰지 않는다 — 빽빽한 판에 세 줄짜리 ⊘ 설명을 싣느니 큰 글자에 짧은 설명이 낫다.
_COMPACT = Scale(26, 32, 19, 22, 6, why_gap=4)
_WHY_LEADING = 1.42
_SMALL_LEADING = 1.4


def _why_lines(canvas: Canvas, why: str, scale: Scale) -> int:
    return len(wrap_text(why, canvas.font(scale.body), CONTENT - 40)) if why else 0


def _block_height(canvas: Canvas, block: MetricBlock, why: str, scale: Scale) -> float:
    """`_draw_block` 이 실제로 쓰는 높이와 똑같이 잰다 — 재는 식과 그리는 식이 어긋나면 넘치거나 남는다."""
    small_font = canvas.font(scale.small)
    lines = len(wrap_text(block.band, small_font, CONTENT - 40))
    lines += len(wrap_text(f"계산식: {block.formula}", small_font, CONTENT - 40))
    height = scale.head * 1.5 + lines * line_height(scale.small, _SMALL_LEADING)
    why_lines = _why_lines(canvas, why, scale)
    if why_lines:
        height += scale.why_gap + why_lines * line_height(scale.body, _WHY_LEADING)
    return height


@dataclass(frozen=True)
class BlockPlan:
    scale: Scale
    whys: list[str]
    #: 앞에서부터 그릴 블록 수. 다 못 그리면 나머지는 '사이트에서' 한 줄로 — 최소 크기에 설명까지 빼고도
    #: 안 들어갈 때만(실제 공시 문장으로는 일어나지 않는다; 테스트가 실데이터 모양으로 지킨다).
    shown: int


_MORE_NOTE_H = 56


def _plan_blocks(canvas: Canvas, blocks: tuple[MetricBlock, ...], room: float) -> BlockPlan:
    """자리가 모자라면 이 순서로 물러선다. 설명은 문장째 바꾸고 문장 가운데서 자르지 않는다.

    1. 설명 전체 → 짧은 설명(첫 문장), 각각 큰 글자부터 최소 크기까지 (줄이기가 바꾸기보다 먼저).
    2. 간격을 좁힌 최소 크기(`_COMPACT`)에서 짧은 설명, 그래도 넘치면 줄이 가장 많은 설명부터
       한 줄짜리(`why_tiny`)로 바꾼다.
    3. 설명을 빼고(⊘ 의 대신 볼 것만 남긴다) 큰 글자부터.
    4. 그래도 넘치면 뒤에서부터 블록을 뺀다.

    예전 2 는 줄 수를 잘라 끝에 '… 회사 페이지에서 계속' 을 붙였다 — 퍼 가는 그림에 '…않았을 수…' 같은
    끊긴 문장이 남았다 (2026-11-21 KB금융 수익성 장).
    """
    n = len(blocks)

    def total(whys, scale: Scale, k: int = n) -> float:
        heights = sum(_block_height(canvas, b, w, scale) for b, w in zip(blocks[:k], whys[:k], strict=True))
        return heights + scale.gap * 2 * max(0, k - 1)

    for pick in (lambda b: b.why, lambda b: b.why_short):
        whys = [pick(b) for b in blocks]
        for scale in _SCALES:
            if total(whys, scale) <= room:
                return BlockPlan(scale, whys, n)

    scale = _COMPACT
    whys = [b.why_short for b in blocks]
    while total(whys, scale) > room:
        swappable = [i for i in range(n) if whys[i] != blocks[i].why_tiny]
        if not swappable:
            break
        # 줄을 가장 많이 버는 것부터, 같으면 한 줄짜리라도 남는 것(⊘ 대신 볼 것·작년 값·⚫ 이유)부터.
        saved = {
            i: _why_lines(canvas, whys[i], scale) - _why_lines(canvas, blocks[i].why_tiny, scale)
            for i in swappable
        }
        i = max(swappable, key=lambda i: (saved[i], bool(blocks[i].why_tiny), i))
        whys[i] = blocks[i].why_tiny
    else:
        return BlockPlan(scale, whys, n)

    whys = [b.why_keep for b in blocks]
    for scale in _SCALES:
        if total(whys, scale) <= room:
            return BlockPlan(scale, whys, n)
    scale = _COMPACT
    for shown in range(n - 1, 0, -1):
        if total(whys, scale, k=shown) <= room - _MORE_NOTE_H:
            return BlockPlan(scale, whys, shown)
    return BlockPlan(scale, whys, 1)


def _draw_block(canvas: Canvas, y: float, block: MetricBlock, why: str, scale: Scale) -> float:
    head, value = scale.head, scale.value
    mid = y + head * 0.75
    canvas.signal_mark(MARGIN + 14, mid, 14, block.signal)
    value_w = min(text_width(block.value_text, canvas.font(value, bold=True)), CONTENT * 0.45)
    canvas.fit_text(
        MARGIN + 40,
        mid,
        block.label,
        (head, head - 4, head - 8),
        CONTENT - 40 - value_w - 24,
        bold=True,
        fill=INK,
        anchor="lm",
    )
    canvas.fit_text(
        WIDTH - MARGIN,
        mid,
        block.value_text,
        (value, value - 6, value - 12),
        CONTENT * 0.45,
        bold=True,
        fill=INK,
        anchor="rm",
    )
    y += head * 1.5
    x = MARGIN + 40
    small = scale.small
    y = canvas.paragraph(x, y, block.band, small, CONTENT - 40, fill=INK_MUTED, leading=_SMALL_LEADING)
    y = canvas.paragraph(
        x, y, f"계산식: {block.formula}", small, CONTENT - 40, fill=INK_MUTED, leading=_SMALL_LEADING
    )
    if not why:
        return y
    return canvas.paragraph(
        x,
        y + scale.why_gap,
        why,
        scale.body,
        CONTENT - 40,
        fill=INK,
        leading=_WHY_LEADING,
    )


def draw_section(canvas: Canvas, deck: CardDeck, section: Section, page: int, pages: int) -> None:
    top, bottom = _frame(canvas, deck, page, pages)
    y = _title(canvas, top, section.title, section.subtitle)
    plan = _plan_blocks(canvas, section.blocks, bottom - y)
    gap = plan.scale.gap
    for i in range(plan.shown):
        if i:
            canvas.line(MARGIN, y + gap, WIDTH - MARGIN, y + gap, fill=BORDER, width=1)
            y += gap * 2
        y = _draw_block(canvas, y, section.blocks[i], plan.whys[i], plan.scale)
    rest = len(section.blocks) - plan.shown
    if rest:
        canvas.text(MARGIN, y + 20, f"나머지 지표 {rest}개는 사이트에서 볼 수 있어요.", 24, fill=INK_MUTED)


def draw_how_to_read(canvas: Canvas, deck: CardDeck, page: int, pages: int) -> None:
    top, bottom = _frame(canvas, deck, page, pages)
    y = _title(canvas, top, "읽는 법 · 출처", "이 카드가 말하는 것과 말하지 않는 것")
    for item in HOW_TO_READ:
        canvas.text(MARGIN, y, "·", 28, bold=True, fill=INK_MUTED)
        y = canvas.paragraph(MARGIN + 28, y, item, 27, CONTENT - 28, fill=INK, leading=1.42) + 14
    y += 18
    canvas.text(MARGIN, y, "출처", 28, bold=True, fill=INK)
    y = canvas.paragraph(
        MARGIN,
        y + 46,
        f"{SOURCE_NAME} dart.fss.or.kr — {deck.name}의 {deck.year} "
        f"{deck.report_label}({deck.statement_label}) 재무 수치로 계산했어요.",
        26,
        CONTENT,
        fill=INK,
        leading=1.42,
    )
    y += 30
    box = Box(MARGIN, y, WIDTH - MARGIN, min(bottom, y + 130))
    canvas.rect(box, fill=SURFACE, outline=BORDER, radius=18, width=2)
    canvas.text(box.x0 + 32, box.y0 + 24, "계산식·경계값·5개년 추이 전체", 24, fill=INK_MUTED)
    canvas.fit_text(
        box.x0 + 32, box.y0 + 66, deck.page_url, (30, 26, 22, 20), box.width - 64, bold=True, fill=INK
    )


# ── 묶어서 ───────────────────────────────────────────────────

Drawer = Callable[[Canvas, int, int], None]


def deck_pages(deck: CardDeck) -> list[tuple[str, Drawer]]:
    """(파일 이름 꼬리, 그리기). 파일은 `<코드>-<번호>-<꼬리>.png`."""
    pages: list[tuple[str, Drawer]] = [
        ("cover", lambda c, p, n: draw_cover(c, deck, p, n)),
        ("summary", lambda c, p, n: draw_summary(c, deck, p, n)),
    ]
    for i, section in enumerate(deck.sections):
        pages.append((f"section{i + 1}", lambda c, p, n, s=section: draw_section(c, deck, s, p, n)))
    pages.append(("read", lambda c, p, n: draw_how_to_read(c, deck, p, n)))
    return pages


def draw_deck(deck: CardDeck, fonts: FontSet, scale: int = 2) -> list[tuple[str, Canvas]]:
    """장마다 (파일 이름, 다 그린 판). 금지 표현이 있으면 `BannedPhraseError`."""
    pages = deck_pages(deck)
    drawn = []
    for number, (tail, draw) in enumerate(pages, start=1):
        canvas = Canvas(WIDTH, HEIGHT, fonts, background=SURFACE_SUNK, scale=scale)
        draw(canvas, number, len(pages))
        found = banned.find_outside_allowed(canvas.all_text(), [deck.name])
        if found:
            raise BannedPhraseError(f"{number}번째 카드에 금지 표현 {found} — 그림을 내지 않는다.")
        drawn.append((f"{deck.stock_code}-{number}-{tail}.png", canvas))
    return drawn
