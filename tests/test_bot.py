from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from fin_checkup.alerts.bot import OFFSET_KEY, TelegramBot, extract_message, handle_command
from fin_checkup.alerts.classify import Severity
from fin_checkup.alerts.message import format_channel_alert
from fin_checkup.alerts.telegram import ConsoleNotifier, TelegramNotifier
from fin_checkup.alerts.worker import AlertWorker
from fin_checkup.config import Settings
from fin_checkup.models import CorpCode
from fin_checkup.service import CheckupService
from fin_checkup.storage import Cache

BASE = "https://opendart.fss.or.kr/api"
SAMSUNG = CorpCode(corp_code="00126380", corp_name="삼성전자", stock_code="005930")
HYNIX = CorpCode(corp_code="00164779", corp_name="SK하이닉스", stock_code="000660")
SDI = CorpCode(corp_code="00126362", corp_name="삼성SDI", stock_code="006400")


@pytest.fixture
def cache(tmp_path):
    with Cache(tmp_path / "bot.duckdb") as c:
        c.save_corp_codes([SAMSUNG, HYNIX, SDI])
        yield c


@pytest.fixture
def service(cache) -> CheckupService:
    return CheckupService(cache, settings=Settings(dart_api_key=""))


def reply_of(service, chat, text):
    r = handle_command(service, chat, text)
    assert r is not None
    return r


# ── 온보딩 ────────────────────────────────────────────────────────


def test_start_explains_what_it_does_and_what_it_does_not(service):
    text = reply_of(service, "u1", "/start").text
    assert "/watch" in text
    assert "투자권유는 하지 않습니다" in text


def test_help(service):
    assert "/unwatch" in reply_of(service, "u1", "/help").text


def test_plain_text_is_nudged_to_a_command(service):
    assert "/help" in reply_of(service, "u1", "안녕하세요").text


def test_unknown_command(service):
    assert "모르는 명령" in reply_of(service, "u1", "/나가").text


# ── 등록 ──────────────────────────────────────────────────────────


def test_watch_by_stock_code(service, cache):
    r = reply_of(service, "u1", "/watch 005930")
    assert r.changed is True
    assert "삼성전자" in r.text
    assert cache.is_watching("u1", SAMSUNG.corp_code)


def test_watch_by_company_name(service, cache):
    r = reply_of(service, "u1", "/watch SK하이닉스")
    assert r.changed is True
    assert cache.is_watching("u1", HYNIX.corp_code)


def test_ambiguous_name_asks_for_the_code(service, cache):
    r = reply_of(service, "u1", "/watch 삼성")
    assert r.changed is False
    assert "종목코드로 지정" in r.text
    assert "005930" in r.text and "006400" in r.text
    assert cache.count_watch("u1") == 0


def test_watch_without_argument(service):
    assert "함께 보내주세요" in reply_of(service, "u1", "/watch").text


def test_watch_unknown_company(service):
    r = reply_of(service, "u1", "/watch 없는회사")
    assert r.changed is False
    assert "찾지 못했습니다" in r.text


def test_watching_twice_is_idempotent(service, cache):
    reply_of(service, "u1", "/watch 005930")
    r = reply_of(service, "u1", "/watch 005930")
    assert r.changed is False
    assert "이미 등록" in r.text
    assert cache.count_watch("u1") == 1


def test_group_style_command_with_bot_suffix(service, cache):
    r = reply_of(service, "u1", "/watch@fincheckup_bot 005930")
    assert r.changed is True


# ── 등록 상한 ─────────────────────────────────────────────────────


def test_watchlist_has_no_limit(service, cache):
    """몇 개를 등록하든 막지 않는다."""
    for code in ("005930", "000660", "006400"):
        assert reply_of(service, "u1", f"/watch {code}").changed is True

    cache.save_corp_codes([SAMSUNG, HYNIX, SDI, CorpCode(
        corp_code="00164742", corp_name="LG화학", stock_code="051910")])
    assert reply_of(service, "u1", "/watch 051910").changed is True


# ── 조회·해제 ─────────────────────────────────────────────────────


def test_list_when_empty(service):
    assert "없습니다" in reply_of(service, "u1", "/list").text


def test_list_shows_registered(service):
    reply_of(service, "u1", "/watch 005930")
    text = reply_of(service, "u1", "/list").text
    assert "삼성전자" in text
    assert "1개" in text


