"""MCP 서버 — 어떤 MCP 클라이언트(Claude Code·Claude Desktop·Cursor·Codex …)든 상장사 재무 진단을 부르게.

    uvx --from "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup" fin-checkup-mcp

- `client.py`  데이터 출처. 기본은 공개 정적 사이트(JSON, 키 불필요, 메모리+디스크 캐시·ETag),
               `FIN_CHECKUP_MCP_SOURCE=local` 이면 로컬 DuckDB 캐시(`CheckupService`, DART 호출 0).
- `core.py`    도구가 돌려줄 값 — 회사 찾기·진단·비교·지표 해설·위험 공시·데이터 상태. MCP 를 모른다.
- `server.py`  MCP 배선(도구·프롬프트·리소스)과 `main()`.

측정값만 돌려준다. 판정은 `metrics.engine`, '왜 이 색' 문장은 `explain.why_color` 그대로 — 여기서 새로
판정하지 않고, 없는 값은 None(⚫) 그대로 둔다. 사고팔라는 말은 쓰지 않는다 (`docs/REGULATORY.md`).
"""
