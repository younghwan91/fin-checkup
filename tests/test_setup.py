"""`cli setup` — 설치 점검과 OpenDART 인증키를 안전하게 넣는 마법사.

지키는 것:
- 인증키는 어떤 출력(stdout·stderr·로그)에도 나오지 않는다 — 앞 몇 글자도. 약관 제19조, 그리고 에이전트가
  대신 돌리면 출력이 그대로 대화 기록에 남는다.
- `.env` 는 원자적으로 쓰고(임시 파일 → rename) 권한은 600. 다른 줄·주석은 그대로 둔다.
- 검증은 DART 호출 **한 번**(기업개황 company.json). 정상 / 키 오류(010·011) / 오늘 한도(020) 를 가른다.
- 터미널이 아니면(에이전트가 그냥 돌리면) 키를 묻지 않고 사용자가 직접 돌릴 명령을 알려 준다.
"""

from __future__ import annotations

import io
import logging
import os
import stat
import sys

import httpx
import pytest
import respx
from dotenv import dotenv_values

from fin_checkup import onboarding
from fin_checkup.cli import main
from fin_checkup.config import Settings
from fin_checkup.onboarding import KeyStatus, update_env_file, validate_key

DART = "https://opendart.fss.or.kr/api"
SITE = "https://younghwan91.github.io/fin-checkup/"
#: 진짜처럼 생긴 가짜 키 — 영문·숫자 40자.
FAKE_KEY = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
WINDOWS = sys.platform.startswith("win")


def company_ok() -> httpx.Response:
    return httpx.Response(200, json={"status": "000", "message": "정상", "corp_name": "삼성전자"})


def dart_status(code: str, message: str) -> httpx.Response:
    return httpx.Response(200, json={"status": code, "message": message})


def mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """사용자 설정 폴더·작업 폴더를 임시로. 환경변수의 키는 지운다."""
    config = tmp_path / "config"
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("FIN_CHECKUP_CONFIG_DIR", str(config))
    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.chdir(work)
    return tmp_path


def assert_no_key(*texts: str) -> None:
    for text in texts:
        assert FAKE_KEY not in text
        assert FAKE_KEY[:8] not in text  # 앞부분만 보여 주는 '가림' 도 하지 않는다


# ── .env 쓰기 ──────────────────────────────────────────────────────


def test_env_file_is_created_with_owner_only_permissions(tmp_path):
    target = tmp_path / "sub" / ".env"
    update_env_file(target, {"DART_API_KEY": FAKE_KEY})
    assert dotenv_values(target)["DART_API_KEY"] == FAKE_KEY
    if not WINDOWS:
        assert mode(target) == 0o600
    # 임시 파일이 남지 않는다
    assert [p.name for p in target.parent.iterdir()] == [".env"]


def test_env_merge_keeps_other_lines_and_replaces_the_key(tmp_path):
    target = tmp_path / ".env"
    target.write_text(
        "# 내 설정\n"
        "TELEGRAM_BOT_TOKEN=\n"
        "export DART_API_KEY=old0000000000000000000000000000000000000\n"
        "SEC_USER_AGENT=Name name@example.com\n"
        "DART_API_KEY=dup0000000000000000000000000000000000000\n"
        "FIN_CHECKUP_DB_PATH=data/x.duckdb\n",
        encoding="utf-8",
    )
    os.chmod(target, 0o644)
    update_env_file(target, {"DART_API_KEY": FAKE_KEY})
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "# 내 설정",
        "TELEGRAM_BOT_TOKEN=",
        f"DART_API_KEY={FAKE_KEY}",
        "SEC_USER_AGENT=Name name@example.com",  # 건드리지 않은 줄은 글자 그대로
        "FIN_CHECKUP_DB_PATH=data/x.duckdb",
    ]
    if not WINDOWS:
        assert mode(target) == 0o600


