"""`<head>` 메타 — 페이지별 제목·설명, canonical, Open Graph·트위터 카드, lang, theme-color.

절대주소(canonical·og:url·og:image)는 `--base-url` 이 절대주소일 때만 낸다. 기본값 `/` 로 만든
canonical 은 GitHub Pages 하위 경로(`/fin-checkup/`)에서 틀린 주소가 된다.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date

import pytest
from hub_seed import seed_site_cache
from PIL import Image

from fin_checkup.site import build
from fin_checkup.site.build import build_site
from fin_checkup.site.context import OG_IMAGE_PAGE, page_url
from fin_checkup.site.images.fonts import resolve_fonts
from fin_checkup.site.seo import check_seo
from fin_checkup.storage import Cache

BASE = "https://example.github.io/fin-checkup/"


async def _build(tmp_path, base_url: str = BASE, name: str = "out", images: bool = False):
    with Cache(tmp_path / f"{name}.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp_path / name
        await build_site(
            cache, target, 2024, base_url=base_url, built_on=date(2026, 11, 21), images=images
        )
    return target


@pytest.fixture
async def out(tmp_path):
    return await _build(tmp_path)


def read(out, path: str) -> str:
    return (out / path).read_text(encoding="utf-8")


def meta(text: str, attr: str, name: str) -> str | None:
    m = re.search(rf'<meta {attr}="{re.escape(name)}" content="([^"]*)"', text)
    return html.unescape(m.group(1)) if m else None


def head(text: str) -> str:
    return text.split("</head>", 1)[0]


def json_ld(text: str) -> dict:
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', head(text), re.DOTALL)
    assert len(blocks) == 1, blocks
    return json.loads(blocks[0])


@pytest.mark.parametrize(
    ("path", "url"),
    [("index.html", ""), ("c/005930/index.html", "c/005930/"), ("feeds/x.xml", "feeds/x.xml")],
)
def test_page_url_drops_index_html(path, url):
    assert page_url(path) == url


async def test_company_title_and_description_state_the_year_and_metric_count(out):
    text = read(out, "c/005930/index.html")
    title = html.unescape(re.search(r"<title>([^<]*)</title>", text).group(1))
    # 검색어(회사 이름 + 지표 이름)가 앞에 온다 — "삼성전자 부채비율" 같은 검색에 걸리게.
    assert title == "삼성전자 재무 신호등 — 부채비율·영업이익률 등 17개 지표 · 2024 사업연도"
    desc = meta(text, "name", "description")
    assert desc is not None and "삼성전자" in desc and "005930" in desc and "계산식과 경계값" in desc
    assert "부채비율·영업이익률·ROE" in desc
    # 신호 개수는 기호가 아니라 글자로 — 검색 결과 스니펫에 이모지만 남지 않게.
    assert "위험" in desc or "주의" in desc or "정상" in desc


async def test_every_page_has_its_own_title_and_description(out):
    seen_titles: set[str] = set()
    for path in sorted(out.rglob("*.html")):
        text = read(out, str(path.relative_to(out)))
        title = re.search(r"<title>([^<]*)</title>", text).group(1)
        assert title not in seen_titles, f"{path}: 제목이 다른 페이지와 같다"
        seen_titles.add(title)
        desc = meta(text, "name", "description")
        assert desc and len(desc) >= 20, f"{path}: 설명이 없다"
        assert meta(text, "property", "og:title") == html.unescape(title), path
        assert meta(text, "property", "og:description") == desc, path
        assert meta(text, "name", "twitter:title") == html.unescape(title), path
        assert meta(text, "name", "twitter:description") == desc, path


async def test_canonical_and_og_url_are_absolute_with_a_base_url(out):
    text = read(out, "c/005930/index.html")
    assert f'<link rel="canonical" href="{BASE}c/005930/">' in text
    assert meta(text, "property", "og:url") == f"{BASE}c/005930/"
    assert f'<link rel="canonical" href="{BASE}">' in read(out, "index.html")
    assert f'<link rel="canonical" href="{BASE}learn/">' in read(out, "learn/index.html")


async def test_no_canonical_without_an_absolute_base_url(tmp_path):
    out = await _build(tmp_path, base_url="/", name="rel")
    for path in out.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert 'rel="canonical"' not in text, path
        assert 'property="og:url"' not in text and 'property="og:image"' not in text, path
        assert "application/ld+json" not in text, f"{path}: 상대 주소의 JSON-LD 는 틀린 정보다"


async def test_common_tags_lang_theme_color_site_name_and_card(out):
    for path in ("index.html", "c/005930/index.html", "learn/index.html"):
        text = read(out, path)
        assert '<html lang="ko"' in text
        assert meta(text, "property", "og:site_name") == "fin-checkup"
        assert meta(text, "property", "og:locale") == "ko_KR"
        assert meta(text, "property", "og:type") == "website"
        assert 'name="theme-color"' in head(text)
        assert meta(text, "name", "twitter:card") == "summary", "이미지가 없으면 큰 카드를 쓰지 않는다"


async def test_no_og_image_without_pngs(tmp_path):
    out = await _build(tmp_path, name="noimg", images=False)
    assert 'property="og:image"' not in read(out, "c/005930/index.html")
    assert not (out / "og").exists()


@pytest.mark.skipif(resolve_fonts() is None, reason="한국어 글꼴 없음 (CI 는 fonts-noto-cjk 를 깐다)")
async def test_og_image_points_at_a_png_that_was_built(tmp_path):
    out = await _build(tmp_path, name="img", images=True)
    text = read(out, "c/005930/index.html")
    assert meta(text, "property", "og:image") == f"{BASE}og/005930.png"
    assert meta(text, "name", "twitter:card") == "summary_large_image"
    assert meta(text, "name", "twitter:image") == f"{BASE}og/005930.png"
    assert json_ld(text)["primaryImageOfPage"] == f"{BASE}og/005930.png"
    assert check_seo(out) == []
    with Image.open(out / "og/005930.png") as png:
        assert png.size == (1200, 630)
    # 회사가 아닌 페이지는 회사 카드를 쓰지 않는다.
    assert 'property="og:image"' not in read(out, "index.html")


async def test_without_a_korean_font_the_build_skips_pngs_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "resolve_fonts", lambda: None)
    with Cache(tmp_path / "nofont.duckdb") as cache:
        seed_site_cache(cache)
        report = await build_site(cache, tmp_path / "nofont", 2024, base_url=BASE, images=True)
    assert "fonts-noto-cjk" in report.image_warning
    assert OG_IMAGE_PAGE not in report.pages
    assert not (tmp_path / "nofont" / "og").exists()
    assert 'property="og:image"' not in read(tmp_path / "nofont", "c/005930/index.html")


async def test_company_name_in_meta_is_escaped(tmp_path):
    from fin_checkup.models import CorpCode

    with Cache(tmp_path / "x.duckdb") as cache:
        seed_site_cache(cache)
        cache.save_corp_codes(
            [CorpCode(corp_code="00126380", corp_name='삼성"><script>x</script>', stock_code="005930")]
        )
        target = tmp_path / "esc"
        await build_site(cache, target, 2024, base_url=BASE, built_on=date(2026, 11, 21))
    text = (target / "c" / "005930" / "index.html").read_text(encoding="utf-8")
    assert "<script>x</script>" not in text
    assert '"><script>' not in head(text)


async def test_company_json_ld_names_the_company_and_the_path_to_it(out):
    data = json_ld(read(out, "c/005930/index.html"))
    assert data["@context"] == "https://schema.org" and data["@type"] == "WebPage"
    assert data["url"] == data["@id"] == f"{BASE}c/005930/"
    assert data["inLanguage"] == "ko-KR"
    assert data["isPartOf"]["url"] == BASE
    assert data["about"] == {"@type": "Corporation", "name": "삼성전자", "tickerSymbol": "KRX:005930"}
    assert data["dateModified"] == "2026-11-21"
    crumbs = data["breadcrumb"]["itemListElement"]
    assert [c["position"] for c in crumbs] == list(range(1, len(crumbs) + 1))
    assert crumbs[0]["item"] == BASE and crumbs[-1] == {
        "@type": "ListItem", "position": len(crumbs), "name": "삼성전자", "item": f"{BASE}c/005930/"
    }
    # 판단으로 읽히는 schema.org 속성(평점·리뷰)은 넣지 않는다 — docs/REGULATORY.md.
    assert not {"aggregateRating", "review"} & data.keys()


async def test_every_indexable_page_has_json_ld_and_404_has_none(out):
    for path in sorted(out.rglob("*.html")):
        rel = str(path.relative_to(out))
        text = read(out, rel)
        if rel == "404.html":
            assert "application/ld+json" not in text
            continue
        assert json_ld(text)["url"] == meta(text, "property", "og:url"), rel


async def test_json_ld_cannot_be_closed_by_a_company_name(tmp_path):
    from fin_checkup.models import CorpCode

    with Cache(tmp_path / "ld.duckdb") as cache:
        seed_site_cache(cache)
        cache.save_corp_codes(
            [CorpCode(corp_code="00126380", corp_name="삼성</script><b>&", stock_code="005930")]
        )
        target = tmp_path / "ld"
        await build_site(cache, target, 2024, base_url=BASE, built_on=date(2026, 11, 21))
    data = json_ld(read(target, "c/005930/index.html"))
    assert data["about"]["name"] == "삼성</script><b>&"


async def test_seo_guard_passes_a_clean_build(out):
    assert check_seo(out) == []


async def test_seo_guard_passes_a_relative_build(tmp_path):
    assert check_seo(await _build(tmp_path, base_url="/", name="rel2")) == []


async def test_seo_guard_catches_missing_meta_json_ld_and_sitemap_entries(out):
    page = out / "c" / "005930" / "index.html"
    text = page.read_text(encoding="utf-8")
    text = re.sub(r'<meta name="description" content="[^"]*">', "", text)
    text = re.sub(r'<script type="application/ld\+json">.*?</script>', "", text, flags=re.DOTALL)
    page.write_text(text, encoding="utf-8")
    sitemap = out / "sitemap.xml"
    sitemap.write_text(
        sitemap.read_text(encoding="utf-8").replace(f"<loc>{BASE}c/105560/</loc>", ""), encoding="utf-8"
    )
    found = {(v.path, v.detail) for v in check_seo(out)}
    assert ("c/005930/index.html", '<meta name="description"> 가 없다') in found
    assert ("c/005930/index.html", "JSON-LD 가 0개 (정확히 하나)") in found
    assert ("c/105560/index.html", "sitemap.xml 에 없다") in found


async def test_seo_guard_needs_sitemap_and_robots(out):
    (out / "sitemap.xml").unlink()
    (out / "robots.txt").unlink()
    details = [v.detail for v in check_seo(out)]
    assert "robots.txt 가 없다" in details and "sitemap.xml 가 없다" in details


async def test_seo_guard_catches_duplicate_titles(out):
    other = out / "c" / "105560" / "index.html"
    mine = re.search(r"<title>[^<]*</title>", read(out, "c/005930/index.html")).group(0)
    replaced = re.sub(r"<title>[^<]*</title>", mine, read(out, "c/105560/index.html"))
    other.write_text(replaced, encoding="utf-8")
    assert any("제목이 다른 페이지" in v.detail for v in check_seo(out))


def test_pages_workflow_runs_the_seo_guard():
    """배포 워크플로가 `site check` 를 돌려야 이 가드가 배포를 막는다 (check_seo 는 cli site check 안)."""
    from pathlib import Path

    workflow = (Path(__file__).parents[1] / ".github/workflows/pages.yml").read_text(encoding="utf-8")
    assert "fin_checkup.cli site check site-out" in workflow
