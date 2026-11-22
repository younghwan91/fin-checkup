"""브랜드 마크 — 한 곳에서 정의하고 SVG·PNG·ICO 로 그린다.

마크: 귀퉁이를 접은 **보고서 한 장** 위에 세 줄. 줄마다 신호 모양 하나와 값 막대 하나 —
"지표마다 측정값과 신호가 붙은 보고서" 라는 이 도구 그 자체다. 모양은 사이트 신호 표시(site.css `.fc-mark`)와
같다: 정상 ●, 주의 ▲, 위험 ■. 색을 못 가려도 모양으로 읽힌다(색만으로 말하지 않는다).
막대 길이는 일부러 들쭉날쭉하다 — 줄어드는 막대는 순위표처럼 읽힌다.

좌표는 64×64 격자. 사이트 파비콘(`site/pages/favicon.py`), 머리글 마크(`inline_mark_svg`), 공유 카드·카드뉴스
(`site/images`), `docs/brand/` 파일(`scripts/brand_assets.py` 가 만든다)이 모두 여기서 나온다 — 모양을 바꾸면
`uv run --with fonttools python scripts/brand_assets.py` 로 docs/brand 를 다시 뽑는다
(테스트가 어긋남을 잡는다).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw

from fin_checkup.metrics.signals import Signal
from fin_checkup.theme import INK, SIGNAL_COLORS

GRID = 64
#: 보고서 한 장 (x0, y0, x1, y1), 모서리 반지름, 접힌 귀퉁이 크기.
SHEET = (9.0, 4.0, 55.0, 60.0)
SHEET_RADIUS = 7.0
FOLD = 13.0
FOLD_RADIUS = 4.0
#: 세 줄의 가운데 y — 위에서부터 정상·주의·위험.
ROWS = (18.0, 32.0, 46.0)
MARK_X = 21.0
#: 값 막대 (시작 x, 굵기, 줄마다 길이).
BAR_X, BAR_H, BAR_LENGTHS = 31.0, 5.0, (17.0, 11.0, 14.0)
#: 사이트 머리글처럼 여백 없이 쓸 때의 viewBox.
TIGHT_VIEWBOX = "8 3 48 58"


@dataclass(frozen=True)
class Palette:
    sheet: str
    fold: str
    bar: str
    green: str
    yellow: str
    red: str
    #: 글자 — 워드마크의 영문 이름과 한국어 부제.
    ink: str
    ink_muted: str


#: 밝은 바탕용. 신호색은 사이트 밝은 모드(theme.SIGNAL_COLORS)와 같고, 세 색 모두 먹색 종이 위에서 3:1 이상
#: (dataviz validate_palette, surface #1b1b19) — 노랑(#fcb51d)은 흰 바탕에선 1.8:1 이라 종이를 먹색으로 했다.
LIGHT = Palette(
    sheet=INK,
    fold="#55554f",
    bar="#d9d9d4",
    green=SIGNAL_COLORS[Signal.GREEN],
    yellow=SIGNAL_COLORS[Signal.YELLOW],
    red=SIGNAL_COLORS[Signal.RED],
    ink=INK,
    ink_muted="#686863",
)
#: 어두운 바탕용. 신호색은 site.css 다크 모드 값(테스트가 대조).
#: 종이는 바탕(#1c1c1a)보다 밝은 회색이라 윤곽이 산다.
DARK = Palette(
    sheet="#3a3a36",
    fold="#6e6e69",
    bar="#a6a6a0",
    green="#35b400",
    yellow="#ffcb5f",
    red="#eb6e6f",
    ink="#ececea",
    ink_muted="#a6a6a0",
)
PALETTES = {"light": LIGHT, "dark": DARK}

#: 인라인·적응형 SVG 의 class 이름 → Palette 필드.
_CLASSES = {
    "fc-lm-sheet": "sheet",
    "fc-lm-fold": "fold",
    "fc-lm-bar": "bar",
    "fc-lm-g": "green",
    "fc-lm-y": "yellow",
    "fc-lm-r": "red",
}


def _fmt(v: float) -> str:
    return f"{v:g}"


def _sheet_path() -> str:
    x0, y0, x1, y1 = SHEET
    r, f = SHEET_RADIUS, FOLD
    return (
        f"M{_fmt(x0 + r)} {_fmt(y0)}H{_fmt(x1 - f)}L{_fmt(x1)} {_fmt(y0 + f)}V{_fmt(y1 - r)}"
        f"A{_fmt(r)} {_fmt(r)} 0 0 1 {_fmt(x1 - r)} {_fmt(y1)}H{_fmt(x0 + r)}"
        f"A{_fmt(r)} {_fmt(r)} 0 0 1 {_fmt(x0)} {_fmt(y1 - r)}V{_fmt(y0 + r)}"
        f"A{_fmt(r)} {_fmt(r)} 0 0 1 {_fmt(x0 + r)} {_fmt(y0)}Z"
    )


def _fold_path() -> str:
    _x0, y0, x1, _y1 = SHEET
    f, fr = FOLD, FOLD_RADIUS
    return (
        f"M{_fmt(x1 - f)} {_fmt(y0)}V{_fmt(y0 + f - fr)}"
        f"A{_fmt(fr)} {_fmt(fr)} 0 0 0 {_fmt(x1 - f + fr)} {_fmt(y0 + f)}H{_fmt(x1)}Z"
    )


def _triangle(cy: float) -> list[tuple[float, float]]:
    """주의 ▲ — 원·사각형과 눈으로 같은 무게가 되게 조금 크게."""
    half_w, h = 6.4, 11.2
    top = cy - h / 2 - 0.4
    return [(MARK_X, top), (MARK_X + half_w, top + h), (MARK_X - half_w, top + h)]


CIRCLE_R = 5.6
SQUARE = 10.6
SQUARE_RADIUS = 1.4


def _elements() -> list[tuple[str, str]]:
    """(class, SVG 요소 본문) — class 는 _CLASSES 의 키."""
    g, y, r = ROWS
    tri = " ".join(f"{_fmt(px)},{_fmt(py)}" for px, py in _triangle(y))
    s = SQUARE
    out = [
        ("fc-lm-sheet", f'path d="{_sheet_path()}"'),
        ("fc-lm-fold", f'path d="{_fold_path()}"'),
        ("fc-lm-g", f'circle cx="{_fmt(MARK_X)}" cy="{_fmt(g)}" r="{_fmt(CIRCLE_R)}"'),
        ("fc-lm-y", f'polygon points="{tri}"'),
        (
            "fc-lm-r",
            f'rect x="{_fmt(MARK_X - s / 2)}" y="{_fmt(r - s / 2)}" width="{_fmt(s)}" height="{_fmt(s)}"'
            f' rx="{_fmt(SQUARE_RADIUS)}"',
        ),
    ]
    for cy, length in zip(ROWS, BAR_LENGTHS, strict=True):
        out.append(
            (
                "fc-lm-bar",
                f'rect x="{_fmt(BAR_X)}" y="{_fmt(cy - BAR_H / 2)}" width="{_fmt(length)}"'
                f' height="{_fmt(BAR_H)}" rx="{_fmt(BAR_H / 2)}"',
            )
        )
    return out


def mark_svg(theme: str = "light") -> str:
    """한 테마로 색을 박은 마크 SVG (64×64)."""
    pal = PALETTES[theme]
    body = "".join(f'<{el} fill="{getattr(pal, _CLASSES[cls])}"/>' for cls, el in _elements())
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}">{body}</svg>\n'


def adaptive_mark_svg() -> str:
    """밝은 색이 기본이고 `prefers-color-scheme: dark` 면 어두운 색 — 브라우저 탭 파비콘용."""

    def rules(pal: Palette) -> str:
        return "".join(f".{cls}{{fill:{getattr(pal, field)}}}" for cls, field in _CLASSES.items())

    style = f"{rules(LIGHT)}@media (prefers-color-scheme:dark){{{rules(DARK)}}}"
    body = "".join(f'<{el} class="{cls}"/>' for cls, el in _elements())
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}">'
        f"<style>{style}</style>{body}</svg>\n"
    )


def inline_mark_svg(css_class: str = "fc-logo") -> str:
    """HTML 에 바로 넣는 마크 — 색은 site.css 가 class 로 칠한다(CSP 가 인라인 style 을 막는다).

    장식이라 화면 읽기에서 숨긴다."""
    body = "".join(f'<{el} class="{cls}"/>' for cls, el in _elements())
    attrs = f'class="{css_class}" viewBox="{TIGHT_VIEWBOX}" aria-hidden="true" focusable="false"'
    return f"<svg {attrs}>{body}</svg>"


# ── 래스터 (Pillow) ─────────────────────────────────────────


def _arc(
    cx: float, cy: float, r: float, start: float, end: float, steps: int = 12
) -> list[tuple[float, float]]:
    return [
        (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
        for a in (start + (end - start) * i / steps for i in range(steps + 1))
    ]


def _sheet_polygon() -> list[tuple[float, float]]:
    x0, y0, x1, y1 = SHEET
    r, f = SHEET_RADIUS, FOLD
    return [
        (x1 - f, y0),
        (x1, y0 + f),
        *_arc(x1 - r, y1 - r, r, 0, 90),
        *_arc(x0 + r, y1 - r, r, 90, 180),
        *_arc(x0 + r, y0 + r, r, 180, 270),
    ]


def _fold_polygon() -> list[tuple[float, float]]:
    _x0, y0, x1, _y1 = SHEET
    f, fr = FOLD, FOLD_RADIUS
    return [(x1 - f, y0), *_arc(x1 - f + fr, y0 + f - fr, fr, 180, 90), (x1, y0 + f)]


def draw_mark(size: int, theme: str = "light", supersample: int = 8) -> Image.Image:
    """size×size RGBA 마크. 크게 그려 줄인다 — Pillow 도형엔 안티에일리어싱이 없다."""
    pal = PALETTES[theme]
    k = size * supersample / GRID
    image = Image.new("RGBA", (size * supersample, size * supersample), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def pts(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(x * k, y * k) for x, y in points]

    draw.polygon(pts(_sheet_polygon()), fill=pal.sheet)
    draw.polygon(pts(_fold_polygon()), fill=pal.fold)
    g, y, r = ROWS
    draw.ellipse(
        [(MARK_X - CIRCLE_R) * k, (g - CIRCLE_R) * k, (MARK_X + CIRCLE_R) * k, (g + CIRCLE_R) * k],
        fill=pal.green,
    )
    draw.polygon(pts(_triangle(y)), fill=pal.yellow)
    s = SQUARE
    draw.rounded_rectangle(
        [(MARK_X - s / 2) * k, (r - s / 2) * k, (MARK_X + s / 2) * k, (r + s / 2) * k],
        radius=SQUARE_RADIUS * k,
        fill=pal.red,
    )
    for cy, length in zip(ROWS, BAR_LENGTHS, strict=True):
        draw.rounded_rectangle(
            [BAR_X * k, (cy - BAR_H / 2) * k, (BAR_X + length) * k, (cy + BAR_H / 2) * k],
            radius=BAR_H / 2 * k,
            fill=pal.bar,
        )
    return image.resize((size, size), Image.Resampling.LANCZOS)
