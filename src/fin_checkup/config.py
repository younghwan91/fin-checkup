from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

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
