"""알림 워커를 주기적으로 돌린다.

운영에서 필요한 건 세 가지다. 죽지 않고 계속 돌 것, 한 번 실패했다고 멈추지 말 것,
그리고 **놓친 구간이 없을 것.**

마지막 세 번째가 중요하다. 프로세스가 두 시간 죽어 있었다면 그 두 시간 사이 공시를
건너뛰면 안 된다. 그래서 "최근 N일"이 아니라 "마지막으로 성공한 시점부터"를 조회한다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from fin_checkup.alerts.worker import AlertWorker, PollReport
from fin_checkup.storage import Cache

logger = logging.getLogger(__name__)

LAST_POLL_KEY = "alerts.last_poll"
#: 마지막 성공 기록이 없거나 너무 오래됐을 때 거슬러 올라갈 최대 일수.
#: 공시검색은 corp_code 없이 조회하면 3개월로 제한된다.
MAX_LOOKBACK_DAYS = 30
#: 공시가 늦게 반영되는 경우를 대비해 겹쳐서 조회한다. 중복은 notified가 막는다.
OVERLAP_DAYS = 1


class AlertScheduler:
    def __init__(
        self,
        cache: Cache,
        worker: AlertWorker,
        interval_seconds: float = 1800.0,
    ) -> None:
        self.cache = cache
        self.worker = worker
        self.interval_seconds = interval_seconds
        self.consecutive_failures = 0

    # ------------------------------------------------------------------
    # 놓친 구간 계산
    # ------------------------------------------------------------------

    def lookback_days(self, now: date | None = None) -> int:
        """마지막 성공 시점부터 며칠치를 조회해야 하는지.

        기록이 없으면 하루치만 본다. 처음 켠 워커가 한 달치 공시를 한꺼번에
        쏟아내면 그건 알림이 아니라 스팸이다.
        """
        today = now or date.today()
        last = self.cache.get_meta(LAST_POLL_KEY)
        if not last:
            return 1
        try:
            last_date = datetime.fromisoformat(last).date()
        except ValueError:
            logger.warning("[scheduler] 마지막 폴링 시각을 읽을 수 없다: %r", last)
            return 1

        gap = (today - last_date).days + OVERLAP_DAYS
        return max(1, min(gap, MAX_LOOKBACK_DAYS))

    def mark_polled(self, when: datetime | None = None) -> None:
        self.cache.set_meta(LAST_POLL_KEY, (when or datetime.now()).isoformat())

    # ------------------------------------------------------------------
    # 한 번 실행
    # ------------------------------------------------------------------

    async def run_once(self, now: date | None = None) -> PollReport | None:
        """한 주기를 실행한다. 실패하면 None을 반환하고 기록은 갱신하지 않는다."""
        days = self.lookback_days(now)
        try:
            report = await self.worker.poll(days=days, today=now)
        except Exception:  # noqa: BLE001
            self.consecutive_failures += 1
            logger.exception(
                "[scheduler] 폴링 실패 (연속 %d회) — 마지막 성공 시점을 유지해 "
                "다음 실행에서 이 구간을 다시 본다",
                self.consecutive_failures,
            )
            return None

        # 성공했을 때만 갱신한다. 실패한 구간을 성공으로 표시하면 그 사이 공시가 영영 사라진다.
        self.mark_polled()
        self.consecutive_failures = 0
        logger.info("[scheduler] %d일치 확인 — %s", days, report.summary())
        return report

    # ------------------------------------------------------------------
    # 반복 실행
    # ------------------------------------------------------------------

    async def run_forever(self, max_cycles: int | None = None) -> int:
        """주기적으로 실행한다. max_cycles를 주면 그만큼만 돌고 멈춘다(테스트용)."""
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            await self.run_once()
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            await asyncio.sleep(self.interval_seconds)
        return cycles
