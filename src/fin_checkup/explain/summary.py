"""회사 하나의 측정 결과를 평이한 한국어 사실 문단으로 — 정적 JSON `summary_ko`, 회사 마크다운 첫머리.

사실만 적는다: 어느 보고서로 쟀는지, 신호 개수(`presentation.summary_chips` 와 같은 칸), 🔴·🟡 구간의 지표
(엔진 순서), ⊘·⚫ 지표, 재무제표로 확인된 위험 신호. 회사 전체를 평가하는 말·순위·권유는 넣지 않는다.
끝 문장은 신호등이 판단이 아니라는 것 (회사 화면 '한눈에 보기' 와 같은 뜻).
"""

from __future__ import annotations

from fin_checkup.format import format_metric
from fin_checkup.metrics.signals import Signal
from fin_checkup.models import FsDiv
from fin_checkup.presentation import summary_chips
from fin_checkup.service import Checkup

#: 끝 문장. 테스트가 이 문장을 본다.
NOT_A_JUDGMENT = "각 신호는 지표 하나가 경계값의 어느 구간에 있는지 잰 결과이며, 회사 전체에 대한 평가나 투자 판단이 아닙니다."


def statement_label(fs_div: FsDiv) -> str:
    return "연결재무제표" if fs_div is FsDiv.CFS else "개별재무제표"


def facts_summary(data: Checkup) -> str:
    latest = data.history[-1]
    result = data.result
    market = data.company.market.value if data.company and data.company.market else ""
    who = f"{data.corp.corp_name}({data.corp.stock_code}{', ' + market if market else ''})"
    parts = [
        f"{who}의 {latest.bsns_year} 사업연도 {latest.reprt_code.label}({statement_label(latest.fs_div)}) 기준으로"
        f" 재무비율 {len(result.metrics)}개를 잰 결과입니다.",
        " · ".join(f"{s.emoji} {s.label} {n}개" for s, n in summary_chips(result)) + ".",
    ]

    def named(signal: Signal, with_value: bool) -> list[str]:
        out = []
        for m in result.metrics:
            if m.signal is signal:
                out.append(f"{m.label} {format_metric(m, result.currency)}" if with_value else m.label)
        return out

    for signal, lead in (
        (Signal.RED, "🔴 위험 구간"),
        (Signal.YELLOW, "🟡 주의 구간"),
    ):
        names = named(signal, with_value=True)
        if names:
            parts.append(f"{lead}: {', '.join(names)}.")
    na = named(Signal.NOT_APPLICABLE, with_value=False)
    if na:
        parts.append(f"⊘ 해당 없음({result.sector.label} 업종에 맞지 않아 판정하지 않음): {', '.join(na)}.")
    unknown = named(Signal.UNKNOWN, with_value=False)
    if unknown:
        parts.append(f"⚫ 데이터 없음(공시에서 필요한 계정을 찾지 못함): {', '.join(unknown)}.")
    if data.red_flags:
        flags = "; ".join(f"{f.label} — {f.detail.rstrip('.')}" for f in data.red_flags)
        parts.append(f"재무제표로 확인된 위험 신호: {flags}.")
    else:
        parts.append("재무제표로 확인된 위험 신호는 없습니다.")
    parts.append(NOT_A_JUDGMENT)
    return " ".join(parts)
