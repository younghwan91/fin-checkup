"""업종별 읽는 법 `/learn/sectors/` — ⊘ 목록·대신 볼 것은 엔진·해설 그대로, 업종 페이지는 업종 레인 경로."""

from __future__ import annotations

import html
import re
from datetime import date

import pytest
from hub_seed import seed_site_cache
from site_text import unmark

from fin_checkup.explain import banned, sector_reading
from fin_checkup.explain.metrics import get_explainer, threshold_text
from fin_checkup.metrics.engine import METRIC_DEFS, NOT_APPLICABLE_BY_SECTOR
from fin_checkup.metrics.sector import Sector
from fin_checkup.site.build import build_site
from fin_checkup.site.groups import sector_path
from fin_checkup.storage import Cache


@pytest.fixture
async def out(tmp_path):
    with Cache(tmp_path / "site.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp_path / "out"
        await build_site(cache, target, 2024, built_on=date(2026, 11, 21))
    return target


def page(out) -> str:
    return unmark(html.unescape((out / "learn" / "sectors" / "index.html").read_text(encoding="utf-8")))


def section(text: str, ident: str) -> str:
    match = re.search(rf'<section[^>]*id="{ident}".*?</section>', text, re.S)
    assert match, ident
    return match.group(0)


def test_every_non_general_sector_has_a_reading():
    assert {r.sector for r in sector_reading.READINGS} == {
        s for s in Sector if NOT_APPLICABLE_BY_SECTOR.get(s)
    }


@pytest.mark.parametrize("sector", [Sector.BANKING, Sector.FINANCIAL, Sector.REAL_ESTATE])
async def test_not_applicable_metrics_come_from_the_engine_with_instead(out, sector):
    block = section(page(out), sector.value)
    for key in NOT_APPLICABLE_BY_SECTOR[sector]:
        e = get_explainer(key)
        caveat = e.caveat_for(sector)
        assert e.label in block and caveat.why in block and caveat.instead in block, key
        assert f'href="../../m/{key}/"' in block
    assert f"⊘ {len(NOT_APPLICABLE_BY_SECTOR[sector])}개" in block


async def test_sector_specific_band_and_the_rest_are_spelled_out(out):
    fin = section(page(out), Sector.FINANCIAL.value)
    assert threshold_text("roa", Sector.FINANCIAL) in fin
    rest = len(METRIC_DEFS) - len(NOT_APPLICABLE_BY_SECTOR[Sector.FINANCIAL]) - 1
    assert f"나머지 {rest}개" in fin
    assert "이자보상배율 연속 1 미만" in fin
    real_estate = section(page(out), Sector.REAL_ESTATE.value)
    assert f"나머지 {len(METRIC_DEFS) - 1}개" in real_estate


async def test_links_to_sector_pages_use_the_sector_lane_paths(out):
    """시드의 KB금융(64992) → 은행·증권·금융지주, 묶음 64. 금융업·부동산 회사는 없어서 그 칸엔 링크도 없다."""
    text = page(out)
    assert f'href="../../{sector_path("64")}"' in section(text, Sector.BANKING.value)
    assert "s/64/" not in section(text, Sector.FINANCIAL.value)
    assert "s/68/" not in text
    for target in re.findall(r'href="\.\./\.\./(s/\d\d/)"', text):
        assert (out / target / "index.html").exists(), target


async def test_holding_companies_are_explained(out):
    holding = section(page(out), "holding")
    assert "64992" in holding and "비금융 지주" in holding


async def test_hub_links_the_sector_guide(out):
    hub = html.unescape((out / "learn" / "index.html").read_text(encoding="utf-8"))
    assert 'href="../learn/sectors/"' in hub


async def test_no_banned_phrases_and_haeyo_style(out):
    text = page(out)
    visible = re.sub(r"<[^>]+>", " ", text.split("<body>", 1)[1])
    assert not banned.find(visible), banned.find(visible)
    main = text.split("<main", 1)[1].split("</main>", 1)[0]
    assert "습니다" not in re.sub(r"<[^>]+>", " ", main)
