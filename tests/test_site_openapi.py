"""`/openapi.json` — OpenAPI 3.1 로 정적 GET 엔드포인트를 적고, 빌드한 JSON 이 그 스키마에 맞는지.

스키마와 실제 JSON 이 어긋나면(필드를 더했는데 스키마를 안 고쳤거나, 열거값이 바뀌었거나) 여기서 막힌다.
공개 스키마는 새 필드를 허용하지만, 테스트는 `additionalProperties: false` 로 조여 선언 누락까지 잡는다.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date

import pytest
from hub_seed import HLB, KB, SAMSUNG, seed_site_cache
from jsonschema import Draft202012Validator

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain import banned
from fin_checkup.site.build import build_site
from fin_checkup.site.links import check_links
from fin_checkup.storage import Cache

BASE = "https://example.github.io/fin-checkup/"


async def _build(tmp_path, base_url: str = BASE, name: str = "out"):
    with Cache(tmp_path / f"{name}.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp_path / name
        await build_site(cache, target, 2024, base_url=base_url, built_on=date(2026, 11, 21), images=False)
    return target


@pytest.fixture
async def out(tmp_path):
    return await _build(tmp_path)


def spec(out) -> dict:
    return json.loads((out / "openapi.json").read_text(encoding="utf-8"))


def _strict(node):
    """속성을 선언한 객체마다 additionalProperties: false — 선언하지 않은 필드를 잡는다."""
    if isinstance(node, dict):
        node = {k: _strict(v) for k, v in node.items()}
        if node.get("type") == "object" and "properties" in node:
            node.setdefault("additionalProperties", False)
        return node
    if isinstance(node, list):
        return [_strict(v) for v in node]
    return node


def validator(doc: dict, name: str, strict: bool = True) -> Draft202012Validator:
    components = _strict(copy.deepcopy(doc["components"])) if strict else doc["components"]
    schema = {"$ref": f"#/components/schemas/{name}", "components": components}
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def errors(v: Draft202012Validator, instance) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in v.iter_errors(instance)]


def load(out, rel: str):
    return json.loads((out / rel).read_text(encoding="utf-8"))


async def test_openapi_is_3_1_with_the_site_as_server(out):
    doc = spec(out)
    assert doc["openapi"].startswith("3.1")
    assert doc["servers"] == [{"url": BASE.rstrip("/")}]
    assert DISCLAIMER in doc["info"]["description"]
    assert banned.find_outside_allowed(json.dumps(doc, ensure_ascii=False)) == []


async def test_every_path_is_a_get_on_a_file_the_build_wrote(out):
    doc = spec(out)
    assert {
        "/api/index.json",
        "/api/c/{code}.json",
        "/c/{code}/index.md",
        "/search-index.json",
        "/build.json",
    } <= set(doc["paths"])
    for path, item in doc["paths"].items():
        assert set(item) == {"get"}, path
        concrete = path.replace("{code}", SAMSUNG.stock_code).lstrip("/")
        assert (out / concrete).is_file(), path
        op = item["get"]
        assert op["operationId"] and op["responses"]["200"]["content"]


@pytest.mark.parametrize("corp", [SAMSUNG, KB, HLB], ids=lambda c: c.stock_code)
async def test_company_json_validates_strictly(out, corp):
    v = validator(spec(out), "Company")
    assert errors(v, load(out, f"api/c/{corp.stock_code}.json")) == []


@pytest.mark.parametrize(
    ("name", "rel"),
    [("CompanyIndex", "api/index.json"), ("SearchIndex", "search-index.json"), ("BuildInfo", "build.json")],
)
async def test_other_json_files_validate_strictly(out, name, rel):
    assert errors(validator(spec(out), name), load(out, rel)) == []


async def test_schema_catches_drift(out):
    doc = spec(out)
    company = load(out, "api/c/005930.json")
    v = validator(doc, "Company")
    bad = copy.deepcopy(company)
    bad["metrics"][0]["signal"]["key"] = "great"
    assert errors(v, bad), "없는 신호 값"
    bad = copy.deepcopy(company)
    bad["metrics"][0]["value"] = "12%"
    assert errors(v, bad), "값은 숫자 또는 null"
    bad = copy.deepcopy(company)
    bad["undeclared"] = 1
    assert errors(v, bad), "선언하지 않은 필드 (테스트만 엄격)"
    # 공개 스키마는 새 필드를 허용한다 — 필드 추가는 판을 올리지 않는 변경이다.
    assert errors(validator(doc, "Company", strict=False), bad) == []


async def test_missing_values_are_null_in_the_schema_too(out):
    schemas = spec(out)["components"]["schemas"]
    assert "null" in schemas["Metric"]["properties"]["value"]["type"]
    assert {"unknown", "not_applicable"} <= set(schemas["Signal"]["properties"]["key"]["enum"])


async def test_relative_base_uses_a_relative_server(tmp_path):
    out = await _build(tmp_path, base_url="/", name="rel")
    assert spec(out)["servers"] == [{"url": "."}]


# ── /agents/ ────────────────────────────────────────────────────────


async def test_agents_page_has_prompts_curl_and_links(out):
    html = (out / "agents" / "index.html").read_text(encoding="utf-8")
    for must in ("llms.txt", "openapi.json", "curl", "ChatGPT", "Claude", "Perplexity", "docs/agents.md"):
        assert must in html, must
    assert f"{BASE}llms.txt" in html, "붙여 넣는 프롬프트에는 절대주소"
    assert DISCLAIMER in html
    assert check_links(out, BASE).broken == []


async def test_agents_prompts_hold_the_line(out):
    html = (out / "agents" / "index.html").read_text(encoding="utf-8")
    prompts = re.findall(r'<pre class="fc-code-block fc-prompt"><code>(.*?)</code></pre>', html, re.S)
    assert len(prompts) >= 3
    for p in prompts:
        assert banned.find(p) == []
        assert "계산식" in p and "면책" in p


async def test_footer_and_llms_point_to_agents_page_and_openapi(out):
    page = (out / "c" / "005930" / "index.html").read_text(encoding="utf-8")
    assert 'href="../../agents/"' in page
    llms = (out / "llms.txt").read_text(encoding="utf-8")
    assert f"{BASE}openapi.json" in llms and f"{BASE}agents/" in llms