def test_unwatch(service, cache):
    reply_of(service, "u1", "/watch 005930")
    r = reply_of(service, "u1", "/unwatch 005930")
    assert r.changed is True
    assert not cache.is_watching("u1", SAMSUNG.corp_code)


def test_unwatch_something_not_registered(service):
    r = reply_of(service, "u1", "/unwatch 005930")
    assert r.changed is False
    assert "등록돼 있지 않습니다" in r.text


def test_users_are_isolated(service, cache):
    reply_of(service, "u1", "/watch 005930")
    assert "없습니다" in reply_of(service, "u2", "/list").text


# ── update 파싱 ───────────────────────────────────────────────────


def test_extract_message():
    assert extract_message(
        {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/list"}}
    ) == ("42", "/list")


@pytest.mark.parametrize(
    "update",
    [
        {"update_id": 1},
        {"update_id": 1, "message": {"chat": {"id": 42}}},          # 사진 등 텍스트 없음
        {"update_id": 1, "message": {"text": "/list"}},              # chat 없음
        {"update_id": 1, "channel_post": {"chat": {"id": 1}, "text": "x"}},
    ],
)
def test_non_command_updates_are_ignored(update):
    assert extract_message(update) is None


# ── 폴링 루프 ─────────────────────────────────────────────────────


class FakeNotifier:
    def __init__(self, updates: list[list[dict]]):
        self._updates = updates
        self.sent: list[tuple[str, str]] = []
        self.offsets: list[int] = []

    async def get_updates(self, offset: int = 0, timeout: int = 25) -> list[dict]:
        self.offsets.append(offset)
        return self._updates.pop(0) if self._updates else []

    async def send(self, chat_id: str, text: str) -> bool:
        self.sent.append((chat_id, text))
        return True


async def test_process_once_replies_and_advances_offset(cache, service):
    notifier = FakeNotifier([[
        {"update_id": 100, "message": {"chat": {"id": 7}, "text": "/watch 005930"}},
    ]])
    bot = TelegramBot(cache, notifier, service)
    assert await bot.process_once() == 1
    assert notifier.sent[0][0] == "7"
    assert "삼성전자" in notifier.sent[0][1]
    assert cache.get_meta(OFFSET_KEY) == "101"


async def test_offset_is_reused_on_the_next_poll(cache, service):
    notifier = FakeNotifier([
        [{"update_id": 100, "message": {"chat": {"id": 7}, "text": "/list"}}],
        [],
    ])
    bot = TelegramBot(cache, notifier, service)
    await bot.process_once()
    await bot.process_once()
    assert notifier.offsets == [0, 101]


async def test_a_failing_command_does_not_block_the_queue(cache, service, monkeypatch):
    """한 명령이 터져도 offset은 넘어가야 한다. 안 그러면 영원히 같은 걸 재시도한다."""
    import fin_checkup.alerts.bot as bot_module

    def boom(*_args, **_kwargs):
        raise RuntimeError("버그")

    monkeypatch.setattr(bot_module, "handle_command", boom)
    notifier = FakeNotifier([[
        {"update_id": 100, "message": {"chat": {"id": 7}, "text": "/watch 005930"}},
    ]])
    bot = TelegramBot(cache, notifier, service)
    await bot.process_once()
    assert cache.get_meta(OFFSET_KEY) == "101"
    assert "문제가 생겼습니다" in notifier.sent[0][1]


async def test_non_text_updates_still_advance_offset(cache, service):
    notifier = FakeNotifier([[{"update_id": 100, "message": {"chat": {"id": 7}}}]])
    bot = TelegramBot(cache, notifier, service)
    assert await bot.process_once() == 0
    assert cache.get_meta(OFFSET_KEY) == "101"


@respx.mock
async def test_get_updates_parses_telegram_response():
    respx.get("https://api.telegram.org/botTOKEN/getUpdates").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": [{"update_id": 1}]}
        )
    )
    async with TelegramNotifier("TOKEN") as n:
        assert await n.get_updates() == [{"update_id": 1}]


@respx.mock
async def test_get_updates_survives_network_failure():
    respx.get("https://api.telegram.org/botTOKEN/getUpdates").mock(
        side_effect=httpx.ConnectError("끊김")
    )
    async with TelegramNotifier("TOKEN") as n:
        assert await n.get_updates() == []


