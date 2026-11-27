"""업종 페이지 `/s/` — 회사 페이지 대조군과 같은 묶음, 분포(⚫·⊘ 제외), 소속 회사 이름순, 순위 없음."""

from __future__ import annotations

import html
import re
import statistics
from datetime import date

import pytest
from hub_seed import HLB, KB, NO_DATA, SAMSUNG, UNLISTED, seed_site_cache, statements
from site_text import unmark

from fin_checkup.explain import banned
from fin_checkup.explain.sectors import KSIC_DIVISIONS, group_sector, peer_group_info
from fin_checkup.metrics import engine
from fin_checkup.metrics.sector import FINANCIAL_PREFIXES, Sector, sector_for
from fin_checkup.models import Company, CorpCode, Market
from fin_checkup.site.build import build_site, collect_companies
from fin_checkup.site.groups import distribution
from fin_checkup.site.pages.sector import member_sectors, metric_blocks
from fin_checkup.storage import Cache

#: 업종 26(전자부품)에 더하는 회사. 이름순과 영업이익률 순이 어긋나게 골랐다.
#: (이름, 종목코드, 매출, 영업이익) — 영업이익률 = 영업이익/매출×100.
EXTRA_26 = [
    ("가나전자", "100001", 1000.0, 50.0),  # 5%
    ("다라전자", "100002", 1000.0, 300.0),  # 30%
    ("마바전자", "100003", 1000.0, -20.0),  # -2%
    ("사아전자", "100004", 1000.0, 100.0),  # 10%
    ("하하전자", "100005", 1000.0, 10.0),  # 1%
]


#: 비금융 지주회사 — 금융지주(KB)와 같은 64992 를 쓰지만 일반 기준으로 판정된다.
HOLDING = CorpCode(corp_code="00800000", corp_name="가나지주", stock_code="200001")


def seed_group(cache: Cache) -> None:
    seed_site_cache(cache)
    cache.save_company(
        Company(
            corp_code=HOLDING.corp_code,
            corp_name=HOLDING.corp_name,
            stock_code=HOLDING.stock_code,
            market=Market.KOSPI,
            industry_code="64992",
        )
    )
    cache.save_statements(statements(HOLDING.corp_code, 2024, 500.0, 50.0))
    corps = [
        CorpCode(corp_code=f"0090000{i}", corp_name=name, stock_code=code)
        for i, (name, code, _r, _o) in enumerate(EXTRA_26)
    ]
    # save_corp_codes 는 표를 통째로 바꾼다 — 시드 회사와 함께 넣는다.
    cache.save_corp_codes([SAMSUNG, KB, HLB, NO_DATA, UNLISTED, HOLDING, *corps])
    for corp, (name, code, revenue, op) in zip(corps, EXTRA_26, strict=True):
        cache.save_company(
            Company(
                corp_code=corp.corp_code,
                corp_name=name,
                stock_code=code,
                market=Market.KOSDAQ,
                industry_code="26110",
            )
        )
        cache.save_statements(statements(corp.corp_code, 2024, revenue, op))


@pytest.fixture
async def out(tmp_path):
    with Cache(tmp_path / "site.duckdb") as cache:
        seed_group(cache)
        target = tmp_path / "out"
        await build_site(cache, target, 2024, built_on=date(2026, 11, 21))
    return target


def read(out, *parts) -> str:
    return unmark(html.unescape((out.joinpath(*parts)).read_text(encoding="utf-8")))


def section(text: str, ident: str) -> str:
    match = re.search(rf'<section[^>]*id="{ident}".*?</section>', text, re.S)
    assert match, ident
    return match.group(0)


def card(text: str, key: str) -> str:
    match = re.search(rf'<li class="fc-metric" id="d-{key}">.*?\n  </li>', text, re.S)
    assert match, key
    return match.group(0)


async def test_index_lists_each_peer_group_in_code_order(out):
    text = read(out, "s", "index.html")
    links = re.findall(r'href="\.\./s/(\d\d)/"', text)
    assert links == ["26", "64"]
    assert "전자부품·컴퓨터·통신장비 제조업" in text and "금융업" in text
    assert "업종코드가 없는 회사 1곳" in text, "기업개황이 없는 에이치엘비는 어느 업종에도 없다"


async def test_members_are_in_name_order_not_metric_order(out):
    members = section(read(out, "s", "26", "index.html"), "members")
    names = re.findall(r'<a href="[^"]+">([^<]+)</a>', members)
    assert names == sorted([n for n, *_ in EXTRA_26] + ["삼성전자"])
    by_margin = ["다라전자", "사아전자", "가나전자", "삼성전자", "하하전자", "마바전자"]
    assert names != by_margin


async def test_distribution_matches_member_values(out):
    text = read(out, "s", "26", "index.html")
    margins = [op / rev * 100 for _n, _c, rev, op in EXTRA_26] + [40 / 900 * 100]
    q1, _, q3 = statistics.quantiles(margins, n=4, method="inclusive")
    opm = card(text, "operating_margin")
    assert "6곳" in opm
    assert f"{statistics.median(margins):,.2f}%" in opm
    assert f"{q1:,.2f}%" in opm and f"{q3:,.2f}%" in opm


