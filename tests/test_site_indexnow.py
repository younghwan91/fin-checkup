"""IndexNow — 배포가 끝나면 이번 빌드에서 바뀐 주소만 검색 엔진(Bing·Naver·Yandex·Seznam)에 알린다.

검색 엔진 계정 없이 색인을 앞당기는 유일한 길이라, 열쇠 파일이 사이트 경로 바로 아래에 있고(`/fin-checkup/`
프로젝트 사이트라 `keyLocation` 으로 알린다) 보낼 주소가 그 아래에만 있는지를 여기서 고정한다.
네트워크는 부르지 않는다 — 보내는 함수엔 가짜 `opener` 를 넘긴다.
"""

from __future__ import annotations

import io
import json
import re
import urllib.error
from datetime import date

import pytest
from hub_seed import seed_site_cache

from fin_checkup.site import indexnow
from fin_checkup.site.build import build_site
from fin_checkup.site.guard import check_site
from fin_checkup.site.seo import check_seo

BASE = "https://example.github.io/fin-checkup/"


async def _build(tmp_path, name: str, built_on: date):
    from fin_checkup.storage import Cache

    with Cache(tmp_path / f"{name}.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp_path / name
        await build_site(cache, target, 2024, base_url=BASE, built_on=built_on)
    return target


@pytest.fixture
async def out(tmp_path):
    return await _build(tmp_path, "out", date(2026, 11, 21))


def test_key_follows_the_protocol() -> None:
    # 8~128자, 영문·숫자·대시. 공개가 전제라 저장소에 둔다.
    # 40자리 16진수(DART 인증키 모양)는 가드가 막으니 피한다.
    assert re.fullmatch(r"[a-zA-Z0-9-]{8,128}", indexnow.KEY)
    assert len(indexnow.KEY) != 40


async def test_build_serves_the_key_file_under_the_site_path(out) -> None:
    key_file = out / indexnow.KEY_FILE
    assert key_file.read_bytes() == indexnow.KEY.encode("utf-8")
    # 열쇠 파일이 들어간 빌드도 가드를 통과한다(인증키 패턴·금지 표현).
    indexnow.write_digests(out)
    assert [v for v in check_site(out) if v.rule != "coverage"] == []
    assert check_seo(out) == []


def test_payload_names_host_key_and_key_location_under_the_site_path() -> None:
    payload = indexnow.build_payload(["", "c/005930/"], BASE)
    assert payload == {
        "host": "example.github.io",
        "key": indexnow.KEY,
        "keyLocation": f"{BASE}{indexnow.KEY}.txt",
        "urlList": [BASE, f"{BASE}c/005930/"],
    }
    # 열쇠 파일이 /fin-checkup/ 에 있으면 그 아래 주소만 보낼 수 있다.
    location_dir = payload["keyLocation"].rsplit("/", 1)[0] + "/"
    assert all(u.startswith(location_dir) for u in payload["urlList"])


def test_payload_is_capped_at_the_protocol_limit() -> None:
    paths = [f"c/{i:06d}/" for i in range(indexnow.MAX_URLS + 5)]
    assert len(indexnow.build_payload(paths, BASE)["urlList"]) == indexnow.MAX_URLS


def test_payload_needs_an_absolute_base() -> None:
    with pytest.raises(ValueError):
        indexnow.build_payload([""], "/")


def test_first_run_sends_every_page_then_only_changes() -> None:
    current = {"": "a", "c/005930/": "b", "c/000660/": "c"}
    assert indexnow.changed_paths(None, current) == ["", "c/000660/", "c/005930/"]
    previous = {"": "a", "c/005930/": "old", "c/999999/": "gone"}
    # 바뀐 것·새로 생긴 것·없어진 것(검색 엔진이 지우게) — 그대로인 홈은 뺀다.
    assert indexnow.changed_paths(previous, current) == ["c/000660/", "c/005930/", "c/999999/"]
    assert indexnow.changed_paths(current, dict(current)) == []


async def test_digests_ignore_the_build_date_but_see_content(tmp_path, out) -> None:
    later = await _build(tmp_path, "later", date(2026, 11, 22))
    first, second = indexnow.site_digests(out), indexnow.site_digests(later)
    assert first and "c/005930/" in first and "" in first
    assert first == second, "날짜만 바뀐 빌드는 아무것도 보내지 않는다"

    page = later / "c" / "005930" / "index.html"
    page.write_text(page.read_text(encoding="utf-8").replace("</main>", "<p>정정공시</p></main>"), "utf-8")
    assert indexnow.changed_paths(first, indexnow.site_digests(later)) == ["c/005930/"]


class _Response:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status, self._body = status, body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


async def test_plan_sends_everything_once_when_no_digests_are_live(out) -> None:
    def fetch(url, timeout):
        assert url == f"{BASE}{indexnow.DIGESTS}"
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())

    payload = indexnow.plan(out, BASE, opener=fetch)
    assert payload is not None and len(payload["urlList"]) == len(indexnow.site_digests(out))
    # 다음 배포의 기준이 되도록 이번 빌드의 지문을 사이트에 싣는다.
    assert json.loads((out / indexnow.DIGESTS).read_text("utf-8"))["pages"] == indexnow.site_digests(out)


