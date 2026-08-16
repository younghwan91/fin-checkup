"""FastAPI 앱.

서버 프로세스 하나가 캐시를 소유하고, 알림 워커는 그 안의 백그라운드 작업으로 돈다
(DuckDB가 단일 writer라 워커를 따로 띄우면 락에서 부딪힌다).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse

from fin_checkup.alerts.scheduler import LAST_POLL_KEY, AlertScheduler
from fin_checkup.alerts.telegram import ConsoleNotifier, TelegramNotifier
from fin_checkup.alerts.worker import AlertWorker
from fin_checkup.api.schemas import (
    AccountOut,
    CheckupOut,
    HealthOut,
    IssuedKeyOut,
    SearchHit,
    WatchItem,
)
from fin_checkup.auth import generate_key, hash_key, normalize_email, user_id_for
from fin_checkup.config import Settings
from fin_checkup.config import settings as default_settings
from fin_checkup.service import CheckupService
from fin_checkup.storage import Cache

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    cache: Cache
    service: CheckupService
    settings: Settings
    scheduler: AlertScheduler | None = None
    worker_task: asyncio.Task | None = None


def create_app(
    settings: Settings | None = None,
    cache: Cache | None = None,
    run_worker: bool = True,
) -> FastAPI:
    settings = settings or default_settings

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = cache is None
        active = cache or Cache(settings.fin_checkup_db_path)
        service = CheckupService(active, settings=settings)
        state = AppState(cache=active, service=service, settings=settings)

        if run_worker and settings.has_api_key:
            notifier = (
                TelegramNotifier(settings.telegram_bot_token)
                if settings.has_telegram
                else ConsoleNotifier()
            )
            state.scheduler = AlertScheduler(
                active, AlertWorker(active, notifier, settings=settings)
            )
            state.worker_task = asyncio.create_task(state.scheduler.run_forever())
            logger.info("[api] 알림 워커를 백그라운드로 시작했다")

        app.state.ctx = state
        try:
            yield
        finally:
            if state.worker_task is not None:
                state.worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await state.worker_task
            if owned:
                active.close()

    app = FastAPI(
        title="fin-checkup API",
        description=(
            "DART·SEC 공시 기반 재무 건강검진. 측정 결과만 제공하며 "
            "투자권유·투자자문을 하지 않습니다."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------
    # 의존성
    # ------------------------------------------------------------------

    def get_state() -> AppState:
        return app.state.ctx

    def get_user_id(
        state: AppState = Depends(get_state),
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> str:
        """API 키로 사용자를 식별한다. 키가 없으면 401."""
        token = x_api_key
        if not token and authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API 키가 필요합니다. X-API-Key 헤더에 넣어주세요.",
            )
        user_id = state.cache.resolve_api_key(hash_key(token))
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 API 키입니다."
            )
        return user_id

    # ------------------------------------------------------------------
    # 공개 엔드포인트
    # ------------------------------------------------------------------

    @app.get("/health", response_model=HealthOut, tags=["운영"])
    def health(state: AppState = Depends(get_state)) -> HealthOut:
        used, quota = state.service.dart_budget()
        corp_count = state.cache.conn.execute("SELECT count(*) FROM corp_codes").fetchone()[0]
        return HealthOut(
            status="ok",
            corp_codes=int(corp_count),
            dart_calls_today=used,
            dart_daily_quota=quota,
            alerts_last_poll=state.cache.get_meta(LAST_POLL_KEY),
        )

    @app.post("/accounts", response_model=IssuedKeyOut, tags=["계정"], status_code=201)
    def create_account(email: str = Query(...), state: AppState = Depends(get_state)):
        """계정을 만들고 API 키를 발급한다. 키는 이 응답에만 나온다."""
        try:
            normalized = normalize_email(email)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        user_id = user_id_for(normalized)
        state.cache.create_account(user_id, normalized)
        issued = generate_key(user_id)
        state.cache.save_api_key(issued.key_hash, user_id, issued.prefix)
        return IssuedKeyOut(user_id=user_id, api_key=issued.plaintext)

    # ------------------------------------------------------------------
    # 인증 필요
    # ------------------------------------------------------------------

    @app.get("/me", response_model=AccountOut, tags=["계정"])
    def me(user_id: str = Depends(get_user_id), state: AppState = Depends(get_state)):
        return AccountOut(
            user_id=user_id,
            watchlist_count=state.cache.count_watch(user_id),
        )

    @app.get("/search", response_model=list[SearchHit], tags=["조회"])
    def search(
        q: str = Query(..., min_length=1),
        limit: int = Query(20, ge=1, le=100),
        user_id: str = Depends(get_user_id),
        state: AppState = Depends(get_state),
    ):
        return [
            SearchHit(corp_code=c.corp_code, corp_name=c.corp_name, stock_code=c.stock_code)
            for c in state.service.search(q, limit=limit)
        ]

    @app.get("/checkup/{query}", response_model=CheckupOut, tags=["조회"])
    async def checkup(
        query: str,
        year: int = Query(2024, ge=2015, le=2100),
        years: int = Query(5, ge=2, le=10),
        user_id: str = Depends(get_user_id),
        state: AppState = Depends(get_state),
    ):
        matches = state.service.search(query)
        if not matches:
            raise HTTPException(status_code=404, detail=f"'{query}'에 해당하는 상장기업이 없습니다.")

        data = await state.service.run(matches[0], end_year=year, years=years)
        if data is None:
            raise HTTPException(
                status_code=404, detail=f"{matches[0].corp_name}의 {year}년 재무제표가 없습니다."
            )
        return CheckupOut.of(data)

    @app.get("/watchlist", response_model=list[WatchItem], tags=["관심종목"])
    def list_watch(user_id: str = Depends(get_user_id), state: AppState = Depends(get_state)):
        return [
            WatchItem(corp_code=c.corp_code, corp_name=c.corp_name, stock_code=c.stock_code)
            for c in state.cache.list_watch(user_id)
        ]

    @app.post("/watchlist/{query}", response_model=list[WatchItem], tags=["관심종목"], status_code=201)
    def add_watch(
        query: str,
        user_id: str = Depends(get_user_id),
        state: AppState = Depends(get_state),
    ):
        matches = state.service.search(query)
        if not matches:
            raise HTTPException(status_code=404, detail=f"'{query}'에 해당하는 상장기업이 없습니다.")

        state.cache.add_watch(user_id, matches[0])
        return list_watch(user_id, state)

    @app.delete("/watchlist/{corp_code}", tags=["관심종목"])
    def remove_watch(
        corp_code: str,
        user_id: str = Depends(get_user_id),
        state: AppState = Depends(get_state),
    ):
        removed = state.cache.remove_watch(user_id, corp_code)
        if not removed:
            raise HTTPException(status_code=404, detail="등록돼 있지 않습니다.")
        return JSONResponse({"removed": corp_code})

    return app
