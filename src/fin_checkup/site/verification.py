"""검색 엔진 소유 확인 파일 — Google Search Console HTML 파일 인증 (2026-11-27 등록).

Search Console 은 URL 접두어 속성(`…/fin-checkup/`)의 주인을 그 아래 `google<토큰>.html` 이 정해진
한 줄을 돌려주는지로 확인한다. 지우면 인증이 풀린다. 페이지가 아니라서 sitemap·IndexNow 에서 빠지고,
가드는 페이지 규칙(면책 문구·제목) 대신 내용이 이 한 줄과 똑같은지만 본다.
"""

from __future__ import annotations

GOOGLE_FILE = "google88933ce58dda7616.html"
GOOGLE_BODY = f"google-site-verification: {GOOGLE_FILE}"

#: 출력 경로 → 정확한 내용.
FILES: dict[str, str] = {GOOGLE_FILE: GOOGLE_BODY}
