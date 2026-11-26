"""공개 스냅샷 — 사이트를 빌드하는 데 필요한 캐시 테이블만 parquet 으로 뽑는다.

캐시 DB 에는 공시에서 받은 공공데이터와 함께 이 도구를 쓰는 사람의 정보(관심종목·알림 기록·
계정·API 키 해시)와 운영 기록(호출량·마지막 폴링 시각)이 같이 들어 있다. 스냅샷은 GitHub
릴리스로 누구나 내려받는 파일이라, "전부 내보내고 몇 개 빼기" 가 아니라 **허용목록**으로 간다.
스키마에 새 테이블이 생기면 `EXPORT_TABLES` 나 `EXCLUDED_TABLES` 중 한쪽에 넣기 전까지
export 가 멈추고 테스트도 실패한다 — 분류를 잊은 테이블이 조용히 공개되는 길을 막는다.

import 는 새 DuckDB 파일을 만들어 같은 스키마(`storage.db.SCHEMA`)에 행을 채운다. 사이트
빌더는 그 파일을 평소 캐시처럼 읽는다. 차단 테이블은 빈 채로 스키마에만 있다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import duckdb

from fin_checkup.clock import kst_now
from fin_checkup.storage import Cache

#: manifest.json 형식 버전. 테이블 구성이나 파일 배치를 바꾸면 올리고 import 쪽도 맞춘다.
SNAPSHOT_SCHEMA_VERSION = 1
MANIFEST = "manifest.json"

#: 내보내는 테이블 → 행 조건. 전부 DART·SEC 가 공개한 데이터다.
EXPORT_TABLES: dict[str, str] = {
    # corp_code 전체 11만 행 중 상장사만. 사이트는 상장사 페이지만 만든다.
    "corp_codes": "stock_code IS NOT NULL AND trim(stock_code) <> ''",
    "companies": "",
    "statement_lines": "",
    "metric_values": "",
    # 빈 응답 기록. 없는 해를 ⚫ 로 보여줄 때 '조회했지만 없었다' 의 근거가 된다.
    "fetch_misses": "",
    # 분기·반기보고서 주요계정 (최근 분기 섹션). 2026-11 에 생겼다.
    "key_accounts": "",
}

#: 나중에 생긴 테이블 — 그 전에 올린 스냅샷에는 없어도 들여온다(빈 채로). 이게 없으면 새 코드가
#: 옛 `data-*` 릴리스를 못 읽어, 첫 수집·배포가 새 테이블이 든 스냅샷을 만들기 전에 멈춘다.
OPTIONAL_TABLES: frozenset[str] = frozenset({"key_accounts"})

#: 어떤 산출물에도 넣지 않는 개인정보·운영 테이블. 스키마에 없는 이름(subscriptions·
#: usage_daily)도 둔다 — 옛 버전이나 포크의 DB 에 남아 있으면 그것도 막아야 한다.
SENSITIVE_TABLES: frozenset[str] = frozenset(
    {
        "accounts",
        "api_keys",
        "subscriptions",
        "watchlist",
        "notified",
        "usage_daily",
        "dart_call_log",
    }
)

#: 내보내지 않는 테이블과 그 이유.
EXCLUDED_TABLES: dict[str, str] = {
    **{name: "개인정보·운영 기록" for name in SENSITIVE_TABLES},
    "meta": "운영 상태(마지막 폴링 시각·봇 offset·마이그레이션 표시)",
    "sec_tickers": "국내 사이트 빌드에 쓰지 않는다 (SEC 가 언제든 새로 내려준다)",
}


class SnapshotError(RuntimeError):
    """스냅샷을 만들거나 들여올 수 없다. 메시지에 비밀값을 담지 않는다."""


def _tables(conn: duckdb.DuckDBPyConnection) -> set[str]:
    rows = conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    return {row[0] for row in rows}


def _sql_path(path: Path) -> str:
    """COPY ... TO 는 경로를 매개변수로 받지 않는다. 작은따옴표만 이스케이프한다."""
    return "'" + str(path).replace("'", "''") + "'"


def _clear(created: list[Path]) -> None:
    for path in created:
        path.unlink(missing_ok=True)


def _leaks(conn: duckdb.DuckDBPyConnection, path: Path, secrets: list[str]) -> bool:
    """parquet 의 문자열 칸 어디에든 비밀값이 들어 있는가. 압축 때문에 바이트 검색만으론 못 믿는다."""
    if any(secret.encode() in path.read_bytes() for secret in secrets):
        return True
    if path.suffix != ".parquet":
        return False
    columns = [
        name
        for name, dtype, *_ in conn.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
        if dtype == "VARCHAR"
    ]
    for column in columns:
        for secret in secrets:
            found = conn.execute(
                f'SELECT count(*) FROM read_parquet(?) WHERE contains("{column}", ?)',
                [str(path), secret],
            ).fetchone()[0]
            if found:
                return True
    return False


def export_snapshot(db_path: Path | str, out_dir: Path | str, secrets: Iterable[str] = ()) -> dict:
    """허용목록 테이블을 `out_dir/<table>.parquet` 과 `manifest.json` 으로 쓴다.

    secrets(DART 인증키 등) 중 하나라도 산출물에 들어 있으면 쓴 파일을 지우고 멈춘다.
    """
    db_path, out_dir = Path(db_path), Path(out_dir)
    secrets = [s for s in (s.strip() for s in secrets) if s]
    if not db_path.exists():
        raise SnapshotError(f"캐시 파일이 없습니다: {db_path}")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SnapshotError(
            f"출력 폴더가 비어 있지 않습니다: {out_dir} — 지난 스냅샷과 섞이지 않게 빈 폴더를 주세요."
        )

    created: list[Path] = []
    with Cache(db_path, read_only=True) as cache:
        conn = cache.conn
        tables = _tables(conn)
        unclassified = sorted(tables - set(EXPORT_TABLES) - set(EXCLUDED_TABLES))
        if unclassified:
            raise SnapshotError(
                "공개 여부를 정하지 않은 테이블이 있습니다: "
                + ", ".join(unclassified)
                + " — snapshot.EXPORT_TABLES 나 EXCLUDED_TABLES 에 넣으세요."
            )
        missing = sorted(set(EXPORT_TABLES) - tables)
        if missing:
            raise SnapshotError(f"캐시에 필요한 테이블이 없습니다: {', '.join(missing)}")

        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_tables: dict[str, dict] = {}
        try:
            for table, where in EXPORT_TABLES.items():
                target = out_dir / f"{table}.parquet"
                query = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "")
                # 같은 캐시면 같은 파일이 나오게 정렬한다 — 릴리스끼리 비교하기 쉽다.
                conn.execute(
                    f"COPY ({query} ORDER BY ALL) TO {_sql_path(target)} (FORMAT parquet, COMPRESSION zstd)"
                )
                created.append(target)
                rows = conn.execute("SELECT count(*) FROM read_parquet(?)", [str(target)]).fetchone()[0]
                manifest_tables[table] = {"file": target.name, "rows": int(rows)}

            years = conn.execute("SELECT DISTINCT bsns_year FROM statement_lines ORDER BY 1").fetchall()
            now = kst_now()
            manifest = {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "built_on": now.date().isoformat(),
                "built_at": now.isoformat(timespec="seconds"),
                "source": "DART (opendart.fss.or.kr) 공시 데이터를 fin-checkup 캐시에서 뽑은 것",
                "bsns_years": [int(row[0]) for row in years],
                "tables": manifest_tables,
            }
            manifest_path = out_dir / MANIFEST
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            created.append(manifest_path)

            if secrets:
                scan = duckdb.connect()
                try:
                    leaked = [p.name for p in created if _leaks(scan, p, secrets)]
                finally:
                    scan.close()
                if leaked:
                    raise SnapshotError(
                        "인증키로 보이는 값이 산출물에 들어 있어 지웠습니다: " + ", ".join(leaked)
                    )
        except BaseException:
            _clear(created)
            raise
    return manifest


def read_manifest(src_dir: Path | str) -> dict:
    path = Path(src_dir) / MANIFEST
    if not path.exists():
        raise SnapshotError(f"{MANIFEST} 가 없습니다: {src_dir}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"{MANIFEST} 를 읽을 수 없습니다: {exc}") from exc


def import_snapshot(src_dir: Path | str, db_path: Path | str) -> dict:
    """스냅샷 폴더로 새 캐시 DB 를 만든다. 이미 있는 파일은 덮어쓰지 않는다.

    허용목록 밖의 테이블이 manifest 에 있으면 들여오지 않고 멈춘다 — 릴리스 자산은
    누구나 다시 올릴 수 있는 파일이라 내용을 믿지 않는다.
    """
    src_dir, db_path = Path(src_dir), Path(db_path)
    if db_path.exists():
        raise SnapshotError(f"캐시 파일이 이미 있습니다: {db_path} — 새 경로를 주세요.")

    manifest = read_manifest(src_dir)
    version = manifest.get("schema_version")
    if version != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotError(f"스냅샷 형식 {version} 은 이 버전이 읽는 {SNAPSHOT_SCHEMA_VERSION} 과 다릅니다.")
    tables: dict[str, dict] = manifest.get("tables") or {}
    unknown = sorted(set(tables) - set(EXPORT_TABLES))
    if unknown:
        raise SnapshotError(f"허용목록 밖의 테이블은 들여오지 않습니다: {', '.join(unknown)}")
    absent = sorted(set(EXPORT_TABLES) - set(tables) - OPTIONAL_TABLES)
    if absent:
        raise SnapshotError(f"스냅샷에 테이블이 빠져 있습니다: {', '.join(absent)}")

    try:
        with Cache(db_path) as cache:
            conn = cache.conn
            for table, info in tables.items():
                # 파일 이름은 manifest 를 믿지 않고 테이블 이름에서 만든다(경로 조작 차단).
                parquet = src_dir / f"{table}.parquet"
                if not parquet.exists():
                    raise SnapshotError(f"파일이 없습니다: {parquet.name}")
                conn.execute(f"INSERT INTO {table} BY NAME SELECT * FROM read_parquet(?)", [str(parquet)])
                rows = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                if rows != info.get("rows"):
                    raise SnapshotError(
                        f"{table}: manifest 는 {info.get('rows')}행인데 {rows}행을 읽었습니다."
                    )
    except BaseException:
        db_path.unlink(missing_ok=True)
        Path(str(db_path) + ".wal").unlink(missing_ok=True)
        raise
    return manifest
