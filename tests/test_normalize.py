from __future__ import annotations

from fin_checkup.dart.normalize import normalize_statements
from fin_checkup.models import (
    AccountLine,
    CapexSource,
    FsDiv,
    InterestSource,
    RawStatements,
    ReceivableSource,
    ReportCode,
)


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
    assert fin.trade_receivables_source is ReceivableSource.TRADE_AND_OTHER


# ── 매출채권: 순수 매출채권 줄을 합계 줄보다 먼저 ─────────────────────
# 2024 상장사 1,311곳이 ifrs-full_TradeAndOtherCurrentReceivables(합계 태그)로 먼저 잡혔다.
# 그 태그를 '기타수취채권'·'기타유동채권' 줄에 붙이는 회사도 150곳 남짓이라 매출채권이 아닌
# 미수금이 분모가 됐다.


def test_pure_trade_receivables_line_beats_the_aggregate_tag():
    fin = normalize_statements(raw(
        line("BS", "ifrs-full_TradeAndOtherCurrentReceivables", "매출채권및기타유동채권", 500.0),
        line("BS", "dart_ShortTermTradeReceivable", "매출채권", 420.0),
    ))
    assert fin.trade_receivables == 420.0
    assert fin.trade_receivables_source is ReceivableSource.TRADE


def test_ifrs_current_trade_receivables_id_is_pure():
    fin = normalize_statements(raw(
        line("BS", "ifrs-full_TradeAndOtherCurrentReceivables", "매출채권 및 기타채권", 500.0),
        line("BS", "ifrs-full_CurrentTradeReceivables", "유동매출채권", 410.0),
    ))
    assert fin.trade_receivables == 410.0
    assert fin.trade_receivables_source is ReceivableSource.TRADE


def test_other_receivables_line_with_the_aggregate_tag_is_not_trade_receivables():
    # 매출채권은 따로 적고 미수금 묶음에 합계 태그를 붙인 회사. 옛 규칙은 기타채권 30 을 골랐다.
    fin = normalize_statements(raw(
        line("BS", "-표준계정코드 미사용-", "매출채권", 400.0),
        line("BS", "ifrs-full_TradeAndOtherCurrentReceivables", "기타수취채권", 30.0),
    ))
    assert fin.trade_receivables == 400.0
    assert fin.trade_receivables_source is ReceivableSource.TRADE


def test_other_receivables_alone_are_not_trade_receivables():
    fin = normalize_statements(raw(
        line("BS", "ifrs-full_TradeAndOtherCurrentReceivables", "기타유동채권", 30.0),
    ))
    assert fin.trade_receivables is None
    assert fin.trade_receivables_source is None


def test_noncurrent_trade_receivables_are_not_picked():
    fin = normalize_statements(raw(
        line("BS", "ifrs-full_NoncurrentTradeReceivables", "매출채권", 70.0),
        line("BS", "ifrs-full_TradeAndOtherCurrentReceivables", "매출채권및기타채권", 500.0),
    ))
    assert fin.trade_receivables == 500.0
    assert fin.trade_receivables_source is ReceivableSource.TRADE_AND_OTHER


def test_aggregate_only_is_kept_with_its_source():
    fin = normalize_statements(raw(
        line("BS", "ifrs-full_TradeAndOtherCurrentReceivables", "매출채권및기타채권", 500.0),
    ))
    assert fin.trade_receivables == 500.0
    assert fin.trade_receivables_source is ReceivableSource.TRADE_AND_OTHER


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


# ── 설비투자: 유형자산 취득 합계 → 종류별 합 → 유·무형 합계 ─────────────
# 2024 상장사 205곳은 유형자산 취득을 종류별(토지·건물·기계장치·기타유형자산…)로만 적는다.
# 옛 규칙은 그중 '기타유형자산의취득' 한 줄을 설비투자로 썼다(56곳).

PPE_ID = "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"


