from __future__ import annotations

import httpx
import pytest
import respx

from fin_checkup.config import Settings
from fin_checkup.models import AccountLine, CorpCode, FsDiv, RawStatements, ReportCode
from fin_checkup.service import CheckupService
from fin_checkup.storage import Cache

BASE = "https://opendart.fss.or.kr/api"


@pytest.fixture
def cache(tmp_path):
    with Cache(tmp_path / "svc.duckdb") as c:
        yield c


@pytest.fixture
def service(cache, test_settings: Settings) -> CheckupService:
    return CheckupService(cache, settings=test_settings)


def statements(year: int, revenue: float, fs_div: FsDiv = FsDiv.CFS) -> RawStatements:
    return RawStatements(
        corp_code="00126380", bsns_year=year, reprt_code=ReportCode.ANNUAL, fs_div=fs_div,
        lines=[
            AccountLine(sj_div="BS", account_id="ifrs-full_Assets",
                        account_nm="자산총계", thstrm_amount=10000.0, ord=1),
            AccountLine(sj_div="BS", account_id="ifrs-full_Equity",
                        account_nm="자본총계", thstrm_amount=6000.0, ord=2),
            AccountLine(sj_div="BS", account_id="ifrs-full_Liabilities",
                        account_nm="부채총계", thstrm_amount=4000.0, ord=3),
            AccountLine(sj_div="IS", account_id="ifrs-full_Revenue",
                        account_nm="매출액", thstrm_amount=revenue, ord=4),
            AccountLine(sj_div="IS", account_id="dart_OperatingIncomeLoss",
                        account_nm="영업이익", thstrm_amount=revenue * 0.1, ord=5),
        ],
    )


SAMSUNG = CorpCode(corp_code="00126380", corp_name="삼성전자", stock_code="005930")


async def test_get_financials_reads_cache_without_network(service, cache):
    cache.save_statements(statements(2024, 1000.0))
    with respx.mock:  # 라우트를 하나도 등록하지 않음 = 네트워크 호출 시 실패
        fin = await service.get_financials("00126380", 2024)
    assert fin is not None
    assert fin.revenue == 1000.0


async def test_allow_fetch_false_returns_none_instead_of_calling_dart(service):
    with respx.mock:
        assert await service.get_financials("00126380", 2024, allow_fetch=False) is None


@respx.mock
async def test_fetch_populates_cache_so_second_call_is_offline(service, cache):
    route = respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "000",
                "list": [
                    {"sj_div": "IS", "account_id": "ifrs-full_Revenue",
                     "account_nm": "매출액", "thstrm_amount": "2,000", "ord": "1"}
                ],
            },
        )
    )
    first = await service.get_financials("00126380", 2024)
    assert first is not None and first.revenue == 2000.0
    assert route.call_count == 1

    second = await service.get_financials("00126380", 2024)
    assert second is not None and second.revenue == 2000.0
    assert route.call_count == 1, "두 번째 조회는 캐시에서 나와야 한다"


@respx.mock
async def test_known_miss_is_not_refetched(service):
    route = respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(200, json={"status": "013", "message": "없음"})
    )
    assert await service.get_financials("00126380", 2024) is None
    assert route.call_count == 2  # CFS, OFS 각 1회

    assert await service.get_financials("00126380", 2024) is None
    assert route.call_count == 2, "빈 응답을 기억해 재조회하지 않아야 한다"


async def test_search_by_six_digit_code(service, cache):
    cache.save_corp_codes([SAMSUNG])
    found = service.search("005930")
    assert len(found) == 1 and found[0].corp_name == "삼성전자"


async def test_search_by_name(service, cache):
    cache.save_corp_codes([SAMSUNG])
    assert [c.corp_name for c in service.search("삼성")] == ["삼성전자"]


async def test_search_empty_keyword(service):
    assert service.search("   ") == []


