"""금액·지표 표시 형식.

한국은 억/조, 미국은 M/B 단위로 읽는다. 통화에 따라 다르게 끊어준다.
CLI와 Streamlit이 같은 함수를 써서 두 화면의 숫자가 어긋나지 않게 한다.
"""

from __future__ import annotations

from fin_checkup.metrics.engine import MONEY, Metric

MISSING = "—"


def format_money(value: float | None, currency: str = "KRW", compact: bool = True) -> str:
    if value is None:
        return MISSING
    if currency == "KRW":
        return _format_krw(value, compact)
    return _format_usd(value, currency, compact)


def _format_krw(value: float, compact: bool) -> str:
    if not compact:
        return f"{value:,.0f}원"
    sign = "-" if value < 0 else ""
    size = abs(value)
    if size >= 1e12:
        return f"{sign}{size / 1e12:,.1f}조원"
    if size >= 1e8:
        return f"{sign}{size / 1e8:,.0f}억원"
    if size >= 1e4:
        return f"{sign}{size / 1e4:,.0f}만원"
    return f"{sign}{size:,.0f}원"


def _format_usd(value: float, currency: str, compact: bool) -> str:
    symbol = "$" if currency == "USD" else f"{currency} "
    if not compact:
        return f"{symbol}{value:,.0f}"
    sign = "-" if value < 0 else ""
    size = abs(value)
    if size >= 1e9:
        return f"{sign}{symbol}{size / 1e9:,.1f}B"
    if size >= 1e6:
        return f"{sign}{symbol}{size / 1e6:,.1f}M"
    if size >= 1e3:
        return f"{sign}{symbol}{size / 1e3:,.1f}K"
    return f"{sign}{symbol}{size:,.0f}"


def format_metric(metric: Metric, currency: str = "KRW") -> str:
    """지표 값 한 줄 표시. 값이 없으면 '—'."""
    if metric.value is None:
        return MISSING
    if metric.unit == MONEY:
        return format_money(metric.value, currency)
    return f"{metric.value:,.2f}{metric.unit}"


def chart_scale(values: list[float | None], currency: str = "KRW") -> tuple[float, str]:
    """추이 그래프의 (나눌 값, 축 라벨).

    단위를 고정하면 삼성전자 매출 300조가 '3M 억원'으로 찍힌다. 실제 값의 크기를
    보고 조/억, B/M을 고른다.
    """
    largest = max((abs(v) for v in values if v is not None), default=0.0)
    if currency == "KRW":
        if largest >= 1e12:  # 1조 이상
            return 1e12, "조원"
        if largest >= 1e8:
            return 1e8, "억원"
        return 1e4, "만원"
    if largest >= 1e9:
        return 1e9, "십억 달러"
    if largest >= 1e6:
        return 1e6, "백만 달러"
    return 1e3, "천 달러"