def test_values_with_spaces_are_quoted_so_the_file_stays_readable(tmp_path):
    """SEC_USER_AGENT 는 '이름 이메일' 이라 공백이 있다.

    따옴표 없이 쓰면 셸에서 source 할 때 깨진다(CLAUDE.md)."""
    target = tmp_path / ".env"
    update_env_file(target, {"SEC_USER_AGENT": 'Hong "GD" hong@example.com'})
    assert target.read_text(encoding="utf-8") == 'SEC_USER_AGENT="Hong \\"GD\\" hong@example.com"\n'
    assert dotenv_values(target)["SEC_USER_AGENT"] == 'Hong "GD" hong@example.com'


def test_env_values_with_newlines_are_refused(tmp_path):
    with pytest.raises(ValueError):
        update_env_file(tmp_path / ".env", {"DART_API_KEY": "abc\nINJECTED=1"})
    assert not (tmp_path / ".env").exists()


# ── 설정을 어디서 읽나 ─────────────────────────────────────────────


def test_settings_read_the_user_config_file_and_the_working_dir_env_wins(home):
    update_env_file(onboarding.user_env_file(), {"DART_API_KEY": FAKE_KEY, "DART_TIMEOUT": "7"})
    assert Settings().dart_api_key == FAKE_KEY
    (home / "work" / ".env").write_text("DART_TIMEOUT=9\n", encoding="utf-8")
    s = Settings()
    assert s.dart_api_key == FAKE_KEY and s.dart_timeout == 9.0


def test_default_target_is_the_working_dir_env_only_when_it_exists(home):
    assert onboarding.default_env_target() == onboarding.user_env_file()
    (home / "work" / ".env").write_text("", encoding="utf-8")
    assert onboarding.default_env_target() == home / "work" / ".env"


def test_key_source_names_where_the_key_came_from_without_the_value(home, monkeypatch):
    assert onboarding.key_source() is None
    update_env_file(onboarding.user_env_file(), {"DART_API_KEY": FAKE_KEY})
    assert onboarding.key_source() == str(onboarding.user_env_file())
    monkeypatch.setenv("DART_API_KEY", FAKE_KEY)
    assert onboarding.key_source() == "환경변수 DART_API_KEY"


# ── 키 검증: DART 한 번 ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (company_ok(), KeyStatus.OK),
        (dart_status("010", "등록되지 않은 키입니다."), KeyStatus.INVALID),
        (dart_status("011", "사용할 수 없는 키입니다."), KeyStatus.INVALID),
        (dart_status("020", "요청 제한을 초과하였습니다."), KeyStatus.QUOTA),
        (dart_status("012", "접근할 수 없는 IP입니다."), KeyStatus.BLOCKED_IP),
        (dart_status("800", "시스템 점검"), KeyStatus.ERROR),
        (httpx.Response(503), KeyStatus.ERROR),
    ],
)
async def test_validation_makes_exactly_one_call(response, expected, caplog):
    caplog.set_level(logging.DEBUG)
    with respx.mock(assert_all_called=True) as router:
        route = router.get(f"{DART}/company.json").mock(return_value=response)
        check = await validate_key(FAKE_KEY)
    assert check.status is expected
    assert route.call_count == 1  # 재시도하지 않는다 — 남은 호출량을 태우지 않게
    assert route.calls[0].request.url.params["corp_code"] == onboarding.VALIDATION_CORP_CODE
    assert_no_key(check.message, caplog.text)


async def test_network_failure_is_reported_without_the_url():
    with respx.mock:
        respx.get(f"{DART}/company.json").mock(side_effect=httpx.ConnectError(f"boom crtfc_key={FAKE_KEY}"))
        check = await validate_key(FAKE_KEY)
    assert check.status is KeyStatus.UNREACHABLE
    assert_no_key(check.message)


async def test_something_that_does_not_look_like_a_key_is_never_sent():
    with respx.mock:  # 라우트 없음 = 호출하면 실패
        check = await validate_key("hunter2 my bank password")
    assert check.status is KeyStatus.MALFORMED


