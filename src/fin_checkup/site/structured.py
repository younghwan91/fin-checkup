"""검색 엔진용 구조화 데이터(JSON-LD, schema.org) — `<head>` 의 `<script type="application/ld+json">`.

모든 색인 페이지는 `WebPage`(이름·설명·주소·언어, `isPartOf` 사이트)를 낸다. 회사 페이지는
`@company_context("structured_data")`(`pages/company.py`)가 회사(`about`)·이동 경로(`breadcrumb`)·
공유 카드(`primaryImageOfPage`)를 보탠다. 값은 화면에 이미 있는 것만 — 점수·평점(`aggregateRating`)처럼
판단으로 읽히는 속성은 넣지 않는다 (`docs/REGULATORY.md`).

절대주소가 없는 빌드(`--base-url /`)에서는 내지 않는다 — 상대 주소의 JSON-LD 는 틀린 정보다.
실행되지 않는 데이터 블록이라 CSP `script-src 'self'` 와 무관하다(가드 `DATA_SCRIPT_TYPES`).
"""

from __future__ import annotations

import json
from typing import Any

from markupsafe import Markup

SITE_NAME = "fin-checkup"
LANGUAGE = "ko-KR"

# `</script>` 로 블록이 닫히거나 HTML 로 읽히지 않게. JSON 문자열 안에서는 같은 글자다.
_ESCAPES = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026"}


def dumps(data: dict[str, Any]) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for raw, escaped in _ESCAPES.items():
        text = text.replace(raw, escaped)
    return text


def web_page(
    url: str, site_url: str, title: str, description: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "@id": url,
        "url": url,
        "name": title,
        "description": description,
        "inLanguage": LANGUAGE,
        "isPartOf": {"@type": "WebSite", "name": SITE_NAME, "url": site_url, "inLanguage": LANGUAGE},
    }
    for key, value in (extra or {}).items():
        if key in data:
            raise ValueError(f"구조화 데이터 '{key}' 를 덮어쓰려 한다")
        if value is not None:
            data[key] = value
    return data


def script_tag(data: dict[str, Any]) -> Markup:
    return Markup(f'<script type="application/ld+json">{dumps(data)}</script>')


def breadcrumb(items: list[tuple[str, str]]) -> dict[str, Any]:
    """(이름, 절대주소) 차례대로 — 사이트 첫 화면부터 이 페이지까지."""
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": url}
            for i, (name, url) in enumerate(items, start=1)
        ],
    }
