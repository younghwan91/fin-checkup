# 기업분석 허브 — 실행 계획·진척 장부

설계: [`../specs/2026-11-21-analysis-hub-design.md`](../specs/2026-11-21-analysis-hub-design.md)

**완료 기준:** 의미 있는 PR 50개 이상이 main 에 병합 · Pages 사이트가 전 상장사 페이지를 서빙 ·
모든 페이지가 면책·계산식 가드 테스트 통과 · CI 녹색.

## 작업 방식

- 레인마다 Orca 워크트리 하나(`~/git/.harness-wt/`), PR 마다 `origin/main` 위에 새 브랜치 → TDD → `ruff check` ·
  `pytest -q` → PR → CI 녹색 → `gh pr merge --merge --delete-branch`.
- 워크트리에는 `.env`·`data/` 가 없다. 실데이터 빌드 확인은 읽기 전용 사본
  `~/git/.harness-wt/fin-checkup-data/fin_checkup.duckdb` 를 `FIN_CHECKUP_DB_PATH` 로 가리켜 한다.
- 충돌을 줄이려고 기반(F) 레인이 **페이지 레지스트리**(페이지 종류마다 모듈 하나)와 **회사 페이지 부분 템플릿**
  (섹션마다 파일 하나)을 먼저 만든다. 다른 레인은 자기 모듈·부분 템플릿만 새로 만든다.

## PR 목록

