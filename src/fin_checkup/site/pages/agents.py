"""`/agents/` — 사람이 읽는 '에이전트에서 쓰기': 붙여 넣는 프롬프트 · curl 예 · 에이전트용 파일 ·
MCP·플러그인 문서.

에이전트 쪽 안내 본문은 `/llms.txt`(pages/llms.py)·`/openapi.json`(pages/openapi.py) 이고, 여기는 그 길을
사람에게 알려 준다. 프롬프트는 복사해 다른 서비스에 붙이는 글이라 절대주소를 쓴다
(`--base-url` 이 상대면 공개 주소). 프롬프트마다 계산식·경계값·면책을 함께 달라고 적는다 —
금지 표현은 테스트와 가드가 본다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fin_checkup.site.context import Site
from fin_checkup.site.pages.llms import LLMS, LLMS_FULL, REPO, example_entries
from fin_checkup.site.pages.openapi import PATH as OPENAPI
from fin_checkup.site.registry import Output, page

#: `--base-url` 이 절대주소가 아닐 때 프롬프트에 쓰는 공개 주소.
PUBLIC_URL = "https://younghwan91.github.io/fin-checkup/"
MCP_DOC = f"{REPO}/blob/main/docs/agents.md"
SKILL_DOC = f"{REPO}/blob/main/docs/agents-skill.md"

_SHOW = (
    "지표마다 값·신호·계산식·경계값을 함께 보여 주고, '데이터 없음' 은 0 이 아니라 공시에 없는 것으로,"
    " '해당 없음' 은 이 업종에 맞지 않는 지표로 적고"
)
_END = "끝에 면책 문구와 회사 페이지 링크를 붙여 줘."


@dataclass(frozen=True)
class Prompt:
    service: str
    how: str
    text: str


def prompts(base: str, first: str, second: str) -> list[Prompt]:
    llms = base + LLMS
    return [
        Prompt(
            "ChatGPT",
            "검색(웹 브라우징)을 켠 채로 붙여 넣으세요.",
            f"{llms} 를 읽고 그 안내대로 {first} 재무를 진단해 줘. {_SHOW}, {_END}",
        ),
        Prompt(
            "Claude",
            "웹 검색·가져오기가 켜진 대화에 붙여 넣으세요. Claude Code 라면 아래 플러그인이 더 편합니다.",
            f"fin-checkup({llms}) 안내를 따라 {first} 재무를 봐 줘. {_SHOW}, {_END}",
        ),
        Prompt(
            "Perplexity",
            "주소를 그대로 넣으면 그 파일을 읽습니다.",
            f"{llms} 와 그 안의 회사 JSON 으로 {first} 재무 측정 결과를 정리해 줘. {_SHOW}, {_END}",
        ),
        Prompt(
            "비교 (어느 서비스든)",
            "두 회사 이상을 나란히 볼 때.",
            f"{llms} 안내대로 {first}와 {second}를 지표별로 나란히 보여 줘. 순위나 우열은 매기지 말고"
            f" {_SHOW}, {_END}",
        ),
    ]


@page("agents")
def agents_page(site: Site) -> Iterator[Output]:
    base = site.base_url if site.has_absolute_base else PUBLIC_URL
    examples = example_entries(site)
    code = examples[0].stock_code if examples else "005930"
    names = [e.name for e in examples] + ["삼성전자", "SK하이닉스"]
    yield site.render(
        "agents/index.html",
        "agents/index.html",
        prompts=prompts(base, names[0], names[1] if len(examples) > 1 else names[-1]),
        public_base=base,
        code=code,
        has_example=bool(examples),
        llms=LLMS,
        llms_full=LLMS_FULL,
        openapi=OPENAPI,
        mcp_doc=MCP_DOC,
        skill_doc=SKILL_DOC,
    )
