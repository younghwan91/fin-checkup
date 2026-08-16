from __future__ import annotations

from datetime import timedelta

import pytest

from fin_checkup.models import (
    AccountLine,
    Company,
    CorpCode,
    FsDiv,
    Market,
    RawStatements,
    ReportCode,
)
from fin_checkup.storage import Cache


@pytest.fixture
def cache(tmp_path):
    with Cache(tmp_path / "t.duckdb") as c:
        yield c


def test_corp_codes_roundtrip_and_lookup(cache: Cache):
    cache.save_corp_codes(
        [
            CorpCode(corp_code="00126380", corp_name="삼성전자", stock_code="005930"),
            CorpCode(corp_code="00111722", corp_name="비상장", stock_code=""),
        ]
    )
    found = cache.find_by_stock_code("005930")
    assert found is not None and found.corp_name == "삼성전자"
    assert cache.find_by_stock_code("999999") is None


def test_search_by_name_returns_listed_only(cache: Cache):
    cache.save_corp_codes(
        [
            CorpCode(corp_code="1", corp_name="삼성전자", stock_code="005930"),
            CorpCode(corp_code="2", corp_name="삼성물산", stock_code="028260"),
            CorpCode(corp_code="3", corp_name="삼성비상장", stock_code=""),
        ]
    )
    names = [c.corp_name for c in cache.search_by_name("삼성")]
    assert "삼성비상장" not in names
    assert len(names) == 2


def test_save_corp_codes_replaces_previous_snapshot(cache: Cache):
    cache.save_corp_codes([CorpCode(corp_code="1", corp_name="옛이름", stock_code="000001")])
    cache.save_corp_codes([CorpCode(corp_code="1", corp_name="새이름", stock_code="000001")])
    found = cache.find_by_stock_code("000001")
    assert found is not None and found.corp_name == "새이름"


def test_corp_codes_age(cache: Cache):
    assert cache.corp_codes_age() is None
    cache.save_corp_codes([CorpCode(corp_code="1", corp_name="A", stock_code="000001")])
    age = cache.corp_codes_age()
    assert age is not None and age < timedelta(minutes=1)


def test_company_roundtrip(cache: Cache):
    cache.save_company(
        Company(
            corp_code="00126380", corp_name="삼성전자", stock_code="005930",
            market=Market.KOSPI, industry_code="264", fiscal_month=12,
        )
    )
    got = cache.get_company("00126380")
    assert got is not None
    assert got.market is Market.KOSPI
    assert got.industry_code == "264"


def raw(fs_div: FsDiv = FsDiv.CFS, amount: float = 1000.0) -> RawStatements:
    return RawStatements(
        corp_code="00126380", bsns_year=2024, reprt_code=ReportCode.ANNUAL, fs_div=fs_div,
        lines=[
            AccountLine(
                sj_div="BS", account_id="ifrs-full_Assets", account_nm="자산총계",
                thstrm_amount=amount, ord=1,
            )
        ],
    )


def test_statements_roundtrip(cache: Cache):
    cache.save_statements(raw())
    got = cache.get_statements("00126380", 2024)
    assert got is not None
    assert got.fs_div is FsDiv.CFS
    assert got.lines[0].thstrm_amount == 1000.0


def test_get_statements_prefers_cfs_then_falls_back_to_ofs(cache: Cache):
    cache.save_statements(raw(FsDiv.OFS, 777.0))
    got = cache.get_statements("00126380", 2024)
    assert got is not None and got.fs_div is FsDiv.OFS

    cache.save_statements(raw(FsDiv.CFS, 999.0))
    got = cache.get_statements("00126380", 2024)
    assert got is not None and got.fs_div is FsDiv.CFS


def test_resaving_statements_does_not_duplicate_rows(cache: Cache):
    cache.save_statements(raw())
    cache.save_statements(raw(amount=2000.0))
    got = cache.get_statements("00126380", 2024)
    assert got is not None
    assert len(got.lines) == 1
    assert got.lines[0].thstrm_amount == 2000.0


def test_miss_is_remembered_and_expires(cache: Cache):
    args = ("00126380", 2024, ReportCode.ANNUAL, FsDiv.CFS)
    assert cache.is_known_miss(*args) is False
    cache.record_miss(*args)
    assert cache.is_known_miss(*args) is True
    assert cache.is_known_miss(*args, ttl=timedelta(seconds=0)) is False


def test_miss_is_scoped_per_fs_div(cache: Cache):
    cache.record_miss("00126380", 2024, ReportCode.ANNUAL, FsDiv.CFS)
    assert cache.is_known_miss("00126380", 2024, ReportCode.ANNUAL, FsDiv.OFS) is False


