"""설치 점검과 OpenDART 인증키 넣기 — `cli setup` 이 쓴다.

공개 사이트·MCP 서버는 키 없이 된다. 개인 인증키가 있으면 사이트에 없는 회사·연도(사이트는 회사마다 최신 사업연도
하나만 싣는다)를 그때그때 DART 에서 받아 올 수 있다. 그 키를 안전하게 넣는 것이 이 모듈의 일이다.

지키는 것:
- 인증키는 어떤 출력에도 내지 않는다 — 앞 몇 글자도. 약관 제19조(남이 쓰게 하지 않는다), 그리고 에이전트가 대신
  돌리면 출력이 대화 기록에 그대로 남는다. 키는 getpass(화면에 안 보임)·표준입력·클립보드로만 받는다.
- `.env` 는 원자적으로 쓴다(같은 폴더 임시 파일 → `os.replace`). 권한 600, 다른 줄은 글자 그대로 둔다.
- 검증은 DART 호출 한 번(기업개황 `company.json`, 삼성전자). 재시도하지 않는다 — 남은 호출량을 태우지 않게.

키를 어디에 쓰나: 작업 폴더에 `.env` 가 있으면 그 파일(그게 먼저 읽히므로), 없으면 사용자 설정 파일
(`user_env_file()` — 저장소를 받지 않고 `uvx` 로 쓰는 MCP 서버도 이 파일을 읽는다).
"""

from __future__ import annotations

import getpass
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OPENDART_URL = "https://opendart.fss.or.kr"
#: 「인증키 신청/관리」 → 「인증키 신청」 (2026-11-22 사이트 메뉴 그대로).
KEY_APPLY_URL = f"{OPENDART_URL}/uss/umt/EgovMberInsertView.do"
#: 「인증키 신청/관리」 → 「인증키 관리」 — 발급된 키를 보고 복사하는 곳.
KEY_MANAGE_URL = f"{OPENDART_URL}/mng/userApiKeyListView.do"
#: 「인증키 신청/관리」 → 「오픈API 이용현황」.
USAGE_URL = f"{OPENDART_URL}/mng/apiUsageStatusView.do"
SITE_URL = "https://younghwan91.github.io/fin-checkup/"
REPO_FROM = "fin-checkup @ git+https://github.com/younghwan91/fin-checkup"
MCP_FROM = "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup"
MCP_COMMAND = f'uvx --from "{MCP_FROM}" fin-checkup-mcp'
#: 저장소를 받은 사람 / 받지 않은 사람이 마법사를 띄우는 명령.
SETUP_IN_REPO = "uv run python -m fin_checkup.cli setup"
SETUP_ANYWHERE = f'uvx --from "{REPO_FROM}" fin-checkup setup'
UV_INSTALL = {
    "macos_linux": "curl -LsSf https://astral.sh/uv/install.sh | sh",
    "windows": 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
}
#: 검증에 쓰는 회사 — 삼성전자 고유번호. 어느 키로든 늘 데이터가 있는 곳.
VALIDATION_CORP_CODE = "00126380"
#: OpenDART 인증키는 영문·숫자 40자다. 조금 넓게 받되, 공백·기호가 든 것(다른 비밀번호 등)은 DART 로 보내지 않는다.
_KEY_SHAPE = re.compile(r"[A-Za-z0-9]{30,64}")
#: 따옴표 없이 써도 되는 값.
_BARE_VALUE = re.compile(r"[A-Za-z0-9_./:@+-]*")

