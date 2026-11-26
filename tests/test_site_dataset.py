"""`/agents/` 의 schema.org `Dataset` — Google 데이터세트 검색이 정적 JSON(`/api/c/<코드>.json`)을 찾게.

검색 노출 가드(`site/seo.py`)는 색인 페이지마다 JSON-LD 가 `WebPage` 하나이길 요구한다. 그래서 Dataset 은
정적 JSON 을 안내하는 `/agents/` 페이지의 `mainEntity` 로 싣는다. 값은 빌드에 실제로 있는 것만 — 사업연도
범위는 데이터에서, 내려받기 주소는 빌드한 파일에서. 원천 데이터(DART)에는 명시적인 라이선스가 없으니
(`docs/REGULATORY.md`) MIT 라고 쓰지 않는다 — MIT 는 그것을 만드는 코드의 라이선스다.
"""

from __future__ import annotations

import json
import re
from datetime import date

import pytest
from hub_seed import seed_site_cache

from fin_checkup.explain import banned
from fin_checkup.metrics.engine import METRIC_DEFS
from fin_checkup.site.build import build_site
from fin_checkup.site.seo import check_seo
from fin_checkup.storage import Cache

BASE = "https://example.github.io/fin-checkup/"


async def _build(tmp_path, base_url: str = BASE):
    with Cache(tmp_path / "site.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp_path / "out"
        await build_site(cache, target, 2024, base_url=base_url, built_on=date(2026, 11, 21), images=False)
    return target


@pytest.fixture
async def out(tmp_path):
    return await _build(tmp_path)


def page_ld(out) -> dict:
    text = (out / "agents" / "index.html").read_text(encoding="utf-8").split("</head>", 1)[0]
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.DOTALL)
    assert len(blocks) == 1
    return json.loads(blocks[0])


async def test_agents_page_carries_a_dataset_for_the_static_json(out):
    page = page_ld(out)
    assert page["@type"] == "WebPage" and page["url"] == f"{BASE}agents/"
    ds = page["mainEntity"]
    assert ds["@type"] == "Dataset"
    for key in ("name", "description", "url", "license", "creator", "temporalCoverage", "distribution"):
        assert ds.get(key), key
    # Google 데이터세트 검색: 설명 50~5,000자.
    assert 50 <= len(ds["description"]) <= 5000
    assert ds["isAccessibleForFree"] is True
    assert ds["inLanguage"] == "ko-KR"
    assert ds["creator"]["url"] == "https://github.com/younghwan91/fin-checkup"
    assert ds["dateModified"] == "2026-11-21"


async def test_temporal_coverage_is_the_fiscal_years_in_the_data(out):
    years = set()
    for f in (out / "api" / "c").glob("*.json"):
        years |= set(json.loads(f.read_text(encoding="utf-8"))["years"])
    expected = f"{min(years)}/{max(years)}" if len(years) > 1 else str(min(years))
    assert page_ld(out)["mainEntity"]["temporalCoverage"] == expected


async def test_distribution_points_at_files_the_build_wrote(out):
    downloads = page_ld(out)["mainEntity"]["distribution"]
    assert downloads and all(d["@type"] == "DataDownload" for d in downloads)
    assert all(d["encodingFormat"] == "application/json" for d in downloads)
    urls = [d["contentUrl"] for d in downloads]
    assert f"{BASE}api/index.json" in urls
    assert any(re.fullmatch(rf"{re.escape(BASE)}api/c/[0-9A-Z]{{6}}\.json", u) for u in urls)
    for u in urls:
        assert (out / u.removeprefix(BASE)).is_file(), u


async def test_license_does_not_call_the_dart_data_mit(out):
    ds = page_ld(out)["mainEntity"]
    # 원천 데이터의 이용 조건은 OpenDART 이용약관 — 명시적 허락 문구가 없으니 그것을 라이선스로 적는다.
    assert ds["license"]["url"] == "https://opendart.fss.or.kr/intro/terms.do"
    assert "MIT" not in json.dumps(ds["license"], ensure_ascii=False)
    assert "DART" in ds["isBasedOn"]["name"]
    assert "투자권유" in ds["description"] or "투자 권유" in ds["description"]
    assert banned.find(json.dumps(ds, ensure_ascii=False)) == []


async def test_variables_are_the_metrics_the_json_holds(out):
    measured = page_ld(out)["mainEntity"]["variableMeasured"]
    assert measured == [spec.label for spec in METRIC_DEFS]


async def test_seo_guard_wants_the_dataset_on_the_agents_page(out):
    assert check_seo(out) == []
    page = out / "agents" / "index.html"
    text = page.read_text(encoding="utf-8")
    page.write_text(text.replace('"mainEntity"', '"mainEntityGone"'), encoding="utf-8")
    assert any("Dataset" in v.detail for v in check_seo(out))


async def test_relative_build_has_no_dataset(tmp_path):
    out = await _build(tmp_path, base_url="/")
    text = (out / "agents" / "index.html").read_text(encoding="utf-8")
    assert "application/ld+json" not in text
