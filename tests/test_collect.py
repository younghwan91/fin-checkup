"""증분 수집 (collect.py) — 빈 곳만, 예산 안에서, 멈춰도 받은 건 남긴다.

Actions 가 매일 같은 인증키로 돌린다. 예산을 넘기거나 020 뒤에도 계속 때리면 그날 로컬·Pages 의
호출까지 막히고, 빈 응답을 매번 다시 물으면 예산이 '없는 회사' 에 다 쓰인다.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timedelta

import httpx
import pytest
import respx

from fin_checkup.cli import main
from fin_checkup.collect import STOP_BUDGET, STOP_ERRORS, STOP_QUOTA, STOP_TIME, collect
from fin_checkup.config import Settings, settings
from fin_checkup.models import (
    AccountLine,
    Company,
    CorpCode,
    FsDiv,
    Market,
    RawStatements,
    ReportCode,
)
from fin_checkup.service import CallBudgetExhausted, CheckupService
from fin_checkup.storage import Cache

BASE = "https://opendart.fss.or.kr/api"
STATEMENTS = f"{BASE}/fnlttSinglAcntAll.json"
OFF_SEASON = date(2026, 11, 22)
IN_SEASON = date(2026, 4, 10)

FOUND = {
    "status": "000",
    "list": [
        {
            "sj_div": "IS",
            "account_id": "ifrs-full_Revenue",
            "account_nm": "매출액",
            "thstrm_amount": "2,000",
            "ord": "1",
        }
    ],
}
NO_DATA = {"status": "013", "message": "조회된 데이타가 없습니다."}
QUOTA = {"status": "020", "message": "요청 제한을 초과하였습니다."}


@pytest.fixture(autouse=True)
def no_quarter_step(monkeypatch):
    """이 파일은 사업보고서 수집만 본다. 분기 주요계정 단계(호출 수가 달라진다)는 test_quarter.py 가 본다."""
    monkeypatch.setattr("fin_checkup.collect.interim_targets", lambda today: [])


@pytest.fixture
def cache(tmp_path):
    with Cache(tmp_path / "collect.duckdb") as c:
        yield c


def corp(i: int) -> CorpCode:
    return CorpCode(corp_code=f"0000000{i}", corp_name=f"회사{i}", stock_code=f"00000{i}")


def seed(cache: Cache, listed: int, delisted: int = 0) -> list[CorpCode]:
    corps = [corp(i) for i in range(listed + delisted)]
    cache.save_corp_codes(corps)
    for n, c in enumerate(corps):
        market = Market.KOSPI if n < listed else Market.OTHER
        cache.save_company(
            Company(corp_code=c.corp_code, corp_name=c.corp_name, stock_code=c.stock_code, market=market)
        )
    return corps[:listed]


def raw(c: CorpCode, year: int) -> RawStatements:
    return RawStatements(
        corp_code=c.corp_code,
        bsns_year=year,
        reprt_code=ReportCode.ANNUAL,
        fs_div=FsDiv.CFS,
        lines=[
            AccountLine(
                sj_div="IS", account_id="ifrs-full_Revenue", account_nm="매출액", thstrm_amount=1.0, ord=1
            )
        ],
    )


def age_misses(cache: Cache, days: float) -> None:
    cache.conn.execute("UPDATE fetch_misses SET fetched_at = ?", [datetime.now() - timedelta(days=days)])


@respx.mock
async def test_fetches_only_missing_listed_pairs(cache, test_settings: Settings):
    a, b = seed(cache, listed=2, delisted=1)
    cache.save_statements(raw(a, 2025))
    route = respx.get(STATEMENTS).mock(return_value=httpx.Response(200, json=FOUND))

    summary = await collect(
        cache, test_settings, budget=100, years=[2025, 2024], today=OFF_SEASON, progress=lambda _: None
    )

    # 상장사 2곳 × 2년 = 4조합 중 a·2025 는 이미 있다. 상장폐지 회사는 묻지 않는다.
    assert summary.listed == 2
    assert summary.missing == 3
    assert summary.fetched == 3
    assert route.call_count == 3 == summary.calls
    asked = {(r.request.url.params["corp_code"], r.request.url.params["bsns_year"]) for r in route.calls}
    assert asked == {(b.corp_code, "2025"), (a.corp_code, "2024"), (b.corp_code, "2024")}
    assert summary.stopped is None and summary.remaining == 0
    assert summary.new_data and summary.changed


@respx.mock
async def test_known_miss_within_ttl_is_skipped(cache, test_settings: Settings):
    (a,) = seed(cache, listed=1)
    for fs in (FsDiv.CFS, FsDiv.OFS):
        cache.record_miss(a.corp_code, 2025, ReportCode.ANNUAL, fs)
    age_misses(cache, days=5)
    route = respx.get(STATEMENTS).mock(return_value=httpx.Response(200, json=NO_DATA))

    summary = await collect(
        cache, test_settings, budget=100, years=[2025], today=OFF_SEASON, progress=lambda _: None
    )

    assert route.call_count == 0
    assert summary.skipped == 1 and summary.missing == 1
    assert summary.miss_ttl_days == 30
    assert not summary.changed, "아무것도 묻지 않았으면 스냅샷을 새로 올릴 이유가 없다"


@respx.mock
async def test_filing_season_rechecks_misses_after_three_days(cache, test_settings: Settings):
    (a,) = seed(cache, listed=1)
    for fs in (FsDiv.CFS, FsDiv.OFS):
        cache.record_miss(a.corp_code, 2025, ReportCode.ANNUAL, fs)
    age_misses(cache, days=5)
    route = respx.get(STATEMENTS).mock(return_value=httpx.Response(200, json=FOUND))

    summary = await collect(
        cache, test_settings, budget=100, years=[2025], today=IN_SEASON, progress=lambda _: None
    )

    assert summary.miss_ttl_days == 3
    assert route.call_count == 1
    assert summary.fetched == 1 and summary.skipped == 0


async def test_miss_ttl_settings_are_configurable(cache):
    s = Settings(dart_api_key="k", miss_ttl_days=10, miss_ttl_season_days=1)
    service = CheckupService(cache, s)
    assert service.miss_ttl(OFF_SEASON) == timedelta(days=10)
    assert service.miss_ttl(IN_SEASON) == timedelta(days=1)


@respx.mock
async def test_stops_before_the_budget_and_keeps_what_was_saved(cache, test_settings: Settings):
    seed(cache, listed=3)
    route = respx.get(STATEMENTS).mock(return_value=httpx.Response(200, json=NO_DATA))

    # 조합 하나에 CFS·OFS 2건. 5건이면 두 조합까지 — 세 번째는 시작하지 않는다.
    summary = await collect(
        cache, test_settings, budget=5, years=[2025], today=OFF_SEASON, progress=lambda _: None
    )

    assert summary.stopped == STOP_BUDGET
    assert route.call_count == 4 == summary.calls
    assert summary.empty == 2 and summary.remaining == 1
    assert cache.conn.execute("SELECT count(*) FROM fetch_misses").fetchone()[0] == 4


@respx.mock
async def test_budget_counts_todays_calls_against_the_daily_quota(cache):
    s = Settings(dart_api_key="test-key-0123456789", dart_min_delay=0.0, dart_daily_quota=10)
    seed(cache, listed=3)
    for _ in range(7):
        cache.record_dart_call("fnlttSinglAcntAll.json", OFF_SEASON.isoformat())
    respx.get(STATEMENTS).mock(return_value=httpx.Response(200, json=NO_DATA))

    summary = await collect(cache, s, budget=100, years=[2025], today=OFF_SEASON, progress=lambda _: None)

    assert summary.budget == 3
    assert summary.calls == 2 and summary.stopped == STOP_BUDGET


@respx.mock
async def test_quota_020_stops_without_retry(cache, test_settings: Settings):
    a, b, c = seed(cache, listed=3)
    route = respx.get(STATEMENTS).mock(
        side_effect=[httpx.Response(200, json=FOUND), httpx.Response(200, json=QUOTA)]
    )

    summary = await collect(
        cache, test_settings, budget=100, years=[2025], today=OFF_SEASON, progress=lambda _: None
    )

    assert summary.stopped == STOP_QUOTA
    assert route.call_count == 2, "020 뒤엔 한 건도 더 보내지 않는다"
    assert summary.fetched == 1 and summary.remaining == 2
    assert cache.get_statements(a.corp_code, 2025) is not None


@respx.mock
async def test_new_listing_gets_company_profile_first(cache, test_settings: Settings):
    seed(cache, listed=0)
    new, gone = corp(1), corp(2)
    cache.save_corp_codes([new, gone])
    respx.get(f"{BASE}/company.json", params={"corp_code": new.corp_code}).mock(
        return_value=httpx.Response(200, json={"status": "000", "corp_name": "회사1", "corp_cls": "K"})
    )
    respx.get(f"{BASE}/company.json", params={"corp_code": gone.corp_code}).mock(
        return_value=httpx.Response(200, json={"status": "000", "corp_name": "회사2", "corp_cls": "E"})
    )
    stmts = respx.get(STATEMENTS).mock(return_value=httpx.Response(200, json=FOUND))

    summary = await collect(
        cache, test_settings, budget=100, years=[2025], today=OFF_SEASON, progress=lambda _: None
    )

    assert summary.companies == 2
    assert summary.listed == 1
    assert [r.request.url.params["corp_code"] for r in stmts.calls] == [new.corp_code]
    assert summary.calls == 3


def corp_code_zip() -> bytes:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><result>'
        "<list><corp_code>00000000</corp_code><corp_name>회사0</corp_name>"
        "<stock_code>000000</stock_code><modify_date>20260101</modify_date></list></result>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml.encode("utf-8"))
    return buf.getvalue()


@respx.mock
async def test_refreshes_stale_corp_codes_within_budget(cache, test_settings: Settings):
    seed(cache, listed=1)
    cache.conn.execute("UPDATE corp_codes SET fetched_at = ?", [datetime.now() - timedelta(days=30)])
    sync = respx.get(f"{BASE}/corpCode.xml").mock(return_value=httpx.Response(200, content=corp_code_zip()))
    respx.get(STATEMENTS).mock(return_value=httpx.Response(200, json=FOUND))

    summary = await collect(
        cache, test_settings, budget=100, years=[2025], today=OFF_SEASON, progress=lambda _: None
    )

    assert sync.call_count == 1
    assert summary.corp_codes_refreshed == 1
    assert summary.calls == 2


@respx.mock
async def test_bad_key_stops_at_first_error(cache, test_settings: Settings):
    seed(cache, listed=3)
    route = respx.get(STATEMENTS).mock(
        return_value=httpx.Response(200, json={"status": "010", "message": "등록되지 않은 키입니다."})
    )

    summary = await collect(
        cache, test_settings, budget=100, years=[2025], today=OFF_SEASON, progress=lambda _: None
    )

    assert summary.stopped == STOP_ERRORS
    assert route.call_count == 1 and summary.failed == 1


async def test_time_limit_stops_between_pairs(cache, test_settings: Settings):
    seed(cache, listed=2)
    ticks = iter([0.0, 100.0])
    with respx.mock:
        summary = await collect(
            cache,
            test_settings,
            budget=100,
            years=[2025],
            today=OFF_SEASON,
            max_seconds=10,
            clock=lambda: next(ticks),
            progress=lambda _: None,
        )
    assert summary.stopped == STOP_TIME and summary.calls == 0 and summary.remaining == 2


@respx.mock
async def test_call_limit_blocks_the_request_itself(cache, test_settings: Settings):
    """사전 계산이 빗나가도(재시도 등) 예산을 넘는 요청은 보내지 않는다."""
    route = respx.get(STATEMENTS).mock(return_value=httpx.Response(200, json=NO_DATA))
    service = CheckupService(cache, test_settings)
    service.call_limit = 1
    with pytest.raises(CallBudgetExhausted):
        await service.get_financials("00000001", 2025)
    assert route.call_count == 1


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    path = tmp_path / "cli.duckdb"
    monkeypatch.setattr(settings, "fin_checkup_db_path", path)
    monkeypatch.setattr(settings, "dart_api_key", "test-key-0123456789")
    monkeypatch.setattr(settings, "dart_min_delay", 0.0)
    with Cache(path) as c:
        seed(c, listed=2)
    return path


@respx.mock
def test_cli_prints_machine_readable_summary_without_key(cli_db, tmp_path, capsys):
    respx.get(STATEMENTS).mock(side_effect=[httpx.Response(200, json=FOUND), httpx.Response(200, json=QUOTA)])
    out = tmp_path / "out" / "summary.json"

    code = main(["collect", "--budget", "50", "--years", "2025", "--summary-json", str(out)])

    assert code == 0, "020 으로 멈춰도 받은 건 남았으니 실패가 아니다"
    printed = capsys.readouterr().out
    assert "test-key-0123456789" not in printed
    last = printed.strip().splitlines()[-1]
    assert last.startswith("collect-summary ")
    data = json.loads(last.removeprefix("collect-summary "))
    assert data == json.loads(out.read_text(encoding="utf-8"))
    assert data["stopped"] == "quota" and data["fetched"] == 1 and data["calls"] == 2
    assert {"fetched", "missing", "empty", "skipped", "calls", "budget", "new_data", "changed"} <= set(data)


@respx.mock
def test_cli_exits_nonzero_when_errors_stop_collection(cli_db, capsys):
    respx.get(STATEMENTS).mock(
        return_value=httpx.Response(200, json={"status": "011", "message": "사용할 수 없는 키입니다."})
    )
    assert main(["collect", "--budget", "50", "--years", "2025"]) == 1
    assert "test-key-0123456789" not in capsys.readouterr().out