#: 키를 받는 법 — 2026-11-22 OpenDART 사이트에서 확인한 메뉴 이름·문구 그대로.
KEY_STEPS: tuple[str, ...] = (
    f"{OPENDART_URL} 에 들어가 위 메뉴 「인증키 신청/관리」 → 「인증키 신청」을 엽니다 ({KEY_APPLY_URL}).",
    "사용자 구분 「개인」을 고르고 이메일(중복확인)·비밀번호·API 사용환경·API 사용용도를 넣은 뒤 「등록」을 누릅니다. "
    "사이트 안내: '개인회원 : 계정신청 완료 후 즉시 발급', '인증키 신청 결과는 메일로 발송됩니다.' "
    "(기업회원은 담당자 승인 1~2영업일 뒤 발급)",
    f"로그인한 뒤 「인증키 신청/관리」 → 「인증키 관리」({KEY_MANAGE_URL})에서 영문·숫자 40자 인증키를 복사합니다.",
    "무료입니다. 하루 약 20,000건까지 부를 수 있습니다 (DART 개발가이드: '일반적으로는 20,000건 이상의 요청에 대하여' "
    f"status 020). 오늘 쓴 양은 「오픈API 이용현황」({USAGE_URL})에서 봅니다.",
    "인증키는 본인만 씁니다 — 남에게 주거나 함께 쓰지 않습니다(이용약관 제19조). 한 사람이 키를 둘 이상 받을 수 없습니다.",
)

KEY_SAFETY: tuple[str, ...] = (
    "키를 채팅창·메일·이슈에 붙여 넣지 마세요. 에이전트에게도 보여 주지 않습니다.",
    "키는 이 명령이 화면에 보이지 않는 입력창(getpass)으로 받아 본인만 읽을 수 있는 파일(권한 600)에 넣습니다.",
    "`.env` 파일은 git 에 올리지 않습니다 (이 저장소의 .gitignore 에 들어 있습니다).",
)

NO_KEY_NEEDED = (
    "공개 사이트와 MCP 서버(에이전트용)는 키 없이 됩니다 — 상장사마다 최신 사업연도 진단이 들어 있습니다. "
    "개인 인증키는 사이트에 없는 회사·지난 사업연도·막 나온 공시를 DART 에서 직접 받아 올 때만 필요합니다."
)


class KeyStatus(str, Enum):
    OK = "ok"
    INVALID = "invalid"
    QUOTA = "quota"
    BLOCKED_IP = "ip"
    UNREACHABLE = "unreachable"
    ERROR = "error"
    MALFORMED = "malformed"


#: 상태마다 사용자에게 보일 말. 키 값은 절대 넣지 않는다.
STATUS_TEXT: dict[KeyStatus, str] = {
    KeyStatus.OK: "유효 — DART 가 이 키로 정상 응답했습니다.",
    KeyStatus.INVALID: "무효 — DART 가 등록되지 않았거나 쓸 수 없는 키라고 답했습니다 (status {status}). "
    "「인증키 관리」 화면에서 키를 다시 복사해 보세요.",
    KeyStatus.QUOTA: "유효 — 다만 오늘 호출 한도를 넘었습니다 (status 020). 한도는 자정(KST)에 다시 찹니다.",
    KeyStatus.BLOCKED_IP: "이 컴퓨터의 IP 에서는 쓸 수 없는 키입니다 (status 012). 기업회원 키라면 「인증키 관리」에서 요청 IP 를 확인하세요.",
    KeyStatus.UNREACHABLE: "DART 에 연결하지 못했습니다 ({detail}). 네트워크를 확인한 뒤 다시 하세요.",
    KeyStatus.ERROR: "DART 가 예상 밖의 응답을 줬습니다 ({detail}). 잠시 뒤 다시 하세요.",
    KeyStatus.MALFORMED: "인증키 모양(영문·숫자 40자)이 아닙니다. DART 로 보내지 않았습니다.",
}

#: 키가 쓸 만하다고 보고 저장하는 상태.
SAVABLE = frozenset({KeyStatus.OK, KeyStatus.QUOTA})


@dataclass(frozen=True)
class KeyCheck:
    status: KeyStatus
    message: str

    @property
    def usable(self) -> bool:
        return self.status in SAVABLE


