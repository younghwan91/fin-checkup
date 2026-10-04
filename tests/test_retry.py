from __future__ import annotations

import random

import httpx
import pytest
import respx

from fin_checkup.config import Settings
from fin_checkup.dart.client import DartClient, DartError, RateLimitExceeded
from fin_checkup.dart.retry import RetryPolicy, is_retryable, with_retry

BASE = "https://opendart.fss.or.kr/api"


@pytest.fixture
def fast_settings(tmp_path) -> Settings:
    return Settings(dart_api_key="k", dart_min_delay=0.0, fin_checkup_db_path=tmp_path / "r.duckdb")


async def no_sleep(_seconds: float) -> None:
    return None


# ── 재시도 대상 판별 ──────────────────────────────────────────────


@pytest.mark.parametrize("status", ["800", "900", "901"])
def test_transient_dart_statuses_are_retryable(status):
    assert is_retryable(DartError(status, "일시적")) is True


@pytest.mark.parametrize("status", ["010", "011", "013", "100", "101"])
def test_permanent_dart_statuses_are_not_retryable(status):
    # 인증키가 틀렸거나 요청이 잘못된 건 다시 해도 같다. 남은 호출량만 태운다.
    assert is_retryable(DartError(status, "영구적")) is False


@pytest.mark.parametrize("status", ["020", "021"])
def test_quota_and_request_limit_statuses_are_not_retryable(status):
    # 020 은 일별 허용량 초과라 몇 초 뒤에 다시 해도 풀리지 않고, 021 은 요청 자체가
    # 잘못된 것(조회 회사 100건 초과)이다. 둘 다 재시도하면 남은 호출량만 태운다.
    assert is_retryable(DartError(status, "한도")) is False


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
def test_transient_http_is_retryable(code):
    exc = httpx.HTTPStatusError(
        "x", request=httpx.Request("GET", "https://x"), response=httpx.Response(code)
    )
    assert is_retryable(exc) is True


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_client_errors_are_not_retryable(code):
    exc = httpx.HTTPStatusError(
        "x", request=httpx.Request("GET", "https://x"), response=httpx.Response(code)
    )
    assert is_retryable(exc) is False


def test_network_errors_are_retryable():
    assert is_retryable(httpx.ConnectError("boom")) is True
    assert is_retryable(httpx.ReadTimeout("slow")) is True


def test_unrelated_errors_are_not_retryable():
    assert is_retryable(ValueError("버그")) is False


# ── 백오프 ────────────────────────────────────────────────────────


def test_delay_grows_exponentially():
    policy = RetryPolicy(base_delay=1.0, jitter=0.0)
    assert [policy.delay_for(i) for i in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]


def test_delay_is_capped():
    policy = RetryPolicy(base_delay=1.0, max_delay=5.0, jitter=0.0)
    assert policy.delay_for(10) == 5.0


def test_jitter_spreads_retries():
    policy = RetryPolicy(base_delay=10.0, jitter=0.5)
    rng = random.Random(0)
    delays = {policy.delay_for(1, rng) for _ in range(20)}
    assert len(delays) > 1, "지터가 없으면 여러 워커가 같은 순간에 몰린다"
    assert all(5.0 <= d <= 15.0 for d in delays)


def test_delay_is_never_negative():
    policy = RetryPolicy(base_delay=0.1, jitter=5.0)
    rng = random.Random(1)
    assert all(policy.delay_for(1, rng) >= 0 for _ in range(50))


# ── with_retry ────────────────────────────────────────────────────


async def test_succeeds_without_retry():
    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        return "ok"

    assert await with_retry(op, sleep=no_sleep) == "ok"
    assert calls == 1


async def test_retries_then_succeeds():
    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("일시적")
        return "ok"

    assert await with_retry(op, RetryPolicy(base_delay=0), sleep=no_sleep) == "ok"
    assert calls == 3


async def test_gives_up_after_max_attempts():
    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("계속 실패")

    with pytest.raises(httpx.ConnectError):
        await with_retry(op, RetryPolicy(max_attempts=3, base_delay=0), sleep=no_sleep)
    assert calls == 3


async def test_permanent_failure_is_not_retried():
    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        raise DartError("010", "인증키가 유효하지 않습니다")

    with pytest.raises(DartError):
        await with_retry(op, RetryPolicy(base_delay=0), sleep=no_sleep)
    assert calls == 1, "영구적 실패를 재시도하면 남은 호출량만 태운다"


async def test_sleeps_between_attempts():
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    async def op():
        raise httpx.ConnectError("실패")

    with pytest.raises(httpx.ConnectError):
        await with_retry(
            op, RetryPolicy(max_attempts=3, base_delay=1.0, jitter=0.0), sleep=record
        )
    assert slept == [1.0, 2.0], "마지막 시도 뒤에는 자지 않는다"


# ── 클라이언트 통합 ───────────────────────────────────────────────


@respx.mock
async def test_client_retries_transient_dart_status(fast_settings):
    responses = [
        httpx.Response(200, json={"status": "800", "message": "시스템 점검"}),
        httpx.Response(200, json={"status": "000", "list": [
            {"sj_div": "BS", "account_id": "ifrs-full_Assets",
             "account_nm": "자산총계", "thstrm_amount": "100", "ord": "1"}]}),
    ]
    route = respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(side_effect=responses)

    client = DartClient(settings=fast_settings, retry_policy=RetryPolicy(base_delay=0))
    raw = await client.fetch_statements("00126380", 2024)
    assert raw is not None
    assert route.call_count == 2, "status=800은 HTTP 200이라도 재시도해야 한다"
    await client.aclose()


