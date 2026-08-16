from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Signal(str, Enum):
    """건강검진 결과지의 신호등."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    #: 측정은 됐지만 절대 기준이 없는 지표(업종 편차가 큰 회전율 등).
    NEUTRAL = "neutral"
    #: 이 업종에는 적용할 수 없는 지표. 은행의 유동비율처럼 계산은 되지만 뜻이 없다.
    NOT_APPLICABLE = "not_applicable"
    #: 공시에서 필요한 계정을 찾지 못했거나 계산이 불가능한 경우.
    UNKNOWN = "unknown"

    @property
    def emoji(self) -> str:
        return {
            "green": "🟢",
            "yellow": "🟡",
            "red": "🔴",
            "neutral": "⚪",
            "not_applicable": "⊘",
            "unknown": "⚫",
        }[self.value]

    @property
    def label(self) -> str:
        return {
            "green": "정상",
            "yellow": "주의",
            "red": "위험",
            "neutral": "측정값",
            "not_applicable": "해당 없음",
            "unknown": "데이터 없음",
        }[self.value]


#: 나쁜 쪽부터. worst 판정에 쓴다.
SEVERITY: dict[Signal, int] = {
    Signal.RED: 3,
    Signal.YELLOW: 2,
    Signal.GREEN: 1,
    Signal.NEUTRAL: 0,
    Signal.NOT_APPLICABLE: 0,
    Signal.UNKNOWN: 0,
}


@dataclass(frozen=True)
class Band:
    """신호등 경계값.

    higher_is_better=True  → value >= good 이면 🟢, >= warn 이면 🟡, 그 아래 🔴
    higher_is_better=False → value <= good 이면 🟢, <= warn 이면 🟡, 그 위 🔴
    """

    good: float
    warn: float
    higher_is_better: bool = True

    def evaluate(self, value: float) -> Signal:
        if self.higher_is_better:
            if value >= self.good:
                return Signal.GREEN
            if value >= self.warn:
                return Signal.YELLOW
            return Signal.RED
        if value <= self.good:
            return Signal.GREEN
        if value <= self.warn:
            return Signal.YELLOW
        return Signal.RED
