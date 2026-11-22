"""에이전트(ChatGPT·Claude·MCP 클라이언트 …)에게 주는 고정 안내 — 이 데이터를 사람에게 어떻게 전하나.

정적 JSON `agent_guidance`(`site/pages/api.py`)와 회사 마크다운(`site/pages/company_md.py`)이 같은 문장을 싣는다.
에이전트가 가장 잘 따르는 영어로 쓴다. 넘지 않는 선은 `docs/REGULATORY.md` — 측정값만, 계산식·경계값과 함께,
⚫ 은 0 이 아니고 ⊘ 는 대신 볼 것, 사고팔라는 말·순위 없음, 면책 그대로. 금지 표현 검사는 `explain_texts()` 가 본다.
"""

from __future__ import annotations

AGENT_GUIDANCE = (
    "Present these as measurements, not opinions: for each metric you mention give its value, signal, formula"
    " and threshold. ⚫ = the data was not found in the filing (never 0). ⊘ = the metric does not fit this"
    " sector; pass on its note. ⚪ = no absolute threshold; compare with the peer median only. Do not tell the"
    " user to buy, sell or hold, do not rank companies, do not forecast prices. State the fiscal year, cite"
    " the company page and DART, and include the disclaimer verbatim."
)
