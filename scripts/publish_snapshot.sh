#!/usr/bin/env bash
# 캐시에서 공개 스냅샷을 뽑아 data-YYYYMMDD 릴리스로 올린다 → pages.yml 이 받아 배포한다.
# collect.yml(매일 증분 수집)이 이 스크립트로 올리고, 로컬에서 손으로 올릴 때도 같다.
#
# 캐시를 쓰는 프로세스(수집·serve·Streamlit)가 없을 때 돌린다. 허용목록 테이블만 나가고,
# 인증키(DART_API_KEY·TELEGRAM_BOT_TOKEN 설정값)가 섞여 있으면 export 가 멈춘다(snapshot.py) —
# 그래서 키를 설정에 둔 채 돌려야 검사가 된다. 캐시 경로는 FIN_CHECKUP_DB_PATH(없으면 .env·기본값).
#
#   scripts/publish_snapshot.sh            # 오늘(KST) 날짜 태그. 이미 있으면 data-YYYYMMDD-2, -3 …
#   scripts/publish_snapshot.sh 20261122   # 태그 날짜 지정 (이미 있으면 멈춘다)
#
# SNAPSHOT_NOTES 가 있으면 릴리스 설명 끝에 붙인다. $GITHUB_OUTPUT 이 있으면 tag=... 를 적는다.
set -euo pipefail

cd "$(dirname "$0")/.."

exists() { gh release view "$1" >/dev/null 2>&1; }

if [ -n "${1:-}" ]; then
  stamp="$1"
  if exists "data-${stamp}"; then
    echo "릴리스 data-${stamp} 가 이미 있습니다. 다른 날짜를 인자로 주세요." >&2
    exit 1
  fi
else
  day="$(TZ=Asia/Seoul date +%Y%m%d)"
  stamp="$day"
  n=1
  while exists "data-${stamp}"; do
    n=$((n + 1))
    stamp="${day}-${n}"
  done
fi
tag="data-${stamp}"
asset="snapshot-${stamp}.zip"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

uv run python -m fin_checkup.cli snapshot export --out "$work/snapshot"
# zipfile -c 는 인자마다 basename 으로 넣는다 — 압축 안이 평평하다(fetch_snapshot.sh 가 그대로 푼다).
uv run python -m zipfile -c "$work/$asset" "$work/snapshot"/*
summary="$(uv run python - "$work/snapshot/manifest.json" <<'PYEOF'
import json, sys

m = json.load(open(sys.argv[1], encoding="utf-8"))
print("사업연도 " + ", ".join(str(y) for y in m["bsns_years"]))
for table, info in m["tables"].items():
    print(f"- {table}: {info['rows']:,}행")
PYEOF
)"

notes="fin-checkup 캐시의 공개 테이블(허용목록) 스냅샷. DART 공시 데이터이며 투자권유가 아닙니다.
Pages 워크플로가 이 릴리스를 받아 사이트를 빌드합니다.

${summary}"
if [ -n "${SNAPSHOT_NOTES:-}" ]; then
  notes="${notes}

${SNAPSHOT_NOTES}"
fi

gh release create "$tag" "$work/$asset" "$work/snapshot/manifest.json" \
  --title "데이터 스냅샷 ${stamp}" \
  --latest=false \
  --notes "$notes"
if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "tag=$tag" >> "$GITHUB_OUTPUT"; fi
echo "올렸습니다: $tag ($asset)"
