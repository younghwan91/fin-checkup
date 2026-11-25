"""검색 노출 가드 — 빌드된 사이트에서 검색 엔진이 볼 것이 빠지지 않았는지 확인한다.

    uv run python -m fin_checkup.cli site check site-out     # guard.check_site 와 함께 돈다

회사 이름으로 들어오는 검색 유입이 이 사이트의 길이라, 메타가 빠진 페이지나 sitemap 에 없는 회사
페이지는 배포 전에 막는다. 규칙(`Violation.rule == "seo"`):

- 색인 페이지(`404.html`·`noindex` 제외)마다 `<title>`·description·og:title·og:description·
  twitter:card·twitter:title 이 비어 있지 않고, 제목은 페이지끼리 겹치지 않는다.
- `sitemap.xml`·`robots.txt` 가 있다. sitemap 은 색인 페이지를 전부 싣는다 — 회사 페이지(`c/<코드>/`)도
  하나도 빠지지 않는다.
- 절대주소 빌드(robots.txt 에 `Sitemap:` 이 있음)라면 — canonical 이 그 페이지 주소와 같고, JSON-LD
  (`WebPage`, url = canonical)가 읽히며, 회사 페이지의 JSON-LD 는 회사(`about.name`)를 담는다.
  공유 카드 PNG(`og/<코드>.png`)가 빌드됐으면 그 회사 페이지는 og:image·twitter:image 로 가리킨다.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from fin_checkup.site.context import page_url
from fin_checkup.site.guard import Violation
from fin_checkup.site.sitemap import ROBOTS, SITEMAP

RULE = "seo"
REQUIRED_META = (
    ("name", "description"),
    ("property", "og:title"),
    ("property", "og:description"),
    ("name", "twitter:card"),
    ("name", "twitter:title"),
)
_COMPANY_PAGE = re.compile(r"^c/[0-9A-Z]{6}/index\.html$")
_LOC = re.compile(r"<loc>([^<]+)</loc>")
_SITEMAP_LINE = re.compile(r"^Sitemap:\s*(\S+)\s*$", re.MULTILINE)


class _HeadScan(HTMLParser):
    """`<head>` 에서 제목·메타·canonical·JSON-LD 만 모은다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.meta: dict[tuple[str, str], str] = {}
        self.canonical: str | None = None
        self.noindex = False
        self.json_ld: list[str] = []
        self._in: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in, self._buf = "title", []
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in, self._buf = "ld", []
        elif tag == "meta":
            for kind in ("name", "property"):
                if kind in a:
                    self.meta[(kind, a[kind].lower())] = a.get("content", "")
            if a.get("name", "").lower() == "robots" and "noindex" in a.get("content", "").lower():
                self.noindex = True
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonical = a.get("href", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in == "title":
            self.title = " ".join("".join(self._buf).split())
            self._in = None
        elif tag == "script" and self._in == "ld":
            self.json_ld.append("".join(self._buf))
            self._in = None

    def handle_data(self, data: str) -> None:
        if self._in:
            self._buf.append(data)


def _scan(text: str) -> _HeadScan:
    scan = _HeadScan()
    scan.feed(text.split("</head>", 1)[0])
    scan.close()
    return scan


def _check_json_ld(rel: str, scan: _HeadScan, url: str) -> list[Violation]:
    if len(scan.json_ld) != 1:
        return [Violation(rel, RULE, f"JSON-LD 가 {len(scan.json_ld)}개 (정확히 하나)")]
    try:
        data = json.loads(scan.json_ld[0])
    except ValueError as exc:
        return [Violation(rel, RULE, f"JSON-LD 가 JSON 으로 읽히지 않는다: {exc}")]
    found = []
    if data.get("@type") != "WebPage" or data.get("url") != url:
        found.append(Violation(rel, RULE, f"JSON-LD 가 이 페이지의 WebPage 가 아니다: {data.get('url')}"))
    if _COMPANY_PAGE.match(rel) and not (data.get("about") or {}).get("name"):
        found.append(Violation(rel, RULE, "회사 페이지 JSON-LD 에 회사(about.name)가 없다"))
    return found


def check_seo(out: Path) -> list[Violation]:
    """출력 폴더의 검색 노출 요소. 위반이 없으면 빈 목록."""
    if not out.is_dir():
        return [Violation("", RULE, f"출력 폴더가 없다: {out}")]
    found: list[Violation] = []
    robots = out / ROBOTS
    sitemap = out / SITEMAP
    for f in (robots, sitemap):
        if not f.is_file():
            found.append(Violation(f.name, RULE, f"{f.name} 가 없다"))
    robots_text = robots.read_text(encoding="utf-8") if robots.is_file() else ""
    declared = _SITEMAP_LINE.search(robots_text)
    base = declared.group(1).removesuffix(SITEMAP) if declared else None
    if declared and not declared.group(1).endswith("/" + SITEMAP):
        detail = f"Sitemap: 이 {SITEMAP} 를 가리키지 않는다: {declared.group(1)}"
        found.append(Violation(ROBOTS, RULE, detail))
    locs = set(_LOC.findall(sitemap.read_text(encoding="utf-8"))) if sitemap.is_file() else set()
    # 상대 base 빌드의 sitemap 주소는 `/c/…` — 비교는 사이트 안 경로로 한다.
    listed = {loc.removeprefix(base) if base else loc.lstrip("/") for loc in locs}

    titles: Counter[str] = Counter()
    title_paths: dict[str, str] = {}
    for file in sorted(out.rglob("*.html")):
        rel = file.relative_to(out).as_posix()
        if rel == "404.html":
            continue
        scan = _scan(file.read_text(encoding="utf-8"))
        if scan.noindex:
            continue
        if not scan.title:
            found.append(Violation(rel, RULE, "<title> 이 비어 있다"))
        else:
            titles[scan.title] += 1
            title_paths.setdefault(scan.title, rel)
        for key in REQUIRED_META:
            if not scan.meta.get(key, "").strip():
                found.append(Violation(rel, RULE, f'<meta {key[0]}="{key[1]}"> 가 없다'))
        url_path = page_url(rel)
        if sitemap.is_file() and url_path not in listed:
            found.append(Violation(rel, RULE, f"{SITEMAP} 에 없다"))
        if base:
            url = base + url_path
            if scan.canonical != url:
                found.append(Violation(rel, RULE, f"canonical 이 {url} 가 아니다: {scan.canonical}"))
            found += _check_json_ld(rel, scan, url)
            code = rel.split("/")[1] if _COMPANY_PAGE.match(rel) else None
            if code and (out / "og" / f"{code}.png").is_file():
                image = base + f"og/{code}.png"
                for key in (("property", "og:image"), ("name", "twitter:image")):
                    if scan.meta.get(key) != image:
                        found.append(Violation(rel, RULE, f"{key[1]} 가 공유 카드 {image} 가 아니다"))
    for title, n in titles.items():
        if n > 1:
            found.append(Violation(title_paths[title], RULE, f"제목이 다른 페이지 {n - 1}곳과 같다: {title}"))
    return found
