"""MCP 도구가 돌려줄 값 — MCP 를 모른다(테스트·다른 클라이언트가 그대로 쓴다).

원칙 (`docs/REGULATORY.md`):
- 측정값만. 판정은 사이트 JSON(= `metrics.engine`)에 이미 있는 것, '왜 이 색' 문장은 `explain.why_color`.
  여기서 새로 판정하거나 종합 점수·순위를 만들지 않는다. 비교는 정해진 지표 순서로 나란히 놓을 뿐 우열이 없다.
- 없는 값은 None(⚫). 0 으로 채우거나 추정하지 않는다. 사이트에 없는 연도는 '없다' 고 답한다.
- 모든 결과에 면책 문구(`DISCLAIMER`)와 출처 링크.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fin_checkup.clock import KST
from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain import banned, explainers, get_disclosure_explainer, get_explainer, why_color
from fin_checkup.mcp.client import Fetched, NotFound, Source, SourceError
from fin_checkup.metrics import engine
from fin_checkup.metrics.engine import Category, Metric
from fin_checkup.metrics.peers import PeerScope, PeerStat, SelfDelta
from fin_checkup.metrics.sector import Sector
from fin_checkup.metrics.signals import Band, Signal
from fin_checkup.site.hangul import normalize_name

#: 한 번에 견줄 수 있는 회사 수 — 사이트 비교 페이지와 같다.
MAX_COMPARE = 4
SEARCH_LIMIT = 10
#: 신호를 보여 주는 순서. 나쁜 쪽부터 줄 세우지 않는다 — 신호등 순서 그대로.
SIGNAL_ORDER = (
    Signal.GREEN,
    Signal.YELLOW,
    Signal.RED,
    Signal.NEUTRAL,
    Signal.NOT_APPLICABLE,
    Signal.UNKNOWN,
)

SIGNAL_LEGEND = """# fin-checkup 신호 범례 (signal legend)

신호는 "이 수치가 이 경계 구간에 있다" 는 측정 결과입니다. 투자권유·추천이 아닙니다.
A signal only says which threshold band a measured value falls in. It is not investment advice.

| 신호 | 뜻 | Meaning |
|---|---|---|
| 🟢 정상 | 경계값의 정상 구간 | value in the 'normal' band |
| 🟡 주의 | 경계값의 주의 구간 | value in the 'caution' band |
| 🔴 위험 | 경계값의 위험 구간 | value in the 'risk' band |
| ⚪ 측정값 | 업종마다 차이가 커서 경계값 없이 값만 보여 줌 | measured only; no absolute threshold |
| ⊘ 해당 없음 | 이 업종에는 맞지 않는 지표(예: 은행의 유동비율) — note 의 '대신 볼 것' 참고 | not applicable to this sector; see note for what to look at instead |
| ⚫ 데이터 없음 | 공시에서 계산에 필요한 계정을 찾지 못함. 0 이 아니다 | data missing in the filing — never treat as zero |

경계값(thresholds)과 계산식(formula)은 결과의 지표마다 함께 실려 있습니다. 경계값의 근거는
`src/fin_checkup/metrics/engine.py` 주석(실제 시장 분위수)입니다.
"""

POLICY = (
    """# fin-checkup 이 넘지 않는 선 (red lines for agents)

fin-checkup 은 공시 수치를 공개된 재무비율 공식으로 계산한 **측정 도구**입니다. 종목 추천이나 투자 판단은 하지 않습니다.
이 MCP 도구를 쓰는 에이전트도 같은 선을 지켜 주세요.

1. 사거나 팔라는 말, 특정 종목을 띄우거나 깎아내리는 말을 하지 않는다. 사용자가 "사도 돼?" 처럼 판단을 물으면
   판단은 하지 않는다고 말하고, 측정값과 읽는 법만 안내한다.
2. 측정값은 계산식·경계값과 함께 보여 준다. 판단 근거를 감춘 종합 점수·등급·순위·"상위 N" 목록을 만들지 않는다.
3. ⚫ 데이터 없음은 0 도, 나쁨도 아니다 — "공시에서 찾지 못함" 이라고 전한다. ⊘ 해당 없음은 note 의 '대신 볼 것' 과 함께.
4. 결과에 없는 값·연도를 지어내거나 추정해 채우지 않는다.
5. 위험 공시는 "어떤 공시가 떴다는 사실 + DART 원문 링크" 까지만 전한다. 해석·전망을 덧붙이지 않는다.
6. 끝에 면책 문구(disclaimer)를 그대로 붙이고, 회사 페이지·DART 출처 링크를 함께 준다.
7. 재촉하거나 서두르게 만드는 말을 쓰지 않는다.

