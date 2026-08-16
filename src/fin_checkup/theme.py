"""화면 색과 스타일.

신호색은 **상태 전용**이고 차트 계열색과 겹치지 않는다. 같은 화면에서 초록 선이
"정상"으로 읽히면 안 되기 때문이다. 계열색은 인접 쌍의 색각 이상 분리도(ΔE ≥ 9.2)를
검증기로 확인한 조합이다.

색만으로 뜻을 전달하지 않는다 — 신호는 항상 색 + 점 + 글자(정상/주의/위험)를 함께 쓴다.
"""

from __future__ import annotations

from fin_checkup.metrics.signals import Signal

#: 상태 전용. 차트 계열색으로 재사용하지 않는다.
SIGNAL_COLORS: dict[Signal, str] = {
    Signal.GREEN: "#0ca30c",
    Signal.YELLOW: "#fab219",
    Signal.RED: "#d03b3b",
    Signal.NEUTRAL: "#8c8c88",
    Signal.NOT_APPLICABLE: "#b4b4ae",
    Signal.UNKNOWN: "#6e6e69",
}

#: 신호 배경(옅은 톤). 카드 좌측 띠와 칩 배경에 쓴다.
SIGNAL_TINTS: dict[Signal, str] = {
    Signal.GREEN: "#eaf6ea",
    Signal.YELLOW: "#fdf3df",
    Signal.RED: "#fbeaea",
    Signal.NEUTRAL: "#f2f2f0",
    Signal.NOT_APPLICABLE: "#f5f5f3",
    Signal.UNKNOWN: "#eeeeec",
}

#: 차트 계열색. 인접 충돌을 피하도록 이 순서를 지킨다(검증 완료).
SERIES_COLORS: tuple[str, ...] = (
    "#2a78d6",  # 파랑
    "#1baf7a",  # 청록
    "#eb6834",  # 주황
    "#4a3aa7",  # 보라
    "#e87ba4",  # 자홍
    "#008300",  # 초록
)

INK = "#1b1b19"
INK_MUTED = "#6e6e69"
BORDER = "#e4e4e0"
SURFACE = "#ffffff"
SURFACE_SUNK = "#f7f7f5"


