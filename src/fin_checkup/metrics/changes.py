"""급변 감지 — 전기 대비 급격히 나빠진 지표를 골라낸다 (계획서 3절 4번 화면).

"나빠졌다"는 두 가지로 본다.
1. 신호등 등급이 내려갔다 (🟢→🟡, 🟡→🔴).
2. 등급은 같아도 값이 나쁜 방향으로 크게 움직였다.
"""

from __future__ import annotations

from dataclasses import dataclass

from fin_checkup.metrics.engine import CheckupResult, Metric
from fin_checkup.metrics.signals import SEVERITY, Signal

#: 등급이 그대로여도 이만큼 나빠졌으면 짚어준다 (상대 변화율, %).
DEFAULT_MOVE_THRESHOLD = 30.0


@dataclass(frozen=True)
class Change:
    key: str
    label: str
    prior_value: float | None
    current_value: float | None
    prior_signal: Signal
    current_signal: Signal
    unit: str
    #: 등급이 내려갔는지.
    downgraded: bool
    #: 나쁜 방향 상대 변화율(%). 값이 없으면 None.
    move_pct: float | None

    @property
    def summary(self) -> str:
        if self.downgraded:
            return f"{self.prior_signal.label} → {self.current_signal.label}로 내려갔다."
        return f"전기 대비 {abs(self.move_pct or 0):.0f}% 나쁜 방향으로 움직였다."


def _higher_is_better(metric: Metric) -> bool:
    return metric.band.higher_is_better if metric.band is not None else True


def detect_changes(
    current: CheckupResult,
    prior: CheckupResult,
    move_threshold: float = DEFAULT_MOVE_THRESHOLD,
) -> list[Change]:
    """나빠진 지표만 심한 순서로 반환."""
    prior_by_key = {m.key: m for m in prior.metrics}
    changes: list[Change] = []

    for metric in current.metrics:
        before = prior_by_key.get(metric.key)
        if before is None:
            continue

        downgraded = SEVERITY[metric.signal] > SEVERITY[before.signal] and {
            metric.signal,
            before.signal,
        } <= {Signal.GREEN, Signal.YELLOW, Signal.RED}

        move = _bad_move_pct(before.value, metric.value, _higher_is_better(metric))
        if not downgraded and (move is None or move < move_threshold):
            continue

        changes.append(
            Change(
                key=metric.key,
                label=metric.label,
                prior_value=before.value,
                current_value=metric.value,
                prior_signal=before.signal,
                current_signal=metric.signal,
                unit=metric.unit,
                downgraded=downgraded,
                move_pct=move,
            )
        )

    changes.sort(key=lambda c: (c.downgraded, c.move_pct or 0), reverse=True)
    return changes


def _bad_move_pct(
    before: float | None, after: float | None, higher_is_better: bool
) -> float | None:
    """나쁜 방향으로 움직인 정도(%). 좋아졌으면 음수."""
    if before is None or after is None or before == 0:
        return None
    delta = (after - before) / abs(before) * 100
    return -delta if higher_is_better else delta
