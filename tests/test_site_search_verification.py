"""검색 엔진 소유 확인 파일 — Google Search Console 의 HTML 파일 인증.

Search Console 은 URL 접두어 속성(`https://younghwan91.github.io/fin-checkup/`)의 주인을 그 경로 아래
`google<토큰>.html` 이 정해진 한 줄을 돌려주는지로 확인한다. 파일이 사라지면 인증이 풀리니 빌드마다 싣는다.
색인할 페이지가 아니다 — sitemap·IndexNow 에 들어가면 안 되고,
SEO 가드의 페이지 규칙(제목·canonical)도 받지 않는다.
"""

from __future__ import annotations

from datetime import date

import pytest
from hub_seed import seed_site_cache

from fin_checkup.site import indexnow
from fin_checkup.site.build import build_site
from fin_checkup.site.guard import check_site
from fin_checkup.site.seo import check_seo
from fin_checkup.site.verification import GOOGLE_BODY, GOOGLE_FILE

BASE = "https://example.github.io/fin-checkup/"


@pytest.fixture
async def out(tmp_path):
    from fin_checkup.storage import Cache

    with Cache(tmp_path / "v.duckdb") as cache:
        seed_site_cache(cache)
        await build_site(cache, tmp_path / "out", 2024, base_url=BASE, built_on=date(2026, 11, 27))
    return tmp_path / "out"


def test_google_file_is_the_exact_line_search_console_asks_for() -> None:
    assert GOOGLE_FILE == "google88933ce58dda7616.html"
    assert GOOGLE_BODY == "google-site-verification: google88933ce58dda7616.html"


async def test_build_serves_it_at_the_site_root_and_keeps_it_out_of_the_index(out) -> None:
    assert (out / GOOGLE_FILE).read_text("utf-8") == GOOGLE_BODY
    assert GOOGLE_FILE not in (out / "sitemap.xml").read_text("utf-8")
    assert GOOGLE_FILE not in indexnow.site_digests(out)
    indexnow.write_digests(out)
    assert [v for v in check_site(out) if v.rule != "coverage"] == []
    assert check_seo(out) == []


def test_guard_rejects_an_altered_verification_file() -> None:
    from fin_checkup.site.guard import check_file

    assert check_file(GOOGLE_FILE, GOOGLE_BODY.encode()) == []
    assert [v.rule for v in check_file(GOOGLE_FILE, (GOOGLE_BODY + "\n").encode())] == ["verification"]
