"""Streamlit 건강검진 화면.

    uv run streamlit run src/fin_checkup/app.py

계획서 3절의 4대 화면을 한 페이지에 담았다.
1) 한 장 요약 카드  2) 용어 툴팁  3) 추이 그래프  4) 급변 감지
"""

from __future__ import annotations

import asyncio
import html
from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fin_checkup.config import settings
from fin_checkup.format import chart_scale, format_metric
from fin_checkup.metrics.changes import detect_changes
from fin_checkup.metrics.engine import MONEY, Category, Metric, checkup
from fin_checkup.metrics.peers import PeerStat, SelfDelta
from fin_checkup.metrics.sector import Sector
from fin_checkup.metrics.signals import SEVERITY, Signal
from fin_checkup.models import Financials
from fin_checkup.service import Checkup, CheckupService
from fin_checkup.storage import Cache, CacheLocked
from fin_checkup.theme import (
    BORDER,
    CSS,
    INK_MUTED,
    SERIES_COLORS,
    SIGNAL_COLORS,
    SURFACE,
    SURFACE_SUNK,
)

DISCLAIMER = """
본 서비스는 소프트웨어 도구이며 투자권유·투자자문을 제공하지 않습니다.
표시되는 정보는 DART 공시 수치를 공개된 재무비율 공식으로 계산한 객관적 측정 결과입니다.
데이터 정확성 및 이용에 따른 손익에 운영자는 책임지지 않습니다. (DART 이용약관 제23조와 동일 취지)
"""

TREND_SERIES: list[tuple[str, str]] = [
    ("revenue", "매출액"),
    ("operating_income", "영업이익"),
    ("net_income", "당기순이익"),
    ("total_liabilities", "부채총계"),
    ("total_equity", "자본총계"),
    ("operating_cash_flow", "영업활동현금흐름"),
]


@st.cache_resource
def _open_cache() -> Cache:
    """연결에 성공했을 때만 캐싱된다. 예외는 캐싱되지 않는다."""
    return Cache(settings.fin_checkup_db_path)


def get_cache() -> Cache | None:
    """캐시를 연다. 다른 프로세스가 쓰고 있으면 None.

    읽기 전용으로 우회하려 해도 소용없다 — DuckDB는 writer가 락을 잡고 있으면
    read_only 연결도 거부한다. 그래서 폴백 대신 무엇을 해야 하는지 알린다.

    실패를 캐싱하지 않는 게 중요하다. None을 캐싱했더니 락이 풀린 뒤에도 앱이
    계속 막힌 채로 남아 재시작해야만 했다.
    """
    try:
        return _open_cache()
    except CacheLocked:
        return None


def get_service() -> CheckupService | None:
    cache = get_cache()
    return None if cache is None else CheckupService(cache)


def band_text(metric: Metric) -> str:
    if metric.band is None:
        return "절대 기준 없음 — 추이와 업종 비교로 판단할 항목."
    band = metric.band
    unit = "" if metric.unit == MONEY else metric.unit
    if band.higher_is_better:
        return f"정상 {band.good}{unit} 이상 · 주의 {band.warn}{unit} 이상 · 그 미만은 위험"
    return f"정상 {band.good}{unit} 이하 · 주의 {band.warn}{unit} 이하 · 그 초과는 위험"


def _esc(text: str) -> str:
    return html.escape(text or "")


def _tooltip(*lines: str) -> str:
    """title 속성에 들어갈 여러 줄 문자열.

    이스케이프한 뒤 줄바꿈을 엔티티로 바꾼다. 날것의 줄바꿈은 속성을 끊는다.
    """
    return "&#10;".join(_esc(line) for line in lines)