# ── 채널 브로드캐스트 ─────────────────────────────────────────────


def _list_response(items: list[dict]) -> httpx.Response:
    return httpx.Response(
        200, json={"status": "000", "total_page": 1, "page_no": 1, "list": items}
    )


def _item(
    report_nm: str, rcept_no: str, corp_name: str = "테라사이언스", corp_code: str = "00999999"
) -> dict:
    return {
        "rcept_no": rcept_no, "corp_code": corp_code, "corp_name": corp_name,
        "stock_code": "099999", "report_nm": report_nm, "flr_nm": corp_name,
        "rcept_dt": "20260814", "corp_cls": "K",
    }


@pytest.fixture
def worker_settings(tmp_path) -> Settings:
    return Settings(dart_api_key="k", dart_min_delay=0.0, fin_checkup_db_path=tmp_path / "b.duckdb")


@respx.mock
async def test_broadcast_sends_critical_without_any_watchlist(cache, worker_settings):
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([_item("주요사항보고서(회생절차개시신청)", "R1")])
    )
    notifier = ConsoleNotifier()
    report = await AlertWorker(cache, notifier, settings=worker_settings).broadcast(
        "@channel", today=date(2026, 8, 14)
    )
    assert report.sent == 1
    assert "회생" in notifier.sent[0][1]


@respx.mock
async def test_broadcast_skips_medium_noise(cache, worker_settings):
    """하루 145건 중 78%가 임원·대주주 소유 변동이다. 채널로 나가면 스팸이다."""
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([
            _item("임원ㆍ주요주주특정증권등소유상황보고서", "R1"),
            _item("주식등의대량보유상황보고서(일반)", "R2"),
            _item("상장폐지사유발생", "R3"),
        ])
    )
    notifier = ConsoleNotifier()
    report = await AlertWorker(cache, notifier, settings=worker_settings).broadcast("@ch")
    assert report.sent == 1
    assert "상장폐지" in notifier.sent[0][1]


@respx.mock
async def test_broadcast_does_not_repeat(cache, worker_settings):
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([_item("주요사항보고서(부도발생)", "R1")])
    )
    worker = AlertWorker(cache, ConsoleNotifier(), settings=worker_settings)
    assert (await worker.broadcast("@ch")).sent == 1
    second = await worker.broadcast("@ch")
    assert second.sent == 0 and second.skipped_duplicate == 1


@respx.mock
async def test_broadcast_caps_a_backlog(cache, worker_settings):
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([
            # 회사가 다르면 별개 사건이다. 같은 회사로 채우면 묶여서 1건이 된다.
            _item("주요사항보고서(부도발생)", f"R{i:03d}", f"회사{i}", f"{i:08d}")
            for i in range(50)
        ])
    )
    worker = AlertWorker(cache, ConsoleNotifier(), settings=worker_settings)
    report = await worker.broadcast("@ch", max_per_run=10)
    assert report.sent == 10, "밀린 구간이 한꺼번에 나가면 채널이 죽는다"

    # 남은 건 다음 회차로 넘어간다.
    assert (await worker.broadcast("@ch", max_per_run=10)).sent == 10


@respx.mock
async def test_broadcast_sends_oldest_first(cache, worker_settings):
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([
            _item("주요사항보고서(부도발생)", r, f"회사{r}", f"0000000{i}")
            for i, r in enumerate(("R003", "R001", "R002"))
        ])
    )
    notifier = ConsoleNotifier()
    await AlertWorker(cache, notifier, settings=worker_settings).broadcast("@ch")
    assert len(notifier.sent) == 3
    order = [t.split("R00")[1][0] for _, t in notifier.sent]
    assert order == ["1", "2", "3"], "밀린 구간은 오래된 것부터 복구돼야 한다"


@respx.mock
async def test_broadcast_severity_floor_is_configurable(cache, worker_settings):
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([_item("주요사항보고서(유상증자결정)", "R1")])
    )
    worker = AlertWorker(cache, ConsoleNotifier(), settings=worker_settings)
    assert (await worker.broadcast("@ch")).sent == 0  # 기본은 CRITICAL만
    assert (await worker.broadcast("@ch", min_severity=Severity.HIGH)).sent == 1


# ── 채널 메시지 ───────────────────────────────────────────────────


