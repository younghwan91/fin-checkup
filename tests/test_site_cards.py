"""카드뉴스 — 모든 장에 출처·면책, 판 밖으로 넘치는 글자 없음, 금지 표현 없음, 두부(□) 없음.

글자는 OCR 하지 않고 `Canvas.texts`(그린 문자열과 글꼴 지표로 잰 상자)로 본다.
한국어 글꼴이 없는 환경에선 건너뛴다 (CI 는 fonts-noto-cjk 를 깔고, 없으면 test_site_images 가 실패한다).
"""

from __future__ import annotations

import dataclasses
import io
from collections import Counter

import pytest
from hub_seed import seed_site_cache
from PIL import Image

from fin_checkup.cli import main
from fin_checkup.config import settings
from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain import banned
from fin_checkup.metrics.signals import Signal
from fin_checkup.site.build import collect_companies
from fin_checkup.site.images.cards import (
    HEIGHT,
    MORE,
    SOURCE_NAME,
    WIDTH,
    BannedPhraseError,
    CardDeck,
    MetricBlock,
    Section,
    card_deck,
    draw_deck,
    plain,
)
from fin_checkup.site.images.fonts import missing_glyphs, resolve_fonts
from fin_checkup.site.images.layout import clamp_lines, wrap_text
from fin_checkup.storage import Cache


@pytest.fixture(scope="module")
def fonts():
    found = resolve_fonts()
    if found is None:
        pytest.skip("한국어 글꼴 없음 (CI 는 fonts-noto-cjk 를 깐다)")
    return found


@pytest.fixture
async def checkups(tmp_path):
    with Cache(tmp_path / "cards.duckdb") as cache:
        seed_site_cache(cache)
        entries, _ = await collect_companies(cache, 2024)
    return {e.stock_code: e.checkup for e in entries}


def squash(text: str) -> str:
    """줄바꿈·띄어쓰기를 걷어낸다 — 문단이 여러 줄로 나뉘어 그려져도 같은 문장인지 보려고."""
    return "".join(text.split())


def assert_card_rules(canvas, deck, fonts):
    text = canvas.all_text()
    assert squash(DISCLAIMER) in squash(text), "모든 장에 면책 문구 전문"
    assert f"출처: {SOURCE_NAME}" in text and "DART" in text, "모든 장에 출처"
    assert canvas.overflow(margin=24) == [], "판 밖으로 나간 글자·도형"
    assert canvas.collisions() == [], "서로 겹친 글자 (본문이 바닥글을 덮는 등)"
    assert len(canvas.logos) == 1, "모든 장 머리에 브랜드 마크 하나"
    logo = canvas.logos[0]
    assert not any(
        min(logo.x1, b.x1) > max(logo.x0, b.x0) and min(logo.y1, b.y1) > max(logo.y0, b.y0)
        for _t, b in canvas.texts
    ), "마크가 글자를 덮지 않는다"
    assert banned.find_outside_allowed(text, [deck.name]) == []
    for line, _box in canvas.texts:
        assert not missing_glyphs(line, fonts.font(24)), f"글꼴에 없는 글자(두부): {line!r}"
        assert not missing_glyphs(line, fonts.font(24, bold=True)), line


@pytest.mark.parametrize("code", ["005930", "105560", "028300"])
async def test_every_card_has_source_disclaimer_and_stays_inside(checkups, fonts, code):
    deck = card_deck(checkups[code])
    pages = draw_deck(deck, fonts)
    assert 5 <= len(pages) <= 7
    for name, canvas in pages:
        assert name.startswith(f"{code}-")
        assert_card_rules(canvas, deck, fonts)


async def test_cards_carry_formula_and_plain_language_why(checkups, fonts):
    deck = card_deck(checkups["005930"])
    text = squash("\n".join(c.all_text() for _, c in draw_deck(deck, fonts)))
    block = deck.sections[0].blocks[0]
    assert squash(f"계산식: {block.formula}") in text
    assert squash(block.why) in text or squash(block.why_short) in text
    # 신호 기호는 도형으로 그리고 글자로는 안 찍는다.
    assert not any(s.emoji in text for s in Signal if s is not Signal.NOT_APPLICABLE)


async def test_bank_cards_say_what_to_look_at_instead(checkups, fonts):
    deck = card_deck(checkups["105560"])
    assert deck.sector_label == "금융업"
    text = squash("\n".join(c.all_text() for _, c in draw_deck(deck, fonts)))
    assert squash("대신 볼 것") in text