async def test_run_builds_checkup_with_growth_from_history(service, cache):
    cache.save_statements(statements(2023, 1000.0))
    cache.save_statements(statements(2024, 1200.0))
    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, years=3, allow_fetch=False)
    assert result is not None
    assert [f.bsns_year for f in result.history] == [2023, 2024]
    growth = result.result.by_key("revenue_growth")
    assert growth is not None and growth.value == pytest.approx(20.0)


async def test_run_returns_none_when_no_data_at_all(service):
    with respx.mock:
        assert await service.run(SAMSUNG, end_year=2024, allow_fetch=False) is None


async def test_history_does_not_go_below_2015(service, cache):
    # DART 전체 재무제표 제공 시작 연도 이전은 조회하지 않는다.
    cache.save_statements(statements(2015, 500.0))
    with respx.mock:
        history = await service.load_history("00126380", 2016, years=10, allow_fetch=False)
    assert [f.bsns_year for f in history] == [2015]


async def test_no_api_key_degrades_to_cache_only(cache):
    service = CheckupService(cache, settings=Settings(dart_api_key=""))
    cache.save_statements(statements(2024, 1000.0))
    with respx.mock:
        fin = await service.get_financials("00126380", 2024)
    assert fin is not None
    # 캐시에 없는 연도는 키가 없으니 그냥 None
    with respx.mock:
        assert await service.get_financials("00126380", 2023) is None


async def test_ensure_corp_codes_skips_when_fresh(service, cache):
    cache.save_corp_codes([SAMSUNG])
    with respx.mock:
        assert await service.ensure_corp_codes() == 0


# ── 업종 비교 ─────────────────────────────────────────────────────


def _company(corp_code: str, industry: str = "264"):
    from fin_checkup.models import Company

    return Company(corp_code=corp_code, corp_name=f"회사{corp_code}",
                   stock_code=corp_code.zfill(6), industry_code=industry)


def _seed_industry(cache, count: int, industry: str = "264") -> None:
    for i in range(count):
        code = f"9000000{i}"
        cache.save_company(_company(code, industry))
        raw = statements(2024, 1000.0 + i * 100)
        raw.corp_code = code
        cache.save_statements(raw)


async def test_peer_comparison_needs_enough_cached_peers(service, cache):
    cache.save_company(_company("00126380"))
    _seed_industry(cache, 2)
    cache.save_statements(statements(2024, 1000.0))
    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None
    assert result.peers == {}, "표본이 모자라면 업종 비교를 만들지 않는다"


async def test_peer_comparison_is_built_with_enough_peers(service, cache):
    cache.save_company(_company("00126380"))
    _seed_industry(cache, 6)
    cache.save_statements(statements(2024, 1000.0))
    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None
    assert "debt_ratio" in result.peers
    assert result.peers["debt_ratio"].sample_size == 6


async def test_other_industries_fall_back_to_the_market_not_counted_as_peers(service, cache):
    """다른 업종 기업을 동종업계로 세면 안 된다.

    다만 아무것도 안 보여주는 것도 답이 아니다 — 실측에서 절반의 기업이 업종
    표본을 못 채웠다. 업종이 안 되면 전체 상장사로 넓히되, 무엇과 견줬는지 밝힌다.
    """
    from fin_checkup.metrics.peers import PeerScope

    cache.save_company(_company("00126380", industry="264"))
    _seed_industry(cache, 6, industry="999")
    cache.save_statements(statements(2024, 1000.0))
    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None
    assert result.peers, "업종이 안 맞아도 시장 전체로는 견줄 수 있다"
    assert all(s.scope is PeerScope.MARKET for s in result.peers.values())


async def test_industry_grouping_uses_the_two_digit_prefix(service, cache):
    """5자리 코드로 정확히 맞추면 업종이 318개로 쪼개져 절반이 표본을 못 채운다."""
    from fin_checkup.metrics.peers import PeerScope

    cache.save_company(_company("00126380", industry="26410"))
    # 앞 두 자리만 같고 뒤가 다른 기업들 — 같은 중분류로 묶여야 한다.
    for i in range(6):
        code = f"9100000{i}"
        cache.save_company(_company(code, industry=f"264{i}0"))
        raw = statements(2024, 1000.0 + i * 50)
        raw.corp_code = code
        cache.save_statements(raw)
    cache.save_statements(statements(2024, 1000.0))

    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None
    assert result.peers["debt_ratio"].scope is PeerScope.INDUSTRY


