"""PNG 배치 도구 — 글꼴 지표로 재서 줄을 바꾸고, 그린 글자마다 상자를 남긴다.

`Canvas` 는 논리 픽셀(1200×630 등)로 받아 `scale` 배로 그린 뒤 줄여 저장한다 — Pillow 의 원·둥근
모서리는 안티에일리어싱이 없어서, 크게 그려 줄이는 게 가장 싸게 매끈하다.
그린 글자는 전부 `Canvas.texts` 에 (문자열, 상자)로 남는다. 테스트가 이것으로 '카드 밖으로 넘친 글자'
와 '면책·출처가 실제로 찍혔는가' 를 본다 — 픽셀을 OCR 하지 않고.

색만으로 말하지 않는다: `signal_mark` 는 모양도 다르다(● ▲ ■ ▬ ⊘ ◌ — 사이트와 같다).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from fin_checkup.brand import TIGHT, draw_tight_mark
from fin_checkup.metrics.signals import Signal
from fin_checkup.site.images.fonts import FontSet
from fin_checkup.theme import BORDER, INK, SIGNAL_COLORS

ELLIPSIS = "…"


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def inside(self, other: Box) -> bool:
        return (
            self.x0 >= other.x0 - 0.5
            and self.y0 >= other.y0 - 0.5
            and self.x1 <= other.x1 + 0.5
            and self.y1 <= other.y1 + 0.5
        )


def text_width(text: str, font: ImageFont.FreeTypeFont) -> float:
    return font.getlength(text)


def _break_word(word: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    """한 단어가 한 줄보다 길면 글자 단위로 자른다 (한국어는 음절 사이에서 끊어도 읽힌다)."""
    pieces: list[str] = []
    current = ""
    for ch in word:
        if current and text_width(current + ch, font) > max_width:
            pieces.append(current)
            current = ch
        else:
            current += ch
    if current:
        pieces.append(current)
    return pieces


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    """띄어쓰기 단위로 줄을 채운다. `\\n` 은 강제 줄바꿈. 모든 줄이 max_width 안에 든다."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}" if current else word
            if text_width(candidate, font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            if text_width(word, font) <= max_width:
                current = word
            else:
                *full, current = _break_word(word, font, max_width)
                lines.extend(full)
        lines.append(current)
    return lines


def ellipsize(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    """한 줄에 안 들어가면 끝을 잘라 '…' 를 붙인다."""
    if text_width(text, font) <= max_width:
        return text
    while text and text_width(text + ELLIPSIS, font) > max_width:
        text = text[:-1]
    return text.rstrip() + ELLIPSIS


def clamp_lines(
    lines: list[str], font: ImageFont.FreeTypeFont, max_width: float, max_lines: int, more: str = ""
) -> list[str]:
    """max_lines 줄까지만. 잘렸으면 마지막 줄 끝에 '…' — `more` 를 주면 그 글자(예: '… 회사 페이지에서
    계속')를 붙여 잘렸다는 것과 나머지가 어디 있는지를 말한다."""
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    if not more:
        kept[-1] = ellipsize(kept[-1] + ELLIPSIS, font, max_width)
        return kept
    room = max_width - text_width(more, font)
    last = kept[-1]
    while last and text_width(last, font) > room:
        last = last[:-1]
    kept[-1] = last.rstrip() + more
    return kept


def line_height(size: int, leading: float) -> int:
    return round(size * leading)


class Canvas:
    """논리 좌표로 그리는 판. `png()` 가 원래 크기로 줄여 PNG 바이트를 낸다."""

    def __init__(self, width: int, height: int, fonts: FontSet, background: str, scale: int = 2) -> None:
        self.width = width
        self.height = height
        self.fonts = fonts
        self.scale = scale
        self.image = Image.new("RGB", (width * scale, height * scale), background)
        self.draw = ImageDraw.Draw(self.image)
        #: 그린 글자 전부 (문자열, 논리 좌표 상자).
        self.texts: list[tuple[str, Box]] = []
        #: 그린 도형의 상자 (넘침 검사용).
        self.shapes: list[Box] = []
        #: 브랜드 마크를 찍은 상자 (테스트가 자리·겹침을 본다).
        self.logos: list[Box] = []

    @property
    def bounds(self) -> Box:
        return Box(0, 0, self.width, self.height)

    def font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        """잴 때 쓰는 논리 크기 글꼴."""
        return self.fonts.font(size, bold)

    def _s(self, *values: float) -> list[float]:
        return [v * self.scale for v in values]

    # ── 도형 ─────────────────────────────────────────────

    def rect(
        self, box: Box, fill: str | None = None, outline: str | None = None, radius: int = 0, width: int = 1
    ) -> None:
        self.shapes.append(box)
        self.draw.rounded_rectangle(
            self._s(box.x0, box.y0, box.x1, box.y1),
            radius=radius * self.scale,
            fill=fill,
            outline=outline,
            width=width * self.scale if outline else 0,
        )

    def line(self, x0: float, y0: float, x1: float, y1: float, fill: str, width: int = 1) -> None:
        self.draw.line(self._s(x0, y0, x1, y1), fill=fill, width=width * self.scale)

    def logo(self, x: float, y: float, height: float) -> Box:
        """브랜드 마크(`fin_checkup.brand`)를 (x, y) 왼쪽 위에 높이 height 로. 폭은 높이의 48/58."""
        x0, y0, x1, y1 = TIGHT
        box = Box(x, y, x + height * (x1 - x0) / (y1 - y0), y + height)
        mark = draw_tight_mark(round(height * self.scale))
        self.image.paste(mark, (round(x * self.scale), round(y * self.scale)), mark)
        self.shapes.append(box)
        self.logos.append(box)
        return box

    def signal_mark(self, cx: float, cy: float, r: float, signal: Signal) -> Box:
        """신호 기호를 도형으로 — 글꼴에 컬러 이모지가 없어서 🟢 를 글자로 못 찍는다.

        사이트(site.css `.fc-mark`)·브랜드 마크와 같은 모양: 정상 ●, 주의 ▲, 위험 ■, 측정값 ▬,
        해당 없음 ⊘(사선 원), 데이터 없음 ◌(점선 빈 원). 색을 못 가려도 모양으로 갈린다."""
        color = SIGNAL_COLORS[signal]
        self.shapes.append(Box(cx - r, cy - r, cx + r, cy + r))
        x0, y0, x1, y1 = self._s(cx - r, cy - r, cx + r, cy + r)
        stroke = max(2, round(r * 0.22)) * self.scale
        if signal is Signal.GREEN:
            self.draw.ellipse((x0, y0, x1, y1), fill=color)
        elif signal is Signal.YELLOW:
            self.draw.polygon(self._s(cx, cy - r, cx + r, cy + r * 0.8, cx - r, cy + r * 0.8), fill=color)
        elif signal is Signal.RED:
            side = r * 0.86
            self.draw.rounded_rectangle(
                self._s(cx - side, cy - side, cx + side, cy + side), radius=r * 0.12 * self.scale, fill=color
            )
        elif signal is Signal.NEUTRAL:
            half = r * 0.42
            self.draw.rounded_rectangle(
                self._s(cx - r, cy - half, cx + r, cy + half), radius=half * 0.5 * self.scale, fill=color
            )
        elif signal is Signal.NOT_APPLICABLE:
            self.draw.ellipse((x0, y0, x1, y1), fill=None, outline=color, width=stroke)
            d = r * 0.62
            self.draw.line(self._s(cx - d, cy + d, cx + d, cy - d), fill=color, width=stroke)
        else:  # UNKNOWN — 점선 빈 원: 비어 있다는 뜻을 모양으로.
            for k in range(10):
                start = k * 36
                self.draw.arc((x0, y0, x1, y1), start, start + 20, fill=color, width=stroke)
        return Box(cx - r, cy - r, cx + r, cy + r)

    # ── 글자 ─────────────────────────────────────────────

    def text(
        self, x: float, y: float, text: str, size: int, *, bold: bool = False, fill: str, anchor: str = "la"
    ) -> Box:
        """한 줄. anchor 는 Pillow 규칙(la=왼쪽·위 ascender, ra=오른쪽, lm=왼쪽·가운데 …)."""
        big = self.fonts.font(size * self.scale, bold)
        x0, y0, x1, y1 = self.draw.textbbox((x * self.scale, y * self.scale), text, font=big, anchor=anchor)
        self.draw.text((x * self.scale, y * self.scale), text, font=big, fill=fill, anchor=anchor)
        box = Box(x0 / self.scale, y0 / self.scale, x1 / self.scale, y1 / self.scale)
        self.texts.append((text, box))
        return box

    def fit_text(
        self,
        x: float,
        y: float,
        text: str,
        sizes: tuple[int, ...],
        max_width: float,
        *,
        bold: bool = False,
        fill: str,
        anchor: str = "la",
    ) -> Box:
        """sizes 를 큰 것부터 대 보고 들어가는 첫 크기로. 가장 작은 크기로도 넘치면 끝을 '…' 로 줄인다."""
        for size in sizes:
            if text_width(text, self.font(size, bold)) <= max_width:
                return self.text(x, y, text, size, bold=bold, fill=fill, anchor=anchor)
        size = sizes[-1]
        return self.text(
            x, y, ellipsize(text, self.font(size, bold), max_width), size, bold=bold, fill=fill, anchor=anchor
        )

    def paragraph(
        self,
        x: float,
        y: float,
        text: str,
        size: int,
        max_width: float,
        *,
        bold: bool = False,
        fill: str,
        leading: float = 1.45,
        max_lines: int | None = None,
        more: str = "",
    ) -> float:
        """여러 줄. 다음에 그릴 y(마지막 줄 아래)를 돌려준다. 잘릴 때 붙일 글자는 `clamp_lines` 참고."""
        font = self.font(size, bold)
        lines = wrap_text(text, font, max_width)
        if max_lines is not None:
            lines = clamp_lines(lines, font, max_width, max_lines, more)
        step = line_height(size, leading)
        for i, line in enumerate(lines):
            if line:
                self.text(x, y + i * step, line, size, bold=bold, fill=fill)
        return y + len(lines) * step

    def all_text(self) -> str:
        return "\n".join(t for t, _ in self.texts)

    def overflow(self, margin: float = 0) -> list[tuple[str, Box]]:
        """판(안쪽 여백 margin) 밖으로 나간 글자·도형. 도형은 이름이 빈 문자열이다.

        판 전체를 덮는 배경 도형을 일부러 그릴 땐 margin=0 으로 본다."""
        inner = Box(margin, margin, self.width - margin, self.height - margin)
        items = self.texts + [("", b) for b in self.shapes]
        return [(t, b) for t, b in items if not b.inside(inner)]

    def collisions(self, tolerance: float = 1.0) -> list[tuple[str, str]]:
        """서로 겹친 글자 쌍 — 본문이 바닥글을 덮거나 줄이 포개진 경우."""
        found = []
        items = [(t, b) for t, b in self.texts if t.strip()]
        for i, (ta, a) in enumerate(items):
            for tb, b in items[i + 1 :]:
                dx = min(a.x1, b.x1) - max(a.x0, b.x0)
                dy = min(a.y1, b.y1) - max(a.y0, b.y0)
                if dx > tolerance and dy > tolerance:
                    found.append((ta, tb))
        return found

    # ── 신호 개수 알약 ───────────────────────────────────

    #: (글자, 숫자, 안쪽 여백, 기호 반지름) — 큰 것부터 대 보고 한 줄에 드는 첫 크기.
    CHIP_SIZES: tuple[tuple[int, int, int, int], ...] = (
        (26, 30, 20, 11),
        (24, 28, 16, 10),
        (22, 26, 14, 9),
        (20, 22, 10, 8),
    )

    def _chip_widths(self, chips, sizes: tuple[int, int, int, int]) -> list[float]:
        label_size, count_size, pad, r = sizes
        return [
            pad
            + r * 2
            + 10
            + text_width(s.label, self.font(label_size))
            + 8
            + text_width(str(n), self.font(count_size, bold=True))
            + pad
            for s, n in chips
        ]

    def signal_chips(
        self, x: float, y: float, chips, max_width: float, height: int = 60, gap: int = 12
    ) -> float:
        """[기호] 정상 9 알약을 한 줄로. 넘치면 글자를 줄이고, 가장 작게도 넘치면 두 줄로. 아래 y 를 낸다."""
        chips = list(chips)
        for sizes in self.CHIP_SIZES:
            widths = self._chip_widths(chips, sizes)
            if sum(widths) + gap * (len(chips) - 1) <= max_width:
                break
        label_size, count_size, pad, r = sizes
        cx, cy0 = x, y
        for (signal, count), width in zip(chips, widths, strict=True):
            if cx > x and cx + width > x + max_width:
                cx, cy0 = x, cy0 + height + gap
            self.rect(
                Box(cx, cy0, cx + width, cy0 + height),
                fill="#ffffff",
                outline=BORDER,
                radius=height // 2,
                width=2,
            )
            mid = cy0 + height / 2
            self.signal_mark(cx + pad + r, mid, r, signal)
            tx = cx + pad + r * 2 + 10
            self.text(tx, mid, signal.label, label_size, fill=INK, anchor="lm")
            count_x = tx + text_width(signal.label, self.font(label_size)) + 8
            self.text(count_x, mid, str(count), count_size, bold=True, fill=INK, anchor="lm")
            cx += width + gap
        return cy0 + height

    def png(self) -> bytes:
        """원래 크기로 줄이고 256색 팔레트로 저장한다 — 면 색이 몇 가지뿐이라 눈으로는 같고 파일은 ⅕.
        회사 2,800곳 × 140KB(RGB) 면 Pages 용량을 금방 채운다."""
        image = self.image
        if self.scale != 1:
            image = image.resize((self.width, self.height), Image.Resampling.LANCZOS)
        image = image.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
        buf = io.BytesIO()
        image.save(buf, format="PNG", compress_level=9)
        return buf.getvalue()


def paragraph_height(
    text: str,
    font: ImageFont.FreeTypeFont,
    size: int,
    max_width: float,
    leading: float = 1.45,
    max_lines: int | None = None,
) -> int:
    lines = wrap_text(text, font, max_width)
    if max_lines is not None:
        lines = lines[:max_lines]
    return len(lines) * line_height(size, leading)
