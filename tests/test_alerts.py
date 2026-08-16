from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from fin_checkup.alerts.classify import RiskKind, Severity, classify, classify_all
from fin_checkup.alerts.message import format_alert, format_digest
from fin_checkup.alerts.telegram import ConsoleNotifier, TelegramNotifier
from fin_checkup.alerts.worker import AlertWorker
from fin_checkup.config import Settings
from fin_checkup.models import CorpCode, Disclosure
from fin_checkup.storage import Cache

BASE = "https://opendart.fss.or.kr/api"
SAMSUNG = CorpCode(corp_code="00126380", corp_name="삼성전자", stock_code="005930")
HYNIX = CorpCode(corp_code="00164779", corp_name="SK하이닉스", stock_code="000660")


@pytest.fixture
def cache(tmp_path):
    with Cache(tmp_path / "alerts.duckdb") as c:
        yield c


def disc(report_nm: str, rcept_no: str = "20240101000001", corp_code: str = "00126380") -> Disclosure:
    return Disclosure(
        rcept_no=rcept_no, corp_code=corp_code, corp_name="삼성전자", stock_code="005930",
        report_nm=report_nm, flr_nm="삼성전자", rcept_dt="20240115",
    )


# ── 관심종목 저장 ─────────────────────────────────────────────────


def test_watchlist_add_and_list(cache: Cache):
    assert cache.add_watch("chat1", SAMSUNG) is True
    assert cache.add_watch("chat1", SAMSUNG) is False, "중복 등록은 False"
    assert [c.corp_name for c in cache.list_watch("chat1")] == ["삼성전자"]


def test_watchlist_is_per_chat(cache: Cache):
    cache.add_watch("chat1", SAMSUNG)
    cache.add_watch("chat2", HYNIX)
    assert [c.corp_name for c in cache.list_watch("chat1")] == ["삼성전자"]
    assert [c.corp_name for c in cache.list_watch("chat2")] == ["SK하이닉스"]
    assert set(cache.watched_corp_codes()) == {SAMSUNG.corp_code, HYNIX.corp_code}


def test_watchlist_remove(cache: Cache):
    cache.add_watch("chat1", SAMSUNG)
    assert cache.remove_watch("chat1", SAMSUNG.corp_code) is True
    assert cache.remove_watch("chat1", SAMSUNG.corp_code) is False
    assert cache.list_watch("chat1") == []


def test_notified_dedup(cache: Cache):
    assert cache.was_notified("chat1", "R1") is False
    cache.mark_notified("chat1", "R1")
    assert cache.was_notified("chat1", "R1") is True
    cache.mark_notified("chat1", "R1")  # 두 번 호출해도 터지지 않는다
    assert cache.was_notified("chat2", "R1") is False, "알림 기록은 대상별로 분리된다"


# ── 메시지 ────────────────────────────────────────────────────────


def test_alert_message_contains_facts_and_link():
    risk = classify(disc("주요사항보고서(유상증자결정)"))
    text = format_alert(risk)
    assert "삼성전자" in text
    assert "유상증자" in text
    assert "dart.fss.or.kr" in text
    assert "2024-01-15" in text


def test_alert_message_never_recommends():
    for name in ["주요사항보고서(유상증자결정)", "상장폐지사유발생", "감사보고서제출"]:
        text = format_alert(classify(disc(name)))
        for word in ("매수", "매도", "추천", "사세요", "파세요", "손절", "익절", "적기"):
            assert word not in text, f"'{name}' 알림에 '{word}'"
        assert "투자권유가 아닙니다" in text


def test_alert_message_escapes_html():
    d = Disclosure(rcept_no="1", corp_code="1", corp_name="<b>주식</b>회사",
                   report_nm="주요사항보고서(유상증자결정)")
    text = format_alert(classify(d))
    assert "&lt;b&gt;" in text


def test_correction_is_marked():
    text = format_alert(classify(disc("[기재정정]주요사항보고서(유상증자결정)")))
    assert "정정공시" in text


def test_digest_groups_and_truncates():
    risks = classify_all([disc("주요사항보고서(유상증자결정)", f"R{i}") for i in range(15)])
    text = format_digest(risks, limit=10)
    assert "15건" in text
    assert "외 5건" in text


def test_digest_of_nothing_is_empty():
    assert format_digest([]) == ""


# ── 텔레그램 ──────────────────────────────────────────────────────


def test_telegram_requires_token():
    with pytest.raises(ValueError, match="봇 토큰"):
        TelegramNotifier("")


@respx.mock
async def test_telegram_sends_html():
    route = respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    async with TelegramNotifier("TOKEN") as notifier:
        assert await notifier.send("chat1", "<b>hi</b>") is True
    body = route.calls.last.request.content.decode()
    assert "HTML" in body


@respx.mock
async def test_telegram_failure_returns_false_without_raising():
    respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    async with TelegramNotifier("TOKEN") as notifier:
        assert await notifier.send("chat1", "hi") is False


@respx.mock
async def test_telegram_network_error_returns_false():
    respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
        side_effect=httpx.ConnectError("boom")
    )
    async with TelegramNotifier("TOKEN") as notifier:
        assert await notifier.send("chat1", "hi") is False


# ── 워커 ──────────────────────────────────────────────────────────


@pytest.fixture
def worker_settings(tmp_path) -> Settings:
    return Settings(dart_api_key="k", dart_min_delay=0.0, fin_checkup_db_path=tmp_path / "w.duckdb")


def _list_response(items: list[dict]) -> httpx.Response:
    return httpx.Response(
        200, json={"status": "000", "total_page": 1, "page_no": 1, "list": items}
    )


