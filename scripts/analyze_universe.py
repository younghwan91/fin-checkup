"""수집된 캐시로 두 가지를 measure한다.

1. **정규화 적중률** — 어떤 계정이 얼마나 자주 비는지. 태그 보강의 근거.
2. **지표 분포** — 실제 시장의 분위수. 신호등 경계값 재조정의 근거.

지금 경계값(부채비율 100/200% 등)은 통용되는 기준을 옮겨놓은 것이라, 한국 시장
분포와 얼마나 맞는지 확인해야 한다.

    uv run python scripts/analyze_universe.py --year 2024
"""

from __future__ import annotations

import argparse
import collections
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fin_checkup.config import settings  # noqa: E402
from fin_checkup.dart.normalize import FIELD_SPECS, normalize_statements  # noqa: E402
from fin_checkup.metrics.engine import METRIC_DEFS, checkup  # noqa: E402
from fin_checkup.metrics.sector import Sector, sector_for  # noqa: E402
from fin_checkup.metrics.signals import Signal  # noqa: E402
from fin_checkup.storage import Cache  # noqa: E402

QUANTILES = [5, 10, 25, 50, 75, 90, 95]


def quantile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = (len(ordered) - 1) * pct / 100
    lo, hi = int(idx), min(int(idx) + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--sector", choices=["all", "general", "financial"], default="general")
    args = parser.parse_args()

    with Cache(settings.fin_checkup_db_path, read_only=True) as cache:
        rows = cache.conn.execute(
            "SELECT DISTINCT corp_code FROM statement_lines WHERE bsns_year = ?", [args.year]
        ).fetchall()
        corp_codes = [r[0] for r in rows]
        print(f"분석 대상 {len(corp_codes):,}사 ({args.year}년)\n")

        missing: collections.Counter[str] = collections.Counter()
        by_sector: collections.Counter[str] = collections.Counter()
        values: dict[str, list[float]] = collections.defaultdict(list)
        signals: dict[str, collections.Counter[str]] = collections.defaultdict(
            collections.Counter
        )
        analyzed = 0

        prior_available = 0
        for code in corp_codes:
            raw = cache.get_statements(code, args.year)
            if raw is None:
                continue
            fin = normalize_statements(raw)
            # 성장성 지표는 전년 재무가 있어야 계산된다. 서비스와 같은 조건으로 재야
            # 실제 사용자가 보는 화면을 측정하는 것이 된다.
            prior_raw = cache.get_statements(code, args.year - 1)
            prior = normalize_statements(prior_raw) if prior_raw is not None else None
            if prior is not None:
                prior_available += 1
            company = cache.get_company(code)
            sector = sector_for(company.industry_code if company else None)
            by_sector[sector.value] += 1

            if args.sector != "all" and sector.value != args.sector:
                continue
            analyzed += 1

            for spec in FIELD_SPECS:
                if getattr(fin, spec.name) is None:
                    missing[spec.name] += 1

            for metric in checkup(fin, prior, sector=sector).metrics:
                signals[metric.key][metric.signal.value] += 1
                if metric.value is not None and metric.signal not in (
                    Signal.UNKNOWN,
                    Signal.NOT_APPLICABLE,
                ):
                    values[metric.key].append(metric.value)

    print("=== 업종 분포 ===")
    for name, count in by_sector.most_common():
        print(f"  {Sector(name).label:<8} {count:>5,}사")

    print(f"\n전년({args.year - 1}) 재무가 함께 있는 기업: {prior_available:,}사")

    print(f"\n=== 계정 누락률 ({args.sector}, {analyzed:,}사) ===")
    for spec in FIELD_SPECS:
        n = missing[spec.name]
        if n:
            print(f"  {spec.name:<22} {n:>5,}사 ({n / max(analyzed, 1) * 100:5.1f}%)")
    clean = [s.name for s in FIELD_SPECS if not missing[s.name]]
    if clean:
        print(f"  (누락 0건: {', '.join(clean)})")

    print("\n=== 지표 분포 (분위수) ===")
    header = "지표".ljust(22) + "".join(f"p{q}".rjust(10) for q in QUANTILES) + "표본".rjust(8)
    print(header)
    print("-" * len(header))
    for spec in METRIC_DEFS:
        vals = values.get(spec.key, [])
        if len(vals) < 30 or spec.unit == "money":
            continue
        cells = "".join(f"{quantile(vals, q):10.1f}" for q in QUANTILES)
        print(f"  {spec.label[:20]:<20}{cells}{len(vals):>8,}")

    print("\n=== 현재 경계값이 만드는 신호 분포 ===")
    for spec in METRIC_DEFS:
        counts = signals.get(spec.key)
        if not counts:
            continue
        total = sum(counts.values())
        green = counts.get("green", 0) / total * 100
        yellow = counts.get("yellow", 0) / total * 100
        red = counts.get("red", 0) / total * 100
        unknown = counts.get("unknown", 0) / total * 100
        band = spec.band
        band_txt = (
            f"{band.good:g}/{band.warn:g}{'↑' if band.higher_is_better else '↓'}"
            if band
            else "-"
        )
        print(
            f"  {spec.label[:20]:<20} 🟢{green:5.1f}% 🟡{yellow:5.1f}% 🔴{red:5.1f}% "
            f"⚫{unknown:5.1f}%   경계 {band_txt}"
        )

    if values.get("debt_ratio"):
        print(
            f"\n참고: 부채비율 중앙값 {statistics.median(values['debt_ratio']):.1f}%, "
            f"p90 {quantile(values['debt_ratio'], 90):.1f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
