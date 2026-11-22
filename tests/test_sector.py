"""업종별 판정 — 실데이터에서 KB금융이 🔴 4개로 나온 것을 계기로 추가.

건전한 은행을 위험하다고 표시하는 건 단순한 계산 오류가 아니라 사용자를 잘못된
판단으로 이끄는 문제다. 여기 테스트는 그 재발을 막는 게 목적이다.
"""

from __future__ import annotations

import pytest

from fin_checkup.metrics.engine import checkup
from fin_checkup.metrics.redflags import detect_red_flags
from fin_checkup.metrics.sector import Sector, sector_for
from fin_checkup.metrics.signals import Signal
from fin_checkup.models import Financials, FsDiv, ReportCode


def fin(**kwargs) -> Financials:
    return Financials(
        corp_code="105560", bsns_year=2024, reprt_code=ReportCode.ANNUAL,
        fs_div=FsDiv.CFS, **kwargs
    )


#: KB금융 2024년 실제 값에 가깝게 구성한 표본.
BANK = dict(
    total_assets=766_000_000_000_000,
    total_liabilities=705_000_000_000_000,
    total_equity=60_400_000_000_000,
    net_income=5_078_000_000_000,
    operating_income=6_700_000_000_000,
    interest_expense=14_600_000_000_000,
)


# ── 업종 판별 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("corp_name", "expected"),
    [
        ("KB금융", Sector.BANKING),
        ("신한지주", Sector.BANKING),
        ("하나금융지주", Sector.BANKING),
        ("우리금융지주", Sector.BANKING),
        ("한국금융지주", Sector.BANKING),
        ("(주)LG", Sector.GENERAL),
        ("GS", Sector.GENERAL),
        ("한진칼", Sector.GENERAL),
        ("에코프로", Sector.GENERAL),
        ("삼양홀딩스", Sector.GENERAL),
        (None, Sector.GENERAL),
    ],
)
def test_holding_company_code_is_split_by_name(corp_name, expected):
    """KSIC 64992 '지주회사'는 KB금융도 (주)LG 도 같은 코드다. 전부 금융업으로 치면
    비금융 지주사의 부채비율·이자보상배율이 ⊘가 돼 진짜 위험이 가려진다."""
    assert sector_for("64992", corp_name) is expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("64110", Sector.BANKING),     # 중앙은행
        ("64121", Sector.BANKING),     # 국내은행 (중소기업은행·카카오뱅크)
        ("64132", Sector.BANKING),     # 저축은행
        ("66121", Sector.BANKING),     # 증권 중개업
        ("64", Sector.FINANCIAL),
        ("64913", Sector.FINANCIAL),   # 신용카드
        ("64209", Sector.FINANCIAL),   # 벤처투자
        ("65110", Sector.FINANCIAL),   # 보험
        ("66199", Sector.FINANCIAL),   # 금융 지원 서비스
        ("68100", Sector.REAL_ESTATE),
        ("264", Sector.GENERAL),       # 삼성전자 (실제 코드)
        ("26410", Sector.GENERAL),
        ("", Sector.GENERAL),
        (None, Sector.GENERAL),
        ("6", Sector.GENERAL),         # 판별 불가능한 길이
    ],
)
def test_sector_detection(code, expected):
    assert sector_for(code) is expected


# ── 핵심: 은행이 위험하게 표시되지 않는다 ─────────────────────────


def test_healthy_bank_is_not_marked_dangerous():
    result = checkup(fin(**BANK), sector=Sector.FINANCIAL)
    reds = [m.key for m in result.metrics if m.signal is Signal.RED]
    assert reds == [], f"건전한 은행에 🔴가 붙었다: {reds}"


def test_same_bank_would_be_misjudged_as_a_general_company():
    # 이 테스트는 수정 전 동작을 기록해둔다 — 왜 업종 구분이 필요한지의 근거다.
    general = checkup(fin(**BANK), sector=Sector.GENERAL)
    reds = {m.key for m in general.metrics if m.signal is Signal.RED}
    assert {"debt_ratio", "equity_ratio", "interest_coverage"} <= reds


@pytest.mark.parametrize(
    "key",
    [
        "debt_ratio", "equity_ratio", "current_ratio", "interest_coverage",
        "inventory_turnover", "receivable_turnover", "asset_turnover",
    ],
)
def test_inapplicable_metrics_are_marked_not_applicable_for_banks(key):
    metric = checkup(fin(**BANK), sector=Sector.FINANCIAL).by_key(key)
    assert metric is not None
    assert metric.signal is Signal.NOT_APPLICABLE
    assert "금융업에는 적용하지 않는다" in metric.note


def test_not_applicable_explains_what_to_look_at_instead():
    metric = checkup(fin(**BANK), sector=Sector.FINANCIAL).by_key("debt_ratio")
    assert "BIS" in metric.note


