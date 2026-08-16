"""업종군별로 판정이 왜곡되는 곳을 찾는다.

금융업에서 겪은 문제 — 계산은 되는데 뜻이 없는 지표에 🔴가 붙는 것 — 가 다른
업종에도 있는지 확인하는 도구다.

**🔴 비율이 높다고 곧바로 오판정은 아니다.** 실제로 부실한 기업이 많은 업종일 수도
있다. 그래서 업종 평균과 시장 평균을 나란히 놓고, 특정 지표에만 🔴가 쏠리는지를 본다.
한 지표에만 몰려 있으면 그 지표가 그 업종에 맞지 않는다는 신호다.

    uv run python scripts/check_sectors.py --year 2024
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fin_checkup.config import settings  # noqa: E402
from fin_checkup.dart.normalize import normalize_statements  # noqa: E402
from fin_checkup.metrics.engine import METRIC_DEFS, checkup  # noqa: E402
from fin_checkup.metrics.sector import sector_for  # noqa: E402
from fin_checkup.metrics.signals import Signal  # noqa: E402
from fin_checkup.storage import Cache  # noqa: E402

#: KSIC 대분류 앞 두 자리 → 사람이 읽는 이름.
GROUPS: dict[str, tuple[str, ...]] = {
    "건설업": ("41", "42"),
    "조선·기타운송장비": ("31",),
    "자동차·부품": ("30",),
    "전자·반도체": ("26",),
    "화학": ("20", "21"),
    "금속": ("24", "25"),
    "기계·장비": ("28", "29"),
    "도매·소매": ("45", "46", "47"),
    "운수·창고": ("49", "50", "51", "52"),
    "출판·정보서비스": ("58", "62", "63"),
    "전문·과학기술": ("70", "71", "72", "73"),
    "금융·보험": ("64", "65", "66"),
    "부동산": ("68",),
}

MIN_SAMPLE = 8


def group_for(industry_code: str) -> str | None:
    prefix = (industry_code or "")[:2]
    return next((name for name, prefixes in GROUPS.items() if prefix in prefixes), None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args()

    with Cache(settings.fin_checkup_db_path, read_only=True) as cache:
        codes = [
            r[0]
            for r in cache.conn.execute(
                "SELECT DISTINCT corp_code FROM statement_lines WHERE bsns_year = ?",
                [args.year],
            ).fetchall()
        ]

        counts: collections.Counter[str] = collections.Counter()
        signal_totals: dict[str, collections.Counter[str]] = collections.defaultdict(
            collections.Counter
        )
        red_by_metric: dict[str, collections.Counter[str]] = collections.defaultdict(
            collections.Counter
        )
        market_red: collections.Counter[str] = collections.Counter()
        market_total = 0

        for code in codes:
            company = cache.get_company(code)
            if company is None:
                continue
            group = group_for(company.industry_code)
            raw = cache.get_statements(code, args.year)
            if raw is None:
                continue

            result = checkup(
                normalize_statements(raw), sector=sector_for(company.industry_code)
            )
            market_total += 1
            for m in result.metrics:
                if m.signal is Signal.RED:
                    market_red[m.key] += 1
                if group is not None:
                    signal_totals[group][m.signal.value] += 1
                    if m.signal is Signal.RED:
                        red_by_metric[group][m.key] += 1
            if group is not None:
                counts[group] += 1

    labels = {d.key: d.label for d in METRIC_DEFS}
    market_rate = {k: v / max(market_total, 1) for k, v in market_red.items()}

    print(f"시장 전체 {market_total:,}사 · {args.year}년\n")
    print(f"{'업종군':<20}{'기업수':>6}{'🔴비율':>9}   시장 대비 🔴가 몰린 지표")
    print("-" * 92)

    suspicious: list[tuple[str, str, float, float]] = []
    for group in GROUPS:
        n = counts[group]
        if n < MIN_SAMPLE:
            print(f"{group:<20}{n:>6}   (표본 부족)")
            continue

        total = sum(signal_totals[group].values())
        red_pct = signal_totals[group]["red"] / total * 100

        # 시장 평균보다 유난히 높은 지표를 고른다.
        outliers = []
        for key, red_count in red_by_metric[group].items():
            rate = red_count / n
            base = market_rate.get(key, 0)
            if rate >= 0.4 and rate > base * 1.5:
                outliers.append((key, rate, base))
                suspicious.append((group, key, rate, base))
        outliers.sort(key=lambda x: x[1] - x[2], reverse=True)
        top = ", ".join(
            f"{labels.get(k, k)} {r * 100:.0f}%(시장 {b * 100:.0f}%)" for k, r, b in outliers[:3]
        )
        print(f"{group:<20}{n:>6}{red_pct:>8.1f}%   {top or '-'}")

    print("\n" + "=" * 92)
    if suspicious:
        print("검토 대상 — 해당 업종에 그 지표가 맞는지 확인할 것:")
        for group, key, rate, base in sorted(suspicious, key=lambda x: x[2] - x[3], reverse=True):
            print(
                f"  {group:<18} {labels.get(key, key):<20} "
                f"🔴 {rate * 100:5.1f}%  (시장 {base * 100:4.1f}%)"
            )
        print(
            "\n🔴가 몰렸다고 곧바로 오판정은 아니다. 실제로 부실한 기업이 많은 업종일 수 있다.\n"
            "특정 지표 하나에만 쏠려 있고 그 지표가 업종 구조상 뜻이 다르다면 그때 조치한다."
        )
    else:
        print("시장 평균 대비 특정 지표에 🔴가 쏠린 업종군은 없다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
