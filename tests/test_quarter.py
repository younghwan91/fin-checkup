"""최근 분기 주요계정 (quarter.py) — 사업보고서 사이 몇 달을 비워 두지 않되, 판정은 하지 않는다.

네이버 증권·FnGuide 는 11월이면 3분기 숫자를 보여 주는데, 사업보고서만 쓰면 11월에도 전년도 숫자뿐이다.
DART 다중회사 주요계정(100곳에 1회)으로 최근 분기를 받아 회사 페이지에 '그대로' 싣는다.
지켜야 할 것: 손익은 그 분기 석 달 값(API 의 thstrm_dt 가 누적 기간을 적어도), 없는 계정은 0 이 아니라 ⚫,
신호등·점수 없음, 사업보고서와 같은 해의 분기는 싣지 않음.
"""

from __future__ import annotations

import html
import re
from datetime import date

import httpx
import pytest
import respx
from hub_seed import HLB, KB, SAMSUNG, seed_site_cache

from fin_checkup.collect import STOP_BUDGET, collect
from fin_checkup.config import Settings
from fin_checkup.dart.client import DartClient
from fin_checkup.models import Company, CorpCode, Market, ReportCode
from fin_checkup.quarter import (
    KeyAccountRow,
    build_quarter,
    interim_targets,
    latest_quarters,
    newer_than_annual,
)
from fin_checkup.site.build import build_site
from fin_checkup.site.guard import check_site
from fin_checkup.snapshot import export_snapshot, import_snapshot
from fin_checkup.storage import Cache

BASE = "https://opendart.fss.or.kr/api"
MULTI = f"{BASE}/fnlttMultiAcnt.json"
TODAY = date(2026, 11, 26)
STATEMENTS = f"{BASE}/fnlttSinglAcntAll.json"


def api_row(corp: str, fs: str, sj: str, name: str, now: str, before: str | None, order: int,
            year: int = 2026, reprt: str = "11014") -> dict:
    """fnlttMultiAcnt 응답 한 줄 — 2026-11-26 실제 응답 모양 (손익 thstrm_dt 는 누적 기간을 적는다)."""
    bs = sj == "BS"
    return {
        "rcept_no": "20261116001482", "bsns_year": str(year), "corp_code": corp, "stock_code": "005930",
        "reprt_code": reprt, "account_nm": name, "fs_div": fs, "fs_nm": "연결재무제표", "sj_div": sj,
        "sj_nm": "재무상태표" if bs else "손익계산서",
        "thstrm_nm": "제 58 기3분기말" if bs else "제 58 기3분기",
        "thstrm_dt": f"{year}.09.30 현재" if bs else f"{year}.01.01 ~ {year}.09.30",
        "thstrm_amount": now,
        "frmtrm_nm": "제 57 기말" if bs else "제 57 기3분기",
        "frmtrm_dt": f"{year - 1}.12.31 현재" if bs else f"{year - 1}.01.01 ~ {year - 1}.09.30",
        "frmtrm_amount": before, "ord": str(order), "currency": "KRW",
    }


def samsung_rows(corp: str = "00126380", year: int = 2026, reprt: str = "11014") -> list[dict]:
    rows = []
    for fs, scale in (("CFS", 1), ("OFS", 2)):
        def r(sj, name, now, before, order, fs=fs, scale=scale):
            return api_row(corp, fs, sj, name, f"{now * scale:,}", f"{before * scale:,}", order, year, reprt)

        rows += [
            r("BS", "자산총계", 7918, 5669, 3),
            r("BS", "부채총계", 1777, 1306, 6),
            r("BS", "자본총계", 6141, 4363, 9),
            r("IS", "매출액", 1952, 860, 10),
            r("IS", "영업이익", 1074, 121, 11),
            r("IS", "당기순이익(손실)", 751, 122, 13),
            r("IS", "당기순이익(손실)", 999, 999, 14),  # 같은 이름이 두 번 — 앞의 것
        ]
    return rows


def to_rows(items: list[dict]) -> list[KeyAccountRow]:
    def amount(v):
        return None if v is None else float(v.replace(",", ""))

    return [
        KeyAccountRow(
            corp_code=i["corp_code"], bsns_year=int(i["bsns_year"]), reprt_code=i["reprt_code"],
            fs_div=i["fs_div"], sj_div=i["sj_div"], account_nm=i["account_nm"], ord=int(i["ord"]),
            thstrm_dt=i["thstrm_dt"], thstrm_amount=amount(i["thstrm_amount"]), frmtrm_dt=i["frmtrm_dt"],
            frmtrm_amount=amount(i["frmtrm_amount"]), currency=i["currency"], rcept_no=i["rcept_no"],
        )
        for i in items
    ]


# ── 값 만들기 ─────────────────────────────────────────────────────────────


