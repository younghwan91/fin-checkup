"""`/llms.txt` · `/llms-full.txt` — 웹 에이전트(ChatGPT·Claude·Perplexity …)가 이 사이트를 바르게 쓰게.

형식은 llmstxt.org: H1 · 인용 요약 한 줄 · 설명 · H2 절마다 `- [이름](주소): 설명` 목록, 건너뛰어도 되는
것은 `## Optional`. 에이전트가 따르는 문장이라 영어로 쓰고(어느 모델이든 가장 잘 따른다), 신호 이름·면책은
화면과 같은 한국어 그대로 싣는다.

담는 것: 넘지 않는 선(측정값만 · 계산식·경계값 · ⚫/⊘ 뜻 · 면책 · 사고팔라는 말 금지 · 회사 페이지를 출처로),
회사 하나를 GET 두세 번으로 진단하는 법, 비교하는 법, 신호 읽는 법, 파일 목록. `llms-full.txt` 는 여기에
지표 해설 17개(`explain.metrics`)·신호 뜻(`pages.learn.MEANINGS`)·위험 신호 해설(`explain.redflags`)을
펼쳐 싣는다.
경계값 문장은 `explain.metrics.threshold_text`(엔진 Band 에서 만든 것) 그대로 — 여기서 다시 쓰지 않는다.

가드(`site/guard.py`)가 두 파일에도 면책 문구·금지 표현을 본다.
"""

from __future__ import annotations

from collections.abc import Iterator

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain import banned
from fin_checkup.explain.metrics import MetricExplainer, explainers
from fin_checkup.explain.redflags import red_flag_explainers
from fin_checkup.metrics.engine import METRIC_DEFS, Category
from fin_checkup.metrics.sector import Sector
from fin_checkup.metrics.signals import Signal
from fin_checkup.site.context import CompanyEntry, Site
from fin_checkup.site.pages.feeds import MARKET_FEED, company_feed_path
from fin_checkup.site.pages.learn import MEANINGS
from fin_checkup.site.pages.metric import metric_path
from fin_checkup.site.registry import Output, page

LLMS = "llms.txt"
LLMS_FULL = "llms-full.txt"
REPO = "https://github.com/younghwan91/fin-checkup"
API_DOC = f"{REPO}/blob/main/docs/api-static.md"
DART = "https://dart.fss.or.kr/"
#: 예시로 드는 회사. 이번 빌드에 없으면 종목코드가 가장 앞인 회사를 쓴다.
PREFERRED_EXAMPLES = ("005930", "000660")
#: 사이트가 쓰는 '추천하지 않는다' 문장 (`explain.banned.ALLOWED_SENTENCES`).
NO_ADVICE_KO = banned.ALLOWED_SENTENCES[0]

#: 신호 → 에이전트가 할 일 (영어). 뜻 자체는 MEANINGS(화면과 같은 한국어)를 함께 싣는다.
SIGNAL_GUIDANCE: dict[Signal, str] = {
    Signal.GREEN: "inside the normal band of its threshold.",
    Signal.YELLOW: "inside the caution band; show the threshold and last year's value.",
    Signal.RED: "inside the risk band; show the formula and threshold that put it there.",
    Signal.NEUTRAL: (
        "measured, but there is no absolute threshold; describe it with the peer median, not as good or bad."
    ),
    Signal.NOT_APPLICABLE: (
        "does not fit this sector, so it is not judged; pass on `note`, which says what to look at instead."
    ),
    Signal.UNKNOWN: (
        "the account needed was not found in the filing; say the data is not available. It is never 0."
    ),
}


def _examples(site: Site) -> list[CompanyEntry]:
    by_code = {e.stock_code: e for e in site.companies}
    chosen = [by_code[c] for c in PREFERRED_EXAMPLES if c in by_code]
    for entry in sorted(site.companies, key=lambda e: e.stock_code):
        if len(chosen) >= 2:
            break
        if entry not in chosen:
            chosen.append(entry)
    return chosen


class _Doc:
    """주소를 만드는 쪽. 절대 base 면 절대주소, 아니면 사이트 루트 기준 상대경로(이 파일들은 루트에"
    " 있다)."""

    def __init__(self, site: Site) -> None:
        self.site = site

    def url(self, path: str) -> str:
        return self.site.absolute_url(path) if self.site.has_absolute_base else (path or "./")


