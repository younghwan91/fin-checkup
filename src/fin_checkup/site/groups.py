"""업종 묶음과 분포 — 업종 페이지(`/s/`)·같은 업종 회사·시장 전체 분포(`/market/`)가 같이 쓴다.

**모집단은 이 빌드에 페이지가 생기는 회사(`site.companies`)다.** 업종 페이지의 "N개사" 가 그 페이지의
회사 목록 길이와 같아야 사용자가 셀 수 있다. 전 상장사 빌드에서는 회사 페이지의 대조군
(`service.peer_comparison`, 캐시의 같은 업종 회사 — 자기 자신 제외)과 같은 모집단이다.

묶음은 `metrics.sector.peer_group_for` (KSIC 앞 두 자리) 그대로다. 분포에서 빼는 것:
- 값이 없는 회사(⚫) — 0 으로 세지 않는다.
- ⊘ — 계산은 되지만 그 업종엔 뜻이 없는 값이다. 은행의 부채비율 1,000% 를 섞으면 분포가 거짓말을 한다.
- 원화가 아닌 금액 — 자릿수가 다르다 (`service._comparable` 와 같은 규칙).

표본이 `MIN_PEERS` 보다 적으면 중앙값·사분위를 내지 않는다 (회사 페이지 대조군과 같은 하한).
목록은 언제나 **이름순**이다 — 지표 값으로 줄 세우지 않는다.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from fin_checkup.format import format_money
from fin_checkup.metrics.engine import METRIC_DEFS, MONEY, MetricDef
from fin_checkup.metrics.peers import MIN_PEERS
from fin_checkup.metrics.sector import peer_group_for
from fin_checkup.metrics.signals import Signal
from fin_checkup.site.context import CompanyEntry, Site

#: 화면에 신호를 늘어놓는 순서 — 판정 색 먼저, 그다음 판정하지 않은 것.
SIGNAL_ORDER = (
    Signal.GREEN,
    Signal.YELLOW,
    Signal.RED,
    Signal.NEUTRAL,
    Signal.NOT_APPLICABLE,
    Signal.UNKNOWN,
)


def peer_group_of(entry: CompanyEntry) -> str:
    """회사의 업종 묶음 코드. 업종코드가 없으면 빈 문자열(업종 페이지 없음)."""
    company = entry.checkup.company
    return peer_group_for(company.industry_code if company else None)


def sector_path(code: str) -> str:
    return f"s/{code}/"


def by_name(entries: Iterable[CompanyEntry]) -> list[CompanyEntry]:
    """이름순(같은 이름이면 종목코드순). 지표 값으로 정렬하는 목록은 만들지 않는다."""
    return sorted(entries, key=lambda e: (e.name, e.stock_code))


_GROUPS_MEMO: tuple[Site, dict[str, list[CompanyEntry]]] | None = None


def peer_groups(site: Site) -> dict[str, list[CompanyEntry]]:
    """묶음 코드 → 소속 회사(이름순), 코드순. 업종코드가 없는 회사는 빠진다.

    회사 페이지마다 부르므로 같은 빌드(같은 Site 객체)에서는 한 번만 계산한다.
    """
    global _GROUPS_MEMO
    if _GROUPS_MEMO is not None and _GROUPS_MEMO[0] is site:
        return _GROUPS_MEMO[1]
    grouped: dict[str, list[CompanyEntry]] = {}
    for entry in site.companies:
        code = peer_group_of(entry)
        if code:
            grouped.setdefault(code, []).append(entry)
    result = {code: by_name(grouped[code]) for code in sorted(grouped)}
    _GROUPS_MEMO = (site, result)
    return result


@dataclass(frozen=True)
class SignalShare:
    signal: Signal
    count: int
    #: 전체(회사 수) 대비 %. 소수 첫째 자리.
    percent: float


@dataclass(frozen=True)
class Distribution:
    """지표 하나의 분포. 표본이 모자라면 median·q1·q3 가 None."""

    spec: MetricDef
    #: 분포에 든 회사 수.
    count: int
    #: 모집단 회사 수. count + 아래 네 가지 = total.
    total: int
    #: 값이 없어(⚫) 뺀 회사.
    missing: int
    #: 값은 없지만 신호는 있는 회사 — 자본잠식처럼 계산할 수 없는 상태 자체로 판정한 경우.
    judged_without_value: int
    #: 업종에 맞지 않아(⊘) 뺀 회사.
    not_applicable: int
    #: 원화가 아닌 금액이라 뺀 회사.
    other_currency: int
    median: float | None
    q1: float | None
    q3: float | None
    signals: tuple[SignalShare, ...]
    p10: float | None = None
    p90: float | None = None

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def enough(self) -> bool:
        return self.median is not None

    @property
    def excluded_text(self) -> str:
        """분포에서 뺀 회사를 이유별로. 뺀 회사가 없으면 빈 문자열."""
        parts = []
        if self.missing:
            parts.append(f"값이 없는 ⚫ {self.missing:,}곳")
        if self.judged_without_value:
            parts.append(f"값 없이 판정된 {self.judged_without_value:,}곳(자본잠식 등)")
        if self.not_applicable:
            parts.append(f"업종에 맞지 않는 ⊘ {self.not_applicable:,}곳")
        if self.other_currency:
            parts.append(f"원화가 아닌 금액 {self.other_currency:,}곳")
        return ", ".join(parts)

    def text(self, value: float | None) -> str:
        """분포 값 표시. 금액은 원화 축약(`format_money`), 비율은 소수 둘째 자리 + 단위."""
        if value is None:
            return "—"
        if self.spec.unit == MONEY:
            return format_money(value, "KRW")
        return f"{value:,.2f}{self.spec.unit}"


def distribution(spec: MetricDef, entries: list[CompanyEntry]) -> Distribution:
    values: list[float] = []
    counts: Counter[Signal] = Counter()
    missing = judged_without_value = not_applicable = other_currency = 0
    for entry in entries:
        metric = entry.checkup.result.by_key(spec.key)
        if metric is None:
            counts[Signal.UNKNOWN] += 1
            missing += 1
            continue
        counts[metric.signal] += 1
        if metric.signal is Signal.NOT_APPLICABLE:
            not_applicable += 1
        elif metric.value is None:
            if metric.signal is Signal.UNKNOWN:
                missing += 1
            else:
                judged_without_value += 1
        elif metric.unit == MONEY and entry.checkup.result.currency != "KRW":
            other_currency += 1
        else:
            values.append(metric.value)
    total = len(entries)
    shares = tuple(
        SignalShare(s, counts[s], round(counts[s] / total * 100, 1) if total else 0.0) for s in SIGNAL_ORDER
    )
    median = q1 = q3 = p10 = p90 = None
    if len(values) >= MIN_PEERS:
        median = statistics.median(values)
        # 'inclusive' 는 최솟값·최댓값을 0%·100% 로 두는 흔한 정의(스프레드시트 QUARTILE 과 같다).
        q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
        deciles = statistics.quantiles(values, n=10, method="inclusive")
        p10, p90 = deciles[0], deciles[8]
    return Distribution(
        spec=spec,
        count=len(values),
        total=total,
        missing=missing,
        judged_without_value=judged_without_value,
        not_applicable=not_applicable,
        other_currency=other_currency,
        median=median,
        q1=q1,
        q3=q3,
        signals=shares,
        p10=p10,
        p90=p90,
    )


def distributions(entries: list[CompanyEntry]) -> list[Distribution]:
    """지표 17개, 엔진 정의 순서."""
    return [distribution(spec, entries) for spec in METRIC_DEFS]
