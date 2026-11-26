"""IndexNow 열쇠 파일 `/<열쇠>.txt` — 검색 엔진이 알림을 보낸 쪽이 이 사이트 주인인지 확인하는 곳.

열쇠는 공개가 전제다(`site/indexnow.py`). 파일에는 열쇠만, 줄바꿈 없이 담는다.
"""

from __future__ import annotations

from collections.abc import Iterator

from fin_checkup.site.context import Site
from fin_checkup.site.indexnow import KEY, KEY_FILE
from fin_checkup.site.registry import Output, page


@page("indexnow")
def indexnow_key(site: Site) -> Iterator[Output]:
    yield Output(KEY_FILE, KEY)