def test_bank_roa_uses_a_banking_band():
    """은행 ROA는 자릿수가 달라 별도 밴드를 쓴다.

    일반 밴드는 🟢 5% 이상, 금융 밴드는 🟢 1% 이상이다. 그래서 우량 은행 수준인
    1.2%가 금융 기준으로는 🟢, 일반 기준으로는 🟡이 된다.
    """
    good_bank = dict(BANK) | {"net_income": 9_200_000_000_000}  # ROA 약 1.2%
    financial = checkup(fin(**good_bank), sector=Sector.FINANCIAL).by_key("roa")
    general = checkup(fin(**good_bank), sector=Sector.GENERAL).by_key("roa")

    assert financial.value == pytest.approx(1.2, abs=0.05)
    assert financial.signal is Signal.GREEN
    assert general.signal is Signal.YELLOW


def test_weak_bank_roa_is_caught_by_the_banking_band():
    # ROA 0.3%는 은행으로선 부진하다. 일반 밴드는 흑자라는 이유로 🟡에 그친다.
    weak = dict(BANK) | {"net_income": 2_300_000_000_000}  # ROA 약 0.3%
    assert checkup(fin(**weak), sector=Sector.FINANCIAL).by_key("roa").signal is Signal.RED
    assert checkup(fin(**weak), sector=Sector.GENERAL).by_key("roa").signal is Signal.YELLOW


def test_roe_still_applies_to_banks():
    roe = checkup(fin(**BANK), sector=Sector.FINANCIAL).by_key("roe")
    assert roe.signal in (Signal.GREEN, Signal.YELLOW, Signal.RED)
    assert roe.value == pytest.approx(8.41, abs=0.1)


def test_growth_and_cash_quality_still_apply_to_banks():
    current = fin(**BANK, operating_cash_flow=3_000_000_000_000)
    prior = Financials(
        corp_code="105560", bsns_year=2023, reprt_code=ReportCode.ANNUAL,
        fs_div=FsDiv.CFS, net_income=4_600_000_000_000,
    )
    result = checkup(current, prior, sector=Sector.FINANCIAL)
    assert result.by_key("net_income_growth").signal is not Signal.NOT_APPLICABLE
    assert result.by_key("operating_cash_flow").signal is not Signal.NOT_APPLICABLE


# ── 위험 신호도 업종을 본다 ───────────────────────────────────────


def test_zombie_flag_is_not_raised_for_banks():
    history = [
        Financials(corp_code="105560", bsns_year=y, reprt_code=ReportCode.ANNUAL,
                   fs_div=FsDiv.CFS, operating_income=6_700_000_000_000,
                   interest_expense=14_600_000_000_000)
        for y in (2022, 2023, 2024)
    ]
    general = {f.key for f in detect_red_flags(history, sector=Sector.GENERAL)}
    financial = {f.key for f in detect_red_flags(history, sector=Sector.FINANCIAL)}
    assert "zombie_streak" in general
    assert "zombie_streak" not in financial, "은행은 이자비용이 영업의 원가다"


def test_real_operating_loss_is_still_flagged_for_banks():
    history = [
        Financials(corp_code="105560", bsns_year=y, reprt_code=ReportCode.ANNUAL,
                   fs_div=FsDiv.CFS, operating_income=-1_000_000_000)
        for y in (2022, 2023, 2024)
    ]
    keys = {f.key for f in detect_red_flags(history, sector=Sector.FINANCIAL)}
    assert "consecutive_operating_loss" in keys, "실제 적자는 업종과 무관하게 알려야 한다"


def test_capital_impairment_still_applies_to_banks():
    history = [fin(total_equity=-100, capital_stock=500)]
    keys = {f.key for f in detect_red_flags(history, sector=Sector.FINANCIAL)}
    assert "full_capital_impairment" in keys


# ── 일반 기업은 그대로 ────────────────────────────────────────────


def test_general_companies_are_unaffected():
    manufacturer = fin(
        total_liabilities=500, total_equity=1000, total_assets=1500,
        current_assets=2000, current_liabilities=1000,
        revenue=1000, operating_income=150, net_income=100,
    )
    result = checkup(manufacturer, sector=Sector.GENERAL)
    assert result.by_key("debt_ratio").signal is Signal.GREEN
    assert result.by_key("current_ratio").signal is Signal.GREEN
    assert not any(m.signal is Signal.NOT_APPLICABLE for m in result.metrics)


def test_real_estate_only_suppresses_inventory_turnover():
    result = checkup(fin(revenue=1000, inventories=200, total_assets=2000),
                     sector=Sector.REAL_ESTATE)
    assert result.by_key("inventory_turnover").signal is Signal.NOT_APPLICABLE
    assert result.by_key("asset_turnover").signal is not Signal.NOT_APPLICABLE


def test_default_sector_is_general():
    assert checkup(fin(revenue=1000, operating_income=100)).sector is Sector.GENERAL


def test_not_applicable_does_not_count_as_worst():
    result = checkup(fin(**BANK), sector=Sector.FINANCIAL)
    assert result.worst is not Signal.NOT_APPLICABLE
    assert result.counts[Signal.NOT_APPLICABLE] == 7


# ── 은행·증권·금융지주: 매출이 없는 업종 (Q12) ──────────────────────


