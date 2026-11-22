"""PNG 배치 도구·글꼴 찾기·공유 카드.

넘침은 픽셀이 아니라 글꼴 지표로 잰다 — `Canvas.texts` 에 남은 글자 상자가 판 안에 드는지.
한국어 글꼴이 없는 환경에선 그리는 테스트를 건너뛴다(CI 는 fonts-noto-cjk 를 깐다).
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image

from fin_checkup.explain import banned
from fin_checkup.metrics.signals import Signal
from fin_checkup.site.images import fonts as font_mod
from fin_checkup.site.images.fonts import FontFace, find_fonts, has_hangul, resolve_fonts
from fin_checkup.site.images.layout import ELLIPSIS, Canvas, ellipsize, text_width, wrap_text
from fin_checkup.site.images.og import FOOTER, HEIGHT, WIDTH, OgCard, OgMetric, draw_og, render_og

DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


@pytest.fixture(scope="module")
def fonts():
    found = resolve_fonts()
    if found is None:
        pytest.skip("한국어 글꼴 없음 (CI 는 fonts-noto-cjk 를 깐다)")
    return found


# ── 글꼴 찾기 ──────────────────────────────────────────────────


def test_a_missing_configured_font_is_not_silently_replaced(monkeypatch):
    monkeypatch.setattr(font_mod, "CANDIDATES", ())
    assert find_fonts("/nonexistent/font.ttf") is None


@pytest.mark.skipif(not DEJAVU.exists(), reason="DejaVu 없음")
def test_a_font_without_hangul_is_rejected():
    """한글 없는 글꼴로 그리면 글자마다 두부(□)다 — 쓰지 않는다."""
    assert not has_hangul(FontFace(str(DEJAVU)))
    assert find_fonts(str(DEJAVU)) is None


def test_found_font_draws_hangul(fonts):
    assert has_hangul(fonts.regular) and has_hangul(fonts.bold)


# ── 줄바꿈·말줄임 ──────────────────────────────────────────────

LONG = (
    "영업이익률은 10.88%로 '8% 이상'이라 🟢 정상이에요(업종 120개사 중앙값 5.1%, 작년 2.5%). "
    "띄어쓰기없이아주길게이어진낱말도한줄을넘으면글자단위로잘라야한다"
)


@pytest.mark.parametrize("width", [120, 300, 640])
def test_wrap_keeps_every_line_inside_the_width(fonts, width):
    font = fonts.font(26)
    lines = wrap_text(LONG, font, width)
    assert all(text_width(line, font) <= width for line in lines)
    # 글자를 잃지 않는다 (띄어쓰기만 줄바꿈으로 바뀐다).
    assert "".join(lines).replace(" ", "") == LONG.replace(" ", "")


def test_wrap_respects_explicit_newlines(fonts):
    assert wrap_text("첫 줄\n둘째 줄", fonts.font(26), 1000) == ["첫 줄", "둘째 줄"]


def test_ellipsize_fits(fonts):
    font = fonts.font(30, bold=True)
    short = ellipsize(LONG, font, 200)
    assert short.endswith(ELLIPSIS) and text_width(short, font) <= 200
    assert ellipsize("삼성전자", font, 400) == "삼성전자"


def test_paragraph_clamps_lines_and_records_boxes(fonts):
    canvas = Canvas(400, 400, fonts, background="#ffffff", scale=1)
    bottom = canvas.paragraph(10, 10, LONG, 22, 380, fill="#000000", max_lines=3)
    assert len(canvas.texts) == 3 and canvas.texts[-1][0].endswith(ELLIPSIS)
    assert bottom <= 400 and not canvas.overflow()


# ── 공유 카드 ──────────────────────────────────────────────────

ALL_SIGNALS = tuple((s, n) for s, n in zip(Signal, (12, 3, 2, 3, 7, 1), strict=True))


def _card(**overrides) -> OgCard:
    base = dict(
        name="삼성전자",
        stock_code="005930",
        year=2024,
        subtitle="005930 · KOSPI · 2024 사업보고서 · 연결재무제표",
        chips=ALL_SIGNALS[:3],
        total=17,
        metrics=(
            OgMetric("영업이익률", "10.88%", Signal.GREEN),
            OgMetric("ROE (자기자본이익률)", "8.57%", Signal.YELLOW),
            OgMetric("부채비율", "1,166.98%", Signal.NOT_APPLICABLE),
            OgMetric("영업활동현금흐름", "—", Signal.UNKNOWN),
        ),
        site_label="younghwan91.github.io/fin-checkup",
    )
    base.update(overrides)
    return OgCard(**base)


@pytest.mark.parametrize(
    "card",
    [
        _card(),
        # 가장 빡빡한 경우: 신호 여섯 가지 전부, 아주 긴 회사명·값·주소.
        _card(
            name="한국가스공사에스케이하이닉스포스코퓨처엠엘지에너지솔루션우선주",
            chips=ALL_SIGNALS,
            subtitle="900000 · KOSDAQ · 2024 사업보고서 · 연결재무제표 · 아주 긴 부가 설명이 붙은 경우",
            metrics=(
                OgMetric("영업이익률", "-12,345,678.90%", Signal.RED),
                OgMetric("ROE (자기자본이익률)", "-1,234.56%", Signal.RED),
                OgMetric("부채비율", "123,456.78%", Signal.RED),
                OgMetric("영업활동현금흐름", "-1,234,567.8조원", Signal.RED),
            ),
            site_label="a-very-long-custom-domain.example.com/some/deep/path/fin-checkup",
        ),
    ],
)
def test_og_card_has_no_text_outside_the_card(fonts, card):
    canvas = draw_og(card, fonts)
    assert canvas.overflow(margin=24) == []
    assert canvas.collisions() == []


def test_og_card_carries_source_disclaimer_and_counts_not_a_score(fonts):
    canvas = draw_og(_card(chips=ALL_SIGNALS), fonts)
    text = canvas.all_text()
    assert FOOTER in text and "출처: DART" in text and "투자권유가 아닙니다" in text
    assert "점수 아님" in text
    for signal in Signal:
        assert signal.label in text  # 색만으로 말하지 않는다
    assert banned.find(text) == []


def test_og_png_is_1200_by_630(fonts):
    png = render_og(_card(), fonts)
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (WIDTH, HEIGHT) == (1200, 630)
    assert len(png) < 120_000  # 2,800장이 Pages 용량을 채우지 않게 팔레트 PNG


@pytest.mark.skipif(not os.environ.get("CI"), reason="CI 에서만 — 로컬은 글꼴이 없어도 된다")
def test_ci_has_a_korean_font_so_drawing_tests_do_not_silently_skip():
    assert resolve_fonts() is not None, "ci.yml 의 fonts-noto-cjk 설치 단계를 확인할 것"


def _overlaps(a, b) -> bool:
    return min(a.x1, b.x1) - max(a.x0, b.x0) > 0 and min(a.y1, b.y1) - max(a.y0, b.y0) > 0


def test_og_card_wears_the_brand_mark_beside_the_name(fonts):
    """머리글 왼쪽에 작은 마크 하나 — 글자와 겹치지 않고 'fin-checkup' 과 같은 줄."""
    canvas = draw_og(_card(), fonts)
    assert len(canvas.logos) == 1
    logo = canvas.logos[0]
    name = next(box for text, box in canvas.texts if text == "fin-checkup")
    assert logo.x1 < name.x0 and logo.height <= 48
    assert logo.y0 < (name.y0 + name.y1) / 2 < logo.y1
    assert not any(_overlaps(logo, box) for _t, box in canvas.texts)
    # 마크의 종이(먹색)가 실제로 찍혔다 — 상자만 남기고 그림을 빠뜨리지 않게.
    with Image.open(io.BytesIO(render_og(_card(), fonts))) as image:
        px = image.convert("RGB").getpixel((round(logo.x0 + logo.width * 0.85), round(logo.y1 - 4)))
    assert max(abs(c - t) for c, t in zip(px, (0x1B, 0x1B, 0x19), strict=True)) < 24, px


def test_signal_marks_are_the_site_shapes_not_six_circles(fonts):
    """PNG 의 신호 기호도 사이트·브랜드 마크와 같은 모양 — ● ▲ ■ ▬ ⊘ ◌.
    색을 못 가려도 모양이 갈린다: 같은 색으로 칠했다고 치고 모양(칠해진 칸)끼리 견준다."""
    masks = {}
    for signal in Signal:
        canvas = Canvas(40, 40, fonts, background="#ffffff", scale=2)
        canvas.signal_mark(20, 20, 14, signal)
        gray = canvas.image.convert("L")
        masks[signal] = [v < 245 for v in gray.getdata()]
    signals = list(Signal)
    for i, a in enumerate(signals):
        for b in signals[i + 1 :]:
            pairs = list(zip(masks[a], masks[b], strict=True))
            differ = sum(x != y for x, y in pairs)
            union = sum(x or y for x, y in pairs)
            assert differ > 0.12 * union, (a, b, differ, union)
