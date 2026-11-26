"""시장 전체 분포 `/market/` — 지표마다 전 상장사의 분포와 신호 비율.

모집단은 업종 페이지와 같이 이 빌드에 페이지가 생기는 회사 전부(`site.companies`), 계산도 같은
`site.groups.distribution` 이다 (⚫·⊘·비원화 금액은 분포에서 빼고 이유별로 센다). 신호 비율은 회사마다
**자기 업종 기준**으로 판정된 결과를 센 것이라, 경계값을 기준(일반·금융업·부동산업)마다 한 줄씩 적는다.

회사 이름은 싣지 않는다 — 시장 전체 페이지에 회사 목록이 붙으면 "값 좋은 회사 모음" 이 되기 쉽다.
"""

from __future__ import annotations

from collections.abc import Iterator

from fin_checkup.metrics.peers import MIN_PEERS
from fin_checkup.site.context import Site
from fin_checkup.site.groups import SIGNAL_ORDER
from fin_checkup.site.images.market import MARKET_OG_PATH
from fin_checkup.site.pages.sector import member_sectors, metric_blocks
from fin_checkup.site.registry import Output, page
from fin_checkup.site.view import SignalView

PATH = "market/index.html"


@page("market")
def market_page(site: Site) -> Iterator[Output]:
    sectors = member_sectors(site.companies)
    yield site.render(
        "market/index.html",
        PATH,
        total=len(site.companies),
        sectors=sectors,
        blocks=metric_blocks(site.companies, sectors),
        legend=[SignalView.of(s) for s in SIGNAL_ORDER],
        min_peers=MIN_PEERS,
        og_image=site.image_url(MARKET_OG_PATH),
    )
