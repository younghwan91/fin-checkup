"""자동 수집 워크플로(collect.yml)의 순서·권한·키 범위 가드.

이 워크플로는 secret DART_API_KEY 로 DART 를 부르고, 공개 릴리스를 만들고, 옛 릴리스를 지운다.
키가 다른 단계로 새거나(로그·산출물), 스냅샷을 들여오기 전에 수집하거나(빈 캐시라 전 상장사를 다시 묻는다),
Pages 배포 요청이 빠지면(GITHUB_TOKEN 릴리스는 release 트리거를 깨우지 않는다) 조용히 틀어진다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "collect.yml"


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _load()["jobs"]["collect"]["steps"]


def _index(steps: list[dict], needle: str) -> int:
    for i, step in enumerate(steps):
        if needle in (step.get("run") or ""):
            return i
    raise AssertionError(f"collect.yml 에 {needle!r} 를 부르는 단계가 없다")


def test_triggers_daily_before_pages_and_by_dispatch() -> None:
    wf = _load()
    on = wf[True] if True in wf else wf["on"]  # YAML 1.1 은 on 을 True 로 읽는다
    assert on["schedule"] == [{"cron": "0 19 * * *"}]  # 04:00 KST, pages.yml 07:00 KST 전
    assert {"budget", "years"} <= set(on["workflow_dispatch"]["inputs"])


def test_permissions_and_concurrency() -> None:
    wf = _load()
    assert wf["permissions"] == {"contents": "write", "actions": "write"}
    assert wf["concurrency"]["group"] == "collect"
    assert wf["concurrency"]["cancel-in-progress"] is False
    assert wf["jobs"]["collect"]["timeout-minutes"] <= 360


def test_order_import_collect_backfill_publish_prune_dispatch() -> None:
    steps = _steps()
    order = [
        _index(steps, "scripts/fetch_snapshot.sh"),
        _index(steps, "fin_checkup.cli collect"),
        _index(steps, "fin_checkup.cli backfill"),
        _index(steps, "scripts/publish_snapshot.sh"),
        _index(steps, "gh release delete"),
        _index(steps, "gh workflow run pages.yml"),
    ]
    assert order == sorted(order)


def test_dart_key_only_where_needed() -> None:
    with_key = [step.get("name") for step in _steps() if "DART_API_KEY" in str(step.get("env") or {})]
    assert with_key == ["증분 수집", "새 스냅샷 릴리스"]
    assert "DART_API_KEY" not in str(_load().get("env") or {})


def test_collect_stops_itself_before_the_job_timeout() -> None:
    run = _steps()[_index(_steps(), "fin_checkup.cli collect")]["run"]
    assert "--max-minutes" in run and "--summary-json" in run
    minutes = int(run.split("--max-minutes", 1)[1].split()[0])
    assert minutes + 30 <= _load()["jobs"]["collect"]["timeout-minutes"]


def test_prune_never_deletes_the_new_release() -> None:
    run = _steps()[_index(_steps(), "gh release delete")]["run"]
    assert ".[7:]" in run and "NEW_TAG" in run


def test_pages_and_collect_share_the_snapshot_script() -> None:
    pages = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    assert "scripts/fetch_snapshot.sh" in pages
    script = (ROOT / "scripts" / "fetch_snapshot.sh").read_text(encoding="utf-8")
    assert "fin_checkup.cli snapshot import" in script


def test_snapshots_are_ordered_by_publish_time_not_commit_time() -> None:
    """gh 의 createdAt 은 릴리스가 가리키는 커밋 시각이다 — 같은 main 커밋 위에 매일 올린 스냅샷은
    createdAt 이 모두 같아서 '가장 최근' 이 아무것이나 되고, 정리 단계가 방금 것보다 옛것을 남길 수 있었다
    (2026-11-22 data-20261122: createdAt 12:31:03 = 커밋 시각, publishedAt 12:37:23)."""
    script = (ROOT / "scripts" / "fetch_snapshot.sh").read_text(encoding="utf-8")
    prune = _steps()[_index(_steps(), "gh release delete")]["run"]
    for text in (script, prune):
        assert "sort_by(.publishedAt)" in text
        assert "sort_by(.createdAt)" not in text
