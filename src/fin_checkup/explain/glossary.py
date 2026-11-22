"""용어집 — 화면에서 처음 만나는 말을 한 줄로.

정의는 한 줄, 쉬운 말. 회계 기준의 세부 예외까지 다 담지 않는다 — 틀리게 단순화할 바엔
덜 말한다. 지표·위험 신호·공시 유형과 이어지는 용어는 그 key 로 링크한다
(`tests/test_explain_glossary.py` 가 링크 대상이 실제로 있는지 본다).
"""

from __future__ import annotations

from dataclasses import dataclass

from fin_checkup.alerts.classify import RiskKind
from fin_checkup.metrics.peers import MIN_PEERS
from fin_checkup.metrics.sector import FINANCIAL_PREFIXES, PEER_GROUP_DIGITS, REAL_ESTATE_PREFIXES


@dataclass(frozen=True)
class GlossaryTerm:
    #: URL 앵커용 영문 식별자 (소문자·하이픈).
    slug: str
    term: str
    #: 한 줄 정의.
    definition: str
    aliases: tuple[str, ...] = ()
    #: 관련 지표 key (explain.metrics).
    metrics: tuple[str, ...] = ()
    #: 관련 위험 신호 key (explain.redflags).
    red_flags: tuple[str, ...] = ()
    #: 관련 위험 공시 유형.
    disclosures: tuple[RiskKind, ...] = ()
    #: 함께 볼 용어 slug.
    see_also: tuple[str, ...] = ()


_T = GlossaryTerm


def _code_range(prefixes: frozenset[str]) -> str:
    codes = sorted(prefixes)
    return codes[0] if len(codes) == 1 else f"{codes[0]}~{codes[-1]}"


_FIN = _code_range(FINANCIAL_PREFIXES)
_RE = _code_range(REAL_ESTATE_PREFIXES)

