from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fin_checkup.api import create_app
from fin_checkup.config import Settings
from fin_checkup.models import AccountLine, Company, CorpCode, FsDiv, RawStatements, ReportCode
from fin_checkup.storage import Cache

SAMSUNG = CorpCode(corp_code="00126380", corp_name="삼성전자", stock_code="005930")
KB = CorpCode(corp_code="00105560", corp_name="KB금융", stock_code="105560")


def statements(corp_code: str, year: int, revenue: float) -> RawStatements:
    def ln(sj, aid, nm, amount, order):
        return AccountLine(sj_div=sj, account_id=aid, account_nm=nm, thstrm_amount=amount, ord=order)

    return RawStatements(
        corp_code=corp_code, bsns_year=year, reprt_code=ReportCode.ANNUAL, fs_div=FsDiv.CFS,
        lines=[
            ln("BS", "ifrs-full_Assets", "자산총계", 10000.0, 1),
            ln("BS", "ifrs-full_Liabilities", "부채총계", 4000.0, 2),
            ln("BS", "ifrs-full_Equity", "자본총계", 6000.0, 3),
            ln("IS", "ifrs-full_Revenue", "매출액", revenue, 4),
            ln("IS", "dart_OperatingIncomeLoss", "영업이익", revenue * 0.1, 5),
            ln("IS", "ifrs-full_ProfitLoss", "당기순이익", revenue * 0.08, 6),
        ],
    )


@pytest.fixture
def cache(tmp_path):
    with Cache(tmp_path / "api.duckdb") as c:
        c.save_corp_codes([SAMSUNG, KB])
        c.save_company(Company(corp_code=SAMSUNG.corp_code, corp_name="삼성전자",
                               stock_code="005930", industry_code="264"))
        c.save_company(Company(corp_code=KB.corp_code, corp_name="KB금융",
                               stock_code="105560", industry_code="64992"))
        for year, rev in [(2022, 900.0), (2023, 1000.0), (2024, 1200.0)]:
            c.save_statements(statements(SAMSUNG.corp_code, year, rev))
            c.save_statements(statements(KB.corp_code, year, rev))
        yield c


