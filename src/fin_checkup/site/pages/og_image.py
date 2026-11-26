"""공유 카드 이미지 `/og/<종목코드>.png` (그림은 `site/images/og.py`), 시장 전체 `/og/market.png`·
지표 `/og/m/<지표>.png` (그림은 `site/images/market.py` — `/market/` 과 같은 분포).

이 페이지가 등록돼 있고 이번 빌드가 PNG 를 만들 때(`--images` + 한국어 글꼴 있음)만 build 가
`site.og_images` 를 켜고, 그래야 회사 페이지가 og:image 를 낸다 — 파일이 없는 주소를 카드로 내보내지 않게.
"""

from __future__ import annotations

from collections.abc import Iterator

from fin_checkup.site import incremental
from fin_checkup.site.context import OG_IMAGE_PAGE, Site
from fin_checkup.site.images.market import draw_market_og, market_deck, market_og_specs
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
    yield from _market_outputs(site, label)


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


#: 같은 프로세스 안에서 그린 시장 공유 카드 (열쇠 → PNG). 내용·글꼴·코드 판이 같으면 그림도 같다 —
#: 테스트처럼 같은 시드로 여러 번 빌드할 때 18장(1.4초)을 매번 다시 그리지 않게. 오래된 것부터 버린다.
_MARKET_MEMO: dict[str, bytes] = {}
_MARKET_MEMO_MAX = 64


def _market_outputs(site: Site, label: str) -> Iterator[Output]:
    """`/market/`·지표 페이지 공유 카드. 증분 캐시가 있으면 거기서, 없으면 프로세스 안 메모에서 다시 쓴다."""
    deck = market_deck(site.companies, site.year)
    fonts = incremental.fonts_id(site.fonts)
    head = (deck.year, deck.total, deck.sector_labels)
    for path, metric, kicker in market_og_specs(deck):
        k = incremental.key("market-og", incremental.code_version(), fonts, head, metric, kicker, label)
        png = site.build_cache.get_bytes(k) if site.build_cache is not None else _MARKET_MEMO.get(k)
        if png is None:
            png = draw_market_og(deck, metric, site.fonts, kicker, label).png()
            if site.build_cache is not None:
                site.build_cache.put_bytes(k, png)
            else:
                while len(_MARKET_MEMO) >= _MARKET_MEMO_MAX:
                    _MARKET_MEMO.pop(next(iter(_MARKET_MEMO)))
                _MARKET_MEMO[k] = png
        yield Output(path, png)
