"""빌드된 사이트의 링크 검사 — 내부 링크가 실제 파일·앵커로 이어지는지.

    uv run python -m fin_checkup.cli site links site-out --base-url https://younghwan91.github.io/fin-checkup/

HTML 파일을 전부 읽어 `href`·`src` 를, 에이전트용 글(`llms.txt`·`*.md`, `guard.is_agent_text`)은
마크다운 링크 `[글](주소)` 를 모은다. 사이트 안을 가리키는 주소(상대경로, 그리고 `--base-url`
아래의 절대주소)는 출력 폴더의 파일로 풀어 없으면 **깨진 링크**다. `#앵커` 가 붙어 있으면 그 페이지에 같은
`id` 가 있어야 한다. 다른 사이트 주소는 모아서 보고만 한다 — 네트워크를 부르지 않고, 남의 사이트 장애로
배포를 막지 않는다.

Pages 워크플로가 빌드 뒤에 돌리고(깨진 내부 링크가 있으면 배포하지 않는다), 테스트는 픽스처 빌드에 돌린다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from fin_checkup.site.guard import is_agent_text

#: 주소를 담는 속성. `srcset` 은 쉼표로 여러 개.
_URL_ATTRS = frozenset({"href", "src", "poster", "data"})
#: 마크다운 링크 `[글](주소)` 의 주소. 주소에 공백·괄호는 없다고 본다(우리가 만드는 글).
_MD_LINK = re.compile(r"\]\(([^)\s]+)\)")
#: 이동하지 않는 스킴 — 검사하지 않는다.
_IGNORED_SCHEMES = frozenset({"mailto", "tel", "data", "javascript", "blob", "about"})


@dataclass(frozen=True)
class BrokenLink:
    #: 링크가 있는 파일 (출력 폴더 기준 POSIX 경로).
    source: str
    url: str
    reason: str

    def __str__(self) -> str:
        return f"{self.source}: {self.url} — {self.reason}"


@dataclass
class LinkReport:
    pages: int = 0
    internal: int = 0
    broken: list[BrokenLink] = field(default_factory=list)
    #: 다른 사이트 주소 → 그 주소가 나온 파일 수.
    external: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.broken


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []
        self.ids: set[str] = set()
        self.base: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "a" and a.get("name"):
            self.ids.add(a["name"])
        if tag == "base" and a.get("href"):
            self.base = a["href"]
            return
        # canonical·og:url 은 같은 페이지의 절대주소다 — 링크와 똑같이 검사한다.
        if tag == "meta" and a.get("property", "").lower() in {"og:url", "og:image"} and a.get("content"):
            self.urls.append(a["content"])
        for name, value in a.items():
            if name in _URL_ATTRS and value.strip():
                self.urls.append(value.strip())
            elif name == "srcset":
                self.urls.extend(part.split()[0] for part in value.split(",") if part.strip())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def _parse(path: Path) -> _Collector:
    collector = _Collector()
    collector.feed(path.read_text(encoding="utf-8", errors="replace"))
    collector.close()
    return collector


def _page_url(rel: str) -> str:
    """출력 파일 경로 → 사이트 루트 기준 URL 경로 (`c/005930/index.html` → `/c/005930/`)."""
    url = "/" + rel
    if url.endswith("/index.html"):
        url = url[: -len("index.html")]
    return url


def _site_path(base_url: str) -> tuple[str, str]:
    """(스킴+호스트, 경로 접두사). 상대 base_url(`/`, `/fin-checkup/`)이면 호스트가 비어 있다."""
    parts = urlsplit(base_url or "/")
    origin = f"{parts.scheme}://{parts.netloc}".lower() if parts.netloc else ""
    prefix = parts.path or "/"
    if not prefix.endswith("/"):
        prefix += "/"
    return origin, prefix


#: 상대주소를 풀 때 쓰는 가짜 사이트 루트. 풀린 주소가 이 아래가 아니면 출력 폴더 밖이다.
_ROOT = "http://site.invalid/__root__/"


def _to_root(url: str, origin: str, prefix: str) -> str | None:
    """사이트 안을 가리키는 절대·루트 기준 주소를 가짜 루트 기준으로 바꾼다. 사이트 밖이면 None."""
    parts = urlsplit(url)
    if (parts.scheme or url.startswith("//")) and (
        not origin or f"{parts.scheme or 'https'}://{parts.netloc}".lower() != origin
    ):
        return None
    path = parts.path or "/"
    if not (path + ("" if path.endswith("/") else "/")).startswith(prefix) and path != prefix.rstrip("/"):
        return None
    rest = path[len(prefix) :] if path.startswith(prefix) else ""
    tail = (f"?{parts.query}" if parts.query else "") + (f"#{parts.fragment}" if parts.fragment else "")
    return _ROOT + rest + tail


def _target_file(out: Path, site_path: str) -> Path | None:
    """사이트 루트 기준 경로(`c/005930/`) → 출력 폴더의 파일. 없으면 None."""
    candidate = out / site_path
    if site_path == "" or site_path.endswith("/") or candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate if candidate.is_file() else None


def check_links(out: Path, base_url: str = "/") -> LinkReport:
    """출력 폴더의 모든 HTML 의 링크를 검사한다.

    base_url 은 `site build --base-url` 과 같은 값. 절대주소면 그 호스트·경로 아래 주소를 사이트 안으로 보고,
    루트 기준 주소(`/x`)는 그 경로 접두사(Pages 프로젝트 사이트면 `/fin-checkup/`) 아래여야 한다.
    """
    out = Path(out)
    report = LinkReport()
    origin, prefix = _site_path(base_url)
    html_files = sorted(p for p in out.rglob("*.html") if p.is_file())
    report.pages = len(html_files)

    parsed: dict[Path, _Collector] = {}

    def collector_for(path: Path) -> _Collector:
        if path not in parsed:
            parsed[path] = _parse(path)
        return parsed[path]

    sources: list[tuple[str, str, list[str]]] = []
    for page in html_files:
        rel = page.relative_to(out).as_posix()
        collector = collector_for(page)
        page_url = _ROOT + _page_url(rel).lstrip("/")
        if collector.base:
            page_url = urljoin(page_url, _to_root(collector.base, origin, prefix) or collector.base)
        sources.append((rel, page_url, collector.urls))
    for text_file in sorted(p for p in out.rglob("*") if p.is_file()):
        rel = text_file.relative_to(out).as_posix()
        if is_agent_text(rel):
            urls = _MD_LINK.findall(text_file.read_text(encoding="utf-8", errors="replace"))
            sources.append((rel, _ROOT + rel, urls))

    external_seen: dict[str, set[str]] = {}
    for rel, page_url, urls in sources:
        for url in urls:
            parts = urlsplit(url)
            scheme = parts.scheme.lower()
            if scheme in _IGNORED_SCHEMES:
                continue
            if scheme or url.startswith("/"):
                rooted = _to_root(url, origin, prefix)
                if rooted is None:
                    if scheme in {"http", "https"} or url.startswith("//"):
                        external_seen.setdefault(url, set()).add(rel)
                    elif not scheme:
                        report.internal += 1
                        report.broken.append(
                            BrokenLink(rel, url, f"사이트 경로({prefix}) 밖의 루트 기준 주소")
                        )
                    continue
                resolved = rooted
            else:
                resolved = urljoin(page_url, url)
            report.internal += 1
            target_parts = urlsplit(resolved)
            if not resolved.startswith(_ROOT):
                report.broken.append(BrokenLink(rel, url, "출력 폴더 밖을 가리킨다"))
                continue
            site_path = unquote(target_parts.path[len(urlsplit(_ROOT).path) :])
            target = _target_file(out, site_path)
            if target is None:
                report.broken.append(BrokenLink(rel, url, "파일이 없다"))
                continue
            fragment = unquote(target_parts.fragment)
            if fragment and target.suffix == ".html" and fragment not in collector_for(target).ids:
                report.broken.append(BrokenLink(rel, url, f"앵커 #{fragment} 가 없다"))

    report.external = {url: len(sources) for url, sources in sorted(external_seen.items())}
    return report


def format_report(report: LinkReport, limit: int = 50) -> Iterable[str]:
    for broken in report.broken[:limit]:
        yield str(broken)
    if len(report.broken) > limit:
        yield f"… 깨진 링크 {len(report.broken) - limit:,}건 더"
    if report.external:
        yield f"외부 링크 {len(report.external):,}종 (검사하지 않고 보고만 한다):"
        for url, count in list(report.external.items())[:limit]:
            yield f"  {url} ({count:,}개 파일)"
        if len(report.external) > limit:
            yield f"  … {len(report.external) - limit:,}종 더"
