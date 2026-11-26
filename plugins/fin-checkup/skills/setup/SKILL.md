---
name: setup
description: "fin-checkup 설치·연결과 개인 OpenDART 인증키 발급을 돕는다 — uv 확인, MCP 서버 연결(Claude Code·Claude Desktop·Cursor·VS Code·Codex·Gemini CLI), 시험 진단, 그리고 원하면 DART 키 받는 법과 키를 채팅에 넣지 않고 저장하는 법. 이럴 때 쓴다: \"fin-checkup 설치해줘\", \"설치\", \"세팅해줘\", \"MCP 연결해줘\", \"DART 키 받는 거 도와줘\", \"DART 키\", \"API 키 넣고 싶어\", \"인증키 발급\", \"키 넣었는데 되나?\". English: \"set up fin-checkup\", \"install fin-checkup\", connect the fin-checkup MCP server, get an OpenDART API key. 키는 사용자가 직접 입력한다 — 에이전트는 키를 채팅으로 받지 않는다."
license: MIT
compatibility: "셸 실행 도구가 있으면 확인·설치 명령을 돌린다(실행 전에 묻는다). 인터넷 접속 필요."
metadata:
  site: "https://younghwan91.github.io/fin-checkup/"
  source: "https://github.com/younghwan91/fin-checkup"
---

# fin-checkup 설치 · 연결 · 인증키

비개발자가 "fin-checkup 설치해줘", "DART 키 받는 거 도와줘" 라고 하면 이 순서로 돕는다. 목표는 셋이다:
① 에이전트가 fin-checkup 을 쓸 수 있게 연결하고 ② 시험 진단으로 확인하고 ③ 원하면 개인 OpenDART 인증키를
**사용자가 직접** 넣게 안내한다.

## 0. 먼저 지키는 것

- **인증키를 채팅으로 받지 않는다.** "키를 여기 붙여 주세요" 라고 하지 않는다. 사용자가 그래도 붙여 넣으면 그 값을
  쓰지도 옮기지도 않고, 대화 기록에 남았다고 알린 뒤 아래 5번처럼 본인이 직접 넣게 한다.
- 키를 명령줄 인자·에이전트가 돌리는 명령에 넣지 않는다(`echo 키 | …`, `-e DART_API_KEY=키` 를 에이전트가 쓰지 않는다).
  명령 기록과 대화 기록에 남는다.
- `.env`·설정 파일의 키 값을 `cat`·출력·요약하지 않는다. 있는지만 본다: MCP `setup_status()` 또는
  `uvx --from "fin-checkup @ git+https://github.com/younghwan91/fin-checkup" fin-checkup setup --check`.
- `.env` 를 git 에 커밋하지 않는다(`git add .env` 금지).
- 설치 명령(uv 설치)과 설정 파일 수정은 **무엇을 바꾸는지 보여 주고, 사용자가 좋다고 한 뒤에** 한다. 설정 파일은 고치기
  전에 같은 폴더에 사본(`<파일>.bak`)을 둔다.
- 인증키는 본인 것만 쓴다 — 남의 키를 받거나 나눠 쓰게 하지 않는다(OpenDART 이용약관 제19조).

## 1. 상황 파악

- OS: `uname -s` (macOS `Darwin` · Linux) / Windows 는 `$env:OS` 또는 `ver`.
- 클라이언트: 지금 Claude Code 안이면 Claude Code. 아니면 무엇을 쓰는지 묻는다(Claude Desktop · Cursor · VS Code ·
  Codex · Gemini CLI). MCP 도구 `setup_guide(client)` 가 보이면 그 결과를 따른다 — 클라이언트별 설정 경로·스니펫이 있다.
- 이미 연결됐는지: MCP 도구 `setup_status` 가 보이면 이미 붙어 있다 → 4번(확인)으로. 이 플러그인으로 설치했다면
  `.mcp.json` 이 MCP 서버를 함께 띄운다(`/mcp` 에서 `fin-checkup` 이 보인다).

## 2. uv 확인

`uv --version` (그리고 `uvx --version`). 없으면 공식 설치 명령을 **보여 주고 물은 뒤** 실행한다:

