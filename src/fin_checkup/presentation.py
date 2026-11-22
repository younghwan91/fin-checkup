"""화면이 공통으로 쓰는 표시 규칙 — Streamlit·정적 사이트가 같은 문장을 내게 한다.

판정은 `metrics/` 가 한다. 여기는 그 판정을 사람이 읽는 문장·순서로 옮기는 자리다.
같은 규칙을 화면마다 따로 쓰면 한쪽만 고쳐져 두 화면이 서로 다른 말을 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from fin_checkup.metrics.changes import Change, detect_changes
from fin_checkup.metrics.engine import MONEY, Category, CheckupResult, Metric, checkup
from fin_checkup.metrics.sector import Sector
from fin_checkup.metrics.signals import SEVERITY, Signal
from fin_checkup.service import Checkup

#: 요약 줄에 항상 보일 신호. 나머지(측정값·해당없음·데이터없음)는 0이면 감춘다.
PRIMARY_SIGNALS = (Signal.RED, Signal.YELLOW, Signal.GREEN)
SECONDARY_SIGNALS = (Signal.NEUTRAL, Signal.NOT_APPLICABLE, Signal.UNKNOWN)


def band_text(metric: Metric) -> str:
    """판정 경계값을 한 줄로. 왜 이 색인지 감추지 않는다."""
    if metric.signal is Signal.NOT_APPLICABLE and metric.band is None:
        return "이 업종에는 판정 기준을 적용하지 않음 — 아래 '대신 볼 것' 참고."
    if metric.band is None:
        return "절대 기준 없음 — 추이와 업종 비교로 판단할 항목."
    band = metric.band
    unit = "" if metric.unit == MONEY else metric.unit
    # 정상과 주의 경계가 같으면(영업활동현금흐름·FCF 의 0) 🟡 구간이 없다. "주의 0 이상" 을 적으면
    # 주의 구간이 있는 것처럼 읽힌다 — 에이전트 답에도 그대로 옮겨졌다(2026-11-22 직접 써 보고 발견).
    if band.higher_is_better:
        if band.good == band.warn:
            return f"정상 {band.good}{unit} 이상 · 그 미만은 위험 (주의 구간 없음)"
        return f"정상 {band.good}{unit} 이상 · 주의 {band.warn}{unit} 이상 · 그 미만은 위험"
    if band.good == band.warn:
        return f"정상 {band.good}{unit} 이하 · 그 초과는 위험 (주의 구간 없음)"
    return f"정상 {band.good}{unit} 이하 · 주의 {band.warn}{unit} 이하 · 그 초과는 위험"


def summary_chips(result: CheckupResult) -> list[tuple[Signal, int]]:
    """신호 개수 요약. 정상/주의/위험은 늘 보이고, 나머지는 실제로 있을 때만."""
    counts = result.counts
    chips = [(s, counts[s]) for s in PRIMARY_SIGNALS]
    chips += [(s, counts[s]) for s in SECONDARY_SIGNALS if counts[s]]
    return chips


def metrics_by_category(result: CheckupResult) -> list[tuple[Category, list[Metric]]]:
    """지표 묶음별로, 묶음 안에서는 나쁜 것부터. 사용자가 먼저 봐야 할 건 위험한 쪽이다."""
    groups: list[tuple[Category, list[Metric]]] = []
    for category in Category:
        metrics = result.by_category(category)
        if metrics:
            groups.append((category, sorted(metrics, key=lambda m: -SEVERITY[m.signal])))
    return groups


@dataclass(frozen=True)
class RecentChanges:
    """직전 사업연도 대비 급격히 나빠진 지표."""

    prior_year: int
    latest_year: int
    changes: list[Change]


def recent_changes(data: Checkup) -> RecentChanges | None:
    """직전 사업연도와 견준 급변. 두 해가 없으면 None.

    업종을 넘긴다 — 빼면 은행의 부채비율이 일반 기업 기준으로 판정돼 ⊘ 가 🔴 로 둔갑한다.
    전전년은 있으면 쓴다(작년 성장성 지표용). 없으면 성장성만 비교에서 빠진다.
    """
    by_year = {f.bsns_year: f for f in data.history}
    latest = data.history[-1] if data.history else None
    prev = by_year.get(latest.bsns_year - 1) if latest else None
    if latest is None or prev is None:
        return None
    prev2 = by_year.get(prev.bsns_year - 1)
    sector = data.result.sector
    changes = detect_changes(data.result, checkup(prev, prev2, sector=sector))
    return RecentChanges(prior_year=prev.bsns_year, latest_year=latest.bsns_year, changes=changes)


def sector_notice(data: Checkup) -> str:
    """일반 업종이 아니면, 일부 지표를 ⊘ 로 두는 이유를 한 줄로."""
    sector = data.result.sector
    if sector is Sector.GENERAL:
        return ""
    return (
        f"이 회사는 {sector.label}으로 분류돼, 업종에 맞지 않는 지표는 ⊘로 표시하고 판정하지 않습니다. "
        "각 지표의 '대신 볼 것'을 참고하세요."
    )
