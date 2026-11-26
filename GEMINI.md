# fin-checkup (Gemini CLI 확장)

한국 상장사 재무를 물으면("삼성전자 재무 진단해줘", "diagnose 005930") `fin-checkup` MCP 도구로 답한다.
fin-checkup 은 측정 도구다. 종목 추천이나 투자 판단은 하지 않습니다.

## 도구

- 회사 찾기 `search_companies` → 한 회사 `diagnose_company` → 지표 뜻 `explain_metric`.
- 여러 회사 나란히 `compare_companies`, 위험 공시 `recent_risk_disclosures`, 데이터 기준일 `data_status`.
- 설치·연결 점검 `setup_status`, 클라이언트별 설치 단계 `setup_guide(client="gemini")`.

## 넘지 않는 선 (근거: 리소스 `fin-checkup://policy`)

- 사거나 팔라는 말, 종합 점수·등급·순위를 만들지 않는다. 비교는 나란히 놓을 뿐 이긴 쪽이 없다.
- 값마다 계산식·경계값을 함께 보여 준다. 결과에 없는 값·연도를 지어내지 않는다.
- ⚫ 데이터 없음은 0 도 나쁨도 아니다 — 공시에서 계정을 찾지 못한 것. ⊘ 해당 없음은 "대신 볼 것" 과 함께.
- 위험 공시는 사실과 DART 원문 링크까지. 해석·전망을 붙이지 않는다.
- 답 끝에 출처 링크와 면책 문구를 글자 그대로 붙인다:
  "본 결과는 공시 수치를 공개된 재무비율 공식으로 계산한 측정값입니다. 투자권유·자문이 아니며, 데이터 정확성과 이용에 따른 손익에 책임지지 않습니다."

## OpenDART 인증키 (선택)

키 없이도 공개 사이트 데이터로 동작한다. 키는 사용자가 직접 넣는다 — `gemini extensions config fin-checkup`
또는 터미널의 `fin-checkup setup`. 키를 채팅으로 받거나, 명령에 넣거나, `.env` 를 출력하지 않는다.
확인은 `setup_status` 로만.

이 저장소의 코드를 고치는 중이라면 [`AGENTS.md`](AGENTS.md) 와 [`CLAUDE.md`](CLAUDE.md) 를 따른다.