def render_metric(
    metric: Metric,
    peer: PeerStat | None = None,
    currency: str = "KRW",
    delta: SelfDelta | None = None,
) -> None:
    """지표 카드 하나.

    신호는 색만으로 말하지 않는다. 좌측 띠(색) + 점 + 툴팁의 판정 문구가 함께 간다 —
    색각 이상이 있어도 읽을 수 있어야 한다.
    """
    color = SIGNAL_COLORS[metric.signal]
    value = format_metric(metric, currency)
    muted = " muted" if metric.value is None else ""

    # 판정 근거를 감추지 않는 게 이 제품의 약속이다. 카드에 올리면 그대로 뜬다.
    #
    # 줄바꿈은 반드시 &#10;로 넣는다. 날것의 \n을 속성에 두면 마크다운이 태그를
    # 끊어버려 툴팁 내용이 본문으로 새어나온다(실제로 그랬다).
    tooltip = _tooltip(
        f"{metric.label} — {metric.signal.label}",
        "",
        metric.description,
        "",
        f"계산식: {metric.formula}",
        band_text(metric),
    )

    parts = [
        f'<div class="fc-card" style="--sig:{color}" title="{tooltip}">',
        '<div class="fc-card-label">',
        f'<span class="fc-dot" style="background:{color}"></span>',
        f"<span>{_esc(metric.label)}</span>",
        '<span class="fc-info">?</span>',
        "</div>",
        f'<div class="fc-card-value{muted}">{_esc(value)}</div>',
    ]

    # 자기 과거와의 비교. 대조군이 답하지 못하는 "우리가 나아졌나"에 답한다.
    if delta is not None and not delta.unchanged:
        tone = "up" if delta.improved else "down"
        parts.append(f'<div class="fc-delta {tone}">{_esc(delta.summary)}</div>')

    if peer is not None and peer.percentile is not None:
        parts += [
            '<div class="fc-peer">',
            '<div class="fc-peer-track">',
            f'<div class="fc-peer-fill" style="width:{max(peer.percentile, 2):.0f}%"></div>',
            "</div>",
            f'<div class="fc-peer-text">{_esc(peer.summary)}</div>',
            "</div>",
        ]
    elif peer is not None:
        parts.append(f'<div class="fc-card-note">{_esc(peer.summary)}</div>')

    if metric.note:
        parts.append(f'<div class="fc-card-note">{_esc(metric.note)}</div>')
    parts.append("</div>")

    st.markdown("".join(parts), unsafe_allow_html=True)


#: 요약 줄에 항상 보일 신호. 나머지(측정값·해당없음·데이터없음)는 0이면 감춘다.
PRIMARY_SIGNALS = (Signal.RED, Signal.YELLOW, Signal.GREEN)
SECONDARY_SIGNALS = (Signal.NEUTRAL, Signal.NOT_APPLICABLE, Signal.UNKNOWN)


def render_summary(data: Checkup) -> None:
    """신호 개수 요약.

    0인 항목까지 다 보여주면 눈이 갈 곳을 잃는다. 정상/주의/위험은 늘 보이고
    나머지는 실제로 있을 때만 나온다.
    """
    counts = data.result.counts
    chips = [(s, counts[s]) for s in PRIMARY_SIGNALS]
    chips += [(s, counts[s]) for s in SECONDARY_SIGNALS if counts[s]]

    html_parts = ['<div class="fc-summary">']
    for signal, count in chips:
        html_parts.append(
            f'<span class="fc-chip">'
            f'<span class="fc-dot" style="background:{SIGNAL_COLORS[signal]}"></span>'
            f"{signal.label} <b>{count}</b></span>"
        )
    html_parts.append("</div>")

    # "좋은 거야?"에 한 줄로 답하는 자리. 점수를 매기는 대신 대조군을 옆에 둔다.
    verdict = summary_verdict(data)
    if verdict:
        html_parts.append(f'<div class="fc-verdict">{_esc(verdict)}</div>')

    st.markdown("".join(html_parts), unsafe_allow_html=True)


def summary_verdict(data: Checkup) -> str:
    """지표가 대조군 대비 어디쯤인지 한 줄로.

    종합 점수를 만들지 않는다 — 점수는 우리가 매긴 것이고, 대조군은 사실이다.
    "상위 몇 %인 지표가 몇 개"까지만 말하고 그 이상 해석하지 않는다.
    """
    ranked = [s for s in data.peers.values() if s.percentile is not None]
    if len(ranked) < 3:
        return ""

    scope = Counter(s.scope for s in ranked).most_common(1)[0][0]
    sample = max(s.sample_size for s in ranked)
    top_quartile = sum(1 for s in ranked if s.percentile >= 75)
    bottom_quartile = sum(1 for s in ranked if s.percentile <= 25)

    return (
        f"{scope.label} {sample}개사와 비교 · "
        f"견줄 수 있는 {len(ranked)}개 지표 중 "
        f"상위 25% 안에 {top_quartile}개, 하위 25%에 {bottom_quartile}개"
    )


def render_red_flags(data: Checkup) -> None:
    if not data.red_flags:
        st.success("재무제표에서 확인되는 위험 신호는 없습니다.")
        return
    for flag in data.red_flags:
        st.error(f"**{flag.label}** — {flag.detail}")
        if flag.reference:
            st.caption(f"↳ {flag.reference}")