def test_ppe_total_line_is_capex():
    fin = normalize_statements(raw(line("CF", PPE_ID, "유형자산의 취득", -200.0)))
    assert fin.capex == 200.0
    assert fin.capex_source is CapexSource.PPE


def test_itemized_ppe_purchases_are_summed_not_one_class():
    fin = normalize_statements(raw(
        line("CF", "dart_PurchaseOfLand", "토지의 취득", -50.0),
        line("CF", "dart_PurchaseOfMachinery", "기계장치의 취득", -120.0),
        line("CF", "dart_PurchaseOfOtherPropertyPlantAndEquipment", "기타유형자산의 취득", -30.0),
        line("CF", "-표준계정코드 미사용-", "공구와기구의 취득", -5.0),
        line("CF", "dart_PurchaseOfShortTermFinancialInstruments", "단기금융상품의 취득", -999.0),
    ))
    assert fin.capex == 205.0, "옛 규칙은 기타유형자산 30 만 썼다"
    assert fin.capex_source is CapexSource.PPE_ITEMIZED


def test_component_tagged_with_the_total_id_is_summed_with_the_other_classes():
    # 회사가 '시설장치의 취득' 한 줄에 유형자산 합계 태그를 붙이고 나머지는 종류별 태그로 적었다.
    fin = normalize_statements(raw(
        line("CF", PPE_ID, "시설장치의 취득", -10.0),
        line("CF", "dart_PurchaseOfVehicles", "차량운반구의 취득", -40.0),
    ))
    assert fin.capex == 50.0
    assert fin.capex_source is CapexSource.PPE_ITEMIZED


def test_right_of_use_assets_are_not_capex():
    # 사용권자산 '취득' 은 리스 계약이지 현금 설비투자가 아니다.
    fin = normalize_statements(raw(
        line("CF", "dart_PurchaseOfMachinery", "기계장치의 취득", -100.0),
        line("CF", "dart_PurchaseOfOtherPropertyPlantAndEquipment", "사용권자산의 취득", -70.0),
    ))
    assert fin.capex == 100.0

    total = normalize_statements(raw(line("CF", PPE_ID, "유형자산 및 사용권자산의 취득", -240.0)))
    assert total.capex == 240.0, "합계 줄에 사용권자산이 붙어 있어도 설비투자 줄이다"
    assert total.capex_source is CapexSource.PPE


def test_combined_ppe_and_intangible_line_is_kept_with_its_source():
    fin = normalize_statements(raw(line("CF", PPE_ID, "유ㆍ무형자산의 취득", -300.0)))
    assert fin.capex == 300.0
    assert fin.capex_source is CapexSource.PPE_AND_INTANGIBLE

    by_name = normalize_statements(
        raw(line("CF", "-표준계정코드 미사용-", "유형자산 및 무형자산의 취득", -300.0))
    )
    assert by_name.capex_source is CapexSource.PPE_AND_INTANGIBLE

    advance = normalize_statements(raw(line("CF", "x", "선급금의 증가(유형자산및무형자산취득)", -9.0)))
    assert advance.capex is None, "운전자본 줄은 설비투자가 아니다"


def test_ppe_total_beats_the_combined_line():
    fin = normalize_statements(raw(
        line("CF", "-표준계정코드 미사용-", "유무형자산의 취득", -300.0),
        line("CF", PPE_ID, "유형자산의 취득", -200.0),
    ))
    assert fin.capex == 200.0
    assert fin.capex_source is CapexSource.PPE


def test_no_ppe_line_leaves_capex_empty():
    fin = normalize_statements(raw(
        line("CF", "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
             "무형자산의 취득", -9.0),
    ))
    assert fin.capex is None
    assert fin.capex_source is None


def test_interest_expense_prefers_explicit_over_finance_cost():
    fin = normalize_statements(
        raw(
            line("IS", "a", "금융원가", 90.0),
            line("IS", "b", "이자비용", 40.0),
        )
    )
    assert fin.interest_expense == 40.0
    assert fin.interest_expense_source is InterestSource.INCOME_STATEMENT


