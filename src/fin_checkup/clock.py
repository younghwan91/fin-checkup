"""한국 시각.

DART 접수일(rcept_dt)·사업연도·호출량 집계일은 모두 한국 시각 기준이다. 서버가 UTC 로
돌면 `date.today()` 는 KST 00:00~09:00 사이에 어제를 돌려준다 — 그 아홉 시간 동안
당일 접수 공시가 조회 범위에서 빠진다. 장 개시 전에 뜨는 관리종목 지정·매매거래정지가
딱 그 시간대다. 날짜가 필요한 곳은 전부 여기서 받는다.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def kst_now(now: datetime | None = None) -> datetime:
    """지금(또는 주어진 aware datetime)을 KST 로."""
    if now is None:
        return datetime.now(KST)
    if now.tzinfo is None:
        raise ValueError("naive datetime 은 어느 시간대인지 알 수 없다")
    return now.astimezone(KST)


def kst_today(now: datetime | None = None) -> date:
    return kst_now(now).date()
