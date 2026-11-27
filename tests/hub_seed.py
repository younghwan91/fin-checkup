"""정적 사이트 테스트용 캐시 — 실데이터 없이 회사 몇 곳을 심는다.

- 삼성전자(005930): 일반 업종, 3개년. CapEx·매출채권이 없어 FCF·매출채권회전율은 ⚫.
- KB금융(105560): 금융지주 → 부채비율 등 ⊘ (대신 볼 것: BIS 자기자본비율).
- 에이치엘비(028300): 1개년뿐 → 작년 비교 없음.
- 재무없음(123456): 상장사지만 재무가 없다 → 페이지가 생기지 않는다.
- 비상장: 종목코드 없음 → 모집단 밖.
- 신한은행(000010): 옛 종목코드는 남았지만 시장 구분이 OTHER(상장 아님) → 모집단 밖.
"""

from __future__ import annotations

from fin_checkup.models import AccountLine, Company, CorpCode, FsDiv, Market, RawStatements, ReportCode
from fin_checkup.storage import Cache

SAMSUNG = CorpCode(corp_code="00126380", corp_name="삼성전자", stock_code="005930")
KB = CorpCode(corp_code="00688996", corp_name="KB금융", stock_code="105560")
HLB = CorpCode(corp_code="00400000", corp_name="에이치엘비", stock_code="028300")
NO_DATA = CorpCode(corp_code="00999999", corp_name="재무없음", stock_code="123456")
UNLISTED = CorpCode(corp_code="00111111", corp_name="비상장", stock_code="")
DELISTED = CorpCode(corp_code="00149646", corp_name="신한은행", stock_code="000010")


def statements(corp_code: str, year: int, revenue: float, operating_income: float) -> RawStatements:
    def ln(sj, aid, nm, amount, order):
        return AccountLine(sj_div=sj, account_id=aid, account_nm=nm, thstrm_amount=amount, ord=order)

    return RawStatements(
        corp_code=corp_code,
        bsns_year=year,
        reprt_code=ReportCode.ANNUAL,
        fs_div=FsDiv.CFS,
        lines=[
            ln("BS", "ifrs-full_Assets", "자산총계", 10000.0, 1),
            ln("BS", "ifrs-full_Liabilities", "부채총계", 4000.0, 2),
            ln("BS", "ifrs-full_Equity", "자본총계", 6000.0, 3),
            ln("BS", "ifrs-full_IssuedCapital", "자본금", 1000.0, 4),
            ln("BS", "ifrs-full_CurrentAssets", "유동자산", 5000.0, 5),
            ln("BS", "ifrs-full_CurrentLiabilities", "유동부채", 2000.0, 6),
            ln("BS", "ifrs-full_Inventories", "재고자산", 800.0, 7),
            ln("IS", "ifrs-full_Revenue", "매출액", revenue, 8),
            ln("IS", "dart_OperatingIncomeLoss", "영업이익", operating_income, 9),
            ln("IS", "ifrs-full_ProfitLoss", "당기순이익", operating_income * 0.8, 10),
            ln("IS", "ifrs-full_InterestExpense", "이자비용", 50.0, 11),
            ln("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", 900.0, 12),
        ],
    )


def seed_site_cache(cache: Cache) -> None:
    cache.save_corp_codes([SAMSUNG, KB, HLB, NO_DATA, UNLISTED, DELISTED])
    cache.save_company(
        Company(
            corp_code=SAMSUNG.corp_code,
            corp_name="삼성전자",
            stock_code="005930",
            market=Market.KOSPI,
            industry_code="264",
        )
    )
    cache.save_company(
        Company(
            corp_code=KB.corp_code,
            corp_name="KB금융",
            stock_code="105560",
            market=Market.KOSPI,
            industry_code="64992",
        )
    )
    cache.save_company(
        Company(
            corp_code=HLB.corp_code,
            corp_name="에이치엘비",
            stock_code="028300",
            market=Market.KOSDAQ,
        )
    )
    cache.save_company(
        Company(corp_code=NO_DATA.corp_code, corp_name="재무없음", stock_code="123456", market=Market.KOSDAQ)
    )
    cache.save_company(
        Company(
            corp_code=DELISTED.corp_code,
            corp_name="신한은행",
            stock_code="000010",
            market=Market.OTHER,
            industry_code="64121",
        )
    )
    for year, revenue, op in [(2022, 1100.0, 180.0), (2023, 1200.0, 150.0), (2024, 900.0, 40.0)]:
        cache.save_statements(statements(SAMSUNG.corp_code, year, revenue, op))
    for year in (2023, 2024):
        cache.save_statements(statements(KB.corp_code, year, 3000.0, 600.0))
    cache.save_statements(statements(HLB.corp_code, 2024, 100.0, -50.0))
    cache.save_statements(statements(UNLISTED.corp_code, 2024, 100.0, 10.0))
    cache.save_statements(statements(DELISTED.corp_code, 2024, 500.0, 60.0))


def html_pages(out):
    """빌드 산출물의 HTML *페이지* — 검색 엔진 소유 확인 파일(`site/verification.py`)은 HTML 확장자지만
    페이지가 아니라 뺀다(머리·바닥글·면책 문구·제목이 없는 한 줄짜리)."""
    from fin_checkup.site.verification import FILES

    return sorted(p for p in out.rglob("*.html") if p.relative_to(out).as_posix() not in FILES)