async def test_png_size(checkups, fonts):
    (_name, canvas), *_ = draw_deck(card_deck(checkups["005930"]), fonts)
    with Image.open(io.BytesIO(canvas.png())) as image:
        assert image.size == (WIDTH, HEIGHT) == (1080, 1350)


def _bank_without_revenue(checkup):
    """KB금융 2025 실데이터 모양 — 은행은 매출액 계정이 없어 영업이익률·순이익률이 ⚫ 다.
    ⚫ 설명은 why_color 의 세 문장(계정·이유·0으로 안 채움)이라 길다. 2026-11-21 카드에서 이 문장이
    '…않았을 수…' 로 잘렸다 (블록마다 같은 줄 수를 나눠 줘서 한 줄짜리 블록의 남는 자리를 버렸다)."""
    for metric in checkup.result.metrics:
        if metric.key in ("operating_margin", "net_margin", "revenue_growth"):
            metric.value = None
            metric.signal = Signal.UNKNOWN
            metric.note = ""
    return checkup


def _section_pages(pages):
    return [c for name, c in pages if "-section" in name]


async def test_missing_account_why_is_drawn_whole_not_clipped(checkups, fonts):
    deck = card_deck(_bank_without_revenue(checkups["105560"]))
    pages = draw_deck(deck, fonts)
    profit = next(s for s in deck.sections if s.title == "수익성")
    assert [b.signal for b in profit.blocks].count(Signal.UNKNOWN) == 2
    page = next(c for c in _section_pages(pages) if "수익성" in c.all_text())
    text = page.all_text()
    for block in profit.blocks:
        assert squash(block.why) in squash(text), f"{block.label} 설명이 잘렸다"
    assert "…" not in text
    for _name, canvas in pages:
        assert_card_rules(canvas, deck, fonts)


@pytest.mark.parametrize("code", ["005930", "105560", "028300", "105560-bank"])
async def test_every_metric_counted_on_the_summary_is_on_some_card(checkups, fonts, code):
    """요약 장의 신호 개수(🟡 3 등)에 든 지표는 모두 어느 장엔가 있다 — 세었는데 못 찾는 지표가 없게."""
    checkup = (
        _bank_without_revenue(checkups["105560"]) if code == "105560-bank" else checkups[code]
    )
    deck = card_deck(checkup)
    pages = draw_deck(deck, fonts)
    assert len(pages) <= 7
    shown = [b for s in deck.sections for b in s.blocks]
    counted = {s: n for s, n in deck.chips if n}
    assert dict(Counter(b.signal for b in shown)) == counted
    assert sorted(b.label for b in shown) == sorted(m.label for m in checkup.result.metrics)
    assert len(shown) == deck.total
    drawn = squash("\n".join(c.all_text() for c in _section_pages(pages)))
    for block in shown:
        assert squash(block.label) in drawn, f"{block.label} 이 어느 장에도 없다"
        assert squash(f"계산식: {block.formula}") in drawn
        assert squash(block.band) in drawn
        if block.signal is Signal.NOT_APPLICABLE:
            assert squash(block.why_keep) in drawn, "⊘ 는 대신 볼 것을 남긴다"
    assert "사이트에서볼수있어요" not in drawn, "블록을 빼지 않는다"


def test_clamp_says_where_the_rest_is(fonts):
    font = fonts.font(24)
    lines = wrap_text("잘리는 문장이 여러 줄로 이어진다. " * 8, font, 600)
    kept = clamp_lines(lines, font, 600, 2, MORE)
    assert len(kept) == 2 and kept[-1].endswith(MORE)
    assert font.getlength(kept[-1]) <= 600


