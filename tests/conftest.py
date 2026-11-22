from __future__ import annotations

import os

# 사용자 설정 파일(`~/.config/fin-checkup/.env`)에 개발자 본인 키가 있어도 테스트에 섞이지 않게 —
# `fin_checkup.config` 를 처음 불러오기 전에 정한다(전역 `settings` 가 import 때 만들어진다).
os.environ["FIN_CHECKUP_CONFIG_DIR"] = os.path.join(os.path.dirname(__file__), ".no-user-config")

import pytest  # noqa: E402

from fin_checkup.config import Settings  # noqa: E402


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        dart_api_key="test-key-0123456789",
        dart_min_delay=0.0,
        fin_checkup_db_path=tmp_path / "test.duckdb",
    )