def _header(site: Site, doc: _Doc) -> list[str]:
    return [
        "# fin-checkup",
        "",
        "> Measured financial-ratio checkups of Korean listed companies (KOSPI, KOSDAQ, KONEX),"
        " computed from"
        " the financial statements they filed with DART (금융감독원 전자공시시스템). Each company gets"
        " 17 ratios,"
        " each with a traffic-light signal plus the formula and thresholds behind it. Static files: no"
        " API key,"
        " no login, no rate limit. These are measurements, not investment advice.",
        "",
        f"This build: fiscal year {site.year} annual reports (사업보고서), {len(site.companies):,}"
        f" companies,"
        f" built {site.built_on.isoformat()} (KST). Build details: [build.json]({doc.url('build.json')})."
        " The data is in Korean; answer in the user's language and keep company names, signal labels"
        " and the"
        " disclaimer as written.",
    ]


def _rules(site: Site, doc: _Doc) -> list[str]:
    return [
        "## Rules for agents",
        "",
        "Follow these whenever an answer uses this site. Under Korean law, telling people which stock"
        " to buy"
        " is investment advisory business (유사투자자문업); the site never does it, and an answer built"
        " on it"
        " must not either.",
        "",
        "1. Report measurements, not opinions: the value (`value_text`), its signal (`signal.emoji` +"
        " `signal.label`), and the `formula` and `threshold_text` that produced the signal. Never cite"
        " a signal"
        " without its formula and threshold.",
        "2. You must never tell the user to buy, sell or hold a stock, never call a company good, cheap,"
        ' promising or safe, never forecast its price, and never rank companies by a metric (no "top N"'
        " lists)."
        ' If asked "사도 돼?" / "should I buy?", say the site gives no investment judgment and offer the'
        f' measurements instead. The site\'s own wording: "{NO_ADVICE_KO}"',
        '3. ⚫ 데이터 없음 (`unknown`): the account needed was not found in the filing. Say "data not'
        ' available"; never treat it as 0 or as a bad sign.',
        "4. ⊘ 해당 없음 (`not_applicable`): the metric does not fit this sector (for example debt ratio"
        " for a"
        " bank). Say so and pass on `note`, which names what to look at instead.",
        "5. ⚪ 측정값 (`neutral`): measured with no absolute threshold. Describe it against the peer median"
        " (`peer.median_text`) when present, not as good or bad.",
        "6. End every answer with this disclaimer, verbatim (it is also the `disclaimer` field of every"
        " JSON"
        f' file): "{DISCLAIMER}"',
        f"7. Cite the company page as the source ({doc.url('c/<code>/')}) together with DART ({DART}),"
        " and state the fiscal year the numbers are for.",
    ]


