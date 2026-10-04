"""텔레그램 봇 전송.

봇 토큰은 @BotFather에서 발급받는다. 무료이고 즉시 된다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

#: 텔레그램 메시지 한 건의 상한. 넘기면 400 "message is too long" 으로 통째로 거절된다.
MAX_MESSAGE_LENGTH = 4096
#: 분할할 때 쓰는 여유 있는 길이.
SPLIT_AT = 4000
#: 429 의 retry_after 가 이보다 길면 기다리지 않고 실패로 돌려 다음 회차에 맡긴다.
MAX_RETRY_AFTER = 60.0


def split_message(text: str, limit: int = SPLIT_AT) -> list[str]:
    """줄 단위로 끊어 limit 이하 조각으로 나눈다. 한 줄이 limit 보다 길면 그 줄을 자른다."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class Notifier(Protocol):
    """알림 채널. 텔레그램 외에 카카오 등을 붙일 때 이 모양을 지킨다."""

    async def send(self, chat_id: str, text: str) -> bool: ...


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not bot_token.strip():
            raise ValueError(
                "텔레그램 봇 토큰이 없습니다. @BotFather에서 발급받아 "
                ".env의 TELEGRAM_BOT_TOKEN에 넣어주세요."
            )
        self.bot_token = bot_token
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._sleep = sleep

    async def __aenter__(self) -> TelegramNotifier:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_updates(self, offset: int = 0, timeout: int = 25) -> list[dict] | None:
        """롱폴링으로 들어온 메시지를 받는다. 실패하면 None — 빈 목록과 구분해야 한다.

        webhook이 아니라 롱폴링을 쓰는 이유는 공인 IP도 TLS 인증서도 필요 없어서다.
        검증 단계에서 인프라를 늘릴 이유가 없다.

        실패를 []로 돌려주면 호출한 쪽이 "조용한 하루"로 알고 쉬지 않고 다시 묻는다.
        토큰이 틀렸거나(401) 봇을 두 곳에서 띄웠을 때(409) 초당 수백 번 때리게 된다.
        """
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        try:
            resp = await self._client.get(
                url,
                params={"offset": offset, "timeout": timeout},
                timeout=timeout + 10,
            )
        except httpx.HTTPError:
            logger.warning("[telegram] getUpdates 실패", exc_info=True)
            return None

        if resp.status_code != 200:
            logger.error("[telegram] getUpdates 거절 status=%s", resp.status_code)
            return None
        payload = resp.json()
        if not payload.get("ok"):
            return None
        return payload.get("result", [])

    async def me(self) -> dict | None:
        """봇 자신의 정보. username을 채널 안내 문구에 쓴다."""
        url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        payload = resp.json()
        return payload.get("result") if payload.get("ok") else None

    async def send(self, chat_id: str, text: str) -> bool:
        """보냈으면 True. 실패해도 예외를 올리지 않는다 — 알림 하나 때문에
        워커 전체가 멈추면 나머지 종목의 공시를 놓친다.

        4096자를 넘으면 줄 단위로 나눠 여러 건으로 보낸다. 한 조각이라도 실패하면 False.
        """
        for chunk in split_message(text):
            if not await self._send_one(chat_id, chunk):
                return False
        return True

    async def _send_one(self, chat_id: str, text: str, retried: bool = False) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            resp = await self._client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        except httpx.HTTPError:
            logger.exception("[telegram] 전송 실패 chat_id=%s", chat_id)
            return False

        if resp.status_code == 429 and not retried:
            # 같은 방에 분당 20건을 넘기면 온다. retry_after 만큼 쉬고 한 번만 다시 보낸다.
            # 무시하고 계속 때리면 텔레그램이 차단 시간을 늘린다.
            retry_after = _retry_after(resp)
            if retry_after is not None and retry_after <= MAX_RETRY_AFTER:
                logger.warning("[telegram] 429 chat_id=%s — %.0f초 후 재시도", chat_id, retry_after)
                await self._sleep(retry_after)
                return await self._send_one(chat_id, text, retried=True)

        if resp.status_code != 200:
            logger.error(
                "[telegram] 전송 거절 chat_id=%s status=%s body=%s",
                chat_id, resp.status_code, resp.text[:200],
            )
            return False
        return bool(resp.json().get("ok"))


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        value = resp.json().get("parameters", {}).get("retry_after")
    except ValueError:
        value = None
    if value is None:
        value = resp.headers.get("Retry-After")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class ConsoleNotifier:
    """토큰 없이 워커를 돌려볼 때 쓰는 채널. 터미널에 찍기만 한다."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> bool:
        self.sent.append((chat_id, text))
        print(f"\n─── to {chat_id} ───\n{text}\n")
        return True
