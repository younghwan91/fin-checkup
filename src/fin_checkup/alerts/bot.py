"""텔레그램 봇 — 사용자가 관심종목을 등록하는 쪽.

채널은 시장 전체의 CRITICAL 공시를 뿌리고(발견 경로), 봇은 등록한 종목의 모든
위험 공시를 보낸다(실제 가치). 채널에서 봇으로 넘어오는 게 전환이다.

명령 처리는 순수 함수(`handle_command`)로 두고 네트워크는 바깥에 뒀다. 텔레그램을
띄우지 않고도 대화 흐름 전체를 테스트할 수 있어야 한다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from fin_checkup.service import CheckupService
from fin_checkup.storage import Cache

logger = logging.getLogger(__name__)

OFFSET_KEY = "alerts.bot_offset"

WELCOME = """👋 <b>재무 위험 공시 알림</b>

관심종목을 등록하면 그 종목에 <b>유상증자·CB 발행·감사의견·최대주주 변동</b> 같은
공시가 뜰 때 알려드립니다. 사실과 DART 원문 링크만 전달하며, 투자권유는 하지 않습니다.

<b>사용법</b>
/watch 005930 — 관심종목 등록 (회사명도 됩니다)
/list — 등록한 종목 보기
/unwatch 005930 — 등록 해제
/help — 이 안내

먼저 <code>/watch 005930</code> 처럼 종목코드를 보내보세요."""

HELP = """<b>명령어</b>

/watch &lt;종목코드 또는 회사명&gt; — 관심종목 등록
/list — 등록한 종목 보기
/unwatch &lt;종목코드&gt; — 등록 해제
/help — 이 안내

예: <code>/watch 005930</code> · <code>/watch 삼성전자</code>

알림은 사실 전달이며 투자권유가 아닙니다. 판단은 원문을 직접 확인하세요."""


@dataclass(frozen=True)
class Reply:
    """명령 처리 결과. 네트워크와 분리하려고 값으로 돌려준다."""

    text: str
    #: 등록·해제가 실제로 일어났는지. 통계와 테스트에 쓴다.
    changed: bool = False


def _argument(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def handle_command(
    service: CheckupService, chat_id: str, text: str
) -> Reply | None:
    """명령 한 줄을 처리한다. 우리가 응답할 게 없으면 None.

    네트워크를 타지 않아 대화 흐름을 그대로 테스트할 수 있다.
    """
    text = (text or "").strip()
    if not text.startswith("/"):
        return Reply(
            "명령으로 보내주세요. <code>/watch 005930</code> 처럼 쓰시면 됩니다.\n"
            "전체 명령은 /help"
        )

    # 그룹에서는 /watch@봇이름 형태로 온다.
    command = text.split()[0].split("@")[0].lower()
    argument = _argument(text)
    cache = service.cache

    if command in ("/start", "/help"):
        return Reply(WELCOME if command == "/start" else HELP)

    if command == "/list":
        watched = cache.list_watch(chat_id)
        if not watched:
            return Reply("등록한 관심종목이 없습니다. <code>/watch 005930</code>")
        lines = [f"📋 <b>관심종목 {len(watched)}개</b>", ""]
        lines += [f"  • {c.corp_name} <code>{c.stock_code}</code>" for c in watched]
        return Reply("\n".join(lines))

    if command == "/watch":
        if not argument:
            return Reply("종목코드나 회사명을 함께 보내주세요. <code>/watch 005930</code>")

        matches = service.search(argument, limit=5)
        if not matches:
            return Reply(f"'{argument}'에 해당하는 상장기업을 찾지 못했습니다.")
        if len(matches) > 1 and not argument.isdigit():
            lines = [f"'{argument}'로 여러 곳이 검색됐습니다. 종목코드로 지정해주세요.", ""]
            lines += [f"  • {c.corp_name} <code>{c.stock_code}</code>" for c in matches]
            return Reply("\n".join(lines))

        corp = matches[0]
        if cache.is_watching(chat_id, corp.corp_code):
            return Reply(f"{corp.corp_name}은(는) 이미 등록돼 있습니다.")

        cache.add_watch(chat_id, corp)
        count = cache.count_watch(chat_id)
        return Reply(
            f"✅ <b>{corp.corp_name}</b> <code>{corp.stock_code}</code> 등록했습니다. "
            f"(총 {count}개)\n\n"
            f"이제 이 종목에 위험 공시가 뜨면 알려드립니다.",
            changed=True,
        )

    if command == "/unwatch":
        if not argument:
            return Reply("해제할 종목코드를 함께 보내주세요. <code>/unwatch 005930</code>")
        matches = service.search(argument, limit=1)
        if not matches:
            return Reply(f"'{argument}'에 해당하는 상장기업을 찾지 못했습니다.")
        corp = matches[0]
        removed = cache.remove_watch(chat_id, corp.corp_code)
        if not removed:
            return Reply(f"{corp.corp_name}은(는) 등록돼 있지 않습니다.")
        return Reply(f"🗑 <b>{corp.corp_name}</b> 등록을 해제했습니다.", changed=True)

    return Reply(f"모르는 명령입니다: {command}\n/help 로 사용법을 볼 수 있습니다.")


def extract_message(update: dict) -> tuple[str, str] | None:
    """텔레그램 update에서 (chat_id, text)를 꺼낸다. 다룰 게 없으면 None.

    채널 게시물·사진·스티커 등은 무시한다.
    """
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    text = message.get("text")
    if not isinstance(chat, dict) or not isinstance(text, str):
        return None
    chat_id = chat.get("id")
    return (str(chat_id), text) if chat_id is not None else None


class TelegramBot:
    """롱폴링으로 명령을 받아 처리한다."""

    def __init__(self, cache: Cache, notifier, service: CheckupService | None = None) -> None:
        self.cache = cache
        self.notifier = notifier
        self.service = service or CheckupService(cache)

    @property
    def offset(self) -> int:
        raw = self.cache.get_meta(OFFSET_KEY)
        try:
            return int(raw) if raw else 0
        except ValueError:
            return 0

    def _remember_offset(self, update_id: int) -> None:
        # 텔레그램은 offset 이전 update를 지운다. 처리한 다음 것부터 달라고 한다.
        self.cache.set_meta(OFFSET_KEY, str(update_id + 1))

    async def process_once(self, timeout: int = 25) -> int:
        """들어온 명령을 한 묶음 처리하고 처리 건수를 반환."""
        updates = await self.notifier.get_updates(offset=self.offset, timeout=timeout)
        handled = 0
        for update in updates:
            update_id = update.get("update_id")
            extracted = extract_message(update)
            if extracted is not None:
                chat_id, text = extracted
                try:
                    reply = handle_command(self.service, chat_id, text)
                except Exception:  # noqa: BLE001
                    logger.exception("[bot] 명령 처리 실패 chat=%s text=%r", chat_id, text)
                    reply = Reply("처리 중 문제가 생겼습니다. 잠시 후 다시 시도해주세요.")
                if reply is not None:
                    await self.notifier.send(chat_id, reply.text)
                    handled += 1
            # 처리에 실패했어도 offset은 넘긴다. 같은 명령에 영원히 막히면 안 된다.
            if isinstance(update_id, int):
                self._remember_offset(update_id)
        return handled

    async def run_forever(self, max_cycles: int | None = None) -> int:
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            try:
                await self.process_once()
            except Exception:  # noqa: BLE001
                logger.exception("[bot] 폴링 실패 — 5초 후 재시도")
                await asyncio.sleep(5)
            cycles += 1
        return cycles
