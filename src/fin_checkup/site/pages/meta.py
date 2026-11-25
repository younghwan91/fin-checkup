"""`<head>` 메타 중 회사마다 다른 값 — 공유 카드 이미지 주소, 구조화 데이터(JSON-LD) 보탬.

제목·설명·canonical·og:·twitter: 태그와 JSON-LD 의 `WebPage` 뼈대는 `templates/base.html` 이
블록(title·description)과 `site.canonical_url(page_path)`·`site.json_ld()` 로 만든다. 여기는 회사 페이지에만
있는 `og_image` 와 JSON-LD 속성(회사·이동 경로·카드 이미지)을 보탠다.
이미지 파일은 `OG_IMAGE_PAGE`(context.py) 이름의 페이지가 만든다 — 그 페이지가 없으면 None 이라 태그도 없다.
"""

from __future__ import annotations

from typing import Any

from fin_checkup.site import structured
from fin_checkup.site.context import CompanyEntry, Site
from fin_checkup.site.pages.sector import company_peer_group
from fin_checkup.site.registry import company_context


@company_context("meta")
def og_image(entry: CompanyEntry, site: Site) -> dict[str, Any]:
    return {"og_image": site.og_image_url(entry.stock_code)}


@company_context("structured_data")
def company_json_ld(entry: CompanyEntry, site: Site) -> dict[str, Any]:
    """회사 페이지 `WebPage` 에 보탤 schema.org 속성. 절대주소가 없는 빌드에서는 쓰이지 않는다."""
    crumbs = [(structured.SITE_NAME, site.absolute_url())]
    group = company_peer_group(entry, site)["peer_group"]
    if group is not None:
        crumbs.append((group.info.name, site.absolute_url(group.path)))
    crumbs.append((entry.name, site.absolute_url(entry.path)))
    return {
        "jsonld": {
            "about": {
                "@type": "Corporation",
                "name": entry.name,
                "tickerSymbol": f"KRX:{entry.stock_code}",
            },
            "dateModified": site.built_on.isoformat(),
            "primaryImageOfPage": site.og_image_url(entry.stock_code),
            "breadcrumb": structured.breadcrumb(crumbs),
        }
    }
