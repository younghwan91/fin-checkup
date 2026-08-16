"""공시 제목(report_nm)으로 위험 공시를 분류한다.

DART 공시 제목은 '주요사항보고서(유상증자결정)'처럼 보고서 종류가 이름에 그대로
드러난다. 그래서 본문을 내려받지 않고도 제목만으로 1차 분류가 된다 — 호출 횟수를
아끼면서 놓치는 걸 줄이는 방법이다.

정정공시('[기재정정]')는 같은 종류로 분류하되 정정 여부를 따로 표시한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from fin_checkup.models import Disclosure


class Severity(str, Enum):
    """알림 강도. 사용자가 알림을 걸러 받을 수 있게 하기 위한 것."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"

    @property
    def rank(self) -> int:
        return {"medium": 1, "high": 2, "critical": 3}[self.value]

    @property
    def emoji(self) -> str:
        return {"critical": "🚨", "high": "🔴", "medium": "🟡"}[self.value]


class RiskKind(str, Enum):
    DISTRESS = "distress"
    LISTING_STATUS = "listing_status"
    AUDIT_OPINION = "audit_opinion"
    CAPITAL_REDUCTION = "capital_reduction"
    CONVERTIBLE_BOND = "convertible_bond"
    CAPITAL_RAISE = "capital_raise"
    MAJOR_SHAREHOLDER = "major_shareholder"
    INSIDER_TRADING = "insider_trading"

    @property
    def label(self) -> str:
        return {
            "distress": "부도·영업정지·회생·파산",
            "listing_status": "관리종목·상장폐지 관련",
            "audit_opinion": "감사보고서",
            "capital_reduction": "감자 결정",
            "convertible_bond": "CB·BW·EB 발행 결정",
            "capital_raise": "유상증자 결정",
            "major_shareholder": "최대주주·대량보유 변동",
            "insider_trading": "임원·주요주주 소유 변동",
        }[self.value]

    @property
    def why(self) -> str:
        """왜 알림 대상인지에 대한 사실 진술. 행동 지시가 아니다."""
        return {
            "distress": "기업의 존속에 직접 영향을 주는 사건이 신고됐다.",
            "listing_status": "상장 지위와 관련된 공시다.",
            "audit_opinion": (
                "감사의견이 적정이 아닌 경우 관리종목·상장폐지 사유가 될 수 있다. "
                "원문에서 의견 종류를 확인할 것."
            ),
            "capital_reduction": (
                "감자는 자본금을 줄이는 결정이다. 무상감자인지 유상감자인지는 원문에서 확인할 것."
            ),
            "convertible_bond": "주식으로 전환될 수 있는 사채다. 전환 시 기존 주주 지분이 희석된다.",
            "capital_raise": "새로 주식을 발행해 자금을 조달하는 결정이다. 기존 주주 지분이 희석된다.",
            "major_shareholder": "지배구조나 대주주 지분율에 변동이 생겼다.",
            "insider_trading": "임원 또는 주요주주의 보유 주식 수가 변동됐다.",
        }[self.value]

    @property
    def severity(self) -> Severity:
        return {
            "distress": Severity.CRITICAL,
            "listing_status": Severity.CRITICAL,
            "audit_opinion": Severity.HIGH,
            "capital_reduction": Severity.HIGH,
            "convertible_bond": Severity.HIGH,
            "capital_raise": Severity.HIGH,
            "major_shareholder": Severity.MEDIUM,
            "insider_trading": Severity.MEDIUM,
        }[self.value]


@dataclass(frozen=True)
class RiskDisclosure:
    disclosure: Disclosure
    kind: RiskKind
    #: 제목에서 실제로 걸린 표현. 왜 이렇게 분류됐는지 보여주기 위해 남긴다.
    matched: str
    is_correction: bool = False

    @property
    def severity(self) -> Severity:
        return self.kind.severity

    @property
    def why(self) -> str:
        return self.kind.why


#: 위에 있을수록 우선. 구체적인 표현을 앞에 둬서 일반어에 먹히지 않게 한다.
PATTERNS: tuple[tuple[RiskKind, str], ...] = (
    (RiskKind.DISTRESS, r"부도발생"),
    (RiskKind.DISTRESS, r"회생절차"),
    (RiskKind.DISTRESS, r"파산신청"),
    (RiskKind.DISTRESS, r"영업정지"),
    (RiskKind.DISTRESS, r"채권자?은행.{0,4}관리절차"),
    (RiskKind.LISTING_STATUS, r"상장폐지"),
    (RiskKind.LISTING_STATUS, r"관리종목"),
    (RiskKind.LISTING_STATUS, r"매매거래정지"),
    (RiskKind.LISTING_STATUS, r"투자주의환기종목"),
    (RiskKind.AUDIT_OPINION, r"감사보고서"),
    (RiskKind.AUDIT_OPINION, r"감사의견"),
    (RiskKind.CAPITAL_REDUCTION, r"감자결정"),
    (RiskKind.CAPITAL_REDUCTION, r"자본금감소"),
    (RiskKind.CONVERTIBLE_BOND, r"전환사채권?발행"),
    (RiskKind.CONVERTIBLE_BOND, r"신주인수권부사채권?발행"),
    (RiskKind.CONVERTIBLE_BOND, r"교환사채권?발행"),
    (RiskKind.CAPITAL_RAISE, r"유상증자"),
    (RiskKind.MAJOR_SHAREHOLDER, r"최대주주.{0,6}변경"),
    (RiskKind.MAJOR_SHAREHOLDER, r"대량보유상황보고"),
    (RiskKind.INSIDER_TRADING, r"특정증권등소유상황보고"),
)

_COMPILED: tuple[tuple[RiskKind, re.Pattern[str], str], ...] = tuple(
    (kind, re.compile(pattern), pattern) for kind, pattern in PATTERNS
)

_CORRECTION = re.compile(r"\[(기재정정|첨부정정|첨부추가|정정)\]")


def _squash(text: str) -> str:
    """공백을 없애 '유상증자 결정' 같은 표기 흔들림을 흡수한다."""
    return "".join(text.split())


def classify(disclosure: Disclosure) -> RiskDisclosure | None:
    """위험 공시면 분류 결과, 아니면 None."""
    title = _squash(disclosure.report_nm)
    is_correction = bool(_CORRECTION.search(title))

    for kind, pattern, source in _COMPILED:
        match = pattern.search(title)
        if match:
            return RiskDisclosure(
                disclosure=disclosure,
                kind=kind,
                matched=match.group(0) or source,
                is_correction=is_correction,
            )
    return None


def classify_all(disclosures: list[Disclosure]) -> list[RiskDisclosure]:
    """위험 공시만 골라 심각도 높은 순으로."""
    found = [r for r in (classify(d) for d in disclosures) if r is not None]
    found.sort(key=lambda r: (r.severity.rank, r.disclosure.rcept_dt), reverse=True)
    return found
