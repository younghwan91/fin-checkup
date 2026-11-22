from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수 > 작업 폴더 `.env` > 사용자 설정 파일(`onboarding.user_env_file()`) 순으로 읽는다.

    사용자 설정 파일은 `cli setup` 이 인증키를 넣는 곳이다 — 저장소 없이 `uvx` 로 띄운 MCP 서버처럼
    작업 폴더가 정해지지 않은 프로세스도 같은 키를 읽게. 파일 목록은 만들 때마다 다시 계산한다
    (테스트가 위치를 바꿀 수 있게).
    `Settings(_env_file=None, ...)` 처럼 직접 주면 그것을 쓴다.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    def __init__(self, **values: Any) -> None:
        if "_env_file" not in values:
            from fin_checkup.onboarding import env_files

            values["_env_file"] = env_files()
        super().__init__(**values)

    dart_api_key: str = ""
    dart_base_url: str = "https://opendart.fss.or.kr/api"
    # 약관 제10조 일별 호출 허용량 대응 — 호출 간 최소 대기(초)
    dart_min_delay: float = 0.3
    dart_timeout: float = 30.0
    # 약관 제10조의 일별 호출 허용량. 실제 한도는 DART 홈페이지 공지를 따르며,
    # 여기 값은 우리 쪽에서 스스로 지키기 위한 기준선이다.
    dart_daily_quota: int = 20_000

    fin_checkup_db_path: Path = Path("data/fin_checkup.duckdb")
    # corp_code 매핑 갱신 주기(일)
    corp_code_ttl_days: int = 7

    # 위험 공시 알림 (Phase 1.5)
    telegram_bot_token: str = ""

    # SEC EDGAR (Phase 2) — SEC는 요청마다 연락처가 담긴 User-Agent를 요구한다.
    sec_user_agent: str = ""
    sec_base_url: str = "https://data.sec.gov"

    # 공유 카드·카드뉴스 PNG 에 쓸 한국어 글꼴(.ttf/.otf/.ttc). 비우면 시스템에서 찾는다
    # (`site/images/fonts.py` 의 후보 목록 → fc-list). 못 찾으면 PNG 를 만들지 않는다 — 두부(□) 대신.
    fin_checkup_font_regular: str = ""
    fin_checkup_font_bold: str = ""

    @property
    def has_api_key(self) -> bool:
        return bool(self.dart_api_key.strip())

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token.strip())

    @property
    def has_sec_user_agent(self) -> bool:
        return bool(self.sec_user_agent.strip())


settings = Settings()