async def test_plan_sends_only_changes_against_the_live_digests(out) -> None:
    live = dict(indexnow.site_digests(out))
    live["c/005930/"] = "0" * 16

    def fetch(url, timeout):
        return _Response(200, json.dumps({"version": 1, "pages": live}).encode())

    payload = indexnow.plan(out, BASE, opener=fetch)
    assert payload["urlList"] == [f"{BASE}c/005930/"]
    same = (out / indexnow.DIGESTS).read_bytes()
    assert indexnow.plan(out, BASE, opener=lambda u, timeout: _Response(200, same)) is None


async def test_plan_skips_when_the_live_site_cannot_be_read(out) -> None:
    # 404 가 아닌 실패(네트워크·5xx)로 전체를 다시 보내지 않는다 — 전체 전송은 처음 한 번뿐.
    def fetch(url, timeout):
        raise urllib.error.URLError("timed out")

    assert indexnow.plan(out, BASE, opener=fetch) is None
    assert (out / indexnow.DIGESTS).is_file()


def test_submit_posts_json_and_returns_the_status() -> None:
    seen = {}

    def opener(request, timeout):
        seen["request"] = request
        return _Response(202)

    payload = indexnow.build_payload([""], BASE)
    assert indexnow.submit(payload, opener=opener) == 202
    request = seen["request"]
    assert request.full_url == indexnow.ENDPOINT == "https://api.indexnow.org/indexnow"
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json; charset=utf-8"
    assert json.loads(request.data) == payload


def test_submit_never_raises() -> None:
    payload = indexnow.build_payload([""], BASE)

    def forbidden(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO())

    def offline(request, timeout):
        raise urllib.error.URLError("no route")

    assert indexnow.submit(payload, opener=forbidden) == 403
    assert indexnow.submit(payload, opener=offline) is None


def test_cli_submit_exits_zero_without_a_payload(tmp_path, capsys) -> None:
    assert indexnow.main(["submit", str(tmp_path / "missing.json")]) == 0
    assert "IndexNow" in capsys.readouterr().out


# 2026-11-27 첫 배포: 배포 직후 보낸 2,731곳이 HTTP 403 — 검색 엔진이 아직 열쇠 파일을 확인하지 못했다.
# 몇 분 뒤 같은 본문은 200. 그래서 보내기 전에 열쇠 파일이 라이브인지 보고, 403 이면 기다렸다 다시 보낸다.


def _router(key_statuses, post_statuses):
    """열쇠 파일 GET 과 IndexNow POST 를 차례로 응답하는 가짜 opener. 호출 기록을 남긴다."""
    calls = []
    keys, posts = list(key_statuses), list(post_statuses)

    def opener(request, timeout):
        url = request if isinstance(request, str) else request.full_url
        calls.append(url)
        if url == indexnow.ENDPOINT:
            status = posts.pop(0)
            if status >= 400:
                raise urllib.error.HTTPError(url, status, "x", {}, io.BytesIO())
            return _Response(status)
        status, body = keys.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "x", {}, io.BytesIO())
        return _Response(status, body)

    return opener, calls


def test_deliver_waits_for_the_live_key_file_before_posting() -> None:
    payload = indexnow.build_payload([""], BASE)
    key = indexnow.KEY.encode()
    opener, calls = _router([(404, b""), (200, b"stale"), (200, key)], [200])
    slept = []
    assert indexnow.deliver(payload, opener=opener, sleep=slept.append) == 200
    assert calls[:3] == [payload["keyLocation"]] * 3 and calls[3] == indexnow.ENDPOINT
    assert len(slept) == 2


def test_deliver_retries_a_403_then_succeeds() -> None:
    payload = indexnow.build_payload([""], BASE)
    opener, calls = _router([(200, indexnow.KEY.encode())], [403, 403, 202])
    slept = []
    assert indexnow.deliver(payload, opener=opener, sleep=slept.append) == 202
    assert calls.count(indexnow.ENDPOINT) == 3 and len(slept) == 2


def test_deliver_gives_up_after_the_attempt_limit_without_raising() -> None:
    payload = indexnow.build_payload([""], BASE)
    opener, calls = _router([(404, b"")] * indexnow.ATTEMPTS, [403] * indexnow.ATTEMPTS)
    # 열쇠가 끝내 안 보여도 한 번은 보내 본다 — 상태 코드가 로그에 남아야 원인을 안다.
    assert indexnow.deliver(payload, opener=opener, sleep=lambda s: None) == 403
    assert calls.count(payload["keyLocation"]) == indexnow.ATTEMPTS


def test_deliver_does_not_retry_other_errors() -> None:
    payload = indexnow.build_payload([""], BASE)
    opener, calls = _router([(200, indexnow.KEY.encode())], [422])
    assert indexnow.deliver(payload, opener=opener, sleep=lambda s: None) == 422
    assert calls.count(indexnow.ENDPOINT) == 1
