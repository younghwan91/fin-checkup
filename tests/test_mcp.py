"""MCP 서버 — 출처(사이트 JSON·캐시·로컬 DB), 도구 결과, MCP 배선, 넘지 않는 선.

HTTP 는 respx 로 막고 실제 사이트에서 복사한 JSON(`tests/fixtures/mcp/`)을 돌려준다 — 삼성전자(일반, 🟢·⚪),
KB금융(금융업, ⚫·⊘). 네트워크 없이 돈다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import respx
from hub_seed import seed_site_cache
from mcp import Client, StdioServerParameters

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain import banned
from fin_checkup.mcp import core
from fin_checkup.mcp.client import LocalSource, NotFound, SiteSource, SourceError, source_from_env
from fin_checkup.mcp.server import build_server
from fin_checkup.metrics import engine
from fin_checkup.storage import Cache

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
BASE = "https://fin.example.test/fin-checkup/"
ROUTES = {
    "search-index.json": "search-index.json",
    "build.json": "build.json",
    "data/disclosures.json": "disclosures.json",
    "api/c/005930.json": "api_c_005930.json",
    "api/c/105560.json": "api_c_105560.json",
}
TOOLS = {
    "search_companies",
    "diagnose_company",
    "explain_metric",
    "compare_companies",
    "recent_risk_disclosures",
    "data_status",
    "setup_status",
    "setup_guide",
}


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def site():
    """사이트 전체를 흉내 내는 respx 라우터. 없는 경로는 404."""
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        for path, name in ROUTES.items():
            router.get(path, name=path).mock(
                return_value=httpx.Response(200, json=fixture(name), headers={"ETag": f'"{name}"'})
            )
        router.get(url__regex=r".*").mock(return_value=httpx.Response(404))
        yield router


@pytest.fixture
async def source(site, tmp_path):
    src = SiteSource(BASE, cache_dir=tmp_path / "cache")
    yield src
    await src.aclose()


def proper_nouns() -> list[str]:
    """데이터(회사 이름·공시 제목)는 우리가 쓴 문장이 아니다 — 금지 표현 검사에서 걷어낸다."""
    names = [c["name"] for c in fixture("search-index.json")["companies"]]
    titles = [i["report_nm"] for i in fixture("disclosures.json")["items"]]
    corp = [i["corp_name"] for i in fixture("disclosures.json")["items"]]
    return names + titles + corp


# ── 출처: 캐시·ETag·장애 ───────────────────────────────────────────


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


async def test_site_source_caches_in_memory_then_revalidates_with_etag(tmp_path):
    clock = Clock()
    with respx.mock(base_url=BASE) as router:
        route = router.get("build.json").mock(
            side_effect=[
                httpx.Response(200, json={"fiscal_year": 2025}, headers={"ETag": '"v1"'}),
                httpx.Response(304),
            ]
        )
        src = SiteSource(BASE, cache_dir=None, ttl=600, clock=clock)
        first = await src.build_info()
        again = await src.build_info()
        assert route.call_count == 1 and again.data == first.data == {"fiscal_year": 2025}

        clock.now += 601
        revalidated = await src.build_info()
        assert route.call_count == 2
        assert route.calls.last.request.headers["If-None-Match"] == '"v1"'
        assert revalidated.data == {"fiscal_year": 2025} and not revalidated.stale
        await src.aclose()


async def test_site_source_disk_cache_survives_a_restart(tmp_path):
    with respx.mock(base_url=BASE) as router:
        route = router.get("build.json").mock(return_value=httpx.Response(200, json={"a": 1}))
        first = SiteSource(BASE, cache_dir=tmp_path)
        await first.build_info()
        await first.aclose()
        second = SiteSource(BASE, cache_dir=tmp_path)
        assert (await second.build_info()).data == {"a": 1}
        assert route.call_count == 1
        await second.aclose()


async def test_network_failure_uses_stale_copy_and_says_so(tmp_path):
    clock = Clock()
    with respx.mock(base_url=BASE) as router:
        router.get("build.json").mock(
            side_effect=[httpx.Response(200, json={"a": 1}), httpx.ConnectError("down")]
        )
        src = SiteSource(BASE, cache_dir=None, ttl=10, clock=clock)
        await src.build_info()
        clock.now += 11
        stale = await src.build_info()
        assert stale.stale and stale.data == {"a": 1}
        await src.aclose()


async def test_network_failure_without_cache_is_a_clear_error_not_a_guess():
    with respx.mock(base_url=BASE) as router:
        router.get("build.json").mock(side_effect=httpx.ConnectError("down"))
        src = SiteSource(BASE, cache_dir=None)
        with pytest.raises(SourceError, match="받지 못했습니다") as err:
            await src.build_info()
        assert "추정해 채우지 않습니다" in str(err.value)
        await src.aclose()


async def test_missing_company_file_is_not_found_not_network_error(source):
    with pytest.raises(NotFound):
        await source.company("999999")


def test_source_from_env_defaults_to_the_live_site_and_local_on_request(tmp_path):
    from fin_checkup.config import Settings

    no_key = Settings(_env_file=None, dart_api_key="")
    site = source_from_env({"FIN_CHECKUP_MCP_CACHE_DIR": str(tmp_path)}, settings=no_key)
    assert site.mode == "site" and site.base_url == "https://younghwan91.github.io/fin-checkup/"
    local = source_from_env(
        {"FIN_CHECKUP_MCP_SOURCE": "local", "FIN_CHECKUP_DB_PATH": str(tmp_path / "x.duckdb")}
    )
    assert local.mode == "local"
    off = source_from_env({"FIN_CHECKUP_MCP_CACHE_DIR": "off"}, settings=no_key)
    assert off.cache_dir is None


# ── 회사 찾기 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "code"),
    [("삼성전자", "005930"), ("005930", "005930"), ("ㅅㅅㅈㅈ", "005930"), ("kb금융", "105560")],
)
async def test_search_by_name_code_and_initials(source, query, code):
    out = await core.search_companies(source, query)
    assert out["results"][0]["stock_code"] == code
    assert out["results"][0]["page_url"] == f"{BASE}c/{code}/"
    assert out["disclaimer"] == DISCLAIMER


async def test_search_orders_by_match_then_name_not_by_metric(source):
    out = await core.search_companies(source, "삼성", limit=50)
    names = [r["name"] for r in out["results"]]
    assert names == sorted(names)  # 전부 접두 일치(같은 순위) → 이름순
    assert "지표" in out["note"]


async def test_empty_search_is_an_input_error(source):
    with pytest.raises(core.ToolInputError):
        await core.search_companies(source, "  ")


# ── 진단 ─────────────────────────────────────────────────────────


async def test_diagnose_samsung_returns_every_metric_with_formula_threshold_and_why(source):
    out = await core.diagnose_company(source, "삼성전자")
    assert out["status"] == "ok"
    assert out["company"]["stock_code"] == "005930"
    assert out["company"]["page_url"] == f"{BASE}c/005930/"
    assert [m["key"] for m in out["metrics"]] == [d.key for d in engine.METRIC_DEFS]
    for m in out["metrics"]:
        assert m["formula"] and m["threshold_text"] and m["signal"]
        assert m["why"], m["key"]
        assert m["explain_url"] == f"{BASE}m/{m['key']}/"
    op = next(m for m in out["metrics"] if m["key"] == "operating_margin")
    raw = next(m for m in fixture("api_c_005930.json")["metrics"] if m["key"] == "operating_margin")
    assert op["value"] == raw["value"] and op["value_text"] == raw["value_text"]
    assert op["signal"] == "🟢 정상" and op["peer_median"] == raw["peer"]["median_text"]
    assert op["last_year"]["previous"] == raw["delta"]["previous"]
    assert "7% 이상" in op["why"]  # 경계값 구간이 이유 문장에 그대로
    assert out["disclaimer"] == DISCLAIMER
    assert "disclaimer" in out["presentation_guidance"]
    assert any("opendart" in s["url"] for s in out["sources"])
    assert out["summary"][0].startswith("삼성전자(005930, KOSPI) 2025 사업연도")


async def test_diagnose_includes_the_companys_recent_risk_disclosures_as_facts(source):
    out = await core.diagnose_company(source, "005930")
    items = out["recent_risk_disclosures"]
    assert len(items) == 1
    assert items[0]["dart_url"].startswith("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=")
    assert items[0]["category_label"] and items[0]["what_this_type_is"]
    assert out["disclosure_window"] == {"begin": "2026-11-16", "end": "2026-11-22"}


async def test_diagnose_keeps_missing_as_none_and_not_applicable_with_alternative(source):
    out = await core.diagnose_company(source, "KB금융")
    by_key = {m["key"]: m for m in out["metrics"]}
    missing = by_key["operating_margin"]
    assert missing["signal"] == "⚫ 데이터 없음" and missing["value"] is None and missing["value_text"] == "—"
    assert "데이터 없음" in missing["why"] and "0으로 채우지" in missing["why"]
    na = by_key["current_ratio"]
    assert na["signal"] == "⊘ 해당 없음" and na["thresholds"] is None
    assert "대신 볼 것" in na["why"]
    assert any("⚫ 데이터 없음:" in f for f in out["summary"])


async def test_ambiguous_name_returns_candidates_not_a_guess(source):
    out = await core.diagnose_company(source, "삼성")
    assert out["status"] == "ambiguous"
    assert len(out["candidates"]) > 1 and "metrics" not in out


async def test_unknown_company_returns_not_found(source):
    out = await core.diagnose_company(source, "없는회사이름")
    assert out["status"] == "not_found" and out["candidates"] == []


async def test_a_year_the_site_does_not_carry_is_unavailable_not_invented(source):
    out = await core.diagnose_company(source, "삼성전자", year=2019)
    assert out["status"] == "year_unavailable"
    assert out["available_fiscal_year"] == 2025 and "metrics" not in out


async def test_diagnose_survives_a_missing_disclosure_feed(site, tmp_path):
    site.routes["data/disclosures.json"].mock(return_value=httpx.Response(404))
    src = SiteSource(BASE, cache_dir=None)
    out = await core.diagnose_company(src, "삼성전자")
    assert out["status"] == "ok" and out["recent_risk_disclosures"] is None
    assert "불러오지 못했습니다" in out["disclosures_note"]
    await src.aclose()


# ── 비교·해설·공시·상태 ────────────────────────────────────────────


async def test_compare_puts_companies_side_by_side_in_fixed_metric_order_without_a_winner(source):
    out = await core.compare_companies(source, ["KB금융", "삼성전자"])
    assert [c["stock_code"] for c in out["companies"]] == ["105560", "005930"]  # 입력 순서
    assert [r["key"] for r in out["metrics"]] == [d.key for d in engine.METRIC_DEFS]
    assert all(len(r["values"]) == 2 for r in out["metrics"])
    assert out["metrics"][0]["values"][0]["value"] is None  # KB 영업이익률 ⚫
    assert not {"winner", "best", "rank", "score"} & set(out)
    assert "우열" in out["note"]


@pytest.mark.parametrize("companies", [["삼성전자"], ["a", "b", "c", "d", "e"]])
async def test_compare_needs_two_to_four(source, companies):
    with pytest.raises(core.ToolInputError):
        await core.compare_companies(source, companies)


@pytest.mark.parametrize("query", ["부채비율", "debt_ratio", "부채 비율"])
def test_explain_metric_by_key_or_label(query):
    out = core.explain_metric(SiteSource(BASE, cache_dir=None), query)
    assert out["status"] == "ok" and out["key"] == "debt_ratio"
    assert out["formula"] and out["one_liner"] and out["pitfalls"]
    assert "⊘" in out["thresholds_by_sector"]["금융업"]
    assert out["not_applicable_in"][0]["instead"]
    assert out["page_url"] == f"{BASE}m/debt_ratio/"


def test_unknown_metric_lists_what_exists():
    out = core.explain_metric(SiteSource(BASE, cache_dir=None), "주가수익비율")
    assert out["status"] == "not_found" and len(out["available"]) == len(engine.METRIC_DEFS)


async def test_recent_risk_disclosures_market_wide_and_for_one_company(source):
    market = await core.recent_risk_disclosures(source)
    assert market["status"] == "ok" and market["count"] >= 1
    dates = [i["date"] for i in market["items"]]
    assert dates == sorted(dates, reverse=True)
    one = await core.recent_risk_disclosures(source, "삼성전자", days=7)
    assert one["company"]["stock_code"] == "005930" and one["count"] == 1
    narrow = await core.recent_risk_disclosures(source, "삼성전자", days=1)
    assert narrow["window"]["begin"] == "2026-11-22"


async def test_data_status_reports_build_and_fiscal_years(source):
    out = await core.data_status(source)
    assert out["fiscal_year"] == 2025 and out["fresh"] is True
    assert out["fiscal_years_in_data"] == [2021, 2022, 2023, 2024, 2025]
    assert out["source_mode"] == "site"


# ── 로컬 모드 (DuckDB 캐시, DART 호출 0) ──────────────────────────


async def test_local_source_builds_the_same_schema_from_the_cache(tmp_path):
    with Cache(tmp_path / "local.duckdb") as cache:
        seed_site_cache(cache)
        src = LocalSource(tmp_path / "local.duckdb", base_url=BASE, cache=cache)
        src._year = 2024  # 픽스처는 2022~2024
        found = await core.search_companies(src, "ㅅㅅㅈㅈ")
        assert found["results"][0]["stock_code"] == "005930"
        out = await core.diagnose_company(src, "삼성전자")
        assert out["status"] == "ok" and out["source_mode"] == "local"
        assert out["fiscal_year"] == 2024
        fcf = next(m for m in out["metrics"] if m["key"] == "fcf")
        assert fcf["signal"] == "⚫ 데이터 없음" and fcf["value"] is None  # CapEx 가 없다 → 0 이 아니라 ⚫
        assert out["recent_risk_disclosures"] is None and "로컬 모드" in out["disclosures_note"]
        old = await core.diagnose_company(src, "삼성전자", year=2023)
        assert old["fiscal_year"] == 2023


async def test_local_source_without_a_db_says_where_to_point():
    src = LocalSource("/nonexistent/fin.duckdb")
    with pytest.raises(SourceError, match="FIN_CHECKUP_DB_PATH"):
        await src.search_index()


# ── MCP 배선 (in-process client) ──────────────────────────────────


# Client 는 같은 태스크에서 열고 닫아야 한다(anyio 취소 범위) — async yield 픽스처로 두지 않는다.


async def test_server_lists_its_tools_each_saying_it_is_not_advice(source):
    async with Client(build_server(source)) as client:
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == TOOLS
        for t in tools:
            assert "투자권유·추천이 아닙니다." in t.description, t.name
            assert "buy, sell" in t.description, t.name
            assert t.annotations.read_only_hint is True
        assert "disclaimer" in client.instructions


async def test_every_tool_is_callable_over_mcp(source):
    async with Client(build_server(source)) as client:
        calls = {
            "search_companies": {"query": "ㅅㅅㅈㅈ"},
            "diagnose_company": {"company": "삼성전자"},
            "explain_metric": {"key_or_label": "ROE"},
            "compare_companies": {"companies": ["삼성전자", "KB금융"]},
            "recent_risk_disclosures": {"company": "삼성전자"},
            "data_status": {},
            "setup_status": {},
            "setup_guide": {"client": "claude-desktop", "want_key": True},
        }
        assert set(calls) == TOOLS
        for name, args in calls.items():
            result = await client.call_tool(name, args)
            assert not result.is_error, (name, result.content)
            assert result.structured_content["disclaimer"] == DISCLAIMER, name
            assert json.loads(result.content[0].text) == result.structured_content


async def test_bad_input_comes_back_as_a_tool_error(source):
    async with Client(build_server(source)) as c:
        bad = await c.call_tool("compare_companies", {"companies": ["삼성전자", "KB금융", "a", "b", "c"]})
        assert bad.is_error


async def test_network_failure_comes_back_as_a_clear_tool_error():
    with respx.mock(base_url=BASE) as router:
        router.get(url__regex=r".*").mock(side_effect=httpx.ConnectError("down"))
        offline = SiteSource(BASE, cache_dir=None)
        async with Client(build_server(offline)) as c:
            result = await c.call_tool("diagnose_company", {"company": "삼성전자"})
        assert result.is_error
        assert "받지 못했습니다" in result.content[0].text
        await offline.aclose()


async def test_diagnose_prompt_and_resources(source):
    async with Client(build_server(source)) as client:
        prompts = (await client.list_prompts()).prompts
        assert [p.name for p in prompts] == ["diagnose"]
        got = await client.get_prompt("diagnose", {"company": "삼성전자"})
        text = got.messages[0].content.text
        assert text.startswith("삼성전자 재무 진단") and "diagnose_company" in text and "disclaimer" in text
        uris = {str(r.uri) for r in (await client.list_resources()).resources}
        assert uris == {"fin-checkup://legend", "fin-checkup://policy", "fin-checkup://metrics"}
        legend = (await client.read_resource("fin-checkup://legend")).contents[0].text
        assert all(sym in legend for sym in "🟢🟡🔴⚪⊘⚫") and DISCLAIMER in legend
        policy = (await client.read_resource("fin-checkup://policy")).contents[0].text
        assert DISCLAIMER in policy


# ── 넘지 않는 선: 금지 표현 ───────────────────────────────────────


async def test_no_banned_phrase_in_descriptions_prompts_resources_or_outputs(source):
    async with Client(build_server(source)) as client:
        texts: list[object] = [client.instructions]
        for t in (await client.list_tools()).tools:
            texts += [t.title, t.description, json.dumps(t.input_schema, ensure_ascii=False)]
        for p in (await client.list_prompts()).prompts:
            texts += [p.title, p.description]
        texts.append((await client.get_prompt("diagnose", {"company": "삼성전자"})).messages[0].content.text)
        for r in (await client.list_resources()).resources:
            texts += [r.title, r.description, (await client.read_resource(str(r.uri))).contents[0].text]
        outputs = [
            await core.search_companies(source, "삼성", limit=50),
            await core.diagnose_company(source, "삼성전자"),
            await core.diagnose_company(source, "KB금융"),
            await core.diagnose_company(source, "삼성"),
            await core.diagnose_company(source, "삼성전자", year=2019),
            await core.compare_companies(source, ["삼성전자", "KB금융"]),
            await core.recent_risk_disclosures(source),
            await core.data_status(source),
            await core.setup_status(source),
            *[core.explain_metric(source, d.key) for d in engine.METRIC_DEFS],
        ]
        assert core.banned_in(texts) == []
        assert core.banned_in(outputs, proper_nouns()) == []
        assert banned.find_outside_allowed(core.PRESENTATION_GUIDANCE) == []


# ── 실제 stdio 로 (로컬 HTTP 서버가 사이트 흉내) ────────────────────


@pytest.fixture
def local_site(tmp_path):
    root = tmp_path / "site"
    for path, name in ROUTES.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((FIXTURES / name).read_bytes())

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):  # stderr 를 어지럽히지 않게
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(root)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()


async def test_console_script_speaks_mcp_over_stdio(local_site, tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "fin_checkup.mcp.server"],
        env={**os.environ, "FIN_CHECKUP_MCP_BASE_URL": local_site, "FIN_CHECKUP_MCP_CACHE_DIR": "off"},
    )
    async with Client(params) as c:
        result = await c.call_tool("diagnose_company", {"company": "삼성전자"})
    assert not result.is_error
    assert result.structured_content["company"]["stock_code"] == "005930"
    assert len(result.structured_content["metrics"]) == len(engine.METRIC_DEFS)


def test_console_script_is_declared():
    text = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'fin-checkup-mcp = "fin_checkup.mcp.server:main"' in text
    out = subprocess.run(
        [sys.executable, "-m", "fin_checkup.mcp.server", "--help"], capture_output=True, text=True, check=True
    )
    assert "fin-checkup-mcp" in out.stdout and "투자권유·추천이 아닙니다." in out.stdout
