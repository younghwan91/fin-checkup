"""404 · 파비콘 · 접근성 기본 — 포커스, 대비(두 테마 모두 4.5:1), 랜드마크.

랜드마크·lang·건너뛰기 링크·신호의 기호/글자는 가드(`site/guard.py` `a11y`)가 모든 페이지에서
보고, 여기서는 픽스처 빌드가 그 가드를 통과하는지와 CSS 변수 대비를 숫자로 본다.
"""

from __future__ import annotations

import asyncio
import io
import re
import struct
from datetime import date

import pytest
from hub_seed import seed_site_cache

from fin_checkup.brand import adaptive_mark_svg, draw_mark
from fin_checkup.site.build import build_site
from fin_checkup.site.guard import check_site
from fin_checkup.storage import Cache

BASE = "https://example.github.io/fin-checkup/"


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("a11y")
    with Cache(tmp / "site.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp / "out"
        asyncio.run(build_site(cache, target, 2024, base_url=BASE, built_on=date(2026, 11, 21)))
    return target


def read(out, *parts) -> str:
    return out.joinpath(*parts).read_text(encoding="utf-8")


def test_build_with_absolute_base_passes_every_guard(out):
    assert check_site(out) == []


# ── 404 ───────────────────────────────────────────────────────────


def test_404_page_has_a_search_box_and_ways_back(out):
    text = read(out, "404.html")
    assert "이 주소에는 페이지가 없습니다" in text
    assert text.count('type="search"') == 1, "검색창은 본문에 하나"
    assert '<meta name="robots" content="noindex">' in text
    assert '<link rel="canonical"' not in text, "없는 주소 어디서든 서빙된다 — 정식 주소가 없다"


def test_404_links_work_at_any_depth(out):
    """Pages 는 /fin-checkup/c/999999/ 에서도 이 파일을 준다 — 상대 링크(../)는 깨진다."""
    text = read(out, "404.html")
    assert 'href="/fin-checkup/assets/site.css"' in text
    assert 'src="/fin-checkup/assets/search.js"' in text
    assert 'data-root="/fin-checkup/"' in text, "search.js 가 인덱스·회사 링크를 이 기준으로 만든다"
    assert 'href="/fin-checkup/learn/"' in text
    assert 'href="./' not in text and 'href="../' not in text


def test_404_is_not_in_the_sitemap(out):
    assert "404" not in read(out, "sitemap.xml")


# ── 파비콘 ─────────────────────────────────────────────────────────


def test_every_page_links_the_favicons_relatively(out):
    assert '<link rel="icon" href="../../favicon.ico" sizes="32x32">' in read(
        out, "c", "005930", "index.html"
    )
    assert '<link rel="icon" href="./favicon.svg" type="image/svg+xml">' in read(out, "index.html")


def test_favicon_svg_is_a_plain_self_contained_svg(out):
    svg = read(out, "favicon.svg")
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert "<script" not in svg and "href" not in svg
    assert svg == adaptive_mark_svg(), "파비콘은 브랜드 마크 (fin_checkup.brand)"


def test_favicon_ico_is_a_valid_32px_icon(out):
    data = (out / "favicon.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind, count) == (0, 1, 1)
    width, height, _c, _r, _planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6)
    assert (width, height, bpp) == (32, 32, 32)
    assert offset + size == len(data)
    image = pytest.importorskip("PIL.Image")
    with image.open(io.BytesIO(data)) as icon:
        assert icon.size == (32, 32)
        rgba = icon.convert("RGBA")
        assert rgba.getpixel((0, 0))[3] == 0, "모서리는 투명"
        assert rgba.getpixel((16, 24))[3] == 255, "가운데 보고서 종이는 불투명"
        assert list(rgba.getdata()) == list(draw_mark(32).getdata()), "ICO 도 같은 마크"


# ── 포커스·대비 ────────────────────────────────────────────────────


def css(out) -> str:
    return read(out, "assets", "site.css")


def test_every_focusable_thing_has_a_visible_focus_style(out):
    rule = re.search(r"([^{}]*:focus-visible[^{}]*)\{([^}]*)\}", css(out))
    assert rule is not None
    selectors, body = rule.group(1), rule.group(2)
    for target in ("a", "button", "input", "select", "textarea", "summary", "[tabindex]"):
        assert f"{target}:focus-visible" in selectors, target
    assert "outline: 3px solid var(--focus)" in body
    assert ".fc-skip:focus" in css(out)


def _theme_vars(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


def themes(text: str) -> dict[str, dict[str, str]]:
    light_block = text.split("@media (prefers-color-scheme: dark)", 1)[0]
    dark_block = text.split("@media (prefers-color-scheme: dark)", 1)[1].split("\n}\n", 1)[0]
    light = _theme_vars(light_block)
    return {"light": light, "dark": {**light, **_theme_vars(dark_block)}}


def _luminance(hex_color: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_helper_matches_known_values():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


SIGNALS = ("green", "yellow", "red", "neutral", "not_applicable", "unknown")
#: 글자색 × 그 글자가 놓이는 바탕 (site.css 에서 실제로 쓰는 조합).
TEXT_PAIRS = (
    [(fg, bg) for fg in ("ink", "ink-muted", "link") for bg in ("surface", "surface-sunk")]
    + [("ink", f"tint-{s}") for s in SIGNALS]
    + [("ink-muted", f"tint-{s}") for s in SIGNALS]
)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("fg", "bg"), TEXT_PAIRS)
def test_text_contrast_is_at_least_4_5(out, theme, fg, bg):
    colors = themes(css(out))[theme]
    ratio = contrast(colors[fg], colors[bg])
    assert ratio >= 4.5, f"{theme}: --{fg} {colors[fg]} on --{bg} {colors[bg]} = {ratio:.2f}"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("bg", ["surface", "surface-sunk"])
def test_focus_ring_contrast_is_at_least_3(out, theme, bg):
    """포커스 테두리는 글자가 아니라 3:1 (WCAG 1.4.11)."""
    colors = themes(css(out))[theme]
    assert contrast(colors["focus"], colors[bg]) >= 3.0
