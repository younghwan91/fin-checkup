"""화면 공통 표시 규칙 — Streamlit 과 정적 사이트가 같은 문장을 쓴다."""

from __future__ import annotations

from hub_seed import HLB, KB, SAMSUNG, seed_site_cache

from fin_checkup.config import Settings
from fin_checkup.metrics.signals import Signal
from fin_checkup.presentation import band_text, metrics_by_category, recent_changes, summary_chips
from fin_checkup.service import CheckupService
from fin_checkup.storage import Cache


async def _run(tmp_path, corp):
    with Cache(tmp_path / "p.duckdb") as cache:
        seed_site_cache(cache)
        service = CheckupService(cache, settings=Settings(_env_file=None, dart_api_key=""))
        return await service.run(corp, end_year=2024, allow_fetch=False)


async def test_not_applicable_band_text_points_to_the_alternative(tmp_path):
    data = await _run(tmp_path, KB)
    debt = data.result.by_key("debt_ratio")
    assert debt.signal is Signal.NOT_APPLICABLE
    assert "대신 볼 것" in band_text(debt)
    general = await _run(tmp_path / "s", SAMSUNG)
    assert "절대 기준 없음" in band_text(general.result.by_key("asset_turnover"))


async def test_recent_changes_needs_only_two_years(tmp_path):
    """사업연도 둘이면 급변을 볼 수 있다. 셋을 요구하면 2023·2024 캐시로는 아무도 못 본다."""
    data = await _run(tmp_path, KB)
    window = recent_changes(data)
    assert window is not None and (window.prior_year, window.latest_year) == (2023, 2024)
    assert recent_changes(await _run(tmp_path / "h", HLB)) is None


async def test_recent_changes_judges_the_prior_year_with_the_same_sector(tmp_path):
    data = await _run(tmp_path, KB)
    keys = {c.key for c in recent_changes(data).changes}
    assert not keys & {"debt_ratio", "current_ratio", "equity_ratio"}


async def test_chips_always_show_primary_and_only_present_secondary(tmp_path):
    data = await _run(tmp_path, SAMSUNG)
    shown = [s for s, _ in summary_chips(data.result)]
    assert shown[:3] == [Signal.RED, Signal.YELLOW, Signal.GREEN]
    assert Signal.NOT_APPLICABLE not in shown or data.result.counts[Signal.NOT_APPLICABLE]


async def test_metrics_are_grouped_and_worst_first(tmp_path):
    from fin_checkup.metrics.signals import SEVERITY

    data = await _run(tmp_path, SAMSUNG)
    for _category, metrics in metrics_by_category(data.result):
        severities = [SEVERITY[m.signal] for m in metrics]
        assert severities == sorted(severities, reverse=True)


def test_band_text_without_a_caution_band_does_not_invent_one():
    # 영업활동현금흐름·FCF 는 good == warn == 0 — 🟡 구간이 없다.
    # "주의 0 이상" 이라고 쓰면 있는 것처럼 읽힌다.
    from fin_checkup.metrics.engine import Category, Metric
    from fin_checkup.metrics.signals import Band, Signal

    def metric(band):
        return Metric(
            key="fcf", label="잉여현금흐름", category=Category.EARNINGS_QUALITY, value=1.0, unit="money",
            description="", formula="", band=band, signal=Signal.GREEN,
        )

    up = band_text(metric(Band(good=0, warn=0)))
    assert "주의" not in up.replace("주의 구간 없음", "")
    assert up == "정상 0 이상 · 그 미만은 위험 (주의 구간 없음)"
    down = band_text(metric(Band(good=5, warn=5, higher_is_better=False)))
    assert down == "정상 5 이하 · 그 초과는 위험 (주의 구간 없음)"
    normal = band_text(metric(Band(good=7, warn=0)))
    assert normal == "정상 7 이상 · 주의 0 이상 · 그 미만은 위험"
