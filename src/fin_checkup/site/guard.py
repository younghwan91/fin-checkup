"""빌드된 사이트의 가드 — 넘지 않는 선을 산출물에서 직접 확인한다.

    uv run python -m fin_checkup.cli site check site-out

테스트(`tests/test_site_guard.py`)는 픽스처로 빌드한 사이트에, Pages 워크플로는 실제 빌드에
같은 `check_site` 를 돌린다. 템플릿을 고쳐 선을 넘으면 배포 전에 막힌다.

규칙 (`Violation.rule`)
- `disclaimer`  모든 HTML 에 면책 문구(`DISCLAIMER`)가 그대로 있다.
- `basis`       판정한 지표(🟢🟡🔴)마다 계산식과 경계값이 비어 있지 않게 적혀 있다.
                지표 카드(`.fc-metric`)는 판정과 무관하게 계산식을 적는다.
- `csp`         모든 HTML 에 CSP `<meta>` — default-src 'self', unsafe-eval 없음,
                unsafe-inline 은 style-src 에만, 그것도 인라인 스타일이 실제로 있을 때만.
- `script`      다른 사이트의 스크립트·스타일·이미지를 싣지 않는다. 인라인 실행 스크립트 없음.
- `handler`     인라인 이벤트 핸들러(`onclick=` …)·`javascript:` 주소 없음.
- `storage`     쿠키·sessionStorage·indexedDB 없음. localStorage 는 `fin-checkup:` 키만.
- `secret`      DART 인증키처럼 생긴 40자리 16진수·`crtfc_key` 가 어떤 파일에도 없다.
- `a11y`        `<html lang="ko">` · 본문 건너뛰기 링크(`#main`)
                · 랜드마크(머리 header·nav·main 하나·꼬리 footer)
                · 신호(`sig-*`)는 색만으로 말하지 않는다 — 기호(🟢…)나 글자(정상…)가 함께 있다.
- `banned`      추천·매매 권유·재촉 표현(`explain.banned`)이 빌드된 HTML·템플릿·해설 데이터에 없다.
                예외는 `ALLOWED_SENTENCES` 의 문장 그대로와, 검색 인덱스의 회사 이름(데이터)뿐.
- 에이전트용 글(`llms.txt`·`llms-full.txt`·`*.md`)도 `disclaimer`·`banned` 를 본다 — 에이전트가 그대로
  옮겨 말하는 글이라 HTML 과 같은 선이다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain import banned
from fin_checkup.metrics.signals import Signal

#: 판정한 신호. 이 신호의 지표는 경계값을 반드시 보여야 한다 — 왜 이 색인지 감추지 않는다.
JUDGED_SIGNALS = frozenset({"sig-green", "sig-yellow", "sig-red"})
#: 신호 클래스 → 함께 있어야 하는 기호·글자 (둘 중 하나).
SIGNAL_TEXT = {f"sig-{s.value}": (s.emoji, s.label) for s in Signal}
#: 이 안의 header·footer 는 페이지 머리·꼬리(랜드마크)가 아니다.
_SECTIONING = frozenset({"article", "aside", "main", "nav", "section"})
#: 브라우저 저장소에 쓸 수 있는 키의 접두사 (관심종목 등, 이 기기 안에서만).
STORAGE_PREFIX = "fin-checkup:"
#: 실행되지 않는 데이터 블록. CSP 와 무관하게 허용한다.
DATA_SCRIPT_TYPES = frozenset({"application/ld+json", "application/json"})

_VOID = frozenset("area base br col embed hr img input link meta source track wbr".split())
#: 자원을 불러오는 태그와 그 주소 속성. `<a href>` 는 이동이라 외부도 괜찮다.
_LOADERS = {
    "script": "src",
    "link": "href",
    "img": "src",
    "iframe": "src",
    "source": "src",
    "embed": "src",
    "object": "data",
    "audio": "src",
    "video": "src",
}
#: 아무것도 불러오지 않는 `<link rel>` — 주소를 알려 줄 뿐이다(canonical·피드 등).
#: rel 의 낱말이 전부 여기 있을 때만 통과한다. `alternate stylesheet` 처럼 하나라도 아니면 자원 로드로 본다.
_NAV_LINK_RELS = frozenset(
    {"canonical", "alternate", "author", "license", "help", "next", "prev", "search", "me"}
)
_OFFSITE = re.compile(r"^\s*(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)")
_HEX40 = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])")
_STORAGE_BANNED = re.compile(r"document\s*\.\s*cookie|sessionStorage|indexedDB")
_LOCAL_STORAGE = re.compile(r"localStorage")
_LOCAL_STORAGE_OK = re.compile(
    r"localStorage\.(?:getItem|setItem|removeItem)\(\s*([\"'`])" + re.escape(STORAGE_PREFIX)
)
_TEXT_SUFFIXES = frozenset(
    {".html", ".js", ".css", ".json", ".xml", ".txt", ".svg", ".webmanifest", ".md"}
)
#: 에이전트가 읽고 옮겨 말하는 글. 면책 문구·금지 표현을 HTML 과 똑같이 본다.
AGENT_TEXT_FILES = frozenset({"llms.txt", "llms-full.txt"})
AGENT_TEXT_SUFFIXES = frozenset({".md"})


def is_agent_text(path: str) -> bool:
    return path in AGENT_TEXT_FILES or Path(path).suffix.lower() in AGENT_TEXT_SUFFIXES


@dataclass(frozen=True)
class Violation:
    #: 출력 폴더 기준 POSIX 경로. 사이트 전체에 걸친 규칙은 빈 문자열.
    path: str
    rule: str
    detail: str

    def __str__(self) -> str:
        where = self.path or "(사이트 전체)"
        return f"{where}: [{self.rule}] {self.detail}"


@dataclass
class _Card:
    """`.fc-metric` 카드 하나에서 모은 것."""

    classes: set[str]
    ident: str
    depth: int
    #: (dt 글자, dd 글자) 쌍.
    basis: list[tuple[str, str]] = field(default_factory=list)


class _PageScan(HTMLParser):
    """HTML 한 장에서 가드가 볼 것만 모은다. 템플릿이 태그를 닫는다는 전제(Jinja 산출물)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.csp: list[str] = []
        self.loads: list[tuple[str, str]] = []
        self.inline_scripts = 0
        self.handlers: list[str] = []
        self.js_urls: list[str] = []
        self.inline_styles = 0
        self.cards: list[_Card] = []
        self._open_cards: list[_Card] = []
        self._dt: list[str] | None = None
        self._dd: list[str] | None = None
        self._pending_dt = ""
        self._script_inline = False
        self._script_text: list[str] = []
        self.lang = ""
        self.skip_link = False
        self.mains: list[str] = []
        self.navs = 0
        self.banners = 0
        self.contentinfos = 0
        #: (신호 클래스, 그 요소의 글자) — 닫힐 때 모인다.
        self.signals: list[tuple[str, str]] = []
        self._open_signals: list[tuple[str, int, list[str]]] = []

    # -- 태그 ------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        for name, value in a.items():
            if name.startswith("on"):
                self.handlers.append(f"<{tag} {name}=…>")
            if name in {"href", "src", "action", "formaction", "data"} and value.strip().lower().startswith(
                "javascript:"
            ):
                self.js_urls.append(f"<{tag} {name}={value[:40]}>")
        if "style" in a:
            self.inline_styles += 1
        if tag == "style":
            self.inline_styles += 1
        if tag == "meta" and a.get("http-equiv", "").lower() == "content-security-policy":
            self.csp.append(a.get("content", ""))
        attr = _LOADERS.get(tag)
        rels = set(a.get("rel", "").lower().split())
        if tag == "link" and rels and rels <= _NAV_LINK_RELS:
            attr = None
        if attr and a.get(attr):
            self.loads.append((tag, a[attr]))
        if tag == "script":
            self._script_inline = not a.get("src") and a.get("type", "").lower() not in DATA_SCRIPT_TYPES
            self._script_text = []

        if tag == "html":
            self.lang = a.get("lang", "")
        if tag == "a" and a.get("href") == "#main":
            self.skip_link = True
        if tag == "main":
            self.mains.append(a.get("id", ""))
        if tag == "nav":
            self.navs += 1
        if tag in {"header", "footer"} and not _SECTIONING & set(self.stack):
            if tag == "header":
                self.banners += 1
            else:
                self.contentinfos += 1

        if tag in _VOID:
            return
        self.stack.append(tag)
        classes = set(a.get("class", "").split())
        for cls in classes & SIGNAL_TEXT.keys():
            self._open_signals.append((cls, len(self.stack), []))
        if "fc-metric" in classes:
            card = _Card(classes=classes, ident=a.get("id", ""), depth=len(self.stack))
            self._open_cards.append(card)
        if self._open_cards:
            if tag == "dt":
                self._dt = []
            elif tag == "dd":
                self._dd = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID or tag not in self.stack:
            return
        if tag == "script" and self._script_inline and "".join(self._script_text).strip():
            self.inline_scripts += 1
        if self._open_cards:
            if tag == "dt" and self._dt is not None:
                self._pending_dt = " ".join("".join(self._dt).split())
                self._dt = None
            elif tag == "dd" and self._dd is not None:
                self._open_cards[-1].basis.append((self._pending_dt, " ".join("".join(self._dd).split())))
                self._dd = None
        while self.stack:
            closed = self.stack.pop()
            if self._open_cards and self._open_cards[-1].depth == len(self.stack) + 1:
                self.cards.append(self._open_cards.pop())
            while self._open_signals and self._open_signals[-1][1] == len(self.stack) + 1:
                cls, _depth, text = self._open_signals.pop()
                self.signals.append((cls, "".join(text)))
            if closed == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1] == "script":
            self._script_text.append(data)
        if self._dt is not None:
            self._dt.append(data)
        if self._dd is not None:
            self._dd.append(data)
        for _cls, _depth, text in self._open_signals:
            text.append(data)