def looks_like_key(value: str) -> bool:
    return bool(_KEY_SHAPE.fullmatch(value or ""))


def _check(kind: KeyStatus, **fields: str) -> KeyCheck:
    return KeyCheck(kind, STATUS_TEXT[kind].format(**{"status": "", "detail": "", **fields}))


async def validate_key(key: str, *, base_url: str | None = None, timeout: float = 15.0) -> KeyCheck:
    """DART 를 한 번 불러 키가 되는지 본다. 결과 문장에는 키가 들어가지 않는다."""
    from fin_checkup.config import Settings
    from fin_checkup.dart.client import DartClient, DartError, RateLimitExceeded
    from fin_checkup.dart.retry import RetryPolicy

    key = (key or "").strip()
    if not looks_like_key(key):
        return _check(KeyStatus.MALFORMED)
    overrides: dict[str, Any] = {"dart_api_key": key, "dart_min_delay": 0.0, "dart_timeout": timeout}
    if base_url:
        overrides["dart_base_url"] = base_url
    settings = Settings(_env_file=None, **overrides)
    try:
        async with DartClient(settings=settings, retry_policy=RetryPolicy(max_attempts=1)) as client:
            await client.fetch_company(VALIDATION_CORP_CODE)
    except RateLimitExceeded:
        return _check(KeyStatus.QUOTA)
    except DartError as exc:
        if exc.status in {"010", "011", "901"}:
            return _check(KeyStatus.INVALID, status=exc.status)
        if exc.status == "012":
            return _check(KeyStatus.BLOCKED_IP)
        return _check(KeyStatus.ERROR, detail=f"status {exc.status}")
    except httpx.HTTPStatusError as exc:
        return _check(KeyStatus.ERROR, detail=f"HTTP {exc.response.status_code}")
    except (httpx.HTTPError, OSError) as exc:
        # 예외 문장은 요청 URL(=crtfc_key)을 담을 수 있다 — 종류 이름만.
        return _check(KeyStatus.UNREACHABLE, detail=type(exc).__name__)
    except ValueError:  # 응답이 JSON 이 아님
        return _check(KeyStatus.ERROR, detail="JSON 이 아닌 응답")
    return _check(KeyStatus.OK)


# ── 설정 파일 ─────────────────────────────────────────────────────


