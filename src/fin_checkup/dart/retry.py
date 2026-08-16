"""DART 호출 재시도와 호출량 추적.

운영에서 이 계층이 없으면 두 가지로 죽는다. 네트워크가 잠깐 끊겼을 뿐인데 워커
전체가 멈추거나, 일별 허용량을 모르는 채 계속 때려서 하루치를 태워버린다.

재시도 원칙: **일시적 실패만 다시 시도한다.** 인증키가 틀렸거나 없는 데이터를
달라고 한 건 몇 번을 다시 해도 같다. 그런 걸 재시도하면 남은 호출량만 태운다.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: 다시 시도해볼 만한 DART status 코드.
#: 020 요청제한초과 · 021 조회건수초과 · 800 시스템점검 · 900/901 정의되지 않은 오류
RETRYABLE_STATUSES = frozenset({"020", "021", "800", "900", "901"})

#: 다시 시도해도 소용없는 것. 키가 틀렸거나 요청 자체가 잘못됐다.
FATAL_STATUSES = frozenset({"010", "011", "012", "013", "014", "100", "101"})

#: 다시 시도해볼 만한 HTTP 상태.
RETRYABLE_HTTP = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0
    #: 여러 워커가 동시에 재시도해 몰리지 않게 흔들어준다.
    jitter: float = 0.3

    def delay_for(self, attempt: int, rng: random.Random | None = None) -> float:
        """attempt는 1부터. 지수 백오프에 지터를 더한다."""
        raw = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        spread = raw * self.jitter
        offset = (rng or random).uniform(-spread, spread)
        return max(0.0, raw + offset)


class RateLimitExceeded(RuntimeError):
    """일별 호출 허용량을 넘었다. 재시도해도 오늘은 풀리지 않는다."""


def is_retryable(exc: BaseException) -> bool:
    from fin_checkup.dart.client import DartError

    if isinstance(exc, DartError):
        return exc.status in RETRYABLE_STATUSES
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_HTTP
    # 연결 실패·타임아웃은 거의 항상 일시적이다.
    return isinstance(exc, httpx.TransportError)


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    policy: RetryPolicy | None = None,
    context: str = "",
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> T:
    """일시적 실패면 지수 백오프로 다시 시도한다."""
    policy = policy or RetryPolicy()
    last: BaseException | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await operation()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not is_retryable(exc):
                raise
            if attempt >= policy.max_attempts:
                break
            delay = policy.delay_for(attempt, rng)
            logger.warning(
                "[retry] %s 실패(%s) — %.1f초 후 재시도 %d/%d",
                context or "요청", exc, delay, attempt + 1, policy.max_attempts,
            )
            await sleep(delay)

    assert last is not None
    logger.error("[retry] %s 재시도 %d회 모두 실패", context or "요청", policy.max_attempts)
    raise last
