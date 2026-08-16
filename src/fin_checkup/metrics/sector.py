"""업종 구분.

같은 공식을 모든 업종에 들이대면 거짓말이 된다. 은행은 예금이 부채라 부채비율이
1,000%를 넘는 게 정상이고, 재고자산이나 유동자산 구분 자체가 없다. 실제로 KB금융을
일반 기업 기준으로 재보니 🔴가 4개 나왔다 — 국내에서 가장 건전한 금융지주 중 하나인데도.

그래서 업종을 먼저 가르고, 적용할 수 없는 지표는 억지로 판정하지 않는다.
"""

from __future__ import annotations

from enum import Enum

#: DART induty_code는 통계청 표준산업분류(KSIC)다. 5자리(64992)와 3자리(264)가 섞여 있어
#: 앞 두 자리로 가른다.
FINANCIAL_PREFIXES = frozenset({"64", "65", "66"})
REAL_ESTATE_PREFIXES = frozenset({"68"})


class Sector(str, Enum):
    GENERAL = "general"
    #: 은행·보험·금융지주·증권 (KSIC 64~66)
    FINANCIAL = "financial"
    #: 부동산업 (KSIC 68) — 재고자산이 분양용 토지·건물이라 회전율 해석이 다르다.
    REAL_ESTATE = "real_estate"

    @property
    def label(self) -> str:
        return {
            "general": "일반",
            "financial": "금융업",
            "real_estate": "부동산업",
        }[self.value]


#: 업종 비교를 묶는 단위. KSIC 앞 두 자리(중분류)를 쓴다.
#:
#: 5자리 원본 코드로 묶으면 상장사 912곳이 업종 318개로 쪼개져 절반이 표본 5개사를
#: 못 채운다. 두 자리로 묶으면 57개 업종에 94.7%가 비교 가능해진다. 비교가 안 뜨는
#: 것보다 조금 굵게 묶어서라도 견줄 대상을 주는 편이 낫다.
PEER_GROUP_DIGITS = 2


def peer_group_for(industry_code: str | None) -> str:
    """업종 비교용 그룹 키. 코드가 없거나 너무 짧으면 빈 문자열."""
    code = (industry_code or "").strip()
    return code[:PEER_GROUP_DIGITS] if len(code) >= PEER_GROUP_DIGITS else ""


def sector_for(industry_code: str | None) -> Sector:
    """DART 업종코드로 업종을 가른다. 코드가 없으면 일반으로 본다."""
    code = (industry_code or "").strip()
    if len(code) < 2:
        return Sector.GENERAL
    prefix = code[:2]
    if prefix in FINANCIAL_PREFIXES:
        return Sector.FINANCIAL
    if prefix in REAL_ESTATE_PREFIXES:
        return Sector.REAL_ESTATE
    return Sector.GENERAL
