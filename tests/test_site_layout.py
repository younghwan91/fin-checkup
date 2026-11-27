"""공통 레이아웃·CSS·랜딩 — 모든 페이지에 머리·꼬리(면책·출처), 외부 자원 없음, 다크 모드."""

from __future__ import annotations

import re
from datetime import date

import pytest
from hub_seed import html_pages, seed_site_cache

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.metrics.signals import Signal
from fin_checkup.site.build import build_site
from fin_checkup.storage import Cache
from fin_checkup.theme import SIGNAL_COLORS


@pytest.fixture
async def out(tmp_path):
    with Cache(tmp_path / "site.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp_path / "out"
        await build_site(cache, target, 2024, built_on=date(2026, 11, 21))
    return target




def read(path) -> str:
    return path.read_text(encoding="utf-8")


async def test_every_page_has_header_footer_disclaimer_and_sources(out):
    pages = html_pages(out)
    assert len(pages) >= 5  # 회사 3 + 랜딩 + learn
    for path in pages:
        text = read(path)
        assert 'class="fc-brand"' in text and ">fin-checkup</a>" in text, path
        assert '<svg class="fc-logo"' in text and 'aria-hidden="true"' in text, path
        assert DISCLAIMER in text, path
        assert "DART" in text and "SEC EDGAR" in text, path
        assert "데이터 기준: 2024 사업연도 · 빌드 2026-11-21" in text, path


async def test_no_external_resources_are_loaded(out):
    """글꼴·스크립트·스타일·이미지는 전부 같은 사이트에서. 외부 링크(<a>)만 허용한다."""
    loader = re.compile(r"<(?:script|link|img|iframe|source)\b[^>]*\b(?:src|href)=\"([^\"]+)\"")
    for path in html_pages(out):
        for target in loader.findall(read(path)):
            assert not re.match(r"^(?:[a-z]+:)?//", target), f"{path}: 외부 자원 {target}"
        assert "Content-Security-Policy" in read(path)
    css = read(out / "assets" / "site.css")
    assert "@import" not in css and "url(" not in css


async def test_stylesheet_is_linked_relative_to_each_page(out):
    assert 'href="./assets/site.css"' in read(out / "index.html")
    assert 'href="../../assets/site.css"' in read(out / "c" / "005930" / "index.html")
    assert 'href="../assets/site.css"' in read(out / "learn" / "index.html")


async def test_css_is_mobile_first_with_dark_mode(out):
    css = read(out / "assets" / "site.css")
    assert "@media (prefers-color-scheme: dark)" in css
    assert "@media (min-width:" in css and "@media (max-width:" not in css
    assert 'name="viewport"' in read(out / "index.html")


async def test_light_signal_colors_match_the_app_theme(out):
    """Streamlit 과 사이트의 신호색이 따로 놀면 같은 🟡 가 다른 색으로 보인다."""
    css = read(out / "assets" / "site.css")
    light = css.split("@media (prefers-color-scheme: dark)", 1)[0]
    for signal, color in SIGNAL_COLORS.items():
        assert f"--sig-{signal.value}: {color};" in light, signal


async def test_every_signal_has_a_style_and_a_symbol_in_the_legend(out):
    css = read(out / "assets" / "site.css")
    learn = read(out / "learn" / "index.html")
    for signal in Signal:
        assert f".sig-{signal.value} " in css, signal
        assert signal.emoji in learn and signal.label in learn, signal


async def test_landing_has_one_search_box_explanation_learn_link_and_as_of(out):
    text = read(out / "index.html")
    assert text.count('type="search"') == 1, "첫 화면 검색창은 하나"
    assert "공개된 공식으로 계산해" in text
    assert 'href="./learn/"' in text and "처음 오셨나요" in text
    assert "데이터 기준: 2024 사업연도 사업보고서 · 상장사 3곳 · 빌드 2026-11-21" in text


async def test_inner_pages_carry_the_header_search_box(out):
    text = read(out / "c" / "005930" / "index.html")
    assert text.count('type="search"') == 1
    assert 'href="../../"' in text, "사이트 이름은 홈으로 가는 상대 링크"
