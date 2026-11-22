"""`sitemap.xml` · `robots.txt` — 검색 엔진이 회사 페이지를 빠짐없이 찾게.

다른 페이지가 전부 나온 뒤에 만들어야 해서 `@page` 레지스트리가 아니라 `build.py` 가 마지막에 부른다.
들어가는 건 빌드가 쓴 HTML 중 색인해도 되는 것 — `404.html` 과 `<meta name="robots" content="noindex">`
페이지는 뺀다. 주소는 `site.absolute_url()` (`--base-url`), lastmod 는 빌드 날짜(KST).

robots.txt 는 도메인 루트에 있을 때만 읽힌다. Pages 프로젝트 사이트(`/fin-checkup/`)라면 크롤러는 이
파일을 보지 않으니 sitemap 주소를 서치 콘솔에 직접 제출한다 (`docs/OPERATIONS.md` §7).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from xml.sax.saxutils import escape

from fin_checkup.site.context import Site, page_url
from fin_checkup.site.registry import Output

SITEMAP = "sitemap.xml"
ROBOTS = "robots.txt"
#: sitemap 프로토콜의 파일당 상한. 넘으면 sitemap index 로 나눠야 한다 — 조용히 자르지 않고 멈춘다.
MAX_URLS = 50_000

_NOINDEX = re.compile(r"<meta\s+name=\"robots\"\s+content=\"[^\"]*noindex", re.IGNORECASE)
_NOT_INDEXED = frozenset({"404.html"})


def is_indexable(output: Output) -> bool:
    """색인할 HTML 페이지인가. JSON·CSS·이미지는 아니다."""
    if not output.path.endswith(".html") or output.path in _NOT_INDEXED:
        return False
    content = output.content
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return _NOINDEX.search(content.split("</head>", 1)[0]) is None


def sitemap_xml(site: Site, paths: Iterable[str]) -> str:
    urls = sorted({page_url(p) for p in paths}, key=lambda u: (u != "", u))
    if len(urls) > MAX_URLS:
        raise ValueError(
            f"sitemap URL 이 {len(urls):,}개 — 상한 {MAX_URLS:,} 을 넘으면 sitemap index 로 나눠야 한다"
        )
    lastmod = site.built_on.isoformat()
    rows = "".join(
        f"<url><loc>{escape(site.absolute_url(u))}</loc><lastmod>{lastmod}</lastmod></url>\n" for u in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{rows}</urlset>\n"
    )


#: AI 에이전트용 안내 (`pages/llms.py`). robots.txt 에는 표준 지시어가 없어 주석으로 알린다.
AGENT_GUIDES = ("llms.txt", "llms-full.txt")


def robots_txt(site: Site) -> str:
    lines = ["User-agent: *", "Allow: /"]
    guides = [site.absolute_url(g) if site.has_absolute_base else g for g in AGENT_GUIDES]
    lines += ["", "# AI agents: read these first (how to use this site, rules for answers)"]
    lines += [f"# {g}" for g in guides]
    # Sitemap: 은 절대주소여야 한다. 상대 base 로 만든 빌드에는 넣지 않는다.
    if site.has_absolute_base:
        lines += ["", f"Sitemap: {site.absolute_url(SITEMAP)}"]
    return "\n".join(lines) + "\n"


def crawl_outputs(site: Site, indexable_paths: Iterable[str]) -> Iterator[Output]:
    yield Output(SITEMAP, sitemap_xml(site, indexable_paths))
    yield Output(ROBOTS, robots_txt(site))
