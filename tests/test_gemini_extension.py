"""Gemini CLI 확장 — 저장소 루트의 `gemini-extension.json` 과 그 문맥 파일이 지키는 것.

`gemini extensions install https://github.com/younghwan91/fin-checkup` 은 저장소를 통째로 받아 루트의
매니페스트를 읽고, 확장 갤러리(geminicli.com/extensions)는 `gemini-cli-extension` 토픽이 붙은 공개 저장소의
루트 매니페스트를 매일 긁어 간다. 그래서 여기서 막는 것:

- MCP 명령이 다른 설치 경로(플러그인 `.mcp.json`·README 배지·`setup_guide`)와 어긋나는 것
  — 기준은 `guide.MCP_ARGS`.
- 버전이 릴리스 태그와 어긋나는 것 — `scripts/release.py check-tag` 가 이 파일도 본다.
- 문맥 파일(GEMINI.md)이 측정 도구의 선을 놓치는 것 — Gemini 는 이 파일을 모든 대화에 붙인다.

형식은 https://geminicli.com/docs/extensions/reference/ 를 따른다. 실제 검증은 Node 가 필요해
CI 에 없고 로컬에서 `npx @google/gemini-cli extensions validate .` 로 돌린다(로그인 불필요).
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from fin_checkup import __version__
from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain.banned import find_outside_allowed
from fin_checkup.mcp import guide

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "gemini-extension.json"
INSTALL = "gemini extensions install https://github.com/younghwan91/fin-checkup"

_spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


@pytest.fixture(scope="module")
def ext() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_names_the_extension_for_the_gallery(ext):
    # 이름은 설치 폴더 이름이 된다 — 소문자·숫자·대시만.
    assert ext["name"] == "fin-checkup"
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", ext["name"])
    description = ext["description"]
    assert 1 <= len(description) <= 200
    assert find_outside_allowed(description) == []
    assert not re.search(r"\b(buy|sell|recommend\w*|top)\b", description, re.I)
    assert "not investment advice" in description


def test_version_matches_pyproject_and_the_release_tag_check(ext):
    assert ext["version"] == __version__
    assert release.versions()["gemini-extension.json"] == __version__


def test_mcp_server_runs_the_same_command_as_every_other_install_path(ext):
    assert ext["mcpServers"] == {"fin-checkup": {"command": "uvx", "args": guide.MCP_ARGS}}
    plugin = json.loads((ROOT / "plugins" / "fin-checkup" / ".mcp.json").read_text(encoding="utf-8"))
    assert ext["mcpServers"] == plugin["mcpServers"]


def test_dart_key_is_an_optional_sensitive_setting(ext):
    # Gemini 는 확장 MCP 서버에 셸 환경변수를 넘기지 않는다 — 선언한 것(settings)과 HOME·PATH 정도만.
    # 비워 두면 변수가 아예 설정되지 않아 `fin-checkup setup` 이 넣은 사용자 설정 파일을 그대로 읽는다.
    [setting] = ext["settings"]
    assert setting["envVar"] == "DART_API_KEY"
    assert setting["sensitive"] is True  # 키체인에 저장, 화면에선 가린다
    assert "fin-checkup setup" in setting["description"]
    for text in (setting["name"], setting["description"]):
        assert find_outside_allowed(text) == [], text
    assert "env" not in ext["mcpServers"]["fin-checkup"]  # 키를 매니페스트에 적지 않는다


def test_context_file_states_the_measurement_only_rules(ext):
    context = ROOT / ext["contextFileName"]
    text = context.read_text(encoding="utf-8")
    assert DISCLAIMER in text
    for rule in ("계산식", "경계값", "⚫", "⊘", "setup_status", "setup_guide", "fin-checkup setup"):
        assert rule in text, rule
    assert find_outside_allowed(text) == []
    # 모든 대화에 붙는 문맥이라 짧게 둔다 — 자세한 절차는 MCP 도구(setup_guide)와 스킬이 준다.
    assert len(text.splitlines()) <= 40


@pytest.mark.parametrize("readme", sorted(ROOT.glob("README*.md")), ids=lambda p: p.name)
def test_readmes_show_the_gemini_install_command(readme):
    assert f"`{INSTALL}`" in readme.read_text(encoding="utf-8")


def test_setup_guide_and_agent_docs_offer_the_extension():
    commands = [step.get("command") for step in guide.setup_guide("gemini")["steps"]]
    assert INSTALL in commands
    assert INSTALL in (ROOT / "docs" / "agents.md").read_text(encoding="utf-8")