# ── SEC 티커 (Phase 2) ────────────────────────────────────────────


def test_sec_tickers_roundtrip(cache: Cache):
    cache.save_sec_tickers([("320193", "AAPL", "Apple Inc."), ("789019", "MSFT", "MICROSOFT")])
    assert cache.find_cik("AAPL") == ("320193", "Apple Inc.")
    assert cache.find_cik("aapl") == ("320193", "Apple Inc."), "티커는 대소문자를 가리지 않는다"
    assert cache.find_cik("NOPE") is None


def test_one_cik_can_have_several_tickers(cache: Cache):
    # Alphabet은 GOOGL과 GOOG 두 티커가 같은 CIK를 쓴다.
    saved = cache.save_sec_tickers(
        [("1652044", "GOOGL", "Alphabet Inc."), ("1652044", "GOOG", "Alphabet Inc.")]
    )
    assert saved == 2
    assert cache.find_cik("GOOGL") == ("1652044", "Alphabet Inc.")
    assert cache.find_cik("GOOG") == ("1652044", "Alphabet Inc.")


def test_duplicate_ticker_keeps_the_last_one(cache: Cache):
    saved = cache.save_sec_tickers([("1", "AAPL", "옛이름"), ("2", "AAPL", "새이름")])
    assert saved == 1
    assert cache.find_cik("AAPL") == ("2", "새이름")


def test_saving_sec_tickers_replaces_the_previous_snapshot(cache: Cache):
    cache.save_sec_tickers([("1", "OLD", "옛회사")])
    cache.save_sec_tickers([("2", "NEW", "새회사")])
    assert cache.find_cik("OLD") is None
    assert cache.find_cik("NEW") is not None


# ── 대량 저장 (실데이터에서 유실이 발생해 추가) ────────────────────


def test_saving_a_realistic_corp_code_volume_is_complete_and_fast(cache: Cache):
    """DART 전체 목록은 11만 건이 넘는다.

    한 줄씩 넣던 구현은 5분을 넘겨 타임아웃에 잘렸고, 8,444건이 조용히 사라졌다.
    건수와 소요 시간을 함께 고정해 같은 일이 반복되지 않게 한다.
    """
    import time

    codes = [
        CorpCode(corp_code=f"{i:08d}", corp_name=f"회사{i}", stock_code=f"{i:06d}"[:6])
        for i in range(120_000)
    ]
    started = time.monotonic()
    saved = cache.save_corp_codes(codes)
    elapsed = time.monotonic() - started

    assert saved == 120_000, "저장 건수가 요청 건수와 일치해야 한다"
    assert cache.conn.execute("SELECT count(*) FROM corp_codes").fetchone()[0] == 120_000
    assert elapsed < 60, f"11만 건 저장이 {elapsed:.1f}초 걸렸다 — 타임아웃에 잘릴 수 있다"


def test_save_corp_codes_returns_stored_count_not_requested_count(cache: Cache):
    # 같은 corp_code가 두 번 오면 하나만 남고, 반환값도 실제 저장 건수여야 한다.
    dupes = [
        CorpCode(corp_code="00000001", corp_name="첫번째", stock_code="000001"),
        CorpCode(corp_code="00000001", corp_name="두번째", stock_code="000001"),
    ]
    assert cache.save_corp_codes(dupes) == 1
    found = cache.find_by_stock_code("000001")
    assert found is not None and found.corp_name == "두번째"


def test_bulk_insert_handles_a_chunk_boundary(cache: Cache):
    from fin_checkup.storage.db import BULK_CHUNK

    for count in (BULK_CHUNK - 1, BULK_CHUNK, BULK_CHUNK + 1):
        codes = [
            CorpCode(corp_code=f"{i:08d}", corp_name=f"회사{i}", stock_code=f"{i:06d}")
            for i in range(count)
        ]
        assert cache.save_corp_codes(codes) == count


def test_statement_lines_survive_a_large_filing(cache: Cache):
    # 전체 재무제표는 한 기업·한 해에 수백 줄이 나온다.
    lines = [
        AccountLine(sj_div="BS", account_id=f"tag{i}", account_nm=f"계정{i}",
                    thstrm_amount=float(i), ord=i)
        for i in range(900)
    ]
    raw = RawStatements(
        corp_code="00126380", bsns_year=2024, reprt_code=ReportCode.ANNUAL,
        fs_div=FsDiv.CFS, lines=lines,
    )
    cache.save_statements(raw)
    got = cache.get_statements("00126380", 2024)
    assert got is not None and len(got.lines) == 900
