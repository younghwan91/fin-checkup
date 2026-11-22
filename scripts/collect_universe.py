"""전 상장사 재무 증분 수집 — `python -m fin_checkup.cli collect` 의 얇은 껍데기.

수집 로직은 `src/fin_checkup/collect.py` 에 있다 (GitHub Actions `collect.yml` 과 같은 코드).
상장사 중 재무가 없는 (회사, 연도)만, 호출 예산 안에서 받는다 — 멈춘 데서 다음 실행이 이어받으므로
예전처럼 --skip 으로 나눌 필요가 없다.

    uv run python scripts/collect_universe.py --budget 5000
    uv run python scripts/collect_universe.py --budget 12000 --years 2021 2022
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fin_checkup.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["collect", *sys.argv[1:]]))
