from __future__ import annotations

import pytest

from fin_checkup.alerts.classify import RiskKind, Severity, classify
from fin_checkup.models import Disclosure


def disc(report_nm: str) -> Disclosure:
    return Disclosure(
        rcept_no="20240101000001", corp_code="00126380", corp_name="삼성전자",
        stock_code="005930", report_nm=report_nm, rcept_dt="20240101",
    )


@pytest.mark.parametrize(
    ("report_nm", "expected"),
    [
        ("주요사항보고서(유상증자결정)", RiskKind.CAPITAL_RAISE),
        ("유상증자결정", RiskKind.CAPITAL_RAISE),
        ("주요사항보고서(전환사채권발행결정)", RiskKind.CONVERTIBLE_BOND),
        ("주요사항보고서(신주인수권부사채권발행결정)", RiskKind.CONVERTIBLE_BOND),
        ("주요사항보고서(교환사채권발행결정)", RiskKind.CONVERTIBLE_BOND),
        ("감사보고서제출", RiskKind.AUDIT_OPINION),
        ("[기재정정]감사보고서제출", RiskKind.AUDIT_OPINION),
        ("주요사항보고서(감자결정)", RiskKind.CAPITAL_REDUCTION),
        ("최대주주변경", RiskKind.MAJOR_SHAREHOLDER),
        ("주식등의대량보유상황보고서(일반)", RiskKind.MAJOR_SHAREHOLDER),
        ("임원ㆍ주요주주특정증권등소유상황보고서", RiskKind.INSIDER_TRADING),
        ("주요사항보고서(부도발생)", RiskKind.DISTRESS),
        ("주요사항보고서(회생절차개시신청)", RiskKind.DISTRESS),
        ("주요사항보고서(영업정지)", RiskKind.DISTRESS),
        ("주요사항보고서(파산신청)", RiskKind.DISTRESS),
        ("관리종목지정", RiskKind.LISTING_STATUS),
        ("상장폐지사유발생", RiskKind.LISTING_STATUS),
    ],
)
def test_known_risk_disclosures_are_classified(report_nm, expected):
    result = classify(disc(report_nm))
    assert result is not None, f"'{report_nm}'을 분류하지 못했다"
    assert result.kind is expected


@pytest.mark.parametrize(
    "report_nm",
    [
        "사업보고서 (2023.12)",
        "분기보고서 (2024.03)",
        "현금ㆍ현물배당결정",
        "기업설명회(IR)개최(안내공시)",
        "특수관계인과의내부거래",
    ],
)
def test_routine_disclosures_are_not_flagged(report_nm):
    assert classify(disc(report_nm)) is None


def test_distress_is_the_highest_severity():
    assert classify(disc("주요사항보고서(부도발생)")).severity is Severity.CRITICAL
    assert classify(disc("상장폐지사유발생")).severity is Severity.CRITICAL


def test_capital_raise_is_lower_than_distress():
    raise_ = classify(disc("주요사항보고서(유상증자결정)"))
    distress = classify(disc("주요사항보고서(부도발생)"))
    assert raise_.severity.rank < distress.severity.rank


def test_more_specific_pattern_wins():
    # '전환사채권발행결정'은 '사채'라는 일반어보다 우선한다.
    result = classify(disc("주요사항보고서(전환사채권발행결정)"))
    assert result.kind is RiskKind.CONVERTIBLE_BOND


def test_correction_prefix_does_not_change_classification():
    plain = classify(disc("주요사항보고서(유상증자결정)"))
    corrected = classify(disc("[기재정정]주요사항보고서(유상증자결정)"))
    assert corrected is not None
    assert corrected.kind is plain.kind
    assert corrected.is_correction is True
    assert plain.is_correction is False


def test_whitespace_is_ignored():
    assert classify(disc("주요사항보고서 ( 유상증자 결정 )")) is not None


def test_classification_states_facts_only():
    for report_nm in ["주요사항보고서(유상증자결정)", "상장폐지사유발생", "감사보고서제출"]:
        result = classify(disc(report_nm))
        text = result.kind.label + result.why
        for word in ("매수", "매도", "추천", "사세요", "파세요", "손절", "익절"):
            assert word not in text, f"'{report_nm}' 설명에 투자권유 문구 '{word}'"


def test_every_risk_kind_has_a_plain_explanation():
    for kind in RiskKind:
        assert kind.label
        assert kind.why, f"{kind}에 설명이 없다"
