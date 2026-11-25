# ruff: noqa: E501 — HTML·CSS 조각이 데이터다. 한 줄을 쪼개면 읽고 고치기 어렵다.
"""README 배너와 GitHub 소셜 미리보기 PNG 를 다시 그린다 (playwright 로 HTML 을 찍는다).

    uv run python scripts/brand_images.py          # docs/images/banner*.png · social-preview.png

- `banner.png` · `banner-dark.png` (1280×400) — README.md 맨 위 `<picture>`
- `banner-en.png` · `banner-en-dark.png` — README.en.md
- `social-preview.png` (1280×640) — 저장소 Settings → General → Social preview 에 오너가 올린다(API 로는 못 올린다)

마크는 `fin_checkup.brand`, 글자는 Noto Sans KR(없으면 시스템 고딕). 숫자(회사 수 등)는 넣지 않는다 — 해마다 바뀐다.
모든 그림에 출처와 "측정값이며 투자권유가 아닙니다" 를 싣는다(docs/REGULATORY.md).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from fin_checkup import brand

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
SITE = "younghwan91.github.io/fin-checkup"

TEXT = {
    "ko": {
        "tagline": "재무제표 신호등",
        "headline": "상장사 재무제표를 신호등으로.<br>색마다 계산식과 경계값을 붙였습니다.",
        "legend": ("정상", "주의", "위험", "측정값", "해당 없음", "데이터 없음"),
        "legend_title": "지표 17개 · 신호는 모양과 글자로도",
        "foot": "출처: DART · 측정값이며 투자권유가 아닙니다 · 설치·가입 없음",
    },
    "en": {
        "tagline": "financial-statement traffic light",
        "headline": "Korean listed companies' financials as a traffic light.<br>"
        "Every color comes with its formula and thresholds.",
        "legend": ("Normal", "Caution", "Risk", "Measured", "N/A", "No data"),
        "legend_title": "17 metrics · shape + word, not color alone",
        "foot": "Source: DART · Measurements, not investment advice · No install, no sign-up",
    },
}
MARKS = ("green", "yellow", "red", "neutral", "not_applicable", "unknown")

# 사이트(site.css) 와 같은 색 — 밝은 / 어두운.
COLORS = {
    "light": dict(
        bg="#f7f7f5",
        surface="#ffffff",
        border="#e4e4e0",
        ink="#1b1b19",
        muted="#686863",
        green="#11a102",
        yellow="#fcb51d",
        red="#b93e41",
        neutral="#8c8c88",
        unknown="#6e6e69",
    ),
    "dark": dict(
        bg="#141413",
        surface="#1c1c1a",
        border="#34342f",
        ink="#ececea",
        muted="#a6a6a0",
        green="#35b400",
        yellow="#ffcb5f",
        red="#eb6e6f",
        neutral="#9a9a94",
        unknown="#8a8a84",
    ),
}


def _css(c: dict[str, str]) -> str:
    return f"""
* {{ box-sizing: border-box; margin: 0; }}
body {{ background: {c["bg"]}; color: {c["ink"]};
  font-family: "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif;
  word-break: keep-all; }}
.frame {{ position: relative; overflow: hidden; display: flex; flex-direction: column; justify-content: center; }}
.brand {{ display: flex; align-items: center; gap: 22px; }}
.brand svg {{ flex: none; }}
.name {{ font-weight: 700; letter-spacing: -.01em; line-height: 1; }}
.tag {{ color: {c["muted"]}; margin-top: 10px; }}
.headline {{ font-weight: 500; line-height: 1.45; letter-spacing: -.01em; }}
.legend {{ background: {c["surface"]}; border: 2px solid {c["border"]}; border-radius: 16px; padding: 22px 26px; }}
.legend h2 {{ font-size: 19px; font-weight: 500; color: {c["muted"]}; margin-bottom: 14px; }}
.legend ul {{ list-style: none; padding: 0; display: grid; grid-template-columns: auto auto; gap: 12px 30px; }}
.legend li {{ display: flex; align-items: center; gap: 12px; font-size: 23px; white-space: nowrap; }}
.m {{ display: inline-block; width: 22px; height: 22px; flex: none; }}
.m-green {{ background: {c["green"]}; border-radius: 50%; }}
.m-yellow {{ background: {c["yellow"]}; clip-path: polygon(50% 0, 100% 100%, 0 100%); width: 25px; }}
.m-red {{ background: {c["red"]}; border-radius: 2px; }}
.m-neutral {{ background: {c["neutral"]}; border-radius: 3px; height: 10px; width: 25px; }}
.m-not_applicable {{ border: 3px solid {c["unknown"]}; border-radius: 50%;
  background: linear-gradient(135deg, transparent 42%, {c["unknown"]} 42%, {c["unknown"]} 58%, transparent 58%); }}
