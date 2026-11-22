"""정적 사이트 빌드 — 캐시만 읽고, 회사 페이지가 판정 근거·⚫·⊘·면책을 그대로 싣는지."""

from __future__ import annotations

import html
from datetime import date

import pytest
from hub_seed import DELISTED, HLB, KB, SAMSUNG, seed_site_cache
from site_text import unmark

from fin_checkup.cli import main
from fin_checkup.config import settings
from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.format import format_metric
from fin_checkup.presentation import band_text
from fin_checkup.site import registry
from fin_checkup.site.build import MARKER, build_site, collect_companies
from fin_checkup.site.context import root_for, safe_output_path
from fin_checkup.storage import Cache

BUILT_ON = date(2026, 11, 21)


@pytest.fixture
def cache(tmp_path):
    with Cache(tmp_path / "site.duckdb") as c:
        seed_site_cache(c)
        yield c


@pytest.fixture
async def built(cache, tmp_path):
    out = tmp_path / "out"
    report = await build_site(cache, out, 2024, built_on=BUILT_ON)
    return out, report


def page(out, code: str) -> str:
    return unmark((out / "c" / code / "index.html").read_text(encoding="utf-8"))


# ── 빌드 ──────────────────────────────────────────────────────────


async def test_builds_one_page_per_listed_company_with_statements(built):
    out, report = built
    assert report.companies == 3
    for corp in (SAMSUNG, KB, HLB):
        assert (out / "c" / corp.stock_code / "index.html").exists()
    assert not (out / "c" / "123456").exists(), "재무가 없는 회사는 페이지가 없다"
    assert not (out / "c" / DELISTED.stock_code).exists(), "상장 시장이 아니면(OTHER) 페이지가 없다"
    assert (out / MARKER).exists()


async def test_build_never_calls_dart(cache, tmp_path, monkeypatch):
    """사이트 빌드는 캐시만 읽는다 — 설정에 키가 있어도 DART 클라이언트를 만들지 않는다."""
    import fin_checkup.service as service_module

    def boom(*_a, **_k):
        raise AssertionError("사이트 빌드가 DART 클라이언트를 만들었다")

    monkeypatch.setattr(service_module, "DartClient", boom)
    monkeypatch.setattr(settings, "dart_api_key", "real-looking-key-0123456789")
    report = await build_site(cache, tmp_path / "out", 2024, built_on=BUILT_ON)
    assert report.companies == 3


async def test_limit_takes_the_first_companies_by_stock_code(cache):
    entries, skipped = await collect_companies(cache, 2024, limit=2)
    assert [e.stock_code for e in entries] == ["005930", "028300"]
    assert skipped == 0


async def test_refuses_to_wipe_a_folder_it_did_not_create(cache, tmp_path):
    out = tmp_path / "precious"
    out.mkdir()
    (out / "notes.txt").write_text("지우면 안 됨")
    with pytest.raises(FileExistsError):
        await build_site(cache, out, 2024, built_on=BUILT_ON)
    assert (out / "notes.txt").exists()


async def test_rebuild_replaces_the_previous_output(cache, tmp_path):
    out = tmp_path / "out"
    await build_site(cache, out, 2024, built_on=BUILT_ON)
    (out / "c" / "999999").mkdir()
    await build_site(cache, out, 2024, built_on=BUILT_ON, limit=1)
    assert not (out / "c" / "999999").exists()
    assert not (out / "c" / "105560").exists(), "지난 빌드의 회사가 남지 않는다"


# ── 회사 페이지 내용 ──────────────────────────────────────────────


async def test_company_page_shows_every_metric_with_formula_band_and_value(built, cache):
    from fin_checkup.service import CheckupService

    out, _ = built
    text = page(out, "005930")
    data = await CheckupService(cache).run(SAMSUNG, end_year=2024, allow_fetch=False)
    for metric in data.result.metrics:
        assert html.escape(metric.label) in text
        assert html.escape(metric.formula) in text, f"{metric.key} 계산식이 없다"
        assert html.escape(band_text(metric)) in text, f"{metric.key} 경계값이 없다"
        assert html.escape(format_metric(metric, data.result.currency)) in text


async def test_company_page_carries_disclaimer_and_source(built):
    out, _ = built
    for code in ("005930", "105560", "028300"):
        text = page(out, code)
        assert DISCLAIMER in text
        assert "DART" in text


