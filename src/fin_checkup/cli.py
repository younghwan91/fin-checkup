"""터미널에서 쓰는 최소 진입점.

    python -m fin_checkup.cli sync            # corp_code 매핑 갱신 (최초 1회 필수)
    python -m fin_checkup.cli checkup 005930  # 국내 건강검진
    python -m fin_checkup.cli us AAPL         # 미국 건강검진 (SEC EDGAR)
    python -m fin_checkup.cli watch add 005930
    python -m fin_checkup.cli poll --dry-run  # 위험 공시 확인
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import timedelta

from fin_checkup.alerts.bot import TelegramBot
from fin_checkup.alerts.classify import Severity
from fin_checkup.alerts.scheduler import AlertScheduler
from fin_checkup.alerts.telegram import ConsoleNotifier, Notifier, TelegramNotifier
from fin_checkup.alerts.worker import AlertWorker
from fin_checkup.config import settings
from fin_checkup.format import format_metric
from fin_checkup.metrics.engine import Category, checkup
from fin_checkup.metrics.redflags import detect_red_flags
from fin_checkup.metrics.signals import Signal
from fin_checkup.sec.client import SecClient
from fin_checkup.service import CheckupService
from fin_checkup.storage import Cache, today_key
from fin_checkup.storage.db import CacheLocked

DISCLAIMER = (
    "본 결과는 공시 수치를 공개된 재무비율 공식으로 계산한 측정값입니다. "
    "투자권유·자문이 아니며, 데이터 정확성과 이용에 따른 손익에 책임지지 않습니다."
)


def _format(metric, currency: str = "KRW") -> str:
    return format_metric(metric, currency)


async def cmd_sync(args: argparse.Namespace) -> int:
    if not settings.has_api_key:
        print("DART_API_KEY가 없습니다. https://opendart.fss.or.kr 에서 발급 후 .env에 넣어주세요.")
        return 1
    with Cache(settings.fin_checkup_db_path) as cache:
        service = CheckupService(cache)
        count = await service.ensure_corp_codes(force=args.force)
    print(f"corp_code {count}건 갱신" if count else "corp_code 매핑이 이미 최신입니다.")
    return 0


async def cmd_collect(args: argparse.Namespace) -> int:
    """업종 비교용으로 여러 기업의 재무를 미리 캐시에 채운다.

    기업 1곳당 호출은 기업개황 1회 + 재무제표 1~2회다. DART 일별 허용량이 있으니
    --limit으로 나눠서 돌리는 걸 전제로 만들었다.
    """
    if not settings.has_api_key:
        print("DART_API_KEY가 없습니다.")
        return 1

    with Cache(settings.fin_checkup_db_path) as cache:
        service = CheckupService(cache)
        await service.ensure_corp_codes()

        targets = (
            service.search(args.query) if args.query else cache.search_by_name("", limit=args.limit)
        )
        if not targets:
            print("대상 기업이 없습니다. 먼저 sync를 실행하세요.")
            return 1

        done = skipped = 0
        for i, corp in enumerate(targets, start=1):
            if cache.get_statements(corp.corp_code, args.year) is not None:
                skipped += 1
                continue
            fin = await service.get_financials(corp.corp_code, args.year)
            await service.ensure_company(corp.corp_code)
            done += 1
            status = "○" if fin is None else "●"
            print(f"[{i}/{len(targets)}] {status} {corp.corp_name}", flush=True)

        print(f"\n수집 {done}건, 캐시에 이미 있어 건너뜀 {skipped}건")
    return 0


async def cmd_checkup(args: argparse.Namespace) -> int:
    with Cache(settings.fin_checkup_db_path) as cache:
        service = CheckupService(cache)
        await service.ensure_corp_codes()

        matches = service.search(args.query)
        if not matches:
            print(f"'{args.query}'에 해당하는 상장기업을 찾지 못했습니다. 먼저 sync를 실행했나요?")
            return 1
        corp = matches[0]

        data = await service.run(corp, end_year=args.year, years=args.years)
        if data is None:
            print(f"{corp.corp_name}의 {args.year}년 재무제표를 찾지 못했습니다.")
            return 1

        latest = data.history[-1]
        _print_checkup(
            f"{corp.corp_name} ({corp.stock_code}) — {latest.bsns_year}년",
            f"{latest.reprt_code.label} · "
            f"{'연결' if latest.fs_div.value == 'CFS' else '개별'}재무제표 기준",
            data.result,
            data.red_flags,
            data.result.currency,
        )
    return 0


def _print_checkup(title: str, subtitle: str, result, red_flags, currency: str) -> None:
    print(f"\n{title}")
    print("=" * 64)
    print(subtitle)

    for category in Category:
        metrics = result.by_category(category)
        if not metrics:
            continue
        print(f"\n[{category.value}]")
        for m in metrics:
            note = f"  — {m.note}" if m.note else ""
            print(f"  {m.signal.emoji} {m.label:<20} {_format(m, currency):>18}{note}")

    if red_flags:
        print("\n[위험 신호]")
        for flag in red_flags:
            print(f"  🔴 {flag.label}: {flag.detail}")
            if flag.reference:
                print(f"     ↳ {flag.reference}")

    counts = result.counts
    print(
        f"\n요약: 🟢{counts[Signal.GREEN]} 🟡{counts[Signal.YELLOW]} "
        f"🔴{counts[Signal.RED]} ⚪{counts[Signal.NEUTRAL]} ⚫{counts[Signal.UNKNOWN]}"
    )
    print(f"\n{DISCLAIMER}")


async def cmd_us(args: argparse.Namespace) -> int:
    """SEC EDGAR로 미국 상장기업 건강검진 (Phase 2)."""
    if not settings.has_sec_user_agent:
        print(
            "SEC_USER_AGENT가 없습니다. SEC는 연락처가 담긴 User-Agent를 요구합니다.\n"
            "  .env에 SEC_USER_AGENT='fin-checkup your@email.com' 형태로 넣어주세요."
        )
        return 1

    with Cache(settings.fin_checkup_db_path) as cache:
        async with SecClient() as client:
            age = cache.sec_tickers_age()
            if age is None or age > timedelta(days=settings.corp_code_ttl_days):
                print("SEC 티커 목록을 받는 중…")
                tickers = await client.fetch_tickers()
                cache.save_sec_tickers([(t.cik, t.ticker, t.title) for t in tickers])

            found = cache.find_cik(args.ticker)
            if found is None:
                print(f"'{args.ticker}' 티커를 찾지 못했습니다.")
                return 1
            cik, title = found

            history = await client.fetch_history(cik, end_year=args.year, years=args.years)

    if not history:
        print(f"{title}의 {args.year}년 10-K를 찾지 못했습니다.")
        return 1

    latest = history[-1]
    prior = next((f for f in history if f.bsns_year == latest.bsns_year - 1), None)
    result = checkup(latest, prior)

    _print_checkup(
        f"{title} ({args.ticker.upper()}) — FY{latest.bsns_year}",
        f"SEC EDGAR 10-K 기준 · CIK {cik}",
        result,
        detect_red_flags(history),
        latest.currency,
    )
    return 0


async def cmd_watch(args: argparse.Namespace) -> int:
    """관심종목 등록·조회·해제."""
    with Cache(settings.fin_checkup_db_path) as cache:
        service = CheckupService(cache)

        if args.action == "list":
            watched = cache.list_watch(args.chat)
            if not watched:
                print(f"{args.chat}에 등록된 관심종목이 없습니다.")
                return 0
            for corp in watched:
                print(f"  {corp.corp_name} ({corp.stock_code})")
            return 0

        if not args.query:
            print("종목코드나 회사명을 지정하세요.")
            return 1

        await service.ensure_corp_codes()
        matches = service.search(args.query)
        if not matches:
            print(f"'{args.query}'에 해당하는 상장기업을 찾지 못했습니다.")
            return 1
        corp = matches[0]

        if args.action == "add":
            added = cache.add_watch(args.chat, corp)
            print(f"{'등록' if added else '이미 등록됨'}: {corp.corp_name} ({corp.stock_code})")
        else:
            removed = cache.remove_watch(args.chat, corp.corp_code)
            print(f"{'해제' if removed else '등록돼 있지 않음'}: {corp.corp_name}")
    return 0


async def cmd_poll(args: argparse.Namespace) -> int:
    """관심종목의 최근 위험 공시를 훑어 알림을 보낸다."""
    if not settings.has_api_key:
        print("DART_API_KEY가 없습니다.")
        return 1

    notifier: Notifier
    if args.dry_run or not settings.has_telegram:
        if not args.dry_run:
            print("TELEGRAM_BOT_TOKEN이 없어 콘솔로 출력합니다.\n")
        notifier = ConsoleNotifier()
    else:
        notifier = TelegramNotifier(settings.telegram_bot_token)

    min_severity = Severity(args.min_severity) if args.min_severity else None

    with Cache(settings.fin_checkup_db_path) as cache:
        worker = AlertWorker(cache, notifier, min_severity=min_severity)

        if args.daemon:
            scheduler = AlertScheduler(cache, worker, interval_seconds=args.interval)
            print(
                f"{args.interval / 60:.0f}분 간격으로 감시합니다. Ctrl+C로 중지.\n"
                "마지막 성공 시점부터 조회하므로 잠깐 멈춰도 그 사이 공시를 놓치지 않습니다."
            )
            try:
                await scheduler.run_forever()
            except KeyboardInterrupt:
                print("\n중지했습니다.")
        else:
            scheduler = AlertScheduler(cache, worker)
            days = args.days if args.days else scheduler.lookback_days()
            report = await worker.poll(days=days)
            scheduler.mark_polled()
            print(f"{days}일치 확인 — {report.summary()}")

    if isinstance(notifier, TelegramNotifier):
        await notifier.aclose()
    return 0


async def cmd_bot(args: argparse.Namespace) -> int:
    """텔레그램 봇과 채널 브로드캐스트를 함께 돌린다.

    한 프로세스에서 도는 이유는 DuckDB가 단일 writer라서다. 봇(관심종목 등록)과
    채널(시장 전체 CRITICAL)이 DART 수집을 공유해 호출량도 아낀다.
    """
    if not settings.has_telegram:
        print(
            "TELEGRAM_BOT_TOKEN이 없습니다. @BotFather에서 발급받아 .env에 넣어주세요.\n"
            "  1) 텔레그램에서 @BotFather 검색 → /newbot\n"
            "  2) 받은 토큰을 .env의 TELEGRAM_BOT_TOKEN에 붙여넣기"
        )
        return 1
    if not settings.has_api_key:
        print("DART_API_KEY가 없습니다.")
        return 1

    notifier = TelegramNotifier(settings.telegram_bot_token)
    info = await notifier.me()
    username = (info or {}).get("username", "")
    if not info:
        print("텔레그램 봇 토큰을 확인할 수 없습니다. 토큰이 맞는지 확인해주세요.")
        await notifier.aclose()
        return 1

    print(f"봇 @{username} 로 실행합니다.")
    if args.channel:
        print(f"채널 {args.channel} 에 CRITICAL 공시를 {args.interval / 60:.0f}분마다 보냅니다.")
    print("Ctrl+C로 중지.\n")

    with Cache(settings.fin_checkup_db_path) as cache:
        service = CheckupService(cache)
        await service.ensure_corp_codes()

        bot = TelegramBot(cache, notifier, service)
        worker = AlertWorker(cache, notifier)
        scheduler = AlertScheduler(cache, worker, interval_seconds=args.interval)

        async def poll_disclosures() -> None:
            """관심종목 알림 + 채널 브로드캐스트."""
            while True:
                await scheduler.run_once()
                if args.channel:
                    try:
                        report = await worker.broadcast(
                            args.channel,
                            days=scheduler.lookback_days(),
                            bot_username=username,
                        )
                        if report.sent:
                            print(f"[채널] {report.summary()}", flush=True)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[채널] 실패: {exc}", flush=True)
                await asyncio.sleep(args.interval)

        try:
            await asyncio.gather(bot.run_forever(), poll_disclosures())
        except KeyboardInterrupt:
            print("\n중지했습니다.")

    await notifier.aclose()
    return 0


async def cmd_serve(args: argparse.Namespace) -> int:
    """HTTP API 서버를 띄운다.

    이 프로세스가 캐시를 소유하고 알림 워커를 안에서 함께 돌린다. DuckDB가 단일
    writer라 워커를 따로 띄우면 락에서 부딪히기 때문이다.
    """
    try:
        import uvicorn

        from fin_checkup.api import create_app
    except ImportError:
        print("API 의존성이 없습니다. `uv sync --all-extras`로 설치하세요.")
        return 1

    app = create_app(run_worker=not args.no_worker)
    print(
        f"http://{args.host}:{args.port}/docs 에서 API 문서를 볼 수 있습니다.\n"
        f"알림 워커: {'꺼짐' if args.no_worker else '켜짐 (서버 안에서 함께 실행)'}"
    )
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
    await uvicorn.Server(config).serve()
    return 0


async def cmd_backfill(args: argparse.Namespace) -> int:
    """캐시에 있는 재무로 지표 값을 미리 계산해둔다.

    대조군을 매 조회마다 원본에서 다시 계산하면 12만 줄을 파싱하게 된다. 한 번
    채워두면 조회는 집계 쿼리 한 방으로 끝난다. DART 호출은 하지 않는다.
    """
    with Cache(settings.fin_checkup_db_path) as cache:
        service = CheckupService(cache)
        for year in range(args.year - args.years + 1, args.year + 1):
            before = cache.metric_values_count(year)
            done = service.backfill_metric_values(year)
            print(f"  {year}년: {done:,}사 계산 (이전 {before:,}사)")
    return 0


async def cmd_budget(args: argparse.Namespace) -> int:
    """오늘 DART를 몇 번 호출했는지."""
    with Cache(settings.fin_checkup_db_path) as cache:
        service = CheckupService(cache)
        used, quota = service.dart_budget()
        by_endpoint = cache.dart_calls_by_endpoint(today_key())

    pct = used / quota * 100 if quota else 0
    print(f"\n오늘 DART 호출: {used:,} / {quota:,} ({pct:.1f}%)")
    if by_endpoint:
        print("=" * 40)
        for endpoint, count in by_endpoint.items():
            print(f"  {endpoint:<28} {count:>7,}")
    if pct >= 80:
        print("\n⚠️ 일별 허용량의 80%를 넘었습니다. 캐시에 없는 조회를 줄이세요.")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(prog="fin-checkup", description="DART 재무 건강검진")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="corp_code 매핑 갱신")
    p_sync.add_argument("--force", action="store_true", help="TTL을 무시하고 강제 갱신")
    p_sync.set_defaults(func=cmd_sync)

    p_collect = sub.add_parser("collect", help="업종 비교용으로 여러 기업 재무를 미리 수집")
    p_collect.add_argument("--year", type=int, default=2024, help="수집할 사업연도")
    p_collect.add_argument("--limit", type=int, default=50, help="한 번에 수집할 기업 수")
    p_collect.add_argument("--query", default="", help="특정 이름으로 한정 (예: 삼성)")
    p_collect.set_defaults(func=cmd_collect)

    p_check = sub.add_parser("checkup", help="종목코드 또는 회사명으로 건강검진")
    p_check.add_argument("query", help="종목코드(6자리) 또는 회사명")
    p_check.add_argument("--year", type=int, default=2024, help="기준 사업연도")
    p_check.add_argument("--years", type=int, default=5, help="추이를 볼 연수")
    p_check.set_defaults(func=cmd_checkup)

    p_us = sub.add_parser("us", help="미국 상장기업 건강검진 (SEC EDGAR)")
    p_us.add_argument("ticker", help="티커 (예: AAPL)")
    p_us.add_argument("--year", type=int, default=2024, help="기준 회계연도")
    p_us.add_argument("--years", type=int, default=5, help="추이를 볼 연수")
    p_us.set_defaults(func=cmd_us)

    p_watch = sub.add_parser("watch", help="관심종목 등록·조회·해제")
    p_watch.add_argument("action", choices=["add", "remove", "list"])
    p_watch.add_argument("query", nargs="?", default="", help="종목코드 또는 회사명")
    p_watch.add_argument("--chat", default="local", help="알림 대상 (텔레그램 chat_id 등)")
    p_watch.set_defaults(func=cmd_watch)

    p_poll = sub.add_parser("poll", help="관심종목의 최근 위험 공시를 확인하고 알림")
    p_poll.add_argument(
        "--days", type=int, default=0,
        help="최근 며칠치를 볼지 (0이면 마지막 성공 시점부터 자동 계산)",
    )
    p_poll.add_argument(
        "--min-severity", choices=["medium", "high", "critical"], default=None,
        help="이 강도 이상만 알림 (지정하지 않으면 전 강도)",
    )
    p_poll.add_argument("--dry-run", action="store_true", help="보내지 않고 콘솔에만 출력")
    p_poll.add_argument("--daemon", action="store_true", help="주기적으로 계속 감시")
    p_poll.add_argument("--interval", type=float, default=1800, help="감시 주기(초)")
    p_poll.set_defaults(func=cmd_poll)

    p_backfill = sub.add_parser("backfill", help="대조군용 지표 값 미리 계산 (DART 호출 없음)")
    p_backfill.add_argument("--year", type=int, default=2024, help="기준 사업연도")
    p_backfill.add_argument("--years", type=int, default=2, help="거슬러 올라갈 연수")
    p_backfill.set_defaults(func=cmd_backfill)

    p_budget = sub.add_parser("budget", help="오늘 DART 호출량 확인")
    p_budget.set_defaults(func=cmd_budget)

    p_bot = sub.add_parser("bot", help="텔레그램 봇 + 채널 브로드캐스트 실행")
    p_bot.add_argument(
        "--channel", default="",
        help="CRITICAL 공시를 뿌릴 채널 (예: @fincheckup). 없으면 봇만 실행",
    )
    p_bot.add_argument("--interval", type=float, default=1800, help="공시 확인 주기(초)")
    p_bot.set_defaults(func=cmd_bot)

    p_serve = sub.add_parser("serve", help="HTTP API 서버 실행 (알림 워커 포함)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--no-worker", action="store_true", help="알림 워커 없이 API만")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    try:
        return asyncio.run(args.func(args))
    except CacheLocked as exc:
        print(f"\n{exc}")
        return 1
    except KeyboardInterrupt:
        print("\n중지했습니다.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
