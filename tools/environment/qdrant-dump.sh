#!/bin/bash

# =============================================================================
# CodeCrow Qdrant Dump Script
# =============================================================================
# Exports every Qdrant collection as a collection snapshot and records aliases.
#
# Usage:
#   ./qdrant-dump.sh
#   ./qdrant-dump.sh /path/to/output-directory
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
DEFAULT_OUTPUT_DIR="$PROJECT_ROOT/tools/environment/qdrant-dumps"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
QDRANT_URL="${QDRANT_URL%/}"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')

source "$SCRIPT_DIR/backup-lib.sh"

usage() {
  echo "Usage: ./qdrant-dump.sh [output-directory]"
  echo
  echo "Exports all Qdrant collections and aliases."
  echo "Default output directory: tools/environment/qdrant-dumps"
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

for command in docker curl gzip sha256sum; do
  if ! command -v "$command" >/dev/null 2>&1; then
    error "Required command not found: $command"
    exit 1
  fi
done

if ! check_container "$QDRANT_CONTAINER"; then
  error "Start the containers first with: cd deployment && docker compose up -d"
  exit 1
fi

read_qdrant_api_key "$DEPLOYMENT_DIR"

OUTPUT_DIR="${1:-$DEFAULT_OUTPUT_DIR}"
FINAL_DUMP_PATH="$OUTPUT_DIR/qdrant_dump_$TIMESTAMP"
mkdir -p "$OUTPUT_DIR"

if [[ -e "$FINAL_DUMP_PATH" ]]; then
  error "Dump path already exists: $FINAL_DUMP_PATH"
  exit 1
fi

WORK_PATH=$(mktemp -d "$OUTPUT_DIR/.qdrant_dump_${TIMESTAMP}.partial.XXXXXX")
MANIFEST_FILE="$WORK_PATH/manifest.tsv"
ALIASES_FILE="$WORK_PATH/aliases.tsv"
METADATA_FILE="$WORK_PATH/metadata"

on_error() {
  local status=$?

  if [[ $status -ne 0 && -d "$WORK_PATH" ]]; then
    warn "Export failed. Incomplete files were left at: $WORK_PATH"
  fi
}
trap on_error EXIT

echo -e "\n${BOLD}${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║         CodeCrow Qdrant Export                    ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════╝${NC}\n"

info "Qdrant endpoint: ${BOLD}$QDRANT_URL${NC}"
info "Dump directory:  ${BOLD}$FINAL_DUMP_PATH${NC}"

if ! ROOT_RESPONSE=$(qdrant_curl "$QDRANT_URL/"); then
  error "Qdrant is not reachable at $QDRANT_URL."
  exit 1
fi

QDRANT_VERSION=$(printf '%s' "$ROOT_RESPONSE" |
  qdrant_json_string_values "version" |
  head -1)
QDRANT_VERSION="${QDRANT_VERSION:-unknown}"

if ! COLLECTIONS_RESPONSE=$(qdrant_curl "$QDRANT_URL/collections"); then
  error "Could not list Qdrant collections."
  exit 1
fi

mapfile -t COLLECTIONS < <(
  printf '%s' "$COLLECTIONS_RESPONSE" |
    qdrant_json_string_values "name"
)

printf 'collection\tsnapshot_file\tsha256\n' > "$MANIFEST_FILE"
printf 'alias\tcollection\n' > "$ALIASES_FILE"
{
  echo "format_version=1"
  echo "created_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "qdrant_version=$QDRANT_VERSION"
} > "$METADATA_FILE"

if [[ ${#COLLECTIONS[@]} -eq 0 ]]; then
  warn "No Qdrant collections found; creating an empty dump."
else
  info "Found ${#COLLECTIONS[@]} collection(s)."
fi

SNAPSHOT_INDEX=0
ALIAS_COUNT=0

for collection in "${COLLECTIONS[@]}"; do
  SNAPSHOT_INDEX=$((SNAPSHOT_INDEX + 1))
  ENCODED_COLLECTION=$(urlencode_path_segment "$collection")

  header "Exporting $collection"

  if ! SNAPSHOT_RESPONSE=$(qdrant_curl -X POST \
    "$QDRANT_URL/collections/$ENCODED_COLLECTION/snapshots"); then
    error "Failed to create a snapshot for collection '$collection'."
    exit 1
  fi

  SNAPSHOT_NAME=$(printf '%s' "$SNAPSHOT_RESPONSE" |
    qdrant_json_string_values "name" |
    head -1)

  if [[ -z "$SNAPSHOT_NAME" ]]; then
    error "Qdrant did not return a snapshot name for '$collection'."
    exit 1
  fi

  RAW_SNAPSHOT_FILE=$(printf 'collection_%06d.snapshot' "$SNAPSHOT_INDEX")
  RAW_SNAPSHOT_PATH="$WORK_PATH/$RAW_SNAPSHOT_FILE"
  ENCODED_SNAPSHOT=$(urlencode_path_segment "$SNAPSHOT_NAME")

  info "Downloading snapshot: $SNAPSHOT_NAME"
  if ! qdrant_curl \
    "$QDRANT_URL/collections/$ENCODED_COLLECTION/snapshots/$ENCODED_SNAPSHOT" \
    --output "$RAW_SNAPSHOT_PATH"; then
    error "Failed to download snapshot for '$collection'."
    exit 1
  fi

  if [[ ! -s "$RAW_SNAPSHOT_PATH" ]]; then
    error "Downloaded snapshot for '$collection' is empty."
    exit 1
  fi

  CHECKSUM=$(sha256sum "$RAW_SNAPSHOT_PATH" | cut -d' ' -f1)
  info "Compressing snapshot..."
  gzip "$RAW_SNAPSHOT_PATH"

  SNAPSHOT_FILE="$RAW_SNAPSHOT_FILE.gz"
  SNAPSHOT_PATH="$WORK_PATH/$SNAPSHOT_FILE"
  printf '%s\t%s\t%s\n' "$collection" "$SNAPSHOT_FILE" "$CHECKSUM" >> "$MANIFEST_FILE"

  if ! ALIASES_RESPONSE=$(qdrant_curl \
    "$QDRANT_URL/collections/$ENCODED_COLLECTION/aliases"); then
    error "Failed to list aliases for '$collection'."
    exit 1
  fi

  mapfile -t COLLECTION_ALIASES < <(
    printf '%s' "$ALIASES_RESPONSE" |
      qdrant_json_string_values "alias_name"
  )

  for alias_name in "${COLLECTION_ALIASES[@]}"; do
    printf '%s\t%s\n' "$alias_name" "$collection" >> "$ALIASES_FILE"
    ALIAS_COUNT=$((ALIAS_COUNT + 1))
  done

  if ! qdrant_curl -X DELETE \
    "$QDRANT_URL/collections/$ENCODED_COLLECTION/snapshots/$ENCODED_SNAPSHOT" \
    >/dev/null; then
    warn "Could not delete temporary server snapshot '$SNAPSHOT_NAME'."
  fi

  SNAPSHOT_SIZE=$(du -h "$SNAPSHOT_PATH" | cut -f1)
  success "Exported '$collection' ($SNAPSHOT_SIZE)"
done

mv "$WORK_PATH" "$FINAL_DUMP_PATH"
WORK_PATH=""

if [[ -e "$OUTPUT_DIR/latest" && ! -L "$OUTPUT_DIR/latest" ]]; then
  warn "Not replacing '$OUTPUT_DIR/latest' because it is not a symlink."
else
  ln -sfn "$(basename "$FINAL_DUMP_PATH")" "$OUTPUT_DIR/latest"
fi

TOTAL_SIZE=$(du -sh "$FINAL_DUMP_PATH" | cut -f1)

echo
success "Qdrant export complete."
echo -e "  Location:    ${BOLD}$FINAL_DUMP_PATH${NC}"
echo -e "  Collections: ${BOLD}${#COLLECTIONS[@]}${NC}"
echo -e "  Aliases:     ${BOLD}$ALIAS_COUNT${NC}"
echo -e "  Total size:  ${BOLD}$TOTAL_SIZE${NC}"
echo
echo -e "${YELLOW}To restore this dump:${NC}"
echo -e "  ${DIM}./tools/environment/qdrant-import.sh $FINAL_DUMP_PATH${NC}"
