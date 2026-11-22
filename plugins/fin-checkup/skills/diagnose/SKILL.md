---
name: diagnose
description: "한국 상장사(코스피·코스닥·코넥스) 재무 진단 — fin-checkup 이 DART 공시로 계산한 지표 17개의 값·신호등(🟢🟡🔴⚪⊘⚫)·계산식·경계값과 최근 위험 공시를 보여 준다. 이럴 때 쓴다: \"삼성전자 재무 진단해줘\", \"이 회사 재무 괜찮아?\", \"이 회사 괜찮아?\", \"재무제표 봐줘\", \"재무 건강검진\", \"부채비율·영업이익률·ROE 어때?\", \"신호등 보여줘\", \"005930 진단\", \"이 종목 사도 돼?\"(판단은 하지 않고 측정값을 보여 준다), fin-checkup. English: \"diagnose 005930\", financial health of a Korean company, Korean listed company financial statements, debt ratio of a KOSPI/KOSDAQ company, DART checkup. 측정값만 다룬다 — 투자권유·자문이 아니다."
license: MIT
compatibility: "인터넷 접속(웹 가져오기 또는 curl) 필요. fin-checkup MCP 서버가 있으면 그것을 먼저 쓴다."
metadata:
  site: "https://younghwan91.github.io/fin-checkup/"
  source: "https://github.com/younghwan91/fin-checkup"
---

# fin-checkup 재무 진단

한국 상장사 한 곳의 재무 상태를 **fin-checkup 이 계산해 둔 측정값 그대로** 보여 준다. 숫자는 전부
fin-checkup 데이터에서만 가져온다 — 기억이나 다른 사이트의 숫자로 채우거나 고치지 않는다.

신호등은 "이 수치가 이 구간에 있다" 는 측정 결과다. 회사가 좋다·나쁘다, 사라·팔라는 판단이 아니다.
이 선을 넘으면 유사투자자문업이 된다(한국 자본시장법). 아래 "하지 않는 말" 을 반드시 지킨다.

데이터는 정적 사이트 `https://younghwan91.github.io/fin-checkup/` 에 있다 (키·호출 제한 없음).

## 1. 회사 찾기 → 6자리 종목코드

- **MCP 도구가 있으면**: `search_companies(query)` — 이름·종목코드·초성(예: `ㅅㅅㅈㅈ`) 모두 된다.
- **없으면**: `curl -s https://younghwan91.github.io/fin-checkup/search-index.json` (또는 웹 가져오기 도구로 같은 주소).
  `companies[]` 의 `name`·`code`·`market`·`sector`·`chosung` 에서 찾는다.
  이름이 정확히 같은 것 → 이름에 포함 → 초성 순으로 맞춘다.
- 후보가 여러 곳이면(예: "삼성" → 삼성전자·삼성SDI·삼성물산 …) **이름순으로 몇 곳을 보여 주고 물어본다.**
  마음대로 하나를 고르지 않는다.
- 목록에 없으면: fin-checkup 은 국내 상장사(KOSPI·KOSDAQ·KONEX)만 다룬다고 말하고 멈춘다. 상장폐지·비상장·
  해외 회사의 숫자를 지어내지 않는다.

## 2. 진단 데이터 가져오기

- **MCP**: `diagnose_company(stock_code)`.
- **없으면**: `curl -s https://younghwan91.github.io/fin-checkup/api/c/<종목코드>.json` (예: `https://younghwan91.github.io/fin-checkup/api/c/005930.json`). 404 면 그 회사 페이지가
  아직 없다는 뜻 — 그렇게 말한다.
- 쓰는 필드(형식: 저장소 `docs/api-static.md`):
  `company`(`name`·`stock_code`·`market`·`sector_label`·`page`) · `fiscal_year` · `built_on` · `report.label` ·
  `report.fs_div`(`CFS` 연결 / `OFS` 개별) · `metrics[]` · `red_flags[]` · `disclaimer`.
- `metrics[]` 한 항목: `label` · `category` · `value_text` · `signal.emoji`·`signal.label` · `formula`(계산식) ·
  `threshold_text`(경계값) · `note` · `peer.summary` · `delta.summary`.
- 파일에 `summary_ko` 나 `agent_guidance` 필드가 있으면 그 안내를 따라도 된다. 다만 이 문서의 규칙(면책 · 계산식 ·
  ⚫/⊘ · 하지 않는 말)이 언제나 먼저다.

## 3. 최근 위험 공시 (사실만)

