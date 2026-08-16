from __future__ import annotations

from fin_checkup.metrics.changes import detect_changes
from fin_checkup.metrics.engine import checkup
from fin_checkup.models import Financials, FsDiv, ReportCode


def fin(year: int, **kwargs) -> Financials:
    return Financials(
        corp_code="1", bsns_year=year, reprt_code=ReportCode.ANNUAL, fs_div=FsDiv.CFS, **kwargs
    )


def keys(changes) -> list[str]:
    return [c.key for c in changes]


def test_signal_downgrade_is_detected():
    prior = checkup(fin(2023, total_liabilities=500, total_equity=1000))  # 50% 🟢
    current = checkup(fin(2024, total_liabilities=2500, total_equity=1000))  # 250% 🔴
    changes = detect_changes(current, prior)
    assert "debt_ratio" in keys(changes)
    assert changes[0].downgraded is True


def test_improvement_is_not_reported():
    prior = checkup(fin(2023, total_liabilities=2500, total_equity=1000))
    current = checkup(fin(2024, total_liabilities=500, total_equity=1000))
    assert "debt_ratio" not in keys(detect_changes(current, prior))


def test_large_move_within_same_grade_is_reported():
    # 영업이익률 20% → 12%: 둘 다 🟢이지만 40% 악화.
    prior = checkup(fin(2023, revenue=1000, operating_income=200))
    current = checkup(fin(2024, revenue=1000, operating_income=120))
    changes = detect_changes(current, prior)
    assert "operating_margin" in keys(changes)
    assert changes[0].downgraded is False


def test_small_move_is_ignored():
    prior = checkup(fin(2023, revenue=1000, operating_income=200))
    current = checkup(fin(2024, revenue=1000, operating_income=190))
    assert "operating_margin" not in keys(detect_changes(current, prior))


def test_lower_is_better_direction_is_respected():
    # 부채비율은 낮을수록 좋다. 100%→140%는 악화(둘 다 🟡 경계 안이어도 잡혀야 함).
    prior = checkup(fin(2023, total_liabilities=1000, total_equity=1000))
    current = checkup(fin(2024, total_liabilities=1400, total_equity=1000))
    assert "debt_ratio" in keys(detect_changes(current, prior))


def test_unknown_to_known_is_not_a_downgrade():
    prior = checkup(fin(2023))
    current = checkup(fin(2024, total_liabilities=2500, total_equity=1000))
    for change in detect_changes(current, prior):
        assert change.downgraded is False


def test_nothing_changed_yields_empty():
    prior = checkup(fin(2023, revenue=1000, operating_income=150))
    current = checkup(fin(2024, revenue=1000, operating_income=150))
    assert detect_changes(current, prior) == []


def test_threshold_is_configurable():
    prior = checkup(fin(2023, revenue=1000, operating_income=200))
    current = checkup(fin(2024, revenue=1000, operating_income=190))
    assert "operating_margin" in keys(detect_changes(current, prior, move_threshold=1.0))
