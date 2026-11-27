"""`examples/compare_signals.py` 가 실제 빌드의 정적 JSON 으로 도는지 — 스키마가 바뀌면 예제도 깨진다."""

from __future__ import annotations

import importlib.util
import io
import json
from datetime import date
from pathlib import Path

import pytest
from hub_seed import seed_site_cache

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.metrics.engine import METRIC_DEFS
from fin_checkup.site.build import build_site
from fin_checkup.storage import Cache

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "compare_signals.py"


def _load():
    spec = importlib.util.spec_from_file_location("compare_signals", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


example = _load()


@pytest.fixture
async def site(tmp_path) -> Path:
    with Cache(tmp_path / "site.duckdb") as cache:
        seed_site_cache(cache)
        out = tmp_path / "out"
        await build_site(cache, out, 2024, built_on=date(2026, 11, 21))
    return out


async def test_prints_two_companies_side_by_side_with_disclaimer(site, capsys):
    assert example.main(["005930", "105560", "--base", str(site)]) == 0
    text = capsys.readouterr().out
    lines = text.splitlines()
    header = next(line for line in lines if line.startswith("지표"))
    assert "삼성전자" in header and "KB금융" in header
    assert DISCLAIMER in text
    assert "DART" in text


async def test_rows_follow_engine_order_not_a_ranking(site, capsys):
    example.main(["005930", "105560", "--base", str(site)])
    lines = capsys.readouterr().out.splitlines()
    labels = [d.label for d in METRIC_DEFS]
    rows = [next(i for i, line in enumerate(lines) if line.startswith(label + " ")) for label in labels]
    assert rows == sorted(rows)


async def test_missing_value_is_not_shown_as_zero(site):
    a = json.loads((site / "api" / "c" / "005930.json").read_text(encoding="utf-8"))
    fcf = next(m for m in a["metrics"] if m["key"] == "fcf")
    assert fcf["value"] is None  # 픽스처: CapEx 가 없다
    assert example.cell(fcf, a["currency"]) == "⚫ 데이터 없음 값 없음"


async def test_not_applicable_metric_keeps_its_own_thresholds(site, capsys):
    example.main(["005930", "105560", "--base", str(site)])
    debt = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("부채비율"))
    assert "⊘" in debt
    assert "KB금융:" in debt  # 금융업은 경계값을 적용하지 않는다 — 삼성전자 경계값으로 덮지 않는다


async def test_unknown_stock_code_stops_with_a_message(site):
    with pytest.raises(SystemExit, match="999999"):
        example.main(["005930", "999999", "--base", str(site)])


def test_http_base_fetches_from_the_site(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request.full_url)
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(example.urllib.request, "urlopen", fake_urlopen)
    assert example.fetch_json("https://example.github.io/fin-checkup/", "api/index.json") == {"ok": True}
    assert seen == ["https://example.github.io/fin-checkup/api/index.json"]


def test_missing_base_dir_exits_with_one_line_message(tmp_path):
    missing = tmp_path / "no-such-dir"
    with pytest.raises(SystemExit, match="파일을 읽지 못했습니다"):
        example.main(["005930", "105560", "--base", str(missing)])


def test_http_error_exits_with_one_line_message(monkeypatch):
    def fail_urlopen(request, timeout):
        raise example.urllib.error.URLError("offline")

    monkeypatch.setattr(example.urllib.request, "urlopen", fail_urlopen)
    with pytest.raises(SystemExit, match="데이터를 받지 못했습니다"):
        example.fetch_json("https://example.github.io/fin-checkup/", "api/index.json")
