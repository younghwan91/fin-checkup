"""Streamlit 화면 스모크 테스트.

AppTest는 app.py를 실제로 실행한다. 렌더링 중 예외가 나면 곧바로 잡힌다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

from fin_checkup.config import settings  # noqa: E402
from fin_checkup.models import AccountLine, CorpCode, FsDiv, RawStatements, ReportCode  # noqa: E402
from fin_checkup.storage import Cache  # noqa: E402

APP_PATH = Path(__file__).resolve().parents[1] / "src" / "fin_checkup" / "app.py"


def statements(year: int, revenue: float, operating_income: float) -> RawStatements:
    def ln(sj, aid, nm, amount, order):
        return AccountLine(sj_div=sj, account_id=aid, account_nm=nm, thstrm_amount=amount, ord=order)

    return RawStatements(
        corp_code="00126380", bsns_year=year, reprt_code=ReportCode.ANNUAL, fs_div=FsDiv.CFS,
        lines=[
            ln("BS", "ifrs-full_Assets", "자산총계", 10000.0, 1),
            ln("BS", "ifrs-full_Liabilities", "부채총계", 4000.0, 2),
            ln("BS", "ifrs-full_Equity", "자본총계", 6000.0, 3),
            ln("BS", "ifrs-full_IssuedCapital", "자본금", 1000.0, 4),
            ln("BS", "ifrs-full_CurrentAssets", "유동자산", 5000.0, 5),
            ln("BS", "ifrs-full_CurrentLiabilities", "유동부채", 2000.0, 6),
            ln("BS", "ifrs-full_Inventories", "재고자산", 800.0, 7),
            ln("IS", "ifrs-full_Revenue", "매출액", revenue, 8),
            ln("IS", "dart_OperatingIncomeLoss", "영업이익", operating_income, 9),
            ln("IS", "ifrs-full_ProfitLoss", "당기순이익", operating_income * 0.8, 10),
            ln("IS", "ifrs-full_InterestExpense", "이자비용", 50.0, 11),
            ln("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", 900.0, 12),
        ],
    )


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """앱이 볼 캐시 DB를 준비하고 settings가 그걸 가리키게 한다."""
    import streamlit as st

    db_path = tmp_path / "app.duckdb"
    monkeypatch.setattr(settings, "fin_checkup_db_path", db_path)
    monkeypatch.setattr(settings, "dart_api_key", "")  # 네트워크를 타지 않게

    with Cache(db_path) as cache:
        cache.save_corp_codes(
            [CorpCode(corp_code="00126380", corp_name="삼성전자", stock_code="005930")]
        )
        for year, revenue, op in [
            (2021, 1000.0, 200.0),
            (2022, 1100.0, 180.0),
            (2023, 1200.0, 150.0),
            (2024, 900.0, 40.0),  # 급변: 매출·영업이익 급락
        ]:
            cache.save_statements(statements(year, revenue, op))

    st.cache_resource.clear()
    yield db_path
    st.cache_resource.clear()


def run_app(**session_state) -> AppTest:
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    for key, value in session_state.items():
        app.session_state[key] = value
    return app.run()


def all_text(app) -> str:
    """화면에 나간 텍스트 전부. 렌더 방식(title/caption/markdown)에 묶이지 않게."""
    parts = [e.value for e in app.markdown]
    parts += [e.value for e in app.caption]
    parts += [e.value for e in app.title]
    parts += [e.value for e in app.subheader]
    return " ".join(str(p) for p in parts)


def test_app_renders_without_a_query(seeded_db):
    app = run_app()
    assert not app.exception
    assert "재무 건강검진" in all_text(app)


def test_app_warns_when_api_key_missing(seeded_db):
    app = run_app()
    assert any("DART 인증키가 없습니다" in e.value for e in app.sidebar.error)


def test_app_renders_checkup_for_a_known_stock(seeded_db):
    app = run_app()
    app.sidebar.text_input[0].set_value("005930").run()
    assert not app.exception
    assert "삼성전자" in all_text(app)


def test_app_shows_no_result_message_for_unknown_query(seeded_db):
    app = run_app()
    app.sidebar.text_input[0].set_value("없는회사").run()
    assert not app.exception
    assert any("찾지 못했습니다" in w.value for w in app.warning)


def test_app_always_shows_the_disclaimer(seeded_db):
    app = run_app()
    app.sidebar.text_input[0].set_value("005930").run()
    text = all_text(app)
    assert "투자권유" in text
    assert "책임지지 않습니다" in text


def test_app_renders_all_four_tabs(seeded_db):
    app = run_app()
    app.sidebar.text_input[0].set_value("005930").run()
    assert not app.exception
    labels = {tab.label for tab in app.tabs}
    assert {"한 장 요약", "위험 신호", "급변 감지", "추이"} <= labels


def test_app_reports_a_locked_cache_instead_of_crashing(seeded_db, monkeypatch):
    """다른 프로세스가 캐시를 쓰고 있을 때.

    읽기 전용 폴백은 소용없다 — DuckDB는 writer가 락을 잡고 있으면 read_only
    연결도 거부한다. 우회하는 대신 무엇을 해야 하는지 알려야 한다.
    """
    import streamlit as st

    from fin_checkup.storage import CacheLocked

    def locked(*_args, **_kwargs):
        raise CacheLocked("다른 프로세스가 쓰고 있습니다")

    monkeypatch.setattr("fin_checkup.storage.db.Cache.__init__", locked)
    st.cache_resource.clear()

    app = run_app()
    assert not app.exception, "락 충돌에서 예외가 새어나오면 안 된다"
    assert any("다른 프로세스가 쓰고 있습니다" in e.value for e in app.error)
    st.cache_resource.clear()


def test_metric_tooltips_do_not_leak_into_the_page(seeded_db):
    """title 속성에 날것의 줄바꿈을 넣으면 마크다운이 태그를 끊어 내용이 새어나온다.

    실제로 겪었다 — 설명·계산식이 본문 텍스트로 쏟아지고 `">` 조각까지 보였다.
    """
    import re

    app = run_app()
    app.sidebar.text_input[0].set_value("005930").run()
    assert not app.exception

    tooltips = 0
    for element in app.markdown:
        for attr in re.findall(r'title="([^"]*)"', str(element.value)):
            tooltips += 1
            assert "\n" not in attr, "title 속성에 날것의 줄바꿈이 들어갔다"
            assert "계산식" in attr or "적용하지 않는다" in attr

    assert tooltips >= 10, "지표 카드에 판정 근거 툴팁이 붙어 있어야 한다"

    # 툴팁 내용이 본문으로 새어나오면 설명이 카드 밖 텍스트로 중복된다.
    stripped = re.sub(r"<[^>]*>", "", " ".join(str(e.value) for e in app.markdown))
    assert "계산식:" not in stripped, "툴팁 내용이 본문 텍스트로 새어나왔다"


def test_signal_is_not_conveyed_by_color_alone(seeded_db):
    """색각 이상이 있어도 읽혀야 한다 — 요약 칩에 색과 함께 글자가 나간다."""
    app = run_app()
    app.sidebar.text_input[0].set_value("005930").run()
    body = " ".join(str(e.value) for e in app.markdown)
    for label in ("위험", "주의", "정상"):
        assert label in body


def test_sidebar_chrome_rules_keep_the_expand_button_alive():
    """사이드바 펼치기 버튼은 stToolbar 안에 있다.

    툴바를 통째로 display:none 하면 사이드바를 접은 뒤 되돌릴 방법이 사라진다.
    실제로 그렇게 만들었다가 막혔다. CSS가 툴바 자체를 숨기지 않는지 고정한다.
    """
    import re

    from fin_checkup.theme import CSS

    # display:none 대상에서 stToolbar 자체가 빠져 있어야 한다.
    hidden = re.findall(r"([^{}]+)\{[^{}]*display:\s*none[^{}]*\}", CSS)
    targets = " ".join(hidden)
    assert '[data-testid="stToolbar"]' not in targets, "툴바를 숨기면 사이드바를 못 편다"
    assert '[data-testid="stToolbarActions"]' in targets, "배포·메뉴는 숨겨야 한다"

    # 헤더 높이를 0으로 눌러도 같은 문제가 생긴다.
    assert "height: 0" not in CSS.replace(" ", "").replace("height:0", "height: 0")


def test_chart_series_colors_do_not_reuse_signal_colors():
    """같은 화면에서 초록 선이 '정상'으로 읽히면 안 된다."""
    from fin_checkup.theme import SERIES_COLORS, SIGNAL_COLORS

    overlap = set(SERIES_COLORS) & set(SIGNAL_COLORS.values())
    assert not overlap, f"계열색이 상태색과 겹친다: {overlap}"


@pytest.fixture
def db_with_peers(tmp_path, monkeypatch):
    """동종업계 표본이 있는 캐시. 대조군 표시를 검증하려면 견줄 대상이 있어야 한다."""
    import streamlit as st

    from fin_checkup.models import Company

    db_path = tmp_path / "peers.duckdb"
    monkeypatch.setattr(settings, "fin_checkup_db_path", db_path)
    monkeypatch.setattr(settings, "dart_api_key", "")

    with Cache(db_path) as cache:
        codes = [CorpCode(corp_code="00126380", corp_name="삼성전자", stock_code="005930")]
        cache.save_company(Company(corp_code="00126380", corp_name="삼성전자",
                                   stock_code="005930", industry_code="26410"))
        for year, rev, op in [(2023, 1200.0, 150.0), (2024, 900.0, 40.0)]:
            cache.save_statements(statements(year, rev, op))

        # 앞 두 자리(26)만 같은 동종업계 8곳 — 5자리로 맞추면 하나도 안 잡힌다.
        for i in range(8):
            code = f"9200000{i}"
            codes.append(CorpCode(corp_code=code, corp_name=f"동종{i}", stock_code=f"90000{i}"))
            cache.save_company(Company(corp_code=code, corp_name=f"동종{i}",
                                       industry_code=f"26{i}90"))
            for year in (2023, 2024):
                raw = statements(year, 800.0 + i * 60, 30.0 + i * 12)
                raw.corp_code = code
                cache.save_statements(raw)
        cache.save_corp_codes(codes)

    st.cache_resource.clear()
    yield db_path
    st.cache_resource.clear()


def test_cards_show_what_they_were_compared_against(db_with_peers):
    """'10.88%가 좋은 건가'에 답하려면 견줄 대상이 화면에 있어야 한다."""
    app = run_app()
    app.sidebar.text_input[0].set_value("005930").run()
    assert not app.exception

    body = " ".join(str(e.value) for e in app.markdown)
    assert "동종업계" in body, "무엇과 견줬는지 밝혀야 한다"
    assert "개사 중" in body
    assert "중앙값" in body


def test_summary_line_answers_where_this_company_sits(db_with_peers):
    app = run_app()
    app.sidebar.text_input[0].set_value("005930").run()
    body = " ".join(str(e.value) for e in app.markdown)
    assert "상위 25% 안에" in body and "하위 25%에" in body


def test_summary_line_is_not_a_score(db_with_peers):
    """종합 점수·등급은 만들지 않는다 — 규제선이자 제품 원칙이다."""
    app = run_app()
    app.sidebar.text_input[0].set_value("005930").run()
    body = " ".join(str(e.value) for e in app.markdown)
    for banned in ("점수", "종합등급", "투자등급", "A등급", "매수", "매도", "추천"):
        assert banned not in body, f"화면에 '{banned}'가 나왔다"


def test_cards_show_change_from_last_year(db_with_peers):
    """대조군이 답하지 못하는 '우리가 나아졌나'에 답한다."""
    app = run_app()
    app.sidebar.text_input[0].set_value("005930").run()
    body = " ".join(str(e.value) for e in app.markdown)
    assert "작년" in body
    assert "개선" in body or "악화" in body
