from __future__ import annotations

from fin_checkup.format import chart_scale, format_metric, format_money
from fin_checkup.metrics.engine import checkup
from fin_checkup.models import Financials, FsDiv, ReportCode


def fin(currency: str, **kwargs) -> Financials:
    return Financials(
        corp_code="1", bsns_year=2024, reprt_code=ReportCode.ANNUAL,
        fs_div=FsDiv.CFS, currency=currency, **kwargs
    )


def test_krw_uses_korean_units():
    assert format_money(1_234_500_000_000, "KRW") == "1.2조원"
    assert format_money(35_000_000_000, "KRW") == "350억원"
    assert format_money(-35_000_000_000, "KRW") == "-350억원"


def test_usd_uses_western_units():
    assert format_money(118_254_000_000, "USD") == "$118.3B"
    assert format_money(9_447_000_000, "USD") == "$9.4B"
    assert format_money(-500_000, "USD") == "-$500.0K"


def test_missing_money_is_a_dash():
    assert format_money(None) == "—"


def test_money_metric_follows_the_currency_of_the_data():
    krw = checkup(fin("KRW", operating_cash_flow=35_000_000_000))
    usd = checkup(fin("USD", operating_cash_flow=118_254_000_000))
    assert krw.currency == "KRW"
    assert usd.currency == "USD"
    assert "원" in format_metric(krw.by_key("operating_cash_flow"), krw.currency)
    assert "$" in format_metric(usd.by_key("operating_cash_flow"), usd.currency)
    assert "원" not in format_metric(usd.by_key("operating_cash_flow"), usd.currency)


def test_ratio_metrics_keep_their_own_unit():
    result = checkup(fin("USD", revenue=1000, operating_income=150))
    assert format_metric(result.by_key("operating_margin"), "USD") == "15.00%"


def test_chart_scale_follows_the_magnitude():
    """단위를 고정하면 삼성전자 매출 300조가 '3M 억원'으로 찍힌다."""
    assert chart_scale([300_900_000_000_000], "KRW") == (1e12, "조원")
    assert chart_scale([35_000_000_000], "KRW") == (1e8, "억원")
    assert chart_scale([50_000_000], "KRW") == (1e4, "만원")


def test_chart_scale_for_usd():
    assert chart_scale([391_000_000_000], "USD") == (1e9, "십억 달러")
    assert chart_scale([5_000_000], "USD") == (1e6, "백만 달러")


def test_chart_scale_uses_the_largest_value():
    # 작은 값이 섞여 있어도 축은 가장 큰 값에 맞춘다.
    assert chart_scale([1_000, None, 300_900_000_000_000], "KRW") == (1e12, "조원")


def test_chart_scale_handles_empty_and_none():
    assert chart_scale([], "KRW")[1] == "만원"
    assert chart_scale([None, None], "KRW")[1] == "만원"


def test_default_currency_is_krw():
    assert checkup(fin("KRW")).currency == "KRW"
    assert Financials(corp_code="1", bsns_year=2024, reprt_code=ReportCode.ANNUAL,
                      fs_div=FsDiv.CFS).currency == "KRW"