def _item(report_nm: str, rcept_no: str, corp_code: str = "00126380") -> dict:
    return {
        "rcept_no": rcept_no, "corp_code": corp_code, "corp_name": "삼성전자",
        "stock_code": "005930", "report_nm": report_nm, "flr_nm": "삼성전자",
        "rcept_dt": "20240115", "corp_cls": "Y",
    }


async def test_worker_does_nothing_without_watchers(cache, worker_settings):
    notifier = ConsoleNotifier()
    worker = AlertWorker(cache, notifier, settings=worker_settings)
    with respx.mock:
        report = await worker.poll()
    assert report.sent == 0
    assert notifier.sent == []


@respx.mock
async def test_worker_sends_alert_for_watched_risky_disclosure(cache, worker_settings):
    cache.add_watch("chat1", SAMSUNG)
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([_item("주요사항보고서(유상증자결정)", "R1")])
    )
    notifier = ConsoleNotifier()
    worker = AlertWorker(cache, notifier, settings=worker_settings)
    report = await worker.poll(today=date(2024, 1, 15))
    assert report.sent >= 1
    assert "유상증자" in notifier.sent[0][1]


@respx.mock
async def test_watchers_get_every_severity_by_default(cache, worker_settings):
    """등록한 종목이면 강도를 가리지 않고 보낸다. 하한은 명시할 때만 걸린다."""
    cache.add_watch("chat1", SAMSUNG)
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([_item("주요사항보고서(유상증자결정)", "R1")])
    )
    report = await AlertWorker(cache, ConsoleNotifier(), settings=worker_settings).poll()
    assert report.sent == 1


@respx.mock
async def test_explicit_min_severity_filters_out_lower_grades(cache, worker_settings):
    cache.add_watch("chat1", SAMSUNG)
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([_item("주요사항보고서(유상증자결정)", "R1")])
    )
    worker = AlertWorker(
        cache, ConsoleNotifier(), settings=worker_settings, min_severity=Severity.CRITICAL
    )
    assert (await worker.poll()).sent == 0


@respx.mock
async def test_worker_ignores_unwatched_companies(cache, worker_settings):
    cache.add_watch("chat1", HYNIX)
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([_item("주요사항보고서(유상증자결정)", "R1")])
    )
    notifier = ConsoleNotifier()
    report = await AlertWorker(cache, notifier, settings=worker_settings).poll()
    assert report.sent == 0


@respx.mock
async def test_worker_ignores_routine_disclosures(cache, worker_settings):
    cache.add_watch("chat1", SAMSUNG)
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([_item("사업보고서 (2023.12)", "R1")])
    )
    notifier = ConsoleNotifier()
    report = await AlertWorker(cache, notifier, settings=worker_settings).poll()
    assert report.risky == 0
    assert report.sent == 0


@respx.mock
async def test_worker_does_not_send_the_same_disclosure_twice(cache, worker_settings):
    cache.add_watch("chat1", SAMSUNG)
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([_item("주요사항보고서(유상증자결정)", "R1")])
    )
    notifier = ConsoleNotifier()
    worker = AlertWorker(cache, notifier, settings=worker_settings)
    first = await worker.poll()
    second = await worker.poll()
    assert first.sent >= 1
    assert second.sent == 0
    assert second.skipped_duplicate >= 1


@respx.mock
async def test_worker_respects_min_severity(cache, worker_settings):
    cache.add_watch("chat1", SAMSUNG)
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([_item("주식등의대량보유상황보고서(일반)", "R1")])
    )
    notifier = ConsoleNotifier()
    worker = AlertWorker(cache, notifier, settings=worker_settings, min_severity=Severity.CRITICAL)
    report = await worker.poll()
    assert report.sent == 0, "MEDIUM 공시는 CRITICAL 필터에 걸러져야 한다"


@respx.mock
async def test_failed_send_is_retried_next_poll(cache, worker_settings):
    cache.add_watch("chat1", SAMSUNG)
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([_item("주요사항보고서(부도발생)", "R1")])
    )

    class FlakyNotifier:
        def __init__(self):
            self.attempts = 0

        async def send(self, chat_id, text):
            self.attempts += 1
            return self.attempts > 1  # 첫 시도만 실패

    notifier = FlakyNotifier()
    worker = AlertWorker(cache, notifier, settings=worker_settings)
    first = await worker.poll()
    assert first.failed == 1 and first.sent == 0
    second = await worker.poll()
    assert second.sent == 1, "실패한 알림은 다음 폴링에서 다시 시도돼야 한다"


async def test_worker_without_api_key_is_a_noop(cache, tmp_path):
    cache.add_watch("chat1", SAMSUNG)
    notifier = ConsoleNotifier()
    worker = AlertWorker(cache, notifier, settings=Settings(dart_api_key=""))
    with respx.mock:
        report = await worker.poll()
    assert report.sent == 0


@respx.mock
async def test_worker_queries_all_watch_types(cache, worker_settings):
    from fin_checkup.alerts.worker import WATCH_TYPES

    cache.add_watch("chat1", SAMSUNG)
    route = respx.get(f"{BASE}/list.json").mock(return_value=_list_response([]))
    await AlertWorker(cache, ConsoleNotifier(), settings=worker_settings).poll()
    seen = {call.request.url.params["pblntf_ty"] for call in route.calls}
    assert seen == {t.value for t in WATCH_TYPES}


def test_critical_kinds_outrank_medium():
    assert RiskKind.DISTRESS.severity.rank > RiskKind.INSIDER_TRADING.severity.rank
