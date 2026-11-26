"""Pages 배포 워크플로의 순서 가드.

스냅샷의 metric_values 는 만든 시점 코드의 계산 결과라, 지표 계산을 고친 뒤에도 배포가 옛 대조군을
그대로 내보낼 수 있었다(#19·#36·#41 이 각자 옛 값을 지웠지만 재계산은 수동이었다). 스냅샷을 들여온
뒤·사이트를 빌드하기 전에 지금 코드로 다시 계산하는 단계가 빠지지 않게 막는다.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"


def _position(text: str, needle: str) -> int:
    pos = text.find(needle)
    assert pos >= 0, f"pages.yml 에 {needle!r} 가 없다"
    return pos


def test_backfill_runs_between_snapshot_import_and_site_build() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    # 스냅샷 import 는 collect.yml 과 같이 쓰는 스크립트 안에 있다.
    script = (ROOT / "scripts" / "fetch_snapshot.sh").read_text(encoding="utf-8")
    assert "fin_checkup.cli snapshot import" in script
    imported = _position(text, "scripts/fetch_snapshot.sh")
    backfill = _position(text, "fin_checkup.cli backfill")
    build = _position(text, "fin_checkup.cli site build")
    assert imported < backfill < build


def test_indexnow_plans_before_the_guard_and_pings_only_after_a_main_deploy() -> None:
    """IndexNow 는 배포 뒤·운영 사이트에서만 보내고, 실패해도 배포를 실패로 만들지 않는다."""
    text = WORKFLOW.read_text(encoding="utf-8")
    build = _position(text, "fin_checkup.cli site build")
    plan = _position(text, "fin_checkup.site.indexnow plan")
    guard = _position(text, "fin_checkup.cli site check")
    assert build < plan < guard, "지문 파일도 가드를 거친다"
    job = text[_position(text, "\n  indexnow:") :]
    assert "needs: [build, deploy]" in job
    assert "github.repository == 'younghwan91/fin-checkup'" in job
    assert "github.ref == 'refs/heads/main'" in job
    assert "github.event_name != 'pull_request'" in job
    submit = _position(job, "fin_checkup.site.indexnow submit")
    assert "continue-on-error: true" in job[job.rfind("- name:", 0, submit) : submit]
    assert "pull_request" not in text[: _position(text, "permissions:")], "PR 로는 돌지 않는다"
