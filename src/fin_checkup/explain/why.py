"""'왜 이 색인가' 한 문장.

신호등 한 칸 옆에 붙는 설명이다. 값이 어느 경계 구간에 들어가 이 색이 됐는지,
같은 업종(또는 전체 상장사)과 작년의 자기 자신은 어땠는지를 사실로만 적는다.
구간 이름(정상·주의·위험) 말고는 좋다·나쁘다를 붙이지 않는다 — 방향(올랐다/내렸다)도
'개선/악화'로 읽히지 않게 작년 값만 보여 준다.

경계값은 전부 Metric.band(엔진이 업종별로 고른 Band)에서 온다.
"""

from __future__ import annotations

from fin_checkup.explain._korean import because, is_, topic
from fin_checkup.explain.metrics import band_ranges, get_explainer
from fin_checkup.format import format_metric, format_money
from fin_checkup.metrics.engine import (
    BASIS_NOTES,
    CAPEX_ITEMIZED_NOTE,
    CAPEX_WITH_INTANGIBLES_NOTE,
    INTEREST_PAID_NOTE,
    MISSING_ACCOUNT_NOTE,
    MONEY,
    RECEIVABLE_AGGREGATE_NOTE,
    TINY_INVENTORY_NOTE,
    TINY_RECEIVABLE_NOTE,
    TINY_REVENUE_NOTE,
    Category,
    Metric,
)
from fin_checkup.metrics.peers import PeerStat, SelfDelta
from fin_checkup.metrics.sector import Sector
from fin_checkup.metrics.signals import Signal
from fin_checkup.metrics.units import UNIT_SUSPECT_MARKER

_NO_ZERO_FILL = "없는 값을 0으로 채우지 않고 비워 둬요."

#: 근거 비고(엔진 메모, 평서체)를 '왜 이 색' 문장에 붙일 때의 해요체. 메모를 그대로 붙이면
#: "…정상이에요(작년 48.48배). …이자는 빠진다." 처럼 한 설명 안에서 말투가 갈린다 (2026-11-21 카드).
#: 메모 자체(API `note`)는 그대로 둔다 — 데이터 필드라 문체를 바꾸면 소비자가 깨진다.
BASIS_SENTENCES: dict[str, str] = {
    INTEREST_PAID_NOTE: (
        "현금흐름표의 이자 지급액 기준이에요(손익계산서에 이자비용 단독 계정이 없어요). "
        "전환사채 상각이자처럼 현금으로 나가지 않은 이자는 빠져요."
    ),
    RECEIVABLE_AGGREGATE_NOTE: (
        "매출채권 단독 계정이 없어 매출채권및기타채권 합계(미수금 등 포함)로 계산했어요 — "
        "실제보다 낮게 나올 수 있어요."
    ),
    CAPEX_ITEMIZED_NOTE: "유형자산 취득 합계 줄이 없어 종류별 취득(토지·건물·기계장치 등)을 더해 계산했어요.",
    CAPEX_WITH_INTANGIBLES_NOTE: (
        "유형·무형자산 취득이 한 줄로 공시돼 무형자산 취득까지 빼서 계산했어요 — 공식보다 낮게 나올 수 있어요."
    ),
    TINY_INVENTORY_NOTE: (
        "재고자산이 하루 매출보다 적어 회전율이 크게 나와요 — 재고를 거의 두지 않는 사업일 수 있어요."
    ),
    TINY_RECEIVABLE_NOTE: (
        "매출채권이 하루 매출보다 적어 회전율이 크게 나와요 — 외상 없이 바로 대금을 받는 사업일 수 있어요."
    ),
    TINY_REVENUE_NOTE: (
        "매출액이 이익(손실)의 10분의 1보다 작아 이익률이 ±1,000% 밖으로 나와요 — 매출이 아주 작은 해의 값이에요."
    ),
}


