from __future__ import annotations

import pytest

from fin_checkup.config import Settings


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        dart_api_key="test-key-0123456789",
        dart_min_delay=0.0,
        fin_checkup_db_path=tmp_path / "test.duckdb",
    )
