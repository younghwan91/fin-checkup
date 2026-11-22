"""docs/brand 파일을 `fin_checkup.brand` 정의에서 다시 뽑는다.

    uv run --with fonttools python scripts/brand_assets.py

- `logo-mark.svg` · `logo-mark-dark.svg` — 마크 (64×64)
- `logo-mark-{512,256,128}.png` · `logo-mark-dark-{512,256,128}.png`
- `logo-wordmark.svg` · `logo-wordmark-dark.svg` — 마크 + "fin-checkup" + "재무제표 신호등".
  글자는 윤곽선(path)으로 바꿔 넣는다 — 보는 쪽에 글꼴이 없어도 모양이 같게. 글꼴은 사이트 PNG 와 같은 것
  (`site.images.fonts.resolve_fonts`, 보통 Noto Sans KR/CJK — SIL OFL, 윤곽선으로 넣어 배포해도 된다).

fonttools 는 이 스크립트만 쓴다(프로젝트 의존성 아님).
마크 SVG 가 정의와 같은지는 tests/test_brand.py 가 본다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fin_checkup import brand
from fin_checkup.site.images.fonts import FontFace, resolve_fonts

OUT = Path(__file__).resolve().parents[1] / "docs" / "brand"
NAME = "fin-checkup"
TAGLINE = "재무제표 신호등"


def _outline(text: str, face: FontFace, size: float, x: float, baseline: float) -> tuple[str, float]:
    """글자를 SVG path d 로. (d, 끝 x)."""
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont

    font = TTFont(face.path, fontNumber=face.index)
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    scale = size / font["head"].unitsPerEm
    pen = SVGPathPen(glyphs, ntos=lambda v: f"{round(v, 2):g}")
    for ch in text:
        name = cmap[ord(ch)]
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
        x += glyphs[name].width * scale
    return pen.getCommands(), x


def wordmark_svg(theme: str, faces: tuple[FontFace, FontFace]) -> str:
    """마크(높이 58) 오른쪽에 이름(굵게)과 부제. viewBox 높이 58 단위."""
    regular, bold = faces
    pal = brand.PALETTES[theme]
    mark = brand.mark_svg(theme)
    inner = mark.split(">", 1)[1].rsplit("</svg>", 1)[0]
    # 마크를 tight viewBox(8 3 48 58)만큼 옮겨 왼쪽 위를 (0,0) 에.
    name_d, name_end = _outline(NAME, bold, 31, 60, 30)
    tag_d, tag_end = _outline(TAGLINE, regular, 15.5, 61, 54.5)
    width = round(max(name_end, tag_end) + 2, 1)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:g} 58" role="img">'
        f"<title>{NAME} — {TAGLINE}</title>"
        f'<g transform="translate(-8 -3)">{inner}</g>'
        f'<path fill="{pal.ink}" d="{name_d}"/>'
        f'<path fill="{pal.ink_muted}" d="{tag_d}"/></svg>\n'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    fonts = resolve_fonts()
    if fonts is None:
        raise SystemExit(
            "한국어 글꼴을 찾지 못했다 — FIN_CHECKUP_FONT_REGULAR·FIN_CHECKUP_FONT_BOLD 를 지정할 것"
        )
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        (out / f"logo-mark{suffix}.svg").write_text(brand.mark_svg(theme), encoding="utf-8")
        (out / f"logo-wordmark{suffix}.svg").write_text(
            wordmark_svg(theme, (fonts.regular, fonts.bold)), encoding="utf-8"
        )
        for size in (512, 256, 128):
            brand.draw_mark(size, theme).save(out / f"logo-mark{suffix}-{size}.png", optimize=True)
    print(f"{out} — 글꼴 {Path(fonts.bold.path).name}")


if __name__ == "__main__":
    main()