# ----------------------------------------------------------------------
# 규칙
# ----------------------------------------------------------------------


def _csp_directives(content: str) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for part in content.split(";"):
        tokens = part.split()
        if tokens:
            directives[tokens[0].lower()] = [t.lower() for t in tokens[1:]]
    return directives


def _check_csp(path: str, scan: _PageScan) -> Iterator[Violation]:
    if len(scan.csp) != 1:
        yield Violation(path, "csp", f"CSP <meta http-equiv> 가 {len(scan.csp)}개 (정확히 하나여야 함)")
        return
    d = _csp_directives(scan.csp[0])
    default = d.get("default-src")
    if default is None or not set(default) <= {"'self'", "'none'"} or not default:
        yield Violation(path, "csp", f"default-src 는 'self' 여야 한다: {default}")
    for name, sources in d.items():
        if "'unsafe-eval'" in sources or "'wasm-unsafe-eval'" in sources:
            yield Violation(path, "csp", f"{name} 에 unsafe-eval")
        if "'unsafe-inline'" in sources and name != "style-src":
            yield Violation(path, "csp", f"{name} 에 unsafe-inline (style-src 에만 허용)")
    script = d.get("script-src", default or [])
    if not set(script) <= {"'self'", "'none'"}:
        yield Violation(path, "csp", f"script-src 는 'self' 만: {script}")
    style = d.get("style-src", default or [])
    if scan.inline_styles and "'unsafe-inline'" not in style:
        yield Violation(path, "csp", f"인라인 스타일 {scan.inline_styles}곳이 CSP 에 막힌다")
    if "'unsafe-inline'" in style and not scan.inline_styles:
        yield Violation(path, "csp", "인라인 스타일이 없는데 style-src 에 unsafe-inline")


