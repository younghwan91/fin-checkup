# fin-checkup — 에이전트 플러그인

"삼성전자 재무 진단해줘", "이 회사 재무 괜찮아?", "diagnose 005930" 이라고 하면 에이전트가
[fin-checkup](https://younghwan91.github.io/fin-checkup/) 의 재무 신호등을 읽어 계산식·경계값과 함께 보여 준다.

## 설치 (Claude Code)

```text
/plugin marketplace add younghwan91/fin-checkup
/plugin install fin-checkup@fin-checkup
```

터미널에서는 `claude plugin marketplace add younghwan91/fin-checkup` · `claude plugin install fin-checkup@fin-checkup`.

## 들어 있는 것

| 무엇 | 하는 일 |
|---|---|
| `skills/diagnose` | 한 회사 — 지표 17개 값·신호·계산식·경계값, 위험 신호, 최근 위험 공시, 회사 페이지 링크 |
| `skills/compare` | 2~4곳을 같은 지표끼리 나란히 (순위·승자 없음) |
| `skills/setup` | "fin-checkup 설치해줘" · "DART 키 받는 거 도와줘" — uv·MCP 연결·시험 진단, 원하면 OpenDART 키 받는 법. 키는 사용자가 직접 `fin-checkup setup` 으로 넣는다(채팅으로 받지 않음) |
| `.mcp.json` | MCP 서버 `uvx --from "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup" fin-checkup-mcp` |

MCP 서버를 쓸 수 없어도(uv 가 없거나 서버가 뜨지 않아도) 스킬은 돈다 — 정적 JSON
(`https://younghwan91.github.io/fin-checkup/api/c/<종목코드>.json`)을 웹 가져오기나 `curl` 로 읽는다.
인증키는 필요 없다.

## 지키는 선

측정값과 사실만 보여 준다. 매매 판단·순위·종합 점수는 내지 않고, 없는 값은 ⚫, 업종에 맞지 않는 지표는 ⊘ 와
대신 볼 것을 함께 쓴다([`docs/REGULATORY.md`](../../docs/REGULATORY.md)). 모든 답에 붙는 문장:

> 본 결과는 공시 수치를 공개된 재무비율 공식으로 계산한 측정값입니다. 투자권유·자문이 아니며, 데이터 정확성과 이용에 따른 손익에 책임지지 않습니다.