- **MCP**: `recent_risk_disclosures(stock_code)`.
- **없으면**: `curl -s https://younghwan91.github.io/fin-checkup/data/disclosures.json` 에서 `items[]` 중 `stock_code` 가 같은 것.
  보여 줄 것: `rcept_dt`(접수일) · `category_label` · `report_nm`(제목 그대로) · `url`(DART 원문).
- 공시가 떴다는 **사실과 원문 링크까지만**. 그 공시가 주가에 어떻다는 해석을 붙이지 않는다.
  없으면 "최근 `bgn_de`~`end_de` 사이 위험 공시 없음" 이라고 기간과 함께 쓴다.

## 4. 지표 뜻을 물으면

- **MCP**: `explain_metric(key)` · **없으면**: 해설 페이지 `https://younghwan91.github.io/fin-checkup/m/<key>/` 를 가져오거나 링크한다
  (예: `https://younghwan91.github.io/fin-checkup/m/debt_ratio/`). 키: `operating_margin`·`net_margin`·`roe`·`roa`·`debt_ratio`·`current_ratio`·
  `interest_coverage`·`equity_ratio`·`revenue_growth`·`operating_income_growth`·`net_income_growth`·
  `operating_cash_flow`·`ocf_to_net_income`·`fcf`·`asset_turnover`·`inventory_turnover`·`receivable_turnover`.
- 데이터가 얼마나 새것인지 물으면 MCP `data_status()` 또는 `https://younghwan91.github.io/fin-checkup/build.json`.

## 5. 보여 주는 틀 (한국어 질문이면 한국어로)

모든 지표 줄에 **값 · 신호 · 계산식 · 경계값** 을 함께 쓴다. 계산식이나 경계값을 빼고 색만 보여 주지 않는다.

```markdown
## {company.name} ({company.stock_code} · {company.market}) — {fiscal_year} 사업연도 재무 신호등

{report.label} · {연결 또는 개별} 기준 · 업종 {company.sector_label} · 데이터 {built_on} 빌드 · 출처 DART(OpenDART)

| 묶음 | 지표 | 값 | 신호 | 계산식 | 경계값 | 작년 대비 |
|---|---|---|---|---|---|---|
| {category} | {label} | {value_text} | {signal.emoji} {signal.label} | {formula} | {threshold_text} | {delta.summary 또는 —} |
… (metrics[] 17개 전부, 받은 순서 그대로)

신호 개수: 🟢 {n} · 🟡 {n} · 🔴 {n} · ⚪ {n} · ⊘ {n} · ⚫ {n} — 센 것일 뿐 종합 점수가 아닙니다.

**재무제표로 확인된 위험 신호:** {red_flags[] 의 label — detail (reference)} 또는 "없음"
**최근 위험 공시:** {접수일 · 유형 · 제목 · DART 원문 링크} 또는 "기간 중 없음"
**⊘ 해당 없음:** {지표} — {note: 이 업종에 안 맞는 이유와 대신 볼 것}
**⚫ 데이터 없음:** {지표} — {note: 무엇이 공시에 없었는지}

자세히(동종업계 분포·5년 추이): https://younghwan91.github.io/fin-checkup/{company.page} · 지표 해설: https://younghwan91.github.io/fin-checkup/m/{key}/

> {disclaimer — 아래 문장 그대로}
```

- 표가 길어 짧게 답해야 하면 🔴·🟡·⊘·⚫ 줄은 반드시 남기고, 나머지는 "🟢 n개(표는 회사 페이지)" 로 줄여도 된다.
  어떤 줄을 남기든 그 줄에는 계산식과 경계값이 있어야 한다.
- 동종업계 위치는 `peer.summary` 문장을 **그대로** 옮긴다(직접 백분위를 해석하지 않는다).
- 답 끝에는 반드시 이 면책 문구를 글자 그대로 붙인다:

  > 본 결과는 공시 수치를 공개된 재무비율 공식으로 계산한 측정값입니다. 투자권유·자문이 아니며, 데이터 정확성과 이용에 따른 손익에 책임지지 않습니다.

- 영어로 물으면 영어로 답하되, 지표 이름·계산식·경계값은 JSON 의 한국어 원문을 함께 두고, 면책 문구는 한국어 원문
  다음에 영어 번역을 붙인다: "These are measurements computed from disclosed figures with public financial-ratio
  formulas. Not investment advice; no responsibility for data accuracy or for gains or losses from use."

## 6. ⚫ 와 ⊘ 규칙