def _stress_deck() -> CardDeck:
    long_why = "아주 긴 설명이 이어지는 경우를 본다. " * 12
    block = MetricBlock(
        label="아주아주 긴 지표 이름 (영문 약칭까지 붙은 경우의 이름)",
        value_text="-12,345,678.90%",
        signal=Signal.RED,
        band="위험 — 기준: 정상 100% 이하 · 주의 200% 이하 · 그 초과는 위험",
        formula="부채총계 ÷ 자기자본 × 100 (연결재무제표의 지배기업 소유주지분 기준으로 다시 계산한 값)",
        why=long_why,
        why_short=long_why,
    )
    return CardDeck(
        name="한국가스공사에스케이하이닉스포스코퓨처엠엘지에너지솔루션우선주",
        stock_code="900000",
        market="KOSDAQ",
        year=2024,
        report_label="사업보고서",
        statement_label="연결재무제표",
        sector_label="부동산업",
        sector_notice="이 회사는 부동산업으로 분류돼 일부 지표를 ‘해당 없음’으로 둡니다. " * 4,
        chips=tuple((s, 9) for s in Signal),
        metric_signals=tuple((f"지표 이름 {i} 아주 길게 늘인 경우", Signal.GREEN) for i in range(17)),
        total=54,
        sections=(Section("수익성", "부제", (block,) * 5),),
        page_url="https://a-very-long-custom-domain.example.com/some/deep/path/fin-checkup/c/900000/",
    )


def test_a_too_long_why_is_cut_with_where_to_continue(fonts):
    """줄이고도 넘치면 긴 설명만 줄 수를 자르고 끝에 '… 회사 페이지에서 계속' — 말없이 '…' 로 끊지 않는다."""
    deck = _stress_deck()
    base = deck.sections[0].blocks[0]
    short = dataclasses.replace(base, why="짧은 설명이에요.", why_short="짧은 설명이에요.")
    very_long = "아주 긴 설명이 이어지는 경우를 본다. " * 40
    long = dataclasses.replace(base, why=very_long, why_short=very_long)
    deck = dataclasses.replace(deck, sections=(Section("수익성", "부제", (short, long, short)),))
    pages = draw_deck(deck, fonts)
    page = next(c for name, c in pages if "-section" in name)
    lines = [t for t, _ in page.texts]
    assert any(t.endswith(MORE) for t in lines)
    assert sum("짧은 설명이에요." in t for t in lines) == 2, "짧은 설명은 그대로"
    for _name, canvas in pages:
        assert_card_rules(canvas, deck, fonts)


def test_worst_case_text_still_fits(fonts):
    deck = _stress_deck()
    for _name, canvas in draw_deck(deck, fonts):
        assert_card_rules(canvas, deck, fonts)


def test_a_banned_phrase_stops_the_deck(fonts):
    deck = _stress_deck()
    bad = dataclasses.replace(
        deck, sections=(Section("수익성", "지금 매수할 종목", deck.sections[0].blocks),)
    )
    with pytest.raises(BannedPhraseError):
        draw_deck(bad, fonts)


def test_company_name_with_a_banned_word_is_data_not_advice(fonts):
    """회사 이름은 우리가 쓴 문장이 아니다 — 이름에 금지 표현이 있어도 카드는 나온다."""
    deck = dataclasses.replace(_stress_deck(), name="우량산업")
    assert draw_deck(deck, fonts)


def test_plain_strips_signal_symbols_but_keeps_the_word():
    assert (
        plain("영업이익률 10.88%는 7% 이상이라 🟢 정상이에요") == "영업이익률 10.88%는 7% 이상이라 정상이에요"
    )
    assert plain("업종에 맞지 않는 지표는 ⊘로 표시") == "업종에 맞지 않는 지표는 ‘해당 없음’으로 표시"
    assert plain("부채비율 ⚫ 데이터 없음") == "부채비율 데이터 없음"


def test_missing_glyphs_catches_color_emoji(fonts):
    assert "🟢" in missing_glyphs("정상 🟢", fonts.font(24))
    assert missing_glyphs("정상 7% 이상 · 계산식 ÷ ×", fonts.font(24)) == set()


async def test_cli_writes_the_cards(tmp_path, monkeypatch, fonts, capsys):
    db = tmp_path / "cli.duckdb"
    with Cache(db) as cache:
        seed_site_cache(cache)
    monkeypatch.setattr(settings, "fin_checkup_db_path", db)
    out = tmp_path / "cards"
    assert await _run(["site", "cards", "005930", "--year", "2024", "--out", str(out)]) == 0
    files = sorted(p.name for p in out.iterdir())
    assert files[0] == "005930-1-cover.png" and files[-1].endswith("-read.png")
    assert 5 <= len(files) <= 7
    assert "docs/card-news.md" in capsys.readouterr().out

    assert await _run(["site", "cards", "999999", "--year", "2024", "--out", str(out)]) == 1


async def _run(argv):
    # main() 은 asyncio.run 을 쓴다 — 이미 도는 이벤트 루프 밖(스레드)에서 부른다.
    import asyncio

    return await asyncio.to_thread(main, argv)
