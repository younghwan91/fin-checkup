"""휴대폰 지표 카드 — 계산식·경계값은 늘 보이고, 동종업계 분포 그림은 숫자 요약 아래 한 번 눌러 펼친다.

라이브 사이트를 390px 로 보니 카드 하나가 거의 한 화면(780px 안팎)이라 지표 17개가 10화면을 넘었다.
높이의 절반이 분포 그림과 범례였다. 그림은 페이지에 그대로 두되(<details>) 요약 한 줄만 먼저 보인다.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from hub_seed import SAMSUNG
from test_site_sector import seed_group

from fin_checkup.site.build import build_site
from fin_checkup.site.guard import check_site
from fin_checkup.storage import Cache

#: 휴대폰 화면 높이(390×844)보다 한참 작아야 카드 여러 장이 한 화면에 걸친다.
MAX_CARD_HEIGHT = 600


@pytest.fixture
async def out(tmp_path):
    with Cache(tmp_path / "s.duckdb") as cache:
        seed_group(cache)  # 같은 업종 회사가 있어야 분포 그림이 그려진다
        target = tmp_path / "out"
        await build_site(cache, target, 2024, built_on=date(2026, 11, 21))
    return target


def cards(out) -> list[str]:
    text = (out / "c" / SAMSUNG.stock_code / "index.html").read_text(encoding="utf-8")
    return re.findall(r'<li class="fc-metric .*?</li>\n    (?=<li class="fc-metric|</ul>)', text, re.S)


def test_peer_figure_sits_in_a_closed_details_with_a_numeric_summary(out):
    with_strip = [c for c in cards(out) if "fc-peer" in c]
    assert with_strip, "시드에 분포 그림이 있는 카드가 없다"
    for card in with_strip:
        details = re.search(r'<details class="fc-peer">(.*?)</details>', card, re.S)
        assert details, "분포 그림은 <details> 안에"
        summary = re.search(r"<summary>(.*?)</summary>", details.group(1), re.S).group(1)
        for needed in ("분포", "가운데 50%", "중앙값", "이 회사"):
            assert needed in summary, needed
        assert "<svg" in details.group(1)
        assert "<details open" not in card


def test_formula_and_band_are_never_folded(out):
    for card in cards(out):
        assert "<dt>계산식</dt>" in card
        before_dl = card[: card.index('<dl class="fc-basis-list">')]
        assert "<details" not in before_dl, "계산식·경계값이 접힌 영역 안에 있다"


def test_folded_cards_pass_the_site_guard(out):
    assert check_site(out) == []


def test_browser_phone_cards_stay_short_and_open_on_tap(out):
    from site_browser import chromium, serve

    with serve(out) as base, chromium() as browser:
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(base + f"c/{SAMSUNG.stock_code}/")
        heights = page.locator("li.fc-metric").evaluate_all(
            "els => els.map(e => e.getBoundingClientRect().height)"
        )
        assert heights and max(heights) < MAX_CARD_HEIGHT, heights
        card = page.locator("li.fc-metric:has(details.fc-peer)").first
        svg = card.locator("details.fc-peer svg")
        assert not svg.is_visible()
        assert card.locator("dl.fc-basis-list").is_visible()
        card.locator("details.fc-peer summary").click()
        assert svg.is_visible()


def test_last_year_row_separates_change_from_evaluation(out):
    """"작년 118.70% ↑ 악화" 는 화살표(값이 올랐다)와 낱말(나빠졌다)이 반대로 읽혔다.

    카드에는 값의 움직임(27.93% → 29.94% · 2.01%p 높아짐)과 평가(…나빠진 쪽)를 줄을 나눠 적는다.
    """
    rows = [
        re.search(r'<dt>작년 대비</dt><dd class="fc-delta">(.*?)</dd>', c, re.S)
        for c in cards(out)
    ]
    rows = [r.group(1) for r in rows if r]
    assert rows, "시드에 작년 대비가 있는 카드가 없다"
    for row in rows:
        for contradicting in ("↑", "↓", "개선", "악화"):
            assert contradicting not in row, row
        if "작년과 같음" not in row:
            assert re.search(r"→ .*?(높아짐|낮아짐|늘어남|줄어듦)", row), row
    assert any('<span class="fc-delta-eval">' in r and "쪽" in r for r in rows), "평가 줄이 없다"


# ── 넓은 화면: 카드 줄 맞춤 ──────────────────────────────────────
# 두 열로 놓인 카드는 이름·값 / 설명 / 계산식 / 경계값 / 작년 대비 / 분포 / 링크 / 비고 줄이 서로 어긋났다
# (설명이 두 줄인 카드 옆의 한 줄 카드는 계산식이 한 줄 위에서 시작). 같은 줄에 놓인 카드는 같은 높이에서
# 같은 항목이 시작하게 CSS subgrid 로 맞춘다. 휴대폰(한 열)은 그대로 쌓는다 — 페이지가 길어지지 않게.


def test_card_grid_rows_are_placed_in_the_stylesheet():
    """CI 에는 Chromium 이 없다 — 브라우저 테스트가 건너뛰어도 규칙이 지워지면 여기서 잡는다."""
    import fin_checkup.site

    css = (Path(fin_checkup.site.__file__).parent / "static" / "site.css").read_text(encoding="utf-8")
    block = css[css.index("@supports (grid-template-rows: subgrid)") :]
    assert block.index("@media (min-width: 40rem)") < block.index(".fc-metric-grid"), "넓은 화면에서만"
    assert "grid-template-rows: subgrid" in block[block.index(".fc-metric-grid") :]
    parts = (".fc-metric-head", ".fc-desc", ".fc-basis-list", ".fc-peer", ".fc-metric-links", ".fc-note")
    for selector in parts:
        assert f".fc-metric-grid {selector}" in block, selector


def test_company_metric_list_opts_into_the_card_grid(out):
    text = (out / "c" / SAMSUNG.stock_code / "index.html").read_text(encoding="utf-8")
    assert '<ul class="fc-metrics fc-metric-grid">' in text


def test_browser_desktop_cards_in_a_row_line_up(out):
    from site_browser import chromium, serve

    with serve(out) as base, chromium() as browser:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(base + f"c/{SAMSUNG.stock_code}/")
        rows = page.locator("ul.fc-metric-grid").evaluate_all(
            """lists => lists.flatMap(ul => {
                const cards = [...ul.children];
                const top = (el, sel) => {
                    const n = el.querySelector(sel);
                    return n ? Math.round(n.getBoundingClientRect().top) : null;
                };
                const out = [];
                for (let i = 0; i + 1 < cards.length; i += 2) {
                    out.push(['.fc-desc', 'dl.fc-basis-list dt', '.fc-metric-links'].map(
                        s => [top(cards[i], s), top(cards[i + 1], s)]));
                }
                return out;
            })"""
        )
        assert rows, "두 열로 놓인 카드가 없다"
        for row in rows:
            for left, right in row:
                if left is not None and right is not None:
                    assert left == right, row


def test_browser_phone_cards_stay_one_column(out):
    from site_browser import chromium, serve

    with serve(out) as base, chromium() as browser:
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(base + f"c/{SAMSUNG.stock_code}/")
        lefts = page.locator("ul.fc-metric-grid > li").evaluate_all(
            "els => els.map(e => Math.round(e.getBoundingClientRect().left))"
        )
        assert len(set(lefts)) == 1, lefts
