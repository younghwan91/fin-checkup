"""회사 페이지 차트 섹션에 넘길 값 — 그림 자체는 `site/charts.py` 가 만든다."""

from __future__ import annotations

from typing import Any

from fin_checkup.site.charts import peer_strips, signal_heatmap, trend_panels, years_of
from fin_checkup.site.context import CompanyEntry, Site
from fin_checkup.site.registry import company_context


@company_context("trend")
def trend_context(entry: CompanyEntry, site: Site) -> dict[str, Any]:
    panels = trend_panels(entry.checkup)
    return {
        "trend_panels": panels,
        "trend_years": years_of(panels),
        "signal_heatmap": signal_heatmap(entry.checkup),
    }


@company_context("peer_strip")
def peer_strip_context(entry: CompanyEntry, site: Site) -> dict[str, Any]:
    return {"peer_strips": peer_strips(entry.checkup)}
