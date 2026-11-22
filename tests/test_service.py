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


async def test_growth_and_deltas_use_this_years_prior_column_not_last_years_report(service, cache):
    """작년 보고서(개별)와 올해 보고서(연결)를 견주면 연결 범위가 다른 숫자끼리다.

    2024 상장사 2,404곳 중 83곳이 두 해의 연결/개별이 갈렸고, 같은 기준이어도 재작성으로
    전기 매출이 10% 넘게 달라진 곳이 57곳이었다. 올해 보고서의 전기 열은 올해와 같은 기준이다.
    """
    cache.save_statements(statements(2023, 500.0, fs_div=FsDiv.OFS))
    current = statements(2024, 1200.0)
    for ln in current.lines:
        ln.frmtrm_amount = 1000.0 if ln.account_id == "ifrs-full_Revenue" else ln.thstrm_amount
    cache.save_statements(current)
    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, years=3, allow_fetch=False)
    assert result is not None
    growth = result.result.by_key("revenue_growth")
    assert growth is not None and growth.value == pytest.approx(20.0)
    # 작년 영업이익률도 같은 전기 열로: 전기 영업이익 120 ÷ 전기 매출 1,000 (작년 개별 보고서는 10%).
    assert result.deltas["operating_margin"].previous == pytest.approx(12.0)


async def test_backfill_growth_needs_no_previous_report_when_the_prior_column_exists(service, cache):
    """2023 사업연도는 2022 보고서를 아직 안 모았어도 2023 보고서의 전기 열로 성장성을 낸다."""
    cache.save_company(_company("00126380"))
    current = statements(2024, 1200.0)
    for ln in current.lines:
        ln.frmtrm_amount = 1000.0 if ln.account_id == "ifrs-full_Revenue" else ln.thstrm_amount
    cache.save_statements(current)
    service.backfill_metric_values(2024)
    value = cache.conn.execute(
        "SELECT value FROM metric_values WHERE corp_code = ? AND bsns_year = 2024"
        " AND metric_key = 'revenue_growth'", ["00126380"],
    ).fetchone()
    assert value is not None and value[0] == pytest.approx(20.0)


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


def _company(corp_code: str, industry: str = "264", market=None):
    from fin_checkup.models import Company, Market

    return Company(corp_code=corp_code, corp_name=f"회사{corp_code}",
                   stock_code=corp_code.zfill(6), industry_code=industry,
                   market=market or Market.KOSPI)


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


async def test_unlisted_companies_are_not_counted_as_peers(service, cache):
    """'동종업계 상장사' 라고 적으면서 상장폐지·비상장 회사(market OTHER)를 세면 안 된다.

    실데이터 2024년에 재무가 있는 회사의 8%(217곳)가 OTHER 였다 — 업종·시장 대조군 둘 다에 섞였다.
    """
    from fin_checkup.models import Market

    cache.save_company(_company("00126380"))
    _seed_industry(cache, 6)
    for i in range(4):
        code = f"9500000{i}"
        cache.save_company(_company(code, market=Market.OTHER))
        raw = statements(2024, 5000.0 + i)
        raw.corp_code = code
        cache.save_statements(raw)
    cache.save_statements(statements(2024, 1000.0))
    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None
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


async def test_self_comparison_skips_not_applicable_metrics(service, cache):
    """⊘ 지표에 "작년 대비 개선/악화" 를 붙이면 판정하지 않겠다던 지표를 판정하게 된다."""
    from fin_checkup.models import Company

    # 보험사(금융업): 부채비율은 ⊘, 보험수익으로 낸 영업이익률은 판정한다.
    cache.save_company(Company(corp_code="00126380", corp_name="삼성화재", industry_code="65121"))
    cache.save_statements(statements(2023, 1000.0))
    cache.save_statements(statements(2024, 1200.0))
    result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None
    assert result.result.by_key("debt_ratio").signal.value == "not_applicable"
    assert "debt_ratio" not in result.deltas
    assert "operating_margin" in result.deltas, "판정하는 지표의 작년 대비는 그대로 둔다"