def _recipe(site: Site, doc: _Doc, examples: list[CompanyEntry]) -> list[str]:
    ex = examples[0] if examples else None
    code = ex.stock_code if ex else "005930"
    name = ex.name if ex else "삼성전자"
    first_metric = METRIC_DEFS[0].key
    lines = [
        "## Diagnose one company in 2–3 GETs",
        "",
        "1. Find the 6-character stock code (skip if the user gave one: every path below is built from the"
        " code alone)."
        f" GET [api/index.json]({doc.url('api/index.json')}): `companies[]` has `stock_code`, `name`,"
        f" `market`,"
        " `json` (path of the company file) and `page` (path of the company page). Match `name` exactly"
        " first."
        f' For partial names or Korean initial consonants (초성, e.g. "ㅅㅅㅈㅈ"), GET'
        f" [search-index.json]({doc.url('search-index.json')}): `companies[]` has `name`, `code`, `market`,"
        " `sector`, `chosung`. If several companies match, ask the user which one.",
        f"2. GET `api/c/<code>.json`, e.g. [{name} {code}]({doc.url(f'api/c/{code}.json')})."
        " Use `fiscal_year`, `company` (`name`, `market`, `sector_label`, `page`), `metrics[]` (`label`,"
        " `category`, `value_text`, `signal`, `formula`, `threshold_text`, `note`, `peer.median_text`,"
        " `peer.percentile`, `delta.summary`), `red_flags[]` and `disclaimer`."
        f" Field reference: [api-static.md]({API_DOC}).",
        f"3. Optional: GET `m/<metric key>/` (e.g."
        f" [m/{first_metric}/]({doc.url(metric_path(first_metric))}))"
        " for what a metric means and its limits, or read all of them at once in"
        f" [llms-full.txt]({doc.url(LLMS_FULL)}).",
        "",
        "If the company is not in the index, say this site has no checkup for it (not listed, or no annual"
        f" report for fiscal year {site.year} in the data). Do not estimate the numbers.",
        "",
        "Answer shape (fill from the JSON; keep metrics in the JSON order, grouped by `category`):",
        "",
        "```",
        "<name> (<code>) · <fiscal_year> 사업보고서 기준 재무 측정",
        "신호 개수: 🟢 n · 🟡 n · 🔴 n · ⚪ n · ⊘ n · ⚫ n",
        "<category>",
        "- <signal.emoji> <label> <value_text> — 계산식: <formula> · 경계값: <threshold_text>"
        " · 업종 중앙값 <peer.median_text> · <delta.summary>",
        "- ⊘ <label> — <note>",
        "- ⚫ <label> — 데이터 없음 (<note>)",
        "위험 신호: <red_flags[].label>: <detail>  (none → 재무제표로 확인된 위험 신호 없음)",
        "출처: <company page URL> · DART",
        "<disclaimer>",
        "```",
    ]
    return lines


def _compare(doc: _Doc, examples: list[CompanyEntry]) -> list[str]:
    codes = ",".join(e.stock_code for e in examples) or "005930,000660"
    return [
        "## Compare companies",
        "",
        "GET `api/c/<code>.json` for each company and check they share the same `fiscal_year`. Put them"
        " side by"
        " side metric by metric with value, signal, formula and threshold. Do not declare a winner and"
        " do not"
        " order companies by a metric. `peer.percentile` (0–100, higher means better on that metric) is a"
        " position inside the company's own peer group (`peer.scope_label`, `peer.sample_size`);"
        " companies in"
        " different peer groups are measured against different groups. When sectors differ"
        " (`sector_label`),"
        " say that ⊘ metrics are not comparable. For people, the comparison page takes up to 4 codes:"
        f" [compare/?c={codes}]({doc.url('compare/')}?c={codes}).",
    ]


def _signals() -> list[str]:
    lines = ["## Signals", "", "| signal | `signal.key` | meaning | what to say |", "|---|---|---|---|"]
    for s in Signal:
        lines.append(f"| {s.emoji} {s.label} | `{s.value}` | {MEANINGS[s]} | {SIGNAL_GUIDANCE[s]} |")
    lines += [
        "",
        "Thresholds come from real market percentiles (see each metric's basis in llms-full.txt), so 🔴"
        " means"
        ' "in the risk band of this threshold", not a verdict on the company.',
    ]
    return lines


def _files(doc: _Doc, examples: list[CompanyEntry]) -> list[str]:
    code = examples[0].stock_code if examples else "005930"
    keys = " · ".join(f"`{d.key}` {d.label}" for d in METRIC_DEFS)
    return [
        "## Files",
        "",
        f"- [Company index]({doc.url('api/index.json')}): every company with `stock_code`, `name`,"
        f" `market`,"
        " JSON path and page path, sorted by code.",
        f"- [Search index]({doc.url('search-index.json')}): `name`, `code`, `market`, `sector`, `chosung`"
        " (초성) for fuzzy lookup, sorted by name. No metric values.",
        f"- [Company JSON]({doc.url(f'api/c/{code}.json')}): `api/c/<code>.json`, the 17 metrics with"
        f" signal,"
        " formula, thresholds, peer median and last-year change, plus red flags and sources.",
        f"- [Company page]({doc.url(f'c/{code}/')}): `c/<code>/`, the human-readable page to cite as"
        f" the source.",
        f"- [Metric explainers]({doc.url('m/')}): `m/<key>/` for each metric. Keys: {keys}.",
        f"- [How to read]({doc.url('learn/')}): signals, a first read of a financial statement, red flags,"
        " sector notes, glossary.",
        f"- [Build info]({doc.url('build.json')}): build time, data years, counts, git sha.",
        f"- [Risk disclosure feed]({doc.url(MARKET_FEED)}): Atom feed of recent risk-related DART filings;"
        f" per company [`feeds/c/<code>.xml`]({doc.url(company_feed_path(code))}).",
        f"- [JSON field reference]({API_DOC}): every field of the JSON files.",
    ]


