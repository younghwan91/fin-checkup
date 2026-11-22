"""MCP 가 개인 DART 인증키로 받아 오기 + 설치 도구(setup_status · setup_guide).

- 사이트에 있는 것은 사이트에서(DART 호출 0), 없는 사업연도·회사만 DART 에서 받아 `source: "dart-live"`.
- 일별 호출 한도를 넘길 것 같으면 부르지 않고, 020 이면 멈춘다.
- 인증키는 어떤 도구 결과·오류 문장에도 들어가지 않는다.
- setup_guide 는 클라이언트마다 설정법을 주되, 키를 채팅으로 받지 않게 한다.
"""

from __future__ import annotations

import io
import json
import tomllib
import zipfile
from pathlib import Path

import httpx
import pytest
import respx
from mcp import Client

from fin_checkup import onboarding
from fin_checkup.config import Settings
from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain.banned import find_outside_allowed
from fin_checkup.mcp import core, guide
from fin_checkup.mcp.client import SiteSource, SourceError, source_from_env
from fin_checkup.mcp.live import LIVE_NOTE, LIVE_WORST_CALLS, DartLiveSource
from fin_checkup.mcp.server import build_server
from fin_checkup.storage import Cache, today_key

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
SITE = "https://fin.example.test/fin-checkup/"
DART = "https://opendart.fss.or.kr/api"
FAKE_KEY = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
SITE_ROUTES = {
    "search-index.json": "search-index.json",
    "build.json": "build.json",
    "data/disclosures.json": "disclosures.json",
    "api/c/005930.json": "api_c_005930.json",
}
#: DART 고유번호 목록 — 삼성전자(사이트에 있음) · 새상장(사이트에 없음).
CORP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code>
        <modify_date>20250101</modify_date></list>
  <list><corp_code>01999999</corp_code><corp_name>새상장테크</corp_name><stock_code>499990</stock_code>
        <modify_date>20250101</modify_date></list>
  <list><corp_code>00111111</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code>
        <modify_date>20250101</modify_date></list>