# ── 이자비용의 출처: 손익계산서 이자비용 → 현금흐름표 이자 지급 → 금융원가 ──────
# 2024 상장사 2,759곳 중 손익계산서에 이자비용 단독 계정이 있는 곳은 117곳뿐이다.
# 나머지는 금융원가(외환손실·파생손실 포함)로 떨어졌다 — 삼성전자 금융비용 12.99조
# vs 실제 이자 지급 0.68조. 이자보상배율이 2.52배(🟡)로 찍혔다.


def test_interest_from_standard_id_records_income_statement_source():
    fin = normalize_statements(
        raw(
            line("IS", "ifrs-full_FinanceCosts", "금융원가", 90.0),
            line("IS", "ifrs-full_InterestExpense", "이자비용", 40.0),
            line("CF", "ifrs-full_InterestPaidClassifiedAsOperatingActivities", "이자의 지급", 35.0),
        )
    )
    assert fin.interest_expense == 40.0
    assert fin.interest_expense_source is InterestSource.INCOME_STATEMENT


def test_cash_flow_interest_paid_beats_finance_costs():
    fin = normalize_statements(
        raw(
            line("IS", "ifrs-full_FinanceCosts", "금융비용", 12_985_684.0),
            line("CF", "ifrs-full_InterestPaidClassifiedAsOperatingActivities", "이자의 지급", -675_049.0),
        )
    )
    assert fin.interest_expense == 675_049.0, "유출 부호는 절댓값으로"
    assert fin.interest_expense_source is InterestSource.CASH_FLOW


def test_interest_paid_split_across_activities_is_summed():
    # 영업·재무·투자(자본화 이자)로 나눠 적는 회사가 있다. 한 줄만 쓰면 지급액이 모자란다.
    fin = normalize_statements(
        raw(
            line("IS", "ifrs-full_FinanceCosts", "금융원가", 500.0),
            line("CF", "ifrs-full_InterestPaidClassifiedAsOperatingActivities", "이자지급(영업)", 160.0),
            line("CF", "ifrs-full_InterestPaidClassifiedAsInvestingActivities", "이자의 지급", 80.0),
            line("CF", "ifrs-full_InterestPaidClassifiedAsFinancingActivities", "이자지급", -20.0),
        )
    )
    assert fin.interest_expense == 260.0
    assert fin.interest_expense_source is InterestSource.CASH_FLOW


def test_hybrid_capital_distribution_is_not_interest_paid():
    # 신종자본증권은 자본이다. 그 '이자'는 배당과 같은 분배라 이자 지급에 넣지 않는다.
    fin = normalize_statements(
        raw(
            line("IS", "ifrs-full_FinanceCosts", "금융원가", 500.0),
            line("CF", "ifrs-full_InterestPaidClassifiedAsOperatingActivities", "이자의 지급", 50.0),
            line("CF", "ifrs-full_InterestPaidClassifiedAsFinancingActivities",
                 "신종자본증권의 이자지급", 7.0),
        )
    )
    assert fin.interest_expense == 50.0


def test_interest_paid_by_korean_name_with_numbering():
    fin = normalize_statements(
        raw(
            line("IS", "-표준계정코드 미사용-", "금융원가", 500.0),
            line("CF", "-표준계정코드 미사용-", "3. 이자의 지급", 30.0),
        )
    )
    assert fin.interest_expense == 30.0
    assert fin.interest_expense_source is InterestSource.CASH_FLOW


def test_indirect_method_interest_adjustment_is_not_interest_paid():
    # 간접법의 '이자비용 조정'은 현금 지급이 아니다. 이름이 '이자비용'이어도 집지 않는다.
    fin = normalize_statements(
        raw(
            line("IS", "ifrs-full_FinanceCosts", "금융원가", 500.0),
            line("CF", "dart_AdjustmentsForInterestExpenses", "이자비용", 45.0),
        )
    )
    assert fin.interest_expense == 500.0
    assert fin.interest_expense_source is InterestSource.FINANCE_COSTS