# ── cli setup ─────────────────────────────────────────────────────


def run(argv, capsys, caplog, stdin: str | None = None, monkeypatch=None, tty: bool = False):
    if monkeypatch is not None:
        fake = io.StringIO(stdin or "")
        fake.isatty = lambda: tty  # type: ignore[method-assign]
        monkeypatch.setattr(sys, "stdin", fake)
    caplog.set_level(logging.DEBUG)
    code = main(argv)
    out, err = capsys.readouterr()
    assert_no_key(out, err, caplog.text)
    return code, out


def test_key_stdin_validates_saves_and_never_prints_the_key(home, capsys, caplog, monkeypatch):
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{DART}/company.json").mock(return_value=company_ok())
        code, out = run(["setup", "--key-stdin"], capsys, caplog, FAKE_KEY + "\n", monkeypatch)
    assert code == 0
    assert route.call_count == 1
    target = onboarding.user_env_file()
    assert dotenv_values(target)["DART_API_KEY"] == FAKE_KEY
    assert str(target) in out and "유효" in out
    if not WINDOWS:
        assert mode(target) == 0o600


def test_invalid_key_is_not_saved(home, capsys, caplog, monkeypatch):
    with respx.mock:
        respx.get(f"{DART}/company.json").mock(return_value=dart_status("010", "등록되지 않은 키입니다."))
        code, out = run(["setup", "--key-stdin"], capsys, caplog, FAKE_KEY, monkeypatch)
    assert code == 1
    assert "저장하지 않았습니다" in out
    assert not onboarding.user_env_file().exists()


def test_quota_exceeded_means_the_key_works_so_it_is_saved(home, capsys, caplog, monkeypatch):
    with respx.mock:
        respx.get(f"{DART}/company.json").mock(return_value=dart_status("020", "요청 제한을 초과하였습니다."))
        code, out = run(["setup", "--key-stdin"], capsys, caplog, FAKE_KEY, monkeypatch)
    assert code == 0
    assert "한도" in out
    assert dotenv_values(onboarding.user_env_file())["DART_API_KEY"] == FAKE_KEY


def test_existing_working_dir_env_is_updated_in_place(home, capsys, caplog, monkeypatch):
    env = home / "work" / ".env"
    env.write_text("SEC_USER_AGENT=Name name@example.com\n", encoding="utf-8")
    with respx.mock:
        respx.get(f"{DART}/company.json").mock(return_value=company_ok())
        code, _ = run(["setup", "--key-stdin"], capsys, caplog, FAKE_KEY, monkeypatch)
    assert code == 0
    assert env.read_text(encoding="utf-8").splitlines() == [
        "SEC_USER_AGENT=Name name@example.com",
        f"DART_API_KEY={FAKE_KEY}",
    ]


def test_without_a_terminal_it_does_not_ask_and_tells_the_user_what_to_run(home, capsys, caplog, monkeypatch):
    """에이전트가 그냥 `setup` 을 돌리면 키를 받을 길이 없다 — 멈추지 말고, 사용자가 직접 돌릴 명령을 준다."""
    with respx.mock:
        code, out = run(["setup"], capsys, caplog, "", monkeypatch, tty=False)
    assert code == 2
    assert onboarding.KEY_APPLY_URL in out
    assert "--from-clipboard" in out
    assert not onboarding.user_env_file().exists()


