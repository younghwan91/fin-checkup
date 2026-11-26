"""회사 페이지 '최근 분기' 섹션에 넘길 값.

사업보고서보다 뒤에 나온 분기보고서가 있을 때만 (`fin_checkup.quarter`).
"""

from __future__ import annotations

from typing import Any

from fin_checkup.quarter import debt_ratio_not_applicable, newer_than_annual
from fin_checkup.site.context import CompanyEntry, Site
from fin_checkup.site.registry import company_context


@company_context("quarter")
def quarter_context(entry: CompanyEntry, site: Site) -> dict[str, Any]:
    result = entry.checkup.result
    return {
        "quarter": newer_than_annual(site.quarters.get(entry.corp.corp_code), result.bsns_year),
        "quarter_debt_na": debt_ratio_not_applicable(result),
    }
