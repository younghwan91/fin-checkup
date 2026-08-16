from __future__ import annotations

from fin_checkup.dart.normalize import normalize_statements
from fin_checkup.models import AccountLine, FsDiv, RawStatements, ReportCode


def line(sj_div: str, account_id: str, account_nm: str, amount: float | None) -> AccountLine:
    return AccountLine(
        sj_div=sj_div, account_id=account_id, account_nm=account_nm, thstrm_amount=amount
    )


def raw(*lines: AccountLine) -> RawStatements:
    return RawStatements(
        corp_code="00126380",
        bsns_year=2024,
        reprt_code=ReportCode.ANNUAL,
        fs_div=FsDiv.CFS,
        lines=list(lines),
    )


def test_maps_standard_ifrs_account_ids():
    fin = normalize_statements(
        raw(
            line("BS", "ifrs-full_Assets", "자산총계", 1000.0),
            line("BS", "ifrs-full_Liabilities", "부채총계", 400.0),
            line("BS", "ifrs-full_Equity", "자본총계", 600.0),
            line("BS", "ifrs-full_CurrentAssets", "유동자산", 500.0),
            line("BS", "ifrs-full_CurrentLiabilities", "유동부채", 250.0),
            line("IS", "ifrs-full_Revenue", "수익(매출액)", 2000.0),
            line("IS", "dart_OperatingIncomeLoss", "영업이익", 300.0),
            line("IS", "ifrs-full_ProfitLoss", "당기순이익", 200.0),
            line("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", 350.0),
        )
    )
    assert fin.total_assets == 1000.0
    assert fin.total_liabilities == 400.0
    assert fin.total_equity == 600.0
    assert fin.current_assets == 500.0
    assert fin.current_liabilities == 250.0
    assert fin.revenue == 2000.0
    assert fin.operating_income == 300.0
    assert fin.net_income == 200.0
    assert fin.operating_cash_flow == 350.0


def test_falls_back_to_korean_account_name_when_id_is_nonstandard():
    # 많은 기업이 표준 태그 대신 회사 고유 태그(-표기 포함)를 쓴다.
    fin = normalize_statements(
        raw(
            line("BS", "-표준계정코드 미사용-", "자산총계", 900.0),
            line("IS", "entity00126380_Revenue", "매출액", 1800.0),
            line("CF", "-표준계정코드 미사용-", "영업활동으로 인한 현금흐름", 120.0),
        )
    )
    assert fin.total_assets == 900.0
    assert fin.revenue == 1800.0
    assert fin.operating_cash_flow == 120.0


def test_account_name_matching_ignores_spaces_and_loss_suffix():
    fin = normalize_statements(
        raw(
            line("IS", "x", "영업이익(손실)", -50.0),
            line("IS", "y", "당기순이익(손실)", -80.0),
            line("BS", "z", "매출채권 및 기타유동채권", 300.0),
        )
    )
    assert fin.operating_income == -50.0
    assert fin.net_income == -80.0
    assert fin.trade_receivables == 300.0


def test_missing_accounts_stay_none_not_zero():
    fin = normalize_statements(raw(line("BS", "ifrs-full_Assets", "자산총계", 100.0)))
    assert fin.total_assets == 100.0
    assert fin.interest_expense is None
    assert fin.operating_cash_flow is None
    assert fin.free_cash_flow is None


def test_first_match_wins_by_priority_not_by_document_order():
    # "영업수익"보다 "매출액"이 우선순위가 높다. 문서에 뒤에 나와도 매출액을 쓴다.
    fin = normalize_statements(
        raw(
            line("IS", "a", "영업수익", 111.0),
            line("IS", "b", "매출액", 999.0),
        )
    )
    assert fin.revenue == 999.0


def test_capex_and_fcf():
    fin = normalize_statements(
        raw(
            line("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", 500.0),
            line("CF", "c", "유형자산의 취득", 200.0),
        )
    )
    assert fin.capex == 200.0
    assert fin.free_cash_flow == 300.0


def test_capex_sign_is_normalized_to_positive_outflow():
    # 현금흐름표에서 취득은 음수(유출)로 표기되는 경우가 흔하다.
    fin = normalize_statements(
        raw(
            line("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", 500.0),
            line("CF", "c", "유형자산의 취득", -200.0),
        )
    )
    assert fin.capex == 200.0
    assert fin.free_cash_flow == 300.0


def test_interest_expense_prefers_explicit_over_finance_cost():
    fin = normalize_statements(
        raw(
            line("IS", "a", "금융원가", 90.0),
            line("IS", "b", "이자비용", 40.0),
        )
    )
    assert fin.interest_expense == 40.0


def test_bs_account_is_not_picked_from_income_statement_section():
    # 섹션(sj_div)이 다르면 매칭하지 않는다.
    fin = normalize_statements(raw(line("IS", "ifrs-full_Assets", "자산총계", 1000.0)))
    assert fin.total_assets is None


def test_cis_section_is_accepted_for_income_accounts():
    # 일부 기업은 손익을 CIS(포괄손익계산서)에만 담는다.
    fin = normalize_statements(raw(line("CIS", "ifrs-full_ProfitLoss", "당기순이익", 77.0)))
    assert fin.net_income == 77.0
