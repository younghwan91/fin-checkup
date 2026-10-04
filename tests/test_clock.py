"""KST 날짜 — DART 접수일은 한국 시각이다.

UTC 로 도는 서버에서 date.today() 를 쓰면 KST 00:00~09:00 사이에는 어제 날짜라
당일 접수 공시가 조회 범위에서 빠진다. 관리종목 지정·매매거래정지는 바로 이 시간대
(장 개시 전)에 뜬다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fin_checkup.clock import KST, kst_now, kst_today


def test_kst_today_rolls_over_at_korean_midnight():
    # UTC 2026-10-04 20:00 = KST 2026-10-05 05:00
    assert kst_today(datetime(2026, 10, 4, 20, 0, tzinfo=UTC)) == date(2026, 10, 5)
    assert kst_today(datetime(2026, 10, 4, 14, 59, tzinfo=UTC)) == date(2026, 10, 4)


def test_kst_today_accepts_other_zones():
    ny = datetime(2026, 10, 4, 12, 0, tzinfo=ZoneInfo("America/New_York"))  # 16:00 UTC
    assert kst_today(ny) == date(2026, 10, 5)


def test_kst_now_is_aware_and_in_kst():
    now = kst_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == datetime.now(KST).utcoffset()
