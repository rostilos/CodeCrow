#!/bin/bash -p
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"
WORKSPACE_MAVEN_REPOSITORY="$REPOSITORY_ROOT/.llm-handoff-artifacts/p0-07/dependency-cache/maven"
CACHE_CLOSURE="$REPOSITORY_ROOT/.llm-handoff-artifacts/p0-07/cache-closure"
FROZEN_MAVEN_MANIFEST="$CACHE_CLOSURE/p0-07-maven-cache-manifest.sha256"
CACHE_RECEIPT="$CACHE_CLOSURE/p0-07-maven-cache.receipt"
REQUESTED_MAVEN_REPOSITORY="${CODECROW_MAVEN_REPOSITORY:-}"
EXPECTED_RECEIPT_SHA256="${CODECROW_P007_CACHE_RECEIPT_SHA256:-}"

if [[ ! "$EXPECTED_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "ERROR: P0-07 Maven cache receipt identity is required" >&2
  exit 65
fi
if [[ -z "$REQUESTED_MAVEN_REPOSITORY" ]]; then
  echo "ERROR: P0-07 Maven repository selection is required" >&2
  exit 65
fi
REQUESTED_LEXICAL="$(realpath -ms "$REQUESTED_MAVEN_REPOSITORY")"
REQUESTED_RESOLVED="$(realpath -m "$REQUESTED_MAVEN_REPOSITORY")"
if [[ "$REQUESTED_LEXICAL" != "$REQUESTED_RESOLVED" ]]; then
  echo "ERROR: P0-07 Maven repository must not be selected through a symlink" >&2
  exit 65
fi
if [[ "$REQUESTED_RESOLVED" != "$WORKSPACE_MAVEN_REPOSITORY" ]]; then
  echo "ERROR: Maven repository must be the P0-07 frozen workspace cache" >&2
  exit 65
fi

for directory in "$WORKSPACE_MAVEN_REPOSITORY" "$CACHE_CLOSURE"; do
  if [[ -L "$directory" || ! -d "$directory" ]]; then
    echo "ERROR: P0-07 Maven cache closure directory is missing or symlinked" >&2
    exit 65
  fi
done
for file in "$FROZEN_MAVEN_MANIFEST" "$CACHE_RECEIPT"; do
  if [[ -L "$file" || ! -f "$file" ]]; then
    echo "ERROR: P0-07 Maven cache closure file is missing or symlinked" >&2
    exit 65
  fi
  if [[ "$(stat -c '%u:%a' "$file")" != "$(id -u):444" ]]; then
    echo "ERROR: P0-07 Maven cache closure file ownership or mode is unsafe" >&2
    exit 65
  fi
done
if [[ "$(stat -c '%u:%a' "$WORKSPACE_MAVEN_REPOSITORY")" != "$(id -u):555" ]]; then
  echo "ERROR: P0-07 Maven cache root ownership or mode is unsafe" >&2
  exit 65
fi
if [[ "$(sha256sum "$CACHE_RECEIPT" | cut -d' ' -f1)" != "$EXPECTED_RECEIPT_SHA256" ]]; then
  echo "ERROR: P0-07 Maven cache receipt identity mismatch" >&2
  exit 65
fi
if [[ "$(tail -c 1 "$CACHE_RECEIPT" | od -An -tu1 | tr -d ' ')" != 10 ]] \
  || grep -q $'\r' "$CACHE_RECEIPT"; then
  echo "ERROR: P0-07 Maven cache receipt is not canonical LF text" >&2
  exit 65
fi
mapfile -t RECEIPT_LINES <"$CACHE_RECEIPT"
if [[ ${#RECEIPT_LINES[@]} -ne 5 \
  || "${RECEIPT_LINES[0]}" != 'schemaVersion=1' \
  || "${RECEIPT_LINES[1]}" != 'cachePath=.llm-handoff-artifacts/p0-07/dependency-cache/maven' \
  || ! "${RECEIPT_LINES[2]}" =~ ^cacheManifestSha256=([0-9a-f]{64})$ \
  || ! "${RECEIPT_LINES[3]}" =~ ^entryCount=([1-9][0-9]*)$ \
  || ! "${RECEIPT_LINES[4]}" =~ ^pomInventorySha256=([0-9a-f]{64})$ ]]; then
  echo "ERROR: P0-07 Maven cache receipt contract mismatch" >&2
  exit 65
fi
MANIFEST_SHA256="${RECEIPT_LINES[2]#cacheManifestSha256=}"
ENTRY_COUNT="${RECEIPT_LINES[3]#entryCount=}"
POM_INVENTORY_SHA256="${RECEIPT_LINES[4]#pomInventorySha256=}"

if [[ "$(sha256sum "$FROZEN_MAVEN_MANIFEST" | cut -d' ' -f1)" != "$MANIFEST_SHA256" ]]; then
  echo "ERROR: P0-07 Maven cache manifest identity mismatch" >&2
  exit 65
fi
ACTUAL_POM_INVENTORY_SHA256="$(
  cd "$REPOSITORY_ROOT"
  find java-ecosystem -name pom.xml -not -path '*/target/*' -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum \
    | sha256sum \
    | cut -d' ' -f1
)"
if [[ "$ACTUAL_POM_INVENTORY_SHA256" != "$POM_INVENTORY_SHA256" ]]; then
  echo "ERROR: P0-07 Maven cache was resolved for a different POM inventory" >&2
  exit 65
fi
if [[ -n "$(find "$WORKSPACE_MAVEN_REPOSITORY" -type l -print -quit)" ]]; then
  echo "ERROR: P0-07 Maven cache must not contain symlinks" >&2
  exit 65
fi
if [[ -n "$(find "$WORKSPACE_MAVEN_REPOSITORY" -name '*.lastUpdated' -print -quit)" ]]; then
  echo "ERROR: P0-07 Maven cache must not contain .lastUpdated files" >&2
  exit 65
fi
if [[ -n "$(find "$WORKSPACE_MAVEN_REPOSITORY" -perm /0222 -print -quit)" ]]; then
  echo "ERROR: P0-07 Maven cache must remain frozen and read-only" >&2
  exit 65
fi
if grep -Evq '^[0-9a-f]{64}  [A-Za-z0-9._+/@=-]+$' "$FROZEN_MAVEN_MANIFEST"; then
  echo "ERROR: P0-07 Maven cache manifest contains an unsafe entry" >&2
  exit 65
fi
MANIFEST_ENTRY_COUNT="$(wc -l <"$FROZEN_MAVEN_MANIFEST")"
CACHE_FILE_COUNT="$(find "$WORKSPACE_MAVEN_REPOSITORY" -type f -printf . | wc -c)"
if [[ "$MANIFEST_ENTRY_COUNT" != "$ENTRY_COUNT" || "$CACHE_FILE_COUNT" != "$ENTRY_COUNT" ]]; then
  echo "ERROR: P0-07 Maven cache file inventory mismatch" >&2
  exit 65
fi
if ! (cd "$WORKSPACE_MAVEN_REPOSITORY" \
  && sha256sum --check --strict --quiet "$FROZEN_MAVEN_MANIFEST"); then
  echo "ERROR: P0-07 Maven cache failed frozen-manifest verification" >&2
  exit 65
fi

realpath -e "$WORKSPACE_MAVEN_REPOSITORY"
