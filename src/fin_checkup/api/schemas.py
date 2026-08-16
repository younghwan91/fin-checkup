"""API 응답 모델.

내부 모델을 그대로 내보내지 않는다. 응답에 무엇이 나가는지 여기서 한눈에 보여야
인증키나 이메일이 실수로 새는 걸 막을 수 있다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from fin_checkup.metrics.engine import MONEY, CheckupResult, Metric
from fin_checkup.metrics.redflags import RedFlag
from fin_checkup.service import Checkup

DISCLAIMER = (
    "본 응답은 공시 수치를 공개된 재무비율 공식으로 계산한 측정 결과입니다. "
    "투자권유·투자자문이 아니며, 데이터 정확성 및 이용에 따른 손익에 책임지지 않습니다."
)


class MetricOut(BaseModel):
    key: str
    label: str
    category: str
    value: float | None
    unit: str
    signal: str
    signal_label: str
    description: str
    formula: str
    note: str = ""
    #: 판정 경계값. 왜 이 색인지 감추지 않는다.
    band_good: float | None = None
    band_warn: float | None = None
    higher_is_better: bool | None = None

    @classmethod
    def of(cls, metric: Metric) -> MetricOut:
        band = metric.band
        return cls(
            key=metric.key,
            label=metric.label,
            category=metric.category.value,
            value=metric.value,
            unit="currency" if metric.unit == MONEY else metric.unit,
            signal=metric.signal.value,
            signal_label=metric.signal.label,
            description=metric.description,
            formula=metric.formula,
            note=metric.note,
            band_good=band.good if band else None,
            band_warn=band.warn if band else None,
            higher_is_better=band.higher_is_better if band else None,
        )


class RedFlagOut(BaseModel):
    key: str
    label: str
    detail: str
    reference: str = ""

    @classmethod
    def of(cls, flag: RedFlag) -> RedFlagOut:
        return cls(key=flag.key, label=flag.label, detail=flag.detail, reference=flag.reference)


class YearlyFigure(BaseModel):
    year: int
    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    operating_cash_flow: float | None = None


class CheckupOut(BaseModel):
    corp_code: str
    corp_name: str
    stock_code: str = ""
    market: str | None = None
    sector: str
    sector_label: str
    bsns_year: int
    currency: str
    metrics: list[MetricOut]
    red_flags: list[RedFlagOut] = Field(default_factory=list)
    history: list[YearlyFigure] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    disclaimer: str = DISCLAIMER

    @classmethod
    def of(cls, data: Checkup) -> CheckupOut:
        result: CheckupResult = data.result
        return cls(
            corp_code=data.corp.corp_code,
            corp_name=data.corp.corp_name,
            stock_code=data.corp.stock_code,
            market=data.company.market.value if data.company and data.company.market else None,
            sector=result.sector.value,
            sector_label=result.sector.label,
            bsns_year=result.bsns_year,
            currency=result.currency,
            metrics=[MetricOut.of(m) for m in result.metrics],
            red_flags=[RedFlagOut.of(f) for f in data.red_flags],
            history=[
                YearlyFigure(
                    year=f.bsns_year,
                    revenue=f.revenue,
                    operating_income=f.operating_income,
                    net_income=f.net_income,
                    total_assets=f.total_assets,
                    total_liabilities=f.total_liabilities,
                    total_equity=f.total_equity,
                    operating_cash_flow=f.operating_cash_flow,
                )
                for f in data.history
            ],
            counts={s.value: n for s, n in result.counts.items()},
        )


class SearchHit(BaseModel):
    corp_code: str
    corp_name: str
    stock_code: str


class WatchItem(BaseModel):
    corp_code: str
    corp_name: str
    stock_code: str


class AccountOut(BaseModel):
    user_id: str
    watchlist_count: int


class IssuedKeyOut(BaseModel):
    """키 원문이 나가는 유일한 응답. 다시는 볼 수 없다."""

    user_id: str
    api_key: str
    warning: str = "이 키는 지금 한 번만 표시됩니다. 안전한 곳에 보관하세요."


class HealthOut(BaseModel):
    status: str
    corp_codes: int
    dart_calls_today: int
    dart_daily_quota: int
    alerts_last_poll: str | None = None
