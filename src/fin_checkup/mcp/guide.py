"""`setup_guide` 도구가 돌려줄 설치 안내 — 클라이언트별 MCP 연결과 OpenDART 인증키 넣는 법.

에이전트가 이 안내를 그대로 따라 한다. 그래서 단계마다 '먼저 물어볼 것(ask_first)' 과 '사용자가 직접 할 것
(user_runs)' 을 표시한다: 설치 명령·설정 파일 수정은 보여 주고 물은 뒤에, 인증키는 사용자가 자기 터미널에서
직접 — 에이전트는 키를 채팅으로 받지도, 출력하지도, `.env` 를 읽지도 않는다.

명령·경로는 `docs/agents.md` 와 같다(`tests/test_mcp_setup.py` 가 본다).
"""

from __future__ import annotations

import json
from typing import Any

from fin_checkup import onboarding
from fin_checkup.disclaimer import DISCLAIMER

MCP_ARGS = ["--from", onboarding.MCP_FROM, "fin-checkup-mcp"]
KEY_PLACEHOLDER = "<본인 OpenDART 인증키 — 사용자가 직접 입력>"

RULES_FOR_AGENT: tuple[str, ...] = (
    "인증키를 채팅에 붙여 넣으라고 하지 않는다. 사용자가 붙여 넣으면 쓰지 말고, 그 키는 「인증키 관리」에서 다시 받으라고 권한다.",
    "인증키를 명령줄 인자·에이전트가 실행하는 명령에 넣지 않는다 (명령 기록·대화 기록에 남는다).",
    "`.env`·설정 파일의 키 값을 cat·출력·요약하지 않는다. 확인은 setup_status 또는 `fin-checkup setup --check` 로만.",
    "`.env` 를 git 에 커밋하지 않는다.",
    "설치 명령(uv 설치)·설정 파일 수정은 무엇을 바꾸는지 먼저 보여 주고, 사용자가 좋다고 한 뒤에 한다.",
    "인증키는 본인 것만 쓴다 — 남의 키를 받거나 나눠 쓰지 않는다 (OpenDART 이용약관 제19조).",
)


def _server(env: dict[str, str] | None = None, *, vscode: bool = False) -> dict[str, Any]:
    server: dict[str, Any] = {"command": "uvx", "args": list(MCP_ARGS)}
    if vscode:
        server = {"type": "stdio", **server}
    if env:
        server["env"] = env
    return server


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _mcp_servers_block(env: dict[str, str] | None = None) -> str:
    return _json({"mcpServers": {"fin-checkup": _server(env)}})


CLAUDE_CODE_ADD = f"claude mcp add fin-checkup --scope user -- {onboarding.MCP_COMMAND}"

