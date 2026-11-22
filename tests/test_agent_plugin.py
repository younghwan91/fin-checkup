"""에이전트 플러그인 — Claude Code 마켓플레이스 · 플러그인 · 스킬이 지키는 것.

"삼성전자 재무 진단해줘" 라고 하면 에이전트가 fin-checkup 스킬을 꺼내 쓴다. 스킬 문서는 에이전트가
그대로 따라 하는 지시라, 여기 섞인 말은 에이전트가 사용자에게 하는 말이 된다. 그래서 사이트 문장과 같은
선을 본다: 면책 문구 그대로 · 계산식·경계값 · ⚫(데이터 없음)·⊘(해당 없음) 규칙 · 금지 표현 없음.

예외는 하나 — 스킬이 "이런 말은 하지 말라" 고 가르치려면 그 말을 적어야 한다. 그런 인용은
`<!-- fc:quoted-start -->` … `<!-- fc:quoted-end -->` 사이에만 두고, 그 밖은 금지 표현 린트를 통과해야 한다.

형식(필수 필드·이름 규칙·설명 길이)은 Claude Code 플러그인 문서와 Agent Skills 표준(agentskills.io)을
따른다. `claude plugin validate` 는 CI 에 없어 로컬에서 돌리고, 여기선 같은 필수 항목을 직접 본다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain.banned import find_outside_allowed

ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN = ROOT / "plugins" / "fin-checkup"
SITE = "https://younghwan91.github.io/fin-checkup"
MCP_FROM = "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup"
MCP_TOOLS = (
    "search_companies",
    "diagnose_company",
    "explain_metric",
    "compare_companies",
    "recent_risk_disclosures",
    "data_status",
)

#: 스킬이 꺼내져야 하는 말 — 설명(description)이 트리거라 여기에 들어 있어야 한다.
DIAGNOSE_TRIGGERS = (
    "재무 진단",
    "재무제표",
    "이 회사 괜찮아",
    "부채비율",
    "신호등",
    "fin-checkup",
    "financial health",
    "Korean",
    "diagnose",
)
COMPARE_TRIGGERS = ("비교", "compare", "fin-checkup")
SETUP_TRIGGERS = (
    "설치",
    "설치해줘",
    "세팅",
    "연결",
    "DART 키",
    "API 키",
    "인증키",
    "set up fin-checkup",
    "install",
)

#: Agent Skills 표준과 claude.ai 가 받아 주는 frontmatter 키 — 이 밖의 키는 다른 에이전트에서 거부된다.
PORTABLE_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_QUOTED = re.compile(r"<!-- fc:quoted-start -->.*?<!-- fc:quoted-end -->", re.S)

SKILLS = sorted((PLUGIN / "skills").glob("*/SKILL.md"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def split_skill(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    assert match, f"{path}: frontmatter(--- … ---)로 시작해야 한다"
    meta = yaml.safe_load(match.group(1))
    assert isinstance(meta, dict)
    return meta, match.group(2)


def outside_quotes(text: str) -> str:
    return _QUOTED.sub(" ", text)


# ── 마켓플레이스 · 플러그인 ─────────────────────────────────────────


def test_marketplace_lists_the_plugin_by_relative_path():
    market = _load(MARKETPLACE)
    # `/plugin install fin-checkup@fin-checkup` — 앞은 플러그인, 뒤는 마켓플레이스 이름.
    assert market["name"] == "fin-checkup"
    assert market["owner"]["name"]
    [entry] = market["plugins"]
    assert entry["name"] == "fin-checkup"
    assert entry["source"] == "./plugins/fin-checkup"
    assert (ROOT / entry["source"]).is_dir()
    assert entry["description"]


def test_plugin_manifest_matches_marketplace_entry():
    manifest = _load(PLUGIN / ".claude-plugin" / "plugin.json")
    [entry] = _load(MARKETPLACE)["plugins"]
    assert manifest["name"] == entry["name"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["license"] == "MIT"
    assert manifest["description"]
    assert manifest["repository"] == "https://github.com/younghwan91/fin-checkup"
    for text in (manifest["description"], entry["description"]):
        assert find_outside_allowed(text) == []


def test_mcp_server_command_is_the_published_uvx_entry_point():
    servers = _load(PLUGIN / ".mcp.json")["mcpServers"]
    server = servers["fin-checkup"]
    assert server["command"] == "uvx"
    assert server["args"] == ["--from", MCP_FROM, "fin-checkup-mcp"]
    # 정적 사이트만 읽는 서버다 — DART 인증키를 플러그인에 실을 이유가 없다(약관 제19조).
    assert "env" not in server or not any("DART" in k for k in server["env"])


def test_plugin_has_a_readme_with_install_commands():
    text = (PLUGIN / "README.md").read_text(encoding="utf-8")
    assert "/plugin marketplace add younghwan91/fin-checkup" in text
    assert "/plugin install fin-checkup@fin-checkup" in text
    assert DISCLAIMER in text
    assert find_outside_allowed(outside_quotes(text)) == []


# ── 스킬 ─────────────────────────────────────────────────────────


def test_there_are_diagnose_and_compare_skills():
    assert {p.parent.name for p in SKILLS} >= {"diagnose", "compare"}


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_frontmatter_follows_agent_skills_standard(skill):
    meta, body = split_skill(skill)
    assert set(meta) <= PORTABLE_KEYS, set(meta) - PORTABLE_KEYS
    assert meta["name"] == skill.parent.name
    assert _NAME.fullmatch(meta["name"]) and len(meta["name"]) <= 64
    assert 1 <= len(meta["description"]) <= 1024
    assert len(skill.read_text(encoding="utf-8").splitlines()) < 500


@pytest.mark.parametrize(
    ("name", "triggers"),
    [("diagnose", DIAGNOSE_TRIGGERS), ("compare", COMPARE_TRIGGERS), ("setup", SETUP_TRIGGERS)],
)
def test_description_carries_trigger_phrases(name, triggers):
    meta, _ = split_skill(PLUGIN / "skills" / name / "SKILL.md")
    missing = [t for t in triggers if t not in meta["description"]]
    assert missing == []


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_skill_keeps_the_red_lines(skill):
    meta, body = split_skill(skill)
    # 면책 문구는 글자 그대로 — 에이전트가 답 끝에 붙여야 하는 문장이다.
    assert DISCLAIMER in body
    for must in ("계산식", "경계값", "⚫", "⊘", "데이터 없음", "해당 없음"):
        assert must in body, must
    # 근거가 있는 회사 페이지·정적 JSON 으로 보낸다.
    assert f"{SITE}/api/c/" in body
    assert f"{SITE}/c/" in body
    # MCP 가 없어도 돈다 — 둘 다 적혀 있어야 한다.
    assert "MCP" in body and "curl" in body
    assert any(tool in body for tool in MCP_TOOLS)


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_no_banned_phrases_outside_quoted_examples(skill):
    meta, body = split_skill(skill)
    assert find_outside_allowed(meta["description"]) == []
    assert find_outside_allowed(outside_quotes(body)) == []


def test_quoted_regions_are_balanced():
    for skill in SKILLS:
        text = skill.read_text(encoding="utf-8")
        assert text.count("<!-- fc:quoted-start -->") == text.count("<!-- fc:quoted-end -->")


def test_diagnose_uses_every_needed_source_with_and_without_mcp():
    _, body = split_skill(PLUGIN / "skills" / "diagnose" / "SKILL.md")
    for tool in ("search_companies", "diagnose_company", "recent_risk_disclosures", "explain_metric"):
        assert tool in body, tool
    for path in ("/search-index.json", "/api/c/", "/data/disclosures.json", "/m/"):
        assert f"{SITE}{path}" in body, path
    # "사도 돼?" — 판단은 하지 않고 사실을 내민다.
    assert "사도 돼" in body


def test_compare_does_not_pick_a_winner():
    _, body = split_skill(PLUGIN / "skills" / "compare" / "SKILL.md")
    assert "compare_companies" in body
    assert f"{SITE}/compare/?c=" in body
    assert "순위" in body  # "순위를 매기지 않는다" 규칙이 적혀 있다


def test_setup_never_takes_the_key_through_chat_and_asks_before_installing():
    from fin_checkup import onboarding

    _, body = split_skill(PLUGIN / "skills" / "setup" / "SKILL.md")
    rules = outside_quotes(body)
    assert "채팅으로 받지 않는다" in rules
    assert "cat" in rules and ".env" in rules and "커밋하지 않는다" in rules
    assert "물은 뒤" in rules  # uv 설치·설정 파일 수정 전에 묻는다
    # 사용자가 직접 돌릴 명령은 CLI 와 같은 글자 — 복사해 붙여도 그대로 돈다.
    commands = (onboarding.SETUP_ANYWHERE, onboarding.SETUP_IN_REPO, onboarding.UV_INSTALL["macos_linux"])
    for command in commands:
        assert command in body, command
    assert f"! {onboarding.SETUP_ANYWHERE} --from-clipboard" in body
    assert onboarding.KEY_APPLY_URL in body
    for tool in ("setup_status", "setup_guide", "diagnose_company"):
        assert tool in body
    assert "dart-live" in body
    assert "claude mcp add fin-checkup --scope user -- uvx --from" in body


# ── 다른 에이전트 (Agent Skills 표준 경로 · AGENTS.md · 채팅 지침) ──────────


def _sync_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sync_agent_skills", ROOT / "scripts" / "sync_agent_skills.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PORTABLE = sorted((ROOT / ".agents" / "skills").glob("*/SKILL.md"))


def test_portable_copies_match_the_plugin_skills():
    # 원본만 고치고 사본을 잊으면 Codex·Cursor 는 옛 규칙으로 답한다.
    sync = _sync_module()
    assert sync.stale() == [], "uv run python scripts/sync_agent_skills.py 로 다시 만들 것"
    assert {p.parent.name for p in PORTABLE} == {f"fin-checkup-{p.parent.name}" for p in SKILLS}


@pytest.mark.parametrize("skill", PORTABLE, ids=lambda p: p.parent.name)
def test_portable_copy_is_a_valid_standard_skill(skill):
    assert not skill.is_symlink() and not skill.parent.is_symlink()
    meta, body = split_skill(skill)
    assert set(meta) <= PORTABLE_KEYS
    assert meta["name"] == skill.parent.name
    assert _NAME.fullmatch(meta["name"]) and len(meta["name"]) <= 64
    assert DISCLAIMER in body
    assert find_outside_allowed(outside_quotes(body)) == []
    # 상호 참조도 새 이름으로 — 다른 에이전트엔 `diagnose` 라는 스킬이 없다.
    assert not re.search(r"`(diagnose|compare)` 스킬", body)


def test_agents_md_points_to_the_portable_skills():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for skill in PORTABLE:
        assert str(skill.relative_to(ROOT)) in text
    assert "CLAUDE.md" in text and "docs/agents-skill.md" in text
    assert find_outside_allowed(text) == []


def _instruction_block() -> str:
    text = (ROOT / "docs" / "agents-skill.md").read_text(encoding="utf-8")
    match = re.search(
        r"<!-- fc:instructions-start -->\n```text\n(.*?)\n```\n<!-- fc:instructions-end -->", text, re.S
    )
    assert match, "지침 블록 표식이 없다"
    return match.group(1)


def test_chat_instruction_block_keeps_the_red_lines():
    block = _instruction_block()
    # ChatGPT 맞춤 지침 한 칸이 1,500자다 — 넘으면 붙여 넣다 잘려 면책 문장(끝 줄)이 빠진다.
    assert len(block) <= 1500, len(block)
    assert DISCLAIMER in block
    for must in (f"{SITE}/api/c/", f"{SITE}/search-index.json", f"{SITE}/c/", "⚫", "⊘", "계산식", "경계값"):
        assert must in block, must
    assert find_outside_allowed(block) == []


def test_agents_doc_has_install_paths_for_each_agent():
    text = (ROOT / "docs" / "agents-skill.md").read_text(encoding="utf-8")
    assert "/plugin install fin-checkup@fin-checkup" in text
    assert ".agents/skills/" in text
    assert find_outside_allowed(text) == []
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/agents-skill.md" in readme