# ── 금융원가 대체값은 대조군에 섞지 않는다 ─────────────────────────────


def _with_finance_costs_only(year: int, revenue: float) -> RawStatements:
    raw = statements(year, revenue)
    raw.lines.append(AccountLine(sj_div="IS", account_id="ifrs-full_FinanceCosts",
                                 account_nm="금융원가", thstrm_amount=revenue, ord=6))
    return raw


def _with_interest_paid(year: int, revenue: float) -> RawStatements:
    raw = _with_finance_costs_only(year, revenue)
    raw.lines.append(AccountLine(
        sj_div="CF", account_id="ifrs-full_InterestPaidClassifiedAsOperatingActivities",
        account_nm="이자의 지급", thstrm_amount=revenue * 0.01, ord=7,
    ))
    return raw


async def test_backfill_leaves_finance_cost_proxy_out_of_peer_values(service, cache):
    """금융원가로 낸 이자보상배율은 외환손실 크기에 따라 출렁인다. 분포에 넣으면
    이자 지급액 기준 회사의 백분위가 엉뚱해진다."""
    cache.save_company(_company("00126380"))
    cache.save_statements(_with_finance_costs_only(2024, 1000.0))
    paid = _with_interest_paid(2024, 1000.0)
    paid.corp_code = "90000009"
    cache.save_company(_company("90000009"))
    cache.save_statements(paid)

    service.backfill_metric_values(2024)

    rows = dict(cache.conn.execute(
        "SELECT corp_code, value FROM metric_values WHERE metric_key = 'interest_coverage'"
    ).fetchall())
    assert "00126380" not in rows, "대체값이 대조군에 들어갔다"
    assert rows["90000009"] == pytest.approx(10.0)


async def test_proxy_metric_gets_no_peer_rank_or_self_delta(service, cache):
    cache.save_company(_company("00126380"))
    for i in range(6):
        code = f"9000000{i}"
        cache.save_company(_company(code))
        peer = _with_interest_paid(2024, 1000.0 + i * 100)
        peer.corp_code = code
        cache.save_statements(peer)
    cache.save_statements(_with_interest_paid(2023, 1000.0))
    cache.save_statements(_with_finance_costs_only(2024, 1000.0))
    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None
    m = result.result.by_key("interest_coverage")
    assert m.proxy is True
    assert "interest_coverage" not in result.peers
    assert "interest_coverage" not in result.deltas, "작년(이자 지급)과 올해(금융원가)는 분모가 다르다"


def _seed_market_in_bulk(cache, count: int, industry: str = "999") -> list[str]:
    """상장사 `count` 곳을 SQL 로 한 번에 심는다 — 하나씩 저장하면 수천 곳에 수십 초가 걸린다.

    대조군 모집단은 '재무가 있는 상장사' 이고 값은 미리 계산된 metric_values 에서 온다.
    """
    codes = [f"8{i:07d}" for i in range(count)]
    cache.conn.execute(
        "INSERT INTO companies (corp_code, corp_name, stock_code, market, industry_code, fetched_at) "
        "SELECT '8' || lpad(i::VARCHAR, 7, '0'), '회사' || i, lpad(i::VARCHAR, 6, '0'), 'KOSDAQ', ?, now() "
        "FROM range(?) t(i)",
        [industry, count],
    )
    cache.conn.execute(
        "INSERT INTO statement_lines (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id, "
        "account_nm, thstrm_amount, fetched_at) "
        "SELECT '8' || lpad(i::VARCHAR, 7, '0'), 2024, '11011', 'CFS', 'BS', 'ifrs-full_Assets', "
        "'자산총계', 1.0, now() FROM range(?) t(i)",
        [count],
    )
    cache.conn.execute(
        "INSERT INTO metric_values (corp_code, bsns_year, metric_key, value) "
        "SELECT '8' || lpad(i::VARCHAR, 7, '0'), 2024, 'debt_ratio', i::DOUBLE FROM range(?) t(i)",
        [count],
    )
    return codes


