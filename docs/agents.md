# 에이전트에서 쓰기 — MCP 서버

Claude Code·Claude Desktop·Cursor·Codex·VS Code 같은 MCP 클라이언트에서 "삼성전자 재무 진단해줘" 라고 하면,
fin-checkup 이 계산한 신호등 측정값을 에이전트가 그대로 받아 보여 줍니다.

- **설정이 필요 없습니다.** 기본 데이터는 공개 사이트([younghwan91.github.io/fin-checkup](https://younghwan91.github.io/fin-checkup/))의
  정적 JSON 입니다. DART 인증키가 없어도 됩니다.
- **측정값이며 투자권유·추천이 아닙니다.** 도구 설명과 결과에 보여 주는 법(`presentation_guidance`)과 면책 문구가
  들어 있고, 에이전트는 사거나 팔라는 말을 하지 않도록 안내받습니다 ([넘지 않는 선](#넘지-않는-선)).
- 필요한 것: [`uv`](https://docs.astral.sh/uv/) (`uvx` 가 함께 깔립니다). 저장소를 내려받을 필요는 없습니다.

서버를 띄우는 명령은 하나입니다. 아래 클라이언트 설정은 모두 이 명령을 씁니다.

```bash
uvx --from "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup" fin-checkup-mcp
```

## 설치

### Claude Code

```bash
claude mcp add fin-checkup -- uvx --from "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup" fin-checkup-mcp
```

`claude mcp list` 에 `fin-checkup` 이 보이면 됩니다. 모든 프로젝트에서 쓰려면 `--scope user` 를 붙입니다.

플러그인(`/plugin marketplace add younghwan91/fin-checkup` → `/plugin install fin-checkup@fin-checkup`)으로 설치하면 이 MCP 설정과
진단·비교 스킬이 함께 들어옵니다 — [`agents-skill.md`](agents-skill.md). MCP 가 없는 채팅 도구(ChatGPT·Claude.ai)용 지침도 거기 있습니다.

### Claude Desktop

설정 파일(`claude_desktop_config.json` — macOS `~/Library/Application Support/Claude/`, Windows `%APPDATA%\Claude\`)에:

```json
{
  "mcpServers": {
    "fin-checkup": {
      "command": "uvx",
      "args": [
        "--from",
        "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup",
        "fin-checkup-mcp"
      ]
    }
  }
}
```

저장한 뒤 Claude Desktop 을 다시 엽니다.

### Cursor

`~/.cursor/mcp.json`(모든 프로젝트) 또는 프로젝트의 `.cursor/mcp.json` 에 위의 Claude Desktop 과 같은 `mcpServers` 블록을 넣습니다.

### Codex CLI

`~/.codex/config.toml` 에:

```toml
[mcp_servers.fin-checkup]
command = "uvx"
args = ["--from", "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup", "fin-checkup-mcp"]
```

### VS Code (Copilot 에이전트 모드)

프로젝트의 `.vscode/mcp.json` 에:

```json
{
  "servers": {
    "fin-checkup": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup",
        "fin-checkup-mcp"
      ]
    }
  }
}
```

### 그 밖의 클라이언트 · HTTP

stdio 로 명령을 띄우는 클라이언트면 위 명령 그대로입니다. HTTP(Streamable HTTP)가 필요하면:

```bash
uvx --from "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup" fin-checkup-mcp \
  --transport streamable-http --port 8000      # → http://127.0.0.1:8000/mcp
```

## 도구

| 도구 | 하는 일 |
|---|---|
| `search_companies(query)` | 회사 이름·6자리 종목코드·초성(`ㅅㅅㅈㅈ`)으로 찾기. 순서는 "얼마나 맞았나 → 이름순" |
| `diagnose_company(company, year=None)` | 회사 하나의 진단 — 지표 17개마다 신호·값·계산식·경계값·동종업계 중앙값·작년 값·"왜 이 색" 문장, 위험 신호, 최근 위험 공시(DART 원문 링크), 출처, 면책, `presentation_guidance` |
| `explain_metric(key_or_label)` | 지표 하나의 쉬운 설명 — 정의·계산식·업종별 경계값과 근거·한계·⊘ 업종과 대신 볼 것 |
| `compare_companies(companies)` | 2~4곳을 정해진 지표 순서로 나란히. 회사 순서는 입력 순서이고 우열을 매기지 않음 |
| `recent_risk_disclosures(company=None, days=7)` | 최근 위험 공시의 사실 목록 + DART 원문 링크 (회사 하나 또는 시장 전체) |
| `data_status()` | 데이터 기준 — 빌드 날짜·사업연도·기대한 연도가 반영됐는지·공시 수집 기간 |
| `setup_status(check_key=False)` | 설치 상태 — 출처 모드·사이트 연결·개인 DART 키가 설정됐는지(예/아니오와 어디서 오는지만, 키 값은 없음)·개인 캐시 연도·다음 할 일. `check_key` 면 DART 한 번으로 키 확인 |
| `setup_guide(client=None, want_key=False)` | 클라이언트별 설치 단계(Claude Code·Desktop·Cursor·VS Code·Codex·Gemini CLI)와 키 받는 법·넣는 법. 단계마다 `ask_first`·`user_runs`, `rules_for_agent` |

프롬프트 `diagnose`(회사 이름 하나)는 위 도구를 순서대로 쓰고 결과를 규칙대로 전하게 안내합니다.
리소스: `fin-checkup://legend`(신호 범례) · `fin-checkup://policy`(넘지 않는 선) · `fin-checkup://metrics`(지표 17개 표).

회사가 여럿 맞으면(`삼성`) 진단 대신 `status: "ambiguous"` 와 후보 목록이, 없으면 `not_found` 가 옵니다.
공개 사이트는 회사마다 최신 사업연도 하나만 싣습니다 — 다른 연도를 물으면 `year_unavailable` 로 답하고 값을 만들어 내지 않습니다.
개인 DART 인증키를 넣었으면(`fin-checkup setup`) 사이트에 없는 회사·사업연도만 DART 에서 받아 같은 모양으로 돌려주고
결과에 `source: "dart-live"` 가 붙습니다 — 판정 규칙은 사이트와 같고, 동종업계 중앙값은 개인 캐시에 같은 업종 회사가 있어야 나옵니다.

## 대화 예

> **사용자:** 삼성전자 재무 진단해줘
>
> **에이전트:** *(diagnose_company 호출)* 삼성전자(005930, KOSPI) 2025 사업연도 사업보고서(연결) 기준 측정값입니다.
>
> - 영업이익률 13.07% 🟢 정상 — 계산식: 영업이익 ÷ 매출액 × 100, 경계값: 정상 7% 이상 · 주의 0% 이상 · 그 미만은 위험.
>   동종업계 294개사 중앙값 2.00%, 작년 10.88%.
> - … (지표 17개, 묶음별)
> - 재무제표로 확인된 위험 신호: 없음. 최근 7일 위험 공시 1건 — 2026-11-18 임원ㆍ주요주주특정증권등소유상황보고서 ([DART 원문](https://dart.fss.or.kr/))
>
> 본 결과는 공시 수치를 공개된 재무비율 공식으로 계산한 측정값입니다. 투자권유·자문이 아니며, 데이터 정확성과 이용에 따른 손익에 책임지지 않습니다.
> 회사 페이지: https://younghwan91.github.io/fin-checkup/c/005930/
>
> **사용자:** 그럼 지금 사도 돼?
>
> **에이전트:** 그런 판단은 드리지 않습니다. 위 숫자가 무엇을 뜻하는지는 설명드릴 수 있어요 — 예를 들어 영업이익률이 왜 이 색인지 …

## 넘지 않는 선

fin-checkup 은 측정 도구입니다. 종목 추천이나 투자 판단은 하지 않습니다. MCP 로 쓰는 에이전트도 같은 선을 지킵니다
(도구 설명·결과의 `presentation_guidance`·리소스 `fin-checkup://policy` 에 적혀 있습니다. 근거는 [`REGULATORY.md`](REGULATORY.md)).

- 사거나 팔라는 말을 하지 않는다. 사용자가 판단을 물으면 판단은 하지 않는다고 말하고 측정값과 읽는 법만.
- 값마다 계산식·경계값을 함께. 종합 점수·등급·순위를 만들지 않는다. 비교는 나란히 놓을 뿐 이긴 쪽이 없다.
- ⚫ 데이터 없음은 0 도 나쁨도 아니다 — 공시에서 계정을 찾지 못한 것. ⊘ 해당 없음은 "대신 볼 것" 과 함께.
- 결과에 없는 값·연도를 지어내지 않는다.
- 위험 공시는 사실과 DART 원문 링크까지. 해석·전망을 붙이지 않는다.
- 끝에 면책 문구와 출처 링크.

## 설정 (전부 선택)

| 환경변수 | 기본값 | 뜻 |
|---|---|---|
| `FIN_CHECKUP_MCP_SOURCE` | (비움) | 비우면 공개 사이트 — DART 키가 있으면 사이트에 없는 회사·연도만 DART 에서(`site+dart`). `site` 는 키가 있어도 사이트만, `dart` 는 진단을 늘 DART 에서, `local` 은 로컬 DuckDB 캐시(DART 호출 0) |
| `DART_API_KEY` | (사용자 설정 파일) | 개인 OpenDART 인증키. 보통은 `fin-checkup setup` 이 넣은 `~/.config/fin-checkup/.env`(Windows `%APPDATA%\fin-checkup\.env`)에서 읽으니 따로 줄 필요가 없다 |
| `FIN_CHECKUP_DB_PATH` | `data/fin_checkup.duckdb` · `dart` 류는 `~/.cache/fin-checkup-mcp/dart.duckdb` | `local` 일 때 DB 위치(읽기 전용). `site+dart`·`dart` 일 때는 DART 에서 받은 것을 쌓는 개인 캐시 |
| `FIN_CHECKUP_MCP_BASE_URL` | 공개 사이트 | 직접 빌드한 사이트(`site build`)를 가리킬 때 |
| `FIN_CHECKUP_MCP_CACHE_DIR` | `~/.cache/fin-checkup-mcp` | 디스크 캐시 위치. `off` 면 메모리만 |
| `FIN_CHECKUP_MCP_TTL` | `600` | 캐시를 다시 확인하기까지 초 (사이트의 `max-age` 와 같다). 지나면 ETag 로 재검증 |

클라이언트 설정에 넣을 땐 `"env": {"FIN_CHECKUP_MCP_SOURCE": "local", "FIN_CHECKUP_DB_PATH": "/절대/경로.duckdb"}` 처럼
(Codex 는 `env = { ... }`). 로컬 모드는 저장소를 받아 데이터를 모은 사람용입니다 — [README](../README.md#개발자라면).

## 문제 해결

- **`uvx` 를 찾을 수 없다** — uv 를 설치합니다(`curl -LsSf https://astral.sh/uv/install.sh | sh`). GUI 앱(Claude Desktop·Cursor)은
  셸의 PATH 를 못 볼 수 있어 `"command"` 에 절대경로(`which uvx` 결과)를 넣습니다.
- **처음 실행이 오래 걸린다** — 첫 `uvx` 가 git 에서 받아 설치합니다(수십 초). 다음부터는 uv 캐시를 씁니다.
  최신 코드로 다시 받으려면 `uvx --refresh --from … fin-checkup-mcp`.
- **"데이터를 받지 못했습니다"** — 공개 사이트에 연결하지 못한 것입니다. 네트워크·프록시를 확인합니다. 예전에 받은 사본이 있으면 그걸 쓰고
  결과의 `data_freshness.stale` 이 `true` 가 됩니다.
- **회사가 안 나온다** — 상장사(KOSPI·KOSDAQ·KONEX) 중 사이트에 재무가 있는 회사만 있습니다. `search_companies` 로 표기를 확인합니다.
- **로컬 모드에서 DB 를 열지 못했다** — DuckDB 는 쓰는 프로세스가 하나뿐입니다. `serve`·수집이 돌고 있으면 끝난 뒤 다시.
- **도구가 안 보인다** — 클라이언트를 다시 시작하고, 직접 띄워 오류를 봅니다: 위 `uvx` 명령을 터미널에서 실행하면 stdio 로 대기합니다(Ctrl-C 로 끝).
  `npx @modelcontextprotocol/inspector uvx --from … fin-checkup-mcp` 로 도구를 하나씩 불러 볼 수도 있습니다.

코드는 `src/fin_checkup/mcp/` (`client.py` 출처·캐시, `core.py` 도구 결과, `server.py` MCP 배선), 테스트는 `tests/test_mcp.py`.
