"""터미널에서 쓰는 최소 진입점.

    python -m fin_checkup.cli setup           # 설치 점검 + OpenDART 인증키 넣기 (--check 는 점검만)
    python -m fin_checkup.cli sync            # corp_code 매핑 갱신 (최초 1회 필수)
    python -m fin_checkup.cli checkup 005930  # 국내 건강검진
    python -m fin_checkup.cli us AAPL         # 미국 건강검진 (SEC EDGAR)
    python -m fin_checkup.cli watch add 005930
    python -m fin_checkup.cli poll --dry-run  # 위험 공시 확인
    python -m fin_checkup.cli site build      # 정적 사이트 (캐시만 읽음)
    python -m fin_checkup.cli site check site-out  # 빌드 산출물 가드 (면책·CSP·인증키 …)
    python -m fin_checkup.cli site links site-out  # 깨진 내부 링크 (외부 링크는 보고만)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

from fin_checkup.alerts.bot import TelegramBot
from fin_checkup.alerts.classify import Severity
from fin_checkup.alerts.scheduler import AlertScheduler
from fin_checkup.alerts.telegram import ConsoleNotifier, Notifier, TelegramNotifier
from fin_checkup.alerts.worker import AlertWorker
from fin_checkup.auth import generate_key, normalize_email, user_id_for
from fin_checkup.config import settings
from fin_checkup.dart.client import DartError, RateLimitExceeded
from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.fiscal import default_fiscal_year
from fin_checkup.format import format_metric
from fin_checkup.metrics.engine import Category, checkup
from fin_checkup.metrics.redflags import detect_red_flags
from fin_checkup.metrics.signals import Signal
from fin_checkup.sec.client import SecClient
from fin_checkup.service import CheckupService
from fin_checkup.storage import Cache, today_key
from fin_checkup.storage.db import CacheLocked


def _format(metric, currency: str = "KRW") -> str:
    return format_metric(metric, currency)


async def cmd_setup(args: argparse.Namespace) -> int:
    from fin_checkup.onboarding import run_setup

    return await run_setup(
        check=args.check,
        key_stdin=args.key_stdin,
        from_clipboard=args.from_clipboard,
        env_file=args.env_file,
        sec_user_agent=args.sec_user_agent,
        verify=not args.no_verify,
    )


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
    """상장사 중 재무가 없는 (회사, 연도)만 호출 예산 안에서 받는다 (collect.py).

    예산·일별 허용량(020)·시간에 걸려 멈춰도 받은 데까지는 캐시에 남고 0 으로 끝난다 — 다음 실행이
    이어받는다. 마지막 줄 `collect-summary {...}` 와 --summary-json 은 워크플로가 읽는다.
    """
    import json

    from fin_checkup.collect import STOP_ERRORS, collect

    if not settings.has_api_key:
        print("DART_API_KEY가 없습니다.")
        return 1

    with Cache(settings.fin_checkup_db_path) as cache:
        summary = await collect(
            cache,
            settings,
            budget=args.budget,
            years=args.years,
            max_seconds=args.max_minutes * 60 if args.max_minutes else None,
            limit=args.limit,
            progress=lambda line: print(line, flush=True),
        )

    reasons = {
        "budget": "호출 예산에 닿아 멈췄다",
        "quota": "DART 일별 호출 허용량(020)을 넘어 멈췄다",
        "time": "시간 제한에 닿아 멈췄다",
        "errors": "오류가 이어져 멈췄다",
    }
    print(
        f"\n수집 {summary.fetched:,} · 없음 {summary.empty:,} · 건너뜀(빈 응답 기록) {summary.skipped:,} · "
        f"실패 {summary.failed:,} · 기업개황 {summary.companies:,} · 남음 {summary.remaining:,} · "
        f"호출 {summary.calls:,}/{summary.budget:,}"
    )
    if summary.stopped:
        print(f"{reasons[summary.stopped]} — 받은 것은 캐시에 남았고, 다음 실행이 이어받는다.")
    data = summary.to_dict()
    line = json.dumps(data, ensure_ascii=False)
    if args.summary_json:
        Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary_json).write_text(line + "\n", encoding="utf-8")
    print(f"collect-summary {line}", flush=True)
    return 1 if summary.stopped == STOP_ERRORS else 0


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
        f"🔴{counts[Signal.RED]} ⚪{counts[Signal.NEUTRAL]} "
        f"⊘{counts[Signal.NOT_APPLICABLE]} ⚫{counts[Signal.UNKNOWN]}"
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


async def cmd_account(args: argparse.Namespace) -> int:
    """운영자가 API 키를 발급한다.

    /accounts 는 이메일을 검증하지 않아 같은 이메일로 두 번 만들 수 없다(409). 키를 잃은
    사용자는 API 로는 복구할 길이 없다 — DB 에 손이 닿는 운영자가 여기서 새 키를 준다.
    """
    try:
        email = normalize_email(args.email)
    except ValueError as exc:
        print(str(exc))
        return 1
    user_id = user_id_for(email)
    with Cache(settings.fin_checkup_db_path) as cache:
        created = cache.create_account(user_id, email)
        issued = generate_key(user_id)
        cache.save_api_key(issued.key_hash, user_id, issued.prefix)
    print(f"{'새 계정' if created else '기존 계정'} {user_id}")
    print(f"API 키 (지금 한 번만 표시): {issued.plaintext}")
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
        # 콘솔 출력은 발송이 아니다. 기록을 남기면 토큰을 넣은 뒤 그 공시가 영영 안 나간다.
        worker = AlertWorker(
            cache, notifier, min_severity=min_severity,
            record=isinstance(notifier, TelegramNotifier),
        )

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
            if worker.record:
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


async def cmd_snapshot(args: argparse.Namespace) -> int:
    """공개 스냅샷 export/import. 허용목록 테이블만 다룬다 (snapshot.py)."""
    from fin_checkup.snapshot import SnapshotError, export_snapshot, import_snapshot

    try:
        if args.snapshot_action == "export":
            db = args.db or settings.fin_checkup_db_path
            manifest = export_snapshot(
                db, args.out, secrets=[settings.dart_api_key, settings.telegram_bot_token]
            )
            print(f"스냅샷을 {args.out} 에 썼습니다 (빌드 {manifest['built_on']} KST).")
        else:
            manifest = import_snapshot(args.src, args.db)
            print(f"{args.src} 로 {args.db} 를 만들었습니다 (스냅샷 빌드 {manifest['built_on']}).")
    except SnapshotError as exc:
        print(f"스냅샷 오류: {exc}")
        return 1
    for table, info in manifest["tables"].items():
        print(f"  {table:<16} {info['rows']:>10,}행")
    return 0


async def cmd_disclosures(args: argparse.Namespace) -> int:
    """시장 전체 최근 위험 공시를 JSON 으로 쓴다 (사이트 재료). 호출은 dart_call_log 에 센다."""
    from fin_checkup.dart.client import DartClient
    from fin_checkup.disclosures import fetch_recent_risks, worst_case_calls, write_json

    if not settings.has_api_key:
        print("DART_API_KEY가 없습니다. 공시 목록은 인증키가 있어야 받을 수 있습니다.")
        return 1
    with Cache(settings.fin_checkup_db_path) as cache:
        used = cache.dart_calls_today(today_key())
        need = worst_case_calls(args.max_pages)
        if used + need > settings.dart_daily_quota:
            print(
                f"오늘 DART 호출 {used:,}건 — 이번 실행이 최대 {need}건을 더 쓰면 "
                f"일별 허용량 {settings.dart_daily_quota:,}건을 넘을 수 있어 시작하지 않습니다."
            )
            return 1

        def record(endpoint: str) -> None:
            cache.record_dart_call(endpoint, today_key())

        try:
            async with DartClient(settings=settings, on_call=record) as client:
                payload = await fetch_recent_risks(client, days=args.days, max_pages=args.max_pages)
        except RateLimitExceeded:
            # 020 은 오늘 안에 풀리지 않는다. 지난 파일은 그대로 둔다 — 빈 목록으로 덮으면
            # '최근 위험 공시 없음' 으로 읽힌다.
            print("DART 일별 호출 허용량을 넘었습니다. 파일을 바꾸지 않고 멈춥니다.")
            return 1
    write_json(payload, args.out)
    print(
        f"{payload['bgn_de']}~{payload['end_de']} 상장사 위험 공시 {payload['count']}건 → {args.out}"
    )
    if payload["truncated_types"]:
        print(f"⚠️ 페이지 상한에 걸린 공시유형: {', '.join(payload['truncated_types'])} (일부 누락 가능)")
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


async def cmd_site_build(args: argparse.Namespace) -> int:
    """캐시만 읽어 정적 사이트를 만든다. DART 를 부르지 않는다."""
    from fin_checkup.site.build import build_site
    from fin_checkup.site.buildinfo import read_snapshot_info
    from fin_checkup.site.context import OG_IMAGE_PAGE
    from fin_checkup.site.disclosures import DisclosureFormatError, load_disclosures
    from fin_checkup.snapshot import SnapshotError

    db_path = Path(settings.fin_checkup_db_path)
    if not db_path.exists():
        print(f"캐시 DB 가 없습니다: {db_path}  (FIN_CHECKUP_DB_PATH 로 지정할 수 있습니다)")
        return 1

    snapshot = None
    if args.snapshot:
        try:
            snapshot = read_snapshot_info(args.snapshot)
        except SnapshotError as exc:
            print(f"스냅샷 manifest 를 읽지 못했습니다: {exc}")
            return 1

    def progress(done: int, total: int) -> None:
        if done % 200 == 0 or done == total:
            print(f"  건강검진 {done:,}/{total:,}", flush=True)

    feed = None
    if args.disclosures:
        # 못 읽으면 공시 없이 만든다(공시 섹션·피드 항목이 빠질 뿐). Actions 에선 경고로 남긴다.
        try:
            feed = load_disclosures(args.disclosures)
        except DisclosureFormatError as exc:
            prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else ""
            print(f"{prefix}공시 JSON 을 읽지 못했습니다 — 공시 없이 만듭니다: {exc}")
        else:
            print(
                f"최근 위험 공시 {feed.begin}~{feed.end} {len(feed.items):,}건"
                + (f" (형식 밖 {feed.dropped}건 뺌)" if feed.dropped else "")
            )

    out = Path(args.out)
    with Cache(db_path, read_only=True) as cache:
        try:
            report = await build_site(
                cache,
                out,
                args.year,
                limit=args.limit,
                base_url=args.base_url,
                progress=progress,
                images=args.images,
                image_workers=args.image_workers,
                disclosures=feed,
                snapshot=snapshot,
                jobs=args.jobs,
                incremental_dir=Path(args.incremental) if args.incremental else None,
            )
        except FileExistsError as exc:
            print(str(exc))
            return 1

    print(
        f"\n{out}/ — 회사 {report.companies:,}곳 · 파일 {report.files:,}개 · 건너뜀 {report.skipped:,}곳\n"
        f"  계산 {report.load_seconds:.1f}초 · 렌더 {report.render_seconds:.1f}초 · "
        f"회사당 {report.per_company_ms:.0f}ms"
    )
    if OG_IMAGE_PAGE in report.pages:
        n, seconds = report.pages[OG_IMAGE_PAGE]
        per = seconds / n * 1000 if n else 0.0
        print(
            f"  공유 카드 PNG {n:,}장 · {seconds:.1f}초 · 장당 {per:.0f}ms "
            f"(프로세스 최대 {args.image_workers}개)"
        )
    if report.cache_summary:
        print(f"  {report.cache_summary}")
    # GitHub Actions 에선 워크플로 요약에 경고 주석으로 뜬다.
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else "경고: "
    for warning in [report.image_warning, *report.warnings]:
        if warning:
            print(f"{prefix}{warning}")
    return 0


async def cmd_site_check(args: argparse.Namespace) -> int:
    """빌드된 사이트가 넘지 않는 선(면책·계산식·CSP·외부 스크립트·저장소·인증키)과 검색 노출 요소."""
    from fin_checkup.site.buildinfo import check_freshness
    from fin_checkup.site.guard import check_coverage, check_site
    from fin_checkup.site.seo import check_seo

    out = Path(args.out_dir)
    # 오래된 사업연도는 위반이 아니라 사람이 볼 경고 — 종료 코드에 넣지 않는다.
    stale = check_freshness(out)
    if stale:
        prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else "경고: "
        print(f"{prefix}{stale}")
    violations = check_site(out) + check_coverage(out, args.min_companies) + check_seo(out)
    files = sum(1 for p in out.rglob("*") if p.is_file()) if out.is_dir() else 0
    if not violations:
        print(f"{out}/ — 파일 {files:,}개 검사, 위반 없음")
        return 0
    for violation in violations:
        print(violation)
    rules = "fin_checkup/site/guard.py · seo.py"
    print(f"\n{out}/ — 파일 {files:,}개 중 위반 {len(violations):,}건 (규칙은 {rules})")
    return 1


async def cmd_site_links(args: argparse.Namespace) -> int:
    """빌드된 사이트의 내부 링크가 실제 파일·앵커로 이어지는지. 외부 링크는 목록만."""
    from fin_checkup.site.links import check_links, format_report

    out = Path(args.out_dir)
    if not out.is_dir():
        print(f"{out}/ 가 없다 — site build 의 출력 폴더를 지정하세요.")
        return 1
    report = check_links(out, args.base_url)
    for line in format_report(report):
        print(line)
    summary = f"{out}/ — HTML {report.pages:,}개 · 내부 링크 {report.internal:,}개"
    summary += f" · 외부 {len(report.external):,}종"
    if report.ok:
        print(f"\n{summary} — 깨진 내부 링크 없음")
        return 0
    print(f"\n{summary} — 깨진 내부 링크 {len(report.broken):,}건")
    return 1


async def cmd_site_cards(args: argparse.Namespace) -> int:
    """회사 한 곳의 카드뉴스 PNG 를 만든다. 캐시만 읽고, 어디에도 올리지 않는다 (docs/card-news.md)."""
    import time

    from fin_checkup.site.build import HISTORY_YEARS, _offline_service
    from fin_checkup.site.images.cards import card_deck, draw_deck
    from fin_checkup.site.images.fonts import INSTALL_HINT, resolve_fonts

    if bool(args.market) == bool(args.stock_code):
        print("종목코드 하나 또는 --market 하나만 주세요.")
        return 2
    fonts = resolve_fonts()
    if fonts is None:
        print(INSTALL_HINT)
        return 1
    db_path = Path(settings.fin_checkup_db_path)
    if not db_path.exists():
        print(f"캐시 DB 가 없습니다: {db_path}  (FIN_CHECKUP_DB_PATH 로 지정할 수 있습니다)")
        return 1
    if args.market:
        return await _site_market_cards(args, db_path, fonts)
    with Cache(db_path, read_only=True) as cache:
        corp = cache.find_by_stock_code(args.stock_code)
        if corp is None:
            print(f"종목코드 {args.stock_code} 를 캐시에서 찾지 못했습니다.")
            return 1
        service = _offline_service(cache)
        data = await service.run(corp, end_year=args.year, years=HISTORY_YEARS, allow_fetch=False)
    if data is None or data.history[-1].bsns_year != args.year:
        print(f"{corp.corp_name} 의 {args.year} 사업연도 재무가 캐시에 없습니다.")
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    pages = draw_deck(card_deck(data, args.base_url), fonts)
    for name, canvas in pages:
        (out / name).write_bytes(canvas.png())
        print(f"  {out / name}")
    seconds = time.perf_counter() - started
    print(f"{corp.corp_name} 카드 {len(pages)}장 · {seconds:.1f}초")
    print("올리기 전에 docs/card-news.md 의 규칙을 볼 것 (출처·면책을 지우지 않는다).")
    return 0


async def _site_market_cards(args: argparse.Namespace, db_path: Path, fonts) -> int:
    """시장 전체 카드뉴스 — 모집단·분포는 `/market/` 과 같다 (`collect_companies` · `site.groups`)."""
    import time

    from fin_checkup.site.build import collect_companies
    from fin_checkup.site.images.market import draw_market_deck, market_deck

    started = time.perf_counter()
    with Cache(db_path, read_only=True) as cache:
        entries, _skipped = await collect_companies(cache, args.year)
    if not entries:
        print(f"{args.year} 사업연도 재무가 있는 회사가 캐시에 없습니다.")
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pages = draw_market_deck(market_deck(entries, args.year, args.base_url), fonts)
    for name, canvas in pages:
        (out / name).write_bytes(canvas.png())
        print(f"  {out / name}")
    seconds = time.perf_counter() - started
    print(f"시장 전체(상장사 {len(entries):,}곳) 카드 {len(pages)}장 · {seconds:.1f}초")
    print("올리기 전에 docs/card-news.md 의 규칙을 볼 것 (출처·면책을 지우지 않는다).")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(prog="fin-checkup", description="DART 재무 건강검진")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser(
        "setup", help="설치 점검 + OpenDART 인증키 넣기 (키는 화면·로그에 나오지 않는다)"
    )
    how = p_setup.add_mutually_exclusive_group()
    how.add_argument(
        "--check", action="store_true", help="점검만: Python·uv·공개 사이트·키 설정/유효 여부·캐시"
    )
    how.add_argument(
        "--key-stdin", action="store_true",
        help="키를 표준입력 한 줄로 받는다 (사용자가 직접 연 파이프용 — 명령줄 인자로 키를 넘기지 말 것)",
    )
    how.add_argument(
        "--from-clipboard", action="store_true", help="클립보드의 키를 곧장 파일로 (화면에 안 나온다)"
    )
    p_setup.add_argument(
        "--env-file", default=None,
        help="쓸 파일 (기본: 작업 폴더에 .env 가 있으면 그것, 없으면 사용자 설정 파일)",
    )
    p_setup.add_argument(
        "--sec-user-agent", default="", help="SEC EDGAR User-Agent ('이름 이메일')도 함께 저장"
    )
    p_setup.add_argument(
        "--no-verify", action="store_true", help="DART 로 확인하지 않고 저장 (오프라인일 때)"
    )
    p_setup.set_defaults(func=cmd_setup)

    p_sync = sub.add_parser("sync", help="corp_code 매핑 갱신")
    p_sync.add_argument("--force", action="store_true", help="TTL을 무시하고 강제 갱신")
    p_sync.set_defaults(func=cmd_sync)

    p_collect = sub.add_parser(
        "collect", help="상장사 중 재무가 없는 (회사, 연도)만 호출 예산 안에서 받기 (증분)"
    )
    p_collect.add_argument(
        "--budget", type=int, default=2000,
        help="이번 실행의 최대 DART 호출 수 (오늘 남은 일별 허용량이 더 적으면 그쪽)",
    )
    p_collect.add_argument(
        "--years", type=int, nargs="+", default=None,
        help="사업연도 (기본: 새 보고서가 들어올 해와 그 전 해 — fiscal.collect_years)",
    )
    p_collect.add_argument("--limit", type=int, default=0, help="상장사 앞에서 N곳만 (0이면 전체)")
    p_collect.add_argument(
        "--max-minutes", type=float, default=0, help="이 시간이 지나면 멈춘다 (0이면 제한 없음)"
    )
    p_collect.add_argument("--summary-json", default="", help="결과 요약 JSON 을 쓸 파일")
    p_collect.set_defaults(func=cmd_collect)

    p_check = sub.add_parser("checkup", help="종목코드 또는 회사명으로 건강검진")
    p_check.add_argument("query", help="종목코드(6자리) 또는 회사명")
    p_check.add_argument("--year", type=int, default=default_fiscal_year(), help="기준 사업연도")
    p_check.add_argument("--years", type=int, default=5, help="추이를 볼 연수")
    p_check.set_defaults(func=cmd_checkup)

    p_us = sub.add_parser("us", help="미국 상장기업 건강검진 (SEC EDGAR)")
    p_us.add_argument("ticker", help="티커 (예: AAPL)")
    p_us.add_argument("--year", type=int, default=default_fiscal_year(), help="기준 회계연도")
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
    p_backfill.add_argument("--year", type=int, default=default_fiscal_year(), help="기준 사업연도")
    p_backfill.add_argument("--years", type=int, default=2, help="거슬러 올라갈 연수")
    p_backfill.set_defaults(func=cmd_backfill)

    p_snapshot = sub.add_parser("snapshot", help="공개 스냅샷 export/import (허용목록 테이블만)")
    snapshot_sub = p_snapshot.add_subparsers(dest="snapshot_action", required=True)
    p_export = snapshot_sub.add_parser("export", help="캐시 → parquet + manifest.json")
    p_export.add_argument("--out", required=True, help="빈 출력 폴더")
    p_export.add_argument("--db", default="", help="원본 캐시 (기본: FIN_CHECKUP_DB_PATH)")
    p_export.set_defaults(func=cmd_snapshot)
    p_import = snapshot_sub.add_parser("import", help="스냅샷 폴더 → 새 캐시 DB")
    p_import.add_argument("src", help="manifest.json 이 있는 폴더")
    p_import.add_argument("--db", required=True, help="만들 캐시 파일 (이미 있으면 거부)")
    p_import.set_defaults(func=cmd_snapshot)

    p_disc = sub.add_parser("disclosures", help="시장 전체 최근 위험 공시 수집 (사이트용 JSON)")
    disc_sub = p_disc.add_subparsers(dest="disclosures_action", required=True)
    p_fetch = disc_sub.add_parser("fetch", help="최근 N일(KST) 상장사 위험 공시 → JSON")
    p_fetch.add_argument("--days", type=int, default=7, help="오늘 포함 최근 며칠 (1~90)")
    p_fetch.add_argument("--out", required=True, help="쓸 JSON 파일")
    p_fetch.add_argument(
        "--max-pages", type=int, default=50, help="공시유형당 최대 페이지(100건/페이지)"
    )
    p_fetch.set_defaults(func=cmd_disclosures)

    p_account = sub.add_parser("account", help="운영자용 계정 관리")
    account_sub = p_account.add_subparsers(dest="account_action", required=True)
    p_issue = account_sub.add_parser("issue-key", help="이메일로 API 키 발급 (키를 잃었을 때)")
    p_issue.add_argument("email")
    p_issue.set_defaults(func=cmd_account)

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

    p_site = sub.add_parser("site", help="정적 사이트 (캐시만 읽음, DART 호출 없음)")
    site_sub = p_site.add_subparsers(dest="site_action", required=True)
    p_site_build = site_sub.add_parser("build", help="회사별 페이지를 출력 폴더에 생성")
    p_site_build.add_argument("--year", type=int, default=default_fiscal_year(), help="기준 사업연도")
    p_site_build.add_argument("--out", default="site-out", help="출력 폴더 (빌드마다 비운다)")
    p_site_build.add_argument("--limit", type=int, default=None, help="앞에서 N곳만 (확인용)")
    p_site_build.add_argument(
        "--base-url", default="/",
        help="절대주소가 필요한 곳(canonical·sitemap)에 쓸 사이트 주소. 링크는 상대경로라 무관",
    )
    p_site_build.add_argument(
        "--images",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="공유 카드 PNG(og/<코드>.png) 생성. 한국어 글꼴이 없으면 경고하고 건너뛴다",
    )
    p_site_build.add_argument(
        "--image-workers",
        type=int,
        default=os.cpu_count() or 1,
        help="PNG 를 나눠 그릴 프로세스 수 (기본: CPU 수)",
    )
    p_site_build.add_argument(
        "--disclosures", default=None,
        help="`disclosures fetch` 가 쓴 JSON — 회사 페이지·첫 화면 최근 위험 공시와 Atom 피드에 싣는다",
    )
    p_site_build.add_argument(
        "--snapshot", default=None,
        help="`snapshot import` 한 폴더(manifest.json). 주면 꼬리·build.json 에 스냅샷 날짜를 적는다",
    )
    p_site_build.add_argument(
        "--jobs", type=int, default=1,
        help="건강검진 계산을 나눌 프로세스 수 (DB 를 읽기 전용으로 여는 site build 에서만)",
    )
    p_site_build.add_argument(
        "--incremental", default=None, metavar="DIR",
        help="증분 빌드 캐시 폴더 — 코드·데이터가 같은 건강검진과 공유 카드 PNG 를 다시 만들지 않는다",
    )
    p_site_build.set_defaults(func=cmd_site_build)

    p_site_cards = site_sub.add_parser(
        "cards", help="카드뉴스 PNG (1080×1350, 7장 이하) — 만들기만 하고 올리지 않는다"
    )
    p_site_cards.add_argument("stock_code", nargs="?", help="종목코드 6자리 (--market 이면 빼기)")
    p_site_cards.add_argument(
        "--market",
        action="store_true",
        help="회사 대신 시장 전체 신호 비율 카드 (회사 이름 없음, /market/ 과 같은 분포)",
    )
    p_site_cards.add_argument("--out", required=True, help="PNG 를 쓸 폴더")
    p_site_cards.add_argument("--year", type=int, default=default_fiscal_year(), help="기준 사업연도")
    p_site_cards.add_argument(
        "--base-url",
        default="https://younghwan91.github.io/fin-checkup/",
        help="마지막 장에 적을 사이트 주소",
    )
    p_site_cards.set_defaults(func=cmd_site_cards)
    p_site_check = site_sub.add_parser(
        "check", help="빌드된 사이트를 가드 규칙으로 검사 (위반이 있으면 종료 코드 1)"
    )
    p_site_check.add_argument("out_dir", help="site build 의 출력 폴더")
    p_site_check.add_argument(
        "--min-companies",
        type=int,
        default=0,
        help="회사 페이지가 이보다 적으면 위반 (0 이면 검사 안 함, Pages 배포는 2000)",
    )
    p_site_check.set_defaults(func=cmd_site_check)
    p_site_links = site_sub.add_parser(
        "links", help="빌드된 사이트의 깨진 내부 링크 검사 (있으면 종료 코드 1, 외부 링크는 보고만)"
    )
    p_site_links.add_argument("out_dir", help="site build 의 출력 폴더")
    p_site_links.add_argument(
        "--base-url", default="/",
        help="site build 에 준 것과 같은 주소. 그 아래 절대주소(canonical 등)도 내부 링크로 검사한다",
    )
    p_site_links.set_defaults(func=cmd_site_links)

    args = parser.parse_args(argv)
    try:
        return asyncio.run(args.func(args))
    except CacheLocked as exc:
        print(f"\n{exc}")
        return 1
    except DartError as exc:
        # 트레이스백 대신 상태 코드와 메시지만. 키가 틀렸거나 한도를 넘은 건 코드 버그가 아니다.
        print(f"\nDART 오류: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\n중지했습니다.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