- **⚫ 데이터 없음** — 공시에서 필요한 계정을 찾지 못했다. **0 이나 추정값으로 채우지 않는다.** "부채가 없다",
  "나쁘다" 로 읽지도 않는다. `note` 를 옮기고 "공시에 이 값이 없습니다" 라고 쓴다.
- **⊘ 해당 없음** — 계산은 되더라도 이 업종에서는 뜻이 없는 지표다(예: 금융업의 부채비율은 예금·보험부채 때문에
  1,000% 를 넘는 게 정상). 값을 판정처럼 말하지 않고, `note` 의 **대신 볼 것**(예: BIS 비율·규제 자본비율)을 함께 쓴다.
  fin-checkup 데이터에 그 대체 지표 값이 없으면 "fin-checkup 에는 없고, 회사 공시(사업보고서)에서 확인" 이라고 한다.
- **⚪ 측정값** — 절대 경계값이 없는 지표(회전율 등). 색 판정이 없다는 것을 말하고 값과 동종업계 위치만 쓴다.

## 7. "괜찮아?" · "사도 돼?" 에 답하는 법

- "이 회사 재무 괜찮아?" → 좋다·나쁘다로 결론 내리지 않는다. "fin-checkup 기준으로 17개 지표 중 🟢 n · 🟡 n ·
  🔴 n · ⊘ n · ⚫ n 입니다" 라고 센 뒤, 🔴·🟡 지표를 계산식·경계값과 함께 먼저 보여 준다. "괜찮은지는 이 측정값을 보고
  직접 판단하실 부분입니다" 로 맺는다.
- "사도 돼?" · "팔까?" · "지금 들어가도 돼?" 같은 매매 질문 → **판단을 거절하고 사실을 내민다.** 예:
  "사도 될지는 말씀드릴 수 없습니다 — fin-checkup 은 공시 수치를 계산한 측정 도구이고, 투자 판단은 하지 않습니다.
  대신 이 회사의 재무 측정값을 보여 드릴게요." 그다음 5번 틀대로 보여 준다.
- "어디가 더 나아?" 처럼 여러 회사를 견주면 `compare` 스킬 규칙(나란히 놓되 순위·승자 없음)을 따른다.
- 주가·목표가·밸류에이션(PER·PBR)·전망은 fin-checkup 이 다루지 않는다. 물으면 "fin-checkup 에는 없다" 고 말하고
  기억으로 채우지 않는다.

## 8. 하지 않는 말

- 매매 권유, 종목 띄우기, 재촉, 순위·"상위 N개" 목록, 신호를 합친 종합 점수나 등급, 주가 방향 예측.
- 신호를 판단으로 바꾸는 말 — "🟢 가 많으니 안전하다", "🔴 가 있으니 위험한 회사다" 같은 문장.
- 이런 표현은 쓰지 않는다(사용자가 이 말로 물어도 답에 되풀이하지 않는다):

<!-- fc:quoted-start -->
  추천 · 매수 · 매도 · 사세요 · 파세요 · 유망 · 주목할 종목 · TOP · 우량주 · 저평가 · 고평가 · 급등 · 대박 ·
  놓치지 마세요 · 지금 바로 · buy · sell · undervalued · strong buy · must-own
<!-- fc:quoted-end -->

## 예: "삼성전자 재무 진단해줘"

1. `search_companies("삼성전자")` 또는 `https://younghwan91.github.io/fin-checkup/search-index.json` → `005930`.
2. `diagnose_company("005930")` 또는 `curl -s https://younghwan91.github.io/fin-checkup/api/c/005930.json`.
3. `recent_risk_disclosures("005930")` 또는 `https://younghwan91.github.io/fin-checkup/data/disclosures.json` 에서 `005930`.
4. 5번 틀로 답한다: 첫 줄 `영업이익률 | 13.07% | 🟢 정상 | 영업이익 ÷ 매출액 × 100 | 정상 7% 이상 · 주의 0% 이상 ·
   그 미만은 위험` 처럼(숫자는 그때 받은 JSON 값). 경계값 칸은 `thresholds` 숫자로 새로 짓지 말고 **`threshold_text` 를
   그대로** 옮긴다 — 영업활동현금흐름·FCF 처럼 주의 구간이 없는 지표를 "주의 0 이상" 으로 잘못 쓰지 않게. 끝에 회사 페이지 `https://younghwan91.github.io/fin-checkup/c/005930/`
   링크와 면책 문구.