def _check_a11y(path: str, scan: _PageScan) -> Iterator[Violation]:
    if scan.lang != "ko":
        yield Violation(path, "a11y", f'<html lang="ko"> 가 아니다: lang="{scan.lang}"')
    if not scan.skip_link:
        yield Violation(path, "a11y", '본문 건너뛰기 링크(<a href="#main">)가 없다')
    if scan.mains != ["main"]:
        yield Violation(path, "a11y", f'<main id="main"> 이 정확히 하나여야 한다: {scan.mains}')
    if not scan.navs:
        yield Violation(path, "a11y", "<nav> 랜드마크가 없다")
    if not scan.banners:
        yield Violation(path, "a11y", "페이지 머리 <header> 랜드마크가 없다")
    if not scan.contentinfos:
        yield Violation(path, "a11y", "페이지 꼬리 <footer> 랜드마크가 없다")
    for cls, text in scan.signals:
        emoji, label = SIGNAL_TEXT[cls]
        if emoji not in text and label not in text:
            yield Violation(
                path, "a11y", f"{cls} 가 색만으로 말한다 — 기호 {emoji} 나 글자 '{label}' 가 없다"
            )


def _check_page(path: str, text: str) -> Iterator[Violation]:
    if DISCLAIMER not in text:
        yield Violation(path, "disclaimer", "면책 문구(DISCLAIMER)가 없다")

    scan = _PageScan()
    scan.feed(text)
    scan.close()

    yield from _check_csp(path, scan)

    for tag, target in scan.loads:
        if _OFFSITE.match(target) and not target.lower().startswith("data:"):
            yield Violation(path, "script", f"<{tag}> 가 다른 사이트 자원을 싣는다: {target}")
    if scan.inline_scripts:
        yield Violation(path, "script", f"인라인 <script> {scan.inline_scripts}개 (assets/*.js 로)")
    for handler in scan.handlers:
        yield Violation(path, "handler", f"인라인 이벤트 핸들러 {handler}")
    for url in scan.js_urls:
        yield Violation(path, "handler", f"javascript: 주소 {url}")

    yield from _check_a11y(path, scan)

    for card in scan.cards:
        label = card.ident or "(id 없음)"
        terms = {dt.split(" ")[0]: dd for dt, dd in card.basis}
        if not terms.get("계산식"):
            yield Violation(path, "basis", f"지표 카드 {label} 에 계산식이 없다")
        if card.classes & JUDGED_SIGNALS and not terms.get("경계값"):
            yield Violation(path, "basis", f"판정한 지표 {label} 에 경계값이 없다")