async def test_market_peer_group_is_not_capped(service, cache):
    """시장 전체 대조군을 앞의 2,000곳(corp_code 순)에서 조용히 잘랐다 — 상장사는 ~2,540곳.

    잘린 쪽은 corp_code 가 큰(늦게 등록된) 회사들이라 중앙값이 한쪽으로 치우친다.
    CLI·Streamlit·API·사이트가 모두 같은 함수를 쓴다.
    """
    from fin_checkup.metrics.peers import PeerScope

    count = 2_500
    _seed_market_in_bulk(cache, count)
    cache.save_company(_company("00126380", industry="264"))
    cache.save_statements(statements(2024, 1000.0))

    assert len(cache.list_with_statements(2024)) == count + 1
    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None
    stat = result.peers["debt_ratio"]
    assert stat.scope is PeerScope.MARKET
    assert stat.sample_size == count
    assert stat.median == (count - 1) / 2


async def test_not_applicable_values_stay_out_of_peer_pools(service, cache):
    """⊘ 값(증권사 영업이익률 — 영업수익이 평가·처분이익 총액)은 대조군에 넣지 않는다.

    같은 묶음(66)의 PG사나 시장 전체 분포에 섞이면 다른 회사의 대조군이 거짓말을 한다.
    """
    _seed_industry(cache, 6, industry="66121")  # 증권 중개업 → 은행·증권·금융지주
    general = _company("00126380", industry="66199")  # 같은 묶음 66 의 금융 지원 서비스
    cache.save_company(general)
    cache.save_statements(statements(2024, 1000.0))

    service.backfill_metric_values(2024)
    pooled = cache.peer_metric_values([f"9000000{i}" for i in range(6)], 2024)
    assert "operating_margin" not in pooled and "debt_ratio" not in pooled

    # 견줄 값이 증권사 ⊘ 뿐이면 대조군을 내지 않는다(업종도 시장 전체도).
    with respx.mock:
        result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None
    assert result.result.by_key("operating_margin").signal.value != "not_applicable"
    assert "operating_margin" not in result.peers


# ── '기타 금융업'(649) 비금융 지주는 재무제표로 일반 기준 (Q17) ─────────


def _classified(year: int, revenue: float) -> RawStatements:
    raw = statements(year, revenue)
    extra = [
        AccountLine(sj_div="BS", account_id="ifrs-full_CurrentAssets",
                    account_nm="유동자산", thstrm_amount=3000.0, ord=6),
        AccountLine(sj_div="BS", account_id="ifrs-full_CurrentLiabilities",
                    account_nm="유동부채", thstrm_amount=2000.0, ord=7),
    ]
    return raw.model_copy(update={"lines": [*raw.lines, *extra]})


async def test_649_holding_is_judged_on_its_statements_everywhere(service, cache):
    """화면(run)·대조군 값(backfill)이 같은 근거로 업종을 가른다 — 회사 정보만 보던 자리가 남으면
    같은 회사가 화면에선 일반, 대조군에선 금융업이 된다."""
    from fin_checkup.metrics.sector import Sector
    from fin_checkup.models import Company

    cache.save_company(Company(corp_code="00126380", corp_name="(주)효성", industry_code="649"))
    cache.save_statements(_classified(2023, 1000.0))
    cache.save_statements(_classified(2024, 1200.0))
    result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None and result.result.sector is Sector.GENERAL
    assert result.result.by_key("debt_ratio").signal.value != "not_applicable"
    assert "debt_ratio" in result.deltas

    service.backfill_metric_values(2024)
    stored = cache.peer_metric_values(["00126380"], 2024)
    assert "debt_ratio" in stored and "current_ratio" in stored


async def test_649_without_a_current_split_stays_financial(service, cache):
    from fin_checkup.metrics.sector import Sector
    from fin_checkup.models import Company

    cache.save_company(Company(corp_code="00126380", corp_name="한국캐피탈(주)", industry_code="649"))
    cache.save_statements(statements(2024, 1200.0))
    result = await service.run(SAMSUNG, end_year=2024, allow_fetch=False)
    assert result is not None and result.result.sector is Sector.FINANCIAL
    assert result.result.by_key("debt_ratio").signal.value == "not_applicable"