def _optional(doc: _Doc, full: bool) -> list[str]:
    lines = ["## Optional", ""]
    if not full:
        lines.append(
            f"- [Full text for agents]({doc.url(LLMS_FULL)}): this file plus every metric explainer,"
            f" the signal"
            " legend and the red-flag rules inline."
        )
    else:
        lines.append(f"- [Short version]({doc.url(LLMS)}): the rules and the recipe without the explainers.")
    lines.append(
        f"- [Source code (MIT)]({REPO}): how the metrics and thresholds are computed (`metrics/engine.py`)."
    )
    return lines


def _metric_block(e: MetricExplainer, doc: _Doc) -> list[str]:
    spec = e.definition
    lines = [
        f"### {e.label} (`{e.key}`) — {e.category.value}",
        "",
        f"- Plain name: {e.easy_name}",
        f"- Definition: {e.one_liner}",
        f"- Why look: {e.why_look}",
        f"- 계산식 (formula): {e.formula} · unit:"
        f" {'금액(통화는 JSON `currency`)' if e.unit == 'money' else e.unit}",
        f"- 경계값 (threshold, 일반 업종): {e.threshold_text()}",
    ]
    for sector in Sector:
        if sector is not Sector.GENERAL and spec.sector_bands and sector in spec.sector_bands:
            lines.append(f"- 경계값 ({sector.label}): {e.threshold_text(sector)}")
    for c in e.sector_caveats:
        lines.append(f"- ⊘ {c.sector.label}: {c.why} 대신 볼 것: {c.instead}")
    for r in e.special_rules:
        lines.append(f"- Special rule → {r.signal.emoji} {r.signal.label}: {r.when}")
    lines.append(f"- Why these thresholds: {e.basis}")
    for p in e.pitfalls:
        lines.append(f"- Limit: {p}")
    lines.append(f"- Page: {doc.url(metric_path(e.key))}")
    return lines


def _metrics_full(doc: _Doc) -> list[str]:
    lines = [
        "## Metrics",
        "",
        "All 17 metrics in the order of the JSON `metrics[]`. Formulas and thresholds are the ones the site"
        " uses to set each signal (`metrics/engine.py`).",
    ]
    for category in Category:
        group = [e for e in explainers() if e.category is category]
        if not group:
            continue
        lines += ["", f"## Metrics · {category.value}"]
        for e in group:
            lines += ["", *_metric_block(e, doc)]
    return lines


def _red_flags_full() -> list[str]:
    lines = [
        "## Red flags",
        "",
        "`red_flags[]` in the company JSON lists conditions found in the financial statements. Report"
        " them as"
        " facts with their `detail`; they are not predictions.",
    ]
    for f in red_flag_explainers():
        lines += [
            "",
            f"### {f.title} (`{f.key}`)",
            "",
            f"- What: {f.what}",
            f"- Why it matters: {f.why_matters}",
            f"- How it is detected: {f.detection}",
        ]
    return lines


def llms_txt(site: Site, full: bool = False) -> str:
    doc = _Doc(site)
    examples = _examples(site)
    sections = [
        _header(site, doc),
        _rules(site, doc),
        _recipe(site, doc, examples),
        _compare(doc, examples),
        _signals(),
        _files(doc, examples),
    ]
    if full:
        sections += [_metrics_full(doc), _red_flags_full()]
    sections.append(_optional(doc, full))
    return "\n\n".join("\n".join(s) for s in sections) + "\n"


@page("llms")
def llms_files(site: Site) -> Iterator[Output]:
    yield Output(LLMS, llms_txt(site))
    yield Output(LLMS_FULL, llms_txt(site, full=True))
