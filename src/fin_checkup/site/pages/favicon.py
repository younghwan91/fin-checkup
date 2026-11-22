"""파비콘 `/favicon.svg` · `/favicon.ico` — 브랜드 마크(`fin_checkup.brand`).

브라우저는 `<link rel="icon">` 이 없거나 SVG 를 못 읽으면 사이트 루트의 `/favicon.ico` 를 찾는다.
없으면 모든 페이지가 404 를 하나씩 낸다. ICO 는 같은 마크를 빌드 때 32×32 로 그려 만든다
(바이너리 파일을 저장소에 두지 않는다 — 마크를 바꾸면 둘이 함께 바뀐다).

SVG 는 `prefers-color-scheme: dark` 면 어두운 마크로 바뀐다(어두운 탭에서 먹색 종이가 묻히지 않게).
ICO 는 미디어 쿼리가 없어 밝은 마크 하나.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator

from fin_checkup.brand import adaptive_mark_svg, draw_mark
from fin_checkup.site.context import Site
from fin_checkup.site.registry import Output, page

SIZE = 32


def favicon_svg() -> str:
    return adaptive_mark_svg()


def favicon_ico() -> bytes:
    """32×32 · 32비트 BMP 한 장을 담은 ICO."""
    image = draw_mark(SIZE)
    # BMP 는 BGRA, 아래 줄부터.
    rows = [
        b"".join(bytes((b, g, r, a)) for r, g, b, a in (image.getpixel((ix, iy)) for ix in range(SIZE)))
        for iy in range(SIZE)
    ]
    pixels = b"".join(reversed(rows))
    and_mask = b"\x00" * (SIZE * SIZE // 8)  # 투명도는 알파로. 마스크는 비워 둔다.
    header = struct.pack(
        "<IiiHHIIiiII", 40, SIZE, SIZE * 2, 1, 32, 0, len(pixels) + len(and_mask), 0, 0, 0, 0
    )
    bmp = header + pixels + and_mask
    icondir = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", SIZE, SIZE, 0, 0, 1, 32, len(bmp), 6 + 16)
    return icondir + entry + bmp


@page("favicon")
def favicon_files(site: Site) -> Iterator[Output]:
    yield Output("favicon.svg", favicon_svg())
    yield Output("favicon.ico", favicon_ico())
