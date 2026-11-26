"""공유 카드 이미지 `/og/<종목코드>.png` (그림은 `site/images/og.py`), 시장 전체 `/og/market.png`·
지표 `/og/m/<지표>.png` (그림은 `site/images/market.py` — `/market/` 과 같은 분포).

이 페이지가 등록돼 있고 이번 빌드가 PNG 를 만들 때(`--images` + 한국어 글꼴 있음)만 build 가
`site.og_images` 를 켜고, 그래야 회사 페이지가 og:image 를 낸다 — 파일이 없는 주소를 카드로 내보내지 않게.
"""

from __future__ import annotations

from collections.abc import Iterator

from fin_checkup.site import incremental
from fin_checkup.site.context import OG_IMAGE_PAGE, Site
from fin_checkup.site.images.market import market_deck, market_og_pngs
from fin_checkup.site.images.og import OgCard, og_card_for, og_path, render_many
from fin_checkup.site.registry import Output, page


def site_label(site: Site) -> str:
    """바닥글에 찍을 주소 — 스킴과 끝 '/' 를 뗀다. 절대주소가 없으면 빈 문자열."""
    if not site.has_absolute_base:
        return ""
    return site.base_url.split("://", 1)[1].rstrip("/")


@page(OG_IMAGE_PAGE)
def og_images(site: Site) -> Iterator[Output]:
    if site.fonts is None:  # build 가 이 페이지를 빼야 했다. 직접 불렸을 때도 두부 PNG 를 내지 않는다.
        return
    label = site_label(site)
    cards = [og_card_for(entry, label) for entry in site.companies]
    for card, png in zip(cards, _pngs(site, cards), strict=True):
        yield Output(og_path(card.stock_code), png)
    deck = market_deck(site.companies, site.year)
    for path, png in market_og_pngs(deck, site.fonts, label):
        yield Output(path, png)


def _pngs(site: Site, cards: list[OgCard]) -> list[bytes]:
    """카드 PNG. 증분 캐시가 있으면 카드 내용·글꼴·코드 판이 같은 그림은 다시 그리지 않는다."""
    cache = site.build_cache
    if cache is None:
        return list(render_many(cards, site.fonts, site.image_workers))
    fonts = incremental.fonts_id(site.fonts)
    keys = [incremental.key("og", incremental.code_version(), fonts, card) for card in cards]
    pngs = [cache.get_bytes(k) for k in keys]
    todo = [i for i, png in enumerate(pngs) if png is None]
    drawn = render_many([cards[i] for i in todo], site.fonts, site.image_workers)
    for i, png in zip(todo, drawn, strict=True):
        cache.put_bytes(keys[i], png)
        pngs[i] = png
    return [png for png in pngs if png is not None]
