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
    #: 분포 모양 — 10·25·75·90 백분위수 (inclusive 방식). 분포 그림(`site/charts.py`)이 쓴다.
    p10: float | None = None
    q1: float | None = None
    q3: float | None = None
    p90: float | None = None

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
    #: 엔진 경계값(Band.higher_is_better)의 방향. 경계값이 없는 지표(회전율)는 None —
    #: 좋아졌다·나빠졌다를 말하지 않는다.
    higher_is_better: bool | None
    unit: str = ""
    currency: str = "KRW"

    @property
    def unchanged(self) -> bool:
        """화면에 같은 숫자로 보이면 같다 — "10.00% → 10.00% (0.00%p 낮아짐)" 을 쓰지 않는다."""
        if self.unit == MONEY:
            return self.current == self.previous
        return f"{self.previous:.2f}" == f"{self.current:.2f}"

    @property
    def improved(self) -> bool | None:
        """엔진 기준으로 좋아진 쪽인가. 같거나 경계값이 없는 지표면 None."""
        if self.unchanged or self.higher_is_better is None:
            return None
        return self.current > self.previous if self.higher_is_better else self.current < self.previous

    def _text(self, value: float) -> str:
        from fin_checkup.format import format_money

        if self.unit == MONEY:
            return format_money(value, self.currency)
        return f"{value:,.2f}{self.unit}"

    @property
    def before_text(self) -> str:
        return self._text(self.previous)

    @property
    def after_text(self) -> str:
        return self._text(self.current)

    @property
    def values_text(self) -> str:
        """"작년 27.93% → 올해 29.94%"."""
        return f"작년 {self.before_text} → 올해 {self.after_text}"

    @property
    def change_text(self) -> str:
        """값이 어느 쪽으로 얼마나 움직였나 — 평가 없이. "2.01%p 높아짐"·"80억원 늘어남".

        퍼센트 지표의 차이는 %p(118% → 142% 는 '24% 늘었다' 가 아니다). 배는 단위를 떼어 적는다 —
        "1.50배 높아짐" 은 '1.5배가 됐다' 로 읽힌다.
        """
        up = self.current > self.previous
        if self.unit == MONEY:
            return f"{self._text(abs(self.current - self.previous))} {'늘어남' if up else '줄어듦'}"
        # 보이는 두 숫자의 차이로 — 0.5847 → 0.5884 를 "0.58 → 0.59 (0.00 높아짐)" 으로 쓰지 않는다.
        gap = abs(round(self.current, 2) - round(self.previous, 2))
        unit = {"%": "%p", "배": ""}.get(self.unit, self.unit)
        return f"{gap:,.2f}{unit} {'높아짐' if up else '낮아짐'}"

    @property
    def direction_text(self) -> str:
        """그 움직임이 엔진 경계값의 방향으로 어느 쪽인가. 경계값이 없으면 빈 문자열."""
        if self.improved is None:
            return ""
        side = "높을수록" if self.higher_is_better else "낮을수록"
        return f"{side} 좋게 보는 지표라 {'좋아진' if self.improved else '나빠진'} 쪽"

    @property
    def summary(self) -> str:
        """한 줄 — "작년 118.70% → 올해 142.16% (23.46%p 높아짐 · 낮을수록 좋게 보는 지표라 나빠진 쪽)".

        값의 움직임(높아짐)과 평가(나빠진 쪽)를 따로 적는다. 예전 "작년 118.70% ↑ 악화" 는 화살표와
        낱말이 서로 반대로 읽혔다(2026-11-21 사용성 조사). JSON `delta.summary`·Streamlit·MCP 가
        이 문장을 쓴다.
        """
        if self.unchanged:
            return f"작년과 같음 ({self.before_text})"
        tail = " · ".join(t for t in (self.change_text, self.direction_text) if t)
        return f"{self.values_text} ({tail})"


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
        higher_is_better=metric.band.higher_is_better if metric.band else None,
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
    q1 = q3 = p10 = p90 = None
    if len(values) >= 2:
        q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
        deciles = statistics.quantiles(values, n=10, method="inclusive")
        p10, p90 = deciles[0], deciles[-1]

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
        p10=p10,
        q1=q1,
        q3=q3,
        p90=p90,
    )
