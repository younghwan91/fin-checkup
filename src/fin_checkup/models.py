from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ReportCode(str, Enum):
    """DART 보고서 코드 (reprt_code)."""

    Q1 = "11013"
    HALF = "11012"
    Q3 = "11014"
    ANNUAL = "11011"

    @property
    def label(self) -> str:
        return {
            "11013": "1분기보고서",
            "11012": "반기보고서",
            "11014": "3분기보고서",
            "11011": "사업보고서",
        }[self.value]


class FsDiv(str, Enum):
    """개별(OFS) / 연결(CFS) 재무제표 구분."""

    CFS = "CFS"
    OFS = "OFS"


class SjDiv(str, Enum):
    """재무제표 종류 (sj_div)."""

    BS = "BS"  # 재무상태표
    IS = "IS"  # 손익계산서
    CIS = "CIS"  # 포괄손익계산서
    CF = "CF"  # 현금흐름표
    SCE = "SCE"  # 자본변동표


class Market(str, Enum):
    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"
    KONEX = "KONEX"
    OTHER = "OTHER"


CORP_CLS_TO_MARKET: dict[str, Market] = {
    "Y": Market.KOSPI,
    "K": Market.KOSDAQ,
    "N": Market.KONEX,
    "E": Market.OTHER,
}

#: 지금 거래소에 상장된 시장. `OTHER`(DART corp_cls 'E')는 종목코드가 남아 있어도
#: 상장사가 아니다 — 상장폐지·비상장 전환 회사의 옛 코드다(신한은행 000010 등).
LISTED_MARKETS: tuple[Market, ...] = (Market.KOSPI, Market.KOSDAQ, Market.KONEX)


class CorpCode(BaseModel):
    """DART 고유번호 매핑 한 건 (corpCode.xml)."""

    corp_code: str
    corp_name: str
    stock_code: str = ""
    modify_date: str = ""

    @property
    def is_listed(self) -> bool:
        return bool(self.stock_code.strip())


class Company(BaseModel):
    """기업개황 (company.json)."""

    corp_code: str
    corp_name: str
    stock_code: str = ""
    market: Market | None = None
    industry_code: str = ""
    ceo_name: str = ""
    established_date: str = ""
    fiscal_month: int = 12
    homepage: str = ""


class PblntfTy(str, Enum):
    """공시유형 (list.json의 pblntf_ty)."""

    PERIODIC = "A"  # 정기공시
    MAJOR = "B"  # 주요사항보고
    ISSUANCE = "C"  # 발행공시
    OWNERSHIP = "D"  # 지분공시
    ETC = "E"  # 기타공시
    AUDIT = "F"  # 외부감사관련
    EXCHANGE = "I"  # 거래소공시


class Disclosure(BaseModel):
    """공시 한 건 (list.json)."""

    rcept_no: str
    corp_code: str
    corp_name: str
    stock_code: str = ""
    report_nm: str
    flr_nm: str = ""
    rcept_dt: str = ""
    corp_cls: str = ""

    @property
    def url(self) -> str:
        """DART 원문 링크. 알림에는 항상 원문을 같이 보낸다 — 판단은 사용자 몫이다."""
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.rcept_no}"

    @property
    def rcept_date_display(self) -> str:
        d = self.rcept_dt
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d


class AccountLine(BaseModel):
    """전체 재무제표 응답의 계정 한 줄 (fnlttSinglAcntAll)."""

    sj_div: str
    account_id: str
    account_nm: str
    thstrm_amount: float | None = None
    frmtrm_amount: float | None = None
    bfefrmtrm_amount: float | None = None
    currency: str = "KRW"
    ord: int = 0


class RawStatements(BaseModel):
    """한 기업·한 회계기간의 원본 계정 줄 모음."""

    corp_code: str
    bsns_year: int
    reprt_code: ReportCode
    fs_div: FsDiv
    lines: list[AccountLine] = Field(default_factory=list)


class InterestSource(str, Enum):
    """이자비용(`Financials.interest_expense`)을 어느 계정에서 가져왔나.

    손익계산서에 이자비용을 따로 적는 상장사는 4% 남짓이다(2024, 117/2,759). 나머지는
    금융원가 한 줄로 묶어 적는데, 거기엔 외환·파생 손실이 섞인다 — 삼성전자 2024 금융비용
    12.99조 중 실제 이자 지급은 0.68조였다. 출처를 남겨야 그 값을 이자로 판정할지 정할 수 있다.
    """

    #: 손익계산서의 이자비용 단독 계정.
    INCOME_STATEMENT = "income_statement"
    #: 현금흐름표의 이자의 지급(영업·투자·재무 합). 현금으로 나가지 않은 이자는 빠진다.
    CASH_FLOW = "cash_flow"
    #: 금융원가·금융비용. 이자 말고도 섞여 있어 판정하지 않는 대체값이다.
    FINANCE_COSTS = "finance_costs"
    #: SEC 순이자비용(InterestIncomeExpenseNet, 이자비용 − 이자수익). 이자수익만큼 작아
    #: 배율이 부풀려지는 대체값이다 — 판정하지 않는다.
    NET_INTEREST = "net_interest"

    @property
    def label(self) -> str:
        return {
            "income_statement": "손익계산서 이자비용",
            "cash_flow": "현금흐름표 이자의 지급",
            "finance_costs": "금융원가(대체값)",
            "net_interest": "순이자비용(대체값)",
        }[self.value]

    @property
    def is_proxy(self) -> bool:
        """이자비용 그 자체가 아니라 다른 것이 섞인 값인가 (값만 보여 주고 판정하지 않는다)."""
        return self in (InterestSource.FINANCE_COSTS, InterestSource.NET_INTEREST)