async def test_every_metric_shows_formula_threshold_and_explainer_link(out):
    text = read(out, "s", "26", "index.html")
    for spec in engine.METRIC_DEFS:
        block = card(text, spec.key)
        assert spec.formula in block, spec.key
        assert "경계값" in block
        assert f'm/{spec.key}/"' in block


async def test_missing_values_are_left_out_not_counted_as_zero(out):
    fcf = card(read(out, "s", "26", "index.html"), "fcf")
    assert "0곳" in fcf and "분포를 내지 않습니다" in fcf
    assert "값이 없는 ⚫ 6곳" in fcf and "0으로 세지 않습니다" in fcf


async def test_tiny_group_says_so_and_shows_no_distribution(out):
    text = read(out, "s", "64", "index.html")
    assert "2곳뿐이라 섹터 분포를 산출하지 않습니다" in text
    assert "일반·은행·증권·금융지주 임계치 기준" in text
    assert 'id="distribution"' not in text
    assert "BIS" in section(text, "about"), "금융업 ⊘ 지표의 대신 볼 것"
    assert peer_group_info("64").mixed_note in text
    assert "KB금융" in section(text, "members")


async def test_not_applicable_values_are_left_out_of_the_distribution(tmp_path):
    with Cache(tmp_path / "d.duckdb") as cache:
        seed_group(cache)
        entries, _ = await collect_companies(cache, 2024)
    kb = [e for e in entries if e.stock_code == KB.stock_code]
    debt = next(d for d in engine.METRIC_DEFS if d.key == "debt_ratio")
    dist = distribution(debt, kb)
    assert kb[0].checkup.result.by_key("debt_ratio").value is not None, "⊘ 도 계산은 된다"
    assert (dist.count, dist.not_applicable, dist.total) == (0, 1, 1)
    assert dist.excluded_text == "업종에 맞지 않는 ⊘ 1곳"


async def test_mixed_group_shows_the_thresholds_of_every_sector_used(tmp_path):
    with Cache(tmp_path / "d.duckdb") as cache:
        seed_group(cache)
        entries, _ = await collect_companies(cache, 2024)
    group64 = [e for e in entries if e.stock_code in (KB.stock_code, HOLDING.stock_code)]
    sectors = member_sectors(group64)
    assert sectors == [Sector.GENERAL, Sector.BANKING]
    debt = next(b for b in metric_blocks(group64, sectors) if b.dist.key == "debt_ratio")
    assert [t.sector_label for t in debt.thresholds] == ["일반", "은행·증권·금융지주"]
    assert "⊘" in debt.thresholds[1].text and "이하" in debt.thresholds[0].text
    assert debt.dist.count == 1, "비금융 지주의 부채비율만 분포에 든다"


async def test_company_page_links_to_its_sector_page(out):
    samsung = read(out, "c", "005930", "index.html")
    assert 'href="../../s/26/"' in samsung
    assert "KSIC 26 · 6곳" in samsung
    hlb = read(out, "c", "028300", "index.html")
    hlb_main = hlb.split("<main", 1)[1].split("</main>", 1)[0]  # 머리 nav 의 '업종'(s/) 링크는 뺀다
    assert 'href="../../s/' not in hlb_main, "업종코드가 없으면 업종 링크도 없다"


async def test_sector_pages_have_no_ranking_or_advice_words(out):
    for path in (out / "s").rglob("*.html"):
        text = read(out, *path.relative_to(out).parts)
        main = text.split("<main")[1].split("</main>")[0]
        assert banned.find(main) == [], path
        assert "상위" not in main and "순위" not in main, path


def test_peer_group_names_cover_two_digit_codes():
    assert all(len(code) == 2 and code.isdigit() for code in KSIC_DIVISIONS)
    assert peer_group_info("26").name.startswith("전자부품")
    assert peer_group_info("99").name == "업종코드 99", "표에 없는 코드도 페이지가 생긴다"
    with pytest.raises(ValueError):
        peer_group_info("264")


def test_group_sector_follows_sector_for_prefix_rule():
    for code in KSIC_DIVISIONS:
        if code == "64":
            continue  # 64992 지주회사는 이름으로 가른다 — mixed_note
        assert group_sector(code) is sector_for(code + "000"), code
    assert group_sector("64") is Sector.FINANCIAL
    assert set(FINANCIAL_PREFIXES) <= {c for c in KSIC_DIVISIONS if group_sector(c) is Sector.FINANCIAL}


def test_explanations_have_no_banned_words():
    for name, plain in KSIC_DIVISIONS.values():
        assert banned.find(name + plain) == []


def _title(text: str) -> str:
    return html.unescape(re.search(r"<title>([^<]*)</title>", text).group(1))


async def test_titles_lead_with_the_words_people_search(out):
    # "반도체 업종 재무비율" 처럼 찾는다. 분포를 내는 업종만 중앙값·사분위라고 쓴다 — 없는 것을 약속하지 않게.
    assert _title(read(out, "s", "26", "index.html")) == (
        "전자부품·컴퓨터·통신장비 제조업 섹터 재무비율 — 상장사 6곳 중앙값·사분위 (2024) · fin-checkup"
    )
    tiny = _title(read(out, "s", "64", "index.html"))
    assert tiny == "금융업·지주회사 섹터 상장사 2곳 — 회사별 재무제표 신호등 (2024) · fin-checkup"
    assert "중앙값" not in tiny
    assert _title(read(out, "s", "index.html")).startswith("섹터별 재무비율 분포 — ")
