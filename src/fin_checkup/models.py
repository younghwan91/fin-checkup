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
    capital_stock: float | None = None  # 자본금 — 자본잠식률 계산에 필요

    # 손익계산서
    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    interest_expense: float | None = None

    # 현금흐름표
    operating_cash_flow: float | None = None
    capex: float | None = None

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
