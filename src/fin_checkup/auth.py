"""계정과 API 키.

비밀번호는 다루지 않는다. 이메일 + API 키만으로 충분하고, 비밀번호를 받는 순간
저장·재설정·유출 대응까지 떠안게 된다. 나중에 소셜 로그인을 붙이더라도 이 계층은
"식별된 사용자 → user_id" 하나만 책임진다.

**API 키는 원문을 저장하지 않는다.** 해시만 보관하고, 발급 순간에 한 번만 보여준다.
DB가 새어나가도 남의 키로 우리 서비스를 쓸 수 없어야 한다.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime

#: 키 앞에 붙는 식별자. 로그나 깃 커밋에서 눈에 띄게 하려는 것이다.
KEY_PREFIX = "fck_"
#: 랜덤 부분의 바이트 수. 32바이트면 추측이 불가능하다.
KEY_ENTROPY_BYTES = 32

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvalidEmail(ValueError):
    pass


@dataclass(frozen=True)
class Account:
    user_id: str
    email: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class IssuedKey:
    """발급 결과. plaintext는 이 순간에만 존재한다."""

    user_id: str
    plaintext: str
    key_hash: str
    prefix: str

    @property
    def masked(self) -> str:
        """로그나 화면에 보여줄 형태."""
        return f"{self.prefix}…{self.plaintext[-4:]}"


def normalize_email(email: str) -> str:
    cleaned = email.strip().lower()
    if not EMAIL_RE.match(cleaned):
        raise InvalidEmail(f"이메일 형식이 아닙니다: {email!r}")
    return cleaned


def user_id_for(email: str) -> str:
    """이메일에서 안정적인 user_id를 만든다.

    이메일을 그대로 식별자로 쓰면 로그·URL 곳곳에 개인정보가 남는다.
    해시를 쓰면 같은 사람은 항상 같은 id를 받으면서 그 문제가 사라진다.
    """
    return hashlib.sha256(normalize_email(email).encode()).hexdigest()[:32]


def generate_key(user_id: str) -> IssuedKey:
    """새 API 키를 만든다. 원문은 반환값에만 있고 저장되지 않는다."""
    plaintext = KEY_PREFIX + secrets.token_urlsafe(KEY_ENTROPY_BYTES)
    return IssuedKey(
        user_id=user_id,
        plaintext=plaintext,
        key_hash=hash_key(plaintext),
        prefix=plaintext[: len(KEY_PREFIX) + 6],
    )


def hash_key(plaintext: str) -> str:
    """키를 해시한다.

    솔트를 쓰지 않는다 — 키 자체가 256비트 랜덤이라 무차별 대입이 불가능하고,
    솔트가 있으면 조회할 때 전수 비교를 해야 해서 로그인마다 테이블을 훑게 된다.
    (비밀번호였다면 반대로 솔트가 반드시 필요하다. 사람이 고른 값은 엔트로피가 낮다.)
    """
    return hashlib.sha256(plaintext.encode()).hexdigest()


def verify_key(plaintext: str, expected_hash: str) -> bool:
    """타이밍 공격에 안전하게 비교한다."""
    return hmac.compare_digest(hash_key(plaintext), expected_hash)


def looks_like_key(value: str) -> bool:
    return value.startswith(KEY_PREFIX) and len(value) > len(KEY_PREFIX) + 20
