"""시장 전체 분포 `/market/` — 지표마다 분포·신호 비율·계산식·경계값·해설 링크, 회사 이름 없음."""

from __future__ import annotations

import html
import re
import statistics
from datetime import date
from types import SimpleNamespace

import pytest
from site_text import unmark
from test_site_sector import EXTRA_26, HOLDING, seed_group

from fin_checkup.explain import banned
from fin_checkup.metrics import engine
from fin_checkup.metrics.engine import CheckupResult, Metric
from fin_checkup.metrics.sector import Sector
from fin_checkup.metrics.signals import Signal
from fin_checkup.site.build import build_site
from fin_checkup.site.groups import distribution
from fin_checkup.site.pages.sector import threshold_lines
from fin_checkup.storage import Cache

#: 시드의 페이지가 생기는 회사: 삼성·KB·에이치엘비·가나지주 + EXTRA_26 다섯.
TOTAL = 4 + len(EXTRA_26)


@pytest.fixture
async def page(tmp_path):
    with Cache(tmp_path / "site.duckdb") as cache:
        seed_group(cache)
        target = tmp_path / "out"
        await build_site(cache, target, 2024, built_on=date(2026, 11, 21))
    return unmark(html.unescape((target / "market" / "index.html").read_text(encoding="utf-8")))


def card(text: str, key: str) -> str:
    match = re.search(rf'<li class="fc-metric" id="d-{key}">.*?\n  </li>', text, re.S)
    assert match, key
    return match.group(0)


async def test_every_metric_has_formula_thresholds_signal_shares_and_explainer_link(page):
    for spec in engine.METRIC_DEFS:
        block = card(page, spec.key)
        assert spec.formula in block, spec.key
        assert "경계값" in block
        assert f'href="../m/{spec.key}/"' in block
        for emoji in "🟢🟡🔴⚪⊘⚫":  # 화면에선 CSS 모양 — unmark 로 되돌려 본다 (test_site_signal_marks)
            assert emoji in block, (spec.key, emoji)


async def test_distribution_and_shares_cover_every_company(page):
    # KB금융(매출 3000·영업이익 600)은 은행·증권·금융지주라 영업이익률이 ⊘ — 분포에서 빠진다.
    margins = [op / rev * 100 for _n, _c, rev, op in EXTRA_26] + [
        40 / 900 * 100,
        -50 / 100 * 100,
        50 / 500 * 100,
    ]
    block = card(page, "operating_margin")
    assert f"{len(margins)}곳" in block
    assert f"{statistics.median(margins):,.2f}%" in block
    shares = dict(re.findall(r"[🟢🟡🔴⚪⊘⚫] ([^<\d]+?) [\d.]+% <small>\((\d+)곳\)</small>", block))
    assert sum(int(n) for n in shares.values()) == TOTAL


async def test_thresholds_are_listed_per_sector_rule_in_use(page):
    debt = card(page, "debt_ratio")
    assert "경계값 (일반)" in debt and "경계값 (은행·증권·금융지주)" in debt
    assert "업종에 맞지 않는 ⊘ 1곳" in debt, "KB금융의 부채비율은 분포에 넣지 않는다"


async def test_no_company_names_or_ranking(page):
    main = page.split("<main")[1].split("</main>")[0]
    for name in ["삼성전자", "KB금융", "에이치엘비", HOLDING.corp_name, *(n for n, *_ in EXTRA_26)]:
        assert name not in main, name
    assert 'href="../c/' not in main
    assert banned.find(main) == []
    assert "상위" not in main and "순위" not in main


def test_threshold_lines_merge_sectors_with_the_same_sentence():
    every = [Sector.GENERAL, Sector.FINANCIAL, Sector.REAL_ESTATE]
    (only,) = threshold_lines("operating_margin", every)
    assert only.sector_label == "", "기준마다 문장이 같으면 한 줄"
    roa = threshold_lines("roa", every)
    assert [t.sector_label for t in roa] == ["일반·부동산업", "금융업"]


def _entry(metric: Metric, currency: str = "KRW"):
    result = CheckupResult(corp_code="x", bsns_year=2024, metrics=[metric], currency=currency)
    return SimpleNamespace(checkup=SimpleNamespace(result=result))


def test_judged_without_value_is_not_called_missing():
    spec = next(d for d in engine.METRIC_DEFS if d.key == "debt_ratio")

    def metric(value, signal):
        return Metric(
            key=spec.key,
            label=spec.label,
            category=spec.category,
            value=value,
            unit=spec.unit,
            signal=signal,
            description="",
            formula=spec.formula,
        )

    entries = [
        _entry(metric(None, Signal.RED)),  # 자본잠식 — 값 없이 🔴
        _entry(metric(None, Signal.UNKNOWN)),
        *[_entry(metric(float(v), Signal.GREEN)) for v in range(5)],
    ]
    dist = distribution(spec, entries)
    assert (dist.count, dist.missing, dist.judged_without_value, dist.total) == (5, 1, 1, 7)
    assert dist.excluded_text == "값이 없는 ⚫ 1곳, 값 없이 판정된 1곳(자본잠식 등)"
    assert dist.median == 2.0