def test_prefers_consolidated_and_labels_the_three_month_quarter():
    q = build_quarter(to_rows(samsung_rows()))
    assert q.fs_div == "CFS" and q.statement_label == "연결재무제표"
    # thstrm_dt 는 '2026.01.01 ~ 2026.09.30' 이지만 금액은 7~9월 석 달 값이다.
    assert q.period_label == "2026년 7~9월"
    assert q.prior_period_label == "2025년 7~9월"
    assert q.period_end == date(2026, 9, 30) and q.prior_year_end == date(2025, 12, 31)
    assert q.line("revenue").current == 1952 and q.line("revenue").prior == 860
    assert q.line("net_income").current == 751, "같은 계정명이 두 번이면 ord 가 앞인 것"
    assert q.report_label == "2026년 3분기보고서"
    assert q.dart_url.endswith("rcpNo=20261116001482")


def test_falls_back_to_separate_statements():
    rows = [r for r in to_rows(samsung_rows()) if r.fs_div == "OFS"]
    q = build_quarter(rows)
    assert q.fs_div == "OFS" and q.line("revenue").current == 3904


def test_missing_account_is_none_not_zero():
    rows = [r for r in to_rows(samsung_rows()) if r.account_nm != "영업이익"]
    q = build_quarter(rows)
    line = q.line("operating_income")
    assert line.current is None and line.prior is None
    assert line.change == ""


@pytest.mark.parametrize(
    ("key", "now", "before", "expected"),
    [
        ("revenue", 120.0, 100.0, "+20.0%"),
        ("revenue", 80.0, 100.0, "-20.0%"),
        ("operating_income", 10.0, -5.0, "흑자 전환"),
        ("operating_income", -10.0, 5.0, "적자 전환"),
        ("net_income", -10.0, -5.0, "적자 지속"),
        ("revenue", 10.0, 0.0, ""),
        ("revenue", None, 100.0, ""),
    ],
)
def test_change_is_a_fact_not_a_verdict(key, now, before, expected):
    from fin_checkup.quarter import QuarterLine

    assert QuarterLine(key, "x", now, before, "KRW").change == expected


def test_debt_ratio_is_not_computed_for_non_positive_equity():
    rows = to_rows(samsung_rows())
    q = build_quarter(rows)
    assert q.debt_ratio() == pytest.approx(1777 / 6141 * 100)
    wiped = [
        KeyAccountRow(**{**r.__dict__, "thstrm_amount": -1.0}) if r.account_nm == "자본총계" else r
        for r in rows
    ]
    assert build_quarter(wiped).debt_ratio() is None


def test_latest_quarter_wins_and_only_after_the_annual_report():
    rows = to_rows(samsung_rows(year=2026, reprt="11012") + samsung_rows(year=2026, reprt="11014")
                   + samsung_rows(year=2025, reprt="11014"))
    q = latest_quarters(rows)["00126380"]
    assert (q.bsns_year, q.reprt_code) == (2026, ReportCode.Q3)
    assert newer_than_annual(q, 2025) is q
    assert newer_than_annual(q, 2026) is None, "같은 해 사업보고서가 있으면 분기는 싣지 않는다"


def test_interim_targets_are_the_two_latest_ended_quarters():
    assert interim_targets(date(2026, 11, 26)) == [(2026, ReportCode.Q3), (2026, ReportCode.HALF)]
    assert interim_targets(date(2027, 2, 1)) == [(2026, ReportCode.Q3), (2026, ReportCode.HALF)]
    assert interim_targets(date(2026, 4, 2)) == [(2026, ReportCode.Q1), (2025, ReportCode.Q3)]
    assert interim_targets(date(2026, 3, 31)) == [(2025, ReportCode.Q3), (2025, ReportCode.HALF)]


# ── DART · 캐시 · 수집 ────────────────────────────────────────────────────


@respx.mock
async def test_client_sends_one_call_for_many_companies(test_settings: Settings):
    route = respx.get(MULTI).mock(
        return_value=httpx.Response(200, json={"status": "000", "list": samsung_rows()})
    )
    async with DartClient(settings=test_settings) as client:
        rows = await client.fetch_key_accounts(["00126380", "00164779"], 2026, ReportCode.Q3)
    assert route.call_count == 1
    params = route.calls[0].request.url.params
    assert params["corp_code"] == "00126380,00164779" and params["reprt_code"] == "11014"
    assert len(rows) == len(samsung_rows()) and rows[0].thstrm_amount == 7918
    with pytest.raises(ValueError):
        async with DartClient(settings=test_settings) as client:
            await client.fetch_key_accounts([f"{i:08d}" for i in range(101)], 2026, ReportCode.Q3)


