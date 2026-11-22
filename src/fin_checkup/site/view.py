"""회사 페이지에 넘길 값 — 문장과 숫자를 미리 만들어 템플릿이 판정하지 않게 한다.

숫자는 `fin_checkup.format`, 경계값 문장·순서·급변은 `fin_checkup.presentation` 을 그대로 쓴다.
Streamlit 화면과 같은 함수라 두 화면의 숫자·문장이 어긋나지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from fin_checkup.format import format_metric
from fin_checkup.metrics.engine import Metric
from fin_checkup.metrics.peers import SelfDelta
from fin_checkup.metrics.redflags import RedFlag
from fin_checkup.metrics.signals import Signal
from fin_checkup.models import FsDiv
from fin_checkup.presentation import (
    band_text,
    metrics_by_category,
    recent_changes,
    sector_notice,
    summary_chips,
)
from fin_checkup.service import Checkup


@dataclass(frozen=True)
class SignalView:
    """신호 하나. 색만으로 말하지 않는다 — 기호와 글자를 늘 함께 낸다."""

    key: str
    emoji: str
    label: str

    @classmethod
    def of(cls, signal: Signal) -> SignalView:
        return cls(key=signal.value, emoji=signal.emoji, label=signal.label)


@dataclass(frozen=True)
class Chip:
    signal: SignalView
    count: int


@dataclass(frozen=True)
class MetricRow:
    key: str
    label: str
    description: str
    signal: SignalView
    #: 표시 값. 값이 없으면 `format.MISSING`.
    value_text: str
    formula: str
    band_text: str
    #: 엔진의 비고. ⊘ 면 "대신 볼 것", ⚫ 면 무엇이 없었는지.
    note: str
    #: 작년의 자기 자신과 견준 한 줄(JSON `delta.summary` 와 같은 문장). 견줄 값이 없으면 빈 문자열.
    delta_text: str
    #: 카드에서는 같은 내용을 짧게 나눠 쓴다 — "27.93% → 29.94%" / "2.01%p 높아짐" / 평가(없으면 빈 문자열).
    #: 같으면 delta_values 만 "작년과 같음 (…)".
    delta_values: str
    delta_change: str
    delta_direction: str
    missing: bool
    not_applicable: bool


@dataclass(frozen=True)
class MetricGroup:
    category: str
    rows: list[MetricRow]


@dataclass(frozen=True)
class ChangeRow:
    label: str
    summary: str
    prior: SignalView
    current: SignalView


@dataclass(frozen=True)
class ChangesView:
    prior_year: int
    latest_year: int
    rows: list[ChangeRow]


@dataclass(frozen=True)
class CompanyView:
    name: str
    stock_code: str
    corp_code: str
    market: str
    year: int
    report_label: str
    statement_label: str
    sector_label: str
    sector_notice: str
    currency: str
    years: list[int]
    chips: list[Chip]
    groups: list[MetricGroup]
    red_flags: list[RedFlag]
    #: 직전 사업연도가 캐시에 없으면 None.
    changes: ChangesView | None


def _row(metric: Metric, currency: str, delta: SelfDelta | None) -> MetricRow:
    return MetricRow(
        key=metric.key,
        label=metric.label,
        description=metric.description,
        signal=SignalView.of(metric.signal),
        value_text=format_metric(metric, currency),
        formula=metric.formula,
        band_text=band_text(metric),
        note=metric.note,
        delta_text=delta.summary if delta is not None else "",
        delta_values=""
        if delta is None
        else delta.summary
        if delta.unchanged
        else f"{delta.before_text} → {delta.after_text}",
        delta_change="" if delta is None or delta.unchanged else delta.change_text,
        delta_direction="" if delta is None else delta.direction_text,
        missing=metric.signal is Signal.UNKNOWN,
        not_applicable=metric.signal is Signal.NOT_APPLICABLE,
    )


def company_view(data: Checkup) -> CompanyView:
    latest = data.history[-1]
    result = data.result
    currency = result.currency

    window = recent_changes(data)
    changes = (
        None
        if window is None
        else ChangesView(
            prior_year=window.prior_year,
            latest_year=window.latest_year,
            rows=[
                ChangeRow(
                    label=c.label,
                    summary=c.summary,
                    prior=SignalView.of(c.prior_signal),
                    current=SignalView.of(c.current_signal),
                )
                for c in window.changes
            ],
        )
    )

    return CompanyView(
        name=data.corp.corp_name,
        stock_code=data.corp.stock_code,
        corp_code=data.corp.corp_code,
        market=data.company.market.value if data.company and data.company.market else "",
        year=latest.bsns_year,
        report_label=latest.reprt_code.label,
        statement_label="연결재무제표" if latest.fs_div is FsDiv.CFS else "개별재무제표",
        sector_label=result.sector.label,
        sector_notice=sector_notice(data),
        currency=currency,
        years=[f.bsns_year for f in data.history],
        chips=[Chip(SignalView.of(s), n) for s, n in summary_chips(result)],
        groups=[
            MetricGroup(
                category=category.value,
                rows=[_row(m, currency, data.deltas.get(m.key)) for m in metrics],
            )
            for category, metrics in metrics_by_category(result)
        ],
        red_flags=list(data.red_flags),
        changes=changes,
    )
