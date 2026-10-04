"""기본 기준연도 — 하드코딩된 2024 를 대체한다.

사업보고서는 결산 후 90일 안에 제출된다(12월 결산이면 이듬해 3월 말).
그래서 4월부터는 직전 연도를, 1~3월에는 그 전 연도를 기본값으로 삼아야
"재무제표가 없습니다"로 시작하지 않는다.
"""

from __future__ import annotations

from datetime import date

from fin_checkup.fiscal import default_fiscal_year


def test_after_filing_deadline_uses_previous_year():
    assert default_fiscal_year(date(2026, 10, 5)) == 2025
    assert default_fiscal_year(date(2026, 4, 1)) == 2025


def test_before_filing_deadline_uses_two_years_back():
    assert default_fiscal_year(date(2026, 1, 15)) == 2024
    assert default_fiscal_year(date(2026, 3, 31)) == 2024


def test_defaults_to_today_when_not_given():
    today = date.today()
    expected = today.year - 1 if today.month >= 4 else today.year - 2
    assert default_fiscal_year() == expected
