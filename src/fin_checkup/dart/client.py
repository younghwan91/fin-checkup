"""DART Open API 클라이언트.

약관 준수 메모:
- 제19조: 인증키는 서버가 보관하고 절대 응답에 싣지 않는다.
- 제10조: 일별 호출 허용량 제한 → 호출 간 최소 대기 + 상위 레이어 캐싱으로 대응.
- 제23조: 금감원은 데이터 정확성에 책임지지 않는다 → 값 누락을 조용히 메우지 않는다.
"""

from __future__ import annotations

import asyncio
import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from types import TracebackType

import httpx

from fin_checkup.config import Settings
from fin_checkup.config import settings as default_settings
from fin_checkup.dart.retry import RetryPolicy, with_retry
from fin_checkup.models import (
    CORP_CLS_TO_MARKET,
    AccountLine,
    Company,
    CorpCode,
    Disclosure,
    FsDiv,
    PblntfTy,
    RawStatements,
    ReportCode,
)

logger = logging.getLogger(__name__)


class DartError(RuntimeError):
    """DART가 정상(000) 이외의 status를 반환했을 때."""

    def __init__(self, status: str, message: str, context: str = "") -> None:
        self.status = status
        self.message = message
        super().__init__(f"[{status}] {message}" + (f" ({context})" if context else ""))


#: DART status 코드 중 "데이터 없음"에 해당하는 것. 오류가 아니라 빈 결과로 다룬다.
NO_DATA_STATUSES = frozenset({"013"})


