"""전 상장사 재무제표를 수집해 캐시에 채운다 (운영·분석용).

정규화 적중률 측정과 신호등 경계값 재조정의 입력을 만드는 스크립트다.
DART 일별 호출 허용량이 있어 한 번에 다 받지 않고 이어받을 수 있게 만들었다 —
이미 캐시에 있는 조합은 건너뛴다.

    uv run python scripts/collect_universe.py --year 2024 --limit 500
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fin_checkup.config import settings  # noqa: E402
from fin_checkup.dart.client import DartError  # noqa: E402
from fin_checkup.service import CheckupService  # noqa: E402
from fin_checkup.storage import Cache  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="전 상장사 재무 수집")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--limit", type=int, default=0, help="0이면 전체")
    parser.add_argument("--skip", type=int, default=0, help="앞에서 건너뛸 개수")
    args = parser.parse_args()

    if not settings.has_api_key:
        print("DART_API_KEY가 없습니다.")
        return 1

    with Cache(settings.fin_checkup_db_path) as cache:
        service = CheckupService(cache)
        listed = cache.search_by_name("", limit=10_000)
        targets = listed[args.skip :]
        if args.limit:
            targets = targets[: args.limit]

        print(f"대상 {len(targets):,}사 (전체 상장 {len(listed):,}사) · {args.year}년", flush=True)
        started = time.monotonic()
        fetched = cached = empty = failed = 0

        for i, corp in enumerate(targets, start=1):
            try:
                if cache.get_statements(corp.corp_code, args.year) is not None:
                    cached += 1
                else:
                    fin = await service.get_financials(corp.corp_code, args.year)
                    if fin is None:
                        empty += 1
                    else:
                        fetched += 1
                await service.ensure_company(corp.corp_code)
            except DartError as exc:
                failed += 1
                print(f"  !! {corp.corp_name}: {exc}", flush=True)
                if exc.status == "020":  # 요청 제한 초과
                    print("일별 호출 허용량을 초과했습니다. 내일 --skip으로 이어받으세요.", flush=True)
                    break
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  !! {corp.corp_name}: {type(exc).__name__} {exc}", flush=True)

            if i % 100 == 0:
                rate = i / max(time.monotonic() - started, 1)
                left = (len(targets) - i) / max(rate, 0.01) / 60
                print(
                    f"[{i}/{len(targets)}] 수집 {fetched} · 캐시 {cached} · "
                    f"없음 {empty} · 실패 {failed} · 남은 시간 약 {left:.0f}분",
                    flush=True,
                )

        elapsed = (time.monotonic() - started) / 60
        print(
            f"\n완료 ({elapsed:.1f}분) — 수집 {fetched} · 캐시 {cached} · "
            f"없음 {empty} · 실패 {failed}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
