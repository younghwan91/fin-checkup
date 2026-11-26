# 정적 JSON

사이트 빌드가 회사 페이지와 같은 값을 JSON 으로도 낸다. 서버가 없는 정적 파일이라 키·호출 제한이 없다.
경로는 사이트 루트 기준이다(GitHub Pages 프로젝트 사이트면 `/fin-checkup/` 아래).

기계가 읽는 명세는 [`/openapi.json`](https://younghwan91.github.io/fin-checkup/openapi.json) (OpenAPI 3.1, 아래 파일
전부의 스키마 — `src/fin_checkup/site/pages/openapi.py`, 빌드한 JSON 을 그 스키마로 검증하는 테스트가 있다).
AI 에이전트용 안내는 [`/llms.txt`](https://younghwan91.github.io/fin-checkup/llms.txt), 사람용 안내는
[`/agents/`](https://younghwan91.github.io/fin-checkup/agents/).

**측정값이다.** 투자권유·자문이 아니며, 모든 파일에 `disclaimer` 가 들어 있다 — 다시 보여 줄 땐 함께 보여 준다.

## `api/index.json`

| 필드 | 뜻 |
|---|---|
| `schema_version` | 스키마 판. 필드를 빼거나 뜻을 바꿀 때만 오른다 (더하는 건 그대로) |
| `disclaimer` · `built_on` · `fiscal_year` | 면책 문구 · 빌드 날짜(KST) · 데이터 사업연도 |
| `companies[]` | `stock_code` · `name` · `market` · `json`(회사 파일 경로) · `page`(회사 페이지 경로), 종목코드순 |

## `api/c/<종목코드>.json`

| 필드 | 뜻 |
|---|---|
| `schema_version` · `disclaimer` · `built_on` · `fiscal_year` | 위와 같다 |
| `summary_ko` | 측정 결과를 평이한 한국어 사실 문단으로 — 어느 보고서로 쟀는지, 신호 개수, 🔴·🟡 구간 지표와 값, ⊘·⚫ 지표, 위험 신호. 평가·권유는 없다 (`explain/summary.py`) |
| `agent_guidance` | AI 에이전트가 이 값을 사람에게 전하는 법 — 고정 문장(영어), 모든 회사 같음 (`explain/agents.py`) |
| `company` | `name` · `stock_code` · `corp_code` · `market` · `industry_code`(KSIC) · `peer_group`(KSIC 앞 두 자리, 업종 비교 단위) · `sector`(`general`·`financial`·`banking`·`real_estate` — `banking` 은 은행·증권·금융지주, 2026-11 추가) · `sector_label` · `page` |
| `report` | `reprt_code`(`11011`=사업보고서) · `label` · `fs_div`(`CFS` 연결 / `OFS` 개별) |
| `currency` · `years` | 금액 통화 · 계산에 쓴 사업연도들 |
| `metrics[]` | 지표 17개, 엔진 정의 순서 (아래) |
| `restated` | 재작성된 추이 칸 — 추이 키(`revenue`·`operating_income`·`net_income`·`operating_cash_flow`·`operating_margin`·`debt_ratio`·`current_ratio`) → `{"연도": 다음 해 보고서의 같은 해 값}`. 그해 보고서 값과 1% 넘게 다른 칸만(분할·합병 재분류 등). 회사 페이지 추이는 그해 보고서 값을 그리고 "재작성" 을 표시한다. 없으면 `{}` (2026-11 추가) |
| `latest_quarter` | 사업보고서 뒤에 나온 최근 분기·반기보고서의 주요계정 — 판정·신호 없음. `flows`(매출액·영업이익·순이익, **그 분기 석 달 값**, `prior` = 1년 전 같은 분기) · `stocks`(자산·부채·자본총계, 분기 말, `prior` = 직전 사업연도 말) · `debt_ratio` · `period_end` · `period_label` · `dart_url`. 못 찾은 값은 `null`(0 아님). 그런 분기가 없으면 `null` (2026-11 추가) |
| `red_flags[]` | 재무제표로 확인된 위험 신호 `key` · `label` · `detail` · `reference`. 없으면 빈 배열 |
| `sources[]` | 출처. OpenDART 요청은 `url` + `params` 로 적는다 — 인증키(`crtfc_key`)는 싣지 않는다 |

`metrics[]` 한 항목:

| 필드 | 뜻 |
|---|---|
| `key` · `label` · `category` | 지표 키(`debt_ratio` …) · 이름 · 묶음(수익성·안정성 …) |
| `value` · `unit` | 값과 단위(`%`·`배`·`money`). 금액(`money`)의 통화는 `currency`. **값이 없으면 `null`** — 0 으로 채우지 않는다 |
| `value_text` | 화면과 같은 표시 문자열(`66.67%`·`1.2조원`). 값이 없으면 `—` |
| `signal` | `key`(`green`·`yellow`·`red`·`neutral`·`not_applicable`·`unknown`) · `emoji`(🟢🟡🔴⚪⊘⚫) · `label` |
| `formula` | 계산식 |
| `thresholds` | `good` · `warn` · `higher_is_better`. 절대 기준이 없거나(⚪) 업종에 맞지 않으면(⊘) `null` |
| `threshold_text` | 경계값을 한 줄로 (화면과 같은 문장) |
| `note` | ⊘ 면 대신 볼 것, ⚫ 면 무엇이 없었는지, 그 밖의 판정 메모 |
| `peer` | 대조군 분포 `scope`(`industry` 동종업계 / `market` 전체 상장사) · `sample_size` · `median` · `median_text`(표시 문자열) · `percentile`(0~100, 높을수록 그 지표 기준으로 나은 쪽) · `summary`. 표본이 5개사 미만이면 `null` |
| `delta` | 작년 대비 `previous` · `current` · `improved`(같으면 `null`) · `summary`. 작년 값이 없거나 ⊘ 면 `null` |

판정 규칙·경계값의 근거는 `src/fin_checkup/metrics/engine.py`, 생성 코드는 `src/fin_checkup/site/pages/api.py`.
쓰는 예: [`examples/`](../examples/README.md) — 두 회사를 나란히 출력하는 스크립트와 단계별 설명.

## `c/<종목코드>/index.md` (별칭 `c/<종목코드>.md`)

같은 측정값을 에이전트가 읽기 쉬운 마크다운 한 장으로(회사당 5~8 KB): 한눈에 보기(`summary_ko`) · 지표 표(신호 ·
값 · 작년 · 비교군 중앙값 · 계산식 · 경계값) · 메모(⊘ 대신 볼 것, ⚫ 무엇이 없었는지) · 위험 신호 · 최근 위험 공시 ·
출처·면책. 경계값 칸은 그 지표에 쓰인 경계를 기호로 줄인 것(`🟢 ≥7% · 🟡 ≥0% · 🔴 <0%`). 회사 HTML 은
`<link rel="alternate" type="text/markdown" href="index.md">` 로 이 파일을 알린다. 에이전트용 사이트 안내는 `/llms.txt`.

## `data/disclosures.json` (빌드에 공시 파일을 넘겼을 때만)

`cli disclosures fetch` 가 모은 최근 상장사 위험 공시(제목 기준 분류) — 형식은 `src/fin_checkup/disclosures.py`
(`format_version` · `bgn_de`·`end_de` · `truncated_types` · `items[]`: `stock_code` · `corp_name` · `report_nm` ·
`rcept_dt` · `rcept_no` · `category`·`category_label` · `severity` · `is_correction` · `url`). 빌드가 검증을 통과한 항목만
싣고 `url` 은 접수번호로 다시 만든 DART 원문 주소다. 사실 목록이며 해석은 없다 — 회사 페이지와 첫 화면이 같은 파일로
'최근 위험 공시' 를 그린다 (`src/fin_checkup/site/disclosures.py`).
