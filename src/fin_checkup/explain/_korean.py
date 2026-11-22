"""한국어 조사 고르기 — 받침 유무로 은/는, 이라/라, 이면/면.

해설 문장이 지표 이름·숫자를 끼워 넣어 만들어지므로 조사를 코드가 골라야 한다.
'영업이익률는' 같은 문장은 신뢰를 깎는다.
"""

from __future__ import annotations

import re

#: 숫자를 한국어로 읽었을 때 받침이 있는가 (영·일·삼·육·칠·팔 → 있음).
_DIGIT_HAS_FINAL = {
    "0": True,
    "1": True,
    "2": False,
    "3": True,
    "4": False,
    "5": False,
    "6": True,
    "7": True,
    "8": True,
    "9": False,
}
#: 영문자를 한국어로 읽었을 때 받침이 있는가 (엘·엠·엔·알).
_LATIN_HAS_FINAL = set("LMNRlmnr")
#: 단위 기호를 읽었을 때 (퍼센트·달러 등).
_SYMBOL_HAS_FINAL = {"%": False, "$": False}

_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")


def has_final_consonant(text: str) -> bool:
    """마지막 글자를 읽었을 때 받침이 있는가. 판단할 수 없으면 False."""
    word = _TRAILING_PAREN.sub("", text).rstrip()
    for ch in reversed(word):
        if "가" <= ch <= "힣":
            return (ord(ch) - 0xAC00) % 28 != 0
        if ch in _DIGIT_HAS_FINAL:
            return _DIGIT_HAS_FINAL[ch]
        if ch.isascii() and ch.isalpha():
            return ch in _LATIN_HAS_FINAL
        if ch in _SYMBOL_HAS_FINAL:
            return _SYMBOL_HAS_FINAL[ch]
    return False


def topic(text: str) -> str:
    """'영업이익률' → '영업이익률은', 'ROE (자기자본이익률)' → 'ROE (자기자본이익률)는'."""
    return text + ("은" if has_final_consonant(text) else "는")


def because(text: str) -> str:
    """'7% 이상' → '7% 이상이라', '100% 이하' → '100% 이하라'."""
    return text + ("이라" if has_final_consonant(text) else "라")


def if_(text: str) -> str:
    """'7% 이상' → '7% 이상이면', '100% 이하' → '100% 이하면'."""
    return text + ("이면" if has_final_consonant(text) else "면")


def is_(text: str) -> str:
    """'정상' → '정상이에요', '주의' → '주의예요'."""
    return text + ("이에요" if has_final_consonant(text) else "예요")


def ro(text: str) -> str:
    """'금융업' → '금융업으로', '은행·증권·금융지주' → '은행·증권·금융지주로'. ㄹ 받침은 '로'."""
    word = _TRAILING_PAREN.sub("", text).rstrip()
    last = word[-1:] if word else ""
    rieul = last in "178" or ("가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 == 8)  # 일·칠·팔, ㄹ 받침
    return text + ("으로" if has_final_consonant(text) and not rieul else "로")