- macOS·Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows(PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

설치 뒤 새 터미널(또는 `source ~/.local/bin/env`)에서 `uvx --version` 이 되는지 본다.

## 3. MCP 서버 연결

서버 명령은 하나다: `uvx --from "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup" fin-checkup-mcp`

- **Claude Code**: 물은 뒤
  `claude mcp add fin-checkup --scope user -- uvx --from "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup" fin-checkup-mcp`
  → `claude mcp list` 에 보이면 됨. 플러그인으로 이미 붙어 있으면 건너뛴다.
- **Claude Desktop**: 설정 파일 macOS `~/Library/Application Support/Claude/claude_desktop_config.json`,
  Windows `%APPDATA%\Claude\claude_desktop_config.json`. **Cursor**: `~/.cursor/mcp.json`. **Gemini CLI**:
  `~/.gemini/settings.json`. 셋 다 `mcpServers` 에 `fin-checkup` 블록(`command: "uvx"`, `args` 위 명령). **VS Code**:
  `.vscode/mcp.json` 의 `servers`. **Codex**: `~/.codex/config.toml` 의 `[mcp_servers.fin-checkup]`.
  정확한 스니펫은 `setup_guide(client)` 또는 저장소 `docs/agents.md`. 파일이 있으면 **합친다** — 바꿀 diff 를 보여 주고
  묻고, 사본을 두고 고친다. 고친 뒤 클라이언트를 다시 시작하라고 안내한다.
- **Gemini CLI 는 확장이 더 간단하다**: 사용자가 터미널에서
  `gemini extensions install https://github.com/younghwan91/fin-checkup` (MCP 서버 + 측정 규칙). 인증키 입력칸은 비워 둬도 된다.
- GUI 앱(Desktop·Cursor)이 `uvx` 를 못 찾으면 `command` 에 절대경로(`which uvx` / `where uvx`)를 넣는다.
- 처음 실행은 git 에서 받아 설치하느라 수십 초 걸린다.

## 4. 확인 — 시험 진단

1. MCP `setup_status()` — `site.reachable` 이 `true` 면 연결됨. `next_steps` 를 사용자에게 옮긴다.
2. MCP `diagnose_company("005930")` 를 불러 `diagnose` 스킬 규칙대로 몇 줄만 보여 준다: 지표마다 값·신호·계산식·경계값을
   함께, ⚫ 데이터 없음·⊘ 해당 없음은 그대로(⊘ 는 대신 볼 것과), 끝에 회사 페이지
   `https://younghwan91.github.io/fin-checkup/c/005930/` 와 면책 문구.
3. MCP 가 아직 안 뜨면(재시작 전 등): `curl -s https://younghwan91.github.io/fin-checkup/api/c/005930.json` 으로 데이터가
   열리는지만 확인하고, 클라이언트를 다시 시작한 뒤 1번부터.

답 끝에는 이 문장을 그대로 붙인다:

> 본 결과는 공시 수치를 공개된 재무비율 공식으로 계산한 측정값입니다. 투자권유·자문이 아니며, 데이터 정확성과 이용에 따른 손익에 책임지지 않습니다.

## 5. 개인 OpenDART 인증키 (원할 때만)

먼저 **키가 없어도 된다**고 말한다: 공개 사이트와 MCP 는 키 없이 모든 상장사의 최신 사업연도 진단을 준다. 키는
지난 사업연도("2020년 재무"), 사이트에 없는 회사, 막 나온 사업보고서를 DART 에서 직접 받아 올 때 쓴다.

받는 법(2026-11-22 OpenDART 사이트 메뉴 그대로) — 주소를 보여 주거나, 물은 뒤 브라우저로 연다
(macOS `open` · Linux `xdg-open` · Windows `start`):

1. https://opendart.fss.or.kr → 위 메뉴 「인증키 신청/관리」 → 「인증키 신청」
   (https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do).
2. 사용자 구분 「개인」, 이메일(중복확인)·비밀번호·API 사용환경·API 사용용도 → 「등록」. 사이트 안내:
   '개인회원 : 계정신청 완료 후 즉시 발급', '인증키 신청 결과는 메일로 발송됩니다.'
3. 로그인 → 「인증키 신청/관리」 → 「인증키 관리」에서 영문·숫자 40자 키를 **복사**한다.
4. 무료, 하루 약 20,000건(넘으면 status 020). 본인만 쓰고 남과 나누지 않는다. 한 사람에 키 하나.

넣는 법 — **사용자가 직접 실행**한다(에이전트가 대신 돌리지 않는다):

- Claude Code: 키를 복사한 상태에서 입력창에
  `! uvx --from "fin-checkup @ git+https://github.com/younghwan91/fin-checkup" fin-checkup setup --from-clipboard`
  — 클립보드에서 본인만 읽는 파일(권한 600)로 곧장 가고, 화면·대화에는 "유효/무효/한도" 만 나온다.
- 별도 터미널: `uvx --from "fin-checkup @ git+https://github.com/younghwan91/fin-checkup" fin-checkup setup`
  → 보이지 않는 입력창에 붙여 넣고 Enter. 저장소를 받았다면 그 폴더에서 `uv run python -m fin_checkup.cli setup`.
- 결과가 "무효" 면 「인증키 관리」에서 다시 복사하게 하고, "한도" 면 키는 맞으니 내일 다시 쓰면 된다고 알린다.

## 6. 키를 MCP 서버에 넘기기

`setup` 은 키를 사용자 설정 파일(`~/.config/fin-checkup/.env`, Windows `%APPDATA%\fin-checkup\.env`)에 넣고 MCP
서버가 그 파일을 읽는다 — **클라이언트 설정에 키를 넣지 않아도 된다.** 클라이언트를 다시 시작한 뒤:

1. `setup_status(check_key=true)` → `dart_key.configured` 가 `true`, `dart_key.checked` 가 `"ok"` 면 됨
   (키 값은 결과에 없다 — 보여 달라고 하지 않는다).
2. `diagnose_company("005930", year=2020)` → 결과에 `source: "dart-live"` 가 있으면 DART 에서 받아 온 것.
   이때도 4번처럼 계산식·경계값·⚫/⊘·면책 그대로.

사용자가 굳이 클라이언트 설정의 `env` 로 넘기고 싶어 하면 `setup_guide(client, want_key=true)` 의 `env_alternative`
스니펫을 보여 주고 **사용자가 직접** `<본인 OpenDART 인증키>` 자리를 채우게 한다(VS Code 는 비밀번호 입력칸으로 묻는
`inputs` 방식). 에이전트가 그 파일에 키를 쓰지 않는다.

## 하지 않는 것

<!-- fc:quoted-start -->
- "키를 여기 붙여 주세요" · "제가 대신 .env 에 넣어 드릴게요" · `cat .env` · `echo $DART_API_KEY`
- 묻지 않고 `curl … | sh` 실행, 묻지 않고 설정 파일 덮어쓰기
- 시험 진단 결과로 "이 회사 사도 돼요", "추천 종목" 같은 판단을 덧붙이기
<!-- fc:quoted-end -->

막히면: 저장소 `docs/agents.md` 의 문제 해결, 또는 `fin-checkup setup --check` 결과를 그대로 보여 달라고 한다
(비밀 값은 들어 있지 않다).