def test_zero_interest_paid_falls_through_to_finance_costs():
    # 현금 지급 0 은 무차입의 증거가 아니다 — 전환사채 상각이자는 현금이 나가지 않는다.
    fin = normalize_statements(
        raw(
            line("IS", "ifrs-full_FinanceCosts", "금융원가", 90.0),
            line("CF", "ifrs-full_InterestPaidClassifiedAsOperatingActivities", "이자의 지급", 0.0),
        )
    )
    assert fin.interest_expense == 90.0
    assert fin.interest_expense_source is InterestSource.FINANCE_COSTS


def test_finance_costs_is_the_last_resort():
    fin = normalize_statements(raw(line("IS", "x", "금융비용", -90.0)))
    assert fin.interest_expense == 90.0
    assert fin.interest_expense_source is InterestSource.FINANCE_COSTS


def test_no_interest_line_at_all_has_no_source():
    fin = normalize_statements(raw(line("IS", "dart_OperatingIncomeLoss", "영업이익", 10.0)))
    assert fin.interest_expense is None
    assert fin.interest_expense_source is None


def test_bs_account_is_not_picked_from_income_statement_section():
    # 섹션(sj_div)이 다르면 매칭하지 않는다.
    fin = normalize_statements(raw(line("IS", "ifrs-full_Assets", "자산총계", 1000.0)))
    assert fin.total_assets is None


def test_cis_section_is_accepted_for_income_accounts():
    # 일부 기업은 손익을 CIS(포괄손익계산서)에만 담는다.
    fin = normalize_statements(raw(line("CIS", "ifrs-full_ProfitLoss", "당기순이익", 77.0)))
    assert fin.net_income == 77.0


def test_currency_follows_the_statement_lines():
    """DART 에 USD·JPY·CNY 로 공시하는 기업이 있다(두산밥캣 2024 는 전 라인 USD).
    통화를 KRW 로 두면 자산 82억달러가 '82억원'으로 찍히고, 업종 중앙값에 달러 금액이
    원화와 섞여 들어간다."""
    usd = AccountLine(
        sj_div="BS", account_id="ifrs-full_Assets", account_nm="자산총계",
        thstrm_amount=8_169_804_000.0, currency="USD",
    )
    assert normalize_statements(raw(usd)).currency == "USD"
    assert normalize_statements(raw(line("BS", "ifrs-full_Assets", "자산총계", 1.0))).currency == "KRW"


def test_currency_is_the_majority_when_lines_disagree():
    lines = [
        AccountLine(sj_div="BS", account_id=f"x{i}", account_nm=f"n{i}", thstrm_amount=1.0, currency="USD")
        for i in range(3)
    ] + [AccountLine(sj_div="BS", account_id="k", account_nm="k", thstrm_amount=1.0, currency="KRW")]
    assert normalize_statements(raw(*lines)).currency == "USD"


# ── 매출액: 금융지주의 보험 부문 줄 · 0 으로 적힌 머리 줄 ─────────────────


def test_insurance_revenue_of_a_bank_groups_subsidiary_is_not_revenue():
    """은행·금융지주의 '보험수익' 은 보험 자회사 한 부문이다. 매출로 쓰면 영업이익률이 수천 %가 된다.

    중소기업은행 2024: 보험수익 0.09조 · 이자수익 18.95조 · 영업이익 3.59조 → 영업이익률 3,845% 🟢.
    """
    fin = normalize_statements(raw(
        line("CIS", "ifrs-full_InsuranceRevenue", "보험수익", 93_466.0),
        line("CIS", "ifrs-full_RevenueFromInterest", "이자수익", 18_954_860.0),
        line("CIS", "dart_OperatingIncomeLoss", "영업이익", 3_594_087.0),
    ))
    assert fin.revenue is None


