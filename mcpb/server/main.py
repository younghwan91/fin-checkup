"""fin-checkup MCP 서버 — Claude Desktop 확장(.mcpb) 진입점.

번들에는 저장소의 `pyproject.toml`·`uv.lock`·`src/fin_checkup/` 가 함께 들어 있다. 호스트가
`uv run --directory <번들> --frozen --extra mcp server/main.py` 로 띄우면 uv 가 잠근 의존성을 깔고
이 파일을 돌린다 — 서버 본체는 `fin_checkup.mcp.server.main` 그대로다.
"""

from fin_checkup.mcp.server import main

if __name__ == "__main__":
    main()
