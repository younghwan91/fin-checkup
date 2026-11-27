"""검색 엔진 소유 확인 파일을 사이트 루트에 싣는다 — 규칙은 `site/verification.py`."""

from __future__ import annotations

from collections.abc import Iterator

from fin_checkup.site.context import Site
from fin_checkup.site.registry import Output, page
from fin_checkup.site.verification import FILES


@page("search-verification")
def search_verification(site: Site) -> Iterator[Output]:
    for path, body in FILES.items():
        yield Output(path, body)
