"""SEC EDGAR 클라이언트.

SEC는 인증키를 요구하지 않는 대신 **연락처가 담긴 User-Agent를 요구한다.**
없으면 403으로 막힌다. 초당 10회 제한도 있어 호출 간 간격을 둔다.
"""

from __future__ import annotations

import asyncio
import logging
from types import TracebackType

import httpx

from fin_checkup.config import Settings
from fin_checkup.config import settings as default_settings
from fin_checkup.models import Financials
from fin_checkup.sec.normalize import normalize_company_facts

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
#: SEC 권고는 초당 10회. 여유를 두고 0.15초 간격.
MIN_DELAY = 0.15


class SecError(RuntimeError):
    pass


class SecTicker:
    __slots__ = ("cik", "ticker", "title")

    def __init__(self, cik: str, ticker: str, title: str) -> None:
        self.cik = cik
        self.ticker = ticker
        self.title = title

    @property
    def cik_padded(self) -> str:
        return self.cik.zfill(10)

    def __repr__(self) -> str:
        return f"SecTicker({self.ticker}, {self.title})"


class SecClient:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or default_settings
        if not self.settings.has_sec_user_agent:
            raise ValueError(
                "SEC_USER_AGENT가 설정되지 않았습니다. SEC는 연락처가 담긴 User-Agent를 "
                "요구합니다. 예: 'fin-checkup you@example.com'"
            )
        self._client = client or httpx.AsyncClient(
            timeout=30.0, headers={"User-Agent": self.settings.sec_user_agent}
        )
        self._owns_client = client is None
        self._throttle = asyncio.Lock()

    async def __aenter__(self) -> SecClient:
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

    async def _get(self, url: str) -> httpx.Response:
        async with self._throttle:
            resp = await self._client.get(url)
            await asyncio.sleep(MIN_DELAY)
            return resp

    async def fetch_tickers(self) -> list[SecTicker]:
        """전체 티커 → CIK 매핑."""
        resp = await self._get(TICKERS_URL)
        if resp.status_code != 200:
            raise SecError(f"티커 목록 조회 실패: HTTP {resp.status_code}")

        payload = resp.json()
        # {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        rows = payload.values() if isinstance(payload, dict) else payload
        results: list[SecTicker] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker", "")).strip()
            cik = str(row.get("cik_str", "")).strip()
            if ticker and cik:
                results.append(SecTicker(cik=cik, ticker=ticker, title=str(row.get("title", ""))))
        logger.info("[sec] 티커 %d건 수신", len(results))
        return results

    async def fetch_company_facts(self, cik: str) -> dict | None:
        """companyfacts 원본. 해당 CIK가 없으면 None."""
        url = f"{self.settings.sec_base_url}/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"
        resp = await self._get(url)
        if resp.status_code == 404:
            logger.info("[sec] companyfacts 없음 cik=%s", cik)
            return None
        if resp.status_code != 200:
            raise SecError(f"companyfacts 조회 실패 cik={cik}: HTTP {resp.status_code}")
        return resp.json()

    async def fetch_financials(self, cik: str, fiscal_year: int) -> Financials | None:
        facts = await self.fetch_company_facts(cik)
        if facts is None:
            return None
        return normalize_company_facts(facts, fiscal_year, cik=cik)

    async def fetch_history(
        self, cik: str, end_year: int, years: int = 5
    ) -> list[Financials]:
        """연도 오름차순. companyfacts를 한 번만 받아 여러 해를 뽑는다."""
        facts = await self.fetch_company_facts(cik)
        if facts is None:
            return []
        collected: list[Financials] = []
        for year in range(end_year - years + 1, end_year + 1):
            fin = normalize_company_facts(facts, year, cik=cik)
            if fin is not None:
                collected.append(fin)
        return collected