def test_cache_round_trip_replaces_a_report(tmp_path):
    with Cache(tmp_path / "q.duckdb") as cache:
        rows = to_rows(samsung_rows())
        assert cache.save_key_accounts(rows) == 1
        assert cache.save_key_accounts(rows) == 1
        assert len(cache.key_account_rows()) == len(rows), "다시 넣으면 바꿔 넣는다 (중복 없음)"
        assert cache.corp_codes_with_key_accounts(2026, ReportCode.Q3) == {"00126380"}
        assert cache.key_account_rows(["nobody"]) == []


def listed(cache: Cache, n: int) -> list[CorpCode]:
    corps = [CorpCode(corp_code=f"{i:08d}", corp_name=f"회사{i}", stock_code=f"{i:06d}") for i in range(n)]
    cache.save_corp_codes(corps)
    for c in corps:
        cache.save_company(
            Company(
                corp_code=c.corp_code, corp_name=c.corp_name, stock_code=c.stock_code, market=Market.KOSPI
            )
        )
    return corps


@respx.mock
async def test_collect_asks_in_batches_of_100_and_skips_what_it_has(tmp_path, test_settings: Settings):
    with Cache(tmp_path / "c.duckdb") as cache:
        corps = listed(cache, 150)
        first = corps[0].corp_code

        def reply(request):
            asked = request.url.params["corp_code"].split(",")
            reprt = request.url.params["reprt_code"]
            rows = samsung_rows(first, 2026, reprt) if first in asked else []
            return httpx.Response(200, json={"status": "000", "list": rows} if rows else {"status": "013"})

        multi = respx.get(MULTI).mock(side_effect=reply)
        respx.get(STATEMENTS).mock(return_value=httpx.Response(200, json={"status": "013"}))
        summary = await collect(
            cache, test_settings, budget=10_000, years=[2025], today=TODAY, progress=lambda _: None
        )
        # 분기 둘(3분기·반기) × 150곳 → 묶음 2개씩.
        assert multi.call_count == 4
        assert summary.quarter_reports == ["2026-11014", "2026-11012"]
        assert summary.quarter_fetched == 2 and summary.new_data
        assert max(len(c.request.url.params["corp_code"].split(",")) for c in multi.calls) == 100

        before = multi.call_count
        await collect(
            cache, test_settings, budget=10_000, years=[2025], today=TODAY, progress=lambda _: None
        )
        asked = {
            c for call in list(multi.calls)[before:] for c in call.request.url.params["corp_code"].split(",")
        }
        assert first not in asked, "이미 받은 회사는 다시 묻지 않는다"
        assert len(asked) == 149


@respx.mock
async def test_collect_quarter_step_respects_the_budget(tmp_path, test_settings: Settings):
    with Cache(tmp_path / "c.duckdb") as cache:
        listed(cache, 150)
        multi = respx.get(MULTI).mock(return_value=httpx.Response(200, json={"status": "013"}))
        summary = await collect(
            cache, test_settings, budget=1, years=[2025], today=date(2026, 11, 26), progress=lambda _: None
        )
        assert summary.calls <= 1 and multi.call_count <= 1
        assert summary.stopped == STOP_BUDGET


def test_snapshot_carries_key_accounts_and_old_snapshots_still_import(tmp_path):
    db = tmp_path / "src.duckdb"
    with Cache(db) as cache:
        cache.save_key_accounts(to_rows(samsung_rows()))
    export_snapshot(db, tmp_path / "snap")
    with Cache(import_snapshot(tmp_path / "snap", tmp_path / "a.duckdb") and tmp_path / "a.duckdb") as cache:
        assert len(cache.key_account_rows()) == len(samsung_rows())

    # 이 테이블이 생기기 전의 스냅샷(manifest 에 없음)도 들여온다 — 첫 배포가 옛 data-* 릴리스를 읽는다.
    import json

    manifest = json.loads((tmp_path / "snap" / "manifest.json").read_text(encoding="utf-8"))
    del manifest["tables"]["key_accounts"]
    (tmp_path / "snap" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "snap" / "key_accounts.parquet").unlink()
    import_snapshot(tmp_path / "snap", tmp_path / "b.duckdb")
    with Cache(tmp_path / "b.duckdb") as cache:
        assert cache.key_account_rows() == []


# ── 회사 페이지 ──────────────────────────────────────────────────────────


@pytest.fixture
async def out(tmp_path):
    with Cache(tmp_path / "site.duckdb") as cache:
        seed_site_cache(cache)
        rows = to_rows(samsung_rows(SAMSUNG.corp_code, 2025, "11014"))
        cache.save_key_accounts([r for r in rows if r.account_nm != "영업이익"])
        # 에이치엘비는 사업보고서(2024)와 같은 해의 분기뿐 → 싣지 않는다.
        cache.save_key_accounts(to_rows(samsung_rows(HLB.corp_code, 2024, "11012")))
        # KB금융은 금융지주 — 연간 부채비율이 ⊘ 라 분기 부채비율도 ⊘.
        cache.save_key_accounts(to_rows(samsung_rows(KB.corp_code, 2025, "11014")))
        target = tmp_path / "out"
        await build_site(cache, target, 2024, built_on=date(2026, 11, 21))
    return target


