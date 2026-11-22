"""Claude Code 플러그인 스킬을 Agent Skills 표준 경로(`.agents/skills/`)로 복사한다.

Codex·Cursor·VS Code(Copilot) 는 저장소의 `.agents/skills/<이름>/SKILL.md` 를 읽는다. 원본은
`plugins/fin-checkup/skills/<이름>/SKILL.md` 하나이고, 여기서 만든 사본은 이름만 `fin-checkup-<이름>` 으로
바꾼다 — 플러그인 안에서는 `fin-checkup:diagnose` 로 이름공간이 붙지만, 다른 에이전트의 스킬 폴더에선
`diagnose` 만으로는 무엇의 진단인지 알 수 없다. 심볼릭 링크는 Windows·zip 업로드에서 깨져서 쓰지 않는다.

    uv run python scripts/sync_agent_skills.py          # 사본을 다시 쓴다
    uv run python scripts/sync_agent_skills.py --check  # 원본과 다르면 1 로 끝난다 (테스트도 같은 걸 본다)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugins" / "fin-checkup" / "skills"
TARGET = ROOT / ".agents" / "skills"
PREFIX = "fin-checkup-"
NOTE = (
    "<!-- 생성 파일 — 원본은 plugins/fin-checkup/skills/{name}/SKILL.md. "
    "고친 뒤 `uv run python scripts/sync_agent_skills.py` 로 다시 만든다. -->\n"
)


def skill_names() -> list[str]:
    return sorted(p.parent.name for p in SOURCE.glob("*/SKILL.md"))


def render(name: str, text: str, names: list[str]) -> str:
    """원본 SKILL.md → 표준 경로 사본. frontmatter `name` 과 본문의 `스킬` 상호 참조만 바꾼다."""
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not match:
        raise ValueError(f"{name}: frontmatter 가 없다")
    front, body = match.group(1), text[match.end() :]
    front, count = re.subn(rf"^name: {re.escape(name)}$", f"name: {PREFIX}{name}", front, flags=re.M)
    if count != 1:
        raise ValueError(f"{name}: frontmatter 의 name 이 디렉터리 이름과 다르다")
    for other in names:
        body = body.replace(f"`{other}` 스킬", f"`{PREFIX}{other}` 스킬")
    return f"---\n{front}\n---\n{NOTE.format(name=name)}{body}"


def expected() -> dict[Path, str]:
    names = skill_names()
    return {
        TARGET / f"{PREFIX}{name}" / "SKILL.md": render(
            name, (SOURCE / name / "SKILL.md").read_text(encoding="utf-8"), names
        )
        for name in names
    }


def stale() -> list[Path]:
    """다시 만들어야 하는 사본(없거나 다르거나, 원본이 사라진 것)."""
    want = expected()
    have = set(TARGET.glob(f"{PREFIX}*/SKILL.md"))
    out = [p for p, text in want.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    return sorted(out + [p for p in have if p not in want])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="쓰지 않고 다른 것만 알린다")
    args = parser.parse_args(argv)
    paths = stale()
    if args.check:
        for p in paths:
            print(f"다름: {p.relative_to(ROOT)}", file=sys.stderr)
        return 1 if paths else 0
    want = expected()
    for p in paths:
        if p in want:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(want[p], encoding="utf-8")
        else:
            p.unlink()
            p.parent.rmdir()
        print(f"썼다: {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
