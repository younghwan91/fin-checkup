"""회사별 마크다운 `/c/<코드>/index.md` (+ `/c/<코드>.md`) · JSON `summary_ko`·`agent_guidance`.

에이전트가 HTML 을 긁지 않고 한 번에 읽는 짧은 글 — 한눈에 보기 / 지표 표(신호·값·작년·비교군 중앙값·
계산식·경계값) / 위험 신호 / 최근 위험 공시 / 출처·면책. ⚫ 은 0 이 아니라 '데이터 없음', ⊘ 는 대신 볼 것.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from hub_seed import HLB, KB, SAMSUNG, seed_site_cache

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.disclosures import FORMAT_VERSION
from fin_checkup.explain import banned
from fin_checkup.explain.agents import AGENT_GUIDANCE
from fin_checkup.metrics import engine
from fin_checkup.site.build import build_site
from fin_checkup.site.guard import check_file, check_site
from fin_checkup.site.links import check_links
from fin_checkup.site.pages.api import SCHEMA_VERSION
from fin_checkup.storage import Cache

BASE = "https://example.github.io/fin-checkup/"
SECTIONS = ("## 한눈에 보기", "## 지표", "## 위험 신호", "## 최근 위험 공시", "## 출처·면책")


def _feed():
    item = {
        "corp_code": SAMSUNG.corp_code,
        "stock_code": SAMSUNG.stock_code,
        "corp_name": SAMSUNG.corp_name,
        "report_nm": "주요사항보고서(유상증자결정)",
        "rcept_dt": "20261120",
        "rcept_no": "20261120000001",
        "category": "capital_raise",
        "category_label": "",
        "severity": "high",
        "is_correction": False,
        "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261120000001",
    }
    return {
        "format_version": FORMAT_VERSION,
        "generated_at": "2026-11-21T07:00:00+09:00",
        "bgn_de": "20261115",
        "end_de": "20261121",
        "days": 7,
        "truncated_types": [],
        "count": 1,
        "items": [item],
    }


async def _build(tmp_path, base_url: str = BASE, name: str = "out", feed=None):
    with Cache(tmp_path / f"{name}.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp_path / name
        await build_site(
            cache,
            target,
            2024,
            base_url=base_url,
            built_on=date(2026, 11, 21),
            images=False,
            disclosures=feed,
        )
    return target


@pytest.fixture
async def out(tmp_path):
    return await _build(tmp_path, feed=_feed())


def md(out, code: str, alias: bool = False) -> str:
    path = out / "c" / (f"{code}.md" if alias else f"{code}/index.md")
    return path.read_text(encoding="utf-8")


def table_rows(text: str) -> dict[str, list[str]]:
    """지표 표의 행 — 지표 이름 → 칸."""
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("| ") and not line.startswith(("| 지표", "|---")):
            cells = [c.strip() for c in line.strip("|").split(" | ")]
            rows[cells[0]] = cells
    return rows


async def test_every_company_gets_markdown_and_an_alias(out):
    for corp in (SAMSUNG, KB, HLB):
        text = md(out, corp.stock_code)
        assert text.startswith(f"# {corp.corp_name} ({corp.stock_code})")
        assert md(out, corp.stock_code, alias=True) == text, "절대 base 면 별칭과 같은 글"


async def test_markdown_has_the_sections_disclaimer_and_no_banned_phrases(out):
    for corp in (SAMSUNG, KB, HLB):
        text = md(out, corp.stock_code)
        for heading in SECTIONS:
            assert heading in text, (corp.stock_code, heading)
        assert DISCLAIMER in text
        assert banned.find_outside_allowed(text, [corp.corp_name]) == []
        assert check_file(f"c/{corp.stock_code}/index.md", text.encode()) == []


async def test_markdown_stays_compact(out):
    for corp in (SAMSUNG, KB, HLB):
        size = len(md(out, corp.stock_code).encode("utf-8"))
        assert size < 8_000, (corp.stock_code, size)


async def test_metric_table_has_every_metric_with_formula_and_threshold(out):
    rows = table_rows(md(out, "005930"))
    assert len(rows) == len(engine.METRIC_DEFS)
    roe_def = next(d for d in engine.METRIC_DEFS if d.key == "roe")
    roe = rows[roe_def.label]
    assert roe_def.formula.replace("|", "\\|") in roe
    assert f"{roe_def.band.good:g}%" in roe[-1] and f"{roe_def.band.warn:g}%" in roe[-1]


async def test_missing_is_no_data_never_zero_and_na_names_what_to_see(out):
    fcf = table_rows(md(out, "005930"))["잉여현금흐름 (FCF)"]
    assert fcf[1] == "⚫ 데이터 없음"
    assert fcf[2] == "—"
    kb = md(out, "105560")
    debt = table_rows(kb)["부채비율"]
    assert debt[1] == "⊘ 해당 없음"
    assert "BIS" in kb, "⊘ 는 대신 볼 것을 적는다"


async def test_recent_disclosures_are_listed_with_the_dart_link(out):
    text = md(out, "005930")
    assert "2026-11-20" in text and "유상증자결정" in text
    assert "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261120000001" in text
    # 공시가 없는 회사는 '없음(안전)' 으로 읽히지 않게 — 목록에 없을 뿐이라고 적는다.
    assert "빠진 공시가 있을 수 있" in md(out, "028300")


async def test_markdown_points_to_its_sources(out):
    text = md(out, "005930")
    assert f"{BASE}c/005930/" in text
    assert f"{BASE}api/c/005930.json" in text
    assert "https://dart.fss.or.kr/" in text
    assert "crtfc_key" not in text


async def test_html_page_announces_the_markdown_alternate(out):
    page = (out / "c" / "005930" / "index.html").read_text(encoding="utf-8")
    assert '<link rel="alternate" type="text/markdown"' in page
    assert 'href="index.md"' in page
    assert check_links(out, BASE).broken == []
    assert check_site(out) == []


async def test_relative_base_markdown_links_resolve(tmp_path):
    out = await _build(tmp_path, base_url="/", name="rel")
    assert check_links(out, "/").broken == []
    assert "](../../c/005930/)" in md(out, "005930")


def _json(out, code):
    return json.loads((out / "api" / "c" / f"{code}.json").read_text(encoding="utf-8"))


async def test_json_carries_summary_and_agent_guidance_without_breaking_v1(out):
    for corp in (SAMSUNG, KB, HLB):
        doc = _json(out, corp.stock_code)
        assert doc["schema_version"] == SCHEMA_VERSION == 1, "필드를 더하는 것만으로는 판을 올리지 않는다"
        assert doc["agent_guidance"] == AGENT_GUIDANCE
        summary = doc["summary_ko"]
        assert corp.corp_name in summary and corp.stock_code in summary
        assert "2024" in summary
        assert banned.find_outside_allowed(summary, [corp.corp_name]) == []
        assert "투자 판단이 아닙니다" in summary


async def test_summary_counts_match_the_metrics(out):
    doc = _json(out, "005930")
    reds = [m["label"] for m in doc["metrics"] if m["signal"]["key"] == "red"]
    greens = sum(1 for m in doc["metrics"] if m["signal"]["key"] == "green")
    assert f"🟢 정상 {greens}개" in doc["summary_ko"]
    for label in reds:
        assert label in doc["summary_ko"]
    assert "데이터 없음" in doc["summary_ko"] and "잉여현금흐름 (FCF)" in doc["summary_ko"]


async def test_summary_for_a_financial_company_names_the_not_applicable_metrics(out):
    doc = _json(out, "105560")
    na = [m["label"] for m in doc["metrics"] if m["signal"]["key"] == "not_applicable"]
    assert na
    assert "해당 없음" in doc["summary_ko"]
    for label in na:
        assert label in doc["summary_ko"]


def test_agent_guidance_holds_the_red_lines():
    for must in ("formula", "threshold", "⚫", "⊘", "disclaimer", "buy, sell or hold"):
        assert must in AGENT_GUIDANCE
    assert banned.find(AGENT_GUIDANCE) == []
