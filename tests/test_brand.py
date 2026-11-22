"""브랜드 마크 — 정의 한 곳(`fin_checkup.brand`)에서 나온 파일이 서로 어긋나지 않고,
색만으로 말하지 않는다."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from fin_checkup import brand
from fin_checkup.metrics.signals import Signal
from fin_checkup.theme import SIGNAL_COLORS

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "src" / "fin_checkup" / "site" / "static" / "site.css"
DOCS = ROOT / "docs" / "brand"
SVG = "{http://www.w3.org/2000/svg}"


def _luminance(hex_color: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def css_themes() -> tuple[dict[str, str], dict[str, str]]:
    text = CSS.read_text(encoding="utf-8")
    light_block, rest = text.split("@media (prefers-color-scheme: dark)", 1)
    dark_block = rest.split("\n}\n", 1)[0]

    def parse(block: str) -> dict[str, str]:
        return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))

    light = parse(light_block)
    return light, {**light, **parse(dark_block)}


def test_light_signal_colors_are_the_site_signal_colors():
    assert brand.LIGHT.green == SIGNAL_COLORS[Signal.GREEN]
    assert brand.LIGHT.yellow == SIGNAL_COLORS[Signal.YELLOW]
    assert brand.LIGHT.red == SIGNAL_COLORS[Signal.RED]


def test_dark_signal_colors_are_the_site_dark_mode_colors():
    _light, dark = css_themes()
    assert (brand.DARK.green, brand.DARK.yellow, brand.DARK.red) == (
        dark["sig-green"],
        dark["sig-yellow"],
        dark["sig-red"],
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_signal_shape_stands_out_from_the_sheet(theme):
    """그래픽 요소 대비 3:1 (WCAG 1.4.11) — 노랑은 흰 종이에선 1.8:1 이라 종이를 어둡게 했다."""
    pal = brand.PALETTES[theme]
    for color in (pal.green, pal.yellow, pal.red, pal.bar):
        assert contrast(color, pal.sheet) >= 3.0, (theme, color)


def _shapes(svg: str) -> dict[str, str]:
    """신호 모양 → 요소 태그. 색이 아니라 모양이 다르다는 것을 본다."""
    root = ET.fromstring(svg)
    pal = brand.LIGHT
    by_fill = {el.get("fill"): el.tag.removeprefix(SVG) for el in root}
    return {"green": by_fill[pal.green], "yellow": by_fill[pal.yellow], "red": by_fill[pal.red]}


def test_three_signals_are_three_different_shapes():
    assert _shapes(brand.mark_svg("light")) == {"green": "circle", "yellow": "polygon", "red": "rect"}


def test_adaptive_svg_switches_to_dark_colors():
    svg = brand.adaptive_mark_svg()
    ET.fromstring(svg)
    assert "@media (prefers-color-scheme:dark)" in svg
    dark_part = svg.split("@media", 1)[1]
    for color in (brand.DARK.sheet, brand.DARK.green, brand.DARK.yellow, brand.DARK.red):
        assert color in dark_part
    assert "<script" not in svg and "href" not in svg


def test_inline_svg_is_decorative_and_painted_by_site_css():
    svg = brand.inline_mark_svg()
    assert 'aria-hidden="true"' in svg and "fill=" not in svg and "<style" not in svg
    css = CSS.read_text(encoding="utf-8")
    for cls in re.findall(r'class="(fc-lm-[\w-]+)"', svg):
        assert f".{cls}" in css, cls


def test_raster_mark_has_transparent_corners_and_an_opaque_sheet():
    image = brand.draw_mark(32)
    assert image.size == (32, 32) and image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((31, 0))[3] == 0, "접힌 귀퉁이 밖은 투명"
    assert image.getpixel((16, 24))[3] == 255


@pytest.mark.parametrize(("name", "theme"), [("logo-mark.svg", "light"), ("logo-mark-dark.svg", "dark")])
def test_docs_brand_marks_match_the_definition(name, theme):
    """docs/brand 의 파일은 scripts/brand_assets.py 가 만든다 — 모양을 바꾸고 다시 안 뽑으면 여기서 걸린다."""
    assert (DOCS / name).read_text(encoding="utf-8") == brand.mark_svg(theme)


def test_docs_brand_has_every_file_the_guide_lists():
    guide = (DOCS / "README.md").read_text(encoding="utf-8")
    for name in re.findall(r"\(([\w./-]+\.(?:svg|png))\)", guide):
        assert (DOCS / name).is_file(), name
    for size in (512, 256, 128):
        assert (DOCS / f"logo-mark-{size}.png").is_file()


def test_wordmarks_are_outlined_text():
    """워드마크 글자는 윤곽선(path)이다 — 보는 사람 컴퓨터에 글꼴이 없어도 같은 모양."""
    for name in ("logo-wordmark.svg", "logo-wordmark-dark.svg"):
        svg = (DOCS / name).read_text(encoding="utf-8")
        root = ET.fromstring(svg)
        assert root.find(f".//{SVG}text") is None, name
        assert "<title>fin-checkup" in svg, name


def test_site_css_logo_colors_are_the_brand_palette():
    light, dark = css_themes()
    for theme, pal in ((light, brand.LIGHT), (dark, brand.DARK)):
        assert (theme["logo-sheet"], theme["logo-fold"], theme["logo-bar"]) == (pal.sheet, pal.fold, pal.bar)