def _check_storage(path: str, text: str) -> Iterator[Violation]:
    for m in _STORAGE_BANNED.finditer(text):
        yield Violation(path, "storage", f"{m.group(0)} 사용 — 쿠키·세션 저장소는 쓰지 않는다")
    allowed = {m.start() for m in _LOCAL_STORAGE_OK.finditer(text)}
    for m in _LOCAL_STORAGE.finditer(text):
        if m.start() not in allowed:
            snippet = text[m.start() : m.start() + 50].splitlines()[0]
            yield Violation(
                path,
                "storage",
                f'localStorage 는 getItem/setItem/removeItem("{STORAGE_PREFIX}…") 꼴만: {snippet}',
            )


def _check_secrets(path: str, text: str) -> Iterator[Violation]:
    for m in _HEX40.finditer(text):
        yield Violation(path, "secret", f"DART 인증키처럼 생긴 40자리 16진수 {m.group(0)[:6]}…")
    if "crtfc_key" in text:
        yield Violation(path, "secret", "crtfc_key (DART 인증키 파라미터)")


def _check_banned(path: str, text: str, proper_nouns: Iterable[str]) -> Iterator[Violation]:
    for phrase in banned.find_outside_allowed(text, proper_nouns):
        at = text.find(phrase)
        context = " ".join(text[max(0, at - 30) : at + len(phrase) + 30].split())
        yield Violation(path, "banned", f"금지 표현 '{phrase}' — …{context}…")