CLIENTS: dict[str, dict[str, Any]] = {
    "claude-code": {
        "label": "Claude Code",
        "install": [
            {
                "title": "MCP 서버 추가 (모든 프로젝트에서 쓰려면 --scope user)",
                "command": CLAUDE_CODE_ADD,
                "ask_first": True,
            },
            {
                "title": "또는 플러그인(MCP + 진단·비교·설치 스킬)",
                "command": "/plugin marketplace add younghwan91/fin-checkup  →  /plugin install fin-checkup@fin-checkup",
                "ask_first": True,
            },
            {"title": "확인", "command": "claude mcp list   # fin-checkup 이 보이면 됨"},
        ],
        "store_key": {
            "title": "키 넣기 — 사용자가 Claude Code 입력창에서 직접 (`!` 로 시작)",
            "command": f"! {onboarding.SETUP_ANYWHERE} --from-clipboard",
            "detail": "「인증키 관리」에서 키를 복사한 뒤 실행. 클립보드 → 본인만 읽는 파일(권한 600)로 곧장 가고 화면·대화에 나오지 않는다. "
            f"별도 터미널이 있으면 `{onboarding.SETUP_ANYWHERE}` (보이지 않는 입력창).",
        },
        "env_alternative": {
            "title": "환경변수로 넘기기 (선택) — 사용자가 자기 터미널에서 직접",
            "command": f"claude mcp add fin-checkup --scope user -e DART_API_KEY={KEY_PLACEHOLDER} -- {onboarding.MCP_COMMAND}",
            "detail": "키가 셸 기록과 ~/.claude.json 에 남는다. 사용자 설정 파일 방식이 더 안전하다.",
        },
        "restart": "Claude Code 를 다시 시작하거나 /mcp 에서 fin-checkup 을 다시 연결한다.",
    },
    "claude-desktop": {
        "label": "Claude Desktop",
        "config_paths": {
            "macos": "~/Library/Application Support/Claude/claude_desktop_config.json",
            "windows": "%APPDATA%\\Claude\\claude_desktop_config.json",
        },
        "install": [
            {
                "title": "설정 파일에 mcpServers 블록 추가 (파일이 있으면 합친다 — 바꾸기 전 diff 를 보여 주고 묻는다)",
                "snippet": _mcp_servers_block(),
                "ask_first": True,
            },
            {"title": "Claude Desktop 을 완전히 끝냈다가 다시 연다", "ask_first": False},
        ],
        "env_alternative": {
            "title": "환경변수로 넘기기 (선택) — 사용자가 설정 파일을 직접 고친다",
            "snippet": _mcp_servers_block({"DART_API_KEY": KEY_PLACEHOLDER}),
            "detail": "에이전트가 이 파일에 키를 쓰지 않는다. 사용자 설정 파일(`fin-checkup setup`)을 쓰면 여기엔 키가 필요 없다.",
        },
        "notes": [
            "GUI 앱은 셸 PATH 를 못 볼 수 있다 — uvx 를 못 찾으면 command 에 절대경로(`which uvx` / `where uvx`)를 넣는다.",
        ],
        "restart": "Claude Desktop 을 다시 연다.",
    },
    "cursor": {
        "label": "Cursor",
        "config_paths": {"user": "~/.cursor/mcp.json", "project": ".cursor/mcp.json"},
        "install": [
            {
                "title": "mcp.json 에 mcpServers 블록 추가 (바꾸기 전 diff 를 보여 주고 묻는다)",
                "snippet": _mcp_servers_block(),
                "ask_first": True,
            }
        ],
        "env_alternative": {
            "title": "환경변수로 넘기기 (선택) — 사용자가 직접",
            "snippet": _mcp_servers_block({"DART_API_KEY": KEY_PLACEHOLDER}),
        },
        "restart": "Cursor 설정 → MCP 에서 fin-checkup 을 켜거나 Cursor 를 다시 연다.",
    },
    "vscode": {
        "label": "VS Code (Copilot 에이전트 모드)",
        "config_paths": {"project": ".vscode/mcp.json"},
        "install": [
            {
                "title": ".vscode/mcp.json 에 servers 블록 추가 (바꾸기 전 diff 를 보여 주고 묻는다)",
                "snippet": _json({"servers": {"fin-checkup": _server(vscode=True)}}),
                "ask_first": True,
            }
        ],
        "env_alternative": {
            "title": "환경변수로 넘기기 (선택) — VS Code 가 시작할 때 비밀번호 칸으로 묻는다(파일에 키가 남지 않는다)",
            "snippet": _json(
                {
                    "inputs": [
                        {
                            "type": "promptString",
                            "id": "dart-api-key",
                            "description": "OpenDART 인증키",
                            "password": True,
                        }
                    ],
                    "servers": {
                        "fin-checkup": _server({"DART_API_KEY": "${input:dart-api-key}"}, vscode=True)
                    },
                }
            ),
        },
        "restart": "명령 팔레트 → MCP: List Servers → fin-checkup → Start.",
    },
    "codex": {
        "label": "Codex CLI",
        "config_paths": {"user": "~/.codex/config.toml"},
        "install": [
            {
                "title": "config.toml 에 추가 (바꾸기 전 diff 를 보여 주고 묻는다)",
                "snippet": "[mcp_servers.fin-checkup]\n"
                'command = "uvx"\n'
                f'args = ["--from", "{onboarding.MCP_FROM}", "fin-checkup-mcp"]\n',
                "ask_first": True,
            }
        ],
        "env_alternative": {
            "title": "환경변수로 넘기기 (선택) — 사용자가 직접",
            "snippet": f'[mcp_servers.fin-checkup]\nenv = {{ DART_API_KEY = "{KEY_PLACEHOLDER}" }}\n',
        },
        "restart": "Codex 를 다시 시작한다.",
    },
    "gemini": {
        "label": "Gemini CLI",
        "config_paths": {"user": "~/.gemini/settings.json", "project": ".gemini/settings.json"},
        "install": [
            {
                "title": "settings.json 에 mcpServers 블록 추가 (바꾸기 전 diff 를 보여 주고 묻는다)",
                "snippet": _mcp_servers_block(),
                "ask_first": True,
            },
        ],
        "env_alternative": {
            "title": "환경변수로 넘기기 (선택) — 셸 환경변수를 그대로 넘긴다($VAR 확장)",
            "snippet": _mcp_servers_block({"DART_API_KEY": "$DART_API_KEY"}),
        },
        "restart": "Gemini CLI 를 다시 시작하고 /mcp 로 확인한다.",
    },
}