async def test_missing_accounts_are_black_not_zero(built):
    """CapEx 가 없으면 FCF 는 ⚫ 데이터 없음 — 0원으로 보이면 안 된다."""
    out, _ = built
    text = page(out, "005930")
    fcf = text.split('id="m-fcf"', 1)[1].split("</li>", 1)[0]
    assert "⚫" in fcf and "데이터 없음" in fcf
    assert "공시에서 필요한 계정을 찾지 못했다." in fcf
    assert "0원" not in fcf


async def test_not_applicable_metric_shows_what_to_look_at_instead(built):
    out, _ = built
    text = page(out, "105560")
    debt = text.split('id="m-debt_ratio"', 1)[1].split("</li>", 1)[0]
    assert "⊘" in debt and "해당 없음" in debt
    assert "대신 볼 것" in debt and "BIS" in debt
    assert "은행·증권·금융지주로 분류돼" in text


async def test_signal_is_never_color_alone(built):
    out, _ = built
    text = page(out, "005930")
    for chunk in text.split('class="fc-sig"')[1:]:
        label = chunk.split("</span>", 2)[1]
        assert label.strip(), "신호엔 기호 옆에 글자(정상/주의/위험…)가 붙는다"


async def test_changes_section_compares_with_the_prior_year(built):
    out, _ = built
    samsung = page(out, "005930")
    assert "2023 → 2024년" in samsung
    assert "영업이익률" in samsung.split('id="changes"', 1)[1]
    assert "직전 사업연도 재무가 없어" in page(out, "028300")


async def test_company_name_is_escaped(cache, tmp_path):
    from fin_checkup.models import CorpCode

    evil = CorpCode(corp_code=SAMSUNG.corp_code, corp_name="<script>alert(1)</script>", stock_code="005930")
    cache.save_corp_codes([evil])
    out = tmp_path / "out"
    await build_site(cache, out, 2024, built_on=BUILT_ON)
    text = page(out, "005930")
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


# ── 레지스트리 ────────────────────────────────────────────────────


def test_page_modules_register_themselves():
    pages = registry.load_pages()
    assert "company" in pages


def test_duplicate_page_name_from_another_module_is_rejected(monkeypatch):
    monkeypatch.setattr(registry, "PAGES", dict(registry.PAGES))
    registry.load_pages()

    def other(site):
        return []

    with pytest.raises(ValueError, match="company"):
        registry.page("company")(other)


async def test_company_context_providers_extend_the_template_context(cache, monkeypatch):
    from fin_checkup.site.pages.company import company_page_context

    monkeypatch.setattr(registry, "COMPANY_CONTEXTS", {})
    import fin_checkup.site.pages.company as company_module

    monkeypatch.setattr(company_module, "COMPANY_CONTEXTS", registry.COMPANY_CONTEXTS)
    seen = []

    @registry.company_context("peers")
    def peers(entry, site):
        seen.append(entry.stock_code)
        return {"peers_view": "ok"}

    entries, _ = await collect_companies(cache, 2024, limit=1)
    context = company_page_context(entries[0], site=None)
    assert context["peers_view"] == "ok" and seen == ["005930"]

    @registry.company_context("bad")
    def bad(entry, site):
        return {"c": "덮어쓰기"}

    with pytest.raises(ValueError, match="bad"):
        company_page_context(entries[0], site=None)


def test_relative_root_follows_page_depth():
    assert root_for("index.html") == "./"
    assert root_for("c/005930/index.html") == "../../"


@pytest.mark.parametrize("bad", ["../x.html", "/etc/passwd", "c/../../x", "c//x.html", "", "c\\x"])
def test_output_paths_cannot_escape_the_out_dir(bad):
    with pytest.raises(ValueError):
        safe_output_path(bad)


# ── CLI ───────────────────────────────────────────────────────────


def test_cli_site_build_reads_the_cache_read_only(tmp_path, monkeypatch, capsys):
    db = tmp_path / "cli.duckdb"
    with Cache(db) as c:
        seed_site_cache(c)
    monkeypatch.setattr(settings, "fin_checkup_db_path", db)
    out = tmp_path / "site-out"

    assert main(["site", "build", "--year", "2024", "--out", str(out), "--limit", "1"]) == 0
    assert (out / "c" / "005930" / "index.html").exists()
    printed = capsys.readouterr().out
    assert "회사 1곳" in printed and "회사당" in printed


def test_cli_site_build_without_a_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(settings, "fin_checkup_db_path", tmp_path / "missing.duckdb")
    assert main(["site", "build", "--out", str(tmp_path / "o")]) == 1
    assert "캐시 DB 가 없습니다" in capsys.readouterr().out
