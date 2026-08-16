from __future__ import annotations

import pytest

from fin_checkup.auth import (
    KEY_PREFIX,
    InvalidEmail,
    generate_key,
    hash_key,
    looks_like_key,
    normalize_email,
    user_id_for,
    verify_key,
)
from fin_checkup.storage import Cache


@pytest.fixture
def cache(tmp_path):
    with Cache(tmp_path / "auth.duckdb") as c:
        yield c


# ── 이메일 ────────────────────────────────────────────────────────


def test_email_is_normalized():
    assert normalize_email("  Young@Example.COM ") == "young@example.com"


@pytest.mark.parametrize("bad", ["", "없음", "a@b", "a b@c.com", "@x.com", "x@.com"])
def test_bad_emails_are_rejected(bad):
    with pytest.raises(InvalidEmail):
        normalize_email(bad)


def test_user_id_is_stable_and_case_insensitive():
    assert user_id_for("a@b.com") == user_id_for("A@B.COM")
    assert user_id_for("a@b.com") != user_id_for("c@d.com")


def test_user_id_does_not_leak_the_email():
    uid = user_id_for("young@example.com")
    assert "young" not in uid
    assert "@" not in uid
    assert len(uid) == 32


# ── API 키 ────────────────────────────────────────────────────────


def test_generated_keys_are_unique_and_prefixed():
    keys = {generate_key("u1").plaintext for _ in range(100)}
    assert len(keys) == 100
    assert all(k.startswith(KEY_PREFIX) for k in keys)


def test_key_hash_is_not_the_key():
    issued = generate_key("u1")
    assert issued.key_hash != issued.plaintext
    assert issued.plaintext not in issued.key_hash


def test_verify_key_matches_only_the_original():
    issued = generate_key("u1")
    assert verify_key(issued.plaintext, issued.key_hash) is True
    assert verify_key(issued.plaintext + "x", issued.key_hash) is False
    assert verify_key(generate_key("u1").plaintext, issued.key_hash) is False


def test_masked_form_hides_the_middle():
    issued = generate_key("u1")
    assert issued.plaintext not in issued.masked
    assert issued.masked.startswith(KEY_PREFIX)


def test_looks_like_key():
    assert looks_like_key(generate_key("u1").plaintext) is True
    assert looks_like_key("not-a-key") is False
    assert looks_like_key(KEY_PREFIX) is False


# ── 저장 ──────────────────────────────────────────────────────────


def test_account_roundtrip(cache: Cache):
    uid = user_id_for("young@example.com")
    assert cache.create_account(uid, "young@example.com") is True
    assert cache.create_account(uid, "young@example.com") is False, "중복 생성은 False"
    account = cache.get_account(uid)
    assert account is not None and account[1] == "young@example.com"


def test_api_key_resolves_to_its_owner(cache: Cache):
    uid = user_id_for("a@b.com")
    cache.create_account(uid, "a@b.com")
    issued = generate_key(uid)
    cache.save_api_key(issued.key_hash, uid, issued.prefix)

    assert cache.resolve_api_key(hash_key(issued.plaintext)) == uid
    assert cache.resolve_api_key(hash_key("fck_wrong")) is None


def test_revoked_key_stops_working(cache: Cache):
    uid = user_id_for("a@b.com")
    issued = generate_key(uid)
    cache.save_api_key(issued.key_hash, uid, issued.prefix)

    assert cache.revoke_api_key(issued.key_hash) is True
    assert cache.resolve_api_key(issued.key_hash) is None
    assert cache.revoke_api_key(issued.key_hash) is False, "이미 폐기된 키는 False"


def test_plaintext_key_is_never_stored(cache: Cache):
    uid = user_id_for("a@b.com")
    issued = generate_key(uid)
    cache.save_api_key(issued.key_hash, uid, issued.prefix)

    stored = cache.conn.execute("SELECT * FROM api_keys").fetchall()
    flat = " ".join(str(field) for row in stored for field in row)
    assert issued.plaintext not in flat, "DB가 새어나가도 남의 키를 쓸 수 없어야 한다"


def test_listing_keys_shows_prefix_not_secret(cache: Cache):
    uid = user_id_for("a@b.com")
    issued = generate_key(uid)
    cache.save_api_key(issued.key_hash, uid, issued.prefix)

    keys = cache.list_api_keys(uid)
    assert len(keys) == 1
    assert keys[0][1] == issued.prefix
    assert issued.plaintext not in " ".join(str(f) for f in keys[0])


def test_last_used_is_recorded(cache: Cache):
    uid = user_id_for("a@b.com")
    issued = generate_key(uid)
    cache.save_api_key(issued.key_hash, uid, issued.prefix)
    assert cache.list_api_keys(uid)[0][3] is None  # revoked_at

    cache.resolve_api_key(issued.key_hash)
    row = cache.conn.execute(
        "SELECT last_used_at FROM api_keys WHERE key_hash = ?", [issued.key_hash]
    ).fetchone()
    assert row[0] is not None


def test_keys_are_scoped_per_user(cache: Cache):
    a, b = user_id_for("a@b.com"), user_id_for("c@d.com")
    ka, kb = generate_key(a), generate_key(b)
    cache.save_api_key(ka.key_hash, a, ka.prefix)
    cache.save_api_key(kb.key_hash, b, kb.prefix)

    assert cache.resolve_api_key(ka.key_hash) == a
    assert cache.resolve_api_key(kb.key_hash) == b
    assert len(cache.list_api_keys(a)) == 1
