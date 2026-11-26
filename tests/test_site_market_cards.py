"""시장 전체 신호 비율 그림 — `/market/`·`/m/<지표>/` 공유 카드와 시장 카드뉴스.

지키는 것: `/market/` 과 같은 분포(`site.groups`)·회사 이름 없음·여섯 신호 전부(⚫·⊘ 를 0 으로 접지 않음)·
일반 기준 경계값과 계산식·출처와 면책·'…' 없음·정해진 규칙의 장 순서.
"""

from __future__ import annotations

import asyncio
import dataclasses
import html
import io
import re
from datetime import date

import pytest
from hub_seed import seed_site_cache
from PIL import Image

from fin_checkup.cli import main
from fin_checkup.config import settings
from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain import banned
from fin_checkup.metrics.engine import METRIC_DEFS, Category
from fin_checkup.metrics.signals import Signal
from fin_checkup.site.build import build_site, collect_companies
from fin_checkup.site.groups import SIGNAL_ORDER, distribution
from fin_checkup.site.images.cards import SOURCE_NAME, BannedPhraseError
from fin_checkup.site.images.fonts import resolve_fonts
from fin_checkup.site.images.market import (
    MARKET_OG_PATH,
    SHORT_DISCLAIMER,
    draw_market_deck,
    draw_market_og,
    market_deck,
    metric_og_path,
    share_text,
)
from fin_checkup.site.seo import check_seo
from fin_checkup.storage import Cache

BASE = "https://example.github.io/fin-checkup/"


@pytest.fixture(scope="module")
def fonts():
    found = resolve_fonts()
    if found is None:
        pytest.skip("한국어 글꼴 없음 (CI 는 fonts-noto-cjk 를 깐다)")
    return found


@pytest.fixture
async def entries(tmp_path):
    with Cache(tmp_path / "market.duckdb") as cache:
        seed_site_cache(cache)
        found, _ = await collect_companies(cache, 2024)
    assert found
    return found


def squash(text: str) -> str:
    return "".join(text.split())


def body_text(canvas) -> str:
    """면책 문구(전문·짧은 문구)를 뺀 글자 — '투자' 같은 낱말이 면책 문구 밖에 있는지 볼 때."""
    return squash(canvas.all_text()).replace(squash(DISCLAIMER), "").replace(squash(SHORT_DISCLAIMER), "")


def assert_market_rules(canvas, entries, fonts_, full_disclaimer: bool):
    text = canvas.all_text()
    if full_disclaimer:
        assert squash(DISCLAIMER) in squash(text), "모든 장에 면책 문구 전문"
    else:
        assert SHORT_DISCLAIMER in text
    assert SOURCE_NAME in text, "출처"
    assert canvas.overflow(margin=24) == [], "판 밖으로 나간 글자·도형"
    assert canvas.collisions() == [], "서로 겹친 글자"
    assert len(canvas.logos) == 1
    assert banned.find_outside_allowed(text, []) == []
    for line, _box in canvas.texts:
        assert "…" not in line, f"잘린 글자: {line!r}"
    for entry in entries:
        assert entry.name not in text, f"회사 이름 {entry.name} 이 그림에 있다"
        assert entry.stock_code not in text
    body = body_text(canvas)
    # '순위' 는 읽는 법 장의 "순위는 싣지 않았어요" 에만 — 순위 자체는 없다 (상위·하위 N 도).
    for word in ("투자", "주의하세요", "추천", "상위", "하위"):
        assert word not in body, word


async def test_shares_are_the_market_page_distribution(entries):
    """비율은 `/market/` 이 쓰는 `site.groups.distribution` 그대로 — 회사마다 자기 업종 기준."""
    deck = market_deck(entries, 2024)
    assert deck.total == len(entries)
    assert [m.key for m in deck.metrics] == [spec.key for spec in METRIC_DEFS]
    for spec, metric in zip(METRIC_DEFS, deck.metrics, strict=True):
        page = distribution(spec, entries)
        assert [s.signal for s in metric.shares] == list(SIGNAL_ORDER), "여섯 신호 전부, 0 곳도"
        assert [(s.count, s.percent) for s in metric.shares] == [(s.count, s.percent) for s in page.signals]
        assert sum(s.count for s in metric.shares) == metric.total == len(entries)
        assert metric.measured == page.count


