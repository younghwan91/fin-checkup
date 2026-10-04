"""CLI 배선 테스트 — 명령이 캐시까지 제대로 이어지는지 확인한다."""

from __future__ import annotations

import pytest

from fin_checkup.cli import main
from fin_checkup.config import settings
from fin_checkup.models import CorpCode
from fin_checkup.storage import Cache


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "cli.duckdb"
    monkeypatch.setattr(settings, "fin_checkup_db_path", path)
    monkeypatch.setattr(settings, "dart_api_key", "")
    monkeypatch.setattr(settings, "sec_user_agent", "test test@example.com")
    return path


def seed(db, corps: int = 0) -> None:
    with Cache(db) as cache:
        cache.save_corp_codes(
            [
                CorpCode(corp_code=f"c{i}", corp_name=f"회사{i}", stock_code=f"00000{i}")
                for i in range(max(corps, 1))
            ]
        )


def test_watchlist_has_no_limit(db, capsys):
    """등록 개수에 상한이 없다."""
    seed(db, corps=5)
    with Cache(db) as cache:
        for i in range(9):
            cache.add_watch("local", CorpCode(corp_code=f"x{i}", corp_name=f"기존{i}"))

    assert main(["watch", "add", "000000"]) == 0
    assert "등록" in capsys.readouterr().out


def test_watch_list_and_remove(db, capsys):
    seed(db, corps=2)
    main(["watch", "add", "000000"])
    capsys.readouterr()
    main(["watch", "list"])
    assert "회사0" in capsys.readouterr().out
    main(["watch", "remove", "000000"])
    assert "해제" in capsys.readouterr().out


def test_operator_can_issue_a_key_for_a_locked_out_user(db, capsys):
    """키는 발급 때 한 번만 보이고, 같은 이메일로 /accounts 를 다시 칠 수 없다(409).
    키를 잃으면 API 로는 복구가 안 되므로 운영자 CLI 가 유일한 길이다."""
    from fin_checkup.auth import hash_key

    assert main(["account", "issue-key", "Young@Example.com"]) == 0
    out = capsys.readouterr().out
    key = next(tok for tok in out.split() if tok.startswith("fck_"))
    assert "새 계정" in out

    assert main(["account", "issue-key", "young@example.com"]) == 0
    out = capsys.readouterr().out
    second = next(tok for tok in out.split() if tok.startswith("fck_"))
    assert "기존 계정" in out and second != key

    with Cache(db) as cache:
        assert cache.resolve_api_key(hash_key(key)) == cache.resolve_api_key(hash_key(second))

    assert main(["account", "issue-key", "not-an-email"]) == 1
