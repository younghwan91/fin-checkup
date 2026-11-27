"""sitemap.xml · robots.txt — 빌드가 쓴 색인 대상 HTML 전부, 주소는 --base-url, lastmod 는 빌드 날짜."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

import pytest
from hub_seed import html_pages, seed_site_cache

from fin_checkup.site import registry
from fin_checkup.site.build import build_site
from fin_checkup.site.registry import Output
from fin_checkup.site.sitemap import is_indexable

BASE = "https://example.github.io/fin-checkup/"
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


async def _build(tmp_path, base_url: str = BASE, name: str = "out"):
    from fin_checkup.storage import Cache

    with Cache(tmp_path / f"{name}.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp_path / name
        await build_site(cache, target, 2024, base_url=base_url, built_on=date(2026, 11, 21))
    return target


@pytest.fixture
async def out(tmp_path):
    return await _build(tmp_path)


def locs(out) -> list[str]:
    tree = ET.parse(out / "sitemap.xml")
    return [el.text for el in tree.getroot().findall("s:url/s:loc", NS)]


async def test_sitemap_lists_every_html_page_with_absolute_urls(out):
    urls = locs(out)
    indexable = sorted(
        p.relative_to(out).as_posix()
        for p in html_pages(out)
        if 'name="robots" content="noindex"' not in p.read_text(encoding="utf-8")
    )
    assert (out / "404.html").exists() and "404.html" not in indexable  # noindex — 색인하지 않는다
    assert len(urls) == len(indexable) == len(set(urls))
    assert urls[0] == BASE, "홈이 맨 앞"
    for code in ("005930", "105560", "028300"):
        assert f"{BASE}c/{code}/" in urls
    assert f"{BASE}learn/" in urls
    assert all(u.startswith(BASE) for u in urls)
    assert not any(u.endswith("index.html") for u in urls), "디렉터리 주소로 — canonical 과 같게"


async def test_sitemap_matches_each_pages_canonical(out):
    for url in locs(out):
        rel = url[len(BASE) :] + ("index.html" if url.endswith("/") else "")
        assert f'<link rel="canonical" href="{url}">' in (out / rel).read_text(encoding="utf-8")


async def test_lastmod_is_the_build_date(out):
    root = ET.parse(out / "sitemap.xml").getroot()
    mods = {el.text for el in root.findall("s:url/s:lastmod", NS)}
    assert mods == {"2026-11-21"}


async def test_robots_points_to_the_sitemap(out):
    robots = (out / "robots.txt").read_text()
    assert "User-agent: *" in robots and "Allow: /" in robots
    assert f"Sitemap: {BASE}sitemap.xml" in robots


async def test_robots_omits_a_relative_sitemap_line(tmp_path):
    out = await _build(tmp_path, base_url="/", name="rel")
    assert "Sitemap:" not in (out / "robots.txt").read_text()
    assert (out / "sitemap.xml").exists()


async def test_noindex_and_404_pages_stay_out(tmp_path, monkeypatch):
    def extra(site):
        yield Output("404.html", "<html><head></head><body>없음</body></html>")
        yield Output("draft/index.html", '<html><head><meta name="robots" content="noindex"></head></html>')

    monkeypatch.setitem(registry.PAGES, "zz-extra", extra)
    out = await _build(tmp_path, name="extra")
    urls = locs(out)
    assert not any("404" in u or "draft" in u for u in urls)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (Output("c/005930/index.html", "<head></head>"), True),
        (Output("api/c/005930.json", "{}"), False),
        (Output("assets/site.css", b""), False),
        (Output("404.html", "<head></head>"), False),
        (Output("x/index.html", '<head><meta name="robots" content="noindex, follow"></head>'), False),
    ],
)
def test_is_indexable(output, expected):
    assert is_indexable(output) is expected
