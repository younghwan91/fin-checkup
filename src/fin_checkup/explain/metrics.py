"""지표 해설 — 회계를 모르는 사람이 신호등 한 칸을 읽을 수 있게.

사람이 쓰는 건 쉬운 이름·정의·왜 보나·한계뿐이다. 공식·단위·경계값·⊘ 업종은 전부
`metrics/engine.py` 에서 가져온다. 엔진의 경계값이 바뀌면 문장도 따라 바뀌어야 화면의
색과 설명이 같은 말을 한다 (`tests/test_explain_metrics.py` 가 지킨다).

UI 를 모른다. 사이트·Streamlit·CLI 가 같은 데이터를 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass

from fin_checkup.explain._korean import if_, is_
from fin_checkup.metrics import engine
from fin_checkup.metrics.engine import MONEY, Category, MetricDef
from fin_checkup.metrics.sector import Sector
from fin_checkup.metrics.signals import Band, Signal


@dataclass(frozen=True)
class SectorCaveat:
    """이 업종에는 이 지표로 판정하지 않는다(⊘). engine.NOT_APPLICABLE_BY_SECTOR 와 1:1."""

    sector: Sector
    #: 왜 이 업종엔 맞지 않는가.
    why: str
    #: 대신 볼 것.
    instead: str


@dataclass(frozen=True)
class SpecialRule:
    """경계값 밖에서 엔진이 신호를 정하는 경우 (자본잠식·무차입·흑자전환 등)."""

    #: 규칙 설명 ("~면 ~로 표시해요"). 지표 해설 페이지용.
    when: str
    signal: Signal
    #: 이 회사에 그 규칙이 적용됐을 때의 사실 문장 ("~라 ~했어요"). '왜 이 색' 문장용.
    fact: str
    #: 같은 신호의 규칙이 여럿일 때, 엔진 메모(Metric.note)에 이 문구가 들어 있어야 이 규칙이다.
    #: 비우면 그 신호의 규칙 전부에 해당한다.
    note_marker: str = ""

    def matches(self, signal: Signal, note: str) -> bool:
        return self.signal is signal and (not self.note_marker or self.note_marker in note)


@dataclass(frozen=True)
class MetricExplainer:
    key: str
    #: 회계 용어를 모르는 사람이 부를 이름.
    easy_name: str
    #: 중학생도 이해할 한 줄 정의.
    one_liner: str
    #: 왜 이 숫자를 보는가.
    why_look: str
    #: 경계값을 왜 이렇게 잡았는가 (engine.py 주석의 실측 근거).
    basis: str
    #: 이 숫자의 한계와 함정.
    pitfalls: tuple[str, ...]
    sector_caveats: tuple[SectorCaveat, ...] = ()
    special_rules: tuple[SpecialRule, ...] = ()

    @property
    def definition(self) -> MetricDef:
        return _engine_def(self.key)

    @property
    def label(self) -> str:
        return self.definition.label

    @property
    def formula(self) -> str:
        return self.definition.formula

    @property
    def unit(self) -> str:
        return self.definition.unit

    @property
    def category(self) -> Category:
        return self.definition.category

    def caveat_for(self, sector: Sector) -> SectorCaveat | None:
        return next((c for c in self.sector_caveats if c.sector is sector), None)

    def threshold_text(self, sector: Sector = Sector.GENERAL) -> str:
        return threshold_text(self.key, sector)


# ── 엔진에서 가져오기 ───────────────────────────────────────────────


def _engine_def(key: str) -> MetricDef:
    # 모듈 속성으로 매번 찾는다 — 엔진 정의를 바꾸면 해설도 바로 따라간다.
    for spec in engine.METRIC_DEFS:
        if spec.key == key:
            return spec
    raise KeyError(key)


def _num(value: float, unit: str) -> str:
    text = f"{value:g}"
    # 금액 지표의 경계는 0 뿐이다. 통화는 회사마다 달라 단위를 붙이지 않는다.
    return text if unit == MONEY else f"{text}{unit}"


def band_ranges(band: Band, unit: str) -> dict[Signal, str]:
    """신호별 구간을 말로. 🟡 구간이 비면(good == warn) 그 키가 없다."""
    good, warn = _num(band.good, unit), _num(band.warn, unit)
    if band.higher_is_better:
        ranges = {Signal.GREEN: f"{good} 이상"}
        if band.good != band.warn:
            ranges[Signal.YELLOW] = f"{warn} 이상 {good} 미만"
        ranges[Signal.RED] = f"{warn} 미만"
        return ranges
    ranges = {Signal.GREEN: f"{good} 이하"}
    if band.good != band.warn:
        ranges[Signal.YELLOW] = f"{good} 초과 {warn} 이하"
    ranges[Signal.RED] = f"{warn} 초과"
    return ranges


def threshold_text(key: str, sector: Sector = Sector.GENERAL) -> str:
    """경계값 설명 문장. 숫자는 전부 engine 의 Band 에서 온다."""
    spec = _engine_def(key)
    reason = engine.NOT_APPLICABLE_BY_SECTOR.get(sector, {}).get(key)
    if reason is not None:
        return f"{sector.label}에는 이 지표로 판정하지 않아요(⊘ 해당 없음)."
    band = spec.band_for(sector)
    if band is None:
        return "업종마다 차이가 커서 경계값을 두지 않아요. 값만 ⚪ 측정값으로 보여 줘요."
    parts = [
        f"{if_(phrase)} {signal.emoji} {signal.label}"
        for signal, phrase in band_ranges(band, spec.unit).items()
    ]
    text = is_(", ".join(parts)) + "."
    if band.good == band.warn:
        text += " 🟡 주의 구간은 없어요."
    if sector is not Sector.GENERAL and spec.sector_bands and sector in spec.sector_bands:
        text = f"{sector.label} 기준으로는 " + text
    return text


# ── 해설 ───────────────────────────────────────────────────────────

_PROFIT_BASIS = (
    "업종마다 '좋은 수익성'이 달라서 절대 기준 대신 실제 상장사 분포에 맞췄어요. "
    "🟢 경계는 2024 사업연도 일반업종 상장사 880곳의 상위 25% 근처이고, "
    "🟡과 🔴는 흑자와 적자(0)로 갈라요."
)

_GROWTH_BASIS = (
    "0%는 작년보다 늘었는지 줄었는지를 가르는 선이에요. "
    "그 위에 🟢 경계를 하나 더 두어 크게 늘어난 경우를 구분해요."
)


def _turnaround_rules(noun: str) -> tuple[SpecialRule, ...]:
    return (
        SpecialRule(
            f"작년 {noun}이 적자였는데 올해 흑자가 되면 증가율 대신 '흑자 전환'으로 표시해요.",
            Signal.GREEN,
            f"작년 {noun} 적자에서 올해 흑자로 돌아섰어요(흑자 전환).",
        ),
        SpecialRule(
            f"작년에 이어 올해도 {noun}이 적자면 손실 폭이 줄었든 커졌든 '적자 지속'으로 표시해요.",
            Signal.RED,
            f"작년에 이어 올해도 {noun}이 적자예요(적자 지속).",
            note_marker="적자가 이어지고",
        ),
        SpecialRule(
            f"작년 {noun}이 흑자였는데 올해 적자면 증가율과 함께 '적자 전환'으로 표시해요.",
            Signal.RED,
            f"작년 {noun} 흑자에서 올해 적자로 돌아섰어요(적자 전환).",
            note_marker="흑자에서 적자로",
        ),
        SpecialRule(
            f"작년 {noun}이 정확히 0이면 증가율을 낼 수 없어 비워 둬요.",
            Signal.UNKNOWN,
            f"작년 {noun}이 정확히 0이라 증가율을 낼 수 없어요.",
        ),
    )


def _financial(why: str, instead: str) -> tuple[SectorCaveat, ...]:
    """금융업 규칙은 은행·증권·금융지주에도 그대로 적용된다(engine.BANKING_NOT_APPLICABLE ⊃ 금융업)."""
    return tuple(SectorCaveat(s, why=why, instead=instead) for s in (Sector.FINANCIAL, Sector.BANKING))


#: 은행·증권·금융지주의 매출 기반 지표(engine.NO_SALES_REASON).
_NO_SALES = SectorCaveat(
    Sector.BANKING,
    why=(
        "은행·금융지주에는 '매출' 개념이 없어 매출 기반 지표가 뜻이 없어요. 증권사가 적는 영업수익도 "
        "주식·채권·파생상품을 사고팔거나 평가해서 생긴 이익을 손실과 따로 총액으로 담아서(2024 미래에셋증권은 "
        "영업수익 22.2조 원의 63%) 매출이 아니에요."
    ),
    instead="이 페이지의 ROE·ROA, 사업보고서의 순이자마진(NIM)·영업이익경비율(CIR)",
)

_ENTRIES: tuple[MetricExplainer, ...] = (
    # ── 수익성 ─────────────────────────────────────────────────
    MetricExplainer(
        key="operating_margin",
        easy_name="장사해서 남는 비율",
        one_liner=(
            "물건이나 서비스를 팔아 번 돈(매출) 100원에서 재료비·인건비·임대료 같은 "
            "장사 비용을 빼고 남는 돈이 몇 원인지예요."
        ),
        why_look="회사가 본업 자체로 돈을 남기는 힘을 보여 줘요. 이자·세금·투자 손익이 섞이기 전의 숫자라 본업만 따로 볼 수 있어요.",
        basis=_PROFIT_BASIS,
        pitfalls=(
            "업종마다 보통 수준이 크게 달라요. 유통처럼 많이 팔고 적게 남기는 업종은 원래 낮아요. 같은 업종 중앙값과 함께 보세요.",
            "한 해 숫자만으로는 일시적인 변화인지 알 수 없어요. 몇 해 흐름을 같이 보세요.",
            "매출이 아주 작은 해(이익이나 손실이 매출의 10배 넘게 큰 해)에는 −20만 %처럼 큰 숫자가 나와요. 계산은 맞아서 그대로 보여 주고, 카드에 그 이유를 적어요.",
        ),
        sector_caveats=(_NO_SALES,),
    ),
    MetricExplainer(
        key="net_margin",
        easy_name="최종적으로 남는 비율",
        one_liner=(
            "매출 100원에서 장사 비용은 물론 이자·세금, 본업 밖에서 생긴 이익과 손실까지 "
            "모두 반영하고 마지막에 남은 돈이 몇 원인지예요."
        ),
        why_look="주주 몫으로 최종 남는 이익의 크기를 매출과 견줘 보여 줘요.",
        basis=_PROFIT_BASIS,
        pitfalls=(
            "건물·땅·자회사 지분을 팔아 생긴 이익처럼 한 번뿐인 이익(일회성 이익)이 섞이면 크게 뛰어요. 영업이익률과 차이가 크면 그 이유를 공시에서 확인해 보세요.",
            "연결재무제표가 있으면 연결 당기순이익을 써서, 자회사의 다른 주주 몫(비지배지분)까지 들어가 있어요.",
        ),
        sector_caveats=(_NO_SALES,),
    ),
    MetricExplainer(
        key="roe",
        easy_name="주주 돈으로 번 비율",
        one_liner="주주가 회사에 맡겨 둔 돈(자기자본) 100원으로 1년 동안 순이익을 몇 원 벌었는지예요.",
        why_look="주주 입장에서 내 돈이 회사 안에서 얼마나 불어나고 있는지를 한 숫자로 보여 줘요.",
        basis=_PROFIT_BASIS,
        pitfalls=(
            "빚을 많이 쓰면 자기자본이 작아져 ROE가 높게 나올 수 있어요. 부채비율과 함께 보세요.",
            "순이익으로 계산해서 일회성 이익의 영향을 그대로 받아요.",
            "연말 자기자본 하나로 나눠요. 기초·기말 평균으로 나누는 곳과는 값이 조금 다를 수 있어요.",
        ),
        special_rules=(
            SpecialRule(
                "자기자본이 0 이하(자본잠식)면 비율이 뜻을 잃어 계산하지 않아요.",
                Signal.UNKNOWN,
                "자기자본이 0 이하(자본잠식)라 비율이 뜻을 잃어 계산하지 않았어요.",
            ),
        ),
    ),
    MetricExplainer(
        key="roa",
        easy_name="전체 자산으로 번 비율",
        one_liner="회사가 가진 모든 자산(빌린 돈으로 마련한 것까지) 100원으로 1년 동안 순이익을 몇 원 벌었는지예요.",
        why_look="빚을 얼마나 썼는지와 상관없이, 가진 자산 전체를 얼마나 알차게 굴렸는지 보여 줘요.",
        basis=(
            _PROFIT_BASIS
            + " 은행·보험은 자산이 매우 커서 ROA가 원래 낮기 때문에 금융업에는 따로 낮은 경계값을 써요."
        ),
        pitfalls=(
            "공장·설비가 많은 업종은 자산이 커서 ROA가 낮게 나오기 쉬워요.",
            "연말 총자산 하나로 나눠요. 평균 자산으로 나누는 곳과는 값이 조금 다를 수 있어요.",
        ),
    ),
    # ── 안정성 ─────────────────────────────────────────────────
    MetricExplainer(
        key="debt_ratio",
        easy_name="내 돈 대비 빚",
        one_liner="회사 자기 돈(자기자본)이 100원일 때 갚아야 할 돈(부채)이 몇 원인지예요.",
        why_look="빚이 자기 돈보다 훨씬 크면 매출이 줄거나 금리가 오를 때 버티는 힘이 약해져요.",
        basis=(
            "'갚을 수 있는가'는 업종과 상관없이 뜻이 있어서 시장 분포에 맞추지 않은 절대 기준이에요. "
            "참고로 2024 사업연도 일반업종 상장사의 중앙값은 63.5%였어요."
        ),
        pitfalls=(
            "부채가 모두 이자를 내는 빚은 아니에요. 미리 받은 돈(선수금)이나 리스부채도 부채에 들어가요.",
            "항공·해운처럼 리스가 많은 업종은 높게 나오기 쉬워요. 그래도 실제로 갚아야 하는 돈이라 일부러 ⊘로 빼지 않았어요.",
        ),
        sector_caveats=(
            *_financial(
                why="은행은 고객 예금이, 보험사는 보험금을 줄 준비금이 부채로 잡혀서 1,000%가 넘는 게 보통이에요.",
                instead="은행은 BIS 자기자본비율, 보험사는 지급여력비율(K-ICS)",
            ),
        ),
        special_rules=(
            SpecialRule(
                "자기자본이 0 이하(자본잠식)면 비율을 계산하지 않고 바로 🔴로 표시해요.",
                Signal.RED,
                "자기자본이 0 이하(자본잠식)라 비율 없이 🔴로 표시했어요.",
            ),
        ),
    ),
    MetricExplainer(
        key="current_ratio",
        easy_name="1년 안 빚 감당력",
        one_liner=(
            "1년 안에 갚아야 할 빚(유동부채) 100원에 대해, 1년 안에 현금으로 바꿀 수 있는 "
            "자산(유동자산)이 몇 원 있는지예요."
        ),
        why_look="당장 돌아오는 청구서를 감당할 여유가 있는지 보여 줘요. 100%보다 낮으면 1년 안 빚이 그만한 자산보다 많다는 뜻이에요.",
        basis="1년 안 빚과 자산을 견주는 절대 기준이에요. 참고로 2024 사업연도 일반업종 상장사의 중앙값은 167.4%였어요.",
        pitfalls=(
            "유동자산에는 재고도 들어가요. 잘 안 팔리는 재고가 많으면 비율이 높아도 실제 현금 사정은 다를 수 있어요.",
            "건설·조선처럼 미리 받은 대금이 유동부채로 잡히는 업종은 낮게 나오기도 해요.",
        ),
        sector_caveats=(
            *_financial(
                why="금융회사 재무상태표는 유동·비유동을 나누지 않아서 이 비율을 만들 수 없어요.",
                instead="은행은 감독 규제 지표인 유동성커버리지비율(LCR) 같은 별도 유동성 지표",
            ),
        ),
    ),
    MetricExplainer(
        key="interest_coverage",
        easy_name="이자 감당력",
        one_liner="본업으로 번 돈(영업이익)이 1년 이자비용의 몇 배인지예요. 1배보다 작으면 번 돈으로 이자도 다 못 낸다는 뜻이에요.",
        why_look="빚 자체보다 빚의 이자를 감당하고 있는지를 보여 줘요. 1 미만이 여러 해 이어지는 회사를 흔히 '좀비기업'이라고 불러요.",
        basis="🔴 경계는 '번 돈 = 이자'가 되는 절대적인 선이에요. 2024년 실측에서 색을 매긴 상장사 10곳 중 4곳이 🔴였고 대부분은 영업적자였어요. 경계가 틀려서가 아니라 실제로 그만큼 많다는 뜻이에요.",
        pitfalls=(
            "손익계산서에 이자비용 항목을 따로 적는 회사는 드물어요(2024년 상장사의 약 6%). 없으면 현금흐름표의 '이자의 지급'으로 계산해요. 전환사채 이자처럼 현금이 나가지 않은 이자는 빠져서 배율이 실제보다 높게 나올 수 있어요.",
            "미국 기업은 이자비용 총액이 없으면 이자 지급액(InterestPaidNet)으로 계산해요. 순이자비용(이자수익을 뺀 값)만 있으면 배율이 부풀려져서 색을 매기지 않아요.",
            "영업이익이 적자면 음수가 나와요.",
            "한 해만 1 미만이면 일시적일 수 있어요. 여러 해 연속인지는 위험 신호에서 따로 봐요.",
        ),
        sector_caveats=(
            *_financial(
                why="금융회사에게 이자는 빌린 돈의 값이 아니라 장사의 원가(예금 이자 등)라서 1 미만이 흔하고, 갚을 능력을 나타내지 않아요.",
                instead="BIS 자기자본비율 같은 규제 자본비율, 그리고 이자 장사의 수익성을 보는 순이자마진(NIM)",
            ),
        ),
        special_rules=(
            SpecialRule(
                "이자비용이 0으로 공시되면 사실상 빚이 없다고 보고 값 없이 🟢로 표시해요.",
                Signal.GREEN,
                "이자비용이 0으로 공시돼 사실상 빚이 없다고 보고 🟢로 표시했어요.",
            ),
            SpecialRule(
                "이자비용도 이자 지급액도 없으면 '금융원가'로 계산만 하고 색은 매기지 않아요. "
                "금융원가에는 환율 손실처럼 이자가 아닌 비용이 섞여 배율이 실제보다 낮게 나와요. "
                "업종 비교에도 넣지 않아요.",
                Signal.NEUTRAL,
                "이자비용 항목도 이자 지급액도 없어 금융원가로 계산했어요. 환율 손실처럼 이자가 아닌 "
                "비용이 섞여 실제보다 낮게 나올 수 있어 색을 매기지 않았어요.",
                note_marker="금융원가",
            ),
            SpecialRule(
                "미국 기업이 이자비용 총액도 이자 지급액도 없이 '순이자비용'만 공시하면 계산만 하고 "
                "색은 매기지 않아요. 순이자비용은 이자비용에서 이자수익을 뺀 값이라 배율이 실제보다 "
                "높게 나와요. 업종 비교에도 넣지 않아요.",
                Signal.NEUTRAL,
                "이자비용 총액이 없어 순이자비용(이자수익을 뺀 값)으로 계산했어요. 실제보다 높게 나올 수 "
                "있어 색을 매기지 않았어요.",
                note_marker="순이자비용",
            ),
        ),
    ),
    MetricExplainer(
        key="equity_ratio",
        easy_name="자산 중 내 돈 비중",
        one_liner="회사 전체 자산 100원 중에서 빌린 돈이 아닌 자기 돈(자기자본)이 몇 원인지예요.",
        why_look="자기 돈 비중이 낮을수록 손실이 났을 때 자본이 바닥날(자본잠식) 여유가 적어요.",
        basis="자기 돈 비중이라는 절대 기준이에요. 참고로 2024 사업연도 일반업종 상장사의 중앙값은 61.0%였어요.",
        pitfalls=(
            "부채비율과 같은 이야기를 다른 쪽에서 본 거예요. 자기자본비율 50%는 부채비율 100%와 같아요.",
        ),
        sector_caveats=(
            *_financial(
                why="남의 돈(예금·보험료)으로 영업하는 업종이라 한 자릿수가 보통이에요.",
                instead="BIS 자기자본비율·지급여력비율(K-ICS) 같은 규제 자본비율",
            ),
        ),
    ),
    # ── 성장성 ─────────────────────────────────────────────────
    MetricExplainer(
        key="revenue_growth",
        easy_name="매출이 늘었나",
        one_liner="작년보다 매출이 몇 % 늘었는지(마이너스면 줄었는지)예요.",
        why_look="회사가 파는 양이나 값이 커지고 있는지 보여 줘요. 이익은 비용에 따라 흔들리지만 매출은 사업 규모 자체예요.",
        basis=_GROWTH_BASIS,
        pitfalls=(
            "다른 회사를 사들이거나 사업을 떼어 내면 본업과 상관없이 크게 움직여요.",
            "작년 재무가 없으면 계산하지 않아요(⚫).",
        ),
        sector_caveats=(_NO_SALES,),
        special_rules=(
            SpecialRule(
                "작년 매출액이 0이면 증가율을 낼 수 없어 비워 둬요.",
                Signal.UNKNOWN,
                "작년 매출액이 0이라 증가율을 낼 수 없어요.",
            ),
        ),
    ),
    MetricExplainer(
        key="operating_income_growth",
        easy_name="본업 이익이 늘었나",
        one_liner="작년보다 영업이익이 몇 % 늘었는지(마이너스면 줄었는지)예요.",
        why_look="매출이 늘면서 본업 이익도 함께 늘고 있는지 보여 줘요.",
        basis=_GROWTH_BASIS,
        pitfalls=(
            "작년 이익이 아주 작으면 증가율이 수백 %로 튀어요. 금액도 함께 보세요.",
            "작년이 적자면 %가 뜻을 잃어서 숫자 대신 흑자 전환·적자 지속으로 보여 줘요.",
        ),
        special_rules=_turnaround_rules("영업이익"),
    ),
    MetricExplainer(
        key="net_income_growth",
        easy_name="최종 이익이 늘었나",
        one_liner="작년보다 당기순이익이 몇 % 늘었는지(마이너스면 줄었는지)예요.",
        why_look="주주 몫으로 남는 최종 이익의 방향을 보여 줘요.",
        basis=_GROWTH_BASIS,
        pitfalls=(
            "일회성 이익·손실이 있던 해와 견주면 크게 튀어요. 영업이익 증가율과 함께 보세요.",
            "작년이 적자면 %가 뜻을 잃어서 숫자 대신 흑자 전환·적자 지속으로 보여 줘요.",
        ),
        special_rules=_turnaround_rules("순이익"),
    ),
    # ── 이익의 질 ───────────────────────────────────────────────
    MetricExplainer(
        key="operating_cash_flow",
        easy_name="본업으로 실제 들어온 현금",
        one_liner="장부상 이익과 따로, 본업으로 실제 통장에 들어오거나 나간 현금이에요.",
        why_look="이익은 외상으로 판 것도 포함하지만 현금은 실제로 들어와야 잡혀요. 이익이 나는데 현금이 계속 나가면 그 이유를 볼 필요가 있어요.",
        basis="0은 현금이 들어왔는지 나갔는지를 가르는 선이에요. 그래서 🟡 구간 없이 🟢·🔴 둘로만 나눠요.",
        pitfalls=(
            "조선처럼 대금을 나중에 몰아서 받는 업종은 만드는 동안 현금이 나가 음수가 되기 쉬워요. 그래도 유출은 유출이라 그대로 표시해요.",
            "금액이라 회사 크기에 따라 자릿수가 달라요. 다른 회사와는 금액보다 부호(+/−)와 추이를 견주세요.",
        ),
        special_rules=(
            SpecialRule(
                "장부상 흑자인데 영업현금이 유출됐으면 🔴로 표시하고 그 사실을 따로 적어요.",
                Signal.RED,
                "장부상으로는 흑자인데 영업현금은 빠져나갔어요.",
            ),
        ),
    ),
    MetricExplainer(
        key="ocf_to_net_income",
        easy_name="이익이 현금으로 들어온 정도",
        one_liner="장부상 순이익 1원당 실제로 들어온 영업현금이 몇 원인지예요.",
        why_look="이익이 진짜 현금으로 뒷받침되는지 보여 줘요. 1보다 크게 낮으면 장부 이익과 현금 사이에 차이가 커요.",
        basis="🟢 경계는 '번 만큼 현금이 들어왔다'에 해당하는 선이고, 그보다 한참 모자라면 🔴로 갈라요.",
        pitfalls=(
            "설비가 많은 회사는 감가상각비(현금이 나가지 않는 비용) 때문에 1보다 크게 나오는 게 흔해요.",
            "외상 판매가 늘면 이익은 잡혀도 현금이 덜 들어와 낮아져요.",
        ),
        special_rules=(
            SpecialRule(
                "적자 회사는 비율의 뜻이 뒤집혀(적자에 현금이 들어와도 음수가 됨) 계산하지 않아요. 영업활동현금흐름 자체를 보세요.",
                Signal.NOT_APPLICABLE,
                "올해 적자라 이 비율은 뜻이 뒤집혀 계산하지 않았어요. 영업활동현금흐름 자체를 보세요.",
            ),
        ),
    ),
    MetricExplainer(
        key="fcf",
        easy_name="쓰고 남는 현금",
        one_liner="본업으로 번 현금에서 공장·설비를 사는 데 쓴 돈(설비투자)을 빼고 남은 현금이에요.",
        why_look="배당을 주거나, 빚을 갚거나, 새 사업에 쓸 수 있는 여윳돈이 실제로 생기는지 보여 줘요.",
        basis="0은 여윳돈이 남았는지 모자랐는지를 가르는 선이에요. 그래서 🟡 구간 없이 🟢·🔴 둘로만 나눠요.",
        pitfalls=(
            "성장 중인 회사는 투자를 많이 해서 일부러 음수가 되기도 해요. 한 해보다 몇 해 흐름으로 보세요.",
            "설비투자는 현금흐름표의 '유형자산의 취득' 금액이에요. 소프트웨어·개발비 같은 무형자산 투자는 빠져 있어요. 합계 줄 없이 토지·건물·기계장치처럼 종류별로만 적은 회사는 그 줄들을 더해요.",
            "유형자산과 무형자산 취득을 한 줄로 적은 회사는 무형자산 취득까지 빼서 계산돼 조금 낮게 나와요. 그 경우 카드에 따로 적어요.",
            "금액이라 회사 크기에 따라 자릿수가 달라요.",
        ),
    ),
    # ── 효율성 ─────────────────────────────────────────────────
    MetricExplainer(
        key="asset_turnover",
        easy_name="자산으로 매출을 몇 번 돌렸나",
        one_liner="가진 자산 1원으로 1년 동안 매출을 몇 원 올렸는지예요(자산을 몇 바퀴 돌렸는지).",
        why_look="같은 업종 안에서 자산을 얼마나 바쁘게 쓰는지, 작년보다 늘었는지 줄었는지 볼 때 쓰여요.",
        basis="유통처럼 자산이 적고 많이 파는 업종과 공장이 큰 업종의 차이가 너무 커서 정해진 경계값이 없어요.",
        pitfalls=("다른 업종 회사와 견주면 뜻이 없어요. 같은 업종이나 같은 회사의 과거와 견주세요.",),
        sector_caveats=(
            *_financial(
                why="금융회사는 큰 자산(대출·투자)이 영업의 결과가 아니라 영업의 전제라서 회전율이 뜻을 잃어요.",
                instead="금융업 기준 경계값을 쓰는 ROA (총자산이익률)",
            ),
        ),
    ),
    MetricExplainer(
        key="inventory_turnover",
        easy_name="재고가 팔리는 속도",
        one_liner="1년 매출이 연말 재고자산의 몇 배인지예요. 클수록 재고가 빨리 팔려 나간다는 뜻이에요.",
        why_look="같은 회사에서 이 숫자가 줄어들면 창고에 재고가 쌓이고 있을 수 있어요.",
        basis="업종마다 재고 성격이 달라서(신선식품과 선박을 같은 자로 잴 수 없어요) 정해진 경계값이 없어요.",
        pitfalls=(
            "교과서는 보통 매출원가로 나누지만 여기서는 매출액으로 나눠요. 다른 곳의 숫자와 다를 수 있어요.",
            "연말 재고 하나로 나눠요. 연말에 재고가 일시적으로 많거나 적으면 흔들려요.",
            "재고가 하루 매출보다 적으면(365회 넘게 회전) 수만 회처럼 큰 숫자가 나와요. 재고를 거의 두지 않는 사업이라 계산은 맞고, 카드에 그 이유를 적어요.",
        ),
        sector_caveats=(
            *_financial(
                why="금융회사에는 재고자산이 없어요.",
                instead="ROA·ROE 같은 수익성 지표",
            ),
            SectorCaveat(
                Sector.REAL_ESTATE,
                why="재고자산이 분양용 땅·건물이라 한 바퀴 도는 데 몇 년이 걸려요.",
                instead="사업보고서 본문의 사업별 분양·공사 진행 현황과 영업활동현금흐름",
            ),
        ),
        special_rules=(
            SpecialRule(
                "재고자산이 0으로 공시된 회사(서비스업 등)는 회전율을 계산하지 않아요.",
                Signal.NOT_APPLICABLE,
                "재고자산이 0으로 공시돼 회전율을 계산하지 않았어요.",
            ),
        ),
    ),
    MetricExplainer(
        key="receivable_turnover",
        easy_name="외상값 받는 속도",
        one_liner="1년 매출이 연말 매출채권(아직 못 받은 외상값)의 몇 배인지예요. 클수록 외상값을 빨리 받는다는 뜻이에요.",
        why_look="같은 회사에서 이 숫자가 줄어들면 못 받은 돈이 쌓이고 있을 수 있어요.",
        basis="현금 장사를 하는 소매업과 몇 달 뒤 대금을 받는 납품업의 차이가 커서 정해진 경계값이 없어요.",
        pitfalls=(
            "매출채권 항목이 따로 있으면 그것으로 나눠요. 2024년 상장사 절반가량은 '매출채권및기타채권' 합계만 공시해서, 그때는 미수금 같은 다른 채권까지 들어가 회전율이 조금 낮게 나와요(둘 다 공시한 회사에서 차이의 중앙값 약 4%). 그 경우 카드에 따로 적어요.",
            "연말 잔액 하나로 나눠요. 연말에 매출이 몰리는 회사는 낮게 나올 수 있어요.",
            "매출채권이 하루 매출보다 적으면(365회 넘게 회전) 아주 큰 숫자가 나와요. 외상 없이 바로 대금을 받는 사업일 수 있어 그대로 보여 주고, 카드에 그 이유를 적어요.",
        ),
        sector_caveats=(
            *_financial(
                why="금융회사는 매출채권 대신 대출채권으로 영업해요.",
                instead="대출의 건전성을 보는 고정이하여신비율(부실채권 비율)",
            ),
        ),
        special_rules=(
            SpecialRule(
                "매출채권이 0으로 공시된 회사는 회전율을 계산하지 않아요.",
                Signal.NOT_APPLICABLE,
                "매출채권이 0으로 공시돼 회전율을 계산하지 않았어요.",
            ),
        ),
    ),
)

METRIC_EXPLAINERS: dict[str, MetricExplainer] = {e.key: e for e in _ENTRIES}


def get_explainer(key: str) -> MetricExplainer:
    """지표 key 의 해설. 없는 key 면 KeyError."""
    return METRIC_EXPLAINERS[key]


def explainers() -> tuple[MetricExplainer, ...]:
    """엔진 정의 순서대로 (화면 순서와 같다)."""
    return tuple(METRIC_EXPLAINERS[spec.key] for spec in engine.METRIC_DEFS)