class ReceivableSource(str, Enum):
    """매출채권(`Financials.trade_receivables`)을 어느 줄에서 가져왔나.

    2024 상장사 2,542곳 중 절반(1,298곳)은 '매출채권및기타채권' 합계 줄만 공시한다. 미수금·
    미수수익이 섞여 매출채권보다 크다 — 둘 다 공시한 99곳에서 순수 매출채권은 합계의 중앙값
    95.7%(p25 85%, p10 47%)였다.
    """

    #: 매출채권 단독 줄 (dart_ShortTermTradeReceivable · ifrs-full_CurrentTradeReceivables · '매출채권').
    TRADE = "trade"
    #: 매출채권및기타채권 합계 줄. 미수금 등이 섞인다.
    TRADE_AND_OTHER = "trade_and_other"

    @property
    def label(self) -> str:
        return {"trade": "매출채권", "trade_and_other": "매출채권및기타채권 합계"}[self.value]


class CapexSource(str, Enum):
    """설비투자(`Financials.capex`)를 어느 줄에서 가져왔나.

    지표 공식은 '영업활동현금흐름 − 유형자산 취득' 이다. 2024 상장사 2,542곳 중 205곳은 유형자산
    취득을 종류별(토지·건물·기계장치·기타유형자산…)로만 적고, 몇 곳은 유형·무형 합계 한 줄로 적는다.
    """

    #: 유형자산의 취득 합계 줄.
    PPE = "ppe"
    #: 합계 줄이 없어 종류별 취득 줄을 더한 값.
    PPE_ITEMIZED = "ppe_itemized"
    #: 유형자산과 무형자산 취득을 묶은 한 줄. 무형자산 취득만큼 크다.
    PPE_AND_INTANGIBLE = "ppe_and_intangible"

    @property
    def label(self) -> str:
        return {
            "ppe": "유형자산의 취득",
            "ppe_itemized": "유형자산 종류별 취득의 합",
            "ppe_and_intangible": "유형·무형자산 취득 합계",
        }[self.value]


class Financials(BaseModel):
    """지표 계산에 필요한 계정만 정규화한 결과.

    값이 None인 필드는 "공시에서 찾지 못함"을 뜻한다. 0으로 채우지 않는다 —
    없는 데이터를 있는 것처럼 보여주면 신호등 자체가 거짓이 된다.
    """

    corp_code: str
    bsns_year: int
    reprt_code: ReportCode
    fs_div: FsDiv
    #: 금액 단위. DART는 KRW, SEC EDGAR는 USD.
    currency: str = "KRW"

    # 재무상태표
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    inventories: float | None = None
    trade_receivables: float | None = None
    #: trade_receivables 의 출처. 값이 없으면 None.
    trade_receivables_source: ReceivableSource | None = None
    capital_stock: float | None = None  # 자본금 — 자본잠식률 계산에 필요
    #: 고객 돈 부채 — 예수부채·보험계약부채·투자계약부채의 합. 금융회사 재무상태표인지 가르는 근거
    #: (`metrics.sector`)라 지표가 아니다. 셋 다 못 찾으면 None.
    customer_fund_liabilities: float | None = Field(default=None, exclude=True, repr=False)

    # 손익계산서
    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    interest_expense: float | None = None
    #: interest_expense 의 출처. 값이 없으면 None.
    interest_expense_source: InterestSource | None = None

    # 현금흐름표
    operating_cash_flow: float | None = None
    capex: float | None = None
    #: capex 의 출처. 값이 없으면 None.
    capex_source: CapexSource | None = None

    #: 같은 보고서의 전기 열(frmtrm)로 낸 작년. 성장성·작년 대비는 이것과 견준다 — 작년 보고서는
    #: 그 뒤의 재작성(중단영업 재분류·합병·회계정책 변경)도, 올해의 연결/개별 기준도 모른다.
    #: 전기 열이 없는 보고서(DART 일부·SEC)는 None.
    comparative: Financials | None = Field(default=None, exclude=True, repr=False)
    #: 보고서 금액 단위 오류(×1000) 의심 메모(`metrics.units`). 비면 의심 없음. 그해 지표는 ⚫,
    #: 대조군·작년 대비·위험 신호·추이 값에서 뺀다. 다음 해 보고서와 견줘야 알 수 있어 정규화가 아니라
    #: 이력을 읽는 쪽(service)이 단다.
    unit_suspect: str = Field(default="", exclude=True, repr=False)

    @property
    def free_cash_flow(self) -> float | None:
        if self.operating_cash_flow is None or self.capex is None:
            return None
        return self.operating_cash_flow - self.capex

    @property
    def capital_impairment_rate(self) -> float | None:
        """자본잠식률(%) = (자본금 − 자기자본) ÷ 자본금 × 100.

        음수면 잠식이 아니다. 자본금을 못 찾으면 None — 추정하지 않는다.
        """
        if self.capital_stock is None or self.capital_stock <= 0 or self.total_equity is None:
            return None
        return (self.capital_stock - self.total_equity) / self.capital_stock * 100