_ALIASES = {
    "claudecode": "claude-code",
    "claude": "claude-code",
    "cc": "claude-code",
    "claudedesktop": "claude-desktop",
    "desktop": "claude-desktop",
    "claudeapp": "claude-desktop",
    "cursor": "cursor",
    "vscode": "vscode",
    "code": "vscode",
    "copilot": "vscode",
    "githubcopilot": "vscode",
    "codex": "codex",
    "codexcli": "codex",
    "openaicodex": "codex",
    "gemini": "gemini",
    "geminicli": "gemini",
}


def normalize_client(client: str | None) -> str | None:
    if not client:
        return None
    key = "".join(ch for ch in client.lower() if ch.isalnum())
    return _ALIASES.get(key)


def _uv_step() -> dict[str, Any]:
    return {
        "title": "uv 확인 — `uv --version`. 없으면 공식 설치 명령을 보여 주고, 사용자가 좋다고 한 뒤에 실행한다",
        "commands": dict(onboarding.UV_INSTALL),
        "ask_first": True,
    }


def _verify_steps(want_key: bool) -> list[dict[str, Any]]:
    steps = [
        {"title": "연결 확인 — setup_status() 를 부른다 (site.reachable 이 true 면 됨)"},
        {"title": '시험 진단 — diagnose_company("005930") 결과를 presentation_guidance 대로 보여 준다'},
    ]
    if want_key:
        steps.append(
            {
                "title": "키 확인 — 클라이언트를 다시 시작한 뒤 setup_status(check_key=true): dart_key.checked 가 'ok' 면 됨. "
                '그다음 diagnose_company("005930", year=2020) 으로 DART 에서 받아 오는지(source: dart-live) 본다',
            }
        )
    return steps


def _key_section(client_info: dict[str, Any] | None) -> dict[str, Any]:
    store = (client_info or {}).get("store_key") or {
        "title": "키 넣기 — 사용자가 자기 터미널에서 직접",
        "command": onboarding.SETUP_ANYWHERE,
        "detail": "보이지 않는 입력창(getpass)에 붙여 넣고 Enter. 저장소를 받았다면 "
        f"`{onboarding.SETUP_IN_REPO}`. 입력창이 안 뜨는 곳에서는 키를 복사한 뒤 `--from-clipboard`.",
    }
    return {
        "why": onboarding.NO_KEY_NEEDED,
        "get_key_steps": list(onboarding.KEY_STEPS),
        "open_url": onboarding.KEY_APPLY_URL,
        "store": {**store, "user_runs": True},
        "where_it_goes": f"{onboarding.user_env_file()} (작업 폴더에 .env 가 있으면 그 파일). MCP 서버가 이 파일을 읽으므로 "
        "클라이언트 설정에 키를 넣지 않아도 된다 — 클라이언트를 다시 시작하면 된다.",
        "env_alternative": (client_info or {}).get("env_alternative"),
        "safety": list(onboarding.KEY_SAFETY),
    }


def setup_guide(client: str | None = None, want_key: bool = False) -> dict[str, Any]:
    name = normalize_client(client)
    info = CLIENTS.get(name) if name else None
    out: dict[str, Any] = {
        "status": "ok",
        "client": name,
        "server_command": onboarding.MCP_COMMAND,
        "no_key_needed": onboarding.NO_KEY_NEEDED,
        "rules_for_agent": list(RULES_FOR_AGENT),
    }
    if info is None:
        out["status"] = "choose_client"
        out["message"] = (
            "어느 클라이언트인지 알려 주면 그 설정법을 줍니다. 모르면 사용자에게 묻거나 OS·설정 파일로 짐작합니다."
            if client is None
            else f"'{client}' 는 아는 클라이언트가 아닙니다. stdio 로 명령을 띄우는 클라이언트면 server_command 를 그대로 씁니다."
        )
        out["clients"] = {k: v["label"] for k, v in CLIENTS.items()}
        out["steps"] = [_uv_step(), *_verify_steps(want_key)]
    else:
        out["label"] = info["label"]
        if info.get("config_paths"):
            out["config_paths"] = info["config_paths"]
        out["steps"] = [_uv_step(), *info["install"], {"title": info["restart"]}, *_verify_steps(want_key)]
        if info.get("notes"):
            out["notes"] = info["notes"]
    if want_key:
        out["key"] = _key_section(info)
    out["docs"] = "https://github.com/younghwan91/fin-checkup/blob/main/docs/agents.md"
    out["disclaimer"] = DISCLAIMER
    return out