@pytest.fixture
def client(cache, tmp_path):
    settings = Settings(dart_api_key="", fin_checkup_db_path=tmp_path / "api.duckdb")
    app = create_app(settings=settings, cache=cache, run_worker=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def api_key(client) -> str:
    resp = client.post("/accounts", json={"email": "young@example.com"})
    assert resp.status_code == 201
    return resp.json()["api_key"]


def auth(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


# ── 공개 엔드포인트 ───────────────────────────────────────────────


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["corp_codes"] == 2


def test_account_creation_returns_the_key_once(client):
    body = client.post("/accounts", json={"email": "a@b.com"}).json()
    assert body["api_key"].startswith("fck_")
    assert "한 번만" in body["warning"]


def test_invalid_email_is_rejected(client):
    assert client.post("/accounts", json={"email": "없음"}).status_code == 422


def test_creating_the_same_account_twice_is_rejected(client):
    """이메일을 검증하지 않으므로 같은 이메일로 다시 만들게 두면 누구나 남의 user_id 에
    묶인 유효한 키를 받아 그 사람의 관심종목을 읽고 지울 수 있었다."""
    first = client.post("/accounts", json={"email": "a@b.com"})
    assert first.status_code == 201
    second = client.post("/accounts", json={"email": "A@B.COM"})
    assert second.status_code == 409
    assert "api_key" not in second.json()
    assert "/me/keys" in second.json()["detail"]


def test_email_is_not_accepted_in_the_query_string(client):
    # 쿼리스트링은 접근 로그에 그대로 남는다. 이메일은 본문으로만 받는다.
    assert client.post("/accounts", params={"email": "a@b.com"}).status_code == 422


def test_additional_keys_require_an_existing_key(client, api_key):
    anonymous = client.post("/me/keys")
    assert anonymous.status_code == 401

    issued = client.post("/me/keys", headers=auth(api_key))
    assert issued.status_code == 201
    new_key = issued.json()["api_key"]
    assert new_key != api_key
    me_old = client.get("/me", headers=auth(api_key)).json()
    me_new = client.get("/me", headers=auth(new_key)).json()
    assert me_old["user_id"] == me_new["user_id"]


# ── 인증 ──────────────────────────────────────────────────────────


def test_protected_endpoints_require_a_key(client):
    for path in ("/me", "/search?q=삼성", "/checkup/005930", "/watchlist"):
        assert client.get(path).status_code == 401


def test_bad_key_is_rejected(client):
    assert client.get("/me", headers=auth("fck_wrong")).status_code == 401


def test_bearer_token_also_works(client, api_key):
    resp = client.get("/me", headers={"Authorization": f"Bearer {api_key}"})
    assert resp.status_code == 200


def test_revoked_key_stops_working(client, cache, api_key):
    from fin_checkup.auth import hash_key

    cache.revoke_api_key(hash_key(api_key))
    assert client.get("/me", headers=auth(api_key)).status_code == 401


# ── 조회 ──────────────────────────────────────────────────────────


def test_me_reports_the_account_only(client, api_key):
    body = client.get("/me", headers=auth(api_key)).json()
    assert body["user_id"]
    assert body["watchlist_count"] == 0
    # 계정 응답은 식별자와 관심종목 수만 담는다.
    assert set(body) == {"user_id", "watchlist_count"}


def test_search(client, api_key):
    body = client.get("/search", params={"q": "삼성"}, headers=auth(api_key)).json()
    assert [h["corp_name"] for h in body] == ["삼성전자"]


def test_checkup_returns_metrics_and_bands(client, api_key):
    body = client.get("/checkup/005930", params={"year": 2024}, headers=auth(api_key)).json()
    assert body["corp_name"] == "삼성전자"
    assert body["sector"] == "general"
    keys = {m["key"] for m in body["metrics"]}
    assert "debt_ratio" in keys and "operating_margin" in keys

    debt = next(m for m in body["metrics"] if m["key"] == "debt_ratio")
    assert debt["band_good"] == 100
    assert debt["higher_is_better"] is False


def test_checkup_response_always_carries_the_disclaimer(client, api_key):
    body = client.get("/checkup/005930", headers=auth(api_key)).json()
    assert "투자권유" in body["disclaimer"]


def test_bank_is_reported_with_its_sector(client, api_key):
    body = client.get("/checkup/105560", headers=auth(api_key)).json()
    assert body["sector"] == "financial"
    debt = next(m for m in body["metrics"] if m["key"] == "debt_ratio")
    assert debt["signal"] == "not_applicable"


def test_unknown_company_is_404(client, api_key):
    assert client.get("/checkup/없는회사", headers=auth(api_key)).status_code == 404


def test_response_never_leaks_the_email_or_key(client, api_key):
    body = client.get("/checkup/005930", headers=auth(api_key)).text
    assert "young@example.com" not in body
    assert api_key not in body


# ── 조회 제약 없음 ────────────────────────────────────────────────


def test_checkup_is_not_rate_limited(client, api_key):
    """조회 횟수를 제한하지 않는다."""
    for _ in range(8):
        assert client.get("/checkup/005930", headers=auth(api_key)).status_code == 200


def test_history_is_not_capped(client, api_key):
    body = client.get(
        "/checkup/005930", params={"years": 10}, headers=auth(api_key)
    ).json()
    assert "상한" not in body["disclaimer"]


# ── 관심종목 ──────────────────────────────────────────────────────


def test_watchlist_add_list_remove(client, api_key):
    assert client.get("/watchlist", headers=auth(api_key)).json() == []

    added = client.post("/watchlist/005930", headers=auth(api_key))
    assert added.status_code == 201
    assert [w["corp_name"] for w in added.json()] == ["삼성전자"]

    assert client.delete("/watchlist/00126380", headers=auth(api_key)).status_code == 200
    assert client.get("/watchlist", headers=auth(api_key)).json() == []


def test_watchlist_has_no_limit(client, cache, api_key):
    from fin_checkup.auth import hash_key

    user_id = cache.resolve_api_key(hash_key(api_key))
    for i in range(9):
        cache.add_watch(user_id, CorpCode(corp_code=f"x{i}", corp_name=f"기존{i}"))
    assert client.post("/watchlist/005930", headers=auth(api_key)).status_code == 201


def test_watchlists_are_isolated_between_users(client, api_key):
    other = client.post("/accounts", json={"email": "other@b.com"}).json()["api_key"]
    client.post("/watchlist/005930", headers=auth(api_key))
    assert client.get("/watchlist", headers=auth(other)).json() == []


def test_removing_something_not_watched_is_404(client, api_key):
    assert client.delete("/watchlist/00126380", headers=auth(api_key)).status_code == 404


# ── 문서 ──────────────────────────────────────────────────────────


def test_openapi_states_it_is_not_advice(client):
    schema = client.get("/openapi.json").json()
    assert "투자권유" in schema["info"]["description"]
