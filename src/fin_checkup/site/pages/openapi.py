"""`/openapi.json` — 정적 GET 엔드포인트를 OpenAPI 3.1 로. 에이전트·도구가 스키마를 읽고 바로 부르게.

서버가 없는 정적 파일이라 GET 뿐이고 키·호출 제한이 없다. 스키마의 열거값(신호·묶음·업종·시장·보고서·
대조군 범위·지표 키)은 코드의 Enum·`engine.METRIC_DEFS` 에서 만든다 — 엔진이 바뀌면 스키마도 따라간다.
필드 모양은 `pages/api.py`·`pages/search_index.py`·`buildinfo.build_json` 과 같아야 하고,
`tests/test_site_openapi.py` 가 빌드한 JSON 을 이 스키마로 검증한다(선언하지 않은 필드도 잡는다).

공개 스키마에는 `additionalProperties: false` 를 두지 않는다 — 필드를 더하는 건 판을 올리지 않는 변경이라
(`docs/api-static.md`) 받는 쪽이 새 필드에 깨지면 안 된다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain.agents import AGENT_GUIDANCE
from fin_checkup.metrics.engine import METRIC_DEFS, Category
from fin_checkup.metrics.peers import PeerScope
from fin_checkup.metrics.sector import Sector
from fin_checkup.metrics.signals import Signal
from fin_checkup.models import FsDiv, Market, ReportCode
from fin_checkup.site.buildinfo import BUILD_JSON
from fin_checkup.site.buildinfo import SCHEMA_VERSION as BUILD_SCHEMA_VERSION
from fin_checkup.site.context import Site
from fin_checkup.site.pages.api import SCHEMA_VERSION
from fin_checkup.site.pages.feeds import MARKET_FEED
from fin_checkup.site.pages.llms import API_DOC, LLMS, LLMS_FULL
from fin_checkup.site.pages.search_index import PATH as SEARCH_INDEX
from fin_checkup.site.registry import Output, page

PATH = "openapi.json"
CODE_PATTERN = "^[0-9A-Z]{6}$"

_REF = "#/components/schemas/"


def _ref(name: str) -> dict[str, str]:
    return {"$ref": _REF + name}


def _obj(props: dict[str, Any], description: str = "") -> dict[str, Any]:
    """모든 필드가 늘 있는 객체 (값이 null 일 수는 있다)."""
    schema: dict[str, Any] = {"type": "object", "required": list(props), "properties": props}
    if description:
        schema["description"] = description
    return schema


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return {"oneOf": [schema, {"type": "null"}]}
    out = dict(schema)
    kind = out.get("type")
    out["type"] = [kind, "null"] if isinstance(kind, str) else [*kind, "null"]
    if "enum" in out:
        out["enum"] = [*out["enum"], None]
    return out


STR = {"type": "string"}
INT = {"type": "integer"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}
DATE = {"type": "string", "format": "date"}
CODE = {
    "type": "string",
    "pattern": CODE_PATTERN,
    "description": "KRX 6자리 종목코드",
    "examples": ["005930"],
}


def _enum(values: list[str], description: str = "") -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", "enum": values}
    if description:
        schema["description"] = description
    return schema


def schemas() -> dict[str, Any]:
    market = _enum([m.value for m in Market])
    signal = _obj(
        {
            "key": _enum(
                [s.value for s in Signal],
                "green 정상 · yellow 주의 · red 위험 · neutral 측정값(절대 기준 없음) · "
                "not_applicable 해당 없음(⊘, 업종에 맞지 않음 — note 에 대신 볼 것) · "
                "unknown 데이터 없음(⚫, 0 이 아니다)",
            ),
            "emoji": _enum([s.emoji for s in Signal]),
            "label": _enum([s.label for s in Signal]),
        }
    )
    return {
        "Signal": signal,
        "Thresholds": _obj(
            {"good": NUM, "warn": NUM, "higher_is_better": BOOL},
            "higher_is_better 면 값 ≥ good 🟢, ≥ warn 🟡, 그 아래 🔴. 아니면 ≤ good 🟢, ≤ warn 🟡, 그 위 🔴.",
        ),
        "Peer": _obj(
            {
                "scope": _enum([s.value for s in PeerScope], "industry 동종업계 / market 전체 상장사"),
                "scope_label": STR,
                "sample_size": INT,
                "median": _nullable(NUM),
                "median_text": STR,
                "percentile": _nullable(
                    {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "높을수록 그 지표 기준 나은 쪽",
                    }
                ),
                "summary": STR,
            },
            "대조군 분포. 표본이 5개사 미만이면 metric.peer 가 null.",
        ),
        "Delta": _obj(
            {
                "previous": _nullable(NUM),
                "current": _nullable(NUM),
                "improved": _nullable(
                    {
                        "type": "boolean",
                        "description": "엔진 경계값 방향으로 좋아진 쪽인가. 같거나 경계값이 없는 지표면 null",
                    }
                ),
                "summary": {
                    "type": "string",
                    "description": "한 줄 — '작년 X → 올해 Y (차이 높아짐/낮아짐 · "
                    "높을수록/낮을수록 좋게 보는 지표라 좋아진/나빠진 쪽)'. "
                    "경계값이 없는 지표는 평가 없이 차이만, 같으면 '작년과 같음 (X)'",
                },
            },
            "작년 대비. 작년 값이 없거나 ⊘ 면 metric.delta 가 null.",
        ),
        "Metric": _obj(
            {
                "key": _enum([d.key for d in METRIC_DEFS]),
                "label": STR,
                "category": _enum([c.value for c in Category]),
                "value": _nullable(
                    {"type": "number", "description": "값이 없으면 null(⚫) — 0 으로 채우지 않는다"}
                ),
                "unit": {"type": "string", "description": "%·배·회·money (money 의 통화는 currency)"},
                "value_text": {"type": "string", "description": "화면과 같은 표시 문자열. 값이 없으면 '—'"},
                "signal": _ref("Signal"),
                "formula": {"type": "string", "description": "계산식 — 신호와 함께 늘 보여 준다"},
                "thresholds": _nullable(_ref("Thresholds")),
                "threshold_text": {
                    "type": "string",
                    "description": "경계값 한 줄 — 신호와 함께 늘 보여 준다",
                },
                "note": {"type": "string", "description": "⊘ 면 대신 볼 것, ⚫ 면 무엇이 없었는지"},
                "peer": _nullable(_ref("Peer")),
                "delta": _nullable(_ref("Delta")),
            }
        ),
        "RedFlag": _obj({"key": STR, "label": STR, "detail": STR, "reference": STR}),
        "Source": {
            "type": "object",
            "required": ["name", "url"],
            "properties": {
                "name": STR,
                "url": {"type": "string", "format": "uri"},
                "params": {
                    "type": "object",
                    "additionalProperties": STR,
                    "description": "OpenDART 요청 파라미터. 인증키는 싣지 않는다 — 각자 발급받아 붙인다",
                },
            },
        },
        "Company": _obj(
            {
                "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
                "disclaimer": {"type": "string", "const": DISCLAIMER},
                "built_on": DATE,
                "fiscal_year": INT,
                "summary_ko": {
                    "type": "string",
                    "description": "측정 결과의 평이한 사실 문단 (평가·권유 없음)",
                },
                "agent_guidance": {"type": "string", "const": AGENT_GUIDANCE},
                "company": _obj(
                    {
                        "name": STR,
                        "stock_code": CODE,
                        "corp_code": {"type": "string", "pattern": "^[0-9]{8}$"},
                        "market": _nullable(market),
                        "industry_code": _nullable(STR),
                        "peer_group": _nullable(STR),
                        "sector": _enum([s.value for s in Sector]),
                        "sector_label": STR,
                        "page": STR,
                    }
                ),
                "report": _obj(
                    {
                        "reprt_code": _enum([r.value for r in ReportCode]),
                        "label": STR,
                        "fs_div": _enum([f.value for f in FsDiv], "CFS 연결 / OFS 개별"),
                    }
                ),
                "currency": STR,
                "years": {"type": "array", "items": INT},
                "metrics": {"type": "array", "items": _ref("Metric"), "minItems": len(METRIC_DEFS)},
                "red_flags": {"type": "array", "items": _ref("RedFlag")},
                "sources": {"type": "array", "items": _ref("Source")},
            },
            "회사 하나의 측정 결과 (api/c/<code>.json).",
        ),
        "CompanyIndex": _obj(
            {
                "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
                "disclaimer": {"type": "string", "const": DISCLAIMER},
                "built_on": DATE,
                "fiscal_year": INT,
                "companies": {
                    "type": "array",
                    "items": _obj(
                        {
                            "stock_code": CODE,
                            "name": STR,
                            "market": _nullable(market),
                            "json": STR,
                            "page": STR,
                        }
                    ),
                },
            },
            "회사 목록, 종목코드순 (api/index.json).",
        ),
        "SearchIndex": _obj(
            {
                "year": INT,
                "built_on": DATE,
                "companies": {
                    "type": "array",
                    "items": _obj(
                        {
                            "name": STR,
                            "code": CODE,
                            "market": {"type": "string", "description": "모르면 빈 문자열"},
                            "sector": STR,
                            "chosung": {"type": "string", "description": "이름의 초성 (ㅅㅅㅈㅈ)"},
                        }
                    ),
                },
            },
            "찾기용 최소 필드, 이름순 — 지표 값은 없다 (search-index.json).",
        ),
        "BuildInfo": _obj(
            {
                "schema_version": {"type": "integer", "const": BUILD_SCHEMA_VERSION},
                "disclaimer": {"type": "string", "const": DISCLAIMER},
                "built_at": {"type": "string", "format": "date-time"},
                "built_on": DATE,
                "fiscal_year": INT,
                "fiscal_years_in_data": {"type": "array", "items": INT},
                "expected_fiscal_year": INT,
                "fresh": BOOL,
                "snapshot": _nullable(
                    _obj({"built_on": STR, "built_at": STR, "bsns_years": {"type": "array", "items": INT}})
                ),
                "disclosures": _nullable(_obj({"begin": DATE, "end": DATE, "count": INT})),
                "git_sha": _nullable(STR),
                "counts": _obj({"companies": INT, "skipped": INT, "pages": INT, "files": INT}),
            },
            "빌드 메타 (build.json).",
        ),
    }


def _get(op_id: str, summary: str, content: dict[str, Any], description: str = "", params=None) -> dict:
    op: dict[str, Any] = {
        "operationId": op_id,
        "summary": summary,
        "responses": {"200": {"description": "OK", "content": content}},
    }
    if description:
        op["description"] = description
    if params:
        op["parameters"] = params
    return {"get": op}


def _json_body(name: str) -> dict[str, Any]:
    return {"application/json": {"schema": _ref(name)}}


def _text(media: str) -> dict[str, Any]:
    return {media: {"schema": {"type": "string"}}}


_CODE_PARAM = [{"name": "code", "in": "path", "required": True, "schema": CODE, "example": "005930"}]

DESCRIPTION = (
    "Static JSON/Markdown files of fin-checkup: measured financial-ratio checkups of Korean listed companies,"
    " computed from DART filings. GET only, no key, no rate limit. Find a code with getCompanyIndex or"
    " getSearchIndex, then call getCompany (or getCompanyMarkdown). "
    + AGENT_GUIDANCE
    + f" Full rules for agents: {LLMS}. Field reference: {API_DOC}. Disclaimer: {DISCLAIMER}"
)


def openapi_doc(site: Site) -> dict[str, Any]:
    server = site.base_url.rstrip("/") if site.has_absolute_base else "."
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "fin-checkup static API",
            "version": f"{SCHEMA_VERSION}.{site.built_on:%Y%m%d}",
            "summary": "Korean listed companies' financial ratios with signal, formula and thresholds.",
            "description": DESCRIPTION,
            "license": {"name": "MIT", "identifier": "MIT"},
            "x-disclaimer": DISCLAIMER,
            "x-agent-guidance": AGENT_GUIDANCE,
        },
        "servers": [{"url": server}],
        "externalDocs": {"description": "JSON field reference", "url": API_DOC},
        "paths": {
            "/api/index.json": _get(
                "getCompanyIndex",
                "Every company: code, name, market, JSON and page paths",
                _json_body("CompanyIndex"),
            ),
            "/api/c/{code}.json": _get(
                "getCompany",
                "One company's 17 metrics with signal, formula, thresholds, peer median, red flags",
                _json_body("Company"),
                "404 if the site has no checkup for that code.",
                _CODE_PARAM,
            ),
            "/c/{code}/index.md": _get(
                "getCompanyMarkdown",
                "The same measurements as one compact Markdown page",
                _text("text/markdown"),
                params=_CODE_PARAM,
            ),
            "/c/{code}.md": _get(
                "getCompanyMarkdownAlias",
                "Alias of getCompanyMarkdown",
                _text("text/markdown"),
                params=_CODE_PARAM,
            ),
            f"/{SEARCH_INDEX}": _get(
                "getSearchIndex",
                "Name, code, market, sector and 초성 for fuzzy lookup (no metric values)",
                _json_body("SearchIndex"),
            ),
            f"/{BUILD_JSON}": _get("getBuildInfo", "Build time, data years, counts", _json_body("BuildInfo")),
            f"/{MARKET_FEED}": _get(
                "getDisclosureFeed",
                "Atom feed of recent risk-related DART filings",
                _text("application/atom+xml"),
            ),
            "/feeds/c/{code}.xml": _get(
                "getCompanyFeed", "One company's Atom feed", _text("application/atom+xml"), params=_CODE_PARAM
            ),
            f"/{LLMS}": _get("getLlmsTxt", "Rules and recipe for agents (llmstxt.org)", _text("text/plain")),
            f"/{LLMS_FULL}": _get(
                "getLlmsFullTxt", "llms.txt plus every metric explainer inline", _text("text/plain")
            ),
        },
        "components": {"schemas": schemas()},
    }


@page("openapi")
def openapi_file(site: Site) -> Iterator[Output]:
    yield Output(PATH, json.dumps(openapi_doc(site), ensure_ascii=False, indent=1) + "\n")
