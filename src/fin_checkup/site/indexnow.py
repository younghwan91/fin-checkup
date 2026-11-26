"""IndexNow — Pages 배포가 끝나면 이번 빌드에서 바뀐 주소를 검색 엔진에 알린다 (계정 없이).

    uv run python -m fin_checkup.site.indexnow plan site-out --base-url "$SITE_URL" --out payload.json
    uv run python -m fin_checkup.site.indexnow submit payload.json

프로토콜: https://www.indexnow.org/documentation — `api.indexnow.org` 하나에 보내면 Bing·Naver·Yandex·
Seznam 이 나눠 받는다. 열쇠(`KEY`)는 공개가 전제다: 사이트에 `<KEY>.txt` 로 그대로 싣는다
(`pages/indexnow.py`).
이 사이트는 도메인 루트가 아니라 `/fin-checkup/` 아래라 열쇠 파일도 거기 있고, 그 위치를 `keyLocation` 으로
알린다 — 열쇠 파일이 있는 디렉터리 아래 주소만 보낼 수 있다는 규칙에 맞는다.

"바뀐 주소"는 페이지 지문(`<title>` + `<main>`, 빌드 날짜는 가린다)을 지난 배포와 비교해 고른다. 지난 배포의
지문은 라이브 사이트의 `page-digests.json` 이다 — 배포에 실패하면 라이브가 그대로라 다음 배포가 같은 변경을
다시 잡는다. 라이브에 그 파일이 없으면(404 — 처음 한 번) 색인 페이지 전부를 보낸다. 404 가 아닌 실패로는
전체를 다시 보내지 않고 이번 알림을 건너뛴다.

보내기가 실패해도 배포는 실패하지 않는다 — HTTP 상태만 남기고 종료 코드는 0 이다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fin_checkup.site.context import page_url
from fin_checkup.site.registry import Output
from fin_checkup.site.sitemap import is_indexable

#: 공개 열쇠. 한 번 만들어 고정한다 — 바꾸면 검색 엔진이 새 열쇠를 다시 확인한다.
KEY = "2dcd7ae93c17fed3fcee357319e719ab"
KEY_FILE = f"{KEY}.txt"
ENDPOINT = "https://api.indexnow.org/indexnow"
#: 프로토콜의 요청당 상한.
MAX_URLS = 10_000
#: 이번 빌드의 페이지 지문 — 다음 배포가 비교할 기준. 사이트 루트에 싣는다.
DIGESTS = "page-digests.json"
TIMEOUT = 20

Opener = Callable[..., Any]

_TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_MAIN = re.compile(r"<main\b.*?</main>", re.DOTALL)


def page_digest(html: str, built_on: str) -> str:
    """검색 엔진이 볼 본문의 지문. 매일 바뀌는 빌드 날짜는 가려서 날짜만 바뀐 빌드는 같게 나온다."""
    title = _TITLE.search(html)
    main = _MAIN.search(html)
    body = (title.group(1) if title else "") + "\n" + (main.group(0) if main else html)
    if built_on:
        body = body.replace(built_on, "{built_on}")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def site_digests(out: Path) -> dict[str, str]:
    """출력 폴더의 색인 페이지(sitemap 과 같은 기준) → 지문. 키는 사이트 안 주소(`c/005930/`, 홈은 "")."""
    built_on = ""
    build_json = out / "build.json"
    if build_json.is_file():
        built_on = str(json.loads(build_json.read_text("utf-8")).get("built_on", ""))
    digests = {}
    for file in sorted(out.rglob("*.html")):
        rel = file.relative_to(out).as_posix()
        html = file.read_text(encoding="utf-8")
        if is_indexable(Output(rel, html)):
            digests[page_url(rel)] = page_digest(html, built_on)
    return digests


def write_digests(out: Path) -> dict[str, str]:
    digests = site_digests(out)
    text = json.dumps({"version": 1, "pages": digests}, ensure_ascii=False, sort_keys=True)
    (out / DIGESTS).write_text(text + "\n", encoding="utf-8")
    return digests


def changed_paths(previous: dict[str, str] | None, current: dict[str, str]) -> list[str]:
    """보낼 주소. 기준이 없으면 전부, 있으면 바뀐 것·새로 생긴 것·없어진 것."""
    if previous is None:
        return sorted(current)
    keys = set(previous) | set(current)
    return sorted(k for k in keys if previous.get(k) != current.get(k))


def build_payload(paths: Iterable[str], base_url: str) -> dict[str, Any]:
    """POST 본문. `base_url` 은 사이트 주소(`https://host/fin-checkup/`)."""
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"IndexNow 는 절대주소 사이트에만 보낸다: {base_url!r}")
    base = base_url if base_url.endswith("/") else base_url + "/"
    urls = [base + p for p in paths][:MAX_URLS]
    return {"host": parts.netloc, "key": KEY, "keyLocation": base + KEY_FILE, "urlList": urls}


def live_digests(base_url: str, opener: Opener = urllib.request.urlopen) -> tuple[str, dict[str, str] | None]:
    """라이브 사이트의 지문. ("ok", 지문) · ("missing", None — 404) · ("error", None)."""
    url = base_url.rstrip("/") + "/" + DIGESTS
    try:
        with opener(url, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
        pages = data["pages"]
        if not isinstance(pages, dict):
            raise ValueError("pages 가 객체가 아니다")
        return "ok", {str(k): str(v) for k, v in pages.items()}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "missing", None
        print(f"IndexNow: 라이브 {DIGESTS} HTTP {exc.code}")
        return "error", None
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        print(f"IndexNow: 라이브 {DIGESTS} 를 읽지 못했다 ({type(exc).__name__})")
        return "error", None


def plan(out: Path, base_url: str, opener: Opener = urllib.request.urlopen) -> dict[str, Any] | None:
    """이번 빌드의 지문을 `out/page-digests.json` 에 쓰고, 보낼 것이 있으면 POST 본문을 돌려준다."""
    current = write_digests(out)
    state, previous = live_digests(base_url, opener)
    if state == "error":
        print("IndexNow: 지난 배포의 기준을 몰라 이번엔 보내지 않는다")
        return None
    paths = changed_paths(previous, current)
    reason = "처음 — 색인 페이지 전부" if previous is None else "지난 배포와 달라진 페이지"
    if not paths:
        print("IndexNow: 바뀐 페이지가 없다")
        return None
    payload = build_payload(paths, base_url)
    dropped = len(paths) - len(payload["urlList"])
    note = f" (상한 넘어 {dropped:,}곳 뺌)" if dropped else ""
    print(f"IndexNow: {reason} {len(payload['urlList']):,}곳{note}")
    return payload


def submit(payload: dict[str, Any], opener: Opener = urllib.request.urlopen) -> int | None:
    """POST 하고 HTTP 상태를 돌려준다. 실패해도 예외를 내지 않는다(연결 실패는 None)."""
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with opener(request, timeout=TIMEOUT) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m fin_checkup.site.indexnow", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_plan = sub.add_parser("plan", help="지문을 쓰고 보낼 주소를 고른다 (배포 전, 빌드 산출물에서)")
    p_plan.add_argument("out", type=Path)
    p_plan.add_argument("--base-url", required=True)
    p_plan.add_argument(
        "--out", dest="payload", type=Path, required=True, help="POST 본문을 쓸 곳 (보낼 게 없으면 안 쓴다)"
    )
    p_submit = sub.add_parser("submit", help="POST 본문을 보낸다 (배포 뒤). 실패해도 종료 코드 0")
    p_submit.add_argument("payload", type=Path)
    args = parser.parse_args(argv)

    if args.command == "plan":
        payload = plan(args.out, args.base_url)
        if payload is not None:
            args.payload.parent.mkdir(parents=True, exist_ok=True)
            args.payload.write_text(json.dumps(payload), encoding="utf-8")
        return 0
    if not args.payload.is_file():
        print("IndexNow: 보낼 주소가 없어 건너뛴다")
        return 0
    payload = json.loads(args.payload.read_text("utf-8"))
    status = submit(payload)
    print(f"IndexNow: {len(payload['urlList']):,}곳 → HTTP {status if status is not None else '연결 실패'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