def render_changes(data: Checkup) -> None:
    if len(data.history) < 3:
        st.info("급변을 판단하려면 3개 사업연도 이상의 데이터가 필요합니다.")
        return

    latest, prev, prev2 = data.history[-1], data.history[-2], data.history[-3]
    changes = detect_changes(checkup(latest, prev), checkup(prev, prev2))
    if not changes:
        st.success(f"{prev.bsns_year} → {latest.bsns_year}년 사이 급격히 나빠진 지표는 없습니다.")
        return

    st.caption(f"{prev.bsns_year} → {latest.bsns_year}년 비교")
    for change in changes:
        st.warning(f"**{change.label}** — {change.summary}")


def render_trend(history: list[Financials]) -> None:
    currency = history[-1].currency if history else "KRW"
    divisor, unit_label = chart_scale(
        [getattr(f, field) for f in history for field, _ in TREND_SERIES], currency
    )
    rows = []
    for fin in history:
        row = {"연도": fin.bsns_year}
        for field, label in TREND_SERIES:
            value = getattr(fin, field)
            row[label] = None if value is None else value / divisor
        rows.append(row)
    frame = pd.DataFrame(rows)

    picked = st.multiselect(
        "표시할 항목",
        [label for _, label in TREND_SERIES],
        default=["매출액", "영업이익", "당기순이익"],
    )
    if not picked:
        return

    # 색은 항목에 고정한다. 선택을 바꿨다고 남은 선의 색이 바뀌면 안 된다.
    color_of = {
        label: SERIES_COLORS[i % len(SERIES_COLORS)]
        for i, (_, label) in enumerate(TREND_SERIES)
    }

    figure = go.Figure()
    for label in picked:
        figure.add_trace(
            go.Scatter(
                x=frame["연도"], y=frame[label], mode="lines+markers", name=label,
                line=dict(color=color_of[label], width=2),
                marker=dict(size=8, line=dict(width=2, color=SURFACE)),
                hovertemplate=f"{label} %{{y:,.1f}}{unit_label}<extra></extra>",
            )
        )
    figure.update_layout(
        hovermode="x unified", height=400,
        margin=dict(l=0, r=0, t=8, b=0),
        plot_bgcolor=SURFACE, paper_bgcolor=SURFACE,
        font=dict(color=INK_MUTED, size=12),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0)",
        ),
        yaxis_title=None, xaxis_title=None,
    )
    # 격자와 축은 뒤로 물린다 — 읽어야 할 건 선이다.
    figure.update_xaxes(dtick=1, showgrid=False, linecolor=BORDER, ticks="outside",
                        tickcolor=BORDER)
    figure.update_yaxes(gridcolor=SURFACE_SUNK, zeroline=True, zerolinecolor=BORDER,
                        ticksuffix=f" {unit_label}")
    st.plotly_chart(figure, use_container_width=True)

    with st.expander(f"표로 보기 (단위: {unit_label})"):
        st.dataframe(frame.set_index("연도"), use_container_width=True)


EXAMPLES = [
    ("삼성전자", "005930", "정상 지표가 대부분인 예"),
    ("KB금융", "105560", "금융업은 일부 지표를 적용하지 않음"),
    ("에이치엘비", "028300", "적자 기업이 어떻게 표시되는지"),
]


def render_landing() -> None:
    st.markdown(
        '<div class="fc-hero">'
        "<h2>재무 건강검진</h2>"
        "<p>공시된 재무제표를 공개된 공식으로 계산해 신호등으로 보여줍니다.<br>"
        "판정 기준을 감추지 않습니다 — 각 지표에 계산식과 경계값이 함께 붙습니다.<br>"
        "종목 추천이나 투자 판단은 하지 않습니다.</p>"
        '<div class="fc-examples">'
        + "".join(
            f'<div class="fc-example">{_esc(name)} <code>{code}</code>'
            f"<span>{_esc(hint)}</span></div>"
            for name, code, hint in EXAMPLES
        )
        + "</div></div>",
        unsafe_allow_html=True,
    )
    st.caption("왼쪽에서 회사명이나 종목코드를 입력하세요.")


