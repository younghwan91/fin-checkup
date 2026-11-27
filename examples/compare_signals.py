"""두 회사의 신호등을 나란히 — fin-checkup 정적 JSON API 사용 예.

    python examples/compare_signals.py 005930 000660
    python examples/compare_signals.py 005930 000660 --base ./site-out   # 로컬 빌드 폴더

표준 라이브러리만 쓴다(설치할 것 없음). 값은 측정 결과다 — 투자권유·자문이 아니며, 다시 보여 줄 땐
파일에 든 `disclaimer` 를 함께 보여 준다. 스키마: docs/api-static.md
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SITE = "https://younghwan91.github.io/fin-checkup/"
#: 정적 파일이라 키·호출 제한은 없지만, 누가 부르는지 알 수 있게 이름을 붙인다.
USER_AGENT = "fin-checkup-example/1"


def fetch_json(base: str, path: str) -> dict[str, Any]:
    """base 가 http(s) 주소면 내려받고, 아니면 로컬 빌드 폴더에서 읽는다."""
    if base.startswith(("http://", "https://")):
        url = base.rstrip("/") + "/" + path
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SystemExit(f"{url}: 데이터를 받지 못했습니다 ({exc}).") from exc
    target = Path(base) / path
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"{target}: 파일을 읽지 못했습니다 ({exc}).") from exc


def load_company(base: str, stock_code: str) -> dict[str, Any]:
    index = fetch_json(base, "api/index.json")
    entry = next((c for c in index["companies"] if c["stock_code"] == stock_code), None)
    if entry is None:
        raise SystemExit(f"{stock_code}: 사이트에 없는 종목코드입니다 (api/index.json).")
    return fetch_json(base, entry["json"])


def format_value(metric: dict[str, Any], currency: str) -> str:
    value = metric["value"]
    if value is None:  # 없는 값은 null — 0 으로 읽지 않는다.
        return "값 없음"
    unit = metric["unit"]
    if unit == "money":
        if currency == "KRW":
            return f"{value / 1e8:,.0f}억원"
        return f"{value:,.0f} {currency}"
    if unit == "%":
        return f"{value:,.1f}%"
    return f"{value:,.2f}{unit}"


def cell(metric: dict[str, Any] | None, currency: str) -> str:
    if metric is None:
        return "-"
    signal = metric["signal"]
    return f"{signal['emoji']} {signal['label']} {format_value(metric, currency)}"


def _width(text: str) -> int:
    # 한글·이모지는 터미널에서 두 칸을 차지한다.
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * (width - _width(text))


def thresholds(metric: dict[str, Any], other: dict[str, Any] | None, names: tuple[str, str]) -> str:
    """경계값 한 줄. 업종이 달라 두 회사의 경계값이 다르면 둘 다 적는다(예: 금융업 ROA)."""
    if other is None or other["threshold_text"] == metric["threshold_text"]:
        return metric["threshold_text"]
    return f"{names[0]}: {metric['threshold_text']} / {names[1]}: {other['threshold_text']}"


def side_by_side(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """지표마다 한 줄: 이름 · A · B · 경계값. 순서는 파일의 지표 순서 그대로(순위를 매기지 않는다)."""
    names = (a["company"]["name"], b["company"]["name"])
    b_metrics = {m["key"]: m for m in b["metrics"]}
    rows = [("지표", *names, "경계값")]
    for metric in a["metrics"]:
        other = b_metrics.get(metric["key"])
        rows.append(
            (
                metric["label"],
                cell(metric, a["currency"]),
                cell(other, b["currency"]),
                thresholds(metric, other, names),
            )
        )
    widths = [max(_width(row[i]) for row in rows) for i in range(3)]
    lines = []
    for i, row in enumerate(rows):
        lines.append("  ".join(_pad(text, widths[n]) for n, text in enumerate(row[:3])) + "  " + row[3])
        if i == 0:
            lines.append("-" * (sum(widths) + 4 + _width(row[3]) + 2))
    return lines


def report(a: dict[str, Any], b: dict[str, Any]) -> str:
    years = f"{a['fiscal_year']} 사업연도 · {a['report']['label']}"
    if b["fiscal_year"] != a["fiscal_year"]:
        years += f" / {b['company']['name']}: {b['fiscal_year']} 사업연도"
    lines = [years, ""]
    lines += side_by_side(a, b)
    lines += [
        "",
        "⊘ 는 그 업종에서 뜻이 없는 지표, ⚫ 는 공시에서 값을 찾지 못한 지표입니다(0 이 아닙니다).",
        "계산식·대신 볼 것·대조군 분포는 회사 페이지와 JSON 의 formula·note·peer 에 있습니다.",
        "출처: 금융감독원 전자공시시스템(DART)",
        a["disclaimer"],
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="두 회사의 신호등을 나란히 (fin-checkup 정적 JSON)")
    parser.add_argument("first", help="종목코드 (예: 005930)")
    parser.add_argument("second", help="종목코드 (예: 000660)")
    parser.add_argument("--base", default=SITE, help=f"사이트 주소나 로컬 빌드 폴더 (기본 {SITE})")
    args = parser.parse_args(argv)

    a = load_company(args.base, args.first)
    b = load_company(args.base, args.second)
    print(report(a, b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
