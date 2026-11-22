from __future__ import annotations

import functools
import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from types import TracebackType

import duckdb

from fin_checkup.clock import kst_today
from fin_checkup.models import (
    LISTED_MARKETS,
    AccountLine,
    Company,
    CorpCode,
    FsDiv,
    Market,
    RawStatements,
    ReportCode,
)

logger = logging.getLogger(__name__)

#: `companies.market` 이 상장 시장인지. 앞에 테이블 별칭을 붙여 쓸 수 있게 `market` 으로 시작한다.
#: 값은 코드 상수(Market)라 SQL 에 그대로 넣어도 된다.
_LISTED_SQL = "market IN ({})".format(", ".join(f"'{m.value}'" for m in LISTED_MARKETS))


def today_key(when: date | None = None) -> str:
    """날짜로 나뉜 테이블(dart_call_log 등)의 키. DART 일별 한도는 한국 날짜로 돈다."""
    return (when or kst_today()).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS corp_codes (
    corp_code   VARCHAR PRIMARY KEY,
    corp_name   VARCHAR NOT NULL,
    stock_code  VARCHAR,
    modify_date VARCHAR,
    fetched_at  TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_corp_codes_stock ON corp_codes(stock_code);

CREATE TABLE IF NOT EXISTS companies (
    corp_code        VARCHAR PRIMARY KEY,
    corp_name        VARCHAR NOT NULL,
    stock_code       VARCHAR,
    market           VARCHAR,
    industry_code    VARCHAR,
    ceo_name         VARCHAR,
    established_date VARCHAR,
    fiscal_month     INTEGER,
    homepage         VARCHAR,
    fetched_at       TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS statement_lines (
    corp_code        VARCHAR NOT NULL,
    bsns_year        INTEGER NOT NULL,
    reprt_code       VARCHAR NOT NULL,
    fs_div           VARCHAR NOT NULL,
    sj_div           VARCHAR NOT NULL,
    account_id       VARCHAR NOT NULL,
    account_nm       VARCHAR NOT NULL,
    thstrm_amount    DOUBLE,
    frmtrm_amount    DOUBLE,
    bfefrmtrm_amount DOUBLE,
    currency         VARCHAR,
    ord              INTEGER,
    fetched_at       TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lines_lookup
    ON statement_lines(corp_code, bsns_year, reprt_code, fs_div);

-- SEC EDGAR 티커 → CIK 매핑 (Phase 2)
-- 기본키는 ticker다. CIK 하나에 티커가 여럿 달릴 수 있다(Alphabet의 GOOGL/GOOG).
CREATE TABLE IF NOT EXISTS sec_tickers (
    ticker     VARCHAR PRIMARY KEY,
    cik        VARCHAR NOT NULL,
    title      VARCHAR NOT NULL,
    fetched_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sec_tickers_cik ON sec_tickers(cik);

-- 관심종목. chat_id는 알림을 받을 대상(텔레그램 채팅방 등).
CREATE TABLE IF NOT EXISTS watchlist (
    chat_id    VARCHAR NOT NULL,
    corp_code  VARCHAR NOT NULL,
    corp_name  VARCHAR NOT NULL,
    stock_code VARCHAR,
    added_at   TIMESTAMP NOT NULL,
    PRIMARY KEY (chat_id, corp_code)
);

-- 이미 보낸 알림. 같은 공시를 두 번 보내지 않기 위한 기록.
CREATE TABLE IF NOT EXISTS notified (
    chat_id     VARCHAR NOT NULL,
    rcept_no    VARCHAR NOT NULL,
    notified_at TIMESTAMP NOT NULL,
    PRIMARY KEY (chat_id, rcept_no)
);

-- 계정. 비밀번호는 받지 않는다 — 저장·재설정·유출 대응을 떠안지 않기 위해서.
CREATE TABLE IF NOT EXISTS accounts (
    user_id    VARCHAR PRIMARY KEY,
    email      VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email);

-- API 키. 원문은 저장하지 않고 해시만 둔다. DB가 새어도 남의 키를 쓸 수 없어야 한다.
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash     VARCHAR PRIMARY KEY,
    user_id      VARCHAR NOT NULL,
    prefix       VARCHAR NOT NULL,
    created_at   TIMESTAMP NOT NULL,
    last_used_at TIMESTAMP,
    revoked_at   TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);

-- 계산된 지표 값. 대조군 집계를 위한 파생 테이블이다.
--
-- 원본(statement_lines)에서 매번 다시 계산하면 대조군 한 번에 12만 줄을 파싱하게
-- 되고 실제로 검진 한 건이 16초 걸렸다. 재무는 분기에 한 번 바뀌는데 매 조회마다
-- 재계산할 이유가 없다. 원본은 그대로 두므로 계산 규칙이 바뀌면 다시 채우면 된다.
CREATE TABLE IF NOT EXISTS metric_values (
    corp_code  VARCHAR NOT NULL,
    bsns_year  INTEGER NOT NULL,
    metric_key VARCHAR NOT NULL,
    value      DOUBLE NOT NULL,
    PRIMARY KEY (corp_code, bsns_year, metric_key)
);
CREATE INDEX IF NOT EXISTS idx_metric_values_lookup ON metric_values(bsns_year, metric_key);

-- 운영 상태 저장소. 마지막 폴링 시각처럼 한 줄짜리 값을 담는다.
CREATE TABLE IF NOT EXISTS meta (
    key        VARCHAR PRIMARY KEY,
    value      VARCHAR NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

-- DART 일별 호출량. 약관 제10조의 허용량 안에서 움직이는지 스스로 알기 위한 것.
CREATE TABLE IF NOT EXISTS dart_call_log (
    day      VARCHAR NOT NULL,
    endpoint VARCHAR NOT NULL,
    count    INTEGER NOT NULL,
    PRIMARY KEY (day, endpoint)
);

-- 조회했으나 데이터가 없었던 조합. 같은 요청을 매번 다시 쏘지 않기 위해 기록한다.
CREATE TABLE IF NOT EXISTS fetch_misses (
    corp_code  VARCHAR NOT NULL,
    bsns_year  INTEGER NOT NULL,
    reprt_code VARCHAR NOT NULL,
    fs_div     VARCHAR NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (corp_code, bsns_year, reprt_code, fs_div)
);
"""


#: 한 INSERT 문에 담을 행 수. corp_code는 11만 행이라 한 줄씩 넣으면 5분이 넘는다.
BULK_CHUNK = 2000

#: 금액 단위 지표. 대조군은 원화라 외화 공시 기업의 이 값들은 섞지 않는다.
#: (metrics.engine 의 MONEY 지표와 같아야 한다. storage 가 metrics 를 import 하지 않으려고 적어둔다.)
_MONEY_METRIC_KEYS = ("operating_cash_flow", "fcf")
_MIGRATION_FOREIGN_MONEY = "migrations.metric_values_foreign_money"
#: 이자보상배율은 2026-11 까지 이자비용이 없으면 금융원가로 계산돼 대조군에 들어갔다(2024 상장사 94%).
#: 이제 금융원가 대체값은 대조군에서 빠지므로 옛 값을 지우고 `cli backfill` 로 다시 채운다.
_MIGRATION_INTEREST_SOURCE = "migrations.metric_values_interest_source"
#: 매출채권회전율은 2026-11 까지 합계 태그(TradeAndOtherCurrentReceivables)를 먼저 잡아, 그 태그를
#: 미수금 줄('기타수취채권' 등)에 붙인 회사는 미수금으로 나눈 값이 대조군에 들어갔다.
_MIGRATION_RECEIVABLE_SOURCE = "migrations.metric_values_receivable_source"
#: FCF 는 2026-11 까지 유형자산 취득을 종류별로만 적은 회사의 '기타유형자산의취득' 한 줄을
#: 설비투자로 썼다(2024 상장사 56곳). 종류별 합으로 고친 값으로 다시 채운다.
_MIGRATION_CAPEX_SOURCE = "migrations.metric_values_capex_source"


class CacheLocked(RuntimeError):
    """다른 프로세스가 캐시를 쓰고 있다.

    DuckDB는 한 번에 한 프로세스만 쓸 수 있다. 알림 워커와 웹 앱을 따로 띄우면
    반드시 부딪힌다 — 운영에서는 한 프로세스가 DB를 소유하고(FastAPI 서버),
    워커를 그 안의 백그라운드 작업으로 돌리거나 Postgres로 옮겨야 한다.
    """


class Cache:
    """DuckDB 캐시. `with Cache(path) as cache:` 로 쓴다.

    read_only=True로 열면 쓰기를 하는 프로세스가 없을 때 여러 곳에서 함께 읽을 수 있다.
    """

    def _bulk_insert(self, table: str, rows: list[tuple], columns: int) -> int:
        """여러 행을 한 INSERT 문으로 묶어 넣는다.

        executemany는 행마다 왕복해서 11만 행에 수 분이 걸린다. 값 목록을 묶으면
        문장 수가 수만 개에서 수십 개로 줄어 같은 일이 초 단위로 끝난다.
        """
        if not rows:
            return 0
        placeholder = "(" + ", ".join(["?"] * columns) + ")"
        inserted = 0
        for start in range(0, len(rows), BULK_CHUNK):
            chunk = rows[start : start + BULK_CHUNK]
            values = ", ".join([placeholder] * len(chunk))
            flat: list = [field for row in chunk for field in row]
            self.conn.execute(f"INSERT INTO {table} VALUES {values}", flat)
            inserted += len(chunk)
        return inserted

    def __init__(self, db_path: Path | str, read_only: bool = False) -> None:
        self.db_path = Path(db_path)
        self.read_only = read_only
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._root = duckdb.connect(str(self.db_path), read_only=read_only)
        except duckdb.IOException as exc:
            if "lock" not in str(exc).lower():
                raise
            raise CacheLocked(
                f"캐시({self.db_path})를 다른 프로세스가 쓰고 있습니다.\n"
                "  DuckDB는 한 번에 한 프로세스만 쓸 수 있습니다. "
                "실행 중인 워커나 Streamlit을 먼저 종료하세요."
            ) from exc
        self._owner_thread = threading.get_ident()
        self._local = threading.local()
        # 메서드 단위 직렬화. 복제 연결끼리도 같은 행을 동시에 고치면 DuckDB 가
        # 'Conflict on update' 를 던진다(호출량 집계·last_used_at 처럼 매 요청이 건드리는 행).
        self._lock = threading.RLock()
        self._cursors: list[duckdb.DuckDBPyConnection] = []
        self._cursors_lock = threading.Lock()
        if not read_only:
            self._root.execute(SCHEMA)
            self._migrate()

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        """현재 스레드 전용 연결.

        DuckDB 연결 하나를 여러 스레드가 나눠 쓰면 `execute()` 뒤의 `fetchone()`이
        다른 스레드의 결과를 집어온다 — 예외도 없이 조용히 틀린다. FastAPI 가 동기
        엔드포인트를 스레드풀에서 돌리므로 요청 두 개가 겹치면 A 의 키로 B 의 user_id 가
        돌아오는 일이 실제로 났다. 연결을 만든 스레드는 원본을, 다른 스레드는 각자
        `cursor()` 로 복제한 연결을 쓴다. 같은 DB 인스턴스를 공유하므로 쓰기는 서로 보인다.
        """
        if threading.get_ident() == self._owner_thread:
            return self._root
        cursor = getattr(self._local, "cursor", None)
        if cursor is None:
            cursor = self._root.cursor()
            self._local.cursor = cursor
            with self._cursors_lock:
                self._cursors.append(cursor)
        return cursor

    def _migrate(self) -> None:
        """스키마 밖의 데이터 정정. 한 번 적용하면 meta 에 표시해 다시 돌지 않는다."""
        if self.get_meta(_MIGRATION_FOREIGN_MONEY) is None:
            # 외화로 공시하는 기업의 금액 지표가 원화 대조군에 섞여 있었다. 백필이 이제는
            # 이런 행을 만들지 않지만, 이미 채워진 캐시에는 남아 있어 지워야 효과가 난다.
            # 지운 기업은 다음 backfill 에서 비율 지표만 다시 채워진다.
            placeholders = ", ".join("?" for _ in _MONEY_METRIC_KEYS)
            self.conn.execute(
                f"DELETE FROM metric_values WHERE metric_key IN ({placeholders}) AND corp_code IN ("
                "  SELECT DISTINCT corp_code FROM statement_lines"
                "  WHERE currency IS NOT NULL AND currency <> 'KRW')",
                list(_MONEY_METRIC_KEYS),
            )
            self.set_meta(_MIGRATION_FOREIGN_MONEY, "done")
        if self.get_meta(_MIGRATION_INTEREST_SOURCE) is None:
            # 옛 값 대부분이 외환손실 섞인 금융원가 기준이다. 남겨 두면 고친 값을 옛 분포에 견준다.
            # 지운 뒤 backfill 전까지는 이 지표의 대조군이 비어 있다(틀린 분포보다 낫다).
            self.conn.execute("DELETE FROM metric_values WHERE metric_key = 'interest_coverage'")
            self.set_meta(_MIGRATION_INTEREST_SOURCE, "done")
        if self.get_meta(_MIGRATION_RECEIVABLE_SOURCE) is None:
            self.conn.execute("DELETE FROM metric_values WHERE metric_key = 'receivable_turnover'")
            self.set_meta(_MIGRATION_RECEIVABLE_SOURCE, "done")
        if self.get_meta(_MIGRATION_CAPEX_SOURCE) is None:
            self.conn.execute("DELETE FROM metric_values WHERE metric_key = 'fcf'")
            self.set_meta(_MIGRATION_CAPEX_SOURCE, "done")

    def __enter__(self) -> Cache:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        # 복제 연결이 하나라도 살아 있으면 파일 락이 풀리지 않는다. 전부 닫는다.
        with self._cursors_lock:
            cursors, self._cursors = self._cursors, []
        for cursor in cursors:
            try:
                cursor.close()
            except duckdb.Error:  # 이미 닫혔거나 스레드가 사라진 경우
                pass
        self._root.close()

    # ------------------------------------------------------------------
    # corp_code
    # ------------------------------------------------------------------

    def save_corp_codes(self, codes: list[CorpCode]) -> int:
        """전체 목록을 갈아끼운다. 같은 corp_code가 두 번 오면 뒤가 이긴다.

        반환값은 실제로 저장된 행 수다. 요청 건수를 그대로 돌려주면 중간에 유실돼도
        성공한 것처럼 보인다.
        """
        now = datetime.now()
        deduped = {
            c.corp_code: (c.corp_code, c.corp_name, c.stock_code, c.modify_date, now)
            for c in codes
            if c.corp_code
        }
        self.conn.execute("DELETE FROM corp_codes")
        self._bulk_insert("corp_codes", list(deduped.values()), columns=5)
        return self.count_corp_codes()

    def count_corp_codes(self) -> int:
        return int(self.conn.execute("SELECT count(*) FROM corp_codes").fetchone()[0])

    def corp_codes_age(self) -> timedelta | None:
        """마지막 갱신으로부터 지난 시간. 비어 있으면 None."""
        row = self.conn.execute("SELECT max(fetched_at) FROM corp_codes").fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.now() - row[0]

    def find_by_stock_code(self, stock_code: str) -> CorpCode | None:
        row = self.conn.execute(
            "SELECT corp_code, corp_name, stock_code, modify_date "
            "FROM corp_codes WHERE stock_code = ?",
            [stock_code.strip()],
        ).fetchone()
        return None if row is None else CorpCode(**dict(zip(_CORP_FIELDS, row, strict=True)))

    def search_by_name(self, keyword: str, limit: int = 20) -> list[CorpCode]:
        """상장기업만, 이름 부분일치로 검색."""
        rows = self.conn.execute(
            "SELECT corp_code, corp_name, stock_code, modify_date FROM corp_codes "
            "WHERE stock_code <> '' AND corp_name ILIKE ? "
            "ORDER BY length(corp_name), corp_name LIMIT ?",
            [f"%{keyword.strip()}%", limit],
        ).fetchall()
        return [CorpCode(**dict(zip(_CORP_FIELDS, r, strict=True))) for r in rows]

    def listed_corp_codes(self) -> list[CorpCode]:
        """종목코드가 있는 회사 전부, 이름순.

        corpCode.xml 은 상장폐지 회사의 옛 코드도 남긴다 — 상장 여부는 모른다.
        """
        rows = self.conn.execute(
            "SELECT corp_code, corp_name, stock_code, modify_date FROM corp_codes "
            "WHERE stock_code <> '' ORDER BY corp_name"
        ).fetchall()
        return [CorpCode(**dict(zip(_CORP_FIELDS, r, strict=True))) for r in rows]

    def statement_years(self) -> list[int]:
        """재무제표가 캐시에 있는 사업연도들, 오름차순."""
        rows = self.conn.execute("SELECT DISTINCT bsns_year FROM statement_lines ORDER BY 1").fetchall()
        return [int(r[0]) for r in rows]

    # ------------------------------------------------------------------
    # 기업개황
    # ------------------------------------------------------------------

    def save_company(self, company: Company) -> None:
        self.conn.execute("DELETE FROM companies WHERE corp_code = ?", [company.corp_code])
        self.conn.execute(
            "INSERT INTO companies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                company.corp_code,
                company.corp_name,
                company.stock_code,
                company.market.value if company.market else None,
                company.industry_code,
                company.ceo_name,
                company.established_date,
                company.fiscal_month,
                company.homepage,
                datetime.now(),
            ],
        )

    def get_company(self, corp_code: str) -> Company | None:
        row = self.conn.execute(
            "SELECT corp_code, corp_name, stock_code, market, industry_code, ceo_name, "
            "established_date, fiscal_month, homepage FROM companies WHERE corp_code = ?",
            [corp_code],
        ).fetchone()
        if row is None:
            return None
        return Company(
            corp_code=row[0],
            corp_name=row[1],
            stock_code=row[2] or "",
            market=Market(row[3]) if row[3] else None,
            industry_code=row[4] or "",
            ceo_name=row[5] or "",
            established_date=row[6] or "",
            fiscal_month=row[7] or 12,
            homepage=row[8] or "",
        )

    def list_by_industry(self, industry_prefix: str) -> list[str]:
        """업종 그룹에 속한 **상장** 기업의 corp_code 목록 (동종업계 대조군).

        접두사로 찾는다 — 5자리 원본 코드로 정확히 맞추면 업종이 318개로 쪼개져
        절반이 비교 표본을 못 채운다(실측).
        """
        prefix = (industry_prefix or "").strip()
        if not prefix:
            return []
        rows = self.conn.execute(
            f"SELECT corp_code FROM companies WHERE industry_code LIKE ? AND {_LISTED_SQL} "
            "ORDER BY corp_code",
            [f"{prefix}%"],
        ).fetchall()
        return [r[0] for r in rows]

    def get_statements_bulk(
        self,
        corp_codes: list[str],
        bsns_year: int,
        reprt_code: ReportCode = ReportCode.ANNUAL,
    ) -> dict[str, RawStatements]:
        """여러 기업의 재무제표를 한 번의 쿼리로.

        대조군은 수백 개사를 한꺼번에 본다. 한 곳씩 꺼내면 왕복이 수백 번이 되고
        실제로 16초가 걸렸다. 기업당 연결(CFS)을 우선하고 없는 곳만 개별(OFS)을 쓴다.
        """
        if not corp_codes:
            return {}

        placeholders = ", ".join(["?"] * len(corp_codes))
        rows = self.conn.execute(
            f"SELECT corp_code, fs_div, sj_div, account_id, account_nm, thstrm_amount, "
            f"frmtrm_amount, bfefrmtrm_amount, currency, ord FROM statement_lines "
            f"WHERE bsns_year = ? AND reprt_code = ? AND corp_code IN ({placeholders}) "
            f"ORDER BY corp_code, ord",
            [bsns_year, reprt_code.value, *corp_codes],
        ).fetchall()

        grouped: dict[tuple[str, str], list[AccountLine]] = {}
        for r in rows:
            grouped.setdefault((r[0], r[1]), []).append(
                AccountLine(
                    sj_div=r[2], account_id=r[3], account_nm=r[4],
                    thstrm_amount=r[5], frmtrm_amount=r[6], bfefrmtrm_amount=r[7],
                    currency=r[8] or "KRW", ord=r[9] or 0,
                )
            )

        result: dict[str, RawStatements] = {}
        for corp_code in corp_codes:
            for div in (FsDiv.CFS, FsDiv.OFS):
                lines = grouped.get((corp_code, div.value))
                if lines:
                    result[corp_code] = RawStatements(
                        corp_code=corp_code, bsns_year=bsns_year,
                        reprt_code=reprt_code, fs_div=div, lines=lines,
                    )
                    break
        return result

    # ------------------------------------------------------------------
    # 계산된 지표 값 (대조군 집계용)
    # ------------------------------------------------------------------

    def save_metric_values(
        self, corp_code: str, bsns_year: int, values: dict[str, float]
    ) -> int:
        self.conn.execute(
            "DELETE FROM metric_values WHERE corp_code = ? AND bsns_year = ?",
            [corp_code, bsns_year],
        )
        rows = [(corp_code, bsns_year, key, value) for key, value in values.items()]
        self._bulk_insert("metric_values", rows, columns=4)
        return len(rows)

    def peer_metric_values(
        self, corp_codes: list[str], bsns_year: int, exclude: str = ""
    ) -> dict[str, list[float]]:
        """대조군의 지표별 값 목록. 한 번의 집계 쿼리로 끝난다."""
        if not corp_codes:
            return {}
        placeholders = ", ".join(["?"] * len(corp_codes))
        rows = self.conn.execute(
            f"SELECT metric_key, value FROM metric_values "
            f"WHERE bsns_year = ? AND corp_code IN ({placeholders}) AND corp_code <> ?",
            [bsns_year, *corp_codes, exclude],
        ).fetchall()
        grouped: dict[str, list[float]] = {}
        for key, value in rows:
            grouped.setdefault(key, []).append(float(value))
        return grouped

    def metric_values_count(self, bsns_year: int) -> int:
        row = self.conn.execute(
            "SELECT count(DISTINCT corp_code) FROM metric_values WHERE bsns_year = ?",
            [bsns_year],
        ).fetchone()
        return int(row[0]) if row else 0

    def list_with_statements(self, bsns_year: int, listed_only: bool = True) -> list[str]:
        """해당 연도 재무가 캐시에 있는 기업. 시장 전체 대조군('전체 상장사')의 모집단이다.

        상한을 두지 않는다 — 예전 기본값 `limit=2000` 이 corp_code 앞쪽 2,000곳에서 대조군을
        조용히 잘랐다(상장사 ~2,540곳). 잘린 쪽이 늦게 등록된 회사들이라 중앙값이 치우쳤다.

        `listed_only=False` 는 지표 미리 계산(backfill)용 — 상장 여부와 무관하게 재무가 있는 전부.
        """
        listed = (
            f"AND corp_code IN (SELECT corp_code FROM companies WHERE {_LISTED_SQL}) "
            if listed_only
            else ""
        )
        rows = self.conn.execute(
            "SELECT DISTINCT corp_code FROM statement_lines WHERE bsns_year = ? "
            f"{listed}ORDER BY corp_code",
            [bsns_year],
        ).fetchall()
        return [r[0] for r in rows]

    def corp_codes_with_statements(
        self, bsns_year: int, reprt_code: ReportCode = ReportCode.ANNUAL
    ) -> set[str]:
        """해당 연도·보고서의 재무가 캐시에 있는 corp_code (상장 여부 무관). 증분 수집이 빈 곳을 찾는다."""
        rows = self.conn.execute(
            "SELECT DISTINCT corp_code FROM statement_lines WHERE bsns_year = ? AND reprt_code = ?",
            [bsns_year, reprt_code.value],
        ).fetchall()
        return {r[0] for r in rows}

    def list_stock_corp_codes(self) -> list[tuple[CorpCode, str | None]]:
        """종목코드가 있는 corp_code 와 기업개황의 시장 구분(개황이 없으면 None), 종목코드순.

        종목코드만으로는 상장사가 아니다(상장폐지 회사의 옛 코드가 남는다) — 시장 구분이 None 이면
        개황을 먼저 받아 봐야 안다. 증분 수집(collect.py)의 모집단.
        """
        rows = self.conn.execute(
            "SELECT c.corp_code, c.corp_name, c.stock_code, c.modify_date, co.market "
            "FROM corp_codes c LEFT JOIN companies co ON co.corp_code = c.corp_code "
            "WHERE c.stock_code <> '' ORDER BY c.stock_code, c.corp_code"
        ).fetchall()
        return [(CorpCode(**dict(zip(_CORP_FIELDS, r[:4], strict=True))), r[4]) for r in rows]

    def list_listed_with_statements(
        self, bsns_year: int, reprt_code: ReportCode = ReportCode.ANNUAL
    ) -> list[CorpCode]:
        """상장사 중 해당 연도 보고서가 캐시에 있는 회사, 종목코드순. 정적 사이트가 그리는 모집단이다.

        종목코드만으로는 상장사를 가를 수 없다 — corpCode.xml 은 상장폐지 회사의 옛 코드를 남긴다.
        시장 구분(`companies.market`)이 KOSPI·KOSDAQ·KONEX 인 회사만. 모르면 넣지 않는다.
        """
        rows = self.conn.execute(
            "SELECT c.corp_code, c.corp_name, c.stock_code, c.modify_date FROM corp_codes c "
            "JOIN companies co ON co.corp_code = c.corp_code "
            f"WHERE c.stock_code <> '' AND co.{_LISTED_SQL} AND EXISTS ("
            "  SELECT 1 FROM statement_lines s WHERE s.corp_code = c.corp_code "
            "  AND s.bsns_year = ? AND s.reprt_code = ?) "
            "ORDER BY c.stock_code",
            [bsns_year, reprt_code.value],
        ).fetchall()
        return [CorpCode(**dict(zip(_CORP_FIELDS, r, strict=True))) for r in rows]

    def table_fingerprint(self, table: str) -> tuple[int, str]:
        """(행 수, 행 내용 해시의 합) — 증분 사이트 빌드의 데이터 지문 (`site/incremental.py`).

        행 순서와 무관하고, 스냅샷을 다시 들여와도 내용이 같으면 같다. `fetched_at` 은 받아 온 시각이지
        내용이 아니라 뺀다 — 같은 공시를 다시 받았다고 캐시 전체가 무효가 되지 않게.
        """
        if table not in _FINGERPRINT_TABLES:
            raise ValueError(f"지문을 내지 않는 테이블: {table}")
        columns = [
            name
            for (name,) in self.conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ? ORDER BY ordinal_position",
                [table],
            ).fetchall()
            if name != "fetched_at"
        ]
        rows, total = self.conn.execute(
            f"SELECT count(*), coalesce(sum(hash({', '.join(columns)})), 0) FROM {table}"
        ).fetchone()
        return int(rows), str(total)

    # ------------------------------------------------------------------
    # 재무제표 원본 계정
    # ------------------------------------------------------------------

    def save_statements(self, raw: RawStatements) -> int:
        key = (raw.corp_code, raw.bsns_year, raw.reprt_code.value, raw.fs_div.value)
        self.conn.execute(
            "DELETE FROM statement_lines WHERE corp_code = ? AND bsns_year = ? "
            "AND reprt_code = ? AND fs_div = ?",
            list(key),
        )
        now = datetime.now()
        self._bulk_insert(
            "statement_lines",
            [
                (
                    *key,
                    ln.sj_div,
                    ln.account_id,
                    ln.account_nm,
                    ln.thstrm_amount,
                    ln.frmtrm_amount,
                    ln.bfefrmtrm_amount,
                    ln.currency,
                    ln.ord,
                    now,
                )
                for ln in raw.lines
            ],
            columns=13,
        )
        return len(raw.lines)

    def get_statements(
        self,
        corp_code: str,
        bsns_year: int,
        reprt_code: ReportCode = ReportCode.ANNUAL,
        fs_div: FsDiv | None = None,
    ) -> RawStatements | None:
        """fs_div를 생략하면 연결(CFS) 우선, 없으면 개별(OFS)."""
        divs = [fs_div] if fs_div is not None else [FsDiv.CFS, FsDiv.OFS]
        for div in divs:
            rows = self.conn.execute(
                "SELECT sj_div, account_id, account_nm, thstrm_amount, frmtrm_amount, "
                "bfefrmtrm_amount, currency, ord FROM statement_lines "
                "WHERE corp_code = ? AND bsns_year = ? AND reprt_code = ? AND fs_div = ? "
                "ORDER BY ord",
                [corp_code, bsns_year, reprt_code.value, div.value],
            ).fetchall()
            if not rows:
                continue
            return RawStatements(
                corp_code=corp_code,
                bsns_year=bsns_year,
                reprt_code=reprt_code,
                fs_div=div,
                lines=[
                    AccountLine(
                        sj_div=r[0], account_id=r[1], account_nm=r[2],
                        thstrm_amount=r[3], frmtrm_amount=r[4], bfefrmtrm_amount=r[5],
                        currency=r[6] or "KRW", ord=r[7] or 0,
                    )
                    for r in rows
                ],
            )
        return None

    # ------------------------------------------------------------------
    # 빈 응답 기록
    # ------------------------------------------------------------------

    def record_miss(
        self, corp_code: str, bsns_year: int, reprt_code: ReportCode, fs_div: FsDiv
    ) -> None:
        key = [corp_code, bsns_year, reprt_code.value, fs_div.value]
        self.conn.execute(
            "DELETE FROM fetch_misses WHERE corp_code = ? AND bsns_year = ? "
            "AND reprt_code = ? AND fs_div = ?",
            key,
        )
        self.conn.execute("INSERT INTO fetch_misses VALUES (?, ?, ?, ?, ?)", [*key, datetime.now()])

    def is_known_miss(
        self,
        corp_code: str,
        bsns_year: int,
        reprt_code: ReportCode,
        fs_div: FsDiv,
        ttl: timedelta = timedelta(days=30),
    ) -> bool:
        row = self.conn.execute(
            "SELECT fetched_at FROM fetch_misses WHERE corp_code = ? AND bsns_year = ? "
            "AND reprt_code = ? AND fs_div = ?",
            [corp_code, bsns_year, reprt_code.value, fs_div.value],
        ).fetchone()
        if row is None:
            return False
        return datetime.now() - row[0] < ttl


    # ------------------------------------------------------------------
    # 계정 · API 키
    # ------------------------------------------------------------------

    def create_account(self, user_id: str, email: str) -> bool:
        """새 계정이면 True, 이미 있으면 False."""
        if self.get_account(user_id) is not None:
            return False
        self.conn.execute(
            "INSERT INTO accounts VALUES (?, ?, ?)", [user_id, email, datetime.now()]
        )
        return True

    def get_account(self, user_id: str) -> tuple[str, str, datetime] | None:
        row = self.conn.execute(
            "SELECT user_id, email, created_at FROM accounts WHERE user_id = ?", [user_id]
        ).fetchone()
        return None if row is None else (row[0], row[1], row[2])

    def save_api_key(self, key_hash: str, user_id: str, prefix: str) -> None:
        self.conn.execute(
            "INSERT INTO api_keys VALUES (?, ?, ?, ?, NULL, NULL)",
            [key_hash, user_id, prefix, datetime.now()],
        )

    def resolve_api_key(self, key_hash: str) -> str | None:
        """유효한 키면 user_id, 없거나 폐기됐으면 None."""
        row = self.conn.execute(
            "SELECT user_id, revoked_at FROM api_keys WHERE key_hash = ?", [key_hash]
        ).fetchone()
        if row is None or row[1] is not None:
            return None
        self.conn.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE key_hash = ?", [datetime.now(), key_hash]
        )
        return row[0]

    def revoke_api_key(self, key_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT revoked_at FROM api_keys WHERE key_hash = ?", [key_hash]
        ).fetchone()
        if row is None or row[0] is not None:
            return False
        self.conn.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE key_hash = ?", [datetime.now(), key_hash]
        )
        return True

    def list_api_keys(self, user_id: str) -> list[tuple[str, str, datetime, datetime | None]]:
        """(key_hash, prefix, created_at, revoked_at). 원문은 어디에도 없다."""
        rows = self.conn.execute(
            "SELECT key_hash, prefix, created_at, revoked_at FROM api_keys "
            "WHERE user_id = ? ORDER BY created_at DESC",
            [user_id],
        ).fetchall()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    # ------------------------------------------------------------------
    # 운영 상태
    # ------------------------------------------------------------------

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("DELETE FROM meta WHERE key = ?", [key])
        self.conn.execute("INSERT INTO meta VALUES (?, ?, ?)", [key, value, datetime.now()])

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", [key]).fetchone()
        return None if row is None else row[0]

    # ------------------------------------------------------------------
    # DART 호출량
    # ------------------------------------------------------------------

    def record_dart_call(self, endpoint: str, day: str, count: int = 1) -> int:
        """호출 1건을 기록하고 그날 그 엔드포인트의 누적 횟수를 반환."""
        current = self.conn.execute(
            "SELECT count FROM dart_call_log WHERE day = ? AND endpoint = ?", [day, endpoint]
        ).fetchone()
        total = (int(current[0]) if current else 0) + count
        self.conn.execute(
            "DELETE FROM dart_call_log WHERE day = ? AND endpoint = ?", [day, endpoint]
        )
        self.conn.execute("INSERT INTO dart_call_log VALUES (?, ?, ?)", [day, endpoint, total])
        return total

    def dart_calls_today(self, day: str) -> int:
        row = self.conn.execute(
            "SELECT coalesce(sum(count), 0) FROM dart_call_log WHERE day = ?", [day]
        ).fetchone()
        return int(row[0]) if row else 0

    def dart_calls_by_endpoint(self, day: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT endpoint, count FROM dart_call_log WHERE day = ? ORDER BY count DESC", [day]
        ).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    def count_watch(self, chat_id: str) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM watchlist WHERE chat_id = ?", [chat_id]
        ).fetchone()
        return 0 if row is None else int(row[0])

    # ------------------------------------------------------------------
    # SEC 티커 (Phase 2)
    # ------------------------------------------------------------------

    def save_sec_tickers(self, rows: list[tuple[str, str, str]]) -> int:
        """(cik, ticker, title) 목록을 통째로 갈아끼운다. 같은 티커가 두 번 오면 뒤가 이긴다."""
        now = datetime.now()
        deduped: dict[str, tuple[str, str, str, object]] = {}
        for cik, ticker, title in rows:
            key = ticker.strip().upper()
            if key:
                deduped[key] = (key, cik, title, now)
        self.conn.execute("DELETE FROM sec_tickers")
        self._bulk_insert("sec_tickers", list(deduped.values()), columns=4)
        return self.conn.execute("SELECT count(*) FROM sec_tickers").fetchone()[0]

    def sec_tickers_age(self) -> timedelta | None:
        row = self.conn.execute("SELECT max(fetched_at) FROM sec_tickers").fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.now() - row[0]

    def find_cik(self, ticker: str) -> tuple[str, str] | None:
        """티커로 (cik, title)을 찾는다."""
        row = self.conn.execute(
            "SELECT cik, title FROM sec_tickers WHERE ticker = ?", [ticker.strip().upper()]
        ).fetchone()
        return None if row is None else (row[0], row[1])

    # ------------------------------------------------------------------
    # 관심종목 · 알림 기록
    # ------------------------------------------------------------------

    def add_watch(self, chat_id: str, corp: CorpCode) -> bool:
        """관심종목 등록. 이미 있으면 False."""
        if self.is_watching(chat_id, corp.corp_code):
            return False
        self.conn.execute(
            "INSERT INTO watchlist VALUES (?, ?, ?, ?, ?)",
            [chat_id, corp.corp_code, corp.corp_name, corp.stock_code, datetime.now()],
        )
        return True

    def remove_watch(self, chat_id: str, corp_code: str) -> bool:
        existed = self.is_watching(chat_id, corp_code)
        self.conn.execute(
            "DELETE FROM watchlist WHERE chat_id = ? AND corp_code = ?", [chat_id, corp_code]
        )
        return existed

    def is_watching(self, chat_id: str, corp_code: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM watchlist WHERE chat_id = ? AND corp_code = ?",
            [chat_id, corp_code],
        ).fetchone()
        return row is not None

    def list_watch(self, chat_id: str) -> list[CorpCode]:
        rows = self.conn.execute(
            "SELECT corp_code, corp_name, stock_code FROM watchlist "
            "WHERE chat_id = ? ORDER BY corp_name",
            [chat_id],
        ).fetchall()
        return [
            CorpCode(corp_code=r[0], corp_name=r[1], stock_code=r[2] or "") for r in rows
        ]

    def all_watchers(self) -> dict[str, list[CorpCode]]:
        """chat_id → 관심종목 목록. 워커가 한 번에 훑기 위한 것."""
        rows = self.conn.execute(
            "SELECT chat_id, corp_code, corp_name, stock_code FROM watchlist ORDER BY chat_id"
        ).fetchall()
        grouped: dict[str, list[CorpCode]] = {}
        for chat_id, corp_code, corp_name, stock_code in rows:
            grouped.setdefault(chat_id, []).append(
                CorpCode(corp_code=corp_code, corp_name=corp_name, stock_code=stock_code or "")
            )
        return grouped

    def watched_corp_codes(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT corp_code FROM watchlist ORDER BY corp_code"
        ).fetchall()
        return [r[0] for r in rows]

    def was_notified(self, chat_id: str, rcept_no: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM notified WHERE chat_id = ? AND rcept_no = ?", [chat_id, rcept_no]
        ).fetchone()
        return row is not None

    def mark_notified(self, chat_id: str, rcept_no: str) -> None:
        if self.was_notified(chat_id, rcept_no):
            return
        self.conn.execute(
            "INSERT INTO notified VALUES (?, ?, ?)", [chat_id, rcept_no, datetime.now()]
        )


#: `table_fingerprint` 를 낼 수 있는 테이블 — 건강검진이 읽는 공개 데이터만 (SQL 에 이름이 들어간다).
_FINGERPRINT_TABLES = frozenset({"corp_codes", "companies", "statement_lines", "metric_values"})


def _synchronized(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


# 공개 메서드를 전부 락으로 감싼다. 메서드마다 with 를 쓰면 하나 빠뜨리는 순간 다시
# 경쟁 상태가 되므로 클래스 수준에서 일괄 적용한다. RLock 이라 메서드끼리 호출해도 된다.
for _name, _attr in list(vars(Cache).items()):
    if _name.startswith("__") or not callable(_attr):
        continue
    setattr(Cache, _name, _synchronized(_attr))
del _name, _attr


_CORP_FIELDS = ("corp_code", "corp_name", "stock_code", "modify_date")
