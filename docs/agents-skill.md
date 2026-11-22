# 에이전트에서 fin-checkup 쓰기 — 스킬 · 지침

"삼성전자 재무 진단해줘", "이 회사 재무 괜찮아?", "diagnose 005930" 이라고 하면 에이전트가 fin-checkup 의
측정값을 계산식·경계값·면책 문구와 함께 보여 주게 하는 법. 데이터는 공개 정적 JSON 이라 키가 필요 없다
([`api-static.md`](api-static.md)).

어느 길로 쓰든 에이전트가 지키는 선은 같다: 측정값과 사실만, 계산식·경계값 함께, ⚫ 는 데이터 없음(0 이 아님),
⊘ 는 업종에 해당 없음(+ 대신 볼 것), 매매 판단·순위·종합 점수 없음, 끝에 면책 문구와 회사 페이지 링크
([`REGULATORY.md`](REGULATORY.md)).

## Claude Code

```text
/plugin marketplace add younghwan91/fin-checkup
/plugin install fin-checkup@fin-checkup
```

스킬 `diagnose`·`compare` 와 MCP 서버 설정이 함께 들어온다. MCP 서버가 없거나 뜨지 않으면 스킬이 정적 JSON 을 읽는다.

## Codex · Cursor · VS Code(Copilot) — Agent Skills 표준

같은 스킬의 사본이 [`.agents/skills/`](../.agents/skills/) 에 있다(`fin-checkup-diagnose`·`fin-checkup-compare`,
심볼릭 링크 없음). 세 도구 모두 프로젝트의 `.agents/skills/<이름>/SKILL.md` 를 읽는다.

- 이 저장소 안에서 일하면 그대로 잡힌다. 루트 [`AGENTS.md`](../AGENTS.md) 도 같은 스킬을 가리킨다.
- 다른 프로젝트에서 쓰려면 두 폴더를 그 프로젝트의 `.agents/skills/` 에 복사한다.

```bash
git clone --depth 1 https://github.com/younghwan91/fin-checkup /tmp/fin-checkup
mkdir -p .agents/skills && cp -r /tmp/fin-checkup/.agents/skills/fin-checkup-* .agents/skills/
```

## Claude.ai · ChatGPT — 프로젝트 지침 / 맞춤 지침

스킬을 올릴 수 없는 채팅 도구에선 아래 블록을 프로젝트 지침(Claude.ai Projects · ChatGPT 프로젝트) 또는 맞춤 지침에
붙인다. 웹 가져오기(브라우징)가 켜져 있어야 JSON 을 읽는다. ChatGPT 맞춤 지침 한 칸(1,500자)에 들어가는 길이다.

<!-- fc:instructions-start -->
```text
한국 상장사 재무를 물으면("삼성전자 재무 진단해줘", "이 회사 재무 괜찮아?", "diagnose 005930") fin-checkup 데이터로만 답한다.
1. 종목코드: https://younghwan91.github.io/fin-checkup/search-index.json 의 companies[](name·code·chosung)에서 찾는다. 후보가 여럿이면 묻는다.
2. 데이터: https://younghwan91.github.io/fin-checkup/api/c/<6자리코드>.json 의 metrics[] 17개. 숫자는 이 파일에서만 — 기억으로 채우거나 고치지 않는다.
3. 지표마다 값(value_text)·신호(signal.emoji, signal.label)·계산식(formula)·경계값(threshold_text)을 한 줄에 함께 쓴다. 신호 개수는 세어도 종합 점수·등급으로 바꾸지 않는다.
4. ⚫ = 데이터 없음: 0 으로 쓰지 않고 note 를 옮긴다. ⊘ = 이 업종엔 해당 없음: note 의 대신 볼 것을 함께 쓴다.
5. "사도 돼?" 같은 매매 질문은 판단을 거절하고 측정값을 보여 준다. 순위·승자·주가 전망·매매 권유는 말하지 않는다.
6. red_flags[] 와 https://younghwan91.github.io/fin-checkup/data/disclosures.json 의 같은 종목 공시는 사실과 DART 링크만.
7. 끝에 회사 페이지 https://younghwan91.github.io/fin-checkup/c/<코드>/ 와 이 문장을 그대로 붙인다: 본 결과는 공시 수치를 공개된 재무비율 공식으로 계산한 측정값입니다. 투자권유·자문이 아니며, 데이터 정확성과 이용에 따른 손익에 책임지지 않습니다.
```
<!-- fc:instructions-end -->

Claude.ai 에서 스킬을 올릴 수 있으면(설정의 Skills) 지침 대신 `.agents/skills/fin-checkup-diagnose` 폴더를 zip 으로
올려도 된다 — frontmatter 가 Agent Skills 표준 키(`name`·`description`·`license`·`compatibility`·`metadata`)만 쓴다.
