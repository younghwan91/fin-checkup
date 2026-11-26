"""지표 해설 페이지 `/m/<지표>/` 17개 · 목록 `/m/`.

문장은 전부 `fin_checkup.explain` 에서, 공식·경계값은 `metrics/engine.py` 에서 온다 — 여기서 새로 쓰지 않는다.
"실제 숫자로 계산해 보기" 의 회사는 `site.examples` 의 색과 무관한 규칙으로 고르고, 계산 과정만 보인다.

회사 페이지의 지표 이름이 이 페이지로 링크한다 (`metric_path`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from fin_checkup.explain.glossary import GlossaryTerm, terms_for_metric
from fin_checkup.explain.metrics import MetricExplainer, explainers, threshold_text
from fin_checkup.explain.sectors import peer_group_info
from fin_checkup.explain.worked import ROUNDING_NOTE
from fin_checkup.metrics.engine import NOT_APPLICABLE_BY_SECTOR, Category
from fin_checkup.metrics.peers import MIN_PEERS
from fin_checkup.metrics.sector import Sector
from fin_checkup.site.context import CompanyEntry, Site
from fin_checkup.site.examples import RULE_TEXT, Example, example_for
from fin_checkup.site.groups import peer_group_of, peer_groups, sector_path
from fin_checkup.site.images.market import metric_og_path
from fin_checkup.site.registry import Output, company_context, page
from fin_checkup.site.related import NextRead, next_reads
from fin_checkup.site.view import SignalView

#: 시장 전체 분포 페이지 (pages/market.py). 지표 카드 앵커는 `#d-<key>`.
MARKET_PATH = "market/"


def metric_path(key: str) -> str:
    """지표 해설 페이지의 사이트 내 경로. 링크는 `url(metric_path(key))`."""
    return f"m/{key}/"


@dataclass(frozen=True)
class ThresholdLine:
    #: 일반 기준이면 빈 문자열.
    sector_label: str
    text: str


@dataclass(frozen=True)
class CaveatLine:
    sector_label: str
    why: str
    instead: str


@dataclass(frozen=True)
class RuleLine:
    signal: SignalView
    when: str


@dataclass(frozen=True)
class DistLink:
    """이 지표의 분포 카드로 가는 링크 (`_distribution.html` 의 `#d-<key>`)."""

    path: str
    text: str


@dataclass(frozen=True)
class MetricPage:
    e: MetricExplainer
    path: str
    thresholds: tuple[ThresholdLine, ...]
    caveats: tuple[CaveatLine, ...]
    rules: tuple[RuleLine, ...]
    terms: tuple[GlossaryTerm, ...]
    example: Example | None
    dist_links: tuple[DistLink, ...]
    next_reads: tuple[NextRead, ...]


def dist_links_for(site: Site, key: str, example: Example | None) -> tuple[DistLink, ...]:
    """예시 회사가 속한 업종의 분포(표본이 모자라면 생략) + 시장 전체 분포. 회사 값으로 고르지 않는다."""
    links: list[DistLink] = []
    if example is not None:
        code = peer_group_of(example.entry)
        members = peer_groups(site).get(code, []) if code else []
        if len(members) >= MIN_PEERS:
            links.append(
                DistLink(f"{sector_path(code)}#d-{key}", f"{peer_group_info(code).name} 업종의 분포")
            )
    links.append(DistLink(f"{MARKET_PATH}#d-{key}", "전체 상장사의 분포"))
    return tuple(links)


@company_context("metric_dist")
def company_metric_dist(entry: CompanyEntry, site: Site) -> dict[str, Any]:
    """회사 페이지 지표 카드 → 그 지표의 분포 카드. 업종 표본이 모자라면 시장 전체로."""
    code = peer_group_of(entry)
    members = peer_groups(site).get(code, []) if code else []
    if len(members) >= MIN_PEERS:
        link = DistLink(sector_path(code), f"{peer_group_info(code).name} 업종 분포")
    else:
        link = DistLink(MARKET_PATH, "전체 상장사 분포")
    return {"metric_dist": link}


def thresholds_for(e: MetricExplainer) -> tuple[ThresholdLine, ...]:
    """일반 기준 한 줄 + 업종 경계값이 따로 있는 업종마다 한 줄. ⊘ 업종은 caveats 가 말한다."""
    lines = [ThresholdLine("", threshold_text(e.key))]
    for sector in Sector:
        if sector is Sector.GENERAL or e.key in NOT_APPLICABLE_BY_SECTOR.get(sector, {}):
            continue
        if e.definition.sector_bands and sector in e.definition.sector_bands:
            lines.append(ThresholdLine(sector.label, threshold_text(e.key, sector)))
    return tuple(lines)


def caveats_for(e: MetricExplainer) -> tuple[CaveatLine, ...]:
    return tuple(CaveatLine(c.sector.label, c.why, c.instead) for c in e.sector_caveats)


def metric_page(site: Site, e: MetricExplainer) -> MetricPage:
    example = example_for(site, e.key)
    return MetricPage(
        e=e,
        path=metric_path(e.key),
        thresholds=thresholds_for(e),
        caveats=caveats_for(e),
        rules=tuple(RuleLine(SignalView.of(r.signal), r.when) for r in e.special_rules),
        terms=terms_for_metric(e.key),
        example=example,
        dist_links=dist_links_for(site, e.key, example),
        next_reads=next_reads(metric_path(e.key)),
    )


@dataclass(frozen=True)
class IndexGroup:
    category: str
    items: tuple[MetricExplainer, ...]


def index_groups() -> tuple[IndexGroup, ...]:
    """묶음(수익성·안정성…)마다 엔진 정의 순서 그대로."""
    all_ = explainers()
    return tuple(
        IndexGroup(cat.value, tuple(e for e in all_ if e.category is cat))
        for cat in Category
        if any(e.category is cat for e in all_)
    )


@page("metric")
def metric_pages(site: Site) -> Iterator[Output]:
    yield site.render(
        "metric/index.html",
        "m/index.html",
        groups=index_groups(),
        metric_path=metric_path,
        next_reads=next_reads("m/"),
    )
    for e in explainers():
        mp = metric_page(site, e)
        yield site.render(
            "metric/page.html",
            metric_path(e.key) + "index.html",
            p=mp,
            next_reads=mp.next_reads,
            rule_text=RULE_TEXT,
            rounding_note=ROUNDING_NOTE,
            og_image=site.image_url(metric_og_path(e.key)),
        )