상태: ☐ 대기 · ◐ 진행 · ☑ 병합(#번호)

### F. 기반 (순차)
| # | PR | 상태 |
|---|---|---|
| F1 | 설계·계획서 | ☑ |
| F2 | `site` 패키지 · 페이지 레지스트리 · 회사 페이지 렌더(부분 템플릿) · `cli site build` | ☑ |
| F3 | 공통 레이아웃·CSS(모바일·다크모드) · 랜딩 | ☑ |
| F4 | 검색 인덱스 JSON · 이름/종목코드/초성 검색 | ☑ |
| F5 | 가드 테스트: 모든 HTML 에 면책 · 판정 지표마다 계산식·경계값 · 외부 스크립트 없음(CSP) · `site check` | ☑ |
| F6 | 금지 문구 린트 (추천·매수·매도·TOP 등) — 템플릿·해설·카드 · `site check` | ☑ |
| F7 | 공개 스냅샷 export — 테이블 허용목록 · 개인정보 테이블 차단 테스트 | ☑ |
| F8 | Pages 배포 워크플로 (스냅샷 릴리스 → 빌드 → 배포) | ☑ |
| F9 | 404 · 파비콘 · 접근성 기본(랜드마크·대비·키보드) · Pages 배포 전 `site check` | ☑ |

### U. 이해
| # | PR | 상태 |
|---|---|---|
| U1 | 지표 해설 데이터 17개 (`explain/metrics.py` — `site/` 가 아니라 UI 무관 패키지 `fin_checkup.explain` 로 옮김: 사이트·Streamlit·CLI 공용) | ☑ |
| U2 | 지표 해설 페이지 `/m/<지표>/` (색과 무관한 예시 회사 규칙) | ☑ |
| U3 | "왜 이 색" 한 문장 — 값·경계값·업종 분위수 (`explain/why.py` `why_color`) | ☑ |
| U4 | 위험 신호 해설 (`explain/redflags.py`) | ☑ |
| U5 | 위험 공시 유형 해설 (`explain/disclosures.py`) | ☑ |
| U6 | 용어집 60개 (`explain/glossary.py`) | ☑ |
| U7 | 재무제표 처음 읽기 가이드 | ☑ |
| U8 | 업종별 읽는 법 (금융·보험·지주 ⊘) | ☑ |

### V. 시각
| # | PR | 상태 |
|---|---|---|
| V1 | 5개년 추이 인라인 SVG (`site/charts.py` `trend_svg`) | ☑ |
| V2 | 동종업계 분포 위 내 위치 SVG (`site/charts.py` `peer_strip`) | ☑ |
| V3 | 공유 카드 OG 이미지 | ☑ |
| V4 | 카드뉴스 이미지 생성 (1080×1350) | ☑ |
| V5 | 블로그 임베드 배지 SVG `/badge/<코드>.svg` (`charts.badge_svg`) | ☑ |
| V6 | 카드뉴스 — 잘림 수정·요약에 센 지표는 모두 카드에 | ☑ |

### E. 탐색
| # | PR | 상태 |
|---|---|---|
| E1 | 업종 페이지 `/s/<업종>/` (분포·이름순, 순위표 없음) | ☑ |
| E2 | 회사 페이지 "같은 업종 회사" | ☑ |
| E3 | 회사별 JSON `/api/c/<코드>.json` · `/api/index.json` (스키마: `docs/api-static.md`) | ☑ |
| E4 | 비교 페이지 `/compare/` (`pages/compare.py` · `static/compare.js`, 검색창 고르기 모드 `data-fc-pick`) | ☑ |
| E5 | 시장 전체 분포 페이지 `/market/` | ☑ |
| E6 | 지표 해설 ↔ 회사 ↔ 업종 상호 링크 | ☑ |

### R. 돌아옴
| # | PR | 상태 |
|---|---|---|
| R1 | 관심종목 (localStorage) `/watch/` | ☑ |
| R2 | 지난 방문 이후 바뀐 것 | ☑ |
| R3 | 사업보고서 시즌 안내 · 데이터 기준일 | ☑ |
| R4 | 회사별 Atom 피드 | ☑ |
| R5 | OG/트위터 메타 · canonical | ☑ |
| R6 | sitemap.xml · robots.txt | ☑ |
| R7 | 링크 복사·공유 (추적 없음) | ☑ |

### D. 데이터
| # | PR | 상태 |
|---|---|---|
| D1 | 2025 사업연도 반영 (수집 + 스냅샷 `data-20261121`) | ☑ |
| D2 | 최근 위험 공시 수집 스크립트 (Actions, secret) | ☑ |
| D3 | 회사 페이지 "최근 위험 공시" · 첫 화면 시장 전체 공시 (`site build --disclosures` → `site/disclosures.DisclosureFeed`, 피드(R4)와 같은 검증·기준 공유) | ☑ |
| D4 | 스케줄 재빌드 워크플로 (#13 의 매일 07:00 KST 크론 · #62 배포마다 재계산) | ☑ |
| D5 | 빌드 메타·신선도 표시 (`/build.json` · 꼬리 줄 · `site/buildinfo.py`, `site build --snapshot`) | ☑ |
| D6 | 빌드 병렬화·증분 (`--jobs` · `--incremental`, `site/incremental.py`; 87초 → 55초 → 재빌드 9초) | ☑ |
| D7 | 2022·2021 채우기 (추이 5년, 스냅샷 `data-20261121-2`) | ☑ |
| D8 | 증분 수집 `collect --budget N [--years ...]` (`src/fin_checkup/collect.py`, 상장사 중 빈 (회사, 연도)만 · 예산/020/시간에서 멈추고 0 · `collect-summary` JSON · 빈 응답 TTL 시즌 3일/평소 30일 `fiscal.miss_ttl_days`) | ☑ |
| D9 | `collect.yml` — 매일 04:00 KST 스냅샷 받아 증분 수집 → 새 `data-*` 릴리스 → 옛 릴리스 정리(7개) → `pages.yml` 디스패치 (`scripts/fetch_snapshot.sh` 공유) | ☑ |
| D10 | OPERATIONS 자동 갱신 절 (일정·예산·수동 실행·지난 연도 채우기·한도 초과 대응) | ☑ |
| D11 | 스냅샷 "가장 최근"·정리 순서를 `publishedAt` 으로 (gh `createdAt` 은 태그가 가리키는 커밋 시각 — 같은 main 커밋 위 스냅샷끼리 순서가 없었다) | ☑ |

### M. 알리기·기여
| # | PR | 상태 |
|---|---|---|
| M1 | README 최상단 사이트 링크·화면 | ☑ |
| M2 | 영어 README | ☑ |
| M3 | CONTRIBUTING · 이슈/PR 템플릿 | ☑ |
| M4 | OPERATIONS 에 사이트 운영 절 | ☑ |
| M5 | 블로그 임베드 가이드 | ☑ |
| M6 | 개발자용 JSON 사용 예 | ☑ |
| M7 | 링크 검사 CI | ☑ |
| M8 | 색약 대응 점검 (색 + 기호) | ☑ |

### Q. 정확성
| # | PR | 상태 |
|---|---|---|
| Q1 | 이자보상배율 — 금융원가 대체값을 판정하지 않고 이자 지급액을 먼저 쓴다 | ☑ |
| Q2 | 상장 시장(KOSPI·KOSDAQ·KONEX)만 모집단 — 사이트·업종/시장 대조군에서 OTHER(상장폐지·비상장) 제외 | ☑ |
| Q3 | 시장 전체 대조군의 2,000곳 상한 제거 | ☑ |
| Q4 | 위험 신호 규정 안내 사실 확인 — 코스닥 '4년 연속 영업손실 → 관리종목' 은 2022년 말 삭제, 지금은 5년 연속 → 투자주의환기종목 | ☑ |
| Q5 | SEC 이자보상배율 — 순이자비용을 이자비용으로 쓰지 않는다(총액 → 이자 지급액 → ⚪ 대체값) | ☑ |
| Q6 | 매출채권회전율 — 순수 매출채권 줄을 합계보다 먼저, 미수금 줄은 매출채권으로 쓰지 않는다 | ☑ |
| Q7 | FCF 설비투자 — 종류별로만 적은 회사는 한 줄이 아니라 합으로, 유·무형 합계 줄은 비고 | ☑ |
| Q8 | 성장성·작년 대비 — 작년 보고서가 아니라 올해 보고서의 전기 열(재작성·같은 연결 기준)과 견준다 | ☑ |
| Q9 | 매출액 — 금융지주·은행의 보험 자회사 '보험수익' 줄과 0 으로 온 머리 줄을 매출로 쓰지 않는다 (#64 에 함께 병합) | ☑ |
| Q10 | 계정명 대체 매칭 — 로마 숫자 머리·주석 번호, 지배+비지배 순이익 합, '창출된 현금' 줄은 영업활동현금흐름이 아니다 (#64 에 함께 병합) | ☑ |
| Q11 | 영업이익·순이익 증가율 — 흑자에서 적자로 돌아선 해는 증가율과 함께 '적자 전환' 을 밝힌다 | ☑ |

### X. 첫 사용자 경험
라이브 사이트를 세 사람(50대 휴대폰 투자자·재무제표를 배우는 대학생·블로거)으로 걸어 본 조사(2026-11-21)에서 나온 순서.

| # | PR | 상태 |
|---|---|---|
| X1 | 회사 첫 화면 "한눈에 보기" — 묶음별 지표 이름·신호, 위험 신호 유무, 신호등은 판단이 아니라는 한 줄 (`company/_glance.html`) | ☑ |
| X2 | 휴대폰 지표 카드 줄이기 — 계산식·경계값은 늘 보이고, 동종업계 분포 그림은 요약 한 줄 아래 한 번 눌러 펼침 (`company/_peer_strip.html`) | ☑ |
| X3 | 돌아온 사람의 길 — 메뉴로 `/compare/` 에 들어가면 고른 회사가 지워지던 것, 랜딩에서 관심종목·비교로 바로 (`static/resume.js`) | ☑ |
| X4 | 업종 이름이 실제 구성을 말한다 — KSIC 64 "금융업" → "금융업·지주회사" (상장 125곳 중 74곳이 64992 지주회사, 대부분 비금융) | ☑ |
| X5 | 첫 화면 — 검색 아래 결과 예시 하나(고정 규칙 예시 회사의 한눈에 보기 첫 묶음), 데이터 기준일은 한 줄로 접고 자세히는 한 번 눌러 (`index.html`·`_season.html`) | ☑ |
| X6 | 신호 표시는 CSS 모양 + 글자 — ⚪ 가 흑백 "○" 로 나와 빈 값처럼 보이던 것. 정상 ●·주의 ▲·위험 ■·측정값 ▬·해당 없음 ⊘·데이터 없음 ◌ (`_signal.html`·`render.signal_marks`·compare/watch.js). 피드·JSON·meta 는 이모지 그대로 | ☑ |
| X7 | 작년 대비는 값의 움직임과 평가를 따로 — "작년 118.70% ↑ 악화" 는 화살표와 낱말이 반대로 읽혔다. "작년 X → 올해 Y (23.46%p 높아짐 · 낮을수록 좋게 보는 지표라 나빠진 쪽)", 방향은 `Band.higher_is_better`, 경계값 없는 지표(회전율)는 평가 없이 차이만. 사이트 카드는 두 줄, JSON·Streamlit·MCP 는 `SelfDelta.summary` 한 줄 (`metrics/peers.py`) | ☑ |

### A. 에이전트가 쓰는 길
"삼성전자 재무 진단해줘" 라고 하면 코딩·채팅 에이전트가 fin-checkup 을 쓴다 — 측정값·계산식·경계값·면책 그대로.

| # | PR | 상태 |
|---|---|---|
| A1 | `/llms.txt` · `/llms-full.txt` — 에이전트가 지킬 선·GET 두세 번 진단법·비교·신호 읽기, full 은 지표 해설 17개 펼침 (`pages/llms.py`). robots.txt 주석·꼬리 링크, 가드·링크 검사가 에이전트용 글(`guard.is_agent_text`)도 본다 | ☑ |
| A2 | 회사별 마크다운 `/c/<코드>/index.md`(+`/c/<코드>.md`, `pages/company_md.py`) · HTML 에 `rel="alternate" type="text/markdown"` · JSON 에 `summary_ko`(`explain/summary.py` `facts_summary`)·`agent_guidance`(`explain/agents.py`) — 필드 추가라 schema_version 1 그대로 | ☑ |
| A3 | `/openapi.json` (OpenAPI 3.1, 정적 GET 10개 · 스키마 열거값은 코드 Enum 에서, `pages/openapi.py`) — 빌드한 JSON 을 `jsonschema` 로 엄격 검증 · `/agents/` 에이전트에서 쓰기(붙여 넣는 프롬프트·curl·MCP/플러그인 문서, `pages/agents.py`) · 꼬리 링크 | ☑ |
| A4 | MCP 서버 `fin-checkup-mcp` (`src/fin_checkup/mcp/`, extra `[mcp]`) — 공개 사이트 JSON(키 불필요, 메모리+디스크 캐시·ETag) 또는 `FIN_CHECKUP_MCP_SOURCE=local`. 도구 6개(`search_companies`·`diagnose_company`·`explain_metric`·`compare_companies`·`recent_risk_disclosures`·`data_status`), 프롬프트 `diagnose`, 리소스 범례·넘지 않는 선·지표 | ☑ |
| A5 | MCP 설치·사용 문서 `docs/agents.md` — Claude Code·Desktop·Cursor·Codex·VS Code 설정, 도구 표, 대화 예, 넘지 않는 선, 환경변수, 문제 해결. 가드 `tests/test_docs_agents.py`(명령이 플러그인 `.mcp.json` 과 같은지·JSON/TOML 이 읽히는지·도구 표 = 서버 도구) | ☑ |
| A7 | Claude Code 플러그인 마켓플레이스 — `.claude-plugin/marketplace.json` · `plugins/fin-checkup/`(스킬 `diagnose`·`compare`, `.mcp.json` 은 `fin-checkup-mcp`, MCP 없으면 정적 JSON). `/plugin marketplace add younghwan91/fin-checkup` → `/plugin install fin-checkup@fin-checkup`. 가드 `tests/test_agent_plugin.py` | ☑ |
| A8 | 다른 에이전트용 — Agent Skills 표준 경로 사본 `.agents/skills/fin-checkup-*`(Codex·Cursor·Copilot, `scripts/sync_agent_skills.py` 로 생성·테스트가 어긋남을 막음) · 루트 `AGENTS.md` · ChatGPT/Claude.ai 지침 블록 `docs/agents-skill.md` | ☑ |
| A9 | `cli setup` 설치 도우미(`onboarding.py`) — Python·uv·공개 사이트 점검, '키 없이 된다' 먼저, OpenDART 인증키 받는 법(2026-11-22 사이트 메뉴 그대로)·getpass/`--key-stdin`/`--from-clipboard` 로 받아 DART 한 번(company.json)으로 유효/무효/한도 확인 → `.env` 원자적 쓰기·권한 600. `--check` 는 비밀 없이 상태만. `Settings` 가 사용자 설정 파일(`~/.config/fin-checkup/.env`)도 읽고, 콘솔 스크립트 `fin-checkup`(uvx 로 저장소 없이). 가드 `tests/test_setup.py` | ☑ |
| A10 | MCP 가 개인 키로 DART 에서 받아 오기 — 사이트에 없는 회사·연도는 `CheckupService(allow_fetch=True)` 로(`source: "dart-live"`, 호출 한도 지킴, 키는 응답에 없음) · 도구 `setup_status`·`setup_guide(client, want_key)` | ☑ |
| A11 | 스킬 `setup`(플러그인 + `.agents/skills/` 사본) — 설치·MCP 연결·키 발급을 에이전트가 안내하되 키는 채팅에 받지 않는다 · `docs/agents.md` '설치·키 발급을 에이전트에게 맡기기' · `AGENTS.md` | ☑ |
| A12 | Claude Desktop 확장 `.mcpb`(`mcpb/manifest.json`, server.type `uv` — 번들의 `pyproject.toml`·`uv.lock` 을 `uv run --frozen --extra mcp` 로, 선택·민감 `dart_api_key` → env `DART_API_KEY`, 빈 값·자리표시자는 없는 것으로 `drop_unset_key`) · 공식 MCP Registry `server.json`(`io.github.younghwan91/fin-checkup`, `mcpb` 패키지) · `release.yml`(v* 태그 → 태그=버전 확인·pack·validate·stdio 실행 확인·Release(Latest)·sha256 채워 OIDC publish) · CI `mcpb` 잡 · data-* 가 Latest 를 가져가면 v* 로 되돌림(`publish_snapshot.sh`). 도우미 `scripts/release.py`, 가드 `tests/test_release.py`(공개 스키마 사본 `tests/fixtures/schemas/`) | ☑ |
| A13 | 문서 — `docs/agents.md` 'Claude Desktop 원클릭 (.mcpb)'(최신 릴리스 파일 받기·두 번 눌러 설치·선택 키 칸) · README 두 벌에 한 줄 · `CHANGELOG.md` v0.1.0(릴리스 설명은 이 절에서, `release.py notes`) | ☑ |
| A14 | 릴리스 고침 v0.1.1 — v0.1.0 태그의 release.yml 이 `tests/test_release.py` 에서 `import yaml` 실패(pyyaml 이 다른 extra 를 타고만 들어와 `--extra mcp --extra dev` 엔 없었다). `pyyaml` 을 `dev` 에, CI `mcpb` 잡이 같은 설치·같은 테스트를 PR 마다, 레지스트리 올리기 전 `mcp-publisher validate`. 버전 0.1.1 | ☑ |

### B. 브랜드
차분하고 정직한 측정 도구로 보이게 — 로켓·캔들·돈·위쪽 화살표 없이. 마크·색·말투는 `docs/brand/README.md`.

| # | PR | 상태 |
|---|---|---|
| B1 | 브랜드 마크 — 귀퉁이 접은 보고서 + 신호 모양 ●▲■(`src/fin_checkup/brand.py` 한 곳에서 SVG·PNG·ICO). `docs/brand/`(마크·워드마크 밝은/어두운, PNG 512·256·128, 브랜드 안내 — `scripts/brand_assets.py` 로 생성, `tests/test_brand.py` 가 어긋남을 막음) · 사이트 파비콘(다크 모드 대응 SVG)·머리글 마크 | ☑ |
| B2 | README 배너(밝은/어두운 `<picture>`, `scripts/brand_images.py`) · 라이브 사이트 데모 GIF(`scripts/record_demo.py`, 초성 검색 → 한눈에 보기 → 지표 해설) · GitHub 소셜 미리보기 `docs/images/social-preview.png`(오너가 Settings 에서 올림) | ☑ |
| B3 | 공유 카드(`/og/<코드>.png`)·카드뉴스 모든 장 머리에 마크(`Canvas.logo`, `brand.draw_tight_mark`) · PNG 신호 기호를 사이트 모양 ● ▲ ■ ▬ ⊘ ◌ 로(`Canvas.signal_mark` — 원 여섯 개였다) | ☑ |

마케팅 문안 초안은 `_workspace/marketing/` (gitignore).
