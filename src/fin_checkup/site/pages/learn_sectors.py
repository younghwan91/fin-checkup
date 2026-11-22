"""업종별 읽는 법 `/learn/sectors/` — 은행·증권·금융지주·금융업·부동산업·지주회사가 왜 다르고,
무엇이 ⊘ 이고, 대신 무엇을 보나.

문장은 `explain.sector_reading`(업종 구분마다)과 `explain.metrics`(지표마다 ⊘ 이유·대신 볼 것)에서,
⊘ 목록과 업종 경계값은 `metrics.engine` 에서 온다. 업종 페이지 링크는 업종 레인의 `site.groups.sector_path`
그대로 — 이 빌드에 있는 묶음만 링크한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fin_checkup.explain.metrics import explainers, threshold_text
from fin_checkup.explain.sector_reading import (
    GENERAL_READING,
    HOLDING_READING,
    HOW_DECIDED,
    READINGS,
    WHY_SECTORS,
    SectorReading,
)
from fin_checkup.explain.sectors import peer_group_info
from fin_checkup.metrics.engine import NOT_APPLICABLE_BY_SECTOR
from fin_checkup.metrics.sector import HOLDING_COMPANY_CODE, PEER_GROUP_DIGITS
from fin_checkup.site.context import Site
from fin_checkup.site.groups import peer_groups, sector_path
from fin_checkup.site.pages.metric import metric_path
from fin_checkup.site.pages.sector import Caveat, GroupLink, caveats_for
from fin_checkup.site.registry import Output, page
from fin_checkup.site.related import next_reads


@dataclass(frozen=True)
class OwnBand:
    label: str
    text: str
    path: str


@dataclass(frozen=True)
class SectorBlock:
    reading: SectorReading
    caveats: tuple[Caveat, ...]
    own_bands: tuple[OwnBand, ...]
    #: ⊘ 도 업종 경계값도 아닌, 일반 기준 그대로 판정하는 지표 이름.
    same_as_general: tuple[str, ...]
    #: 이 빌드에 있는 이 기준의 업종 묶음 (코드순).
    groups: tuple[GroupLink, ...]


def sector_block(site: Site, reading: SectorReading) -> SectorBlock:
    sector = reading.sector
    na = NOT_APPLICABLE_BY_SECTOR.get(sector, {})
    own, same = [], []
    for e in explainers():
        if e.key in na:
            continue
        if e.definition.sector_bands and sector in e.definition.sector_bands:
            own.append(OwnBand(e.label, threshold_text(e.key, sector), metric_path(e.key)))
        else:
            same.append(e.label)
    # 묶음(앞 두 자리) 하나에 기준이 섞인다 — 64 에는 은행·금융지주와 카드·투자회사가, 66 에는 증권사와
    # PG사가 함께 있다. 이 기준으로 판정된 회사가 실제로 있는 묶음을 잇는다.
    groups = tuple(
        GroupLink(peer_group_info(code), sector_path(code), len(members))
        for code, members in peer_groups(site).items()
        if any(m.checkup.result.sector is sector for m in members)
    )
    return SectorBlock(reading, tuple(caveats_for(sector)), tuple(own), tuple(same), groups)


def holding_group(site: Site) -> GroupLink | None:
    code = HOLDING_COMPANY_CODE[:PEER_GROUP_DIGITS]
    members = peer_groups(site).get(code)
    return GroupLink(peer_group_info(code), sector_path(code), len(members)) if members else None


@page("learn_sectors")
def learn_sectors_page(site: Site) -> Iterator[Output]:
    yield site.render(
        "learn/sectors.html",
        "learn/sectors/index.html",
        why=WHY_SECTORS,
        how=HOW_DECIDED,
        blocks=tuple(sector_block(site, r) for r in READINGS),
        holding=HOLDING_READING,
        holding_group=holding_group(site),
        general=GENERAL_READING,
        metric_path=metric_path,
        next_reads=next_reads("learn/sectors/"),
    )
