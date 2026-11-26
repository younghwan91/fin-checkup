"""회사별 마크다운 `/c/<종목코드>/index.md` (+ 별칭 `/c/<종목코드>.md`) — 에이전트가 HTML 을 긁지 않고 읽게.

짧게(회사당 수 KB): 한눈에 보기(사실 문단 `explain.summary.facts_summary`) / 지표 표(신호·값·작년·
비교군 중앙값·계산식·경계값) / 위험 신호 / 최근 위험 공시 / 출처·면책. 값·문장은 회사 페이지와 같은 것을
쓴다 — 판정은 `metrics.engine`, 표시 문자열은 `format.format_metric`, 경계값은 그 지표에 실제로 쓰인
`Metric.band` 를 기호로 줄인 것.
⚫ 은 '데이터 없음'(값 칸은 `—`), ⊘ 는 '해당 없음' + 메모에 대신 볼 것. 회사 HTML 은
`<link rel="alternate" type="text/markdown">` 으로 이 파일을 알린다 (`templates/company/index.html`).

가드가 면책 문구·금지 표현을, 링크 검사가 마크다운 링크를 본다 (`guard.is_agent_text`).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain.agents import AGENT_GUIDANCE
from fin_checkup.explain.summary import facts_summary, statement_label
from fin_checkup.format import MISSING, format_metric
from fin_checkup.metrics.engine import MONEY, Metric
from fin_checkup.metrics.peers import PeerScope
from fin_checkup.metrics.sector import peer_group_for
from fin_checkup.metrics.signals import Signal
from fin_checkup.quarter import LatestQuarter, debt_ratio_not_applicable, newer_than_annual
from fin_checkup.site.context import CompanyEntry, Site, root_for
from fin_checkup.site.disclosures import COMPANY_LIMIT
from fin_checkup.site.pages.api import DART_API
from fin_checkup.site.pages.metric import metric_path
from fin_checkup.site.registry import Output, page

DART = "https://dart.fss.or.kr/"
_SCOPE_TAG = {PeerScope.INDUSTRY: "업종", PeerScope.MARKET: "전체"}


def md_path(stock_code: str) -> str:
    return f"c/{stock_code}/index.md"


def md_alias_path(stock_code: str) -> str:
    return f"c/{stock_code}.md"


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|") or MISSING


def _num(value: float, unit: str) -> str:
    return f"{value:g}" if unit == MONEY else f"{value:g}{unit}"


def band_short(metric: Metric) -> str:
    """그 지표에 쓰인 경계값을 기호로 — `🟢 ≥10% · 🟡 ≥0% · 🔴 <0%`. 숫자는 엔진 Band 그대로."""
    if metric.signal is Signal.NOT_APPLICABLE:
        return "⊘ 이 업종엔 판정 안 함"
    band = metric.band
    if band is None:
        return "절대 기준 없음(⚪)"
    good, warn = _num(band.good, metric.unit), _num(band.warn, metric.unit)
    if band.higher_is_better:
        parts = [f"🟢 ≥{good}"] + ([f"🟡 ≥{warn}"] if band.good != band.warn else []) + [f"🔴 <{warn}"]
    else:
        parts = [f"🟢 ≤{good}"] + ([f"🟡 ≤{warn}"] if band.good != band.warn else []) + [f"🔴 >{warn}"]
    return " · ".join(parts)


def company_markdown(entry: CompanyEntry, site: Site, out_path: str) -> str:
    data = entry.checkup
    latest = data.history[-1]
    result = data.result
    currency = result.currency
    company = data.company

    def link(path: str) -> str:
        return site.absolute_url(path) if site.has_absolute_base else root_for(out_path) + path

    market = company.market.value if company and company.market else ""
    industry = company.industry_code if company else ""
    group = peer_group_for(industry) if industry else ""
    years = [f.bsns_year for f in data.history]
    span = f"{years[0]}–{years[-1]}" if len(years) > 1 else str(years[0])
    facts = [
        market,
        f"{result.sector.label} 업종" + (f" (KSIC {industry}, 비교 단위 {group})" if industry else ""),
        statement_label(latest.fs_div),
        f"금액 {currency}",
        f"계산에 쓴 해 {span}",
        f"빌드 {site.built_on.isoformat()}",
    ]
    lines = [
        f"# {entry.name} ({entry.stock_code}) 재무 측정"
        f" — {latest.bsns_year} 사업연도 {latest.reprt_code.label}",
        "",
        " · ".join(f for f in facts if f),
        "",
        f"[웹 페이지]({link(entry.path)}) · [JSON]({link(f'api/c/{entry.stock_code}.json')})",
        "",
        "## 한눈에 보기",
        "",
        facts_summary(data),
        "",
        "## 지표",
        "",
        "| 지표 | 신호 | 값 | 작년 | 비교군 중앙값 | 계산식 | 경계값 |",
        "|---|---|---|---|---|---|---|",
    ]
    notes: dict[tuple[str, str], list[str]] = {}
    for m in result.metrics:
        delta = data.deltas.get(m.key)
        prev = (
            format_metric(dataclasses.replace(m, value=delta.previous), currency)
            if delta is not None and delta.previous is not None
            else MISSING
        )
        peer = data.peers.get(m.key)
        median = f"{peer.median_text} ({_SCOPE_TAG[peer.scope]} {peer.sample_size}곳)" if peer else MISSING
        cells = [
            m.label,
            f"{m.signal.emoji} {m.signal.label}",
            format_metric(m, currency),
            prev,
            median,
            m.formula,
            band_short(m),
        ]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
        # ⚪ 의 메모는 늘 같은 문장('업종별 편차가 커서…')이라 경계값 칸으로 충분하다.
        if m.note and m.signal is not Signal.NEUTRAL:
            # 같은 메모는 한 줄로 — 은행의 이익률·순이익률·매출 증가율 ⊘ 는 이유가 하나다.
            notes.setdefault((m.signal.emoji, " ".join(m.note.split())), []).append(m.label)
    if notes:
        lines += [
            "", "메모 (⊘ 는 대신 볼 것, ⚫ 은 무엇이 없었는지):", "",
            *(f"- {'·'.join(labels)} ({emoji}): {note}" for (emoji, note), labels in notes.items()),
        ]
    lines += [
        "",
        f"지표마다 뜻·경계값 근거·한계: [지표 해설]({link('m/')})"
        f" (예: [{result.metrics[0].label}]({link(metric_path(result.metrics[0].key))}))."
        " ⚫ 데이터 없음은 0 이 아니라 공시에서 계정을 찾지 못했다는 뜻입니다.",
        "",
        "## 위험 신호",
        "",
    ]
    if data.red_flags:
        for f in data.red_flags:
            ref = f" ({f.reference})" if f.reference else ""
            lines.append(f"- 🔴 {f.label} — {f.detail}{ref}")
    else:
        lines.append("재무제표로 확인된 위험 신호(자본잠식·연속 영업손실 등)는 없습니다.")
    quarter = newer_than_annual(site.quarters.get(entry.corp.corp_code), result.bsns_year)
    if quarter is not None:
        lines += ["", *quarter_markdown(quarter, result.bsns_year, debt_ratio_not_applicable(result))]
    lines += ["", "## 최근 위험 공시", ""]
    feed = site.disclosures
    if feed is None:
        lines.append(
            "이번 빌드에는 공시 목록이 실리지 않았습니다. 빠진 공시가 있을 수 있으니 DART 에서 회사 이름으로"
            f" 확인하세요: {DART}"
        )
    else:
        items = feed.for_company(entry.stock_code)
        period = f"{feed.begin.isoformat()}~{feed.end.isoformat()}"
        if items:
            lines.append(f"{period} DART 공시 중 제목으로 위험 공시 유형에 분류된 것 (해석 없음, 원문 확인):")
            lines.append("")
            for item in items[:COMPANY_LIMIT]:
                lines.append(
                    f"- {item.rcept_dt.isoformat()} · {item.label} · {_cell(item.report_nm)}"
                    f" · [DART 원문]({item.url})"
                )
            if len(items) > COMPANY_LIMIT:
                lines.append(f"- … {len(items) - COMPANY_LIMIT}건 더 (DART 에서 회사 이름으로 검색)")
        else:
            lines.append(
                f"{period} 목록에 이 회사 항목이 없습니다. 제목 기준 분류라 빠진 공시가 있을 수 있으니"
                f" DART 에서 확인하세요: {DART}"
            )
        if feed.truncated:
            lines.append("일부 공시유형은 조회 상한에 걸려 빠진 공시가 있을 수 있습니다.")
    params = (
        f"corp_code={entry.corp.corp_code}&bsns_year={latest.bsns_year}"
        f"&reprt_code={latest.reprt_code.value}&fs_div={latest.fs_div.value}"
    )
    lines += [
        "",
        "## 출처·면책",
        "",
        f"- 출처: 금융감독원 전자공시시스템(DART) {DART} · OpenDART `{DART_API}?{params}` (인증키는 각자)",
        f"- 이 회사 페이지: [{entry.name}]({link(entry.path)})",
        f"- 에이전트 안내: {AGENT_GUIDANCE} More: [llms.txt]({link('llms.txt')})",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines) + "\n"


def quarter_markdown(q: LatestQuarter, annual_year: int, debt_ratio_na: str | None = None) -> list[str]:
    """최근 분기 주요계정 — 판정 없이 값만. 없는 값은 ⚫."""

    def cell(value: float | None, text: str) -> str:
        return "⚫ 데이터 없음" if value is None else text

    def ratio(value: float | None) -> str:
        return "⚫ 계산 안 함" if value is None else f"{value:,.1f}%"

    end = q.period_end.strftime("%Y.%m.%d")

    def row(ln) -> str:
        now, before = cell(ln.current, ln.current_text), cell(ln.prior, ln.prior_text)
        return f"| {ln.label} | {now} | {before} | {ln.change} |"

    out = [
        f"## 최근 분기 — {q.period_label}",
        "",
        f"{q.report_label} · {q.statement_label}" + (f" · [DART 원문]({q.dart_url})" if q.dart_url else ""),
        "",
        f"| 손익 (석 달) | {q.period_label} | {q.prior_period_label} | 변화 |",
        "|---|---|---|---|",
        *(row(ln) for ln in q.flows),
        "",
        f"| 재무상태 | {end} | {q.prior_year_end_label} | 변화 |",
        "|---|---|---|---|",
        *(row(ln) for ln in q.stocks),
        (
            f"| 부채비율 | ⊘ 해당 없음 | ⊘ 해당 없음 | {_cell(debt_ratio_na)} |"
            if debt_ratio_na
            else f"| 부채비율 (부채총계 ÷ 자본총계 × 100) | {ratio(q.debt_ratio('current'))}"
            f" | {ratio(q.debt_ratio('prior'))} | |"
        ),
        "",
        f"신호등은 {annual_year}년 사업보고서로만 매깁니다."
        " 이 표는 그 뒤 분기보고서의 주요계정을 판정 없이 옮긴 것이고,"
        " 손익은 그 분기 석 달 값입니다(분기 재무제표는 감사가 아니라 검토).",
    ]
    return out


@page("company-md")
def company_markdown_files(site: Site) -> Iterator[Output]:
    for entry in site.companies:
        for path in (md_path(entry.stock_code), md_alias_path(entry.stock_code)):
            yield Output(path, company_markdown(entry, site, path))