def _amount(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.replace(",", "").strip()
    if text in ("", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


class DartClient:
    """DART Open API 비동기 클라이언트. `async with`로 쓴다."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy | None = None,
        on_call: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings or default_settings
        if not self.settings.has_api_key:
            raise ValueError(
                "DART_API_KEY가 설정되지 않았습니다. "
                "https://opendart.fss.or.kr 에서 인증키를 발급받아 .env에 넣어주세요."
            )
        self._client = client or httpx.AsyncClient(timeout=self.settings.dart_timeout)
        self._owns_client = client is None
        self._throttle = asyncio.Lock()
        self.retry_policy = retry_policy or RetryPolicy()
        #: 이 클라이언트가 실제로 보낸 요청 수. 재시도도 한 건으로 센다.
        self.calls_made = 0
        self._on_call = on_call

    async def __aenter__(self) -> DartClient:
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

    # ------------------------------------------------------------------
    # 저수준 호출
    # ------------------------------------------------------------------

    async def _get_once(self, path: str, params: dict[str, str]) -> httpx.Response:
        async with self._throttle:
            url = f"{self.settings.dart_base_url}/{path}"
            resp = await self._client.get(
                url, params={"crtfc_key": self.settings.dart_api_key, **params}
            )
            self.calls_made += 1
            if self._on_call is not None:
                self._on_call(path)
            resp.raise_for_status()
            if self.settings.dart_min_delay > 0:
                await asyncio.sleep(self.settings.dart_min_delay)
            return resp

    async def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        """일시적 실패는 재시도한다. 영구적 실패는 그대로 올린다."""
        return await with_retry(
            lambda: self._get_once(path, params),
            policy=self.retry_policy,
            context=path,
        )

    async def _get_json(self, path: str, params: dict[str, str], context: str) -> dict | None:
        """정상이면 payload, '데이터 없음'이면 None, 그 외 오류면 DartError.

        상태 판정을 재시도 안쪽에 둬야 status=020(요청제한)도 재시도 대상이 된다.
        바깥에 두면 HTTP 200이라 재시도 없이 그냥 실패한다.
        """

        async def attempt() -> dict | None:
            resp = await self._get_once(path, params)
            data = resp.json()
            status = data.get("status", "")
            if status == "000":
                return data
            if status in NO_DATA_STATUSES:
                logger.info("[dart] no data — %s (status=%s)", context, status)
                return None
            raise DartError(status, data.get("message", "unknown error"), context)

        return await with_retry(attempt, policy=self.retry_policy, context=context)

    # ------------------------------------------------------------------
    # DS001 공시정보
    # ------------------------------------------------------------------

    async def fetch_corp_codes(self) -> list[CorpCode]:
        """고유번호 전체 목록(corpCode.xml ZIP)을 내려받아 파싱."""
        resp = await self._get("corpCode.xml", {})
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_bytes = zf.read(zf.namelist()[0])

        results: list[CorpCode] = []
        for item in ET.fromstring(xml_bytes).iter("list"):
            corp_code = (item.findtext("corp_code") or "").strip()
            if not corp_code:
                continue
            results.append(
                CorpCode(
                    corp_code=corp_code,
                    corp_name=(item.findtext("corp_name") or "").strip(),
                    stock_code=(item.findtext("stock_code") or "").strip(),
                    modify_date=(item.findtext("modify_date") or "").strip(),
                )
            )
        logger.info("[dart] corp_code %d건 수신", len(results))
        return results

    async def fetch_company(self, corp_code: str) -> Company | None:
        """기업개황."""
        data = await self._get_json(
            "company.json", {"corp_code": corp_code}, f"company({corp_code})"
        )
        if data is None:
            return None
        try:
            fiscal_month = int(data.get("acc_mt") or 12)
        except ValueError:
            fiscal_month = 12
        return Company(
            corp_code=corp_code,
            corp_name=data.get("corp_name", ""),
            stock_code=(data.get("stock_code") or "").strip(),
            market=CORP_CLS_TO_MARKET.get(data.get("corp_cls", "")),
            industry_code=data.get("induty_code", ""),
            ceo_name=data.get("ceo_nm", ""),
            established_date=data.get("est_dt", ""),
            fiscal_month=fiscal_month,
            homepage=data.get("hm_url", ""),
        )

    async def search_disclosures(
        self,
        corp_code: str | None = None,
        bgn_de: str | None = None,
        end_de: str | None = None,
        pblntf_ty: PblntfTy | None = None,
        corp_cls: str | None = None,
        page_count: int = 100,
        max_pages: int = 10,
    ) -> list[Disclosure]:
        """공시검색(list.json). 페이지를 끝까지 따라간다.

        corp_code 없이 조회하면 기간이 3개월로 제한된다(DART 규칙).
        max_pages는 폭주 방지용 상한 — 도달하면 경고를 남긴다.
        """
        params: dict[str, str] = {
            "page_count": str(min(page_count, 100)),
            "sort": "date",
            "sort_mth": "desc",
        }
        if corp_code:
            params["corp_code"] = corp_code
        if bgn_de:
            params["bgn_de"] = bgn_de
        if end_de:
            params["end_de"] = end_de
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty.value
        if corp_cls:
            params["corp_cls"] = corp_cls

        results: list[Disclosure] = []
        page = 1
        while page <= max_pages:
            data = await self._get_json(
                "list.json", {**params, "page_no": str(page)}, f"list(page={page})"
            )
            if data is None:
                break
            for item in data.get("list", []):
                results.append(
                    Disclosure(
                        rcept_no=(item.get("rcept_no") or "").strip(),
                        corp_code=(item.get("corp_code") or "").strip(),
                        corp_name=(item.get("corp_name") or "").strip(),
                        stock_code=(item.get("stock_code") or "").strip(),
                        report_nm=(item.get("report_nm") or "").strip(),
                        flr_nm=(item.get("flr_nm") or "").strip(),
                        rcept_dt=(item.get("rcept_dt") or "").strip(),
                        corp_cls=(item.get("corp_cls") or "").strip(),
                    )
                )
            try:
                total_page = int(data.get("total_page") or 1)
            except (TypeError, ValueError):
                total_page = 1
            if page >= total_page:
                break
            page += 1
        else:
            logger.warning(
                "[dart] 공시검색이 %d페이지 상한에 걸렸다 — 기간을 좁혀 다시 조회할 것", max_pages
            )
        return results

    # ------------------------------------------------------------------
    # DS003 상장기업 재무정보
    # ------------------------------------------------------------------

    async def fetch_statements(
        self,
        corp_code: str,
        bsns_year: int,
        reprt_code: ReportCode = ReportCode.ANNUAL,
        fs_div: FsDiv = FsDiv.CFS,
    ) -> RawStatements | None:
        """단일회사 전체 재무제표 (BS/IS/CIS/CF/SCE 전 계정).

        2015 사업연도부터 제공된다.
        """
        context = f"statements({corp_code},{bsns_year},{reprt_code.value},{fs_div.value})"
        data = await self._get_json(
            "fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": str(bsns_year),
                "reprt_code": reprt_code.value,
                "fs_div": fs_div.value,
            },
            context,
        )
        if data is None:
            return None

        lines: list[AccountLine] = []
        for item in data.get("list", []):
            try:
                order = int(item.get("ord") or 0)
            except ValueError:
                order = 0
            lines.append(
                AccountLine(
                    sj_div=(item.get("sj_div") or "").strip(),
                    account_id=(item.get("account_id") or "").strip(),
                    account_nm=(item.get("account_nm") or "").strip(),
                    thstrm_amount=_amount(item.get("thstrm_amount")),
                    frmtrm_amount=_amount(item.get("frmtrm_amount")),
                    bfefrmtrm_amount=_amount(item.get("bfefrmtrm_amount")),
                    currency=(item.get("currency") or "KRW").strip(),
                    ord=order,
                )
            )
        if not lines:
            return None
        return RawStatements(
            corp_code=corp_code,
            bsns_year=bsns_year,
            reprt_code=reprt_code,
            fs_div=fs_div,
            lines=lines,
        )

    async def fetch_statements_with_fallback(
        self,
        corp_code: str,
        bsns_year: int,
        reprt_code: ReportCode = ReportCode.ANNUAL,
    ) -> RawStatements | None:
        """연결(CFS) 우선, 없으면 개별(OFS).

        연결재무제표를 작성하지 않는 기업(종속회사 없음)은 CFS 조회가 비어 온다.
        """
        for fs_div in (FsDiv.CFS, FsDiv.OFS):
            raw = await self.fetch_statements(corp_code, bsns_year, reprt_code, fs_div)
            if raw is not None:
                return raw
        return None