def _risk(report_nm: str = "주요사항보고서(회생절차개시신청)"):
    from fin_checkup.alerts.classify import classify
    from fin_checkup.models import Disclosure

    return classify(Disclosure(
        rcept_no="20260814000001", corp_code="1", corp_name="테라사이언스",
        stock_code="099999", report_nm=report_nm, rcept_dt="20260814",
    ))


def test_channel_message_leads_with_the_company():
    text = format_channel_alert(_risk())
    assert text.index("테라사이언스") < text.index("회생")
    assert "dart.fss.or.kr" in text


def test_channel_message_invites_to_the_bot():
    text = format_channel_alert(_risk(), bot_username="fincheckup_bot")
    assert "t.me/fincheckup_bot" in text
    assert "유상증자" in text


def test_channel_message_omits_the_invite_without_a_username():
    assert "t.me" not in format_channel_alert(_risk())


def test_channel_message_never_recommends():
    text = format_channel_alert(_risk(), bot_username="b")
    for word in ("매수", "매도", "추천", "사세요", "파세요", "손절"):
        assert word not in text
    assert "투자권유가 아닙니다" in text


# ── 같은 사건 묶기 (실데이터에서 발견) ────────────────────────────


@respx.mock
async def test_same_event_split_into_two_disclosures_is_sent_once(cache, worker_settings):
    """실제로 나온 케이스 — 같은 회사·같은 날·같은 사유가 두 공시로 온다."""
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([
            _item("주권매매거래정지 (지정자문인 선임계약 해지)", "R1", "퓨쳐메디신"),
            _item("기타시장안내 (지정자문인 선임계약 해지에 따른 상장폐지절차 안내)",
                  "R2", "퓨쳐메디신"),
        ])
    )
    notifier = ConsoleNotifier()
    report = await AlertWorker(cache, notifier, settings=worker_settings).broadcast("@ch")
    assert report.sent == 1
    assert "주권매매거래정지" in notifier.sent[0][1], "접수번호가 빠른 쪽을 대표로 쓴다"


@respx.mock
async def test_absorbed_disclosure_does_not_resurface_next_run(cache, worker_settings):
    """묶여서 빠진 공시를 기록하지 않으면 다음 회차에 그게 대표가 돼 중복 발송된다."""
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([
            _item("주권매매거래정지 (지정자문인 선임계약 해지)", "R1", "퓨쳐메디신"),
            _item("기타시장안내 (지정자문인 선임계약 해지 관련)", "R2", "퓨쳐메디신"),
        ])
    )
    worker = AlertWorker(cache, ConsoleNotifier(), settings=worker_settings)
    assert (await worker.broadcast("@ch")).sent == 1
    assert (await worker.broadcast("@ch")).sent == 0


@respx.mock
async def test_different_kinds_on_the_same_day_are_separate_events(cache, worker_settings):
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([
            _item("주요사항보고서(부도발생)", "R1", "어떤회사"),
            _item("상장폐지사유발생", "R2", "어떤회사"),
        ])
    )
    notifier = ConsoleNotifier()
    report = await AlertWorker(cache, notifier, settings=worker_settings).broadcast("@ch")
    assert report.sent == 2, "부도와 상장폐지는 별개 사건이다"


@respx.mock
async def test_failed_send_does_not_swallow_the_absorbed_ones(cache, worker_settings):
    """대표 발송이 실패하면 묶인 것도 기록하지 않아 다음에 다시 시도된다."""
    respx.get(f"{BASE}/list.json").mock(
        return_value=_list_response([
            _item("주권매매거래정지 (사유)", "R1", "회사"),
            _item("기타시장안내 (사유)", "R2", "회사"),
        ])
    )

    class Failing:
        async def send(self, chat_id, text):
            return False

    worker = AlertWorker(cache, Failing(), settings=worker_settings)
    assert (await worker.broadcast("@ch")).failed == 1
    assert cache.was_notified("@ch", "R2") is False


# ── 공시명 공백 정리 ──────────────────────────────────────────────


def test_padded_disclosure_title_is_tidied():
    from fin_checkup.alerts.classify import classify
    from fin_checkup.models import Disclosure

    risk = classify(Disclosure(
        rcept_no="1", corp_code="1", corp_name="회사",
        report_nm="주권매매거래정지              (지정자문인 선임계약 해지)",
    ))
    text = format_channel_alert(risk)
    assert "주권매매거래정지 (지정자문인 선임계약 해지)" in text
    assert "              " not in text
