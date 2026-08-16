from __future__ import annotations

import io
import zipfile

import httpx
import pytest
import respx

from fin_checkup.config import Settings
from fin_checkup.dart.client import DartClient, DartError
from fin_checkup.models import FsDiv, ReportCode

BASE = "https://opendart.fss.or.kr/api"

CORP_CODE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name>
        <stock_code>005930</stock_code><modify_date>20240101</modify_date></list>
  <list><corp_code>00111722</corp_code><corp_name>비상장기업</corp_name>
        <stock_code> </stock_code><modify_date>20240101</modify_date></list>
</result>"""


def corp_code_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", CORP_CODE_XML.encode("utf-8"))
    return buf.getvalue()


@pytest.fixture
def client(test_settings: Settings):
    return DartClient(settings=test_settings)


def test_missing_api_key_is_rejected_up_front():
    with pytest.raises(ValueError, match="DART_API_KEY"):
        DartClient(settings=Settings(dart_api_key=""))


@respx.mock
async def test_fetch_corp_codes_parses_zip(client: DartClient):
    respx.get(f"{BASE}/corpCode.xml").mock(
        return_value=httpx.Response(200, content=corp_code_zip())
    )
    codes = await client.fetch_corp_codes()
    assert len(codes) == 2
    assert codes[0].corp_code == "00126380"
    assert codes[0].stock_code == "005930"
    assert codes[0].is_listed is True
    assert codes[1].is_listed is False
    await client.aclose()


@respx.mock
async def test_api_key_is_sent_but_never_returned(client: DartClient):
    route = respx.get(f"{BASE}/corpCode.xml").mock(
        return_value=httpx.Response(200, content=corp_code_zip())
    )
    await client.fetch_corp_codes()
    assert route.calls.last.request.url.params["crtfc_key"] == "test-key-0123456789"
    await client.aclose()


@respx.mock
async def test_fetch_statements_maps_lines(client: DartClient):
    respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": [
                    {
                        "sj_div": "BS", "account_id": "ifrs-full_Assets",
                        "account_nm": "자산총계", "thstrm_amount": "1,234,567",
                        "frmtrm_amount": "1,000,000", "currency": "KRW", "ord": "1",
                    },
                    {
                        "sj_div": "IS", "account_id": "-표준계정코드 미사용-",
                        "account_nm": "매출액", "thstrm_amount": "-",
                        "frmtrm_amount": "", "currency": "KRW", "ord": "2",
                    },
                ],
            },
        )
    )
    raw = await client.fetch_statements("00126380", 2024)
    assert raw is not None
    assert raw.lines[0].thstrm_amount == 1234567.0
    assert raw.lines[0].frmtrm_amount == 1000000.0
    # "-"와 빈 문자열은 0이 아니라 None
    assert raw.lines[1].thstrm_amount is None
    assert raw.lines[1].frmtrm_amount is None
    await client.aclose()


@respx.mock
async def test_fetch_statements_sends_required_params(client: DartClient):
    route = respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(200, json={"status": "000", "list": []})
    )
    await client.fetch_statements("00126380", 2023, ReportCode.Q3, FsDiv.OFS)
    params = route.calls.last.request.url.params
    assert params["corp_code"] == "00126380"
    assert params["bsns_year"] == "2023"
    assert params["reprt_code"] == "11014"
    assert params["fs_div"] == "OFS"
    await client.aclose()


@respx.mock
async def test_no_data_status_returns_none_not_error(client: DartClient):
    respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(200, json={"status": "013", "message": "조회된 데이터가 없습니다."})
    )
    assert await client.fetch_statements("00126380", 2024) is None
    await client.aclose()


@respx.mock
async def test_real_error_status_raises(client: DartClient):
    respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(200, json={"status": "020", "message": "요청 제한을 초과하였습니다."})
    )
    with pytest.raises(DartError) as exc:
        await client.fetch_statements("00126380", 2024)
    assert exc.value.status == "020"
    await client.aclose()


@respx.mock
async def test_fallback_tries_ofs_when_cfs_is_empty(client: DartClient):
    calls: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        fs_div = request.url.params["fs_div"]
        calls.append(fs_div)
        if fs_div == "CFS":
            return httpx.Response(200, json={"status": "013", "message": "없음"})
        return httpx.Response(
            200,
            json={
                "status": "000",
                "list": [
                    {
                        "sj_div": "BS", "account_id": "ifrs-full_Assets",
                        "account_nm": "자산총계", "thstrm_amount": "500", "ord": "1",
                    }
                ],
            },
        )

    respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(side_effect=responder)
    raw = await client.fetch_statements_with_fallback("00111722", 2024)
    assert calls == ["CFS", "OFS"]
    assert raw is not None
    assert raw.fs_div == FsDiv.OFS
    await client.aclose()


@respx.mock
async def test_empty_list_is_treated_as_no_data(client: DartClient):
    respx.get(f"{BASE}/fnlttSinglAcntAll.json").mock(
        return_value=httpx.Response(200, json={"status": "000", "list": []})
    )
    assert await client.fetch_statements("00126380", 2024) is None
    await client.aclose()


@respx.mock
async def test_fetch_company(client: DartClient):
    respx.get(f"{BASE}/company.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "000", "message": "정상", "corp_name": "삼성전자",
                "stock_code": "005930", "corp_cls": "Y", "ceo_nm": "홍길동",
                "induty_code": "264", "est_dt": "19690113", "acc_mt": "12",
            },
        )
    )
    company = await client.fetch_company("00126380")
    assert company is not None
    assert company.corp_name == "삼성전자"
    assert company.market.value == "KOSPI"
    assert company.fiscal_month == 12
    await client.aclose()


# ── 공시검색 (DS001 list.json) ────────────────────────────────────


def _disclosure_page(page_no: int, total_page: int, names: list[str]) -> dict:
    return {
        "status": "000", "message": "정상",
        "page_no": page_no, "total_page": total_page,
        "list": [
            {
                "rcept_no": f"2024010100000{i}", "corp_code": "00126380",
                "corp_name": "삼성전자", "stock_code": "005930",
                "report_nm": name, "flr_nm": "삼성전자", "rcept_dt": "20240101",
                "corp_cls": "Y",
            }
            for i, name in enumerate(names)
        ],
    }


@respx.mock
async def test_search_disclosures_follows_pagination(client: DartClient):
    def responder(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page_no"])
        return httpx.Response(200, json=_disclosure_page(page, 3, [f"공시{page}"]))

    route = respx.get(f"{BASE}/list.json").mock(side_effect=responder)
    found = await client.search_disclosures(corp_code="00126380")
    assert [d.report_nm for d in found] == ["공시1", "공시2", "공시3"]
    assert route.call_count == 3
    await client.aclose()


@respx.mock
async def test_search_disclosures_stops_at_max_pages(client: DartClient):
    respx.get(f"{BASE}/list.json").mock(
        side_effect=lambda r: httpx.Response(
            200, json=_disclosure_page(int(r.url.params["page_no"]), 999, ["공시"])
        )
    )
    found = await client.search_disclosures(corp_code="00126380", max_pages=2)
    assert len(found) == 2
    await client.aclose()


@respx.mock
async def test_search_disclosures_sends_filters(client: DartClient):
    from fin_checkup.models import PblntfTy

    route = respx.get(f"{BASE}/list.json").mock(
        return_value=httpx.Response(200, json=_disclosure_page(1, 1, ["공시"]))
    )
    await client.search_disclosures(
        corp_code="00126380", bgn_de="20240101", end_de="20240131",
        pblntf_ty=PblntfTy.MAJOR, corp_cls="Y",
    )
    params = route.calls.last.request.url.params
    assert params["bgn_de"] == "20240101"
    assert params["end_de"] == "20240131"
    assert params["pblntf_ty"] == "B"
    assert params["corp_cls"] == "Y"
    await client.aclose()


@respx.mock
async def test_search_disclosures_empty_result(client: DartClient):
    respx.get(f"{BASE}/list.json").mock(
        return_value=httpx.Response(200, json={"status": "013", "message": "없음"})
    )
    assert await client.search_disclosures(corp_code="00126380") == []
    await client.aclose()


def test_disclosure_url_points_to_dart_original():
    from fin_checkup.models import Disclosure

    d = Disclosure(rcept_no="20240101000001", corp_code="1", corp_name="A", report_nm="X")
    assert d.url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20240101000001"
