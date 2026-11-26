"""검색 엔진용 구조화 데이터(JSON-LD, schema.org) — `<head>` 의 `<script type="application/ld+json">`.

모든 색인 페이지는 `WebPage`(이름·설명·주소·언어, `isPartOf` 사이트)를 낸다. 회사 페이지는
`@company_context("structured_data")`(`pages/company.py`)가 회사(`about`)·이동 경로(`breadcrumb`)·
공유 카드(`primaryImageOfPage`)를 보탠다. 값은 화면에 이미 있는 것만 — 점수·평점(`aggregateRating`)처럼
판단으로 읽히는 속성은 넣지 않는다 (`docs/REGULATORY.md`).
정적 JSON 을 안내하는 `/agents/` 는 `mainEntity` 로 `Dataset`(`dataset()`)을 보탠다
— Google 데이터세트 검색용.

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


#: 원천 데이터의 이용 조건. OpenDART 이용약관에는 재배포를 허락·금지하는 명시 문구가 없다
#: (`docs/REGULATORY.md`) — 그래서 데이터를 MIT 라고 적지 않는다.
#: MIT 는 그것을 만드는 코드(`creator` 저장소)의 라이선스다.
DART_TERMS = "https://opendart.fss.or.kr/intro/terms.do"
DART_HOME = "https://dart.fss.or.kr/"


def dataset(
    *,
    page_url: str,
    site_url: str,
    repo_url: str,
    years: list[int],
    built_on: str,
    company_count: int,
    sample_code: str | None,
    variables: list[str],
) -> dict[str, Any]:
    """정적 JSON(`api/index.json`·`api/c/<코드>.json`)을 schema.org `Dataset` 으로. 값은 빌드에 있는 것만."""
    span = f"{min(years)}/{max(years)}" if len(set(years)) > 1 else (str(years[0]) if years else None)
    downloads = [
        {
            "@type": "DataDownload",
            "name": "회사 목록 (api/index.json)",
            "encodingFormat": "application/json",
            "contentUrl": f"{site_url}api/index.json",
        }
    ]
    if sample_code:
        downloads.append(
            {
                "@type": "DataDownload",
                "name": f"회사 하나 — api/c/<종목코드>.json (예: {sample_code})",
                "description": (
                    "회사마다 같은 모양의 파일이 하나씩 있습니다. 종목코드는 api/index.json 에 있습니다."
                ),
                "encodingFormat": "application/json",
                "contentUrl": f"{site_url}api/c/{sample_code}.json",
            }
        )
    data: dict[str, Any] = {
        "@type": "Dataset",
        "@id": f"{page_url}#dataset",
        "name": "fin-checkup 상장사 재무 지표 (정적 JSON)",
        "description": (
            f"국내 상장사 {company_count:,}곳의 사업보고서 재무제표(금융감독원 전자공시시스템 DART)로 "
            f"계산한 재무 지표 {len(variables)}개 — 회사마다 값, 신호(정상·주의·위험·측정값·해당 없음·"
            "데이터 없음), 계산식, 경계값, 작년 값, 대조군(같은 업종 또는 전체 상장사) 중앙값. 못 찾은 값은 "
            "0 이 아니라 null 입니다. 키·로그인 없이 받는 정적 JSON 이고 필드는 openapi.json 에 적었습니다. "
            "공시 수치를 공식대로 계산한 측정값이며 투자권유가 아닙니다."
        ),
        "url": page_url,
        "inLanguage": LANGUAGE,
        "isAccessibleForFree": True,
        "license": {
            "@type": "CreativeWork",
            "name": "원천 데이터 이용 조건: OpenDART 이용약관",
            "url": DART_TERMS,
        },
        "creator": {"@type": "Organization", "name": SITE_NAME, "url": repo_url},
        "isBasedOn": {"@type": "Dataset", "name": "금융감독원 전자공시시스템(DART) 공시", "url": DART_HOME},
        "keywords": ["재무제표", "재무비율", "상장사", "DART", "부채비율", "영업이익률", "ROE"],
        "variableMeasured": variables,
        "dateModified": built_on,
        "distribution": downloads,
    }
    if span:
        data["temporalCoverage"] = span
    return data