def check_file(path: str, data: bytes, proper_nouns: Iterable[str] = ()) -> list[Violation]:
    """파일 하나. `path` 는 출력 폴더 기준 경로(확장자로 규칙을 고른다).

    `proper_nouns` 는 금지 표현 검사에서 걷어낼 고유명사(회사 이름) — `check_site` 가 검색 인덱스에서 읽는다.
    """
    suffix = Path(path).suffix.lower()
    # 바이너리(ico·png)도 인증키 검사는 한다 — latin-1 은 어떤 바이트든 글자로 읽는다.
    text = data.decode("utf-8") if suffix in _TEXT_SUFFIXES else data.decode("latin-1")
    found = list(_check_secrets(path, text))
    if suffix == ".html":
        found += _check_page(path, text)
        found += _check_banned(path, text, proper_nouns)
    elif is_agent_text(path):
        if DISCLAIMER not in text:
            found.append(Violation(path, "disclaimer", "면책 문구(DISCLAIMER)가 없다"))
        found += _check_banned(path, text, proper_nouns)
    if suffix in {".html", ".js"}:
        found += _check_storage(path, text)
    return found


TEMPLATES = files("fin_checkup.site") / "templates"
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)


def _walk_templates(node: Traversable, prefix: str) -> Iterator[tuple[str, Traversable]]:
    for child in sorted(node.iterdir(), key=lambda c: c.name):
        if child.is_dir():
            yield from _walk_templates(child, f"{prefix}{child.name}/")
        elif child.name.endswith((".html", ".xml", ".svg", ".txt")):
            yield f"{prefix}{child.name}", child


def check_sources() -> list[Violation]:
    """빌드와 무관하게 소스 쪽 — 사이트 템플릿(Jinja 주석 제외)과 `fin_checkup.explain` 해설 데이터."""
    found: list[Violation] = []
    for rel, resource in _walk_templates(TEMPLATES, "templates/"):
        text = _JINJA_COMMENT.sub(" ", resource.read_text(encoding="utf-8"))
        found += _check_banned(rel, text, ())
    for where, text in banned.explain_texts():
        found += _check_banned(where, text, ())
    return found


def _proper_nouns(out: Path) -> list[str]:
    """검색 인덱스의 회사 이름. 인덱스가 없거나 깨졌으면 빈 목록(그만큼 엄격해질 뿐)."""
    try:
        payload = json.loads((out / "search-index.json").read_text(encoding="utf-8"))
        return [c["name"] for c in payload["companies"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def check_site(out: Path, sources: bool = True) -> list[Violation]:
    """출력 폴더 전체(+ 기본으로 템플릿·해설 데이터). 위반이 없으면 빈 목록."""
    violations = check_sources() if sources else []
    if not out.is_dir():
        return [*violations, Violation("", "site", f"출력 폴더가 없다: {out}")]
    nouns = _proper_nouns(out)
    pages = 0
    for file in sorted(p for p in out.rglob("*") if p.is_file()):
        rel = file.relative_to(out).as_posix()
        pages += rel.endswith(".html")
        violations += check_file(rel, file.read_bytes(), nouns)
    if not pages:
        violations.append(Violation("", "site", "HTML 페이지가 하나도 없다 — 빈 빌드는 통과시키지 않는다"))
    return violations


def check_coverage(out: Path, min_companies: int) -> list[Violation]:
    """회사 페이지가 `min_companies` 곳보다 적으면 위반 — 거의 빈 사이트를 배포하지 않게.

    `--year` 가 데이터에 없는 사업연도를 가리키면(예: 2025 를 아직 수집하지 않았는데 기본값 2025 로
    빌드) 빌드는 성공하고 다른 가드도 모두 통과한 채 회사 몇 곳짜리 사이트가 나간다. 가장 최근 해가
    그 몇 곳 덕에 2025 로 보여 신선도 경고도 뜨지 않는다. 0 이면 검사하지 않는다(픽스처 빌드용).
    """
    if min_companies <= 0 or not out.is_dir():
        return []
    count = sum(1 for p in (out / "c").glob("*/index.html")) if (out / "c").is_dir() else 0
    if count >= min_companies:
        return []
    return [
        Violation(
            "c/",
            "coverage",
            f"회사 페이지가 {count:,}곳뿐이다 (최소 {min_companies:,}곳)"
            " — --year 가 데이터에 없는 해인지 확인",
        )
    ]