def user_config_dir(env: dict[str, str] | None = None) -> Path:
    """사용자 설정 폴더. `FIN_CHECKUP_CONFIG_DIR` > Windows `%APPDATA%\\fin-checkup` > `$XDG_CONFIG_HOME|~/.config/fin-checkup`."""
    env = dict(os.environ if env is None else env)
    explicit = env.get("FIN_CHECKUP_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser()
    if os.name == "nt" and env.get("APPDATA"):
        return Path(env["APPDATA"]) / "fin-checkup"
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "fin-checkup"


def user_env_file(env: dict[str, str] | None = None) -> Path:
    return user_config_dir(env) / ".env"


def env_files() -> tuple[Path, ...]:
    """`Settings` 가 읽는 파일 — 뒤에 있는 것이 이긴다(작업 폴더 `.env` 가 사용자 설정을 덮는다)."""
    return (user_env_file(), Path(".env"))


def default_env_target() -> Path:
    """키를 쓸 파일. 작업 폴더에 `.env` 가 있으면 그것(먼저 읽히므로), 없으면 사용자 설정 파일."""
    local = Path.cwd() / ".env"
    return local if local.is_file() else user_env_file()


def key_source() -> str | None:
    """DART 키가 어디서 오는지 — '환경변수 DART_API_KEY' 또는 파일 경로. 값은 돌려주지 않는다."""
    from dotenv import dotenv_values

    if os.environ.get("DART_API_KEY", "").strip():
        return "환경변수 DART_API_KEY"
    for path in reversed(env_files()):
        if path.is_file() and (dotenv_values(path).get("DART_API_KEY") or "").strip():
            return str(path.resolve() if not path.is_absolute() else path)
    return None


def format_env_value(value: str) -> str:
    if any(ch in value for ch in "\r\n\0"):
        raise ValueError("설정 값에 줄바꿈을 넣을 수 없습니다")
    if _BARE_VALUE.fullmatch(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def update_env_file(path: Path, updates: dict[str, str]) -> Path:
    """`KEY=value` 줄을 바꾸거나 덧붙인다. 다른 줄은 글자 그대로, 같은 키가 또 나오면 지운다. 권한 600.

    같은 폴더의 임시 파일에 다 쓴 뒤 `os.replace` 로 바꾼다 — 쓰다가 죽어도 반쯤 쓴 `.env` 가 남지 않는다.
    """
    rendered = {k: format_env_value(v) for k, v in updates.items()}
    path = Path(path)
    if path.is_symlink():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.is_file() else []
    patterns = {k: re.compile(rf"^\s*(?:export\s+)?{re.escape(k)}\s*=") for k in rendered}

    out: list[str] = []
    written: set[str] = set()
    for line in lines:
        key = next((k for k, p in patterns.items() if p.match(line)), None)
        if key is None:
            out.append(line)
        elif key not in written:
            out.append(f"{key}={rendered[key]}\n")
            written.add(key)
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    out.extend(f"{k}={v}\n" for k, v in rendered.items() if k not in written)

    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(out))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    if os.name != "nt":
        os.chmod(path, 0o600)
    return path


# ── 클립보드 ──────────────────────────────────────────────────────


class ClipboardUnavailable(RuntimeError):
    pass


def _clipboard_commands() -> list[list[str]]:
    if sys.platform == "darwin":
        return [["pbpaste"]]
    if os.name == "nt":
        return [["powershell", "-NoProfile", "-Command", "Get-Clipboard"]]
    return [
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
        ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard"],  # WSL
    ]


def read_clipboard() -> str:
    """클립보드 글자. 내용은 로그에 남기지 않는다."""
    for cmd in _clipboard_commands():
        if shutil.which(cmd[0]) is None:
            continue
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return done.stdout
    raise ClipboardUnavailable(
        "클립보드를 읽을 도구를 찾지 못했습니다 (macOS pbpaste · Windows PowerShell · Linux wl-paste/xclip/xsel)."
    )


# ── 점검 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SiteCheck:
    reachable: bool
    detail: str
    fiscal_year: int | None = None
    built_on: str | None = None


def check_site(base_url: str = SITE_URL, timeout: float = 10.0) -> SiteCheck:
    """공개 사이트(MCP 기본 출처)의 build.json 을 한 번 받아 본다."""
    url = (base_url if base_url.endswith("/") else base_url + "/") + "build.json"
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001 — 점검은 무엇이 터져도 '연결 안 됨' 으로 보고한다
        return SiteCheck(False, type(exc).__name__)
    if resp.status_code >= 400:
        return SiteCheck(False, f"HTTP {resp.status_code}")
    try:
        info = resp.json()
    except ValueError:
        return SiteCheck(False, "JSON 이 아닌 응답")
    info = info if isinstance(info, dict) else {}
    return SiteCheck(True, "", info.get("fiscal_year"), info.get("built_on"))


def uv_path() -> str | None:
    return shutil.which("uv")


def python_ok() -> bool:
    return sys.version_info >= (3, 11)


def texts() -> Iterator[str]:
    """사용자에게 보이는 고정 문장 — 금지 표현 검사용."""
    yield from KEY_STEPS
    yield from KEY_SAFETY
    yield NO_KEY_NEEDED
    yield from STATUS_TEXT.values()


# ── 마법사 ────────────────────────────────────────────────────────


def _say(*lines: str) -> None:
    for line in lines:
        print(line)


def _print_key_steps() -> None:
    _say("", "OpenDART 인증키 받기 (무료, 개인은 신청 즉시 발급):")
    for i, step in enumerate(KEY_STEPS, 1):
        _say(f"  {i}. {step}")
    _say("", "키를 다룰 때:")
    for line in KEY_SAFETY:
        _say(f"  - {line}")


def _print_how_to_enter() -> None:
    _say(
        "",
        "키를 넣는 법 — 키가 대화 기록·명령 기록에 남지 않게, 본인이 직접 실행합니다:",
        f"  - 터미널에서:            {SETUP_ANYWHERE}",
        f"    (저장소를 받았다면:    {SETUP_IN_REPO})",
        "    → 보이지 않는 입력창에 키를 붙여 넣고 Enter.",
        "  - Claude Code 안에서는 입력창이 뜨지 않을 수 있습니다. 「인증키 관리」에서 키를 복사한 뒤:",
        f"      ! {SETUP_ANYWHERE} --from-clipboard",
        "    (클립보드에서 곧장 파일로 갑니다. 키가 화면·대화에 나오지 않습니다.)",
    )


def _print_environment() -> None:
    v = sys.version_info
    _say(
        f"  Python   {v.major}.{v.minor}.{v.micro} — "
        + ("됨 (3.11 이상)" if python_ok() else "3.11 이상이 필요합니다")
    )
    uv = uv_path()
    if uv:
        _say(f"  uv       {uv}")
    else:
        _say(
            "  uv       없음 — MCP 서버를 띄우는 uvx 가 uv 에 들어 있습니다. 공식 설치 명령:",
            f"             macOS·Linux: {UV_INSTALL['macos_linux']}",
            f"             Windows:     {UV_INSTALL['windows']}",
        )


def _print_site(site: SiteCheck) -> None:
    if site.reachable:
        extra = ", ".join(
            x
            for x in (
                f"사업연도 {site.fiscal_year}" if site.fiscal_year else "",
                f"빌드 {site.built_on}" if site.built_on else "",
            )
            if x
        )
        _say(f"  공개 사이트  연결됨{f' — {extra}' if extra else ''} (MCP 기본 출처, 키 불필요)")
    else:
        _say(
            f"  공개 사이트  연결 안 됨 ({site.detail}) — 네트워크를 확인하세요. MCP 는 예전에 받은 사본이 있으면 그걸 씁니다."
        )


async def save_key(key: str, target: Path, *, sec_user_agent: str = "", verify: bool = True) -> int:
    """키를 검증하고 쓸 만하면 저장한다. 0 = 저장, 1 = 저장 안 함."""
    key = (key or "").strip()
    if not looks_like_key(key):
        _say(f"키 확인: {STATUS_TEXT[KeyStatus.MALFORMED]}", "저장하지 않았습니다.")
        return 1
    if verify:
        check = await validate_key(key)
        _say(f"키 확인: {check.message}")
        if not check.usable:
            _say("저장하지 않았습니다.")
            return 1
    else:
        _say("키 확인: 건너뜀 (--no-verify)")
    updates = {"DART_API_KEY": key}
    if sec_user_agent.strip():
        updates["SEC_USER_AGENT"] = sec_user_agent.strip()
    written = update_env_file(target, updates)
    names = " · ".join(updates)
    _say(f"저장했습니다: {written} ({names}, 권한 600 — 본인만 읽기)")
    source = key_source()
    if source and source != str(written):
        _say(f"주의: 지금은 {source} 의 키가 먼저 쓰입니다. 그쪽을 지우거나 같은 키로 바꾸세요.")
    _say(
        "",
        "MCP 서버도 이 파일을 읽습니다 — 클라이언트 설정에 키를 따로 넣지 않아도 됩니다. 클라이언트를 다시 시작하면",
        "사이트에 없는 사업연도를 물을 때 DART 에서 받아 옵니다. 점검: " + SETUP_ANYWHERE + " --check",
    )
    return 0


async def run_check() -> int:
    from fin_checkup.config import Settings

    settings = Settings()
    _say("fin-checkup 설정 점검 (비밀 값은 보여 주지 않습니다)", "")
    _print_environment()
    _print_site(check_site())
    code = 0
    if settings.has_api_key:
        check = await validate_key(settings.dart_api_key)
        verdict = {
            KeyStatus.OK: "유효",
            KeyStatus.QUOTA: "유효(오늘 한도 초과)",
            KeyStatus.INVALID: "무효",
        }.get(check.status, "확인 못 함")
        _say(f"  DART 키: 설정됨 (출처: {key_source() or '설정'}) · {verdict}", f"           {check.message}")
        if check.status in {KeyStatus.INVALID, KeyStatus.MALFORMED, KeyStatus.BLOCKED_IP}:
            code = 1
    else:
        _say(f"  DART 키: 없음 — 사이트·MCP 는 키 없이 됩니다. 넣으려면 직접 실행: {SETUP_ANYWHERE}")
    _say(
        f"  SEC User-Agent: {'설정됨' if settings.has_sec_user_agent else '없음 (미국 기업 진단 때만 필요)'}"
    )
    db = Path(settings.fin_checkup_db_path)
    _say(f"  캐시 DB: {db.resolve()} ({'있음' if db.is_file() else '아직 없음'})")
    user_file = user_env_file()
    _say(f"  사용자 설정 파일: {user_file} ({'있음' if user_file.is_file() else '없음'})")
    return code


async def run_setup(
    *,
    check: bool = False,
    key_stdin: bool = False,
    from_clipboard: bool = False,
    env_file: str | None = None,
    sec_user_agent: str = "",
    verify: bool = True,
) -> int:
    if check:
        return await run_check()
    target = Path(env_file).expanduser() if env_file else default_env_target()

    if key_stdin:
        return await save_key(sys.stdin.readline(), target, sec_user_agent=sec_user_agent, verify=verify)
    if from_clipboard:
        try:
            value = read_clipboard()
        except ClipboardUnavailable as exc:
            _say(str(exc), f"대신 터미널에서 {SETUP_ANYWHERE} 를 실행해 보이지 않는 입력창에 붙여 넣으세요.")
            return 1
        return await save_key(value, target, sec_user_agent=sec_user_agent, verify=verify)

    _say("fin-checkup 설치 도우미", "", NO_KEY_NEEDED, "", f"  에이전트(MCP) 연결: {MCP_COMMAND}", "")
    if not sys.stdin.isatty():
        # 에이전트가 그냥 돌린 경우 — 키를 받을 길이 없고, 받으려 들면 안 된다.
        _print_key_steps()
        _print_how_to_enter()
        _say("", "터미널이 아니라 여기서는 키를 묻지 않습니다. 위 명령을 본인이 직접 실행하세요.")
        return 2

    _print_environment()
    _print_site(check_site())
    current = key_source()
    question = (
        f"\nDART 키가 이미 있습니다 (출처: {current}). 새 키로 바꿀까요? [y/N] "
        if current
        else "\n개인 DART 인증키를 지금 넣을까요? (없어도 됩니다) [y/N] "
    )
    if input(question).strip().lower() not in {"y", "yes", "예", "ㅇ"}:
        _say("키 없이 마칩니다. 나중에 넣으려면 같은 명령을 다시 실행하세요.")
        return 0
    _print_key_steps()
    key = getpass.getpass("\nDART 인증키를 붙여 넣고 Enter (입력해도 화면에 보이지 않습니다): ")
    if not sec_user_agent:
        sec_user_agent = input(
            "SEC EDGAR User-Agent — 미국 기업 진단 때만 필요. '이름 이메일' (비우면 건너뜀): "
        )
    return await save_key(key, target, sec_user_agent=sec_user_agent, verify=verify)
