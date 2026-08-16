"""DuckDB 캐시.

재무데이터는 분기에 한 번 갱신된다. DART 일별 호출 허용량(약관 제10조) 안에서
움직이려면 캐시가 선택이 아니라 전제다.

원본 계정 줄을 그대로 저장한다 — 정규화 규칙을 고치더라도 재수집 없이 다시 계산하려고.
"""

from fin_checkup.storage.db import Cache, CacheLocked, today_key

__all__ = ["Cache", "CacheLocked", "today_key"]
