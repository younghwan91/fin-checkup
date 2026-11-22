# 기여 안내

fin-checkup 은 DART·SEC 공시 숫자를 공개된 재무비율 공식으로 계산해 신호등(🟢🟡🔴⚪⊘⚫)으로 보여 주는 도구입니다.
코드·해설 문장·데이터 오류 제보 모두 환영합니다. 처음이라면 [`good first issue`](https://github.com/younghwan91/fin-checkup/labels/good%20first%20issue)
라벨부터 보세요.

*English summary at the bottom.*

## 준비

```bash
uv sync --all-extras                        # 웹·API·테스트 의존성까지 (CI 는 --frozen)
uv run pytest -q                            # 전체 테스트
uv run ruff check src tests scripts         # CI 가 막는 린트
```

**테스트는 `.env` 도 DART 인증키도 없이 돕니다.** `tests/conftest.py` 의 `Settings` 픽스처가 가짜 키를 넣고,
정적 사이트 테스트는 `tests/hub_seed.py` 가 회사 몇 곳을 심은 임시 캐시로 빌드합니다. 네트워크를 부르는 테스트는 없습니다.

실제 데이터로 돌려 보려면 [OpenDART](https://opendart.fss.or.kr/) 인증키를 받아 레포 루트의 `.env` 에
`DART_API_KEY=...` 를 넣으세요. 키는 커밋하지 않습니다(`.gitignore` 에 있음). 자기 키를 포크·배포물에 섞으면
DART 이용약관 제19조 위반입니다.

## 넘지 않는 선

자세한 이유는 [`docs/REGULATORY.md`](docs/REGULATORY.md). PR 템플릿의 체크리스트가 같은 내용입니다.

- **추천·투자 판단을 쓰지 않습니다.** "사세요/파세요", 매수·매도, 유망·주목할·저평가, 순위표·"상위 N" 목록 모두 안 됩니다.
  신호등은 "이 수치가 이 구간에 있다"는 측정 결과입니다. 목록은 이름순·업종순으로만 냅니다.
  해설 문장은 `src/fin_checkup/explain/banned.py` 의 금지 표현 목록으로 테스트가 걸러 냅니다.
- **계산식과 경계값을 늘 함께 보여 줍니다.** 근거를 감춘 종합 점수·등급은 넣지 않습니다.
  화면·알림·API·이미지에는 면책 문구(`fin_checkup.disclaimer.DISCLAIMER`)와 출처(DART/SEC)가 붙습니다.
- **없는 값은 ⚫, 0 이 아닙니다.** 계정을 못 찾았으면 비워 두고 무엇이 없었는지 적습니다. 뜻이 다른 계정을 끌어와 채우지 않습니다.
- **⊘ 는 "계산은 되지만 그 업종에서 뜻이 없을 때"만** 씁니다(예: 은행의 부채비율 — 예금이 부채라서). 값이 나쁘게 치우쳤다는 건
  ⊘ 사유가 아닙니다. ⊘ 에는 늘 "대신 볼 것"을 적습니다. 기준은 [`docs/OPERATIONS.md`](docs/OPERATIONS.md) §4.
- **경계값 숫자는 바꾸지 않습니다.** `src/fin_checkup/metrics/engine.py` 의 `Band` 는 실제 상장사 분포의 분위수로 맞춘 값이고
  근거가 주석에 있습니다. "교과서 기준은 200% 인데요" 같은 이유로는 바꾸지 않습니다. 경계값이 이상해 보이면 이슈로 근거를 보여 주세요.
- 정적 사이트는 외부 스크립트·추적기·쿠키 없이 갑니다. 빌드 산출물 가드(`uv run python -m fin_checkup.cli site check site-out`)가 확인합니다.

## 무엇을 어디에 더하나

해설은 UI 와 무관한 `src/fin_checkup/explain/` 패키지에 있고, 사이트·Streamlit·CLI 가 같은 데이터를 씁니다.
문장은 "~예요/~해요" 체, 중학생이 읽을 수 있는 말로. 좋다/나쁘다는 평가 대신 사실을 씁니다.

### 지표 해설 고치기·더하기

- 파일: `src/fin_checkup/explain/metrics.py` 의 `MetricExplainer` (쉬운 이름·한 줄 정의·왜 보나·경계값 근거·한계).
- 공식·단위·경계값·⊘ 업종은 **엔진에서 가져옵니다** — 해설에 숫자를 손으로 다시 쓰지 마세요. 엔진 경계값이 바뀌면 문장도 따라가야
  화면의 색과 설명이 같은 말을 합니다.
- 새 지표는 먼저 `metrics/engine.py` 의 `METRIC_DEFS` 에 들어가야 하고(계산식·경계값 근거와 함께, 이 부분은 이슈로 먼저 논의),
  `tests/test_explain_metrics.py` 가 엔진 지표마다 해설이 정확히 하나 있는지 확인합니다.

### 용어집에 용어 더하기

- 파일: `src/fin_checkup/explain/glossary.py` 의 `GLOSSARY` 에 `GlossaryTerm(slug, term, definition, ...)` 한 줄.
- `slug` 는 영문 소문자·하이픈(사이트 앵커). 관련 지표·위험 신호·공시 유형·함께 볼 용어를 key 로 이으면
  `tests/test_explain_glossary.py` 가 그 대상이 실제로 있는지 봅니다.
- 정의는 한두 문장. 회계 기준의 세부 예외까지 담기보다 틀리지 않게 덜 말합니다.
- 지금 60개로 꽉 차 있고 테스트가 60개 상한을 겁니다(`test_size_is_within_40_to_60`). 새 용어는 이슈에서 어떤 용어와
  바꿀지(또는 상한을 올릴지) 먼저 정합니다.

### 업종 설명 고치기

- 업종 이름·한 줄 설명: `src/fin_checkup/explain/sectors.py` 의 `KSIC_DIVISIONS` (KSIC 앞 두 자리 = 업종 비교 단위).
- 업종 때문에 판정하지 않는 지표(⊘)와 그 이유·대신 볼 것: `explain/metrics.py` 의 `SectorCaveat` 인데, 어느 업종에서 ⊘ 인지는
  `metrics/engine.py` 의 `NOT_APPLICABLE_BY_SECTOR` 가 정합니다. ⊘ 를 늘리자는 제안은 코드보다 이슈가 먼저입니다 —
  `scripts/check_sectors.py` 결과처럼 "계산은 되지만 뜻이 없다"는 근거를 붙여 주세요.

### MCP 도구 고치기

- 파일: `src/fin_checkup/mcp/core.py`(도구가 돌려주는 값) · `server.py`(도구 설명·프롬프트·리소스). 쓰는 법은 [`docs/agents.md`](docs/agents.md).
- 판정·문장은 새로 만들지 않고 사이트 JSON 과 `explain` 을 그대로 씁니다. `tests/test_mcp.py` 가 도구 설명·결과 전체의 금지 표현을 봅니다.

### 데이터가 이상할 때

어떤 회사의 숫자가 DART 원문과 다르면 **"데이터가 이상해요"** 이슈 양식으로 종목코드·지표·사업연도·DART 원문 링크를 적어 주세요.
원인 대부분은 계정 이름이 회사마다 달라 정규화가 못 찾는 경우이고, 고치는 곳은 `src/fin_checkup/dart/normalize.py` 입니다.

## 커밋·PR

- **한 PR 에 한 가지.** 버그 수정에 리팩터링·재포맷을 섞지 않습니다. `ruff format` 은 강제하지 않으니 **기존 파일을 통째로
  재포맷하지 마세요** — 새 파일만 포맷합니다.
- 고친 것에는 테스트를 붙입니다. 실패하는 테스트를 먼저 쓰고 고치면 리뷰가 빠릅니다.
- 커밋 메시지는 [Conventional Commits](https://www.conventionalcommits.org/ko/) 형식, 한국어 권장:
  `fix(format): 1조 직전 금액이 '10,000억원' 으로 나오던 것`. 본문에는 **무엇이 문제였나(실패 시나리오)** 를 적습니다.
- CI 가 `pytest`·`ruff check` 를 3.11·3.13 에서 돌립니다. 둘 다 녹색이어야 병합됩니다.

## 행동 규범

서로의 시간을 아끼는 말로 대화합니다. 특정 종목을 띄우거나 깎아내리는 글, 매매 권유는 이슈·PR 에서도 지웁니다.

---

## English

fin-checkup computes public financial ratios from Korean DART (and US SEC EDGAR) filings and shows them as traffic-light signals.
Contributions of code, explanatory text and data-error reports are welcome.

- **Setup:** `uv sync --all-extras`, then `uv run pytest -q` and `uv run ruff check src tests scripts`. Tests need no `.env` and no
  network — a settings fixture injects a fake key and site tests build from a seeded temporary cache.
- **Red lines** (`docs/REGULATORY.md`): no stock recommendations or buy/sell wording, no rankings or "top N" lists (Korean law treats
  that as unregistered investment advice); always show the formula and thresholds; missing data is ⚫, never 0; ⊘ (not applicable)
  only when a ratio is computable but meaningless for a sector, always with what to look at instead; **do not change the threshold
  numbers** in `metrics/engine.py` — they are calibrated to real market quantiles.
- **Where things live:** metric explainers `src/fin_checkup/explain/metrics.py`, glossary `explain/glossary.py`, sector names
  `explain/sectors.py`, account normalization `dart/normalize.py`.
- **Commits:** one change per PR, tests included, Conventional Commits (`fix(format): ...`), don't reformat existing files.
