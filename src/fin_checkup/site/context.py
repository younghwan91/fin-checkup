"""빌드 한 번이 공유하는 것 — 데이터 기준 연도, 회사 목록, 템플릿 렌더.

링크는 전부 **상대경로**다. 페이지 깊이만큼 `../` 를 붙인 `root` 를 템플릿에 넘기고,
템플릿은 `url('c/005930/')` 로 링크를 만든다. 그래서 GitHub Pages 프로젝트 사이트
(`/fin-checkup/`)든 도메인 루트든 로컬 `python -m http.server` 든 같은 산출물이 동작한다.

`base_url` 은 절대주소가 꼭 필요한 곳(canonical·sitemap·OG)만 쓴다 — `site.absolute_url(path)`.
기본값 `/` 처럼 절대주소가 아니면 canonical·og:url·og:image 를 아예 내지 않는다
(`/c/005930/` 은 Pages 하위 경로 `/fin-checkup/` 에서 틀린 주소다) — `site.canonical_url()` 이 None.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import cached_property
from posixpath import normpath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from jinja2 import Environment

from fin_checkup.models import CorpCode
from fin_checkup.service import Checkup
from fin_checkup.site.registry import Output
from fin_checkup.site.season import SeasonView, season_view

if TYPE_CHECKING:
    from fin_checkup.site.buildinfo import SnapshotInfo
    from fin_checkup.site.disclosures import DisclosureFeed
    from fin_checkup.site.incremental import BuildCache


@dataclass(frozen=True)
class CompanyEntry:
    """사이트에 페이지가 생기는 회사 하나."""

    corp: CorpCode
    checkup: Checkup

    @property
    def stock_code(self) -> str:
        return self.corp.stock_code

    @property
    def name(self) -> str:
        return self.corp.corp_name

    @property
    def path(self) -> str:
        """회사 페이지의 사이트 내 경로(디렉터리). 링크는 `url(entry.path)`."""
        return f"c/{self.stock_code}/"


#: 공유 카드 이미지(`og/<종목코드>.png`)를 만드는 페이지 이름. 이 이름의 `@page` 가 등록돼 있을 때만
#: 회사 페이지가 og:image 를 낸다 — 파일이 없는 주소를 카드로 내보내지 않게.
OG_IMAGE_PAGE = "og-image"


def page_url(output_path: str) -> str:
    """출력 경로 → 사이트 내 주소. `c/005930/index.html` → `c/005930/`, `index.html` → ``."""
    if output_path == "index.html":
        return ""
    if output_path.endswith("/index.html"):
        return output_path[: -len("index.html")]
    return output_path


def root_for(output_path: str) -> str:
    """출력 경로에서 사이트 루트로 가는 상대경로. `c/005930/index.html` → `../../`."""
    depth = output_path.count("/")
    return "./" if depth == 0 else "../" * depth


def safe_output_path(path: str) -> str:
    """출력 폴더 밖으로 나가는 경로를 거부한다. 종목코드는 DB 에서 오는 값이다."""
    normalized = normpath(path)
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or normalized.startswith("..")
        or normalized != path.rstrip("/")
    ):
        raise ValueError(f"허용하지 않는 출력 경로: {path!r}")
    return normalized


@dataclass
class Site:
    year: int
    built_on: date
    env: Environment
    companies: list[CompanyEntry] = field(default_factory=list)
    #: 절대주소용. 끝은 항상 '/'. 예: https://younghwan91.github.io/fin-checkup/
    base_url: str = "/"
    #: 공유 카드 이미지가 이번 빌드에 생기는지. build 가 `OG_IMAGE_PAGE` 가 등록돼 있고
    #: PNG 를 만들 수 있을 때(--images, 한국어 글꼴 있음)만 켠다.
    og_images: bool = False
    #: PNG 에 쓸 한국어 글꼴 (`images.fonts.FontSet`). None 이면 PNG 를 만들지 않는다.
    fonts: Any = None
    #: PNG 를 나눠 그릴 프로세스 수.
    image_workers: int = 1
    #: `cli disclosures fetch` 가 쓴 최근 위험 공시 (`site/disclosures.py` 가 검증). 없으면 None —
    #: 회사 페이지·첫 화면 공시 섹션은 생기지 않고, 피드는 공시 없이 만든다.
    disclosures: DisclosureFeed | None = None
    #: 스냅샷 manifest (`site build --snapshot`). 꼬리의 데이터 기준 줄에 날짜를 적는다.
    snapshot: SnapshotInfo | None = None
    #: 증분 빌드 캐시 (`site build --incremental`). 비싼 산출물(공유 카드 PNG)이 내용 열쇠로 재사용한다.
    build_cache: BuildCache | None = None

    def __post_init__(self) -> None:
        if not self.base_url.endswith("/"):
            self.base_url += "/"

    @cached_property
    def season(self) -> SeasonView:
        """데이터 기준일·다가오는 정기 공시 기한 (`templates/_season.html`)."""
        return season_view(self.year, self.built_on)

    @cached_property
    def company_paths(self) -> frozenset[str]:
        """이번 빌드에 페이지가 생기는 회사 경로(`c/005930/`). 링크를 걸어도 되는지 볼 때."""
        return frozenset(e.path for e in self.companies)

    def absolute_url(self, path: str = "") -> str:
        return self.base_url + path.lstrip("/")

    @property
    def root_path(self) -> str:
        """사이트 루트의 경로 부분 — `https://x.github.io/fin-checkup/` → `/fin-checkup/`.

        상대 링크가 안 통하는 페이지(어느 깊이의 주소로든 서빙되는 `404.html`)만 쓴다.
        """
        path = urlsplit(self.base_url).path or "/"
        return path if path.endswith("/") else path + "/"

    @property
    def has_absolute_base(self) -> bool:
        return self.base_url.startswith(("https://", "http://"))

    def canonical_url(self, output_path: str) -> str | None:
        """그 페이지의 절대주소. base_url 이 절대주소가 아니면 None (태그를 내지 않는다)."""
        # 404.html 은 없는 주소 어디서든 서빙된다 — 그 페이지의 '정식 주소' 는 없다.
        if not self.has_absolute_base or output_path == "404.html":
            return None
        return self.absolute_url(page_url(output_path))

    def og_image_url(self, stock_code: str) -> str | None:
        """회사 공유 카드 이미지의 절대주소. 이미지가 안 생기는 빌드면 None."""
        if not (self.og_images and self.has_absolute_base):
            return None
        return self.absolute_url(f"og/{stock_code}.png")

    def json_ld(
        self, output_path: str, title: str, description: str, extra: dict[str, Any] | None = None
    ) -> str:
        """그 페이지의 JSON-LD `<script>` (`site/structured.py`). 절대주소가 없으면(404 포함) 빈 문자열."""
        url = self.canonical_url(output_path)
        if url is None:
            return ""
        from fin_checkup.site import structured

        data = structured.web_page(url, self.absolute_url(), title, description, extra)
        return structured.script_tag(data)

    def render(self, template: str, output_path: str, **context: Any) -> Output:
        """템플릿 하나를 출력 경로에 렌더한다. root·site 는 자동으로 들어간다.

        `root=` 를 넘기면 상대경로 대신 그 값을 링크 기준으로 쓴다 (`404.html` 의 `site.root_path`).
        """
        path = safe_output_path(output_path)
        root = context.pop("root", None) or root_for(path)
        html = self.env.get_template(template).render(
            site=self, root=root, page_path=path, **context
        )
        return Output(path, html)
