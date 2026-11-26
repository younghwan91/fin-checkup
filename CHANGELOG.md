# Changelog

버전은 코드 릴리스(`v*` 태그)입니다. 데이터 스냅샷(`data-*` 릴리스)은 매일 따로 올라가며 여기에 적지 않습니다.

## [Unreleased]

- **Dataset 구조화 데이터** — `/agents/` 의 JSON-LD 가 정적 JSON(`api/index.json`·`api/c/<종목코드>.json`)을 schema.org
  `Dataset` 으로 알린다(Google 데이터세트 검색). 라이선스 칸은 원천 데이터의 OpenDART 이용약관 — 데이터에 MIT 를 붙이지 않는다.
- **IndexNow** — 사이트 배포가 끝나면 그 빌드에서 바뀐 페이지 주소를 IndexNow(Bing·네이버·Yandex·Seznam)에 알린다.
  검색 엔진 계정이 없어도 된다. 처음 한 번은 색인 페이지 전부.
- **검색어에 맞춘 제목** — 회사 페이지 `삼성전자 재무제표 신호등 — 부채비율·영업이익률·ROE 등 17개 지표 (2025)`,
  업종 페이지 `… 업종 재무비율 — 상장사 N곳 중앙값·사분위 (2025)`. og:·twitter: 제목도 같이 바뀐다.
- **최근 분기** — 회사 페이지·회사 JSON(`latest_quarter`)·마크다운·MCP `diagnose_company` 에 사업보고서 뒤에 나온 분기·반기보고서의
  매출·영업이익·순이익(그 분기 석 달, 1년 전 같은 분기와 나란히)과 자산·부채·자본·부채비율을 판정 없이 싣는다. 신호등은 그대로
  사업보고서로만 매긴다. 연간 부채비율이 ⊘ 인 업종은 분기 부채비율도 ⊘. DART 다중회사 주요계정으로 분기당 ~28회 호출.
- **Gemini CLI 확장** — `gemini extensions install https://github.com/younghwan91/fin-checkup` 으로 MCP 서버와 측정 규칙
  (`GEMINI.md`)을 함께 설치한다. DART 키 칸은 선택(키체인 저장). 저장소 루트 `gemini-extension.json` 버전은 릴리스 태그 확인에 들어간다.

## [0.1.1] — 2026-11-23

첫 버전 릴리스입니다. (`v0.1.0` 태그는 릴리스 워크플로가 번들을 만들기 전에 멈춰 릴리스·레지스트리 항목이 없습니다 —
내용은 같고, 테스트 의존성 `pyyaml` 을 `dev` extra 에 적었습니다.)

- **사이트** — [younghwan91.github.io/fin-checkup](https://younghwan91.github.io/fin-checkup/): 상장사 2,642곳(코스피·코스닥·코넥스)의
  재무 지표 17개를 신호(🟢정상 🟡주의 🔴위험 ⚪측정값 ⊘해당 없음 ⚫데이터 없음)로. 지표마다 계산식·경계값·작년 값·같은 업종
  분포를 함께 보여 주고, 최근 위험 공시는 사실과 DART 원문 링크만. 회사 비교, 지표·업종 해설, 정적 JSON·피드·배지.
- **에이전트가 읽는 길** — `llms.txt`·`llms-full.txt`, 회사별 마크다운, `openapi.json`. ChatGPT·Claude.ai 에 주소만 붙여 넣으면 된다.
- **MCP 서버** `fin-checkup-mcp` — 도구 8개(회사 찾기·진단·지표 해설·비교·위험 공시·데이터 상태·설치 상태·설치 안내).
  키 없이 공개 사이트를 읽고, 원하면 개인 OpenDART 키로 사이트에 없는 회사·연도를 받아 온다.
- **Claude Desktop 확장** — 릴리스 파일 [`fin-checkup.mcpb`](https://github.com/younghwan91/fin-checkup/releases/download/v0.1.1/fin-checkup.mcpb)
  를 두 번 눌러 설치(uv·git 불필요, DART 키 칸은 선택). 공식 MCP Registry 항목 `io.github.younghwan91/fin-checkup`.
- **Claude Code 플러그인** — `claude plugin marketplace add younghwan91/fin-checkup` → `claude plugin install fin-checkup@fin-checkup`
  (진단·비교·설치 스킬 + MCP). Codex·Cursor·Copilot 용 Agent Skills 사본(`.agents/skills/`).
- **설치 도우미** `fin-checkup setup` — 키 없이 되는 것부터 점검하고, OpenDART 키는 화면·로그에 남기지 않고 사용자 설정 파일에 넣는다.
- **자동 데이터** — 매일 DART 증분 수집 → `data-*` 스냅샷 릴리스 → 사이트 다시 만들기.

본 결과는 공시 수치를 공개된 재무비율 공식으로 계산한 측정값입니다. 투자권유·자문이 아니며, 데이터 정확성과 이용에 따른 손익에 책임지지 않습니다.