English: fin-checkup returns measurements only. Never tell the user to buy, sell or hold, never rank companies
or produce a composite score, show formula + thresholds with every value, keep ⚫ (missing) and ⊘ (not applicable)
as they are, never invent numbers, and always end with the disclaimer and source links.

면책: """
    + DISCLAIMER
)

PRESENTATION_GUIDANCE = (
    "이 결과를 사용자에게 보여 줄 때 (how to present this result):\n"
    "1. 사실만 전한다: 회사·사업연도·보고서 기준, 지표마다 이름·값·신호(🟢정상 🟡주의 🔴위험 ⚪측정값 ⊘해당 없음 ⚫데이터 없음)"
    "·계산식(formula)·경계값(threshold_text). 묶음(category) 순서를 지킨다.\n"
    "2. 'why' 문장은 그 색이 된 이유를 그대로 옮긴다. 동종업계 중앙값(peer)·작년 값(last_year)은 있을 때만 말한다.\n"
    "3. ⚫ 은 공시에서 계정을 찾지 못한 것 — 0 이나 나쁨으로 바꾸지 않는다. ⊘ 는 이 업종에 맞지 않는 지표 — note 의 '대신 볼 것' 을 함께.\n"
    "4. 결과에 없는 값·연도를 지어내지 않는다. 종합 점수·등급·순위를 만들지 않는다.\n"
    "5. 사고팔라는 말이나 종목 평가를 하지 않는다. 종목 추천이나 투자 판단은 하지 않습니다. "
    "사용자가 판단을 물으면 판단은 하지 않는다고 말하고 측정값과 읽는 법(explain_metric)만 안내한다.\n"
    "6. 위험 신호(red_flags)·최근 위험 공시는 사실과 DART 원문 링크만. 해석·전망을 덧붙이지 않는다.\n"
    "7. 끝에 disclaimer 를 그대로, 그리고 page_url 과 DART 출처 링크를 붙인다.\n"
    "English: report the measured facts with formula and thresholds, keep ⚫/⊘ as-is, never invent values, "
    "never advise buying or selling or rank companies, and end with the disclaimer and the source links."
)

COMPARE_GUIDANCE = (
    "회사 순서는 입력한 순서이며 우열이 아닙니다. 지표마다 값·신호·경계값을 나란히 전하고, 어느 회사가 낫다고 "
    "말하지 않는다. 업종이 다르면 경계값·⊘ 가 다를 수 있다 — 회사별 threshold_text 를 함께. "
    "끝에 disclaimer 와 회사별 page_url. "
    "English: columns follow the input order, not a ranking; present side by side and never name a winner."
)


class ToolInputError(ValueError):
    """도구 입력이 잘못됐다. 메시지는 사용자·모델에게 그대로 보여 줄 문장."""


# ── 회사 찾기 ─────────────────────────────────────────────────────


def _rank(item: dict[str, Any], q: str) -> int:
    """`static/search.js` 의 rank 와 같은 규칙 — 얼마나 맞았나만 본다. 지표로 줄 세우지 않는다."""
    name = normalize_name(str(item.get("name", "")))
    code = str(item.get("code", "")).lower()
    cho = str(item.get("chosung") or "")
    if name == q or code == q:
        return 0
    if name.startswith(q) or code.startswith(q):
        return 1
    if q in name:
        return 2
    if cho.startswith(q):
        return 3
    if q in cho:
        return 4
    return -1


def _search(items: list[dict[str, Any]], query: str, limit: int) -> list[tuple[int, dict[str, Any]]]:
    q = normalize_name(query or "")
    if not q:
        return []
    hits = [(r, item) for item in items if (r := _rank(item, q)) >= 0]
    hits.sort(key=lambda h: (h[0], str(h[1].get("name", ""))))
    return hits[:limit]


def _company_brief(source: Source, item: dict[str, Any]) -> dict[str, Any]:
    code = str(item.get("code", ""))
    return {
        "name": item.get("name"),
        "stock_code": code,
        "market": item.get("market"),
        "sector": item.get("sector"),
        "page_url": f"{source.base_url}c/{code}/",
    }


async def _index_items(source: Source) -> list[dict[str, Any]]:
    fetched = await source.search_index()
    companies = fetched.data.get("companies") if isinstance(fetched.data, dict) else None
    if not isinstance(companies, list):
        raise SourceError("검색 인덱스 형식이 예상과 다릅니다 (companies 가 없음).")
    return [c for c in companies if isinstance(c, dict) and c.get("code")]


async def _refresh(source: Source) -> bool:
    """출처가 색인을 넓힐 수 있으면(개인 DART 키 — 사이트에 없는 회사) 넓힌다."""
    refresh = getattr(source, "refresh_companies", None)
    return bool(refresh is not None and await refresh())


async def search_companies(source: Source, query: str, limit: int = SEARCH_LIMIT) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        raise ToolInputError(
            "검색어가 비어 있습니다. 회사 이름·6자리 종목코드·초성(예: ㅅㅅㅈㅈ)을 넣어 주세요."
        )
    limit = max(1, min(int(limit), 50))
    items = await _index_items(source)
    hits = _search(items, query, limit)
    if not hits and await _refresh(source):
        hits = _search(await _index_items(source), query, limit)
    return {
        "query": query,
        "count": len(hits),
        "results": [_company_brief(source, item) for _, item in hits],
        "note": "결과 순서는 '얼마나 맞았나 → 이름순' 입니다. 지표로 줄 세운 것이 아닙니다."
        if hits
        else "맞는 상장사가 없습니다. 다른 표기(예: 영문 이름·종목코드·초성)로 다시 찾아 보세요.",
        "disclaimer": DISCLAIMER,
    }


async def resolve_company(source: Source, query: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """(정확히 한 회사, 후보들). 이름이 정확히 같거나 종목코드가 같거나 후보가 하나뿐이면 그 회사."""
    found, candidates = await _resolve(source, query)
    if found is None and not candidates and await _refresh(source):
        found, candidates = await _resolve(source, query)
    return found, candidates


async def _resolve(source: Source, query: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    items = await _index_items(source)
    q = normalize_name(query or "")
    if not q:
        return None, []
    exact = [i for i in items if normalize_name(str(i.get("name", ""))) == q or str(i.get("code")) == q]
    if len(exact) == 1:
        return exact[0], []
    hits = [item for _, item in _search(items, query, SEARCH_LIMIT)]
    if len(hits) == 1 and not exact:
        return hits[0], []
    return None, exact or hits


def _unresolved(source: Source, query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    status = "ambiguous" if candidates else "not_found"
    message = (
        f"'{query}' 에 맞는 회사가 여럿입니다. candidates 중 하나의 종목코드(stock_code)로 다시 불러 주세요."
        if candidates
        else f"'{query}' 에 맞는 상장사를 찾지 못했습니다. search_companies 로 표기를 확인해 주세요."
    )
    return {
        "status": status,
        "query": query,
        "message": message,
        "candidates": [_company_brief(source, c) for c in candidates],
        "disclaimer": DISCLAIMER,
    }


# ── 진단 ─────────────────────────────────────────────────────────


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _metric_from_json(raw: dict[str, Any]) -> Metric:
    """사이트 JSON 의 지표 한 칸 → `Metric` (판정은 다시 하지 않는다 — signal 그대로)."""
    thresholds = raw.get("thresholds")
    band = (
        Band(
            good=float(thresholds["good"]),
            warn=float(thresholds["warn"]),
            higher_is_better=bool(thresholds.get("higher_is_better", True)),
        )
        if isinstance(thresholds, dict)
        else None
    )
    signal = Signal(raw["signal"]["key"])
    return Metric(
        key=raw["key"],
        label=raw["label"],
        category=Category(raw["category"]),
        value=_finite(raw.get("value")),
        unit=raw.get("unit", ""),
        signal=signal,
        description="",
        formula=raw.get("formula", ""),
        note=raw.get("note") or "",
        band=band,
        # 경계값이 있는데 ⚪ 면 엔진이 대체 계정(금융원가 등)이라 판정하지 않은 것이다 (engine 의 is_proxy).
        proxy=signal is Signal.NEUTRAL and band is not None,
    )


def _peer_from_json(raw: dict[str, Any], metric: Metric, currency: str) -> PeerStat | None:
    peer = raw.get("peer")
    if not isinstance(peer, dict) or _finite(peer.get("median")) is None:
        return None
    hib = metric.band.higher_is_better if metric.band else None  # 경계값 없는 지표는 방향을 말하지 않는다
    return PeerStat(
        metric_key=metric.key,
        median=float(peer["median"]),
        sample_size=int(peer.get("sample_size") or 0),
        scope=PeerScope(peer.get("scope", "industry")),
        percentile=_finite(peer.get("percentile")),
        higher_is_better=hib,
        unit=metric.unit,
        currency=currency,
    )


def _delta_from_json(raw: dict[str, Any], metric: Metric, currency: str) -> SelfDelta | None:
    delta = raw.get("delta")
    if not isinstance(delta, dict):
        return None
    previous, current = _finite(delta.get("previous")), _finite(delta.get("current"))
    if previous is None or current is None:
        return None
    hib = metric.band.higher_is_better if metric.band else None  # 경계값 없는 지표는 방향을 말하지 않는다
    return SelfDelta(metric.key, previous, current, hib, metric.unit, currency)


def _why(raw: dict[str, Any], sector: Sector | None, currency: str) -> str | None:
    """'왜 이 색' 한 문장 — 사이트 회사 페이지·카드와 같은 함수(`why_color`)."""
    try:
        metric = _metric_from_json(raw)
        return why_color(
            metric,
            _peer_from_json(raw, metric, currency),
            _delta_from_json(raw, metric, currency),
            sector=sector,
            currency=currency,
        )
    except (KeyError, ValueError, TypeError):
        # 스키마가 바뀌어 문장을 못 만들면 비워 둔다 — 지어내지 않는다.
        return None


def _signal_text(signal: dict[str, Any]) -> str:
    return f"{signal.get('emoji', '')} {signal.get('label', '')}".strip()


def _metric_view(source: Source, raw: dict[str, Any], sector: Sector | None, currency: str) -> dict[str, Any]:
    peer = raw.get("peer") if isinstance(raw.get("peer"), dict) else None
    delta = raw.get("delta") if isinstance(raw.get("delta"), dict) else None
    signal = raw.get("signal") or {}
    return {
        "key": raw.get("key"),
        "label": raw.get("label"),
        "category": raw.get("category"),
        "signal": _signal_text(signal),
        "signal_key": signal.get("key"),
        "value": _finite(raw.get("value")),
        "unit": raw.get("unit"),
        "value_text": raw.get("value_text"),
        "formula": raw.get("formula"),
        "thresholds": raw.get("thresholds"),
        "threshold_text": raw.get("threshold_text"),
        "peer": None
        if peer is None
        else {
            "scope": peer.get("scope_label") or peer.get("scope"),
            "sample_size": peer.get("sample_size"),
            "median_text": peer.get("median_text"),
            "summary": peer.get("summary"),
        },
        "peer_median": peer.get("median_text") if peer else None,
        "last_year": None
        if delta is None
        else {"previous": _finite(delta.get("previous")), "summary": delta.get("summary")},
        "note": raw.get("note") or "",
        "why": _why(raw, sector, currency),
        "explain_url": f"{source.base_url}m/{raw.get('key')}/",
    }


def _counts(metrics: list[dict[str, Any]]) -> dict[str, int]:
    counts = {f"{s.emoji} {s.label}": 0 for s in SIGNAL_ORDER}
    for m in metrics:
        key = m.get("signal_key")
        try:
            s = Signal(key)
        except ValueError:
            continue
        counts[f"{s.emoji} {s.label}"] += 1
    return counts


def _summary(doc: dict[str, Any], metrics: list[dict[str, Any]], disclosures: list[dict] | None) -> list[str]:
    company = doc.get("company") or {}
    report = doc.get("report") or {}
    fs = {"CFS": "연결", "OFS": "개별"}.get(report.get("fs_div"), report.get("fs_div") or "")
    facts = [
        f"{company.get('name')}({company.get('stock_code')}, {company.get('market') or '시장 정보 없음'}) "
        f"{doc.get('fiscal_year')} 사업연도 {report.get('label') or ''}({fs}재무제표) 기준, 지표 {len(metrics)}개.",
        f"업종 구분: {company.get('sector_label') or '알 수 없음'}"
        + (f" (KSIC {company['industry_code']})" if company.get("industry_code") else "")
        + ".",
        "신호 수: " + " · ".join(f"{k} {v}" for k, v in _counts(metrics).items()) + ".",
    ]
    for s in (Signal.RED, Signal.YELLOW, Signal.NOT_APPLICABLE, Signal.UNKNOWN):
        names = [m["label"] for m in metrics if m.get("signal_key") == s.value]
        if names:
            facts.append(f"{s.emoji} {s.label}: {', '.join(names)}.")
    flags = doc.get("red_flags") or []
    facts.append(
        "재무제표로 확인된 위험 신호: " + (", ".join(f["label"] for f in flags) if flags else "없음") + "."
    )
    if disclosures is not None:
        facts.append(f"공시 목록 기간 안의 이 회사 위험 공시: {len(disclosures)}건.")
    return facts


def _disclosure_view(source: Source, item: Any) -> dict[str, Any]:
    try:
        what = get_disclosure_explainer(item.kind).what
    except KeyError:
        what = None
    return {
        "date": item.rcept_dt.isoformat(),
        "company": item.corp_name,
        "stock_code": item.stock_code,
        "title": item.report_nm,
        "category": item.kind.value,
        "category_label": item.label,
        "is_correction": item.is_correction,
        "what_this_type_is": what,
        "dart_url": item.url,
        "type_explainer_url": f"{source.base_url}{item.explainer_path}",
    }


async def _feed(source: Source) -> tuple[Any | None, str | None, Fetched | None]:
    """(검증한 공시 피드, 못 읽었을 때 사유, 원본)."""
    from fin_checkup.site.disclosures import DisclosureFormatError, feed_from_payload

    try:
        fetched = await source.disclosures()
    except NotFound as exc:
        return None, str(exc), None
    except SourceError as exc:
        return None, str(exc), None
    try:
        return feed_from_payload(fetched.data), None, fetched
    except DisclosureFormatError as exc:
        return None, f"공시 목록 형식을 읽지 못했습니다: {exc}", fetched


def _stamp(fetched: Fetched) -> str:
    return datetime.fromtimestamp(fetched.fetched_at, KST).isoformat(timespec="seconds")


def _freshness(fetched: Fetched) -> dict[str, Any]:
    out: dict[str, Any] = {"fetched_at": _stamp(fetched), "stale": fetched.stale}
    if fetched.stale:
        out["note"] = "출처에 새로 연결하지 못해 예전에 받은 사본입니다. 최신 값과 다를 수 있습니다."
    return out


async def diagnose_company(source: Source, company: str, year: int | None = None) -> dict[str, Any]:
    company = (company or "").strip()
    if not company:
        raise ToolInputError("회사 이름 또는 6자리 종목코드를 넣어 주세요.")
    found, candidates = await resolve_company(source, company)
    if found is None:
        return _unresolved(source, company, candidates)
    code = str(found["code"])

    try:
        fetched = await source.company(code, year)
    except NotFound:
        return {
            "status": "no_data",
            "query": company,
            "company": _company_brief(source, found),
            "message": f"{found.get('name')}({code}) 의 "
            + (f"{year} 사업연도 " if year else "")
            + "진단 데이터가 출처에 없습니다. 값을 추정해 채우지 않습니다.",
            "disclaimer": DISCLAIMER,
        }
    doc = fetched.data
    fiscal_year = doc.get("fiscal_year")
    if year is not None and fiscal_year != year:
        return {
            "status": "year_unavailable",
            "query": company,
            "company": _company_brief(source, found),
            "requested_year": year,
            "available_fiscal_year": fiscal_year,
            "message": f"공개 사이트는 사업연도 {fiscal_year} 하나만 싣습니다. {year} 사업연도 값은 여기서 줄 수 없습니다 "
            "(작년 값은 지표마다 last_year 에 있습니다). 사용자가 개인 DART 인증키를 넣으면(직접 `fin-checkup setup` 실행, "
            "setup_guide 도구 참고) 다른 사업연도를 DART 에서 받아 옵니다. 로컬 모드(FIN_CHECKUP_MCP_SOURCE=local)도 있습니다.",
            "disclaimer": DISCLAIMER,
        }

    info = doc.get("company") or {}
    currency = doc.get("currency") or "KRW"
    try:
        sector: Sector | None = Sector(info.get("sector"))
    except ValueError:
        sector = None
    metrics = [_metric_view(source, m, sector, currency) for m in doc.get("metrics") or []]

    feed, feed_problem, _ = await _feed(source)
    company_disclosures = None
    if feed is not None:
        company_disclosures = [_disclosure_view(source, i) for i in feed.for_company(code)]

    live = doc.get("source") == "dart-live"
    # DART 에서 받은 회사가 사이트에 없으면 사이트 링크를 내지 않는다 (없는 주소를 출처로 주지 않게).
    on_site = found.get("on_site", True) is not False
    page = info.get("page") or f"c/{code}/"
    site_sources = (
        [
            {"name": "fin-checkup 회사 페이지", "url": f"{source.base_url}{page}"},
            {"name": "fin-checkup 회사 JSON", "url": f"{source.base_url}api/c/{code}.json"},
        ]
        if on_site and not live
        else [{"name": "fin-checkup 회사 페이지 (최신 사업연도)", "url": f"{source.base_url}{page}"}]
        if on_site
        else []
    )
    result: dict[str, Any] = {
        "status": "ok",
        "company": {
            "name": info.get("name"),
            "stock_code": info.get("stock_code") or code,
            "market": info.get("market"),
            "sector": info.get("sector"),
            "sector_label": info.get("sector_label"),
            "industry_code": info.get("industry_code"),
            "page_url": f"{source.base_url}{page}" if on_site else None,
        },
        "fiscal_year": fiscal_year,
        "report": doc.get("report"),
        "currency": currency,
        "years_in_data": doc.get("years"),
        "built_on": doc.get("built_on"),
        "summary": _summary(doc, metrics, company_disclosures),
        "metrics": metrics,
        "red_flags": doc.get("red_flags") or [],
        "recent_risk_disclosures": company_disclosures,
        "sources": [*site_sources, *(doc.get("sources") or [])],
        "data_freshness": _freshness(fetched),
        "source_mode": source.mode,
        "source": "dart-live" if live else source.mode.split("+")[0],
        "presentation_guidance": PRESENTATION_GUIDANCE,
        "disclaimer": doc.get("disclaimer") or DISCLAIMER,
    }
    if live:
        result["live_note"] = doc.get("live_note")
    # 사업보고서 뒤 최근 분기 주요계정 — 사이트 JSON 에 있을 때만 (판정 없음, 없는 값은 null 그대로).
    if doc.get("latest_quarter"):
        result["latest_quarter"] = doc["latest_quarter"]
    if feed is not None:
        result["disclosure_window"] = {"begin": feed.begin.isoformat(), "end": feed.end.isoformat()}
    elif feed_problem:
        result["disclosures_note"] = f"최근 위험 공시 목록을 불러오지 못했습니다: {feed_problem}"
    # 다른 레인이 사이트 JSON 에 더하는 요약·안내가 있으면 그대로 넘긴다 (없으면 없음).
    for extra in ("summary_ko", "agent_guidance"):
        if doc.get(extra):
            result[f"site_{extra}"] = doc[extra]
    return result


# ── 비교 ─────────────────────────────────────────────────────────


async def compare_companies(source: Source, companies: list[str]) -> dict[str, Any]:
    queries = [c.strip() for c in companies or [] if c and c.strip()]
    if len(queries) < 2:
        raise ToolInputError("비교할 회사를 2곳 이상 넣어 주세요 (최대 4곳).")
    if len(queries) > MAX_COMPARE:
        raise ToolInputError(
            f"한 번에 최대 {MAX_COMPARE}곳까지 견줄 수 있습니다. {len(queries)}곳을 받았습니다."
        )

    columns: list[dict[str, Any]] = []
    docs: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for q in queries:
        found, candidates = await resolve_company(source, q)
        if found is None:
            unresolved.append(_unresolved(source, q, candidates))
            continue
        code = str(found["code"])
        if code in seen:
            continue
        seen.add(code)
        try:
            fetched = await source.company(code)
        except NotFound:
            unresolved.append(
                {"status": "no_data", "query": q, "company": _company_brief(source, found), "candidates": []}
            )
            continue
        doc = fetched.data
        info = doc.get("company") or {}
        columns.append(
            {
                "name": info.get("name"),
                "stock_code": code,
                "sector_label": info.get("sector_label"),
                "fiscal_year": doc.get("fiscal_year"),
                "report": doc.get("report"),
                "page_url": f"{source.base_url}{info.get('page') or f'c/{code}/'}",
                "red_flags": [f.get("label") for f in doc.get("red_flags") or []],
            }
        )
        docs.append(doc)

    rows = []
    for spec in engine.METRIC_DEFS:
        values = []
        for doc in docs:
            raw = next((m for m in doc.get("metrics") or [] if m.get("key") == spec.key), None)
            if raw is None:
                values.append({"signal": f"{Signal.UNKNOWN.emoji} {Signal.UNKNOWN.label}", "value": None})
                continue
            values.append(
                {
                    "signal": _signal_text(raw.get("signal") or {}),
                    "value": _finite(raw.get("value")),
                    "value_text": raw.get("value_text"),
                    "threshold_text": raw.get("threshold_text"),
                    "note": raw.get("note") or "",
                }
            )
        rows.append(
            {
                "key": spec.key,
                "label": spec.label,
                "category": spec.category.value,
                "formula": spec.formula,
                "values": values,
                "explain_url": f"{source.base_url}m/{spec.key}/",
            }
        )
    return {
        "status": "ok" if len(columns) >= 2 else "insufficient",
        "companies": columns,
        "metrics": rows,
        "unresolved": unresolved,
        "note": "values[i] 는 companies[i] 의 값입니다. 지표 순서는 엔진 정의 순서로 고정, 회사 순서는 입력 순서 — 우열이 아닙니다.",
        "presentation_guidance": COMPARE_GUIDANCE,
        "disclaimer": DISCLAIMER,
    }


# ── 지표 해설 ─────────────────────────────────────────────────────


def explain_metric(source: Source, key_or_label: str) -> dict[str, Any]:
    q = normalize_name(key_or_label or "")
    if not q:
        raise ToolInputError("지표 키(예: debt_ratio) 또는 이름(예: 부채비율)을 넣어 주세요.")
    every = explainers()

    def names(e: Any) -> list[str]:
        return [normalize_name(x) for x in (e.key, e.label, e.easy_name)]

    match = next((e for e in every if q in names(e)), None)
    if match is None:
        partial = [e for e in every if any(q in n or (len(n) > 1 and n in q) for n in names(e))]
        if len(partial) == 1:
            match = partial[0]
    if match is None:
        return {
            "status": "not_found",
            "query": key_or_label,
            "message": "그런 지표가 없습니다. 아래 17개 중에서 골라 주세요.",
            "available": [{"key": e.key, "label": e.label, "category": e.category.value} for e in every],
            "disclaimer": DISCLAIMER,
        }
    e = get_explainer(match.key)
    return {
        "status": "ok",
        "key": e.key,
        "label": e.label,
        "easy_name": e.easy_name,
        "category": e.category.value,
        "unit": e.unit,
        "formula": e.formula,
        "one_liner": e.one_liner,
        "why_look": e.why_look,
        "threshold_basis": e.basis,
        "pitfalls": list(e.pitfalls),
        "thresholds_by_sector": {s.label: e.threshold_text(s) for s in Sector},
        "not_applicable_in": [
            {"sector": c.sector.label, "why": c.why, "instead": c.instead} for c in e.sector_caveats
        ],
        "special_rules": [
            {"when": r.when, "signal": f"{r.signal.emoji} {r.signal.label}"} for r in e.special_rules
        ],
        "page_url": f"{source.base_url}m/{e.key}/",
        "disclaimer": DISCLAIMER,
    }


# ── 최근 위험 공시 ────────────────────────────────────────────────


async def recent_risk_disclosures(
    source: Source, company: str | None = None, days: int = 7, limit: int = 30
) -> dict[str, Any]:
    feed, problem, fetched = await _feed(source)
    if feed is None:
        raise SourceError(problem or "최근 위험 공시 목록을 불러오지 못했습니다.")
    window = (feed.end - feed.begin).days + 1
    days = max(1, min(int(days), window))
    limit = max(1, min(int(limit), 100))
    since = feed.end - timedelta(days=days - 1)

    target = None
    if company and company.strip():
        found, candidates = await resolve_company(source, company)
        if found is None:
            return _unresolved(source, company.strip(), candidates)
        target = _company_brief(source, found)
        items = feed.for_company(target["stock_code"])
        scope = f"{target['name']}({target['stock_code']}) 의 위험 공시"
    else:
        items = feed.market_wide()
        scope = (
            "시장 전체로 올리는 유형의 위험 공시 (같은 사건은 하나로 묶음 — 사이트 첫 화면·알림과 같은 기준)"
        )
    items = [i for i in items if i.rcept_dt >= since]
    return {
        "status": "ok",
        "scope": scope,
        "company": target,
        "window": {"begin": since.isoformat(), "end": feed.end.isoformat(), "days": days, "max_days": window},
        "count": len(items),
        "shown": min(len(items), limit),
        "items": [_disclosure_view(source, i) for i in items[:limit]],
        "truncated_types": list(feed.truncated_types),
        "note": "공시 제목으로 분류한 사실 목록입니다(최근 것부터). 해석·전망이 아니며, 원문은 dart_url 에서 확인하세요."
        + (
            " 수집 상한에 걸려 빠졌을 수 있는 공시유형이 있습니다(truncated_types)." if feed.truncated else ""
        ),
        "data_freshness": _freshness(fetched) if fetched else None,
        "disclaimer": DISCLAIMER,
    }


# ── 데이터 상태 ───────────────────────────────────────────────────


async def data_status(source: Source) -> dict[str, Any]:
    fetched = await source.build_info()
    info = fetched.data if isinstance(fetched.data, dict) else {}
    keep = (
        "built_at",
        "built_on",
        "fiscal_year",
        "fiscal_years_in_data",
        "expected_fiscal_year",
        "fresh",
        "disclosures",
        "counts",
        "git_sha",
        "db_path",
    )
    status = {k: info[k] for k in keep if k in info}
    return {
        "status": "ok",
        "source_mode": source.mode,
        "base_url": source.base_url,
        **status,
        "note": "사이트는 회사마다 최신 사업연도 하나(fiscal_year)를 싣고, 성장성·작년 대비는 그 전 해와 견줍니다. "
        "fresh 가 false 면 기대한 사업연도(expected_fiscal_year)가 아직 반영되지 않은 것입니다.",
        "data_freshness": _freshness(fetched),
        "disclaimer": DISCLAIMER,
    }


# ── 설치 상태 ─────────────────────────────────────────────────────


async def setup_status(source: Source, check_key: bool = False) -> dict[str, Any]:
    """무엇이 설치·설정됐나. 인증키 값은 절대 싣지 않는다 — 있다/없다, 어디서 오나(파일 경로·환경변수)만."""
    import sys
    from importlib.metadata import PackageNotFoundError, version

    from fin_checkup import onboarding
    from fin_checkup.config import Settings

    settings = getattr(source, "settings", None) or Settings()
    try:
        package_version = version("fin-checkup")
    except PackageNotFoundError:  # pragma: no cover
        package_version = "0"

    site: dict[str, Any]
    try:
        fetched = await source.build_info()
        info = fetched.data if isinstance(fetched.data, dict) else {}
        site = {
            "reachable": not fetched.stale,
            "stale_copy": fetched.stale,
            "fiscal_year": info.get("fiscal_year"),
            "built_on": info.get("built_on"),
        }
    except SourceError as exc:
        site = {"reachable": False, "detail": str(exc)}

    key: dict[str, Any] = {
        "configured": bool(settings.has_api_key),
        "from": onboarding.key_source() if settings.has_api_key else None,
        "checked": None,
    }
    if check_key and settings.has_api_key:
        check = await onboarding.validate_key(settings.dart_api_key)
        key["checked"] = check.status.value
        key["check_message"] = check.message

    live = source.mode in {"dart", "site+dart"}
    cache: dict[str, Any] | None = None
    if hasattr(source, "cache_years"):
        db_path = getattr(source, "db_path", None)
        cache = {"path": str(db_path), "exists": bool(db_path and Path(db_path).is_file()), "fiscal_years": source.cache_years()}
    elif source.mode == "local":
        db_path = getattr(source, "db_path", None)
        cache = {"path": str(db_path), "exists": bool(db_path and Path(db_path).is_file())}

    next_steps: list[str] = []
    if not site["reachable"]:
        next_steps.append("공개 사이트에 연결하지 못했습니다 — 네트워크·프록시를 확인합니다.")
    if not settings.has_api_key:
        next_steps.append(
            "키 없이도 모든 상장사의 최신 사업연도 진단이 됩니다. 다른 사업연도·사이트에 없는 회사가 필요하면 setup_guide(want_key=true) 를 "
            "따라 사용자가 직접 키를 넣습니다 — 키를 채팅으로 받지 않습니다."
        )
    elif not live:
        next_steps.append("키는 있지만 FIN_CHECKUP_MCP_SOURCE 가 site/local 이라 DART 에서 받지 않습니다. 비우면 사이트에 없는 연도를 받아 옵니다.")
    elif key["checked"] in {"invalid", "malformed", "ip"}:
        next_steps.append("키가 통하지 않습니다. 사용자가 직접 `fin-checkup setup` 을 다시 실행해 키를 바꿉니다.")
    return {
        "status": "ok",
        "fin_checkup_version": package_version,
        "python": ".".join(map(str, sys.version_info[:3])),
        "source_mode": source.mode,
        "base_url": source.base_url,
        "site": site,
        "dart_key": key,
        "live_fetch": live and bool(settings.has_api_key),
        "cache": cache,
        "sec_user_agent_configured": bool(settings.has_sec_user_agent),
        "user_config_file": str(onboarding.user_env_file()),
        "next_steps": next_steps,
        "note": "인증키 값은 이 결과에 들어 있지 않습니다. 키를 보여 달라거나 채팅에 붙여 넣으라고 하지 마세요.",
        "disclaimer": DISCLAIMER,
    }


def banned_in(obj: Any, proper_nouns: list[str] | tuple[str, ...] = ()) -> list[str]:
    """결과 안 문자열에서 금지 표현 (회사 이름·공시 제목 같은 데이터는 걷어낸다). 테스트·자체 점검용."""
    found: list[str] = []
    for text in banned.iter_strings(obj):
        found.extend(banned.find_outside_allowed(text, proper_nouns))
    return found
