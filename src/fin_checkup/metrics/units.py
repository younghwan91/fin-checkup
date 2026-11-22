"""보고서 금액 단위 오류(×1000) 의심.

(주)대산에프앤비 2023 사업보고서는 모든 줄이 1,000배로 공시됐다(자산 56.7조 — 2024 보고서의 전기 열은
564억). 그대로 쓰면 그해 금액 지표·추이·시장 분포가 자릿수째 틀린다. 같은 사업연도는 두 보고서에 나온다 —
그해 보고서의 당기 열과 다음 해 보고서의 전기 열. 둘이 기준 계정(자산총계·자본총계·매출액) 두 개 이상에서
정확히 1,000배(±2%) 차이 나면 한쪽이 단위를 잘못 적은 것이다.

어느 쪽인지는 이웃 보고서로 가른다. 단위를 잘못 적은 보고서는 당기·전기 열이 함께 1,000배라 **앞뒤 두 쌍
모두**와 어긋나고, 멀쩡한 보고서는 다른 이웃과 맞는다. 그래서 "1,000배 쌍의 앞 해" 를 기계적으로 의심하면
대산에프앤비 2022(멀쩡하다 — 2023 보고서의 전기 열이 1,000배)가 함께 걸린다. 이웃이 없어 가를 수 없으면
앞 해(그해 보고서)를 의심한다.

자동으로 1,000 으로 나누지 않는다 — 어느 숫자가 맞는지는 원문을 본 사람이 정한다. 의심 연도는 지표를 ⚫ 로
두고 대조군·작년 대비·위험 신호·추이 값에서 뺀다.
"""

from __future__ import annotations

from collections.abc import Mapping

from fin_checkup.models import Financials

#: 같은 사업연도를 두 보고서로 견줄 기준 계정 — 재작성이 드물고 0 이 아닌 큰 금액.
ANCHORS: tuple[str, ...] = ("total_assets", "total_equity", "revenue")
SCALES: tuple[float, ...] = (1000.0, 0.001)
TOLERANCE = 0.02
#: 기준 계정 중 이만큼이 1,000배여야 한다. 매출은 재작성으로 함께 움직이기도 한다
#: (대산에프앤비 2023 매출 1,097배).
MIN_ANCHORS = 2

UNIT_SUSPECT_NOTE = "보고서 금액 단위 오류 의심 — 다음 해 보고서의 같은 해 값과 1,000배 차이"
#: 다음 해 보고서가 없어 앞 해 보고서로 알아챈 경우(최신 연도).
UNIT_SUSPECT_PREVIOUS_NOTE = "보고서 금액 단위 오류 의심 — 앞 해 보고서와 겹치는 해의 값이 1,000배 차이"
#: 그해 지표·작년 대비를 ⚫ 로 둘 때 메모에서 이 사유를 알아보는 표시.
UNIT_SUSPECT_MARKER = "보고서 금액 단위 오류 의심"
PRIOR_UNIT_SUSPECT_NOTE = (
    "작년 보고서 금액 단위 오류 의심 — 작년과 견줄 수 없다(올해 보고서에 전기 열이 없다)."
)


def pair_scale(own: Financials | None, following: Financials | None) -> float | None:
    """그해 보고서(own)의 당기 값 ÷ 다음 해 보고서(following)의 전기 값이 1,000배 또는 1/1,000 이면 그 배수.

    둘이 맞으면 1.0, 견줄 수 없으면(보고서·전기 열·기준 계정이 없으면) None.
    """
    comparative = following.comparative if following is not None else None
    if own is None or comparative is None:
        return None
    ratios = []
    for name in ANCHORS:
        mine, theirs = getattr(own, name), getattr(comparative, name)
        if mine and theirs:
            ratios.append(mine / theirs)
    if not ratios:
        return None
    for scale in SCALES:
        if sum(abs(r / scale - 1) <= TOLERANCE for r in ratios) >= MIN_ANCHORS:
            return scale
    return 1.0


def unit_suspects(reports: Mapping[int, Financials]) -> dict[int, str]:
    """사업연도 → 단위 오류가 의심되는 보고서의 메모. 해마다 그해 사업보고서(전기 열 포함)를 넘긴다."""
    found: dict[int, str] = {}
    for year in sorted(reports):
        scale = pair_scale(reports.get(year), reports.get(year + 1))
        if scale is None or scale == 1.0:
            continue
        before = pair_scale(reports.get(year - 1), reports.get(year))
        after = pair_scale(reports.get(year + 1), reports.get(year + 2))
        if before is not None:
            odd = year if before != 1.0 else year + 1
        elif after is not None:
            odd = year + 1 if after != 1.0 else year
        else:
            odd = year
        found[odd] = UNIT_SUSPECT_NOTE if odd == year else UNIT_SUSPECT_PREVIOUS_NOTE
    return found


def mark_unit_suspects(reports: Mapping[int, Financials]) -> dict[int, Financials]:
    """의심 연도의 Financials 에 `unit_suspect` 메모를 단 사본. 나머지는 그대로."""
    suspects = unit_suspects(reports)
    return {
        year: fin.model_copy(update={"unit_suspect": suspects[year]}) if year in suspects else fin
        for year, fin in reports.items()
    }