def page(out, code: str, name: str = "index.html") -> str:
    return html.unescape((out / "c" / code / name).read_text(encoding="utf-8"))


def section(text: str) -> str:
    match = re.search(r'<section[^>]*id="quarter".*?</section>', text, re.S)
    assert match, "최근 분기 절이 없다"
    return match.group(0)


def test_company_page_shows_latest_quarter_without_signals(out):
    text = page(out, SAMSUNG.stock_code)
    q = section(text)
    assert "최근 분기 — 2025년 7~9월" in q
    assert "2025년 3분기보고서 · 연결재무제표" in q
    assert "dart.fss.or.kr/dsaf001/main.do?rcpNo=20261116001482" in q
    assert "+127.0%" in q  # 매출 1952 / 860
    # 판정 없음: 신호 배지·점수가 없다. 없는 계정(영업이익)은 0 이 아니라 데이터 없음.
    assert "fc-sig" not in q and "점수" not in q
    missing = (
        r'영업이익</th><td><span class="fc-mark mark-unknown"[^>]*></span>'
        r'<span class="muted">없음'
    )
    assert re.search(missing, q)
    assert "부채총계 ÷ 자본총계 × 100" in q
    assert text.index('id="metrics"') < text.index('id="quarter"') < text.index('id="trend"')


def test_quarter_section_is_absent_when_not_newer_than_annual(out):
    assert 'id="quarter"' not in page(out, HLB.stock_code)


def test_debt_ratio_stays_not_applicable_where_the_annual_one_is(out):
    q = section(page(out, KB.stock_code))
    assert re.search(r'부채비율</th><td colspan="3"[^>]*><span class="fc-mark mark-not_applicable"', q)
    assert "%p" not in q, "⊘ 업종에 분기 부채비율 숫자를 내지 않는다"
    md = (out / "c" / KB.stock_code / "index.md").read_text(encoding="utf-8")
    assert "| 부채비율 | ⊘ 해당 없음 | ⊘ 해당 없음 |" in md
    import json

    doc = json.loads((out / "api" / "c" / f"{KB.stock_code}.json").read_text(encoding="utf-8"))
    ratio = doc["latest_quarter"]["debt_ratio"]
    assert ratio["value"] is None and ratio["not_applicable"]


def test_markdown_page_carries_the_quarter_too(out):
    md = (out / "c" / SAMSUNG.stock_code / "index.md").read_text(encoding="utf-8")
    assert "## 최근 분기 — 2025년 7~9월" in md
    assert "| 영업이익 | ⚫ 데이터 없음 | ⚫ 데이터 없음 |" in md
    assert "## 최근 분기" not in (out / "c" / HLB.stock_code / "index.md").read_text(encoding="utf-8")


def test_site_guard_still_passes(out):
    assert check_site(out) == []


def test_company_json_carries_latest_quarter_as_values_only(out):
    import json

    doc = json.loads((out / "api" / "c" / f"{SAMSUNG.stock_code}.json").read_text(encoding="utf-8"))
    q = doc["latest_quarter"]
    assert q["period_end"] == "2025-09-30" and q["reprt_code"] == "11014"
    assert q["flows"]["revenue"] == {"label": "매출액", "value": 1952.0, "prior": 860.0}
    assert q["flows"]["operating_income"]["value"] is None, "없는 값은 null — 0 이 아니다"
    assert "signal" not in json.dumps(q)
    hlb = json.loads((out / "api" / "c" / f"{HLB.stock_code}.json").read_text(encoding="utf-8"))
    assert hlb["latest_quarter"] is None


async def test_mcp_diagnose_passes_the_quarter_through(out, tmp_path):
    from fin_checkup.mcp import core
    from fin_checkup.mcp.client import SiteSource

    base = "https://fin.example.test/fin-checkup/"
    with respx.mock(base_url=base, assert_all_called=False) as router:
        for rel in ("search-index.json", "build.json", f"api/c/{SAMSUNG.stock_code}.json"):
            router.get(rel).mock(return_value=httpx.Response(200, content=(out / rel).read_bytes()))
        router.get(url__regex=r".*").mock(return_value=httpx.Response(404))
        src = SiteSource(base, cache_dir=tmp_path / "mcp-cache")
        try:
            result = await core.diagnose_company(src, SAMSUNG.stock_code)
        finally:
            await src.aclose()
    assert result["status"] == "ok"
    assert result["latest_quarter"]["period_label"] == "2025년 7~9월"