async def test_peer_comparison_is_empty_without_company_info(service, cache):
    _seed_industry(cache, 6)
    cache.save_statements(statements(2024, 1000.0))
    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None
    assert result.peers == {}


# ── 대조군 성능 (실측에서 16초가 나와 추가) ───────────────────────


async def test_backfill_precomputes_metric_values(service, cache):
    _seed_industry(cache, 6)
    cache.save_company(_company("00126380"))
    cache.save_statements(statements(2024, 1000.0))

    assert cache.metric_values_count(2024) == 0
    done = service.backfill_metric_values(2024)
    assert done == 7
    assert cache.metric_values_count(2024) == 7


async def test_peer_values_come_from_the_precomputed_table(service, cache, monkeypatch):
    """채워둔 뒤에는 원본을 다시 파싱하지 않아야 한다."""
    _seed_industry(cache, 6)
    cache.save_company(_company("00126380"))
    cache.save_statements(statements(2024, 1000.0))
    service.backfill_metric_values(2024)

    def boom(*_args, **_kwargs):
        raise AssertionError("미리 계산해둔 값이 있는데 원본을 다시 읽었다")

    monkeypatch.setattr(cache, "get_statements_bulk", boom)
    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None
    assert result.peers


async def test_peer_comparison_still_works_without_backfill(service, cache):
    """아직 안 채운 캐시에서도 동작해야 한다 — 느릴 뿐이지 틀리면 안 된다."""
    _seed_industry(cache, 6)
    cache.save_company(_company("00126380"))
    cache.save_statements(statements(2024, 1000.0))

    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None
    assert "debt_ratio" in result.peers


async def test_bulk_fetch_matches_one_by_one(cache):
    """한 번에 가져온 결과가 한 곳씩 가져온 것과 같아야 한다."""
    _seed_industry(cache, 4)
    codes = [f"9000000{i}" for i in range(4)]

    bulk = cache.get_statements_bulk(codes, 2024)
    for code in codes:
        one = cache.get_statements(code, 2024)
        assert one is not None
        assert bulk[code].fs_div == one.fs_div
        assert len(bulk[code].lines) == len(one.lines)
        assert [ln.account_nm for ln in bulk[code].lines] == [
            ln.account_nm for ln in one.lines
        ]


async def test_bulk_fetch_prefers_consolidated(cache):
    from fin_checkup.models import FsDiv

    ofs = statements(2024, 500.0, FsDiv.OFS)
    ofs.corp_code = "77777777"
    cache.save_statements(ofs)
    cfs = statements(2024, 999.0, FsDiv.CFS)
    cfs.corp_code = "77777777"
    cache.save_statements(cfs)

    got = cache.get_statements_bulk(["77777777"], 2024)
    assert got["77777777"].fs_div is FsDiv.CFS


async def test_bulk_fetch_with_no_codes(cache):
    assert cache.get_statements_bulk([], 2024) == {}


async def test_backfill_includes_growth_metrics(service, cache):
    """전년을 안 넘기면 성장성 지표가 통째로 빠진다.

    화면에서 성장성 카드만 대조군 없이 휑하게 비는 걸로 드러났다.
    """
    cache.save_company(_company("00126380"))
    cache.save_statements(statements(2023, 1000.0))
    cache.save_statements(statements(2024, 1200.0))
    service.backfill_metric_values(2024)

    keys = {
        r[0]
        for r in cache.conn.execute(
            "SELECT metric_key FROM metric_values WHERE corp_code = ? AND bsns_year = 2024",
            ["00126380"],
        ).fetchall()
    }
    assert "revenue_growth" in keys, "전년이 있는데 성장성이 안 채워졌다"
    assert "operating_margin" in keys
