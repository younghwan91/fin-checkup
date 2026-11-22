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