def why_color(
    metric: Metric,
    peer_stat: PeerStat | None = None,
    delta: SelfDelta | None = None,
    *,
    sector: Sector | None = None,
    currency: str = "KRW",
) -> str:
    """이 지표가 이 신호를 받은 이유를 쉬운 문장으로.

    sector 를 안 주면 ⊘ 메모(engine 이 '금융업에는 적용하지 않는다 — …' 로 남긴다)에서 읽는다.
    """
    explainer = get_explainer(metric.key)
    name = metric.label
    signal = metric.signal

    if signal is Signal.NOT_APPLICABLE:
        return _not_applicable(metric, sector or _sector_from_note(metric.note))

    if signal is Signal.UNKNOWN:
        return f"{topic(name)} ⚫ 데이터 없음이에요 — {_missing_reason(metric)}"

    # 값 없이 규칙으로 색이 정해진 경우 (흑자 전환·무차입·자본잠식).
    if metric.value is None:
        rule = next((r for r in explainer.special_rules if r.matches(signal, metric.note)), None)
        reason = rule.fact if rule else metric.note
        return f"{topic(name)} {signal.emoji} {is_(signal.label)} — {reason}"

    value = format_metric(metric, currency)
    context = _context(peer_stat, delta, currency)

    if metric.proxy:
        # 경계값은 있는데 분모가 대체값이라 판정하지 않았다. '업종 편차' 문장을 쓰면 이유가 거짓이 된다.
        # 대체값이 여럿이다(금융원가·순이자비용). 엔진 메모로 어느 규칙인지 고른다.
        rule = next((r for r in explainer.special_rules if r.matches(Signal.NEUTRAL, metric.note)), None)
        reason = rule.fact if rule else metric.note
        return f"{name} {topic(value)} {signal.emoji} {is_(signal.label)} — {reason}"

    # 근거 비고는 여럿일 수 있다(합계 기준 + 하루 매출보다 작은 매출채권). 메모에 적힌 순서대로 붙인다.
    found = sorted((n for n in BASIS_NOTES if n in metric.note), key=metric.note.index)
    basis = " ".join(BASIS_SENTENCES[n] for n in found) or None
    if signal is Signal.NEUTRAL or metric.band is None:
        sentence = f"{name} {topic(value)} 업종마다 차이가 커서 경계값 없이 ⚪ 측정값으로만 보여 줘요{context}."
        return f"{sentence} {basis}" if basis else sentence

    ranges = band_ranges(metric.band, metric.unit)
    band_phrase = ranges.get(signal)
    if band_phrase is None or metric.band.evaluate(metric.value) is not signal:
        # 경계값과 다른 색을 규칙이 정했다. 엔진이 남긴 메모가 이유다.
        return f"{name} {topic(value)} {signal.emoji} {is_(signal.label)} — {metric.note}{context}"
    sentence = f"{name} {topic(value)} {because(band_phrase)} {signal.emoji} {is_(signal.label)}{context}."
    # 분모의 출처 비고(이자 지급액 기준 등)는 규칙이 아니라 계산 근거다. 그대로 붙인다 — 같은 색의
    # 규칙(무차입 🟢)을 대신 붙이면 이자를 내는 회사를 '빚이 없다' 고 적게 된다.
    if basis:
        return f"{sentence} {basis}"
    # 경계값과 같은 색이어도 규칙이 따로 짚은 사실이 있으면 덧붙인다 (흑자인데 현금 유출).
    rule = next((r for r in explainer.special_rules if r.matches(signal, metric.note)), None)
    if rule is not None and metric.note:
        sentence += f" {rule.fact}"
    return sentence


def _sector_from_note(note: str) -> Sector | None:
    return next((s for s in Sector if note.startswith(f"{s.label}에는")), None)


def _not_applicable(metric: Metric, sector: Sector | None) -> str:
    explainer = get_explainer(metric.key)
    name = metric.label
    caveat = explainer.caveat_for(sector) if sector else None
    if caveat is not None:
        return (
            f"{topic(name)} {sector.label}에는 맞지 않아 ⊘ 해당 없음이에요 — "
            f"{caveat.why} 대신 볼 것: {caveat.instead}."
        )
    rule = next((r for r in explainer.special_rules if r.signal is Signal.NOT_APPLICABLE), None)
    reason = rule.fact if rule else metric.note
    return f"{topic(name)} ⊘ 해당 없음이에요 — {reason}"


def _missing_reason(metric: Metric) -> str:
    explainer = get_explainer(metric.key)
    if UNIT_SUSPECT_MARKER in metric.note:
        # 자본잠식 같은 규칙 문장으로 바꿔 적으면 이유가 거짓이 된다.
        return metric.note.rstrip(".") + ". 원문에서 단위를 확인해 주세요."
    if metric.note and metric.note != MISSING_ACCOUNT_NOTE:
        rule = next((r for r in explainer.special_rules if r.signal is Signal.UNKNOWN), None)
        if rule is not None:
            return rule.fact
        return metric.note
    if explainer.category is Category.GROWTH:
        return "작년 재무가 없거나, 공시에서 계산에 필요한 계정을 찾지 못했어요. " + _NO_ZERO_FILL
    return (
        f"공시에서 계산에 필요한 계정({explainer.formula})을 찾지 못했어요. "
        "회사가 계정 이름을 다르게 적었거나 그 항목을 공시하지 않았을 수 있어요. " + _NO_ZERO_FILL
    )


def last_year_text(delta: SelfDelta, currency: str = "KRW") -> str:
    """작년 값을 설명 문장에 쓰는 모양으로 (10.88% · 4.0조원)."""
    if delta.unit == MONEY:
        return format_money(delta.previous, currency)
    return f"{delta.previous:,.2f}{delta.unit}"


def _context(peer: PeerStat | None, delta: SelfDelta | None, currency: str) -> str:
    parts: list[str] = []
    if peer is not None:
        text = f"{peer.scope.label} {peer.sample_size}개사 중앙값 {peer.median_text}"
        if peer.rank_text:
            text += f"·{peer.rank_text}"
        parts.append(text)
    if delta is not None:
        parts.append(f"작년 {last_year_text(delta, currency)}")
    return f"({', '.join(parts)})" if parts else ""
