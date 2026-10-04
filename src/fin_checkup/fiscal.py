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
