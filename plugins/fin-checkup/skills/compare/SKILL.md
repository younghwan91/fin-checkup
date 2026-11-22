---
name: compare
description: "한국 상장사 2~4곳의 재무 지표를 나란히 비교 — fin-checkup 이 DART 공시로 계산한 값·신호등(🟢🟡🔴⚪⊘⚫)·계산식·경계값을 같은 표에 놓는다. 이럴 때 쓴다: \"삼성전자랑 SK하이닉스 재무 비교해줘\", \"두 회사 부채비율 비교\", \"A와 B 중 재무가 어디가 나아?\"(순위·승자 없이 나란히 보여 준다), \"같은 업종 회사끼리 비교\", fin-checkup 비교. English: compare the financial health of Korean companies, compare 005930 and 000660. 측정값만 다룬다 — 투자권유·자문이 아니다."
license: MIT
compatibility: "인터넷 접속(웹 가져오기 또는 curl) 필요. fin-checkup MCP 서버가 있으면 그것을 먼저 쓴다."
metadata:
  site: "https://younghwan91.github.io/fin-checkup/"
  source: "https://github.com/younghwan91/fin-checkup"
---

# fin-checkup 회사 비교

여러 회사의 재무 측정값을 **같은 지표끼리 나란히** 놓는다. 어느 회사가 낫다고 결론 내리지 않는다 —
순위·승자·종합 점수는 판단이고, 판단을 주면 유사투자자문업이 된다. 한 회사만 물으면 `diagnose` 스킬을 쓴다.

## 1. 회사 고르기 (2~4곳)

- 회사마다 `diagnose` 스킬 1번 순서로 종목코드를 찾는다: MCP `search_companies(query)`, 없으면
  `curl -s https://younghwan91.github.io/fin-checkup/search-index.json`.
- 후보가 여럿이면 묻는다. 5곳 이상이면 4곳까지만 고르게 한다(사이트 비교 화면과 같은 한도).
- 열 순서는 **사용자가 말한 순서**. 값이 좋은 순으로 다시 늘어놓지 않는다.

## 2. 데이터

- **MCP**: `compare_companies([stock_code, …])`.
- **없으면**: 회사마다 `curl -s https://younghwan91.github.io/fin-checkup/api/c/<종목코드>.json`
  (형식: 저장소 `docs/api-static.md`). 지표는 `metrics[]` 의 `key` 로 맞춘다 — 17개, 모든 회사가 같은 순서다.
- `fiscal_year`·`report.fs_div`(연결/개별)·`company.sector_label` 이 회사마다 다르면 표 위에 그 차이를 먼저 밝힌다
  (예: "B 는 개별 재무제표 기준"). 업종이 다르면 같은 지표라도 뜻이 다를 수 있다고 쓴다.

## 3. 보여 주는 틀

```markdown
## 재무 신호등 나란히 — {A 이름}({코드}) · {B 이름}({코드})

{fiscal_year} 사업연도 · 출처 DART(OpenDART) · 데이터 {built_on} 빌드

| 지표 | 계산식 | 경계값 | {A 이름} | {B 이름} |
|---|---|---|---|---|
| {label} | {formula} | {threshold_text} | {value_text} {signal.emoji} | {value_text} {signal.emoji} |
… (17개, metrics[] 순서)

**⊘ 해당 없음:** {회사 · 지표 — note(대신 볼 것)}
**⚫ 데이터 없음:** {회사 · 지표 — note}

같은 화면을 사이트에서: https://younghwan91.github.io/fin-checkup/compare/?c={A코드},{B코드}
회사 페이지: https://younghwan91.github.io/fin-checkup/c/{코드}/ (회사마다)

> {disclaimer — 아래 문장 그대로}
```

- 경계값은 지표마다 하나다(업종 무관, `threshold_text`). ⊘ 인 회사 칸에는 값 대신 "⊘ 해당 없음" 을 쓴다.
- ⚫ 칸은 비워 두거나 0 으로 쓰지 않고 "⚫ 데이터 없음" 이라고 쓴다.
- 답 끝 면책 문구(글자 그대로):

  > 본 결과는 공시 수치를 공개된 재무비율 공식으로 계산한 측정값입니다. 투자권유·자문이 아니며, 데이터 정확성과 이용에 따른 손익에 책임지지 않습니다.

## 4. 순위를 매기지 않는다

- "어디가 더 나아?" → "어느 쪽이 낫다고는 말씀드리지 않습니다 — 지표마다 나란히 놓아 드릴게요." 그다음 표.
- 지표 하나를 두고 "A 의 부채비율이 B 보다 낮다" 처럼 **값의 크고 작음을 사실로** 말하는 것은 된다.
  그것을 합쳐 "그래서 A 가 낫다", 🟢 개수로 승자를 정하는 것, 회사를 값 순으로 줄 세우는 것은 하지 않는다.
- 매매 질문("둘 중 뭘 사?")은 `diagnose` 스킬 7번처럼 판단을 거절하고 측정값을 보여 준다.
- 쓰지 않는 표현은 `diagnose` 스킬 8번 목록과 같다.
