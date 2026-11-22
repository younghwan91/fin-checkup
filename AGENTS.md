# AGENTS.md

이 저장소에서 일하는 코딩 에이전트(Codex·Cursor·Copilot 등)를 위한 안내. 개발 규약 전체는
[`CLAUDE.md`](CLAUDE.md) 에 있다 — 명령(`uv sync --all-extras` · `uv run pytest -q` · `uv run ruff check src tests scripts`),
환경 함정, 커밋 규칙이 거기 있고 이 파일과 같이 따른다.

## 넘지 않는 선 (`docs/REGULATORY.md`)

fin-checkup 은 측정 도구다. 화면·알림·문서·에이전트 답 어디에도 매매 권유·종목 순위·판단 근거를 감춘 종합 점수를
넣지 않는다. 계산식과 경계값을 늘 함께 보여 주고, 없는 값은 ⚫(0 으로 채우지 않는다), 업종에 맞지 않는 지표는 ⊘ +
대신 볼 것. 면책 문구는 `src/fin_checkup/disclaimer.py` 의 `DISCLAIMER` 를 글자 그대로.

## 사용자가 회사 재무를 물으면

"삼성전자 재무 진단해줘", "이 회사 재무 괜찮아?", "diagnose 005930" 처럼 한국 상장사 재무를 물으면 코드를 읽지 말고
스킬을 따른다:

- 한 회사: [`.agents/skills/fin-checkup-diagnose/SKILL.md`](.agents/skills/fin-checkup-diagnose/SKILL.md)
- 여러 회사 나란히: [`.agents/skills/fin-checkup-compare/SKILL.md`](.agents/skills/fin-checkup-compare/SKILL.md)

데이터는 공개 정적 사이트 `https://younghwan91.github.io/fin-checkup/` 의 JSON 이다(키 불필요, 형식은
[`docs/api-static.md`](docs/api-static.md)). 다른 프로젝트·채팅 도구에서 쓰는 법은 [`docs/agents-skill.md`](docs/agents-skill.md).

## 스킬을 고칠 때

원본은 `plugins/fin-checkup/skills/<이름>/SKILL.md`(Claude Code 플러그인) 하나다. `.agents/skills/` 는 생성 사본이라
원본을 고친 뒤 `uv run python scripts/sync_agent_skills.py` 로 다시 만든다 — 다르면 `tests/test_agent_plugin.py` 가 막는다.