CSS = f"""
<style>
/* Streamlit 기본 크롬을 감춘다 — 배포 버튼과 햄버거는 제품에 필요 없다.
   단 stToolbar 자체는 건드리지 않는다: 사이드바를 접었을 때 다시 펼치는 버튼이
   그 안에 들어 있어서, 툴바를 display:none 하면 되돌릴 방법이 사라진다.
   (실제로 그렇게 만들었다가 사이드바를 못 펴게 됐다.) */
#MainMenu,
footer,
[data-testid="stToolbarActions"],
[data-testid="stMainMenu"],
[data-testid="stAppDeployButton"],
[data-testid="stDeployButton"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"] {{ display: none !important; }}

header, [data-testid="stHeader"] {{
    background: transparent !important;
    height: 3rem !important;
}}

/* 사이드바 펼치기 버튼은 항상 살아 있어야 한다. */
[data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapsedControl"] {{
    display: flex !important; visibility: visible !important; opacity: 1 !important;
    z-index: 999 !important;
}}

.block-container {{ padding-top: 1.2rem; max-width: 1180px; }}

/* 회사 헤더 */
.fc-company {{
    display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap;
    margin-bottom: .15rem;
}}
.fc-company-name {{ font-size: 1.65rem; font-weight: 700; color: {INK}; letter-spacing: -.02em; }}
.fc-ticker {{
    font-size: .82rem; color: {INK_MUTED}; background: {SURFACE_SUNK};
    border: 1px solid {BORDER}; border-radius: 5px; padding: .12rem .42rem;
    font-variant-numeric: tabular-nums;
}}
.fc-sub {{ font-size: .85rem; color: {INK_MUTED}; margin-bottom: 1.1rem; }}

/* 신호 요약 줄 */
.fc-summary {{ display: flex; gap: .5rem; flex-wrap: wrap; margin-bottom: .4rem; }}
.fc-chip {{
    display: inline-flex; align-items: center; gap: .38rem;
    border: 1px solid {BORDER}; border-radius: 999px;
    padding: .3rem .7rem; font-size: .84rem; color: {INK};
}}
.fc-chip b {{ font-variant-numeric: tabular-nums; font-weight: 700; }}
.fc-dot {{ width: .58rem; height: .58rem; border-radius: 50%; flex: none; }}

/* 요약 한 줄 — 대조군에서 어디쯤인지 */
.fc-verdict {{
    font-size: .85rem; color: {INK_MUTED}; margin: .55rem 0 .2rem;
    font-variant-numeric: tabular-nums;
}}

/* 자기 과거 대비 */
.fc-delta {{
    font-size: .74rem; margin-top: .3rem;
    font-variant-numeric: tabular-nums;
}}
.fc-delta.up {{ color: #0b7a0b; }}
.fc-delta.down {{ color: #b23434; }}

/* 지표 카드 */
.fc-card {{
    border: 1px solid {BORDER}; border-left: 3px solid var(--sig);
    border-radius: 8px; padding: .7rem .85rem;
    background: {SURFACE};
    /* 한 줄 안에서 카드 밑단이 들쭉날쭉하면 성의 없어 보인다. */
    min-height: 8.4rem; display: flex; flex-direction: column;
}}
.fc-card-label {{
    display: flex; align-items: center; gap: .35rem;
    font-size: .8rem; color: {INK_MUTED}; margin-bottom: .28rem;
}}
.fc-info {{
    margin-left: auto; width: .95rem; height: .95rem; flex: none;
    border: 1px solid {BORDER}; border-radius: 50%;
    font-size: .62rem; line-height: .88rem; text-align: center;
    color: {INK_MUTED}; cursor: help;
}}
.fc-card-value {{
    font-size: 1.35rem; font-weight: 650; color: {INK};
    font-variant-numeric: tabular-nums; letter-spacing: -.02em;
    line-height: 1.15;
}}
.fc-card-value.muted {{ color: {INK_MUTED}; font-weight: 500; }}
.fc-card-note {{ font-size: .74rem; color: {INK_MUTED}; margin-top: .3rem; line-height: 1.4; }}

/* 업종 내 위치 막대.
   구분선으로 읽히면 안 된다 — 트랙을 뚜렷이 두고 채움은 불투명하게. */
.fc-peer {{ margin-top: .55rem; }}
.fc-peer-track {{
    position: relative; height: 6px; border-radius: 3px;
    background: #ebebe7; overflow: hidden;
}}
.fc-peer-fill {{
    position: absolute; top: 0; bottom: 0; left: 0;
    border-radius: 3px; background: var(--sig);
}}
.fc-peer-text {{
    font-size: .72rem; color: {INK_MUTED}; margin-top: .3rem;
    font-variant-numeric: tabular-nums; line-height: 1.45;
}}

/* 카테고리 제목 */
.fc-section {{
    font-size: .78rem; font-weight: 700; color: {INK_MUTED};
    letter-spacing: .06em; margin: 1.4rem 0 .55rem;
    text-transform: none;
}}

/* 랜딩 */
.fc-hero {{ padding: 2.4rem 0 1rem; }}
.fc-hero h2 {{ font-size: 1.5rem; font-weight: 700; color: {INK}; margin: 0 0 .5rem; }}
.fc-hero p {{ color: {INK_MUTED}; font-size: .95rem; line-height: 1.65; margin: 0 0 1.4rem; }}
.fc-examples {{ display: flex; gap: .5rem; flex-wrap: wrap; }}
.fc-example {{
    border: 1px solid {BORDER}; border-radius: 8px; padding: .55rem .8rem;
    background: {SURFACE}; font-size: .86rem; color: {INK};
}}
.fc-example span {{ color: {INK_MUTED}; font-size: .78rem; display: block; margin-top: .12rem; }}

/* 각주 */
.fc-footnote {{
    border-top: 1px solid {BORDER}; margin-top: 2.2rem; padding-top: .9rem;
    font-size: .76rem; color: {INK_MUTED}; line-height: 1.6;
}}
</style>
"""