@respx.mock
async def test_quota_exceeded_stops_immediately_with_a_distinct_error(fast_settings):
    route = respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(200, json={"status": "020", "message": "요청 제한 초과"})
    )
    client = DartClient(settings=fast_settings, retry_policy=RetryPolicy(base_delay=0))
    with pytest.raises(RateLimitExceeded) as info:
        await client.fetch_statements("00126380", 2024)
    assert isinstance(info.value, DartError), "기존 except DartError 경로가 그대로 잡아야 한다"
    assert info.value.status == "020"
    assert route.call_count == 1, "일별 한도는 재시도로 풀리지 않는다 — 더 때리면 안 된다"
    await client.aclose()


@respx.mock
async def test_client_does_not_retry_bad_api_key(fast_settings):
    route = respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(200, json={"status": "010", "message": "등록되지 않은 키"})
    )
    client = DartClient(settings=fast_settings, retry_policy=RetryPolicy(base_delay=0))
    with pytest.raises(DartError):
        await client.fetch_statements("00126380", 2024)
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_client_retries_transient_http_error(fast_settings):
    responses = [
        httpx.Response(503, text="일시적"),
        httpx.Response(200, json={"status": "013", "message": "없음"}),
    ]
    route = respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(side_effect=responses)
    client = DartClient(settings=fast_settings, retry_policy=RetryPolicy(base_delay=0))
    assert await client.fetch_statements("00126380", 2024) is None
    assert route.call_count == 2
    await client.aclose()


@respx.mock
async def test_client_counts_calls_including_retries(fast_settings):
    seen: list[str] = []
    responses = [
        httpx.Response(200, json={"status": "800", "message": "점검"}),
        httpx.Response(200, json={"status": "013", "message": "없음"}),
    ]
    respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(side_effect=responses)

    client = DartClient(
        settings=fast_settings,
        retry_policy=RetryPolicy(base_delay=0),
        on_call=seen.append,
    )
    await client.fetch_statements("00126380", 2024)
    assert client.calls_made == 2
    assert seen == ["fnlttSinglAcntAll.json"] * 2
    await client.aclose()


@respx.mock
async def test_no_data_still_returns_none_without_retrying(fast_settings):
    route = respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(200, json={"status": "013", "message": "없음"})
    )
    client = DartClient(settings=fast_settings, retry_policy=RetryPolicy(base_delay=0))
    assert await client.fetch_statements("00126380", 2024) is None
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_timed_out_calls_are_still_counted(fast_settings):
    """응답을 못 받은 호출도 DART 쪽에서는 한 건이다. 응답 뒤에 세면 타임아웃이
    집계에서 빠져 예산이 실제보다 적게 보인다."""
    seen: list[str] = []
    respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(side_effect=httpx.ReadTimeout("느림"))
    client = DartClient(
        settings=fast_settings,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0),
        on_call=seen.append,
    )
    with pytest.raises(httpx.ReadTimeout):
        await client.fetch_statements("00126380", 2024)
    assert client.calls_made == 2
    assert len(seen) == 2
    await client.aclose()


# ── 인증키 노출 ───────────────────────────────────────────────────


@respx.mock
async def test_http_error_message_does_not_contain_the_api_key(fast_settings, caplog):
    """httpx 의 HTTPStatusError 메시지는 전체 URL 을 담는다 — crtfc_key 까지. 503 한 번이면
    WARNING 로그 한 줄과 트레이스백에 키가 남는다(약관 제19조)."""
    respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(return_value=httpx.Response(503, text="점검"))
    client = DartClient(settings=fast_settings, retry_policy=RetryPolicy(max_attempts=2, base_delay=0))
    with caplog.at_level("DEBUG"), pytest.raises(httpx.HTTPStatusError) as info:
        await client.fetch_statements("00126380", 2024)
    await client.aclose()

    assert fast_settings.dart_api_key == "k"
    assert "crtfc_key=k" not in str(info.value)
    assert "crtfc_key=k" not in repr(info.value)
    for record in caplog.records:
        assert "crtfc_key=k" not in record.getMessage(), record.name


@respx.mock
async def test_httpx_request_log_masks_the_api_key(fast_settings, caplog):
    """httpx 는 INFO 레벨로 요청 URL 전체를 로그에 남긴다. 로깅 레벨을 올리는 순간 모든
    호출마다 키가 찍힌다."""
    respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(200, json={"status": "013", "message": "없음"})
    )
    client = DartClient(settings=fast_settings)
    with caplog.at_level("DEBUG"):
        await client.fetch_statements("00126380", 2024)
    await client.aclose()

    httpx_lines = [r.getMessage() for r in caplog.records if r.name.startswith("httpx")]
    assert httpx_lines, "httpx 가 요청 로그를 남겨야 이 테스트가 뜻이 있다"
    assert all("crtfc_key=k" not in line for line in httpx_lines)
    assert any("crtfc_key=***" in line for line in httpx_lines)
