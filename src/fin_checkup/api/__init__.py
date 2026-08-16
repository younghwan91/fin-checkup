"""HTTP API (Phase 3 — SaaS 전환).

여기가 DuckDB 단일 writer 제약을 아키텍처로 푸는 자리다. 서버 프로세스 하나가
캐시를 소유하고, 알림 워커는 그 안의 백그라운드 작업으로 돈다. 워커를 별도
프로세스로 띄우면 반드시 락에서 부딪힌다.

Postgres로 옮기면 이 제약은 사라지지만, 그때도 이 구조는 그대로 쓸 수 있다.
"""

from fin_checkup.api.app import create_app

__all__ = ["create_app"]
