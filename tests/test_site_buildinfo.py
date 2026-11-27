"""빌드 메타 `/build.json` · 모든 페이지 꼬리의 데이터 기준·빌드 줄 · 사업연도 신선도 경고."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from hub_seed import html_pages, seed_site_cache

from fin_checkup.cli import main
from fin_checkup.clock import KST
from fin_checkup.config import settings
from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.site.build import MARKER, build_site
from fin_checkup.site.buildinfo import BUILD_JSON, SnapshotInfo, freshness_warning, read_snapshot_info
from fin_checkup.site.guard import check_site
from fin_checkup.storage import Cache

SHA = "0123456789abcdef0123456789abcdef01234567"
BUILT_AT = datetime(2026, 11, 21, 7, 12, 3, tzinfo=KST)
SNAPSHOT = SnapshotInfo(built_on="2026-11-20", built_at="2026-11-20T23:01:00+09:00", bsns_years=(2023, 2024))


async def build(tmp_path, name="out", **kw):
    with Cache(tmp_path / f"{name}.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp_path / name
        report = await build_site(cache, target, 2024, built_on=BUILT_AT.date(), built_at=BUILT_AT, **kw)
    return target, report


@pytest.fixture
async def built(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", SHA)
    return await build(tmp_path, snapshot=SNAPSHOT, images=False)


def load(out):
    return json.loads((out / BUILD_JSON).read_text(encoding="utf-8"))


async def test_build_json_describes_time_years_snapshot_sha_and_counts(built):
    out, report = built
    doc = load(out)
    assert doc["disclaimer"] == DISCLAIMER
    assert doc["built_at"] == "2026-11-21T07:12:03+09:00"
    assert doc["built_on"] == "2026-11-21"
    assert doc["fiscal_year"] == 2024
    assert doc["fiscal_years_in_data"] == [2022, 2023, 2024]
    assert doc["expected_fiscal_year"] == 2025  # 2026-11 이면 2025 사업보고서가 나와 있을 때다
    assert doc["fresh"] is False
    assert doc["snapshot"] == {
        "built_on": "2026-11-20",
        "built_at": "2026-11-20T23:01:00+09:00",
        "bsns_years": [2023, 2024],
    }
    # 40자리 16진수는 가드가 인증키로 본다 — 짧게 싣는다.
    assert doc["git_sha"] == SHA[:12]
    files = [p for p in out.rglob("*") if p.is_file() and p.name != MARKER]
    assert doc["counts"] == {
        "companies": 3,
        "skipped": report.skipped,
        "pages": sum(1 for p in files if p.suffix == ".html"),
        "files": len(files),
    }
    assert report.files == len(files)


async def test_every_page_footer_names_the_data_basis_and_links_build_json(built):
    out, _ = built
    for path in html_pages(out):
        text = path.read_text(encoding="utf-8")
        assert "데이터 기준: 2024 사업연도 (스냅샷 2026-11-20) · 빌드 2026-11-21" in text, path
        assert 'build.json">빌드 정보</a>' in text, path


async def test_build_with_metadata_passes_every_guard(built):
    out, _ = built
    assert check_site(out) == []


async def test_old_fiscal_year_is_warned_in_the_report(built):
    _, report = built
    assert any("2024" in w and "2025" in w for w in report.warnings)


async def test_current_fiscal_year_is_not_warned(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    with Cache(tmp_path / "s.duckdb") as cache:
        seed_site_cache(cache)
        when = datetime(2025, 11, 21, 7, 0, tzinfo=KST)
        report = await build_site(
            cache, tmp_path / "o", 2024, built_on=when.date(), built_at=when, images=False
        )
    assert report.warnings == []
    doc = load(tmp_path / "o")
    assert doc["fresh"] is True
    assert doc["snapshot"] is None


@pytest.mark.parametrize(
    ("newest", "today", "warned"),
    [
        (2024, date(2026, 3, 31), False),  # 3월엔 아직 2024 가 기대값
        (2024, date(2026, 4, 1), True),
        (2025, date(2026, 11, 21), False),
        (None, date(2026, 11, 21), True),
    ],
)
def test_freshness_follows_default_fiscal_year(newest, today, warned):
    assert bool(freshness_warning(newest, today)) is warned


def test_snapshot_manifest_is_read_from_the_import_directory(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "built_on": "2026-11-20", "built_at": "x", "bsns_years": [2024]}),
        encoding="utf-8",
    )
    assert read_snapshot_info(tmp_path) == SnapshotInfo("2026-11-20", "x", (2024,))


def test_cli_build_reads_snapshot_and_warns_about_an_old_year(tmp_path, monkeypatch, capsys):
    db = tmp_path / "cli.duckdb"
    with Cache(db) as c:
        seed_site_cache(c)
    monkeypatch.setattr(settings, "fin_checkup_db_path", db)
    monkeypatch.setattr("fin_checkup.site.build.kst_today", lambda: date(2026, 11, 21))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)  # Actions 에선 "경고: " 대신 ::warning:: 으로 찍는다
    (tmp_path / "snap").mkdir()
    (tmp_path / "snap" / "manifest.json").write_text(
        json.dumps({"built_on": "2026-11-20", "built_at": "y", "bsns_years": [2024]}), encoding="utf-8"
    )
    out = tmp_path / "site-out"
    args = [
        "site",
        "build",
        "--year",
        "2024",
        "--out",
        str(out),
        "--no-images",
        "--snapshot",
        str(tmp_path / "snap"),
    ]
    assert main(args) == 0
    printed = capsys.readouterr().out
    assert "2025" in printed and "경고" in printed
    assert load(out)["snapshot"]["built_on"] == "2026-11-20"

    # site check 도 같은 경고를 내지만 위반은 아니다 (종료 코드 0).
    monkeypatch.setattr("fin_checkup.site.buildinfo.kst_today", lambda: date(2026, 11, 21))
    assert main(["site", "check", str(out)]) == 0
    assert "2025" in capsys.readouterr().out
