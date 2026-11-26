"""코드 릴리스(`v*` 태그) 도우미 — Claude Desktop 확장 번들(.mcpb)과 MCP Registry 항목(server.json).

`.github/workflows/release.yml` 이 태그마다 이 순서로 부른다:

    release.py check-tag v0.1.0          # 태그 = pyproject = manifest = server.json = 플러그인 = Gemini 확장
    release.py stage _mcpb               # 번들에 넣을 파일만 골라 담는다
    npx -y @anthropic-ai/mcpb@<버전> validate _mcpb/manifest.json, pack _mcpb fin-checkup.mcpb
    release.py smoke fin-checkup.mcpb    # 푼 번들을 manifest 의 명령 그대로 띄워 도구 목록 확인
    release.py server-json --sha256 SHA  # 레지스트리에 올릴 server.json 에 해시를 채운다
    release.py notes v0.1.0              # CHANGELOG.md 의 그 버전 절 (릴리스 설명)

(`release.py` 는 `uv run python scripts/release.py`.)
로컬에서 실제 사이트로 확인하려면 `smoke fin-checkup.mcpb --diagnose 삼성전자`.

번들에는 허용목록 파일만 들어간다(`.env`·`data/`·캐시가 섞이지 않게) — DART 인증키는 번들이 아니라
Claude Desktop 의 확장 설정(user_config, 민감 값으로 저장)에서 환경변수 DART_API_KEY 로 넘어온다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "mcpb" / "manifest.json"
SERVER_JSON = ROOT / "server.json"
PLUGIN_JSON = ROOT / "plugins" / "fin-checkup" / ".claude-plugin" / "plugin.json"
#: Gemini CLI 확장 — 갤러리·`gemini extensions` 가 보여 주는 버전이라 릴리스 태그와 같아야 한다.
GEMINI_EXTENSION = ROOT / "gemini-extension.json"
CHANGELOG = ROOT / "CHANGELOG.md"
BUNDLE_NAME = "fin-checkup.mcpb"
REPO_URL = "https://github.com/younghwan91/fin-checkup"
#: 저장소의 server.json 에는 해시 자리만 둔다 — 실제 값은 릴리스 워크플로가 만든 번들에서 계산해 채운다.
SHA_PLACEHOLDER = "0" * 64

#: 번들 안 경로 → 저장소 경로. 디렉터리는 통째로(캐시 파일 빼고).
BUNDLE_FILES: dict[str, Path] = {
    "manifest.json": MANIFEST,
    "server/main.py": ROOT / "mcpb" / "server" / "main.py",
    "icon.png": ROOT / "docs" / "brand" / "logo-mark-512.png",
    "icon-dark.png": ROOT / "docs" / "brand" / "logo-mark-dark-512.png",
    # uv 가 의존성을 잠근 그대로 깐다(--frozen). hatchling 은 readme 파일이 있어야 빌드된다.
    "pyproject.toml": ROOT / "pyproject.toml",
    "uv.lock": ROOT / "uv.lock",
    "README.md": ROOT / "README.md",
    "LICENSE": ROOT / "LICENSE",
    "src/fin_checkup": ROOT / "src" / "fin_checkup",
}
_SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".env", ".env.*", "*.duckdb", "*.duckdb.wal")


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def project_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def versions() -> dict[str, str]:
    """버전을 적는 곳 전부. 하나라도 다르면 릴리스하지 않는다."""
    from fin_checkup import __version__

    server = _json(SERVER_JSON)
    found = {
        "pyproject.toml": project_version(),
        "fin_checkup.__version__": __version__,
        "mcpb/manifest.json": _json(MANIFEST)["version"],
        "server.json": server["version"],
        "plugins/fin-checkup/.claude-plugin/plugin.json": _json(PLUGIN_JSON)["version"],
        "gemini-extension.json": _json(GEMINI_EXTENSION)["version"],
    }
    for i, pkg in enumerate(server["packages"]):
        found[f"server.json packages[{i}].version"] = pkg.get("version", "")
        m = re.search(r"/releases/download/v([^/]+)/", pkg["identifier"])
        found[f"server.json packages[{i}].identifier"] = m.group(1) if m else pkg["identifier"]
    return found


def check_tag(tag: str | None) -> list[str]:
    """문제 목록(없으면 빈 목록). tag 가 None 이면 버전끼리만 맞춰 본다."""
    found = versions()
    want = project_version()
    problems = [f"{where}: {v} (pyproject {want})" for where, v in found.items() if v != want]
    if tag is not None and tag != f"v{want}":
        problems.append(f"태그 {tag} 가 pyproject 버전 v{want} 와 다르다")
    return problems


def stage(dest: Path) -> list[str]:
    """번들 내용을 dest 에 담고 담은 파일 목록(번들 안 경로)을 돌려준다."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for inside, src in BUNDLE_FILES.items():
        target = dest / inside
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, target, ignore=_SKIP)
        else:
            shutil.copy2(src, target)
    return sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())