def render_footnote() -> None:
    st.markdown(
        f'<div class="fc-footnote">{_esc(DISCLAIMER.strip())}</div>',
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="fin-checkup 재무 건강검진", page_icon="🩺", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    service = get_service()
    if service is None:
        st.error(
            "**캐시를 다른 프로세스가 쓰고 있습니다.**\n\n"
            "DuckDB는 한 번에 한 프로세스만 쓸 수 있습니다. "
            "`fin_checkup.cli serve`나 수집 스크립트가 돌고 있다면 먼저 종료하세요.\n\n"
            "API 서버와 함께 쓰려면 Streamlit 대신 `http://localhost:8000/docs`를 이용하세요."
        )
        render_footnote()
        return

    with st.sidebar:
        st.header("종목 찾기")
        if not settings.has_api_key:
            st.error(
                "DART 인증키가 없습니다. https://opendart.fss.or.kr 에서 발급받아 "
                "`.env`의 `DART_API_KEY`에 넣어주세요. 지금은 캐시에 있는 데이터만 조회됩니다."
            )

        age = service.cache.corp_codes_age()
        if age is None:
            st.warning("종목 목록이 비어 있습니다. 아래 버튼으로 먼저 받아주세요.")
        else:
            st.caption(f"종목 목록 갱신: {age.days}일 전")

        if st.button("종목 목록 갱신", disabled=not settings.has_api_key):
            with st.spinner("DART에서 고유번호 목록을 받는 중…"):
                count = asyncio.run(service.ensure_corp_codes(force=True))
            st.success(f"{count:,}건 갱신")
            st.rerun()

        query = st.text_input("회사명 또는 종목코드", placeholder="예: 삼성전자 · 005930")
        year = st.number_input("기준 사업연도", min_value=2015, max_value=2100, value=2024, step=1)
        years = st.slider("추이 기간(년)", min_value=2, max_value=10, value=5)

    if not query:
        render_landing()
        render_footnote()
        return

    matches = service.search(query)
    if not matches:
        st.warning(f"'{query}'에 해당하는 상장기업을 찾지 못했습니다.")
        render_footnote()
        return

    labels = [f"{c.corp_name} ({c.stock_code})" for c in matches]
    picked = st.selectbox("검색 결과", labels) if len(matches) > 1 else labels[0]
    corp = matches[labels.index(picked)]

    with st.spinner(f"{corp.corp_name} 재무제표를 불러오는 중…"):
        data = asyncio.run(service.run(corp, end_year=int(year), years=int(years)))

    if data is None:
        st.warning(f"{corp.corp_name}의 {year}년 재무제표를 찾지 못했습니다. 다른 연도를 시도해보세요.")
        render_footnote()
        return

    latest = data.history[-1]
    market = (
        f'<span class="fc-ticker">{data.company.market.value}</span>'
        if data.company and data.company.market
        else ""
    )
    st.markdown(
        f'<div class="fc-company">'
        f'<span class="fc-company-name">{_esc(corp.corp_name)}</span>'
        f'<span class="fc-ticker">{_esc(corp.stock_code)}</span>{market}'
        f"</div>"
        f'<div class="fc-sub">{latest.bsns_year}년 {latest.reprt_code.label} · '
        f"{'연결' if latest.fs_div.value == 'CFS' else '개별'}재무제표 · "
        f"{data.result.sector.label} 기준</div>",
        unsafe_allow_html=True,
    )
    if data.result.sector is not Sector.GENERAL:
        st.info(
            f"이 기업은 **{data.result.sector.label}**으로 분류돼, 업종에 맞지 않는 지표는 "
            "⊘로 표시하고 판정하지 않습니다. 예를 들어 은행은 예금이 부채로 잡혀 "
            "부채비율이 1,000%를 넘는 것이 정상이며, 건전성은 BIS 자기자본비율로 봅니다."
        )

    render_summary(data)
    st.divider()

    tab_card, tab_flags, tab_changes, tab_trend = st.tabs(
        ["한 장 요약", "위험 신호", "급변 감지", "추이"]
    )

    with tab_card:
        if not data.peers:
            st.caption(
                "업종 비교는 같은 업종 기업이 캐시에 5곳 이상 있어야 표시됩니다. "
                "`python -m fin_checkup.cli collect`로 채울 수 있습니다."
            )
        for category in Category:
            metrics = data.result.by_category(category)
            if not metrics:
                continue
            st.markdown(
                f'<div class="fc-section">{_esc(category.value)}</div>',
                unsafe_allow_html=True,
            )
            # 나쁜 것부터 보이게 정렬한다. 사용자가 먼저 봐야 할 건 위험한 쪽이다.
            ordered = sorted(metrics, key=lambda m: -SEVERITY[m.signal])
            for row_start in range(0, len(ordered), 4):
                cols = st.columns(4)
                for col, metric in zip(cols, ordered[row_start : row_start + 4], strict=False):
                    with col:
                        render_metric(
                            metric,
                            data.peers.get(metric.key),
                            data.result.currency,
                            data.deltas.get(metric.key),
                        )

    with tab_flags:
        render_red_flags(data)
        st.caption(
            "감사의견·유상증자·CB/BW·최대주주 지분 변화는 공시 데이터가 필요해 "
            "다음 단계(위험 공시 알림)에서 추가됩니다."
        )

    with tab_changes:
        render_changes(data)

    with tab_trend:
        render_trend(data.history)

    render_footnote()


main()
