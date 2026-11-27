"""신호 표시는 CSS 모양 + 글자 — 이모지 글꼴에 기대지 않는다.

사용성 조사(2026-11-21): ⚪(측정값)가 어떤 글꼴에선 흑백 "○" 로 그려져 '값이 비어 있음' 처럼 읽혔다.
화면(회사·나란히 보기·관심종목·업종/시장 표)에서는 `_signal.html` 의 모양(.fc-mark.mark-<신호>)과 신호 이름을
쓰고, 글자만 있는 곳(피드·JSON·meta 설명)은 이모지를 그대로 둔다.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path

import pytest
from hub_seed import html_pages, seed_site_cache

from fin_checkup.metrics.signals import Signal
from fin_checkup.site.build import build_site
from fin_checkup.storage import Cache

STATIC = Path(__file__).parent.parent / "src" / "fin_checkup" / "site" / "static"
#: ⊘ 는 글꼴이 흔히 가진 수학 기호라 본문 설명("⊘로 표시")에 남아도 된다. 나머지는 이모지 글꼴에 기댄다.
EMOJI = [s.emoji for s in Signal if s is not Signal.NOT_APPLICABLE]


@pytest.fixture
async def out(tmp_path):
    with Cache(tmp_path / "site.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp_path / "out"
        await build_site(cache, target, 2024, built_on=date(2026, 11, 21))
    return target


def main(path: Path) -> str:
    text = html.unescape(path.read_text(encoding="utf-8"))
    body = re.search(r"<main.*?</main>", text, re.S).group(0)
    # 범례의 "글자로는 🟢" 는 피드·알림 글자와 화면 모양을 잇는 설명이라 뺀다.
    body = re.sub(r'<small class="fc-sig-glyph">.*?</small>', "", body, flags=re.S)
    # 인라인 SVG 의 <title>·<desc>·칸 글자는 그림의 글자 설명이라 그대로 둔다.
    return re.sub(r"<svg.*?</svg>", "", body, flags=re.S)


SCREENS = [
    "index.html",
    "c/005930/index.html",
    "c/105560/index.html",
    "market/index.html",
    "m/debt_ratio/index.html",
    "m/interest_coverage/index.html",
    "learn/index.html",
    "learn/first-read/index.html",
    "learn/redflags/index.html",
]


def test_no_page_draws_signal_emoji_on_screen(out):
    for path in html_pages(out):
        body = main(path)
        for emoji in EMOJI:
            assert emoji not in body, f"{path.relative_to(out)}: 화면에 이모지 {emoji}"


@pytest.mark.parametrize("path", SCREENS)
def test_screens_draw_signals_with_css_marks_not_emoji(out, path):
    body = main(out / path)
    for emoji in EMOJI:
        assert emoji not in body, f"{path}: 화면에 이모지 {emoji}"
    marks = re.findall(r'<span class="fc-mark mark-(\w+)" aria-hidden="true"></span>', body)
    assert marks, f"{path}: 신호 모양이 없다"
    assert set(marks) <= {s.value for s in Signal}


def test_every_metric_card_mark_matches_its_signal_and_carries_the_label(out):
    body = main(out / "c" / "005930" / "index.html")
    cards = re.findall(r'<li class="fc-metric sig-(\w+)" id="m-\w+">(.*?)</li>\n', body, re.S)
    assert cards
    labels = {s.value: s.label for s in Signal}
    for key, card in cards:
        mark = f'<span class="fc-mark mark-{key}" aria-hidden="true"></span>'
        badge = f'<span class="fc-sig sig-{key}">{mark}{labels[key]}</span>'
        assert badge in card


def test_neutral_mark_is_a_filled_shape_not_an_empty_ring(out):
    css = (out / "assets" / "site.css").read_text(encoding="utf-8")
    for signal in Signal:
        assert re.search(rf"\.mark-{signal.value} \{{[^}}]+\}}", css), signal
    neutral = re.search(r"\.mark-neutral \{([^}]+)\}", css).group(1)
    assert "background: var(--sig-neutral)" in neutral and "border" not in neutral.replace(
        "border-radius", ""
    )


def test_compare_and_watch_scripts_draw_marks_not_emoji():
    compare = (STATIC / "compare.js").read_text(encoding="utf-8")
    watch = (STATIC / "watch.js").read_text(encoding="utf-8")
    assert '"fc-mark mark-" + metric.signal.key' in compare and "signal.emoji" not in compare
    assert '"fc-mark mark-" + key' in watch and ".emoji +" not in watch


def test_plain_text_contexts_keep_emoji(out):
    """피드·JSON·meta 설명은 글자만 있는 곳 — 이모지를 그대로 둔다."""
    company = json.loads((out / "api" / "c" / "005930.json").read_text(encoding="utf-8"))
    assert {m["signal"]["emoji"] for m in company["metrics"]} <= {s.emoji for s in Signal}
    head = (out / "learn" / "index.html").read_text(encoding="utf-8").split("<main", 1)[0]
    assert all(s.emoji in head for s in Signal), "meta 설명의 신호 기호"
    feed = (out / "feeds" / "c" / "005930.xml").read_text(encoding="utf-8")
    assert "<feed" in feed


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "10% 이상이면 🟢 정상",
            '10% 이상이면 <span class="fc-mark mark-green" aria-hidden="true"></span>정상',
        ),
        (
            "바로 🔴로 표시해요",
            '바로 <span class="fc-mark mark-red" aria-hidden="true"></span>'
            '<span class="fc-visually-hidden">위험</span>로 표시해요',
        ),
        (
            "값만 ⚪ 측정값으로",
            '값만 <span class="fc-mark mark-neutral" aria-hidden="true"></span>측정값으로',
        ),
        (
            "<b>⊘ 해당 없음</b>",
            '&lt;b&gt;<span class="fc-mark mark-not_applicable" aria-hidden="true"></span>'
            "해당 없음&lt;/b&gt;",
        ),
        ("신호 없음", "신호 없음"),
    ],
)
def test_marks_filter_swaps_glyphs_keeps_the_name_and_escapes(text, expected):
    from fin_checkup.site.render import signal_marks

    assert str(signal_marks(text)) == expected
