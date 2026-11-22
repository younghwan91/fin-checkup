"""`docs/agents.md` — MCP 설치 안내가 실제 서버와 같은 말을 하는지.

설치 명령은 플러그인 `.mcp.json` 과 같아야 하고(복사한 사람의 설정이 깨지지 않게), JSON·TOML 예시는
그대로 붙여 넣어도 읽혀야 하며, 도구 표는 서버가 실제로 내는 도구와 같아야 한다. 금지 표현도 본다.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain.banned import find_outside_allowed
from fin_checkup.mcp.client import SiteSource
from fin_checkup.mcp.server import build_server

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "agents.md"
FROM = "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup"
COMMAND = f'uvx --from "{FROM}" fin-checkup-mcp'
ARGS = ["--from", FROM, "fin-checkup-mcp"]


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _blocks(lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", _doc(), flags=re.S)


def test_install_command_matches_the_plugin_and_the_console_script():
    doc = _doc()
    assert COMMAND in doc
    assert f"claude mcp add fin-checkup -- {COMMAND}" in doc
    plugin = json.loads((ROOT / "plugins" / "fin-checkup" / ".mcp.json").read_text(encoding="utf-8"))
    assert plugin["mcpServers"]["fin-checkup"]["args"] == ARGS
    assert 'fin-checkup-mcp = "fin_checkup.mcp.server:main"' in (ROOT / "pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_json_and_toml_snippets_parse_and_launch_the_same_command():
    configs = [json.loads(b) for b in _blocks("json")]
    assert len(configs) == 2  # Claude Desktop(= Cursor) · VS Code
    for cfg in configs:
        server = (cfg.get("mcpServers") or cfg.get("servers"))["fin-checkup"]
        assert server["command"] == "uvx" and server["args"] == ARGS
    (toml,) = _blocks("toml")
    server = tomllib.loads(toml)["mcp_servers"]["fin-checkup"]
    assert server["command"] == "uvx" and server["args"] == ARGS


async def test_tool_table_lists_exactly_the_servers_tools():
    from mcp import Client

    documented = set(re.findall(r"^\| `(\w+)\(", _doc(), flags=re.M))
    async with Client(build_server(SiteSource(cache_dir=None))) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
    assert documented == tools


def test_doc_keeps_the_red_lines_and_the_disclaimer():
    doc = _doc()
    assert DISCLAIMER in doc
    assert find_outside_allowed(doc) == []
    for target in re.findall(r"\]\(((?!https?:|#)[^)\s#]+)", doc):
        assert (DOC.parent / target).exists(), target


def test_readme_and_skill_doc_link_here():
    assert "(docs/agents.md)" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "(agents.md)" in (ROOT / "docs" / "agents-skill.md").read_text(encoding="utf-8")