def test_interactive_reads_the_key_with_getpass(home, capsys, caplog, monkeypatch):
    prompts: list[str] = []
    answers = iter(["y", ""])  # 키를 넣을까요? → y, SEC User-Agent → 건너뜀
    monkeypatch.setattr("builtins.input", lambda prompt="": (prompts.append(prompt), next(answers))[1])
    monkeypatch.setattr(
        onboarding.getpass, "getpass", lambda prompt="": (prompts.append(prompt), FAKE_KEY)[1]
    )
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SITE}build.json").mock(return_value=httpx.Response(200, json={"fiscal_year": 2025}))
        router.get(f"{DART}/company.json").mock(return_value=company_ok())
        code, out = run(["setup"], capsys, caplog, "", monkeypatch, tty=True)
    assert code == 0
    assert onboarding.KEY_APPLY_URL in out
    assert "키 없이" in out  # 사이트·MCP 는 키가 필요 없다는 말이 먼저
    assert any("보이지 않" in p for p in prompts)
    assert dotenv_values(onboarding.user_env_file())["DART_API_KEY"] == FAKE_KEY


def test_from_clipboard_saves_a_key_and_refuses_anything_else(home, capsys, caplog, monkeypatch):
    monkeypatch.setattr(onboarding, "read_clipboard", lambda: "my bank password 1234")
    with respx.mock:  # 키 모양이 아니면 DART 로 보내지 않는다
        code, out = run(["setup", "--from-clipboard"], capsys, caplog, "", monkeypatch)
    assert code == 1
    assert "my bank password" not in out
    assert not onboarding.user_env_file().exists()

    monkeypatch.setattr(onboarding, "read_clipboard", lambda: f"  {FAKE_KEY}\n")
    with respx.mock:
        respx.get(f"{DART}/company.json").mock(return_value=company_ok())
        code, out = run(["setup", "--from-clipboard"], capsys, caplog, "", monkeypatch)
    assert code == 0
    assert dotenv_values(onboarding.user_env_file())["DART_API_KEY"] == FAKE_KEY


def test_sec_user_agent_option_is_saved_quoted(home, capsys, caplog, monkeypatch):
    with respx.mock:
        respx.get(f"{DART}/company.json").mock(return_value=company_ok())
        code, _ = run(
            ["setup", "--key-stdin", "--sec-user-agent", "Hong Gildong hong@example.com"],
            capsys,
            caplog,
            FAKE_KEY,
            monkeypatch,
        )
    assert code == 0
    values = dotenv_values(onboarding.user_env_file())
    assert values["SEC_USER_AGENT"] == "Hong Gildong hong@example.com"


# ── cli setup --check ─────────────────────────────────────────────


def test_check_without_a_key(home, capsys, caplog, monkeypatch):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SITE}build.json").mock(
            return_value=httpx.Response(200, json={"fiscal_year": 2025, "built_on": "2026-11-21"})
        )
        code, out = run(["setup", "--check"], capsys, caplog, "", monkeypatch)
    assert code == 0
    assert "DART 키: 없음" in out
    assert "연결됨" in out and "2025" in out
    assert "캐시" in out


def test_check_with_a_valid_key_reports_only_ok(home, capsys, caplog, monkeypatch):
    update_env_file(onboarding.user_env_file(), {"DART_API_KEY": FAKE_KEY})
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SITE}build.json").mock(return_value=httpx.Response(503))
        route = router.get(f"{DART}/company.json").mock(return_value=company_ok())
        code, out = run(["setup", "--check"], capsys, caplog, "", monkeypatch)
    assert code == 0
    assert route.call_count == 1
    assert "DART 키: 설정됨" in out and "유효" in out
    assert str(onboarding.user_env_file()) in out
    assert "연결 안 됨" in out


def test_check_with_an_invalid_key(home, capsys, caplog, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", FAKE_KEY)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SITE}build.json").mock(return_value=httpx.Response(200, json={}))
        router.get(f"{DART}/company.json").mock(return_value=dart_status("011", "사용할 수 없는 키입니다."))
        code, out = run(["setup", "--check"], capsys, caplog, "", monkeypatch)
    assert code == 1
    assert "무효" in out and "환경변수" in out


def test_setup_text_has_no_banned_phrases():
    from fin_checkup.explain.banned import find_outside_allowed

    for text in onboarding.texts():
        assert find_outside_allowed(text) == [], text
