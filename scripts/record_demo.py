"""README 데모 GIF — 라이브 사이트를 playwright 로 눌러 보며 화면을 찍고 Pillow 로 잇는다.

    uv run python scripts/record_demo.py                 # docs/images/demo.gif (960×540)
    uv run python scripts/record_demo.py --base http://127.0.0.1:8000/   # 로컬 빌드로

흐름: 첫 화면 → 검색창에 초성 "ㅅㅅㅈㅈ" → 삼성전자 → 한눈에 보기 → 영업이익률 카드의 동종업계 분포 →
지표 해설(/m/operating_margin/). 삼성전자는 사이트 예시 규칙(일반 업종 중 매출액 최대)으로 고른 회사다.
프레임은 장면마다 머무는 시간을 따로 줘서(같은 그림을 반복하지 않는다) 3MB 아래로 맞춘다.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "docs" / "images" / "demo.gif"
LIVE = "https://younghwan91.github.io/fin-checkup/"
SIZE = (960, 540)
MAX_BYTES = 3 * 1024 * 1024


class Recorder:
    def __init__(self, page) -> None:
        self.page = page
        self.frames: list[tuple[Image.Image, int]] = []

    def shot(self, ms: int) -> None:
        image = Image.open(io.BytesIO(self.page.screenshot())).convert("RGB")
        self.frames.append((image, ms))

    def scroll_to(self, selector: str, steps: int = 6, ms: int = 90, offset: int = 12) -> None:
        """스크롤을 몇 단계로 나눠 찍는다 — 눈이 어디로 가는지 따라오게."""
        start = self.page.evaluate("window.scrollY")
        target = self.page.evaluate(
            "([s, o]) => document.querySelector(s).getBoundingClientRect().top + window.scrollY - o",
            [selector, offset],
        )
        for i in range(1, steps + 1):
            t = i / steps
            eased = 1 - (1 - t) ** 3
            self.page.evaluate("y => window.scrollTo(0, y)", start + (target - start) * eased)
            self.shot(ms)


def record(base: str) -> list[tuple[Image.Image, int]]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": SIZE[0], "height": SIZE[1]}, color_scheme="light")
        rec = Recorder(page)
        page.goto(base, wait_until="networkidle")
        rec.shot(1000)

        box = page.locator("main .fc-search-input").first
        box.click()
        rec.shot(300)
        for ch in "ㅅㅅㅈㅈ":
            box.press_sequentially(ch)
            page.wait_for_timeout(250)
            rec.shot(280)
        page.locator("main .fc-results a", has_text="삼성전자").first.wait_for()
        rec.shot(1100)

        page.locator("main .fc-results a", has_text="삼성전자").first.click()
        page.wait_for_load_state("networkidle")
        rec.shot(1100)

        rec.scroll_to("#glance")
        rec.shot(1800)

        rec.scroll_to("#m-operating_margin")
        rec.shot(900)
        page.locator("#m-operating_margin details.fc-peer summary").first.click()
        page.wait_for_timeout(200)
        rec.shot(1800)

        page.locator('#m-operating_margin a[href$="m/operating_margin/"]').first.click()
        page.wait_for_load_state("networkidle")
        rec.shot(2000)
        browser.close()
    return rec.frames


def save_gif(frames: list[tuple[Image.Image, int]], out: Path) -> int:
    # 한 팔레트로 — 장면 사이 색이 깜빡이지 않고 파일도 작다.
    first = frames[0][0]
    palette = first.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    images = [img.quantize(palette=palette, dither=Image.Dither.NONE) for img, _ms in frames]
    images[0].save(
        out,
        save_all=True,
        append_images=images[1:],
        duration=[ms for _img, ms in frames],
        loop=0,
        optimize=True,
        disposal=1,
    )
    return out.stat().st_size


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=LIVE, help="사이트 주소 (끝에 /)")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    frames = record(args.base)
    size = save_gif(frames, args.out)
    seconds = sum(ms for _img, ms in frames) / 1000
    print(f"{args.out} — 프레임 {len(frames)} · {seconds:.1f}초 · {size / 1024 / 1024:.2f}MB")
    if size > MAX_BYTES:
        raise SystemExit(f"{MAX_BYTES // 1024 // 1024}MB 를 넘는다 — 장면을 줄일 것")


if __name__ == "__main__":
    main()