def test_insurers_insurance_revenue_is_still_revenue():
    """보험사는 보험수익이 본업 매출이다 — 운용 이자수익보다 크다(삼성화재 2024: 17.28조 vs 2.25조)."""
    fin = normalize_statements(raw(
        line("IS", "ifrs-full_InsuranceRevenue", "보험수익", 17_276.0),
        line("IS", "-표준계정코드 미사용-", "이자수익", 2_251.0),
    ))
    assert fin.revenue == 17_276.0


def test_zero_header_line_does_not_hide_the_real_revenue_line():
    """한화생명 2023: '영업수익' 머리 줄이 0 으로 와서 보험수익 9.79조를 가렸다 — 매출 0 → 증가율 ⚫."""
    fin = normalize_statements(raw(
        line("IS", "-표준계정코드 미사용-", "영업수익", 0.0),
        line("IS", "ifrs-full_InsuranceRevenue", "보험수익", 9_790.0),
    ))
    assert fin.revenue == 9_790.0


def test_revenue_reported_as_zero_stays_zero():
    """매출이 정말 0 인 회사(기업인수목적회사 등)는 0 이다 — 다른 후보가 없으면 없다고 하지 않는다."""
    fin = normalize_statements(raw(line("IS", "ifrs-full_Revenue", "매출액", 0.0)))
    assert fin.revenue == 0.0


# ── 이름 대체 매칭: 로마 숫자 머리 · 각주 꼬리 · 지배/비지배 합 · '창출된 현금' ───────


def test_roman_numeral_section_prefix_and_footnote_suffix_are_ignored_by_name():
    """표준 태그 없는 줄은 'Ⅰ.매출액' · 'VIII. 당기순이익' · '재고자산 (주7)' 처럼 온다."""
    fin = normalize_statements(raw(
        line("IS", "-표준계정코드 미사용-", "Ⅰ.매출액", 238.0),
        line("IS", "-표준계정코드 미사용-", "IV.영업이익(손실)", -51.0),
        line("IS", "-표준계정코드 미사용-", "VIII. 당기순이익", 89.0),
        line("BS", "-표준계정코드 미사용-", "재고자산 (주7)", 42.0),
        line("CF", "ifrs-full_CashFlowsFromUsedInOperations", "I. 영업활동으로인한현금흐름", 4015.0),
    ))
    assert (fin.revenue, fin.operating_income, fin.net_income, fin.inventories) == (238.0, -51.0, 89.0, 42.0)
    assert fin.operating_cash_flow == 4015.0, "코오롱인더스트리 2023·2024 영업활동현금흐름 ⚫"


def test_arabic_numbered_sub_item_is_not_read_as_the_total():
    """'1. 매출액' 은 'Ⅰ. 영업수익' 아래의 내역일 수 있다 — 아라비아 숫자 머리는 떼지 않는다."""
    fin = normalize_statements(raw(
        line("IS", "-표준계정코드 미사용-", "Ⅰ. 영업수익", 500.0),
        line("IS", "-표준계정코드 미사용-", "1. 매출액", 300.0),
    ))
    assert fin.revenue == 500.0


def test_consolidated_net_income_names():
    fin = normalize_statements(raw(line("IS", "-표준계정코드 미사용-", "당기연결순이익(손실)", -48.0)))
    assert fin.net_income == -48.0
    fin = normalize_statements(raw(line("IS", "-표준계정코드 미사용-", "연결당기순이익", 72.0)))
    assert fin.net_income == 72.0


def test_net_income_is_the_sum_of_owners_and_noncontrolling_when_no_total_line():
    """일진전기 2024 는 당기순이익 합계 줄 없이 지배·비지배 귀속 두 줄만 적었다 → 순이익 ⚫."""
    fin = normalize_statements(raw(
        line("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업 소유주지분", 461.0),
        line("CIS", "ifrs-full_ProfitLossAttributableToNoncontrollingInterests", "비지배지분", -3.0),
    ))
    assert fin.net_income == 458.0


