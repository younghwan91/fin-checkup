"""최근 분기 주요계정 — 사업보고서(연간) 사이에 나온 분기·반기보고서의 숫자.

신호등은 사업보고서로만 매긴다. 이 모듈은 그 사이 몇 달을 비워 두지 않으려고, 가장 최근 분기의
**주요계정 몇 개(매출·영업이익·순이익·자산·부채·자본)를 그대로** 보여 준다. 판정·점수는 없다.

데이터는 DART 다중회사 주요계정(`fnlttMultiAcnt.json`, 호출 한 번에 회사 100곳)에서 온다 — 상장사
~2,800곳의 한 분기를 ~28회로 받는다. 단일회사 전체 재무제표(`fnlttSinglAcntAll`)로 받으면 회사마다
1~2회라 분기마다 수천 건이 든다.

**함정 — 손익 `thstrm_amount` 는 그 분기 3개월 값이다.** 반기·3분기보고서의 `thstrm_dt` 는
'2026.01.01 ~ 2026.09.30' 처럼 누적 기간을 적지만, 다중회사 API 의 금액은 7~9월 석 달 값이다
(누적은 단일회사 API 의 `thstrm_add_amount` 에만 있다; 2026-11-26 삼성전자 2026 3분기로 대조).
그래서 기간 이름은 `thstrm_dt` 문자열이 아니라 재무상태표 기준일로부터 석 달을 거꾸로 세어 만든다.
`frmtrm_amount` 는 손익이면 1년 전 같은 분기, 재무상태표면 직전 사업연도 말이다.

못 찾은 계정은 0 이 아니라 None(⚫)이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from fin_checkup.format import format_money
from fin_checkup.metrics.signals import Signal
from fin_checkup.models import ReportCode

#: 분기보고서 차례. 같은 사업연도 안에서 뒤가 최근이다.
INTERIM_ORDER = (ReportCode.Q1, ReportCode.HALF, ReportCode.Q3)
#: 12월 결산 기준 각 분기가 끝나는 (월, 일). 수집 대상을 고를 때만 쓴다 — 화면의 기간은 공시 날짜로 만든다.
_PERIOD_END = {ReportCode.Q1: (3, 31), ReportCode.HALF: (6, 30), ReportCode.Q3: (9, 30)}

#: 항목 키 → (표시 이름, 재무제표 구분, DART 계정명 후보). 후보는 앞에서부터 찾는다.
ACCOUNTS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "revenue": ("매출액", "IS", ("매출액", "수익(매출액)", "영업수익", "매출")),
    "operating_income": ("영업이익", "IS", ("영업이익", "영업이익(손실)")),
    "net_income": ("순이익", "IS", ("당기순이익(손실)", "당기순이익", "분기순이익", "반기순이익")),
    "total_assets": ("자산총계", "BS", ("자산총계",)),
    "total_liabilities": ("부채총계", "BS", ("부채총계",)),
    "total_equity": ("자본총계", "BS", ("자본총계",)),
}
FLOW_KEYS = ("revenue", "operating_income", "net_income")
STOCK_KEYS = ("total_assets", "total_liabilities", "total_equity")

_DATE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")


@dataclass(frozen=True)
class KeyAccountRow:
    """`fnlttMultiAcnt` 의 한 줄 (캐시 `key_accounts` 와 같은 모양)."""

    corp_code: str
    bsns_year: int
    reprt_code: str
    fs_div: str
    sj_div: str
    account_nm: str
    ord: int
    thstrm_dt: str
    thstrm_amount: float | None
    frmtrm_dt: str
    frmtrm_amount: float | None
    currency: str
    rcept_no: str


def interim_targets(today: date, count: int = 2) -> list[tuple[int, ReportCode]]:
    """수집할 분기보고서 (사업연도, 보고서), 최근 것부터.

    분기가 끝난 것만 — 아직 안 끝난 분기는 물어도 비어 온다.
    """
    periods = [
        (year, code)
        for year in (today.year - 1, today.year)
        for code in INTERIM_ORDER
        if date(year, *_PERIOD_END[code]) < today
    ]
    return list(reversed(periods))[:count]


def _parse_date(text: str) -> date | None:
    found = _DATE.findall(text or "")
    if not found:
        return None
    y, m, d = found[-1]  # '2026.01.01 ~ 2026.09.30' 이면 끝 날짜
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def _quarter_label(end: date) -> str:
    """기준일로 끝나는 석 달. 2026-09-30 → '2026년 7~9월', 2026-02-28 → '2025년 12월~2026년 2월'."""
    start_month = end.month - 2
    if start_month >= 1:
        return f"{end.year}년 {start_month}~{end.month}월"
    return f"{end.year - 1}년 {start_month + 12}월~{end.year}년 {end.month}월"


def _shift_year(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 2월 29일
        return day.replace(year=day.year + years, day=28)


@dataclass(frozen=True)
class QuarterLine:
    key: str
    label: str
    current: float | None
    prior: float | None
    currency: str

    @property
    def current_text(self) -> str:
        return format_money(self.current, self.currency)

    @property
    def prior_text(self) -> str:
        return format_money(self.prior, self.currency)

    @property
    def change(self) -> str:
        """1년 전 같은 분기(재무상태는 직전 사업연도 말) 대비. 판정 없이 퍼센트나 '흑자 전환' 같은 사실만."""
        now, before = self.current, self.prior
        if now is None or before is None:
            return ""
        if self.key in ("operating_income", "net_income"):
            if before <= 0 < now:
                return "흑자 전환"
            if now <= 0 < before:
                return "적자 전환"
            if now <= 0 and before <= 0:
                return "적자 지속" if now < 0 and before < 0 else ""
        if before <= 0:
            return ""
        pct = (now - before) / before * 100
        return f"{pct:+,.1f}%"


@dataclass(frozen=True)
class LatestQuarter:
    """회사 하나의 가장 최근 분기 주요계정."""

    corp_code: str
    bsns_year: int
    reprt_code: ReportCode
    fs_div: str
    rcept_no: str
    #: 재무상태표 기준일 (분기 말).
    period_end: date
    #: 직전 사업연도 말 (재무상태표 비교 기준). 공시에 없으면 None.
    prior_year_end: date | None
    currency: str
    flows: tuple[QuarterLine, ...]
    stocks: tuple[QuarterLine, ...]

    @property
    def report_label(self) -> str:
        return f"{self.bsns_year}년 {self.reprt_code.label}"

    @property
    def statement_label(self) -> str:
        return "연결재무제표" if self.fs_div == "CFS" else "개별재무제표"

    @property
    def period_label(self) -> str:
        return _quarter_label(self.period_end)

    @property
    def prior_period_label(self) -> str:
        return _quarter_label(_shift_year(self.period_end, -1))

    @property
    def prior_year_end_label(self) -> str:
        return self.prior_year_end.strftime("%Y.%m.%d") if self.prior_year_end else "직전 사업연도 말"

    @property
    def dart_url(self) -> str:
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.rcept_no}" if self.rcept_no else ""

    def line(self, key: str) -> QuarterLine:
        return next(ln for ln in (*self.flows, *self.stocks) if ln.key == key)

    def debt_ratio(self, which: str = "current") -> float | None:
        """부채총계 ÷ 자본총계 × 100. 자본이 0 이하(완전잠식)면 뜻이 없어 None."""
        debt = getattr(self.line("total_liabilities"), which)
        equity = getattr(self.line("total_equity"), which)
        if debt is None or equity is None or equity <= 0:
            return None
        return debt / equity * 100

    @property
    def has_any(self) -> bool:
        return any(ln.current is not None for ln in (*self.flows, *self.stocks))


def debt_ratio_not_applicable(result: Any) -> str | None:
    """사업보고서 판정에서 부채비율이 ⊘(이 업종엔 뜻이 없음)이면 그 사유.

    분기 부채비율도 같은 이유로 내지 않는다.

    은행·금융지주는 예금이 부채라 부채비율이 1,000% 를 넘는 게 보통이다 — 연간엔 ⊘ 인데 분기 표에만
    숫자가 나오면 같은 페이지가 스스로 어긋난다.
    """
    metric = next((m for m in getattr(result, "metrics", []) if m.key == "debt_ratio"), None)
    if metric is None or metric.signal is not Signal.NOT_APPLICABLE:
        return None
    return " ".join((metric.note or "이 업종에는 뜻이 없는 지표입니다.").split())


def quarter_json(q: LatestQuarter | None, debt_ratio_na: str | None = None) -> dict | None:
    """`api/c/<code>.json` 의 `latest_quarter`. 판정 없이 값만, 없는 값은 null (0 이 아니다)."""
    if q is None:
        return None

    def lines(items: tuple[QuarterLine, ...]) -> dict:
        return {ln.key: {"label": ln.label, "value": ln.current, "prior": ln.prior} for ln in items}

    return {
        "bsns_year": q.bsns_year,
        "reprt_code": q.reprt_code.value,
        "label": q.report_label,
        "fs_div": q.fs_div,
        "currency": q.currency,
        "period_end": q.period_end.isoformat(),
        "period_label": q.period_label,
        "prior_year_end": q.prior_year_end.isoformat() if q.prior_year_end else None,
        "flows_note": "손익은 그 분기 석 달 값. prior 는 1년 전 같은 분기",
        "flows": lines(q.flows),
        "stocks_note": "재무상태는 분기 말 값. prior 는 직전 사업연도 말",
        "stocks": lines(q.stocks),
        "debt_ratio": (
            {"value": None, "prior": None, "not_applicable": debt_ratio_na}
            if debt_ratio_na
            else {
                "value": q.debt_ratio("current"),
                "prior": q.debt_ratio("prior"),
                "formula": "부채총계 ÷ 자본총계 × 100 (자본총계 0 이하면 null)",
            }
        ),
        "rcept_no": q.rcept_no or None,
        "dart_url": q.dart_url or None,
        "note": (
            "신호등은 사업보고서(연간)로만 매긴다. 이 값은 판정하지 않는다."
            " 분기 재무제표는 감사가 아니라 검토"
        ),
    }


def _pick(rows: list[KeyAccountRow], sj_div: str, names: tuple[str, ...]) -> KeyAccountRow | None:
    for name in names:
        for row in rows:  # ord 순 — 같은 이름이 두 번 나오면(당기순이익) 앞의 것
            if row.sj_div == sj_div and row.account_nm == name:
                return row
    return None


def build_quarter(rows: list[KeyAccountRow]) -> LatestQuarter | None:
    """한 회사·한 보고서의 줄들 → 화면용 값. 연결이 있으면 연결, 없으면 개별 (사업보고서와 같은 규칙)."""
    if not rows:
        return None
    divs = {r.fs_div for r in rows}
    fs_div = "CFS" if "CFS" in divs else "OFS"
    rows = sorted((r for r in rows if r.fs_div == fs_div), key=lambda r: r.ord)
    picked = {key: _pick(rows, sj, names) for key, (_, sj, names) in ACCOUNTS.items()}
    bs = next((r for r in rows if r.sj_div == "BS"), None)
    period_end = _parse_date(bs.thstrm_dt) if bs else None
    if period_end is None:
        is_row = next((r for r in rows if r.sj_div == "IS"), None)
        period_end = _parse_date(is_row.thstrm_dt) if is_row else None
    if period_end is None:
        return None
    currency = rows[0].currency or "KRW"

    def line(key: str) -> QuarterLine:
        row = picked[key]
        return QuarterLine(
            key=key,
            label=ACCOUNTS[key][0],
            current=row.thstrm_amount if row else None,
            prior=row.frmtrm_amount if row else None,
            currency=currency,
        )

    first = rows[0]
    return LatestQuarter(
        corp_code=first.corp_code,
        bsns_year=first.bsns_year,
        reprt_code=ReportCode(first.reprt_code),
        fs_div=fs_div,
        rcept_no=first.rcept_no,
        period_end=period_end,
        prior_year_end=_parse_date(bs.frmtrm_dt) if bs else None,
        currency=currency,
        flows=tuple(line(k) for k in FLOW_KEYS),
        stocks=tuple(line(k) for k in STOCK_KEYS),
    )


def _order(row: KeyAccountRow) -> tuple[int, int]:
    code = ReportCode(row.reprt_code)
    return (row.bsns_year, INTERIM_ORDER.index(code) if code in INTERIM_ORDER else -1)


def latest_quarters(rows: list[KeyAccountRow]) -> dict[str, LatestQuarter]:
    """회사별 가장 최근 분기. 값이 하나도 없는 보고서는 건너뛰고 그 앞 분기를 쓴다."""
    by_report: dict[tuple[str, int, str], list[KeyAccountRow]] = {}
    for row in rows:
        if ReportCode(row.reprt_code) not in INTERIM_ORDER:
            continue
        by_report.setdefault((row.corp_code, row.bsns_year, row.reprt_code), []).append(row)
    best: dict[str, tuple[tuple[int, int], LatestQuarter]] = {}
    for (corp, _, _), group in by_report.items():
        rank = _order(group[0])
        if corp in best and best[corp][0] >= rank:
            continue
        quarter = build_quarter(group)
        if quarter is not None and quarter.has_any:
            best[corp] = (rank, quarter)
    return {corp: q for corp, (_, q) in best.items()}


def newer_than_annual(quarter: LatestQuarter | None, annual_year: int) -> LatestQuarter | None:
    """사업보고서보다 뒤의 분기만. 같은 사업연도의 분기는 사업보고서가 이미 담고 있다."""
    if quarter is None or quarter.bsns_year <= annual_year:
        return None
    return quarter
