"""코드 릴리스 가드 — Claude Desktop 확장(.mcpb) 매니페스트 · MCP Registry server.json · 릴리스 워크플로.

태그를 밀면 되돌릴 수 없는 것들이 나간다(공개 Release 파일, 레지스트리 항목 — 같은 버전은 다시 올릴 수 없다).
그래서 태그 전에 여기서 막는다: 버전이 어긋나거나(pyproject 0.2.0 인데 매니페스트 0.1.0),
공개 스키마에 안 맞거나, 설명에 권유 문구가 섞이거나, 번들에 `.env`·캐시가 섞이거나,
data-* 스냅샷이 코드 릴리스 자리(Latest)를 가져가는 것.
스키마는 공개본을 받아 `tests/fixtures/schemas/` 에 둔다(네트워크 없이 돈다) — 받은 곳은 `$schema`·파일 이름.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

import jsonschema
import pytest
import yaml

from fin_checkup import __version__
from fin_checkup.explain.banned import find_outside_allowed

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = Path(__file__).parent / "fixtures" / "schemas"
WORKFLOWS = ROOT / ".github" / "workflows"

_spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

MANIFEST = json.loads(release.MANIFEST.read_text(encoding="utf-8"))
SERVER = json.loads(release.SERVER_JSON.read_text(encoding="utf-8"))
NAME = "io.github.younghwan91/fin-checkup"


# ── 버전 ────────────────────────────────────────────────────────────


def test_every_version_source_agrees():
    assert release.check_tag(None) == []
    assert set(release.versions().values()) == {__version__}


def test_tag_must_match_pyproject():
    assert release.check_tag(f"v{__version__}") == []
    assert release.check_tag("v9.9.9") and release.check_tag(__version__)  # 'v' 빠진 태그도 거부
    assert release.main(["check-tag", "v9.9.9"]) == 1


def test_release_workflow_checks_the_tag_before_building():
    steps = yaml.safe_load((WORKFLOWS / "release.yml").read_text(encoding="utf-8"))["jobs"]["release"][
        "steps"
    ]
    runs = [s.get("run") or "" for s in steps]
    check = next(i for i, r in enumerate(runs) if "release.py check-tag" in r)
    pack = next(i for i, r in enumerate(runs) if "mcpb" in r and " pack " in r)
    publish = next(i for i, r in enumerate(runs) if "mcp-publisher publish" in r)
    upload = next(i for i, r in enumerate(runs) if "gh release upload" in r)
    assert check < pack < upload < publish
    assert '"$TAG"' in runs[check]


# ── .mcpb 매니페스트 ────────────────────────────────────────────────


def test_manifest_validates_against_the_published_schema():
    schema = json.loads((SCHEMAS / f"mcpb-manifest-v{MANIFEST['manifest_version']}.schema.json").read_text())
    jsonschema.validate(MANIFEST, schema)
    assert MANIFEST["$schema"].endswith(f"mcpb-manifest-v{MANIFEST['manifest_version']}.schema.json")


def test_manifest_runs_under_uv_with_the_locked_mcp_extra():
    server = MANIFEST["server"]
    assert server["type"] == "uv"
    cfg = server["mcp_config"]
    assert cfg["command"] == "uv"
    args = cfg["args"]
    assert args[:3] == ["run", "--directory", "${__dirname}"]
    assert "--frozen" in args and args[args.index("--extra") + 1] == "mcp"
    assert args[-1] == server["entry_point"] == "server/main.py"
    assert (ROOT / "mcpb" / server["entry_point"]).is_file()


def test_dart_key_is_optional_sensitive_and_passed_only_as_env():
    opt = MANIFEST["user_config"]["dart_api_key"]
    assert opt["type"] == "string" and opt["sensitive"] is True and opt["required"] is False
    # 기본값이 없으면 호스트가 '${user_config.dart_api_key}' 글자 그대로 넘긴다 — 빈 값으로 둔다.
    assert opt["default"] == ""
    assert MANIFEST["server"]["mcp_config"]["env"] == {"DART_API_KEY": "${user_config.dart_api_key}"}
    assert "user_config" not in " ".join(MANIFEST["server"]["mcp_config"]["args"])


def test_manifest_tools_match_the_server():
    from test_mcp import TOOLS

    assert {t["name"] for t in MANIFEST["tools"]} == TOOLS
    assert [p["name"] for p in MANIFEST["prompts"]] == ["diagnose"]


def test_manifest_icons_come_from_the_brand_folder():
    assert release.BUNDLE_FILES[MANIFEST["icon"]] == ROOT / "docs" / "brand" / "logo-mark-512.png"
    for icon in MANIFEST["icons"]:
        assert release.BUNDLE_FILES[icon["src"]].parent == ROOT / "docs" / "brand"
        assert icon["size"] == "512x512"


def test_manifest_text_has_no_advice_wording_and_carries_the_disclaimer():
    from fin_checkup.disclaimer import DISCLAIMER

    texts = [
        MANIFEST["display_name"],
        MANIFEST["description"],
        MANIFEST["long_description"],
        *[t["description"] for t in MANIFEST["tools"]],
        *[p["description"] + p["text"] for p in MANIFEST["prompts"]],
        *[o["title"] + o["description"] for o in MANIFEST["user_config"].values()],
    ]
    for text in texts:
        assert find_outside_allowed(text) == [], text
    assert DISCLAIMER in MANIFEST["long_description"]
    assert "투자권유·추천이 아닙니다." in MANIFEST["description"]


# ── 번들 내용 ───────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def staged(tmp_path_factory):
    dest = tmp_path_factory.mktemp("mcpb") / "bundle"
    return dest, release.stage(dest)


def test_bundle_holds_only_the_allowlist(staged):
    dest, files = staged
    top = {f.split("/")[0] for f in files}
    assert top == {
        "manifest.json",
        "server",
        "icon.png",
        "icon-dark.png",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "LICENSE",
        "src",
    }
    assert all(not f.startswith("src/") or f.startswith("src/fin_checkup/") for f in files)
    assert "src/fin_checkup/mcp/server.py" in files
    for f in files:
        assert "__pycache__" not in f and not f.endswith((".pyc", ".duckdb")), f
        assert not re.search(r"(^|/)\.env", f), f
    # 번들의 pyproject·lock 은 저장소 것 그대로 — --frozen 이 잠근 의존성을 깐다.
    assert (dest / "pyproject.toml").read_bytes() == (ROOT / "pyproject.toml").read_bytes()
    assert (dest / "uv.lock").read_bytes() == (ROOT / "uv.lock").read_bytes()


def test_bundle_command_substitutes_like_the_host(staged):
    dest, _ = staged
    command, args, env = release.bundle_command(dest)
    assert command == "uv" and str(dest) in args and env == {"DART_API_KEY": ""}
    _, _, env = release.bundle_command(dest, {"dart_api_key": "abc"})
    assert env == {"DART_API_KEY": "abc"}


def test_bundle_has_no_secret_values(staged, monkeypatch):
    """번들에 키 값이 들어가면 공개 Release 로 나간다 — 설정된 키(있다면)가 어느 파일에도 없어야 한다."""
    dest, files = staged
    from fin_checkup.config import Settings

    key = Settings().dart_api_key.strip()
    if not key:
        pytest.skip("설정된 DART 키가 없다 (CI) — 허용목록 검사로 충분")
    for f in files:
        assert key.encode() not in (dest / f).read_bytes(), f


# ── server.json (공식 MCP Registry) ─────────────────────────────────


def test_server_json_validates_against_the_published_schema():
    date = re.search(r"/schemas/([\d-]+)/server\.schema\.json$", SERVER["$schema"]).group(1)
    schema = json.loads((SCHEMAS / f"server-{date}.schema.json").read_text())
    jsonschema.validate(SERVER, schema)
    jsonschema.validate(release.server_json_for("a" * 64), schema)


def test_server_json_follows_official_registry_rules():
    assert SERVER["name"] == NAME  # GitHub OIDC 가 확인하는 이름공간
    assert 1 <= len(SERVER["description"]) <= 100
    assert find_outside_allowed(SERVER["description"]) == []
    assert not re.search(r"\b(buy|sell|recommend\w*|top)\b", SERVER["description"], re.I)
    (pkg,) = SERVER["packages"]
    assert pkg["registryType"] == "mcpb" and pkg["transport"] == {"type": "stdio"}
    assert pkg["identifier"] == (f"{release.REPO_URL}/releases/download/v{__version__}/{release.BUNDLE_NAME}")
    assert "mcp" in pkg["identifier"]  # 레지스트리 요구: URL 에 'mcp'
    # 저장소엔 자리만 — 실제 해시는 워크플로가 만든 번들에서 채운다(손으로 적은 해시가 번들과 어긋나지 않게).
    assert pkg["fileSha256"] == release.SHA_PLACEHOLDER
    env = {e["name"]: e for e in pkg["environmentVariables"]}
    assert env["DART_API_KEY"]["isSecret"] is True and env["DART_API_KEY"]["isRequired"] is False


def test_server_json_patch_fills_the_hash_and_refuses_placeholders(tmp_path):
    sha = "ab" * 32
    out = tmp_path / "server.json"
    assert release.main(["server-json", "--sha256", sha, "--out", str(out)]) == 0
    assert json.loads(out.read_text())["packages"][0]["fileSha256"] == sha
    for bad in (release.SHA_PLACEHOLDER, "XYZ", "AB" * 32):
        with pytest.raises(ValueError):
            release.server_json_for(bad)


def test_release_notes_come_from_the_changelog_section(tmp_path, monkeypatch):
    log = tmp_path / "CHANGELOG.md"
    log.write_text("# Changelog\n\n## [0.2.0] — x\n\n- 둘\n\n## [0.1.0] — y\n\n- 하나\n", encoding="utf-8")
    monkeypatch.setattr(release, "CHANGELOG", log)
    assert release.release_notes("v0.1.0") == "- 하나\n"
    assert release.release_notes("v0.2.0") == "- 둘\n"
    assert "fin-checkup.mcpb" in release.release_notes("v0.3.0")


# ── MCP 서버가 빈 키를 '없음' 으로 ──────────────────────────────────


@pytest.mark.parametrize("value", ["", "  ", "${user_config.dart_api_key}"])
def test_blank_or_unsubstituted_key_from_the_host_falls_back_to_the_config_file(value, tmp_path, monkeypatch):
    from fin_checkup.config import Settings
    from fin_checkup.mcp.server import drop_unset_key

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / ".env").write_text("DART_API_KEY=from-file-key\n", encoding="utf-8")
    monkeypatch.setenv("FIN_CHECKUP_CONFIG_DIR", str(cfg))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DART_API_KEY", value)
    drop_unset_key()
    assert "DART_API_KEY" not in os.environ
    assert Settings().dart_api_key == "from-file-key"


def test_a_real_key_from_the_host_is_kept(monkeypatch):
    from fin_checkup.mcp.server import drop_unset_key

    monkeypatch.setenv("DART_API_KEY", "real-key")
    drop_unset_key()
    assert os.environ["DART_API_KEY"] == "real-key"


# ── data-* 스냅샷이 코드 릴리스와 섞이지 않게 ───────────────────────


def _release_create_lines() -> list[tuple[str, str]]:
    found = []
    for path in [*WORKFLOWS.glob("*.yml"), *(ROOT / "scripts").glob("*.sh")]:
        text = path.read_text(encoding="utf-8")
        # 줄 끝 '\' 로 이어 쓴 명령을 한 줄로.
        joined = re.sub(r"\\\n\s*", " ", text)
        for line in joined.splitlines():
            if re.search(r"\bgh release create\b", line):
                found.append((path.name, line))
    return found


def test_every_data_release_is_created_without_taking_latest():
    lines = _release_create_lines()
    data = [(name, line) for name, line in lines if name != "release.yml"]
    assert data, "data-* 를 만드는 gh release create 를 못 찾았다"
    for name, line in data:
        assert "--latest=false" in line, f"{name}: {line.strip()}"
    # 코드 릴리스만 Latest 를 가져간다.
    code = [line for name, line in lines if name == "release.yml"]
    assert code and all("--latest" in line and "--latest=false" not in line for line in code)


def test_data_snapshot_publish_hands_latest_back_to_the_code_release():
    script = (ROOT / "scripts" / "publish_snapshot.sh").read_text(encoding="utf-8")
    assert 'startswith("v")' in script and "gh release edit" in script and "--latest" in script


def test_data_pipelines_only_pick_data_releases():
    fetch = (ROOT / "scripts" / "fetch_snapshot.sh").read_text(encoding="utf-8")
    assert 'select(.tagName | startswith("data-"))' in fetch
    collect = (WORKFLOWS / "collect.yml").read_text(encoding="utf-8")
    prune = collect[collect.index("gh release list") : collect.index("gh release delete")]
    assert 'startswith("data-")' in prune  # 정리 단계가 v* 를 지우지 않는다
    pages = yaml.safe_load((WORKFLOWS / "pages.yml").read_text(encoding="utf-8"))
    assert "startsWith(github.event.release.tag_name, 'data-')" in pages["jobs"]["build"]["if"]


def test_release_workflow_runs_only_on_version_tags_with_oidc():
    wf = yaml.safe_load((WORKFLOWS / "release.yml").read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))
    assert on == {"push": {"tags": ["v*"]}}
    assert wf["permissions"] == {"contents": "write", "id-token": "write"}
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "mcp-publisher login github-oidc" in text and "cosign verify-blob" in text
    assert re.search(r"MCP_PUBLISHER_VERSION: v\d+\.\d+\.\d+", text)
    assert re.search(r'MCPB_VERSION: "\d+\.\d+\.\d+"', text)


def test_ci_builds_and_runs_the_bundle_with_the_same_mcpb_version():
    """태그 때 처음 번들을 만들면 깨진 걸 되돌릴 수 없다 — PR 마다 같은 명령으로 만들어 띄워 본다."""
    ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))["jobs"]["mcpb"]
    rel = yaml.safe_load((WORKFLOWS / "release.yml").read_text(encoding="utf-8"))
    assert ci["env"]["MCPB_VERSION"] == rel["env"]["MCPB_VERSION"]
    runs = " ".join(s.get("run") or "" for s in ci["steps"])
    for needle in ("release.py check-tag", "release.py stage", " validate ", " pack ", "release.py smoke"):
        assert needle in runs, needle
