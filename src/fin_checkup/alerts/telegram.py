"""텔레그램 봇 전송.

봇 토큰은 @BotFather에서 발급받는다. 무료이고 즉시 된다.
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    """알림 채널. 텔레그램 외에 카카오 등을 붙일 때 이 모양을 지킨다."""

    async def send(self, chat_id: str, text: str) -> bool: ...


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ) -> None:
        if not bot_token.strip():
            raise ValueError(
                "텔레그램 봇 토큰이 없습니다. @BotFather에서 발급받아 "
                ".env의 TELEGRAM_BOT_TOKEN에 넣어주세요."
            )
        self.bot_token = bot_token
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

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

    async def get_updates(self, offset: int = 0, timeout: int = 25) -> list[dict]:
        """롱폴링으로 들어온 메시지를 받는다.

        webhook이 아니라 롱폴링을 쓰는 이유는 공인 IP도 TLS 인증서도 필요 없어서다.
        검증 단계에서 인프라를 늘릴 이유가 없다.
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
            return []

        if resp.status_code != 200:
            logger.error("[telegram] getUpdates 거절 status=%s", resp.status_code)
            return []
        payload = resp.json()
        return payload.get("result", []) if payload.get("ok") else []

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
        워커 전체가 멈추면 나머지 종목의 공시를 놓친다."""
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

        if resp.status_code != 200:
            logger.error(
                "[telegram] 전송 거절 chat_id=%s status=%s body=%s",
                chat_id, resp.status_code, resp.text[:200],
            )
            return False
        return bool(resp.json().get("ok"))


class ConsoleNotifier:
    """토큰 없이 워커를 돌려볼 때 쓰는 채널. 터미널에 찍기만 한다."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> bool:
        self.sent.append((chat_id, text))
        print(f"\n─── to {chat_id} ───\n{text}\n")
        return True
