#!/bin/bash

# =============================================================================
# CodeCrow Qdrant Import Script
# =============================================================================
# Restores collection snapshots and aliases created by qdrant-dump.sh.
#
# Usage:
#   ./qdrant-import.sh <dump-directory>
#   ./qdrant-import.sh latest
#
# A relative dump name is resolved under tools/environment/qdrant-dumps.
#
# Environment overrides:
#   QDRANT_CONTAINER  Container name used for the running-container check
#   QDRANT_URL        Qdrant HTTP endpoint (default: http://localhost:6333)
#   QDRANT_API_KEY    API key; an explicitly empty value disables authentication
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOYMENT_DIR="$PROJECT_ROOT/deployment"
DEFAULT_DUMPS_DIR="$PROJECT_ROOT/tools/environment/qdrant-dumps"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
QDRANT_URL="${QDRANT_URL%/}"

source "$SCRIPT_DIR/backup-lib.sh"

usage() {
  echo "Usage: ./qdrant-import.sh <dump-directory>"
  echo
  echo "A dump name such as 'latest' is resolved under:"
  echo "  tools/environment/qdrant-dumps"
}

if [[ $# -gt 1 ]]; then
  error "Too many arguments."
  usage
  exit 1
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

for command in docker curl gunzip gzip sha256sum; do
  if ! command -v "$command" >/dev/null 2>&1; then
    error "Required command not found: $command"
    exit 1
  fi
done

if ! check_container "$QDRANT_CONTAINER"; then
  error "Start the containers first with: cd deployment && docker compose up -d"
  exit 1
fi

DUMP_PATH="${1:-}"

if [[ -z "$DUMP_PATH" ]]; then
  echo -e "${YELLOW}Available Qdrant dumps:${NC}"
  if [[ -d "$DEFAULT_DUMPS_DIR" ]]; then
    find "$DEFAULT_DUMPS_DIR" -mindepth 1 -maxdepth 1 -type d \
      -name 'qdrant_dump_*' -print 2>/dev/null | sort -r || true
  fi
  echo

  DEFAULT_SELECTION=""
  if [[ -d "$DEFAULT_DUMPS_DIR/latest" ]]; then
    DEFAULT_SELECTION="latest"
  fi

  if [[ -n "$DEFAULT_SELECTION" ]]; then
    read -rp "Enter dump directory [$DEFAULT_SELECTION]: " DUMP_PATH
    DUMP_PATH="${DUMP_PATH:-$DEFAULT_SELECTION}"
  else
    read -rp "Enter dump directory: " DUMP_PATH
  fi
fi

if [[ ! -d "$DUMP_PATH" && -d "$DEFAULT_DUMPS_DIR/$DUMP_PATH" ]]; then
  DUMP_PATH="$DEFAULT_DUMPS_DIR/$DUMP_PATH"
fi

if [[ ! -d "$DUMP_PATH" ]]; then
  error "Qdrant dump directory not found: $DUMP_PATH"
  exit 1
fi

DUMP_PATH="$(cd "$DUMP_PATH" && pwd)"
MANIFEST_FILE="$DUMP_PATH/manifest.tsv"
ALIASES_FILE="$DUMP_PATH/aliases.tsv"
METADATA_FILE="$DUMP_PATH/metadata"

if [[ ! -f "$MANIFEST_FILE" || ! -f "$ALIASES_FILE" || ! -f "$METADATA_FILE" ]]; then
  error "Invalid Qdrant dump: manifest.tsv, aliases.tsv, and metadata are required."
  exit 1
fi

FORMAT_VERSION=$(grep -E '^format_version=' "$METADATA_FILE" | cut -d= -f2- || true)
SOURCE_QDRANT_VERSION=$(grep -E '^qdrant_version=' "$METADATA_FILE" | cut -d= -f2- || true)

if [[ "$FORMAT_VERSION" != "1" ]]; then
  error "Unsupported Qdrant dump format: ${FORMAT_VERSION:-missing}"
  exit 1
fi

declare -a COLLECTIONS=()
declare -a SNAPSHOT_FILES=()
declare -a CHECKSUMS=()
declare -A DUMP_COLLECTIONS=()

while IFS=$'\t' read -r collection snapshot_file checksum; do
  if [[ "$collection" == "collection" && "$snapshot_file" == "snapshot_file" ]]; then
    continue
  fi

  if [[ -z "$collection" || -z "$snapshot_file" || -z "$checksum" ]]; then
    error "Invalid row in $MANIFEST_FILE."
    exit 1
  fi

  if [[ "$snapshot_file" != "$(basename -- "$snapshot_file")" ]]; then
    error "Snapshot paths must be file names: $snapshot_file"
    exit 1
  fi

  if [[ "$snapshot_file" != *.snapshot.gz ]]; then
    error "Snapshot is not gzip-compressed: $snapshot_file"
    exit 1
  fi

  SNAPSHOT_PATH="$DUMP_PATH/$snapshot_file"
  if [[ ! -f "$SNAPSHOT_PATH" ]]; then
    error "Snapshot file not found: $SNAPSHOT_PATH"
    exit 1
  fi

  if ! gzip -t "$SNAPSHOT_PATH"; then
    error "Invalid gzip data in snapshot: $snapshot_file"
    exit 1
  fi

  ACTUAL_CHECKSUM=$(gunzip -c "$SNAPSHOT_PATH" | sha256sum | cut -d' ' -f1)
  if [[ "$ACTUAL_CHECKSUM" != "$checksum" ]]; then
    error "Checksum mismatch for snapshot: $snapshot_file"
    exit 1
  fi

  COLLECTIONS+=("$collection")
  SNAPSHOT_FILES+=("$snapshot_file")
  CHECKSUMS+=("$checksum")
  DUMP_COLLECTIONS["$collection"]=1
done < "$MANIFEST_FILE"

declare -a ALIASES=()
declare -a ALIAS_COLLECTIONS=()

while IFS=$'\t' read -r alias_name collection; do
  if [[ "$alias_name" == "alias" && "$collection" == "collection" ]]; then
    continue
  fi

  if [[ -z "$alias_name" || -z "$collection" ]]; then
    error "Invalid row in $ALIASES_FILE."
    exit 1
  fi

  if [[ -z "${DUMP_COLLECTIONS[$collection]:-}" ]]; then
    error "Alias '$alias_name' targets collection '$collection', which is absent from the dump."
    exit 1
  fi

  ALIASES+=("$alias_name")
  ALIAS_COLLECTIONS+=("$collection")
done < "$ALIASES_FILE"

read_qdrant_api_key "$DEPLOYMENT_DIR"

if ! ROOT_RESPONSE=$(qdrant_curl "$QDRANT_URL/"); then
  error "Qdrant is not reachable at $QDRANT_URL."
  exit 1
fi

TARGET_QDRANT_VERSION=$(printf '%s' "$ROOT_RESPONSE" |
  qdrant_json_string_values "version" |
  head -1)
TARGET_QDRANT_VERSION="${TARGET_QDRANT_VERSION:-unknown}"

echo -e "\n${BOLD}${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║         CodeCrow Qdrant Import                    ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════╝${NC}\n"

info "Qdrant endpoint: ${BOLD}$QDRANT_URL${NC}"
info "Dump directory:  ${BOLD}$DUMP_PATH${NC}"
info "Collections:     ${BOLD}${#COLLECTIONS[@]}${NC}"
info "Aliases:         ${BOLD}${#ALIASES[@]}${NC}"
info "Qdrant versions: ${BOLD}${SOURCE_QDRANT_VERSION:-unknown}${NC} → ${BOLD}$TARGET_QDRANT_VERSION${NC}"

if [[ -n "$SOURCE_QDRANT_VERSION" &&
      "$SOURCE_QDRANT_VERSION" != "unknown" &&
      "$TARGET_QDRANT_VERSION" != "unknown" &&
      "$SOURCE_QDRANT_VERSION" != "$TARGET_QDRANT_VERSION" ]]; then
  warn "Source and target Qdrant versions differ; snapshot compatibility is not guaranteed."
fi

if [[ ${#COLLECTIONS[@]} -eq 0 ]]; then
  success "The dump is valid and contains no collections to restore."
  exit 0
fi

echo
echo -e "${RED}${BOLD}WARNING: This overwrites every collection listed in the dump.${NC}"
echo -e "${YELLOW}Collections not listed in the dump are left unchanged.${NC}"
echo -e "${YELLOW}If a later collection fails, earlier collections remain restored.${NC}"
read -rp "$(echo -e "${BOLD}Type 'yes' to continue: ${NC}")" CONFIRM

if [[ "$CONFIRM" != "yes" ]]; then
  warn "Operation cancelled."
  exit 0
fi

TEMP_SNAPSHOT=$(mktemp "${TMPDIR:-/tmp}/codecrow-qdrant-import.XXXXXX.snapshot")
cleanup_temp_snapshot() {
  rm -f -- "$TEMP_SNAPSHOT"
}
trap cleanup_temp_snapshot EXIT

for i in "${!COLLECTIONS[@]}"; do
  collection="${COLLECTIONS[$i]}"
  snapshot_file="${SNAPSHOT_FILES[$i]}"
  checksum="${CHECKSUMS[$i]}"
  snapshot_path="$DUMP_PATH/$snapshot_file"
  encoded_collection=$(urlencode_path_segment "$collection")
  uploaded_snapshot_file=$(basename "$TEMP_SNAPSHOT")
  encoded_snapshot_file=$(urlencode_path_segment "$uploaded_snapshot_file")

  header "Restoring $collection"

  info "Decompressing snapshot..."
  if ! gunzip -c "$snapshot_path" > "$TEMP_SNAPSHOT"; then
    error "Failed to decompress snapshot for collection '$collection'."
    exit 1
  fi

  if [[ ! -s "$TEMP_SNAPSHOT" ]]; then
    error "Decompressed snapshot for '$collection' is empty."
    exit 1
  fi

  if ! qdrant_curl -X POST \
    "$QDRANT_URL/collections/$encoded_collection/snapshots/upload?priority=snapshot&wait=true&checksum=$checksum" \
    --form "snapshot=@$TEMP_SNAPSHOT" \
    >/dev/null; then
    error "Failed to restore collection '$collection'."
    exit 1
  fi

  if ! qdrant_curl -X DELETE \
    "$QDRANT_URL/collections/$encoded_collection/snapshots/$encoded_snapshot_file" \
    >/dev/null; then
    warn "Collection restored, but uploaded server snapshot '$uploaded_snapshot_file' could not be deleted."
  fi

  success "Restored '$collection'"
done

for i in "${!ALIASES[@]}"; do
  alias_name="${ALIASES[$i]}"
  collection="${ALIAS_COLLECTIONS[$i]}"
  escaped_alias=$(json_escape_string "$alias_name")
  escaped_collection=$(json_escape_string "$collection")
  alias_body="{\"actions\":[{\"delete_alias\":{\"alias_name\":\"$escaped_alias\"}},{\"create_alias\":{\"collection_name\":\"$escaped_collection\",\"alias_name\":\"$escaped_alias\"}}]}"

  info "Restoring alias: $alias_name → $collection"
  if ! qdrant_curl -X POST \
    "$QDRANT_URL/collections/aliases" \
    -H "Content-Type: application/json" \
    --data "$alias_body" \
    >/dev/null; then
    error "Collections were restored, but alias '$alias_name' could not be restored."
    exit 1
  fi
done

echo
success "Qdrant import complete."
echo -e "  Collections restored: ${BOLD}${#COLLECTIONS[@]}${NC}"
echo -e "  Aliases restored:     ${BOLD}${#ALIASES[@]}${NC}"
