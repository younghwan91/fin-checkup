"""위험 신호(Red Flag) 탐지 — 재무제표로 판정 가능한 것만.

계획서 4절 ⑥ 중 감사의견·CB/BW·최대주주 지분 변화는 공시(DS002/DS004/DS005)가 있어야
판정할 수 있어 Phase 1.5로 미뤄져 있다. 여기서는 재무제표만으로 확정되는 항목을 다룬다.

각 항목은 '사실'만 진술한다. 상장폐지·관리종목 지정은 거래소가 여러 요건을 종합해
결정하므로, 여기서 하는 말은 "이 요건에 해당하는 수치가 관측됐다"까지다.
"""

from __future__ import annotations

from dataclasses import dataclass

from fin_checkup.format import format_money
from fin_checkup.metrics.sector import Sector
from fin_checkup.models import Financials

#: 판정 경계. 해설(explain.redflags)이 이 값을 그대로 문장에 옮긴다 — 여기만 고치면 된다.
#: 자본잠식률이 이 % 이상이면 부분 자본잠식 신호.
IMPAIRMENT_RATE_MIN = 50
#: 영업손실이 이 해 수 이상 이어지면 신호.
LOSS_STREAK_MIN = 3
#: 영업이익 ÷ 이자비용이 이 값 미만인 해가 ZOMBIE_STREAK_MIN 해 이상 이어지면 신호.
ZOMBIE_COVERAGE_MAX = 1
ZOMBIE_STREAK_MIN = 3


@dataclass(frozen=True)
class RedFlag:
    key: str
    label: str
    #: 관측된 사실. 해석이나 권유를 담지 않는다.
    detail: str
    #: 관련 규정 안내 (교육 목적).
    reference: str = ""


def detect_red_flags(
    history: list[Financials], sector: Sector = Sector.GENERAL
) -> list[RedFlag]:
    """연도 오름차순 재무 이력에서 위험 신호를 찾는다.

    금융업에는 이자보상배율 기반 판정을 적용하지 않는다 — 이자비용이 차입 원가가
    아니라 영업의 원가여서 1 미만이 정상이다.
    """
    if not history:
        return []
    ordered = sorted(history, key=lambda f: f.bsns_year)
    latest = ordered[-1]
    flags: list[RedFlag] = []
    # 보고서 금액 단위 오류 의심(metrics.units)인 해는 판정하지 않는다. 연속 기록도 그 해에서 끊긴다.
    if latest.unit_suspect:
        return flags

    def money(value: float | None) -> str:
        # 공시 통화로 적는다. '원'을 박아두면 미국 기업에도 원이 붙는다.
        return format_money(value, latest.currency, compact=False)

    # 자본잠식
    if latest.total_equity is not None and latest.total_equity <= 0:
        flags.append(
            RedFlag(
                "full_capital_impairment",
                "완전자본잠식",
                f"{latest.bsns_year}년 자기자본이 {money(latest.total_equity)}으로 0 이하다.",
                "완전자본잠식은 상장폐지 사유에 해당한다.",
            )
        )
    elif (rate := latest.capital_impairment_rate) is not None and rate >= IMPAIRMENT_RATE_MIN:
        flags.append(
            RedFlag(
                "partial_capital_impairment",
                f"자본잠식률 {IMPAIRMENT_RATE_MIN}% 이상",
                f"{latest.bsns_year}년 자본잠식률이 {rate:.1f}%다.",
                "자본잠식률 50% 이상은 관리종목 지정 요건 중 하나다.",
            )
        )

    # 연속 영업손실
    streak = _trailing_loss_streak(ordered)
    if streak >= LOSS_STREAK_MIN:
        flags.append(
            RedFlag(
                "consecutive_operating_loss",
                f"{streak}년 연속 영업손실",
                f"{ordered[-streak].bsns_year}~{latest.bsns_year}년 영업이익이 연속 음수다.",
                # KRX 「코스닥상장법인 정기결산 관련 투자유의사항」(2025-02) 기준. 예전의 4년 연속
                # → 관리종목 요건은 2022년 말 코스닥시장 상장규정 개정으로 삭제됐다.
                "코스닥 시장에서는 5년 연속 영업손실이 투자주의환기종목 지정 사유다 "
                "(예전의 '4년 연속 → 관리종목' 요건은 2022년 말 규정 개정으로 없어졌다).",
            )
        )

    # 흑자인데 영업현금흐름 유출
    if (
        latest.net_income is not None
        and latest.net_income > 0
        and latest.operating_cash_flow is not None
        and latest.operating_cash_flow < 0
    ):
        flags.append(
            RedFlag(
                "profit_without_cash",
                "흑자인데 영업현금 유출",
                f"{latest.bsns_year}년 당기순이익은 {money(latest.net_income)}이지만 "
                f"영업활동현금흐름은 {money(latest.operating_cash_flow)}이다.",
            )
        )

    # 이자보상배율 1 미만 연속
    zombie = 0 if sector.is_financial else _trailing_zombie_streak(ordered)
    if zombie >= ZOMBIE_STREAK_MIN:
        flags.append(
            RedFlag(
                "zombie_streak",
                f"{zombie}년 연속 이자보상배율 {ZOMBIE_COVERAGE_MAX} 미만",
                f"{ordered[-zombie].bsns_year}~{latest.bsns_year}년 영업이익이 "
                "이자비용에 미치지 못했다.",
            )
        )

    return flags


def _trailing_loss_streak(ordered: list[Financials]) -> int:
    streak = 0
    for fin in reversed(ordered):
        if fin.unit_suspect:
            break
        if fin.operating_income is not None and fin.operating_income < 0:
            streak += 1
        else:
            break
    return streak


def _trailing_zombie_streak(ordered: list[Financials]) -> int:
    streak = 0
    for fin in reversed(ordered):
        if fin.unit_suspect:
            break
        op, interest = fin.operating_income, fin.interest_expense
        if op is None or interest is None or interest == 0:
            break
        # 대체값(금융원가 — 외환손실 포함, 순이자비용 — 이자수익 차감)으로는 '이자도 못 갚았다'를
        # 단정하지 않는다.
        if fin.interest_expense_source is not None and fin.interest_expense_source.is_proxy:
            break
        if op / interest < ZOMBIE_COVERAGE_MAX:
            streak += 1
        else:
            break
    return streak