</result>"""
DATA_YEARS = {2018, 2019, 2020}


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def corp_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", CORP_XML.encode("utf-8"))
    return buf.getvalue()


def statements_payload(year: int) -> dict:
    def item(sj, aid, nm, amount, order):
        return {
            "sj_div": sj,
            "account_id": aid,
            "account_nm": nm,
            "thstrm_amount": str(amount),
            "ord": str(order),
            "currency": "KRW",
            "rcept_no": f"{year + 1}0310000001",
        }

    revenue = 1000 + (year - 2018) * 100
    return {
        "status": "000",
        "message": "정상",
        "list": [
            item("BS", "ifrs-full_Assets", "자산총계", 10000, 1),
            item("BS", "ifrs-full_Liabilities", "부채총계", 4000, 2),
            item("BS", "ifrs-full_Equity", "자본총계", 6000, 3),
            item("BS", "ifrs-full_CurrentAssets", "유동자산", 5000, 4),
            item("BS", "ifrs-full_CurrentLiabilities", "유동부채", 2000, 5),
            item("IS", "ifrs-full_Revenue", "매출액", revenue, 6),
            item("IS", "dart_OperatingIncomeLoss", "영업이익", revenue // 10, 7),
            item("IS", "ifrs-full_ProfitLoss", "당기순이익", revenue // 12, 8),
        ],
    }


def statements_route(request: httpx.Request) -> httpx.Response:
    year = int(request.url.params["bsns_year"])
    if year in DATA_YEARS and request.url.params["fs_div"] == "CFS":
        return httpx.Response(200, json=statements_payload(year))
    return httpx.Response(200, json={"status": "013", "message": "조회된 데이타가 없습니다."})


@pytest.fixture
def web():
    """사이트(픽스처 JSON) + DART(고유번호·재무제표·기업개황)."""
    with respx.mock(assert_all_called=False) as router:
        for path, name in SITE_ROUTES.items():
            router.get(SITE + path).mock(return_value=httpx.Response(200, json=_fixture(name)))
        router.get(url__regex=rf"^{SITE}.*").mock(return_value=httpx.Response(404))
        router.get(f"{DART}/corpCode.xml", name="corp").mock(
            return_value=httpx.Response(200, content=corp_zip())
        )
        router.get(f"{DART}/fnlttSinglAcntAll.json", name="fs").mock(side_effect=statements_route)
        router.get(f"{DART}/company.json", name="company").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "000",
                    "corp_name": "삼성전자",
                    "stock_code": "005930",
                    "corp_cls": "Y",
                    "induty_code": "264",
                    "acc_mt": "12",
                },
            )
        )
        yield router


def key_settings(tmp_path, key: str = FAKE_KEY) -> Settings:
    return Settings(
        _env_file=None, dart_api_key=key, dart_min_delay=0.0, fin_checkup_db_path=tmp_path / "unused.duckdb"
    )


def dart_calls(web) -> int:
    return sum(r.call_count for r in web.routes if str(r.name) in {"corp", "fs", "company"})


@pytest.fixture
async def hybrid(web, tmp_path):
    src = DartLiveSource(SiteSource(SITE, cache_dir=None), key_settings(tmp_path), tmp_path / "dart.duckdb")
    yield src
    await src.aclose()


def assert_no_key(obj) -> None:
    assert FAKE_KEY not in json.dumps(obj, ensure_ascii=False)


# ── 출처 고르기 ───────────────────────────────────────────────────


def test_source_mode_follows_the_key_and_the_env(tmp_path):
    env = {"FIN_CHECKUP_MCP_CACHE_DIR": str(tmp_path)}
    no_key = Settings(_env_file=None, dart_api_key="")
    assert source_from_env(env, settings=no_key).mode == "site"
    with_key = source_from_env(env, settings=key_settings(tmp_path))
    assert with_key.mode == "site+dart"
    assert Path(with_key.db_path) == tmp_path / "dart.duckdb"
    assert (
        source_from_env({**env, "FIN_CHECKUP_MCP_SOURCE": "site"}, settings=key_settings(tmp_path)).mode
        == "site"
    )
    assert source_from_env({**env, "FIN_CHECKUP_MCP_SOURCE": "dart"}, settings=no_key).mode == "dart"
    off = source_from_env({"FIN_CHECKUP_MCP_CACHE_DIR": "off"}, settings=key_settings(tmp_path))
    assert off.site.cache_dir is None and Path(off.db_path).name == "dart.duckdb"


def test_the_mcp_server_reads_the_key_from_the_user_config_file(tmp_path, monkeypatch):
    """`fin-checkup setup` 이 넣은 파일을 uvx 로 띄운 서버가 그대로 읽는다.

    클라이언트 설정에 키가 필요 없다."""
    monkeypatch.setenv("FIN_CHECKUP_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    onboarding.update_env_file(onboarding.user_env_file(), {"DART_API_KEY": FAKE_KEY})
    assert source_from_env({"FIN_CHECKUP_MCP_CACHE_DIR": str(tmp_path / "c")}).mode == "site+dart"


# ── site+dart: 사이트 먼저, 없는 것만 DART ─────────────────────────


async def test_the_site_year_is_served_from_the_site_without_calling_dart(hybrid, web):
    out = await core.diagnose_company(hybrid, "삼성전자")
    assert out["status"] == "ok" and out["fiscal_year"] == 2025
    assert out["source"] == "site" and out["source_mode"] == "site+dart"
    assert dart_calls(web) == 0


async def test_a_year_not_on_the_site_is_fetched_live(hybrid, web, tmp_path):
    out = await core.diagnose_company(hybrid, "삼성전자", year=2020)
    assert out["status"] == "ok"
    assert out["source"] == "dart-live" and out["fiscal_year"] == 2020
    assert out["years_in_data"] == [2018, 2019, 2020]
    assert "dart-live" in out["live_note"]
    assert out["disclaimer"] == DISCLAIMER
    assert any("opendart" in s.get("url", "") for s in out["sources"])
    assert all("crtfc_key" not in json.dumps(s) for s in out["sources"])
    assert_no_key(out)
    # 2016~2020: 연결 5번 + (없는 해) 개별 2번 + 고유번호 1 + 기업개황 1
    assert web["fs"].call_count == 7 and web["corp"].call_count == 1 and web["company"].call_count == 1
    with Cache(tmp_path / "dart.duckdb", read_only=True) as cache:
        assert cache.dart_calls_today(today_key()) == dart_calls(web)
        assert cache.statement_years() == [2018, 2019, 2020]
    # 두 번째는 개인 캐시에서 — 새로 부르지 않는다
    before = dart_calls(web)
    again = await core.diagnose_company(hybrid, "005930", year=2020)
    assert again["fiscal_year"] == 2020 and dart_calls(web) == before


async def test_a_company_missing_from_the_site_is_found_and_fetched(hybrid, web):
    found = await core.search_companies(hybrid, "새상장")
    assert [r["stock_code"] for r in found["results"]] == ["499990"]
    out = await core.diagnose_company(hybrid, "499990", year=2019)
    assert out["status"] == "ok" and out["source"] == "dart-live"
    assert out["company"]["page_url"] is None  # 사이트에 없는 페이지를 출처로 주지 않는다
    assert all(SITE not in s.get("url", "") for s in out["sources"])


async def test_a_year_dart_does_not_have_is_no_data_not_invented(hybrid, web):
    out = await core.diagnose_company(hybrid, "삼성전자", year=2016)
    assert out["status"] == "no_data" and "추정해 채우지 않습니다" in out["message"]
    too_old = await core.diagnose_company(hybrid, "삼성전자", year=2009)
    assert too_old["status"] == "no_data"


async def test_quota_guard_stops_before_calling_dart(hybrid, web, tmp_path):
    with Cache(tmp_path / "dart.duckdb") as cache:
        cache.record_dart_call("fnlttSinglAcntAll.json", today_key(), count=20_000 - LIVE_WORST_CALLS + 1)
    with pytest.raises(SourceError, match="한도"):
        await core.diagnose_company(hybrid, "삼성전자", year=2020)
    assert dart_calls(web) == 0


async def test_dart_quota_status_020_stops_without_retrying(hybrid, web):
    web["fs"].mock(
        return_value=httpx.Response(200, json={"status": "020", "message": "요청 제한을 초과하였습니다."})
    )
    with pytest.raises(SourceError, match="020") as exc:
        await core.diagnose_company(hybrid, "삼성전자", year=2020)
    assert FAKE_KEY not in str(exc.value)
    assert web["fs"].call_count == 1


async def test_invalid_key_points_to_setup_and_hides_the_key(hybrid, web):
    web["corp"].mock(
        return_value=httpx.Response(200, json={"status": "010", "message": "등록되지 않은 키입니다."})
    )
    with pytest.raises(SourceError, match="setup --check") as exc:
        await core.diagnose_company(hybrid, "삼성전자", year=2020)
    assert FAKE_KEY not in str(exc.value)


async def test_network_errors_never_carry_the_request_url(hybrid, web, monkeypatch):
    from fin_checkup.dart import client as dart_client
    from fin_checkup.dart.retry import RetryPolicy

    monkeypatch.setattr(dart_client, "RetryPolicy", lambda: RetryPolicy(base_delay=0.0, jitter=0.0))
    web["corp"].mock(side_effect=httpx.ConnectError(f"https://x/?crtfc_key={FAKE_KEY}"))
    with pytest.raises(SourceError) as exc:
        await core.diagnose_company(hybrid, "삼성전자", year=2020)
    assert FAKE_KEY not in str(exc.value) and "ConnectError" in str(exc.value)


async def test_dart_mode_without_a_key_says_to_run_setup(web, tmp_path):
    src = DartLiveSource(
        SiteSource(SITE, cache_dir=None),
        Settings(_env_file=None, dart_api_key=""),
        tmp_path / "d.duckdb",
        always_live=True,
    )
    with pytest.raises(SourceError, match="fin-checkup setup"):
        await core.diagnose_company(src, "삼성전자")
    assert dart_calls(web) == 0
    await src.aclose()


async def test_over_mcp_the_live_result_and_errors_never_contain_the_key(hybrid, web):
    async with Client(build_server(hybrid)) as client:
        ok = await client.call_tool("diagnose_company", {"company": "삼성전자", "year": 2020})
        assert not ok.is_error and ok.structured_content["source"] == "dart-live"
        assert FAKE_KEY not in ok.content[0].text
        status = await client.call_tool("setup_status", {})
        assert FAKE_KEY not in status.content[0].text
        web["fs"].mock(return_value=httpx.Response(200, json={"status": "020", "message": "요청 제한"}))
        bad = await client.call_tool("diagnose_company", {"company": "삼성전자", "year": 2019})
        assert bad.is_error and FAKE_KEY not in bad.content[0].text


# ── setup_status ─────────────────────────────────────────────────


async def test_setup_status_without_a_key(web, tmp_path, monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    site = SiteSource(SITE, cache_dir=None)
    out = await core.setup_status(site)
    assert out["source_mode"] == "site" and out["site"]["reachable"] is True
    assert out["site"]["fiscal_year"] == 2025
    assert out["dart_key"] == {"configured": False, "from": None, "checked": None}
    assert out["live_fetch"] is False
    assert any("setup_guide" in step for step in out["next_steps"])
    await site.aclose()


async def test_setup_status_with_a_key_says_where_from_and_can_check_it(hybrid, web, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", FAKE_KEY)
    out = await core.setup_status(hybrid)
    assert out["dart_key"]["configured"] is True and out["dart_key"]["from"] == "환경변수 DART_API_KEY"
    assert out["live_fetch"] is True and out["source_mode"] == "site+dart"
    assert out["cache"]["fiscal_years"] == []
    assert web["company"].call_count == 0  # 묻지 않으면 DART 를 부르지 않는다
    checked = await core.setup_status(hybrid, check_key=True)
    assert checked["dart_key"]["checked"] == "ok" and web["company"].call_count == 1
    assert_no_key(out)
    assert_no_key(checked)


# ── setup_guide ──────────────────────────────────────────────────


@pytest.mark.parametrize("client", sorted(guide.CLIENTS))
def test_every_client_guide_has_install_verify_and_key_steps(client):
    out = guide.setup_guide(client, want_key=True)
    assert out["status"] == "ok" and out["client"] == client
    titles = " ".join(step["title"] for step in out["steps"])
    assert "uv" in titles and "setup_status" in titles and "diagnose_company" in titles
    assert any(step.get("ask_first") for step in out["steps"])
    key = out["key"]
    assert key["open_url"] == onboarding.KEY_APPLY_URL
    assert key["store"]["user_runs"] is True and "setup" in key["store"]["command"]
    assert key["env_alternative"] is not None
    assert out["disclaimer"] == DISCLAIMER
    assert any("채팅" in rule for rule in out["rules_for_agent"])


def test_config_snippets_parse_and_launch_the_same_server():
    for name, info in guide.CLIENTS.items():
        blocks = [s.get("snippet") for s in info["install"]] + [info["env_alternative"].get("snippet")]
        for snippet in filter(None, blocks):
            if name == "codex":
                cfg = tomllib.loads(snippet)["mcp_servers"]["fin-checkup"]
            else:
                parsed = json.loads(snippet)
                cfg = (parsed.get("mcpServers") or parsed.get("servers"))["fin-checkup"]
            if "command" in cfg:
                assert cfg["command"] == "uvx" and cfg["args"] == guide.MCP_ARGS, name
            if "env" in cfg:
                assert set(cfg["env"]) == {"DART_API_KEY"}


def test_claude_desktop_lists_windows_and_macos_paths_and_vscode_asks_with_a_password_box():
    desktop = guide.setup_guide("Claude Desktop")
    assert "%APPDATA%\\Claude\\claude_desktop_config.json" in desktop["config_paths"]["windows"]
    assert desktop["config_paths"]["macos"].startswith("~/Library/Application Support/Claude/")
    vscode = json.loads(guide.CLIENTS["vscode"]["env_alternative"]["snippet"])
    assert vscode["inputs"][0]["password"] is True
    assert vscode["servers"]["fin-checkup"]["env"]["DART_API_KEY"] == "${input:dart-api-key}"


def test_claude_code_uses_the_documented_add_command():
    out = guide.setup_guide("claude code", want_key=True)
    commands = [s.get("command", "") for s in out["steps"]]
    assert guide.CLAUDE_CODE_ADD in commands
    assert (
        out["key"]["store"]["command"].startswith("! ")
        and "--from-clipboard" in out["key"]["store"]["command"]
    )


@pytest.mark.parametrize(
    ("alias", "name"),
    [
        ("VS Code", "vscode"),
        ("copilot", "vscode"),
        ("Gemini CLI", "gemini"),
        ("codex", "codex"),
        ("cursor", "cursor"),
        ("desktop", "claude-desktop"),
    ],
)
def test_client_aliases(alias, name):
    assert guide.normalize_client(alias) == name


def test_unknown_or_missing_client_lists_the_choices():
    assert guide.setup_guide(None)["status"] == "choose_client"
    odd = guide.setup_guide("emacs", want_key=True)
    assert odd["status"] == "choose_client" and "claude-code" in odd["clients"] and "key" in odd


def test_guide_never_contains_a_key_and_has_no_banned_phrases():
    outputs = [guide.setup_guide(c, want_key=w) for c in [None, "x", *guide.CLIENTS] for w in (False, True)]
    from fin_checkup.explain import banned

    for text in banned.iter_strings(outputs):
        assert find_outside_allowed(text) == [], text
        assert FAKE_KEY not in text
    assert find_outside_allowed(LIVE_NOTE) == []
