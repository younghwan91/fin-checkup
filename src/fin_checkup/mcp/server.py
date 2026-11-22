"""fin-checkup MCP 서버 — 도구 8개 · 프롬프트 `diagnose` · 리소스(신호 범례·넘지 않는 선·지표 목록).

    fin-checkup-mcp                                   # stdio (MCP 클라이언트가 띄운다)
    fin-checkup-mcp --transport streamable-http --port 8000   # http://127.0.0.1:8000/mcp

설치 없이:
    uvx --from "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup" fin-checkup-mcp

환경변수(전부 선택): `FIN_CHECKUP_MCP_SOURCE`(비우면 사이트 + 키가 있으면 없는 연도만 DART · `site` · `dart` ·
`local` 로컬 DuckDB, `FIN_CHECKUP_DB_PATH`) · `DART_API_KEY`(보통은 `fin-checkup setup` 이 넣은 사용자 설정 파일에서) ·
`FIN_CHECKUP_MCP_BASE_URL`(사이트 주소) · `FIN_CHECKUP_MCP_CACHE_DIR`(디스크 캐시, `off` 면 끔) ·
`FIN_CHECKUP_MCP_TTL`(초). 설명은 `docs/agents.md`.

stdio 에서는 stdout 이 곧 통신선이다 — 로그는 전부 stderr 로.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain import explainers
from fin_checkup.mcp import core, guide
from fin_checkup.mcp.client import Source, SourceError, source_from_env

#: 모든 도구 설명 끝에 붙는다 — 측정값이라는 것, 사고팔라는 말을 하지 말라는 것.
NOT_ADVICE = (
    "\n\n측정값이며 투자권유·추천이 아닙니다. 결과를 전할 때 사거나 팔라는 말을 하지 마세요. "
    "Returns measurements, not investment advice — never tell the user to buy, sell or hold."
)

INSTRUCTIONS = (
    "fin-checkup: 한국 상장사(KOSPI·KOSDAQ·KONEX)의 DART 공시 재무제표를 공개된 재무비율 공식으로 계산한 "
    "신호등(🟢정상 🟡주의 🔴위험 ⚪측정값 ⊘해당 없음 ⚫데이터 없음) 측정값을 돌려준다. "
    "회사 진단은 diagnose_company, 이름이 애매하면 search_companies 로 먼저 찾는다. "
    "결과의 presentation_guidance 를 따른다: 값마다 계산식·경계값을 함께, ⚫/⊘ 는 그대로, 값을 지어내지 않고, "
    "종합 점수·순위를 만들지 않으며, 끝에 disclaimer 와 출처 링크. 종목 추천이나 투자 판단은 하지 않습니다.\n"
    "English: Korean listed-company financial check-up from DART filings. Measurements only — never advise "
    "buying or selling, never rank companies, keep missing (⚫) and not-applicable (⊘) as they are, and always "
    "include the disclaimer and source links. See resource fin-checkup://policy."
)

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
_LOCAL_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

logger = logging.getLogger(__name__)


def _version() -> str:
    try:
        return version("fin-checkup")
    except PackageNotFoundError:  # pragma: no cover — 소스 트리에서 바로 돌릴 때
        return "0"


def _metrics_resource() -> str:
    lines = [
        "# fin-checkup 지표 17개 (metrics)",
        "",
        "엔진 정의 순서 그대로. 경계값은 일반 업종 기준이며, 업종에 따라 ⊘ 해당 없음이 될 수 있다.",
        "",
        "| key | 지표 | 묶음 | 계산식 | 경계값(일반 업종) |",
        "|---|---|---|---|---|",
    ]
    for e in explainers():
        lines.append(f"| `{e.key}` | {e.label} | {e.category.value} | {e.formula} | {e.threshold_text()} |")
    lines += ["", "자세히: explain_metric 도구 또는 사이트의 /m/<key>/ 페이지.", "", DISCLAIMER]
    return "\n".join(lines)


def build_server(source: Source) -> MCPServer:
    """도구·프롬프트·리소스를 붙인 서버. 테스트는 가짜 출처를 넣는다."""
    server = MCPServer(
        "fin-checkup",
        title="fin-checkup — 한국 상장사 재무 신호등",
        instructions=INSTRUCTIONS,
        website_url=source.base_url,
        version=_version(),
    )

    async def guarded(coro: Any) -> dict[str, Any]:
        try:
            return await coro
        except (core.ToolInputError, SourceError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        title="회사 찾기 (search companies)",
        description=(
            "한국 상장사를 회사 이름·6자리 종목코드·초성(예: ㅅㅅㅈㅈ → 삼성전자)으로 찾는다. 결과 순서는 '얼마나 맞았나 → 이름순' 이며 "
            "지표 순위가 아니다. 진단 전에 회사가 애매할 때 쓴다.\n"
            "Find Korean listed companies by name, 6-digit stock code, or Hangul initial consonants. Ordered by match "
            "quality then name — not by any metric." + NOT_ADVICE
        ),
        annotations=_READ_ONLY,
    )
    async def search_companies(
        query: Annotated[
            str, Field(description="회사 이름·종목코드·초성 / company name, stock code, or initials")
        ],
        limit: Annotated[int, Field(description="최대 결과 수 (1~50) / max results", ge=1, le=50)] = 10,
    ) -> dict[str, Any]:
        return await guarded(core.search_companies(source, query, limit))

    @server.tool(
        title="재무 진단 (diagnose company)",
        description=(
            "회사 하나의 재무 신호등 진단. 이름이나 종목코드를 받아 회사를 찾고, 지표 17개마다 신호·값·단위·계산식·경계값·"
            "동종업계 중앙값·작년 값·'왜 이 색' 문장, 재무제표 위험 신호, 최근 위험 공시(DART 원문 링크), 출처, 면책 문구, "
            "그리고 보여 주는 법(presentation_guidance)을 돌려준다. 회사가 여럿 맞으면 status=ambiguous 와 후보를 돌려준다. "
            "없는 값은 null·⚫ 그대로다 — 추정해 채우지 말 것.\n"
            "Financial traffic-light check-up of one Korean listed company (DART filings): per-metric signal, value, "
            "formula, thresholds, peer median, last year, a 'why this colour' sentence, red flags, recent risk "
            "disclosures, sources and presentation_guidance. Follow presentation_guidance when answering."
            + NOT_ADVICE
        ),
        annotations=_READ_ONLY,
    )
    async def diagnose_company(
        company: Annotated[
            str, Field(description="회사 이름 또는 6자리 종목코드 / company name or 6-digit code")
        ],
        year: Annotated[
            int | None,
            Field(description="사업연도. 비우면 최신 / fiscal year; omit for the latest available"),
        ] = None,
    ) -> dict[str, Any]:
        return await guarded(core.diagnose_company(source, company, year))

    @server.tool(
        title="지표 해설 (explain metric)",
        description=(
            "재무 지표 하나를 쉬운 말로 설명한다: 정의·왜 보나·계산식·업종별 경계값과 그 근거·한계·⊘ 해당 없음 업종과 대신 볼 것. "
            "키(debt_ratio)나 이름(부채비율)을 받는다.\n"
            "Explain one metric in plain words: definition, formula, thresholds by sector and their basis, "
            "pitfalls, and sectors where it does not apply." + NOT_ADVICE
        ),
        annotations=_READ_ONLY,
    )
    async def explain_metric(
        key_or_label: Annotated[str, Field(description="지표 키 또는 이름 / metric key or Korean label")],
    ) -> dict[str, Any]:
        try:
            return core.explain_metric(source, key_or_label)
        except core.ToolInputError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        title="회사 비교 (compare companies)",
        description=(
            "회사 2~4곳의 지표를 정해진 순서로 나란히 놓는다. 회사 순서는 입력 순서이며 우열을 매기지 않는다 — 어느 회사가 "
            "낫다고 말하지 말 것.\n"
            "Side-by-side table of 2-4 companies in a fixed metric order. Columns follow the input order; there is "
            "no winner and you must not name one." + NOT_ADVICE
        ),
        annotations=_READ_ONLY,
    )
    async def compare_companies(
        companies: Annotated[
            list[str],
            Field(
                description="회사 이름 또는 종목코드 2~4개 / 2-4 names or codes", min_length=2, max_length=4
            ),
        ],
    ) -> dict[str, Any]:
        return await guarded(core.compare_companies(source, companies))

    @server.tool(
        title="최근 위험 공시 (recent risk disclosures)",
        description=(
            "최근 위험 공시(관리종목·감사의견·감자·전환사채·유상증자·최대주주 변경 등, 공시 제목 기준 분류)의 사실 목록과 DART "
            "원문 링크. 회사를 주면 그 회사만, 비우면 시장 전체(같은 사건은 하나로). 해석·전망을 덧붙이지 말 것.\n"
            "Facts-only list of recent risk-type DART disclosures with original links, for one company or "
            "market-wide. Do not add interpretation or forecasts." + NOT_ADVICE
        ),
        annotations=_READ_ONLY,
    )
    async def recent_risk_disclosures(
        company: Annotated[
            str | None,
            Field(description="회사 이름 또는 종목코드. 비우면 시장 전체 / company; omit for market-wide"),
        ] = None,
        days: Annotated[int, Field(description="최근 며칠 (1~수집 기간) / last N days", ge=1, le=31)] = 7,
        limit: Annotated[int, Field(description="최대 건수 / max items", ge=1, le=100)] = 30,
    ) -> dict[str, Any]:
        return await guarded(core.recent_risk_disclosures(source, company, days, limit))

    @server.tool(
        title="데이터 상태 (data status)",
        description=(
            "데이터가 언제 기준인지: 빌드 날짜, 사업연도, 데이터에 든 연도들, 기대한 사업연도가 반영됐는지(fresh), 공시 수집 기간, "
            "회사 수, 출처 모드.\n"
            "Data freshness: build date, fiscal year(s), whether the expected fiscal year is in, disclosure "
            "window, company count, and source mode." + NOT_ADVICE
        ),
        annotations=_READ_ONLY,
    )
    async def data_status() -> dict[str, Any]:
        return await guarded(core.data_status(source))

    @server.tool(
        title="설치 상태 (setup status)",
        description=(
            "fin-checkup 이 어떻게 설치·설정됐나: 출처 모드(site · site+dart · dart · local), 공개 사이트 연결, 개인 DART 인증키가 "
            "설정됐는지(예/아니오와 어디서 오는지만 — 키 값은 절대 돌려주지 않는다), DART 에서 받아 오기가 켜졌는지, 개인 캐시의 사업연도, "
            "다음에 할 일. check_key=true 면 DART 를 한 번 불러 키가 통하는지 본다. 설치를 돕거나 '키 넣었는데 되나?' 를 확인할 때 쓴다. "
            "사용자에게 키를 채팅으로 달라고 하지 말 것.\n"
            "Installation/config status: source mode, site reachability, whether a personal DART key is configured "
            "(yes/no and where from — never the key itself), live DART fetching, cached years, next steps. Never ask "
            "the user to paste the key into chat." + NOT_ADVICE
        ),
        annotations=_READ_ONLY,
    )
    async def setup_status(
        check_key: Annotated[
            bool, Field(description="DART 를 한 번 불러 키가 통하는지 확인 / call DART once to verify the key")
        ] = False,
    ) -> dict[str, Any]:
        return await guarded(core.setup_status(source, check_key))

    @server.tool(
        title="설치 안내 (setup guide)",
        description=(
            "클라이언트별 설치 단계: uv 확인, MCP 서버 연결(Claude Code · Claude Desktop(Windows·macOS 설정 경로) · Cursor · VS Code · "
            "Codex · Gemini CLI), 확인 방법. want_key=true 면 OpenDART 인증키 받는 법과, 키를 사용자가 직접 터미널에서 넣는 명령, "
            "MCP 서버 env 로 넘기는 법까지. 단계마다 ask_first(먼저 물을 것)·user_runs(사용자가 직접 할 것)가 있고 rules_for_agent 를 "
            "따른다 — 키를 채팅으로 받지 않는다.\n"
            "Step-by-step setup for a given MCP client, and (want_key) how the user obtains and stores their own OpenDART "
            "key without pasting it into chat." + NOT_ADVICE
        ),
        annotations=_LOCAL_ONLY,
    )
    async def setup_guide(
        client: Annotated[
            str | None,
            Field(
                description="claude-code · claude-desktop · cursor · vscode · codex · gemini. 비우면 목록 / client name"
            ),
        ] = None,
        want_key: Annotated[
            bool, Field(description="OpenDART 인증키 안내도 / include the personal DART key steps")
        ] = False,
    ) -> dict[str, Any]:
        return guide.setup_guide(client, want_key)

    @server.prompt(
        name="diagnose",
        title="재무 진단 (diagnose)",
        description="회사 하나를 fin-checkup 도구로 진단하고, 측정값만 규칙대로 전한다. / Diagnose one company and "
        "present measurements only.",
    )
    def diagnose(
        company: Annotated[str, Field(description="회사 이름 또는 종목코드 / company name or code")],
    ) -> str:
        return (
            f"{company} 재무 진단\n\n"
            "fin-checkup MCP 도구로 다음 순서대로 해 주세요.\n"
            f'1. diagnose_company(company="{company}") 를 부른다. status 가 ambiguous 면 candidates 를 보여 주고 '
            "사용자에게 고르게 한 뒤 그 stock_code 로 다시 부른다. not_found 면 search_companies 로 표기를 찾는다.\n"
            "2. 결과의 presentation_guidance 를 그대로 따른다: summary 의 사실 → 묶음(category)별 지표마다 신호·값·계산식·"
            "경계값·why 문장 → 위험 신호(red_flags)·최근 위험 공시(사실과 DART 링크만).\n"
            "3. ⚫ 데이터 없음·⊘ 해당 없음은 그대로 전하고(⊘ 는 note 의 대신 볼 것과 함께), 값을 지어내지 않는다.\n"
            "4. 사용자가 지표 뜻을 물으면 explain_metric 을 쓴다. 다른 회사와 견주자고 하면 compare_companies.\n"
            "5. 종합 점수·순위를 만들지 않고, 사고팔라는 말을 하지 않는다. 종목 추천이나 투자 판단은 하지 않습니다.\n"
            "6. 끝에 disclaimer 를 그대로, 그리고 회사 페이지(page_url)와 DART 출처 링크를 붙인다.\n\n"
            "Answer in the user's language. Measurements only; no buy/sell/hold advice."
        )

    @server.resource(
        "fin-checkup://legend",
        name="signal-legend",
        title="신호 범례 (signal legend)",
        description="🟢🟡🔴⚪⊘⚫ 신호의 뜻 / what each signal means",
        mime_type="text/markdown",
    )
    def legend() -> str:
        return core.SIGNAL_LEGEND + "\n" + DISCLAIMER

    @server.resource(
        "fin-checkup://policy",
        name="red-lines",
        title="넘지 않는 선 (red-line policy)",
        description="에이전트가 지켜야 할 선 — 사고팔라는 말 금지, 계산식·경계값 노출, ⚫/⊘ 그대로 / rules for agents",
        mime_type="text/markdown",
    )
    def policy() -> str:
        return core.POLICY

    @server.resource(
        "fin-checkup://metrics",
        name="metrics",
        title="지표 17개 (metric list)",
        description="지표 키·이름·묶음·계산식·경계값 표 / metric keys, formulas and thresholds",
        mime_type="text/markdown",
    )
    def metrics() -> str:
        return _metrics_resource()

    return server


def drop_unset_key(environ: dict[str, str] | Any = os.environ) -> None:
    """호스트가 '비워 둠' 을 빈 문자열이나 치환 안 된 자리표시자로 넘기면 없는 것으로 친다.

    Claude Desktop 확장(.mcpb)은 선택 칸 `dart_api_key` 를 비워 두면 `DART_API_KEY=""` 로(기본값이 없으면
    `${user_config.dart_api_key}` 글자 그대로) 넘긴다. 환경변수는 설정 파일보다 앞서서, 그대로 두면
    `fin-checkup setup` 이 사용자 설정 파일에 넣어 둔 키까지 빈 값으로 덮인다.
    """
    value = environ.get("DART_API_KEY")
    if value is not None and (not value.strip() or value.strip().startswith("${")):
        del environ["DART_API_KEY"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="fin-checkup-mcp",
        description="fin-checkup MCP 서버 — 한국 상장사 재무 신호등 (측정값, 투자권유·추천이 아닙니다.)",
    )
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="streamable-http 때만")
    parser.add_argument("--port", type=int, default=8000, help="streamable-http 때만")
    args = parser.parse_args(argv)

    # stdout 은 stdio 통신선이다 — 로그는 stderr.
    logging.basicConfig(
        level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    drop_unset_key()
    server = build_server(source_from_env())
    if args.transport == "streamable-http":
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        server.run()


if __name__ == "__main__":  # pragma: no cover
    main()