async def test_bank_metrics_show_not_applicable_counts_and_name_the_own_standard(entries):
    deck = market_deck(entries, 2024)
    margin = next(m for m in deck.metrics if m.key == "operating_margin")
    not_applicable = next(s for s in margin.shares if s.signal is Signal.NOT_APPLICABLE)
    assert not_applicable.count >= 1, "KB금융(은행·증권·금융지주)의 영업이익률은 ⊘"
    assert "은행·증권·금융지주에는 판정하지 않음" in margin.sector_note
    assert margin.standard == "정상 7% 이상 · 주의 0% 이상 · 그 미만은 위험"


async def test_deck_follows_the_fixed_rule_cover_categories_read(entries, fonts):
    deck = market_deck(entries, 2024)
    pages = draw_market_deck(deck, fonts)
    names = [name for name, _ in pages]
    assert len(pages) <= 7
    assert names[0] == "market-1-cover.png" and names[-1] == f"market-{len(pages)}-read.png"
    categories = [c for c, _ in deck.by_category()]
    assert categories == [c for c in Category if any(m.category is c for m in deck.metrics)]
    assert len(names) == len(categories) + 2


async def test_every_card_has_source_disclaimer_and_no_company_names(entries, fonts):
    deck = market_deck(entries, 2024)
    for _name, canvas in draw_market_deck(deck, fonts):
        assert_market_rules(canvas, entries, fonts, full_disclaimer=True)


async def test_every_metric_shows_all_six_shares_standard_and_formula(entries, fonts):
    deck = market_deck(entries, 2024)
    drawn = squash("\n".join(c.all_text() for _n, c in draw_market_deck(deck, fonts)))
    for metric in deck.metrics:
        assert squash(metric.label) in drawn
        assert squash(f"계산식: {metric.formula}") in drawn
        assert squash(f"경계값(일반): {metric.standard}") in drawn
        if metric.sector_note:
            assert squash(f"업종 기준: {metric.sector_note}") in drawn
        assert squash(f"값이 있는 곳 {metric.measured:,}곳") in drawn
        for share in metric.shares:
            assert squash(f"{share.percent:.1f}% ({share.count:,}곳)") in drawn, (metric.key, share)


async def test_market_share_card_carries_year_count_bar_standard_formula_source(entries, fonts):
    deck = market_deck(entries, 2024)
    metric = deck.metrics[0]
    canvas = draw_market_og(deck, metric, fonts, "시장 전체 분포", "example.github.io/fin-checkup")
    text = canvas.all_text()
    assert "2024 사업연도" in text and f"상장사 {deck.total:,}곳" in text
    assert metric.label in text and f"계산식: {metric.formula}" in text
    assert f"경계값(일반): {metric.standard}" in text
    for share in metric.shares:
        assert share_text(share) in text, share
    assert_market_rules(canvas, entries, fonts, full_disclaimer=False)
    with Image.open(io.BytesIO(canvas.png())) as image:
        assert image.size == (1200, 630)


async def test_a_banned_phrase_stops_the_market_cards(entries, fonts):
    deck = market_deck(entries, 2024)
    bad = dataclasses.replace(deck.metrics[0], label="지금 매수할 지표")
    deck = dataclasses.replace(deck, metrics=(bad, *deck.metrics[1:]))
    with pytest.raises(BannedPhraseError):
        draw_market_deck(deck, fonts)
    with pytest.raises(BannedPhraseError):
        draw_market_og(deck, bad, fonts, "시장 전체 분포")


