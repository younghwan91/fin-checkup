"""업종 평균 비교 (계획서 4절 "수치 + 업종평균 대비").

절대 임계값만으로는 업종 차이를 못 담는다. 조선사의 부채비율과 소프트웨어사의
부채비율을 같은 자로 재는 건 정확하지 않다. 그래서 같은 업종코드(induty_code)
기업들의 중앙값을 함께 보여준다.

여기서 하는 일은 "같은 업종 N개사 중 몇 번째"까지다. 그 등수로 매수·매도를
말하지 않는다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from enum import Enum

from fin_checkup.metrics.engine import MONEY, Metric

#: 이보다 표본이 적으면 중앙값을 내지 않는다. 3개사 평균은 평균이라 부르기 어렵다.
MIN_PEERS = 5


class PeerScope(str, Enum):
    """무엇과 견줬는지. 이걸 숨기면 사용자가 근거를 확인할 수 없다."""

    INDUSTRY = "industry"
    MARKET = "market"

    @property
    def label(self) -> str:
        return {"industry": "동종업계", "market": "전체 상장사"}[self.value]


@dataclass(frozen=True)
class PeerStat:
    """한 지표의 대조군 분포."""

    metric_key: str
    median: float
    sample_size: int
    scope: PeerScope = PeerScope.INDUSTRY
    #: 대상 기업의 값이 업종 내에서 차지하는 백분위(0~100). 값이 없으면 None.
    percentile: float | None = None
    higher_is_better: bool = True
    #: 표시 단위. 금액 지표를 그대로 찍으면 -846,681,792.00 같은 게 나온다.
    unit: str = ""
    currency: str = "KRW"

    @property
    def median_text(self) -> str:
        from fin_checkup.format import format_money

        if self.unit == MONEY:
            return format_money(self.median, self.currency)
        return f"{self.median:,.2f}{self.unit}"

    @property
    def rank_text(self) -> str | None:
        """업종 내 위치.

        '상위 0%'는 1등이라는 뜻인데 꼴찌처럼 읽힌다. 양 끝은 말로 쓴다.
        """
        if self.percentile is None:
            return None
        # 범위 이름은 summary 앞머리가 이미 말한다. 여기서 '업종'을 또 붙이면
        # 시장 전체와 견줬을 때 어긋난다.
        top = 100 - self.percentile
        if top < 0.5:
            return "최상위"
        if top > 99.5:
            return "최하위"
        return f"상위 {top:.0f}%"

    @property
    def summary(self) -> str:
        """무엇과, 몇 곳과 견줬고, 어디쯤인지를 한 줄로."""
        rank = self.rank_text
        head = f"{self.scope.label} {self.sample_size}개사"
        if rank:
            return f"{head} 중 {rank} · 중앙값 {self.median_text}"
        return f"{head} 중앙값 {self.median_text}"


@dataclass(frozen=True)
class SelfDelta:
    """작년의 자기 자신과 견준 결과.

    업종·시장 대조군이 "남들과 비교해 어디쯤"이라면 이건 "우리가 나아졌나"다.
    둘은 서로를 대신하지 못한다 — 업종 전체가 나빠진 해에는 상위 20%여도 작년보다
    나쁠 수 있다.
    """

    metric_key: str
    previous: float
    current: float
    higher_is_better: bool
    unit: str = ""
    currency: str = "KRW"

    @property
    def improved(self) -> bool:
        return (
            self.current > self.previous
            if self.higher_is_better
            else self.current < self.previous
        )

    @property
    def unchanged(self) -> bool:
        return self.current == self.previous

    @property
    def summary(self) -> str:
        from fin_checkup.format import format_money

        if self.unit == MONEY:
            before = format_money(self.previous, self.currency)
        else:
            before = f"{self.previous:,.2f}{self.unit}"
        if self.unchanged:
            return f"작년과 같음 ({before})"
        arrow = "↑" if self.current > self.previous else "↓"
        word = "개선" if self.improved else "악화"
        return f"작년 {before} {arrow} {word}"


def self_delta(
    metric: Metric, previous_value: float | None, currency: str = "KRW"
) -> SelfDelta | None:
    """지표 하나의 전년 대비. 어느 한쪽이라도 없으면 None."""
    if metric.value is None or previous_value is None:
        return None
    return SelfDelta(
        metric_key=metric.key,
        previous=previous_value,
        current=metric.value,
        higher_is_better=metric.band.higher_is_better if metric.band else True,
        unit=metric.unit,
        currency=currency,
    )


def peer_stats(
    metric: Metric,
    peer_values: list[float],
    min_peers: int = MIN_PEERS,
    currency: str = "KRW",
    scope: PeerScope = PeerScope.INDUSTRY,
) -> PeerStat | None:
    """같은 업종 기업들의 같은 지표 값 목록으로 분포를 낸다.

    peer_values에는 대상 기업 자신의 값이 포함돼 있어도 되고 없어도 된다.
    """
    values = [v for v in peer_values if v is not None]
    if len(values) < min_peers:
        return None

    higher_is_better = metric.band.higher_is_better if metric.band is not None else True
    median = statistics.median(values)

    percentile: float | None = None
    if metric.value is not None:
        below = sum(1 for v in values if v < metric.value)
        ties = sum(1 for v in values if v == metric.value)
        # 동점은 절반만 아래로 세는 통상적 정의.
        raw = (below + ties / 2) / len(values) * 100
        percentile = raw if higher_is_better else 100 - raw

    return PeerStat(
        metric_key=metric.key,
        median=median,
        sample_size=len(values),
        percentile=percentile,
        higher_is_better=higher_is_better,
        unit=metric.unit,
        currency=currency,
        scope=scope,
    )