.m-unknown {{ border: 3px dashed {c["unknown"]}; border-radius: 50%; }}
.foot {{ color: {c["muted"]}; font-size: 20px; }}
"""


def _mark(theme: str, height: int) -> str:
    svg = brand.mark_svg(theme).strip()
    width = round(height * 48 / 58)
    return svg.replace(
        'viewBox="0 0 64 64"', f'viewBox="{brand.TIGHT_VIEWBOX}" width="{width}" height="{height}"'
    )


def _legend(t: dict) -> str:
    items = "".join(
        f'<li><span class="m m-{key}"></span>{label}</li>'
        for key, label in zip(MARKS, t["legend"], strict=True)
    )
    return f'<div class="legend"><h2>{t["legend_title"]}</h2><ul>{items}</ul></div>'


def banner_html(lang: str, theme: str) -> str:
    t, c = TEXT[lang], COLORS[theme]
    headline_size = 30 if lang == "ko" else 26
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_css(c)}
.frame {{ width: 1280px; height: 400px; padding: 0 72px; }}
.row {{ display: flex; align-items: center; justify-content: space-between; gap: 48px; }}
.left {{ max-width: 640px; }}
.name {{ font-size: 64px; }} .tag {{ font-size: 25px; }}
.headline {{ font-size: {headline_size}px; margin-top: 30px; }}
.foot {{ position: absolute; left: 72px; bottom: 26px; }}
</style></head><body><div class="frame"><div class="row"><div class="left">
<div class="brand">{_mark(theme, 116)}<div><div class="name">fin-checkup</div><div class="tag">{t["tagline"]}</div></div></div>
<p class="headline">{t["headline"]}</p></div>{_legend(t)}</div>
<p class="foot">{t["foot"]}</p></div></body></html>"""


def readme_pitch(readme: str) -> str:
    """README 제목(# fin-checkup) 아래 첫 굵은 줄 — 소셜 미리보기는 이 한 줄 소개를 그대로 싣는다."""
    m = re.search(r"^# fin-checkup\s*$.*?^\*\*(.+?)\*\*\s*$", readme, re.M | re.S)
    if not m:
        raise SystemExit("README 제목 아래 굵은 한 줄 소개를 못 찾았다")
    return m.group(1)


def social_html() -> str:
    t, c = TEXT["ko"], COLORS["light"]
    ko = readme_pitch((ROOT / "README.md").read_text("utf-8"))
    en = readme_pitch((ROOT / "README.en.md").read_text("utf-8"))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_css(c)}
.frame {{ width: 1280px; height: 640px; padding: 0 88px; }}
.row {{ display: flex; align-items: center; justify-content: space-between; gap: 56px; }}
.name {{ font-size: 76px; }} .tag {{ font-size: 28px; }}
.headline {{ font-size: 42px; font-weight: 700; margin-top: 40px; }}
.en {{ color: {c["muted"]}; font-size: 24px; margin-top: 14px; line-height: 1.45; max-width: 620px; }}
.foot {{ position: absolute; left: 88px; right: 88px; bottom: 40px; display: flex; justify-content: space-between; }}
.url {{ color: {c["ink"]}; font-weight: 500; }}
</style></head><body><div class="frame"><div class="row"><div>
<div class="brand">{_mark("light", 140)}<div><div class="name">fin-checkup</div><div class="tag">{t["tagline"]}</div></div></div>
<p class="headline">{ko}</p>
<p class="en">{en}</p></div>{_legend(t)}</div>
<p class="foot"><span>{t["foot"]}</span><span class="url">{SITE}</span></p></div></body></html>"""


def render(pages: dict[str, tuple[str, int, int]], out: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, (html, width, height) in pages.items():
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(html)
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(out / name), clip={"x": 0, "y": 0, "width": width, "height": height})
            page.close()
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pages = {
        "banner.png": (banner_html("ko", "light"), 1280, 400),
        "banner-dark.png": (banner_html("ko", "dark"), 1280, 400),
        "banner-en.png": (banner_html("en", "light"), 1280, 400),
        "banner-en-dark.png": (banner_html("en", "dark"), 1280, 400),
        "social-preview.png": (social_html(), 1280, 640),
    }
    render(pages, args.out)
    for name in pages:
        print(args.out / name)


if __name__ == "__main__":
    main()
