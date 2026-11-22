"""기준 사업연도 기본값.

CLI·API·화면이 모두 `2024`를 기본값으로 박아두고 있었다. 2026년에 쓰면 2년 전
재무제표가 뜬다. 하드코딩을 걷어내고 날짜로 계산한다.

12월 결산 법인의 사업보고서 제출 기한은 결산일로부터 90일(3월 말)이다. 4월이 되면
직전 연도 보고서가 대부분 올라와 있고, 그 전에는 아직 없는 기업이 많아 두 해 전을 쓴다.
"""

from __future__ import annotations

from datetime import date

from fin_checkup.clock import kst_today

#: 사업보고서가 대부분 올라오는 달. 이 달부터 직전 연도를 기본값으로 쓴다.
FILING_COMPLETE_MONTH = 4


def default_fiscal_year(today: date | None = None) -> int:
    """오늘 기준으로 재무제표를 기대할 수 있는 가장 최근 사업연도."""
    today = today or kst_today()
    if today.month >= FILING_COMPLETE_MONTH:
        return today.year - 1
    return today.year - 2


# ---------------------------------------------------------------------------
# 증분 수집 (collect.py)
# ---------------------------------------------------------------------------

#: 사업보고서 시즌 (월, 일) — 12월 결산 법인의 제출 기한(3월 말)과 기한 연장·지각 제출이 몰리는
#: 4월까지. 이 기간엔 '아직 공시 없음' 이 며칠 만에 바뀌므로 빈 응답 기록을 짧게 믿는다.
FILING_SEASON_START = (3, 20)
FILING_SEASON_END = (4, 30)

#: 빈 응답(fetch_misses)을 다시 묻지 않고 믿는 기간(일). 평소 / 시즌.
MISS_TTL_DAYS = 30
MISS_TTL_SEASON_DAYS = 3


def in_filing_season(today: date | None = None) -> bool:
    today = today or kst_today()
    return FILING_SEASON_START <= (today.month, today.day) <= FILING_SEASON_END


def latest_filing_year(today: date | None = None) -> int:
    """새 사업보고서가 들어올 수 있는 가장 최근 사업연도.

    `default_fiscal_year` 는 '대부분 올라온' 해(4월부터 직전 연도)라 화면 기본값에 쓴다. 수집은
    그보다 먼저 — 시즌이 시작되는 3/20 부터 — 직전 연도를 받기 시작해야 4월 사이트에 숫자가 있다.
    """
    today = today or kst_today()
    if (today.month, today.day) >= FILING_SEASON_START:
        return today.year - 1
    return today.year - 2


def collect_years(today: date | None = None) -> list[int]:
    """증분 수집이 기본으로 보는 사업연도, 최근 것부터. 직전 해는 지각 제출 회사를 잡으려고 함께 본다."""
    latest = latest_filing_year(today)
    return [latest, latest - 1]


def miss_ttl_days(
    today: date | None = None,
    normal: int = MISS_TTL_DAYS,
    season: int = MISS_TTL_SEASON_DAYS,
) -> int:
    """빈 응답 기록을 며칠 믿을지. 시즌엔 짧게 — 30일 믿으면 3/25 에 없던 회사가 4월 내내 ⚫ 로 남는다."""
    return season if in_filing_season(today) else normal