@pytest.mark.parametrize(
    ("corp_name", "expected"),
    [
        ("다올투자증권(주)", Sector.BANKING),
        # 세 자리 661 은 PG사·기업인수목적회사도 쓴다. 이름이 '증권'으로 끝나는 회사만 증권사다.
        ("에스케이증권제14호기업인수목적 주식회사", Sector.FINANCIAL),
        ("(주)헥토파이낸셜", Sector.FINANCIAL),
        (None, Sector.FINANCIAL),
    ],
)
def test_three_digit_financial_support_code_splits_securities_by_name(corp_name, expected):
    assert sector_for("661", corp_name) is expected


@pytest.mark.parametrize("key", ["operating_margin", "net_margin", "revenue_growth"])
def test_sales_based_metrics_are_not_applicable_to_banks(key):
    """은행·금융지주는 '매출' 줄이 없어 이 셋이 ⚫(데이터 없음)로 나갔다 — 없는 게 아니라 개념이 없다."""
    metric = checkup(fin(**BANK), fin(**BANK), sector=Sector.BANKING).by_key(key)
    assert metric.signal is Signal.NOT_APPLICABLE
    assert "'매출' 개념이 없어" in metric.note


def test_securities_operating_revenue_is_not_judged_as_sales():
    """증권사는 '영업수익' 줄이 있어도 금융상품 평가·처분이익 총액이라 매출로 판정하지 않는다."""
    securities = fin(**BANK, revenue=22_242_000_000_000)
    prior = fin(**BANK, revenue=18_000_000_000_000)
    result = checkup(securities, prior, sector=Sector.BANKING)
    for key in ("operating_margin", "net_margin", "revenue_growth"):
        metric = result.by_key(key)
        assert metric.signal is Signal.NOT_APPLICABLE, key
        assert metric.band is None


def test_insurer_revenue_is_still_judged():
    """보험사의 보험수익(IFRS 17)은 매출에 해당한다 — 금융업이어도 이익률을 판정한다."""
    insurer = fin(**BANK, revenue=17_276_000_000_000)
    result = checkup(insurer, sector=Sector.FINANCIAL)
    assert result.by_key("operating_margin").signal in (Signal.GREEN, Signal.YELLOW, Signal.RED)
    assert result.by_key("net_margin").signal in (Signal.GREEN, Signal.YELLOW, Signal.RED)


def test_banking_keeps_every_financial_rule():
    """은행·증권·금융지주는 금융업 규칙(부채비율 ⊘·ROA 밴드·좀비 제외)을 모두 그대로 받는다."""
    from fin_checkup.metrics.engine import NOT_APPLICABLE_BY_SECTOR

    assert set(NOT_APPLICABLE_BY_SECTOR[Sector.FINANCIAL]) < set(NOT_APPLICABLE_BY_SECTOR[Sector.BANKING])
    weak = dict(BANK) | {"net_income": 2_300_000_000_000}  # ROA 약 0.3%
    assert checkup(fin(**weak), sector=Sector.BANKING).by_key("roa").signal is Signal.RED
    history = [
        Financials(corp_code="105560", bsns_year=y, reprt_code=ReportCode.ANNUAL,
                   fs_div=FsDiv.CFS, operating_income=6_700_000_000_000,
                   interest_expense=14_600_000_000_000)
        for y in (2022, 2023, 2024)
    ]
    assert "zombie_streak" not in {f.key for f in detect_red_flags(history, sector=Sector.BANKING)}
    assert Sector.BANKING.is_financial and Sector.FINANCIAL.is_financial
    assert not Sector.GENERAL.is_financial


# ── 지주회사(64992): 규칙을 바꾸지 않는다 (Q13) ─────────────────────


@pytest.mark.parametrize(
    "corp_name", ["주식회사 엘엑스홀딩스", "에스케이스퀘어 주식회사", "(주)LG", "(주)한진칼"]
)
def test_non_financial_holding_company_stays_on_general_rules(corp_name):
    """비금융 지주는 연결재무제표가 그룹의 제조·유통 사업을 담는다(2024·2025 비금융 64992 전부 연결).
    부채비율·이자보상배율은 실제로 갚아야 할 빚이라 금융업 ⊘ 로 가리지 않는다."""
    assert sector_for("64992", corp_name) is Sector.GENERAL


@pytest.mark.parametrize("corp_name", ["(주)KB금융지주", "(주)신한금융지주회사", "(주)메리츠금융지주"])
def test_financial_holding_company_stays_financial(corp_name):
    sector = sector_for("64992", corp_name)
    assert sector.is_financial and sector is Sector.BANKING


def test_holding_company_margin_above_100_percent_is_still_judged():
    """지분법이익을 영업수익에 넣는 지주는 영업이익률이 100% 를 넘는다(엘엑스홀딩스 2024 연결 384%).
    공시 그대로의 치우친 값이지 뜻이 없는 값이 아니라 ⊘ 로 빼지 않는다(⊘ 기준)."""
    holding = fin(revenue=100, operating_income=384, net_income=300, total_assets=5000,
                  total_liabilities=1000, total_equity=4000)
    margin = checkup(holding, sector=sector_for("64992", "주식회사 엘엑스홀딩스")).by_key("operating_margin")
    assert margin.signal is Signal.GREEN and margin.value == pytest.approx(384)
