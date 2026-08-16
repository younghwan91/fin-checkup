from __future__ import annotations

from datetime import date, datetime

import httpx
import pytest
import respx

from fin_checkup.alerts.scheduler import LAST_POLL_KEY, MAX_LOOKBACK_DAYS, AlertScheduler
from fin_checkup.alerts.telegram import ConsoleNotifier
from fin_checkup.alerts.worker import AlertWorker
from fin_checkup.config import Settings
from fin_checkup.models import CorpCode
from fin_checkup.storage import Cache

BASE = "https://opendart.fss.or.kr/api"
SAMSUNG = CorpCode(corp_code="00126380", corp_name="삼성전자", stock_code="005930")


@pytest.fixture
def cache(tmp_path):
    with Cache(tmp_path / "sched.duckdb") as c:
        yield c


@pytest.fixture
def worker_settings(tmp_path) -> Settings:
    return Settings(dart_api_key="k", dart_min_delay=0.0, fin_checkup_db_path=tmp_path / "s.duckdb")


@pytest.fixture
def scheduler(cache, worker_settings) -> AlertScheduler:
    worker = AlertWorker(cache, ConsoleNotifier(), settings=worker_settings)
    return AlertScheduler(cache, worker, interval_seconds=0)


def empty_response() -> httpx.Response:
    return httpx.Response(200, json={"status": "000", "total_page": 1, "page_no": 1, "list": []})


# ── 놓친 구간 계산 ────────────────────────────────────────────────


def test_first_run_looks_back_one_day(scheduler: AlertScheduler):
    # 처음 켠 워커가 한 달치를 쏟아내면 알림이 아니라 스팸이다.
    assert scheduler.lookback_days() == 1


def test_gap_since_last_success_is_covered(scheduler: AlertScheduler, cache: Cache):
    cache.set_meta(LAST_POLL_KEY, datetime(2026, 8, 10, 9, 0).isoformat())
    # 8/10에 마지막 성공, 지금은 8/15 → 5일 + 겹침 1일
    assert scheduler.lookback_days(now=date(2026, 8, 15)) == 6


def test_lookback_is_capped(scheduler: AlertScheduler, cache: Cache):
    cache.set_meta(LAST_POLL_KEY, datetime(2020, 1, 1).isoformat())
    assert scheduler.lookback_days(now=date(2026, 8, 15)) == MAX_LOOKBACK_DAYS


def test_same_day_rerun_still_looks_at_today(scheduler: AlertScheduler, cache: Cache):
    cache.set_meta(LAST_POLL_KEY, datetime(2026, 8, 15, 9, 0).isoformat())
    assert scheduler.lookback_days(now=date(2026, 8, 15)) == 1


def test_corrupt_timestamp_falls_back_to_one_day(scheduler: AlertScheduler, cache: Cache):
    cache.set_meta(LAST_POLL_KEY, "쓰레기값")
    assert scheduler.lookback_days(now=date(2026, 8, 15)) == 1


# ── 실행과 기록 ───────────────────────────────────────────────────


@respx.mock
async def test_successful_run_records_the_timestamp(scheduler: AlertScheduler, cache: Cache):
    respx.get(f"{BASE}/list.json").mock(return_value=empty_response())
    cache.add_watch("chat1", SAMSUNG)

    assert cache.get_meta(LAST_POLL_KEY) is None
    report = await scheduler.run_once()
    assert report is not None
    assert cache.get_meta(LAST_POLL_KEY) is not None


@respx.mock
async def test_failed_run_does_not_advance_the_timestamp(scheduler: AlertScheduler, cache: Cache):
    cache.add_watch("chat1", SAMSUNG)
    cache.set_meta(LAST_POLL_KEY, datetime(2026, 8, 10).isoformat())
    respx.get(f"{BASE}/list.json").mock(
        return_value=httpx.Response(200, json={"status": "010", "message": "키 오류"})
    )

    assert await scheduler.run_once() is None
    assert cache.get_meta(LAST_POLL_KEY) == datetime(2026, 8, 10).isoformat(), (
        "실패한 구간을 성공으로 표시하면 그 사이 공시가 영영 사라진다"
    )


@respx.mock
async def test_failure_does_not_raise_out_of_the_scheduler(scheduler: AlertScheduler, cache: Cache):
    cache.add_watch("chat1", SAMSUNG)
    respx.get(f"{BASE}/list.json").mock(side_effect=httpx.ConnectError("망 끊김"))
    assert await scheduler.run_once() is None  # 예외가 새어나오지 않는다


@respx.mock
async def test_consecutive_failures_are_counted_and_reset(scheduler: AlertScheduler, cache: Cache):
    cache.add_watch("chat1", SAMSUNG)
    route = respx.get(f"{BASE}/list.json")

    route.mock(return_value=httpx.Response(200, json={"status": "010", "message": "키 오류"}))
    await scheduler.run_once()
    await scheduler.run_once()
    assert scheduler.consecutive_failures == 2

    route.mock(return_value=empty_response())
    await scheduler.run_once()
    assert scheduler.consecutive_failures == 0


@respx.mock
async def test_run_forever_stops_after_max_cycles(scheduler: AlertScheduler, cache: Cache):
    respx.get(f"{BASE}/list.json").mock(return_value=empty_response())
    cache.add_watch("chat1", SAMSUNG)
    assert await scheduler.run_forever(max_cycles=3) == 3


@respx.mock
async def test_recovery_after_downtime_covers_the_gap(scheduler: AlertScheduler, cache: Cache):
    """두 시간이 아니라 며칠 죽어 있었어도 그 구간을 다시 본다."""
    cache.add_watch("chat1", SAMSUNG)
    seen: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["bgn_de"])
        return empty_response()

    respx.get(f"{BASE}/list.json").mock(side_effect=responder)
    cache.set_meta(LAST_POLL_KEY, datetime(2026, 8, 10).isoformat())
    await scheduler.worker.poll(days=scheduler.lookback_days(now=date(2026, 8, 15)),
                                today=date(2026, 8, 15))
    assert seen and seen[0] == "20260810", "마지막 성공 시점까지 거슬러 올라가야 한다"


# ── meta 저장소 ───────────────────────────────────────────────────


def test_meta_roundtrip(cache: Cache):
    assert cache.get_meta("없는키") is None
    cache.set_meta("k", "v1")
    assert cache.get_meta("k") == "v1"
    cache.set_meta("k", "v2")
    assert cache.get_meta("k") == "v2"