def server_json_for(sha256: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", sha256) or sha256 == SHA_PLACEHOLDER:
        raise ValueError(f"sha256 이 아니다: {sha256!r}")
    data = _json(SERVER_JSON)
    for pkg in data["packages"]:
        if pkg["registryType"] == "mcpb":
            pkg["fileSha256"] = sha256
    return data


def release_notes(tag: str) -> str:
    """CHANGELOG.md 의 `## [0.1.0]`·`## 0.1.0`·`## v0.1.0` 절. 없으면 짧은 기본 문장."""
    version = tag.removeprefix("v")
    if CHANGELOG.is_file():
        text = CHANGELOG.read_text(encoding="utf-8")
        head = re.compile(rf"^## \[?v?{re.escape(version)}\]?(\s|$).*$", re.M)
        m = head.search(text)
        if m:
            rest = text[m.end() :]
            nxt = re.search(r"^## ", rest, re.M)
            body = rest[: nxt.start()] if nxt else rest
            return body.strip() + "\n"
    return (
        f"fin-checkup {tag}. Claude Desktop 확장 `{BUNDLE_NAME}` 을 받아 두 번 눌러 설치한다.\n\n"
        "본 결과는 공시 수치를 공개된 재무비율 공식으로 계산한 측정값입니다. "
        "투자권유·자문이 아니며, 데이터 정확성과 이용에 따른 손익에 책임지지 않습니다.\n"
    )


def bundle_command(
    bundle_dir: Path, user_config: dict[str, str] | None = None
) -> tuple[str, list[str], dict[str, str]]:
    """manifest 의 mcp_config 를 호스트처럼 풀어 쓴다 (`${__dirname}`·`${user_config.*}` — 기본값 포함)."""
    manifest = _json(bundle_dir / "manifest.json")
    values = {k: str(v.get("default", "")) for k, v in manifest.get("user_config", {}).items()}
    values.update(user_config or {})

    def sub(s: str) -> str:
        s = s.replace("${__dirname}", str(bundle_dir))
        return re.sub(r"\$\{user_config\.([^}]+)\}", lambda m: values.get(m.group(1), m.group(0)), s)

    cfg = manifest["server"]["mcp_config"]
    return (
        sub(cfg["command"]),
        [sub(a) for a in cfg.get("args", [])],
        {k: sub(v) for k, v in cfg.get("env", {}).items()},
    )


async def _smoke(bundle: Path, diagnose: str | None) -> dict[str, Any]:
    from mcp import Client, StdioServerParameters

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "bundle"
        with zipfile.ZipFile(bundle) as zf:
            zf.extractall(root)
        manifest = _json(root / "manifest.json")
        command, args, env = bundle_command(root)
        # 바깥 `uv run` 의 가상환경·설정이 번들 쪽 uv 로 새지 않게 — 호스트가 띄우는 것처럼 깨끗하게.
        base = {
            k: v for k, v in os.environ.items() if not k.startswith(("UV_", "VIRTUAL_ENV", "FIN_CHECKUP_"))
        }
        base.pop("DART_API_KEY", None)
        params = StdioServerParameters(
            command=command, args=args, env={**base, **env, "FIN_CHECKUP_MCP_CACHE_DIR": "off"}
        )
        async with Client(params) as client:
            names = sorted(t.name for t in (await client.list_tools()).tools)
            result: dict[str, Any] = {"tools": names}
            declared = sorted(t["name"] for t in manifest["tools"])
            if names != declared:
                raise SystemExit(f"manifest tools {declared} != 서버 도구 {names}")
            status = await client.call_tool("setup_status", {})
            result["setup_status"] = (
                {
                    k: status.structured_content.get(k)
                    for k in ("fin_checkup_version", "python", "source_mode", "dart_key", "site")
                }
                if status.structured_content
                else status.content[0].text
            )
            if diagnose:
                out = await client.call_tool("diagnose_company", {"company": diagnose})
                if out.is_error:
                    raise SystemExit(f"diagnose_company 실패: {out.content[0].text}")
                sc = out.structured_content
                result["diagnose"] = {
                    "company": sc["company"],
                    "fiscal_year": sc.get("fiscal_year"),
                    "metrics": len(sc["metrics"]),
                    "source": sc.get("source"),
                }
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check-tag", help="태그와 모든 버전이 같은지")
    p.add_argument("tag", nargs="?")
    p = sub.add_parser("stage", help="번들 파일을 디렉터리에 담는다")
    p.add_argument("dest", type=Path)
    p = sub.add_parser("server-json", help="해시를 채운 server.json 을 쓴다")
    p.add_argument("--sha256", required=True)
    p.add_argument("--out", type=Path, default=SERVER_JSON)
    p = sub.add_parser("notes", help="릴리스 설명 (CHANGELOG 의 그 버전 절)")
    p.add_argument("tag")
    p = sub.add_parser("smoke", help="번들을 풀어 manifest 명령 그대로 띄우고 MCP 로 불러 본다")
    p.add_argument("bundle", type=Path)
    p.add_argument("--diagnose", metavar="COMPANY", help="실제 사이트로 diagnose_company 까지")
    args = parser.parse_args(argv)

    if args.cmd == "check-tag":
        problems = check_tag(args.tag)
        for line in problems:
            print(f"::error::{line}", file=sys.stderr)
        if not problems:
            print(f"버전 일치: v{project_version()}")
        return 1 if problems else 0
    if args.cmd == "stage":
        files = stage(args.dest)
        print(f"{len(files)}개 파일 → {args.dest}")
        return 0
    if args.cmd == "server-json":
        data = server_json_for(args.sha256)
        args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"fileSha256 = {args.sha256} → {args.out}")
        return 0
    if args.cmd == "notes":
        sys.stdout.write(release_notes(args.tag))
        return 0
    if args.cmd == "smoke":
        print(json.dumps(asyncio.run(_smoke(args.bundle, args.diagnose)), ensure_ascii=False, indent=2))
        return 0
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