GLOSSARY: tuple[GlossaryTerm, ...] = (
    # ── 재무제표 세 가지와 보고서 ─────────────────────────────────
    _T(
        "dart",
        "DART (전자공시시스템)",
        "금융감독원이 운영하는 공시 사이트예요. 상장사가 낸 보고서를 누구나 무료로 볼 수 있고, 이 도구의 숫자도 모두 여기서 왔어요.",
        aliases=("전자공시",),
    ),
    _T(
        "balance-sheet",
        "재무상태표",
        "특정 날짜(보통 연말)에 회사가 가진 것(자산)과 갚아야 할 것(부채), 그 차이인 주주 몫(자본)을 적은 표예요.",
        aliases=("대차대조표",),
        see_also=("assets", "liabilities", "equity"),
    ),
    _T(
        "income-statement",
        "손익계산서",
        "1년 동안 얼마를 팔았고(매출) 비용을 빼고 얼마가 남았는지(이익)를 적은 표예요.",
        see_also=("revenue", "operating-income", "net-income"),
    ),
    _T(
        "cash-flow-statement",
        "현금흐름표",
        "1년 동안 현금이 실제로 어디서 들어오고 어디로 나갔는지를 영업·투자·재무 활동으로 나눠 적은 표예요.",
        see_also=("operating-cash-flow", "capex"),
    ),
    _T(
        "consolidated",
        "연결재무제표",
        "회사가 지배하는 자회사까지 한 회사처럼 합쳐서 만든 재무제표예요. 이 도구는 연결이 있으면 연결을, 없으면 별도를 써요.",
        aliases=("연결",),
        see_also=("separate", "non-controlling-interest"),
    ),
    _T(
        "separate",
        "별도재무제표",
        "자회사를 합치지 않고 그 회사 하나만의 숫자로 만든 재무제표예요.",
        aliases=("별도", "개별"),
        see_also=("consolidated",),
    ),
    _T(
        "fiscal-year",
        "사업연도",
        "재무제표가 다루는 1년이에요. 대부분 1월 1일부터 12월 31일까지지만 회사마다 다를 수 있어요.",
        aliases=("회계연도",),
    ),
    _T(
        "annual-report",
        "사업보고서",
        "1년에 한 번, 사업연도가 끝나고 90일 안에 내는 가장 자세한 정기 보고서예요. 감사받은 연간 재무제표가 들어 있어요.",
        see_also=("half-year-report", "audit-report"),
    ),
    _T(
        "half-year-report",
        "반기보고서",
        "사업연도 앞 6개월의 실적을 담아 반기가 끝나고 45일 안에 내는 보고서예요. 재무제표는 감사가 아니라 검토를 받아요.",
        see_also=("annual-report",),
    ),
    _T(
        "major-report",
        "주요사항보고서",
        "유상증자·감자·사채 발행·부도처럼 회사에 중요한 일이 정해졌을 때 바로 내야 하는 보고서예요.",
        disclosures=(
            RiskKind.CAPITAL_RAISE,
            RiskKind.CAPITAL_REDUCTION,
            RiskKind.CONVERTIBLE_BOND,
            RiskKind.DISTRESS,
        ),
    ),
    _T(
        "correction",
        "기재정정",
        "이미 낸 공시의 내용을 고쳐서 다시 낸 공시예요. 제목 앞에 [기재정정]이 붙고, 본문 앞쪽에 무엇을 고쳤는지 적혀 있어요.",
        aliases=("정정공시",),
    ),
    # ── 손익 ───────────────────────────────────────────────────
    _T(
        "revenue",
        "매출액",
        "회사가 물건이나 서비스를 팔아 1년 동안 벌어들인 돈 전체예요. 금융회사는 영업수익이라고 적어요.",
        aliases=("매출", "영업수익"),
        metrics=("revenue_growth", "operating_margin", "net_margin", "asset_turnover"),
        see_also=("cost-of-sales",),
    ),
    _T(
        "cost-of-sales",
        "매출원가",
        "판 물건을 만들거나 사 오는 데 직접 든 비용이에요(재료비·공장 인건비 등).",
        see_also=("revenue", "sga"),
    ),
    _T(
        "sga",
        "판매비와관리비",
        "물건을 팔고 회사를 운영하는 데 드는 비용이에요(광고비·본사 인건비·임대료 등). 줄여서 판관비라고 해요.",
        aliases=("판관비",),
        see_also=("operating-income",),
    ),
    _T(
        "operating-income",
        "영업이익",
        "매출액에서 매출원가와 판매비와관리비를 뺀 값이에요. 본업으로 번 이익이고, 음수면 영업손실이에요.",
        aliases=("영업손실",),
        metrics=("operating_margin", "operating_income_growth", "interest_coverage"),
        red_flags=("consecutive_operating_loss",),
    ),
    _T(
        "net-income",
        "당기순이익",
        "영업이익에 이자·투자 손익 같은 본업 밖의 손익을 더하고 빼고, 세금까지 낸 뒤 최종으로 남은 이익이에요.",
        aliases=("순이익", "당기순손실"),
        metrics=("net_margin", "roe", "roa", "net_income_growth", "ocf_to_net_income"),
        see_also=("one-off-gain",),
    ),
    _T(
        "one-off-gain",
        "일회성 이익",
        "건물·땅·자회사 지분 매각처럼 매년 되풀이되지 않는 일로 생긴 이익이에요. 순이익을 한 해만 크게 부풀릴 수 있어요.",
        metrics=("net_margin", "net_income_growth"),
    ),
    _T(
        "interest-expense",
        "이자비용",
        "빌린 돈에 대해 1년 동안 낸 이자예요. 따로 적혀 있지 않으면 이 도구는 금융원가로 대신 계산해요.",
        metrics=("interest_coverage",),
        see_also=("finance-cost",),
    ),
    _T(
        "finance-cost",
        "금융원가",
        "이자비용과 그 밖의 금융 관련 비용을 함께 묶어 적은 항목이에요. 회사마다 묶는 범위가 달라 이자비용보다 클 수 있어요.",
        aliases=("금융비용",),
        metrics=("interest_coverage",),
    ),
    _T(
        "depreciation",
        "감가상각비",
        "공장·기계처럼 오래 쓰는 자산의 값을 쓰는 기간에 나눠 비용으로 잡은 거예요. 현금이 실제로 나가지는 않아요.",
        metrics=("ocf_to_net_income",),
    ),
    _T(
        "turnaround",
        "흑자 전환",
        "작년에 적자(손실)였던 이익이 올해 흑자로 돌아선 거예요. 반대로 적자가 이어지면 적자 지속이라고 해요.",
        aliases=("적자 지속",),
        metrics=("operating_income_growth", "net_income_growth"),
    ),
    # ── 재무상태 ────────────────────────────────────────────────
    _T(
        "assets",
        "자산",
        "회사가 가진 모든 것의 값이에요(현금·재고·공장·받을 돈 등). 전부 더한 것을 자산총계(총자산)라고 해요.",
        aliases=("총자산", "자산총계"),
        metrics=("roa", "asset_turnover", "equity_ratio"),
    ),
    _T(
        "liabilities",
        "부채",
        "회사가 앞으로 갚거나 내줘야 할 것이에요(빌린 돈·외상값·미리 받은 돈 등). 전부 더한 것이 부채총계예요.",
        aliases=("부채총계",),
        metrics=("debt_ratio",),
    ),
    _T(
        "equity",
        "자본",
        "자산에서 부채를 뺀 주주 몫이에요. 자기자본·자본총계라고도 해요.",
        aliases=("자기자본", "자본총계"),
        metrics=("roe", "debt_ratio", "equity_ratio"),
        see_also=("capital-stock", "capital-impairment"),
    ),
    _T(
        "capital-stock",
        "자본금",
        "주주가 주식을 받고 회사에 넣은 돈 가운데 '주식 수 × 액면가'만큼을 적은 금액이에요.",
        see_also=("equity", "capital-impairment"),
    ),
    _T(
        "non-controlling-interest",
        "비지배지분",
        "연결재무제표에서 자회사 지분 가운데 모회사가 아닌 다른 주주의 몫이에요.",
        see_also=("consolidated",),
    ),
    _T(
        "current-assets",
        "유동자산",
        "1년 안에 현금으로 바꿀 수 있는 자산이에요(현금·단기 예금·매출채권·재고 등).",
        metrics=("current_ratio",),
    ),
    _T(
        "current-liabilities",
        "유동부채",
        "1년 안에 갚아야 하는 부채예요(단기 차입금·외상 매입금 등).",
        metrics=("current_ratio",),
    ),
    _T(
        "inventories",
        "재고자산",
        "팔려고 가지고 있는 물건과 만드는 중인 제품, 원재료예요.",
        aliases=("재고",),
        metrics=("inventory_turnover",),
    ),
    _T(
        "receivables",
        "매출채권",
        "물건을 외상으로 팔고 아직 받지 못한 돈이에요.",
        aliases=("외상값",),
        metrics=("receivable_turnover",),
    ),
    # ── 현금흐름 ────────────────────────────────────────────────
    _T(
        "operating-cash-flow",
        "영업활동현금흐름",
        "본업을 하면서 1년 동안 실제로 들어오고 나간 현금의 차이예요.",
        aliases=("영업현금흐름",),
        metrics=("operating_cash_flow", "ocf_to_net_income", "fcf"),
        red_flags=("profit_without_cash",),
    ),
    _T(
        "capex",
        "설비투자",
        "공장·기계 같은 유형자산을 사는 데 쓴 돈이에요. 현금흐름표에는 '유형자산의 취득'으로 적혀 있어요.",
        aliases=("CapEx", "유형자산의 취득"),
        metrics=("fcf",),
    ),
    _T(
        "fcf",
        "잉여현금흐름",
        "영업활동현금흐름에서 설비투자를 뺀, 회사가 자유롭게 쓸 수 있는 현금이에요. 영어 약자로 FCF라고 해요.",
        aliases=("FCF",),
        metrics=("fcf",),
    ),
    # ── 위험 상태 ──────────────────────────────────────────────
    _T(
        "capital-impairment",
        "자본잠식",
        "손실이 쌓여 자본이 자본금보다 작아진 상태예요. 얼마나 작아졌는지를 자본금 대비 비율(자본잠식률)로 나타내요.",
        aliases=("자본잠식률",),
        red_flags=("partial_capital_impairment",),
        see_also=("full-capital-impairment", "capital-stock"),
    ),
    _T(
        "full-capital-impairment",
        "완전자본잠식",
        "자본이 0 이하가 된 상태예요. 장부상으로는 자산을 다 팔아도 빚을 갚고 나면 주주 몫이 남지 않아요.",
        red_flags=("full_capital_impairment",),
        see_also=("capital-impairment", "delisting"),
    ),
    _T(
        "marginal-firm",
        "한계기업",
        "영업이익으로 이자비용도 감당하지 못하는 상태(이자보상배율 1 미만)가 몇 해째 이어지는 회사를 흔히 부르는 말이에요. 좀비기업이라고도 해요.",
        aliases=("좀비기업",),
        metrics=("interest_coverage",),
        red_flags=("zombie_streak",),
    ),
    _T(
        "administrative-issue",
        "관리종목",
        "자본잠식·감사의견 등 거래소가 정한 요건에 걸려 상장폐지 가능성을 알리려고 따로 지정한 종목이에요.",
        disclosures=(RiskKind.LISTING_STATUS,),
        see_also=("delisting",),
    ),
    _T(
        "delisting",
        "상장폐지",
        "주식이 거래소에서 빠져 더는 시장에서 거래되지 않게 되는 거예요. 확정되면 정리매매 기간을 거쳐요.",
        disclosures=(RiskKind.LISTING_STATUS,),
        see_also=("administrative-issue", "trading-halt"),
    ),
    _T(
        "trading-halt",
        "매매거래정지",
        "거래소가 일정 기간 그 주식을 사고팔 수 없게 멈추는 거예요. 감자·액면분할 같은 일상 절차 때문에도 생겨요.",
        disclosures=(RiskKind.LISTING_STATUS,),
    ),
    _T(
        "rehabilitation",
        "회생절차",
        "빚을 갚기 어려운 회사가 법원의 관리 아래 빚을 조정하고 회사를 다시 세우는 절차예요. 법정관리라고도 해요.",
        aliases=("법정관리",),
        disclosures=(RiskKind.DISTRESS,),
    ),
    # ── 감사 ───────────────────────────────────────────────────
    _T(
        "audit-report",
        "감사보고서",
        "외부 회계법인(감사인)이 회사 재무제표가 기준에 맞게 작성됐는지 검사하고 의견을 적은 보고서예요.",
        disclosures=(RiskKind.AUDIT_OPINION,),
        see_also=("opinion-unqualified", "opinion-qualified", "opinion-adverse", "opinion-disclaimer"),
    ),
    _T(
        "opinion-unqualified",
        "적정 의견",
        "재무제표가 회계 기준에 맞게 작성됐다는 감사 의견이에요. 회사가 잘하고 있다는 뜻이 아니라 숫자를 믿을 수 있게 적었다는 뜻이에요.",
        aliases=("적정",),
        disclosures=(RiskKind.AUDIT_OPINION,),
    ),
    _T(
        "opinion-qualified",
        "한정 의견",
        "일부 항목이 기준에 맞지 않거나 확인할 수 없었지만 나머지는 적정하다는 감사 의견이에요.",
        aliases=("한정",),
        disclosures=(RiskKind.AUDIT_OPINION,),
    ),
    _T(
        "opinion-adverse",
        "부적정 의견",
        "재무제표가 전체적으로 회계 기준에 맞지 않게 작성됐다는 감사 의견이에요.",
        aliases=("부적정",),
        disclosures=(RiskKind.AUDIT_OPINION,),
    ),
    _T(
        "opinion-disclaimer",
        "의견거절",
        "감사인이 충분한 증거를 얻지 못해 재무제표에 대한 의견 자체를 낼 수 없다는 뜻이에요.",
        disclosures=(RiskKind.AUDIT_OPINION,),
    ),
    _T(
        "going-concern",
        "계속기업 불확실성",
        "감사인이 회사가 앞으로도 계속 영업할 수 있을지 중요한 의문이 있다고 감사보고서에 따로 적은 문단이에요.",
        aliases=("계속기업 관련 중요한 불확실성",),
        disclosures=(RiskKind.AUDIT_OPINION,),
    ),
    # ── 주식·자금 조달 ──────────────────────────────────────────
    _T(
        "rights-offering",
        "유상증자",
        "회사가 새 주식을 발행해 돈을 받고 파는 거예요. 주식 수가 늘어요.",
        disclosures=(RiskKind.CAPITAL_RAISE,),
        see_also=("dilution", "bonus-issue"),
    ),
    _T(
        "bonus-issue",
        "무상증자",
        "돈을 받지 않고 회사에 쌓인 자본(잉여금)을 자본금으로 옮기면서 기존 주주에게 새 주식을 나눠 주는 거예요.",
        see_also=("rights-offering",),
    ),
    _T(
        "capital-reduction",
        "감자",
        "자본금을 줄이는 거예요. 대가 없이 주식 수를 줄이면 무상감자, 주주에게 돈을 돌려주면 유상감자예요.",
        aliases=("무상감자", "유상감자"),
        disclosures=(RiskKind.CAPITAL_REDUCTION,),
        see_also=("capital-stock",),
    ),
    _T(
        "dilution",
        "지분 희석",
        "새 주식이 생겨 전체 주식 수가 늘면서 기존 주주가 가진 비율이 낮아지는 거예요.",
        aliases=("희석",),
        disclosures=(RiskKind.CAPITAL_RAISE, RiskKind.CONVERTIBLE_BOND),
    ),
    _T(
        "cb",
        "전환사채 (CB)",
        "나중에 정해진 가격으로 그 회사 주식으로 바꿀 수 있는 권리가 붙은 회사채예요.",
        aliases=("CB", "전환사채"),
        disclosures=(RiskKind.CONVERTIBLE_BOND,),
        see_also=("bw", "eb", "dilution"),
    ),
    _T(
        "bw",
        "신주인수권부사채 (BW)",
        "채권은 그대로 두고, 정해진 가격에 그 회사 새 주식을 살 수 있는 권리가 붙은 회사채예요.",
        aliases=("BW", "신주인수권부사채"),
        disclosures=(RiskKind.CONVERTIBLE_BOND,),
        see_also=("cb", "dilution"),
    ),
    _T(
        "eb",
        "교환사채 (EB)",
        "회사가 가진 다른 주식(자기주식이나 다른 회사 주식)으로 바꿀 수 있는 권리가 붙은 회사채예요.",
        aliases=("EB", "교환사채"),
        disclosures=(RiskKind.CONVERTIBLE_BOND,),
        see_also=("cb",),
    ),
    _T(
        "largest-shareholder",
        "최대주주",
        "특수관계인(가족·계열사 등)의 지분까지 합쳐 회사 주식을 가장 많이 가진 주주예요.",
        disclosures=(RiskKind.MAJOR_SHAREHOLDER,),
        see_also=("five-percent-rule",),
    ),
    _T(
        "principal-shareholder",
        "주요주주",
        "회사 주식을 10% 이상 가졌거나, 임원 선임 등으로 경영에 사실상 영향력을 행사하는 주주예요. 임원과 함께 보유 주식이 바뀌면 보고해야 해요.",
        disclosures=(RiskKind.INSIDER_TRADING,),
        see_also=("largest-shareholder",),
    ),
    _T(
        "five-percent-rule",
        "5% 룰 (대량보유 보고)",
        "상장사 주식을 5% 이상 갖게 되거나 그 뒤 비율이 1%포인트 이상 바뀌면 보유 목적과 함께 보고해야 하는 규칙이에요.",
        aliases=("대량보유상황보고", "5% 룰"),
        disclosures=(RiskKind.MAJOR_SHAREHOLDER,),
    ),
    # ── 비교·통계 ──────────────────────────────────────────────
    _T(
        "ksic",
        "KSIC (한국표준산업분류)",
        f"통계청이 정한 업종 분류 코드예요. 이 도구는 앞 {PEER_GROUP_DIGITS}자리로 같은 업종 회사를 묶고, {_FIN}은 금융업(지주회사는 금융지주만, 64 의 나머지는 재무상태표가 금융회사 모양일 때만), {_RE}은 부동산업으로 따로 봐요.",
        aliases=("표준산업분류", "업종코드"),
        see_also=("peer-group",),
    ),
    _T(
        "peer-group",
        "동종업계",
        f"같은 업종(KSIC 앞 {PEER_GROUP_DIGITS}자리)으로 묶인 회사들이에요. 견줄 회사가 {MIN_PEERS}곳이 안 되면 전체 상장사와 견줘요.",
        aliases=("같은 업종",),
        see_also=("median", "percentile", "ksic"),
    ),
    _T(
        "median",
        "중앙값",
        "값을 크기 순서로 줄 세웠을 때 한가운데 오는 값이에요. 아주 크거나 작은 값 몇 개에 덜 흔들려서 평균 대신 써요.",
        see_also=("percentile",),
    ),
    _T(
        "percentile",
        "백분위",
        "100명 가운데 몇 번째쯤인지로 위치를 나타낸 거예요. 이 도구의 '상위 20%'는 견준 회사들 가운데 앞쪽 20% 안이라는 뜻이고, 부채비율처럼 낮을수록 🟢에 가까운 지표는 낮은 쪽을 앞으로 세요.",
        aliases=("분위수",),
        see_also=("median", "peer-group"),
    ),
)

GLOSSARY_BY_SLUG: dict[str, GlossaryTerm] = {t.slug: t for t in GLOSSARY}


def get_term(slug: str) -> GlossaryTerm:
    """slug 로 찾는다. 없으면 KeyError."""
    return GLOSSARY_BY_SLUG[slug]


def find_term(word: str) -> GlossaryTerm | None:
    """용어 이름이나 다른 이름(별칭)으로 찾는다. 공백은 무시한다."""
    key = "".join(word.split())
    for t in GLOSSARY:
        names = (t.term, *t.aliases)
        if any("".join(n.split()) == key for n in names):
            return t
    return None


def terms_for_metric(metric_key: str) -> tuple[GlossaryTerm, ...]:
    """지표 해설 페이지에 붙일 관련 용어 (용어집 순서)."""
    return tuple(t for t in GLOSSARY if metric_key in t.metrics)