def _meta(text: str, attr: str, name: str) -> str | None:
    m = re.search(rf'<meta {attr}="{re.escape(name)}" content="([^"]*)"', text)
    return html.unescape(m.group(1)) if m else None


@pytest.fixture
async def built(tmp_path, fonts):
    with Cache(tmp_path / "site.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp_path / "out"
        await build_site(cache, target, 2024, base_url=BASE, built_on=date(2026, 11, 21), images=True)
    return target


async def test_market_and_metric_pages_point_at_their_share_cards(built):
    pages = {"market/index.html": MARKET_OG_PATH}
    pages |= {f"m/{spec.key}/index.html": metric_og_path(spec.key) for spec in METRIC_DEFS}
    for page, png in pages.items():
        text = (built / page).read_text(encoding="utf-8")
        assert _meta(text, "property", "og:image") == BASE + png, page
        assert _meta(text, "name", "twitter:image") == BASE + png, page
        assert _meta(text, "name", "twitter:card") == "summary_large_image", page
        with Image.open(built / png) as image:
            assert image.size == (1200, 630)
    assert 'property="og:image"' not in (built / "index.html").read_text(encoding="utf-8")
    assert check_seo(built) == []


async def test_seo_guard_catches_a_market_page_without_its_share_card(built):
    page = built / "market" / "index.html"
    text = re.sub(r'<meta property="og:image" content="[^"]*">\n?', "", page.read_text(encoding="utf-8"))
    page.write_text(text, encoding="utf-8")
    metric = built / "m" / "roe" / "index.html"
    text = re.sub(r'<meta name="twitter:image" content="[^"]*">\n?', "", metric.read_text(encoding="utf-8"))
    metric.write_text(text, encoding="utf-8")
    found = {(v.path, v.detail) for v in check_seo(built)}
    assert ("market/index.html", f"og:image 가 공유 카드 {BASE}og/market.png 가 아니다") in found
    assert ("m/roe/index.html", f"twitter:image 가 공유 카드 {BASE}og/m/roe.png 가 아니다") in found


async def test_cli_writes_the_market_cards(tmp_path, monkeypatch, fonts, capsys):
    db = tmp_path / "cli.duckdb"
    with Cache(db) as cache:
        seed_site_cache(cache)
    monkeypatch.setattr(settings, "fin_checkup_db_path", db)
    out = tmp_path / "cards"
    argv = ["site", "cards", "--market", "--year", "2024", "--out", str(out)]
    assert await asyncio.to_thread(main, argv) == 0
    files = sorted(p.name for p in out.iterdir())
    assert files[0] == "market-1-cover.png" and files[-1].endswith("-read.png")
    assert len(files) <= 7
    assert "docs/card-news.md" in capsys.readouterr().out
    both = ["site", "cards", "005930", "--market", "--out", str(out)]
    assert await asyncio.to_thread(main, both) == 2
    assert await asyncio.to_thread(main, ["site", "cards", "--out", str(out)]) == 2


async def test_same_content_is_not_redrawn_in_one_process(tmp_path, fonts, monkeypatch):
    """시장 공유 카드 18장은 내용이 같으면 다시 그리지 않는다 — 테스트 빌드마다 1.4초씩 늘던 것."""
    from fin_checkup.site.pages import og_image

    calls = []
    real = og_image.draw_market_og
    monkeypatch.setattr(og_image, "draw_market_og", lambda *a, **k: calls.append(1) or real(*a, **k))
    monkeypatch.setattr(og_image, "_MARKET_MEMO", {})
    with Cache(tmp_path / "memo.duckdb") as cache:
        seed_site_cache(cache)
        for name in ("a", "b"):
            await build_site(cache, tmp_path / name, 2024, base_url=BASE, built_on=date(2026, 11, 21))
    assert len(calls) == len(METRIC_DEFS) + 1, "두 번째 빌드는 메모에서"
    assert (tmp_path / "a" / MARKET_OG_PATH).read_bytes() == (tmp_path / "b" / MARKET_OG_PATH).read_bytes()