def test_owners_share_alone_is_not_net_income():
    """비지배 줄이 없으면 합계를 모른다 — 지배기업 몫만으로 채우지 않는다."""
    fin = normalize_statements(raw(
        line("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업 소유주지분", 461.0),
    ))
    assert fin.net_income is None


def test_cash_generated_from_operations_is_not_operating_cash_flow():
    """인팩 2024: OCF 태그가 '영업활동에서 창출된 현금흐름'(이자·법인세 전, 183억)에 붙고, 실제
    영업활동현금흐름 49억은 태그 없는 줄이었다."""
    fin = normalize_statements(raw(
        line("CF", "-표준계정코드 미사용-", "영업활동으로 인한 현금흐름", 49.0),
        line("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동에서 창출된 현금흐름", 183.0),
    ))
    assert fin.operating_cash_flow == 49.0


def test_cash_generated_line_is_kept_when_it_is_the_only_candidate():
    fin = normalize_statements(raw(
        line("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업에서 창출된 현금흐름", 145.0),
    ))
    assert fin.operating_cash_flow == 145.0
# ── 전기 열(frmtrm) — 같은 보고서가 적은 작년 ─────────────────────────


def test_comparative_period_comes_from_the_prior_year_column():
    """작년 숫자는 작년 보고서보다 올해 보고서의 전기 열이 맞다 — 재작성·연결 범위가 올해와 같다.

    (주)효성 2024: 분할로 중단영업을 재분류해 전기 매출이 3.44조 → 1.85조로 다시 적혔다.
    """
    fin = normalize_statements(raw(
        AccountLine(sj_div="IS", account_id="ifrs-full_Revenue", account_nm="매출액",
                    thstrm_amount=2273.0, frmtrm_amount=1847.0),
        AccountLine(sj_div="BS", account_id="ifrs-full_Assets", account_nm="자산총계",
                    thstrm_amount=9000.0, frmtrm_amount=8000.0),
    ))
    assert fin.revenue == 2273.0
    prior = fin.comparative
    assert prior is not None
    assert (prior.bsns_year, prior.fs_div, prior.revenue, prior.total_assets) == (
        2023, FsDiv.CFS, 1847.0, 8000.0,
    )
    assert prior.comparative is None


def test_report_without_a_prior_year_column_has_no_comparative():
    fin = normalize_statements(raw(line("IS", "ifrs-full_Revenue", "매출액", 100.0)))
    assert fin.comparative is None


# ── 고객 돈 부채 (업종 판별 근거, Q17) ─────────────────────────────


def test_customer_fund_liabilities_sum_deposits_and_insurance_contracts():
    from fin_checkup.dart.normalize import normalize_statements
    from fin_checkup.models import AccountLine, FsDiv, RawStatements, ReportCode

    def line(account_id, name, amount):
        return AccountLine(sj_div="BS", account_id=account_id, account_nm=name, thstrm_amount=amount)

    raw = RawStatements(
        corp_code="1", bsns_year=2025, reprt_code=ReportCode.ANNUAL, fs_div=FsDiv.CFS,
        lines=[
            line("ifrs-full_DepositsFromCustomers", "예수부채", 500.0),
            line("ifrs-full_InsuranceContractsIssuedThatAreLiabilities", "보험계약부채", 300.0),
            line("-표준계정코드 미사용-", "투자계약부채", 20.0),
            line("ifrs-full_Liabilities", "부채총계", 1000.0),
        ],
    )
    assert normalize_statements(raw).customer_fund_liabilities == 820.0
    plain = raw.model_copy(update={"lines": raw.lines[-1:]})
    assert normalize_statements(plain).customer_fund_liabilities is None
    assert "customer_fund_liabilities" not in normalize_statements(raw).model_dump()
