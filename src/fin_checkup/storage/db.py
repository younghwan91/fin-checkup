from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from types import TracebackType

import duckdb

from fin_checkup.models import (
    AccountLine,
    Company,
    CorpCode,
    FsDiv,
    Market,
    RawStatements,
    ReportCode,
)

logger = logging.getLogger(__name__)


def today_key(when: date | None = None) -> str:
    """날짜로 나뉜 테이블(dart_call_log 등)의 키."""
    return (when or date.today()).isoformat()


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
            self.conn = duckdb.connect(str(self.db_path), read_only=read_only)
        except duckdb.IOException as exc:
            if "lock" not in str(exc).lower():
                raise
            raise CacheLocked(
                f"캐시({self.db_path})를 다른 프로세스가 쓰고 있습니다.\n"
                "  DuckDB는 한 번에 한 프로세스만 쓸 수 있습니다. "
                "실행 중인 워커나 Streamlit을 먼저 종료하세요."
            ) from exc
        if not read_only:
            self.conn.execute(SCHEMA)

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
        self.conn.close()

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
        return self.conn.execute("SELECT count(*) FROM corp_codes").fetchone()[0]

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
        """업종 그룹에 속한 기업의 corp_code 목록.

        접두사로 찾는다 — 5자리 원본 코드로 정확히 맞추면 업종이 318개로 쪼개져
        절반이 비교 표본을 못 채운다(실측).
        """
        prefix = (industry_prefix or "").strip()
        if not prefix:
            return []
        rows = self.conn.execute(
            "SELECT corp_code FROM companies WHERE industry_code LIKE ? ORDER BY corp_code",
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

    def list_with_statements(self, bsns_year: int, limit: int = 2000) -> list[str]:
        """해당 연도 재무가 캐시에 있는 기업. 시장 전체 대조군의 모집단이다."""
        rows = self.conn.execute(
            "SELECT DISTINCT corp_code FROM statement_lines WHERE bsns_year = ? "
            "ORDER BY corp_code LIMIT ?",
            [bsns_year, limit],
        ).fetchall()
        return [r[0] for r in rows]

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


_CORP_FIELDS = ("corp_code", "corp_name", "stock_code", "modify_date")
