"""위험 공시 감시 워커.

관심종목으로 등록된 기업의 최근 공시를 훑어 위험 공시만 골라 알린다.

호출 절약이 설계의 중심이다. 관심종목이 100개라고 100번 호출하지 않는다 —
기간으로 한 번에 긁어 corp_code로 거를 수 있으면 그렇게 한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from fin_checkup.alerts.classify import RiskDisclosure, Severity, classify_all
from fin_checkup.alerts.message import format_alert, format_channel_alert
from fin_checkup.alerts.telegram import Notifier
from fin_checkup.config import Settings
from fin_checkup.config import settings as default_settings
from fin_checkup.dart.client import DartClient
from fin_checkup.models import PblntfTy
from fin_checkup.storage import Cache

logger = logging.getLogger(__name__)

#: 공시검색을 이 유형들로 나눠 호출한다. 정기공시(A)는 위험 공시가 아니라 뺀다.
WATCH_TYPES: tuple[PblntfTy, ...] = (
    PblntfTy.MAJOR,  # B 주요사항보고 — 유상증자·CB/BW·부도·회생
    PblntfTy.ISSUANCE,  # C 발행공시
    PblntfTy.OWNERSHIP,  # D 지분공시 — 최대주주·대량보유
    PblntfTy.AUDIT,  # F 외부감사 — 감사보고서
    PblntfTy.EXCHANGE,  # I 거래소공시 — 관리종목·상장폐지
)


def _collapse_same_event(
    risks: list[RiskDisclosure],
) -> tuple[list[RiskDisclosure], dict[str, list[str]]]:
    """한 사건이 여러 공시로 쪼개져 오는 경우를 하나로 묶는다.

    실제로 이런 게 나왔다 — 퓨쳐메디신의 '주권매매거래정지(지정자문인 선임계약 해지)'와
    '기타시장안내(지정자문인 선임계약 해지에 따른 상장폐지절차 안내)'. 같은 사건인데
    채널에는 두 번 나간다.

    같은 회사·같은 날·같은 종류면 접수번호가 빠른 것 하나만 남긴다. 종류가 다르면
    (유상증자와 감사의견처럼) 별개 사건이므로 묶지 않는다.

    반환값의 두 번째는 {대표 접수번호: [묶여서 빠진 접수번호들]}이다. 대표를 보낸 뒤
    이것들도 함께 발송 기록에 남겨야 한다 — 안 그러면 다음 회차에 남은 게 대표가 돼서
    결국 같은 사건이 두 번 나간다.
    """
    kept: dict[tuple[str, str, str], RiskDisclosure] = {}
    absorbed: dict[str, list[str]] = {}
    for risk in sorted(risks, key=lambda r: r.disclosure.rcept_no):
        d = risk.disclosure
        key = (d.corp_code, d.rcept_dt, risk.kind.value)
        leader = kept.setdefault(key, risk)
        if leader is not risk:
            absorbed.setdefault(leader.disclosure.rcept_no, []).append(d.rcept_no)
    return sorted(kept.values(), key=lambda r: r.disclosure.rcept_no), absorbed


@dataclass
class PollReport:
    """한 번 훑은 결과. 워커가 무엇을 했는지 그대로 보고한다."""

    scanned: int = 0
    risky: int = 0
    sent: int = 0
    skipped_duplicate: int = 0
    failed: int = 0
    by_chat: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"공시 {self.scanned}건 확인 · 위험 {self.risky}건 · "
            f"발송 {self.sent}건 · 중복 제외 {self.skipped_duplicate}건 · 실패 {self.failed}건"
        )


class AlertWorker:
    def __init__(
        self,
        cache: Cache,
        notifier: Notifier,
        settings: Settings | None = None,
        min_severity: Severity | None = None,
    ) -> None:
        self.cache = cache
        self.notifier = notifier
        self.settings = settings or default_settings
        #: 관심종목 알림의 하한. 기본은 걸지 않는다 — 등록한 종목이면 다 보낸다.
        self.min_severity = min_severity or Severity.MEDIUM

    async def collect(
        self,
        days: int,
        today: date | None,
        report: PollReport,
        only_corp_codes: set[str] | None = None,
    ) -> list[RiskDisclosure] | None:
        """기간 내 위험 공시를 모은다. 인증키가 없으면 None.

        관심종목 알림과 채널 브로드캐스트가 이 수집을 공유한다. 둘이 각자 DART를
        긁으면 호출량이 두 배가 된다.
        """
        if not self.settings.has_api_key:
            logger.warning("[worker] DART 인증키가 없어 공시를 조회할 수 없다")
            return None

        end = today or date.today()
        begin = end - timedelta(days=max(days - 1, 0))
        bgn_de, end_de = begin.strftime("%Y%m%d"), end.strftime("%Y%m%d")

        # 한 공시가 둘 이상의 공시유형 조회에 함께 잡힐 수 있다. 접수번호로 한 번만 남긴다.
        seen: set[str] = set()
        risks: list[RiskDisclosure] = []
        async with DartClient(settings=self.settings) as client:
            for pblntf_ty in WATCH_TYPES:
                found = await client.search_disclosures(
                    bgn_de=bgn_de, end_de=end_de, pblntf_ty=pblntf_ty
                )
                report.scanned += len(found)
                if only_corp_codes is not None:
                    found = [d for d in found if d.corp_code in only_corp_codes]
                for risk in classify_all(found):
                    rcept_no = risk.disclosure.rcept_no
                    if rcept_no in seen:
                        continue
                    seen.add(rcept_no)
                    risks.append(risk)
        return risks

    async def broadcast(
        self,
        channel_id: str,
        days: int = 1,
        today: date | None = None,
        min_severity: Severity = Severity.CRITICAL,
        max_per_run: int = 30,
        bot_username: str = "",
    ) -> PollReport:
        """관심종목과 무관하게 시장 전체의 위험 공시를 채널 하나로 보낸다.

        실측하면 위험 공시는 하루 145건이고 그중 78%가 임원·대주주 소유 변동이다.
        그건 '내 종목일 때만' 의미가 있어서 채널에는 CRITICAL만 내보낸다(하루 ~10건).

        max_per_run은 폭주 방지다. 며칠 밀렸다가 한꺼번에 수백 건이 나가면 채널이
        죽는다 — 넘치면 오래된 것부터 보내고 나머지는 다음 회차로 넘긴다.
        """
        report = PollReport()
        risks = await self.collect(days, today, report)
        if risks is None:
            return report

        eligible = [r for r in risks if r.severity.rank >= min_severity.rank]
        report.risky = len(eligible)

        fresh = [
            r
            for r in eligible
            if not self.cache.was_notified(channel_id, r.disclosure.rcept_no)
        ]
        fresh, absorbed = _collapse_same_event(fresh)
        report.skipped_duplicate = len(eligible) - len(fresh)

        # 오래된 것부터 — 밀린 구간이 시간순으로 복구된다.
        fresh.sort(key=lambda r: r.disclosure.rcept_no)
        if len(fresh) > max_per_run:
            logger.warning(
                "[broadcast] 대상 %d건이 한 회차 상한(%d)을 넘어 나머지는 다음 회차로 넘긴다",
                len(fresh), max_per_run,
            )
            fresh = fresh[:max_per_run]

        for risk in fresh:
            ok = await self.notifier.send(
                channel_id, format_channel_alert(risk, bot_username)
            )
            if ok:
                rcept_no = risk.disclosure.rcept_no
                self.cache.mark_notified(channel_id, rcept_no)
                # 같은 사건으로 묶여 빠진 것들도 발송된 것으로 친다.
                for absorbed_no in absorbed.get(rcept_no, []):
                    self.cache.mark_notified(channel_id, absorbed_no)
                report.sent += 1
                report.by_chat[channel_id] = report.by_chat.get(channel_id, 0) + 1
            else:
                report.failed += 1

        logger.info("[broadcast] %s", report.summary())
        return report

    async def poll(self, days: int = 1, today: date | None = None) -> PollReport:
        """최근 N일치 공시를 훑어 알림을 보낸다."""
        report = PollReport()
        watchers = self.cache.all_watchers()
        if not watchers:
            logger.info("[worker] 관심종목이 등록된 대상이 없다")
            return report

        watched = set(self.cache.watched_corp_codes())
        risks = await self.collect(days, today, report, only_corp_codes=watched)
        if risks is None:
            return report

        risks_by_corp: dict[str, list[RiskDisclosure]] = {}
        for risk in risks:
            risks_by_corp.setdefault(risk.disclosure.corp_code, []).append(risk)
        report.risky = len(risks)

        floor = self.min_severity
        for chat_id, corps in watchers.items():
            for corp in corps:
                for risk in risks_by_corp.get(corp.corp_code, []):
                    if risk.severity.rank < floor.rank:
                        continue
                    rcept_no = risk.disclosure.rcept_no
                    if self.cache.was_notified(chat_id, rcept_no):
                        report.skipped_duplicate += 1
                        continue
                    ok = await self.notifier.send(chat_id, format_alert(risk))
                    if ok:
                        self.cache.mark_notified(chat_id, rcept_no)
                        report.sent += 1
                        report.by_chat[chat_id] = report.by_chat.get(chat_id, 0) + 1
                    else:
                        # 실패한 건 기록하지 않는다 — 다음 폴링에서 다시 시도한다.
                        report.failed += 1

        logger.info("[worker] %s", report.summary())
        return report
