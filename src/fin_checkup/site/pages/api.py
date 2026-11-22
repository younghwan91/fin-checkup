"""정적 JSON `/api/c/<종목코드>.json` · `/api/index.json` — 개발자가 같은 데이터를 다시 쓰게.

스키마는 `docs/api-static.md` 에 적는다. 필드를 빼거나 뜻을 바꾸면 `SCHEMA_VERSION` 을 올린다
(필드를 더하는 것만으로는 올리지 않는다).

회사 페이지와 같은 값만 싣는다 — 판정은 `metrics.engine`, 경계값 문장은 `presentation.band_text`,
대조군·작년 대비는 `service.Checkup` 이 이미 만든 것. 여기서 새로 판정하지 않는다.
없는 값은 `null`(⚫), 업종에 맞지 않는 지표는 `signal.key == "not_applicable"`(⊘) + `note` 에 대신 볼 것.
DART 인증키는 어디에도 싣지 않는다 — 출처는 요청 파라미터만 적는다.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from typing import Any

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain.agents import AGENT_GUIDANCE
from fin_checkup.explain.summary import facts_summary
from fin_checkup.format import format_metric
from fin_checkup.metrics.engine import Metric
from fin_checkup.metrics.peers import PeerStat, SelfDelta
from fin_checkup.metrics.sector import peer_group_for
from fin_checkup.metrics.signals import Signal
from fin_checkup.presentation import band_text
from fin_checkup.site.context import CompanyEntry, Site
from fin_checkup.site.registry import Output, page

SCHEMA_VERSION = 1

DART_API = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"


def _number(value: float | None) -> float | None:
    """JSON 에 NaN·Infinity 는 없다. 유한하지 않은 값은 '없음' 으로 낸다 — 0 으로 채우지 않는다."""
    if value is None or not math.isfinite(value):
        return None
    return round(value, 6)


def _signal(signal: Signal) -> dict[str, str]:
    return {"key": signal.value, "emoji": signal.emoji, "label": signal.label}


def _peer(stat: PeerStat | None) -> dict[str, Any] | None:
    if stat is None:
        return None
    return {
        "scope": stat.scope.value,
        "scope_label": stat.scope.label,
        "sample_size": stat.sample_size,
        "median": _number(stat.median),
        "median_text": stat.median_text,
        "percentile": _number(stat.percentile),
        "summary": stat.summary,
    }


def _delta(delta: SelfDelta | None) -> dict[str, Any] | None:
    if delta is None:
        return None
    return {
        "previous": _number(delta.previous),
        "current": _number(delta.current),
        "improved": None if delta.unchanged else delta.improved,
        "summary": delta.summary,
    }


def _metric(
    metric: Metric, peer: PeerStat | None, delta: SelfDelta | None, currency: str
) -> dict[str, Any]:
    band = metric.band
    return {
        "key": metric.key,
        "label": metric.label,
        "category": metric.category.value,
        "value": _number(metric.value),
        "unit": metric.unit,
        # 화면과 같은 표시 문자열 (`format.format_metric`). 값이 없으면 '—'.
        "value_text": format_metric(metric, currency),
        "signal": _signal(metric.signal),
        "formula": metric.formula,
        "thresholds": None
        if band is None
        else {"good": band.good, "warn": band.warn, "higher_is_better": band.higher_is_better},
        "threshold_text": band_text(metric),
        "note": metric.note,
        "peer": _peer(peer),
        "delta": _delta(delta),
    }


def company_json(entry: CompanyEntry, site: Site) -> dict[str, Any]:
    data = entry.checkup
    latest = data.history[-1]
    result = data.result
    company = data.company
    industry_code = company.industry_code if company else None
    return {
        "schema_version": SCHEMA_VERSION,
        "disclaimer": DISCLAIMER,
        "built_on": site.built_on.isoformat(),
        "fiscal_year": latest.bsns_year,
        # 평이한 사실 문단과 에이전트용 고정 안내 (schema 1 에 더한 필드 — 판은 그대로).
        "summary_ko": facts_summary(data),
        "agent_guidance": AGENT_GUIDANCE,
        "company": {
            "name": entry.name,
            "stock_code": entry.stock_code,
            "corp_code": entry.corp.corp_code,
            "market": company.market.value if company and company.market else None,
            "industry_code": industry_code or None,
            "peer_group": peer_group_for(industry_code) or None,
            "sector": result.sector.value,
            "sector_label": result.sector.label,
            "page": entry.path,
        },
        "report": {
            "reprt_code": latest.reprt_code.value,
            "label": latest.reprt_code.label,
            "fs_div": latest.fs_div.value,
        },
        "currency": result.currency,
        "years": [f.bsns_year for f in data.history],
        "metrics": [
            _metric(m, data.peers.get(m.key), data.deltas.get(m.key), result.currency)
            for m in result.metrics
        ],
        "red_flags": [
            {"key": f.key, "label": f.label, "detail": f.detail, "reference": f.reference}
            for f in data.red_flags
        ],
        "sources": [
            {
                "name": "OpenDART 단일회사 전체 재무제표",
                "url": DART_API,
                # 인증키(crtfc_key)는 싣지 않는다 — 각자 발급받아 붙인다.
                "params": {
                    "corp_code": entry.corp.corp_code,
                    "bsns_year": str(latest.bsns_year),
                    "reprt_code": latest.reprt_code.value,
                    "fs_div": latest.fs_div.value,
                },
            },
            {"name": "금융감독원 전자공시시스템(DART)", "url": "https://dart.fss.or.kr/"},
        ],
    }


def index_json(site: Site) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "disclaimer": DISCLAIMER,
        "built_on": site.built_on.isoformat(),
        "fiscal_year": site.year,
        "companies": [
            {
                "stock_code": e.stock_code,
                "name": e.name,
                "market": e.checkup.company.market.value
                if e.checkup.company and e.checkup.company.market
                else None,
                "json": f"api/c/{e.stock_code}.json",
                "page": e.path,
            }
            for e in sorted(site.companies, key=lambda e: e.stock_code)
        ],
    }


def _dump(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"


@page("api")
def api_files(site: Site) -> Iterator[Output]:
    for entry in site.companies:
        yield Output(f"api/c/{entry.stock_code}.json", _dump(company_json(entry, site)))
    yield Output("api/index.json", _dump(index_json(site)))
