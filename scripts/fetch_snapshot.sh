#!/usr/bin/env bash
# 가장 최근 data-* 스냅샷 릴리스(또는 지정한 태그)를 받아 새 캐시 DB 로 들여온다.
# pages.yml(사이트 빌드)과 collect.yml(증분 수집)이 같이 쓴다. 로컬에서 최신 공개 데이터로 시작할 때도.
#
#   scripts/fetch_snapshot.sh <작업폴더> [태그]
#
# 결과: <작업폴더>/files (manifest.json + parquet — `site build --snapshot` 이 읽는다),
#       캐시 DB 는 $FIN_CHECKUP_DB_PATH (없으면 <작업폴더>/fin_checkup.duckdb, 이미 있으면 import 가 거부).
# $GITHUB_OUTPUT 이 있으면 found=true|false 와 tag=... 를 적는다. 릴리스가 없으면 경고만 하고 0 (found=false).
# 레포는 $GH_REPO 나 $GITHUB_REPOSITORY, 없으면 gh 가 git remote 에서 찾는다.
set -euo pipefail

work="${1:?사용법: scripts/fetch_snapshot.sh <작업폴더> [태그]}"
tag="${2:-}"
db="${FIN_CHECKUP_DB_PATH:-$work/fin_checkup.duckdb}"
repo="${GH_REPO:-${GITHUB_REPOSITORY:-}}"
repo_args=()
if [ -n "$repo" ]; then repo_args=(--repo "$repo"); fi

output() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "$1" >> "$GITHUB_OUTPUT"; fi
}

if [ -z "$tag" ]; then
  # 초안은 빼고, 이름이 아니라 만든 시각으로 가장 최근 것 (data-20261121-2 > data-20261121).
  tag="$(gh release list "${repo_args[@]}" --limit 100 \
    --json tagName,isDraft,createdAt \
    --jq '[.[] | select(.isDraft | not) | select(.tagName | startswith("data-"))]
          | sort_by(.createdAt) | reverse | .[0].tagName // ""')"
fi
if [ -z "$tag" ]; then
  echo "::warning::data-* 스냅샷 릴리스가 없다. scripts/publish_snapshot.sh 로 먼저 올릴 것."
  output "found=false"
  exit 0
fi

mkdir -p "$work/download" "$work/files"
gh release download "$tag" "${repo_args[@]}" --pattern 'snapshot-*.zip' --dir "$work/download" --clobber
shopt -s nullglob
zips=("$work"/download/snapshot-*.zip)
if [ "${#zips[@]}" -ne 1 ]; then
  echo "::error::릴리스 $tag 에 snapshot-*.zip 이 하나가 아니다: ${zips[*]}"
  exit 1
fi
uv run python -m zipfile -e "${zips[0]}" "$work/files"
uv run python -m fin_checkup.cli snapshot import "$work/files" --db "$db"
echo "스냅샷: $tag → $db"
output "tag=$tag"
output "found=true"
