#!/bin/bash

# =============================================================================
# CodeCrow Full Backup Script
# =============================================================================
# Creates a complete backup of all CodeCrow data stores:
#   - PostgreSQL database (pg_dump)
#   - Qdrant vector snapshots (HTTP API)
#   - Redis data (RDB snapshot)
#   - Config files (tar archive)
#
# Usage:
#   ./backup-all.sh                      # backup to default directory
#   ./backup-all.sh /path/to/backup/dir  # backup to custom directory
#   ./backup-all.sh --pg-only            # PostgreSQL only
#   ./backup-all.sh --skip-qdrant        # skip Qdrant (faster)
#
# Requirements: docker, curl, tar
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOYMENT_DIR="$PROJECT_ROOT/deployment"
DEFAULT_BACKUP_DIR="$PROJECT_ROOT/tools/environment/backups"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

info()    { echo -e "${BLUE}ℹ${NC}  $*"; }
success() { echo -e "${GREEN}✔${NC}  $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✖${NC}  $*" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}── $* ──${NC}\n"; }

# Container names
PG_CONTAINER="codecrow-postgres"
QDRANT_CONTAINER="codecrow-qdrant"
REDIS_CONTAINER="codecrow-redis"

# Defaults
BACKUP_DIR="$DEFAULT_BACKUP_DIR"
DO_PG=true
DO_QDRANT=true
DO_REDIS=true
DO_CONFIG=true

# ── Parse arguments ────────────────────────────────────────────────────────

for arg in "$@"; do
  case "$arg" in
    --pg-only)
      DO_QDRANT=false; DO_REDIS=false; DO_CONFIG=false ;;
    --skip-qdrant)
      DO_QDRANT=false ;;
    --skip-redis)
      DO_REDIS=false ;;
    --skip-config)
      DO_CONFIG=false ;;
    --help|-h)
      echo "Usage: ./backup-all.sh [backup-dir] [flags]"
      echo "  backup-dir        Output directory (default: tools/environment/backups)"
      echo "  --pg-only         Backup PostgreSQL only"
      echo "  --skip-qdrant     Skip Qdrant vector backup"
      echo "  --skip-redis      Skip Redis backup"
      echo "  --skip-config     Skip config files backup"
      echo "  --help            Show this help"
      exit 0
      ;;
    -*)
      error "Unknown flag: $arg (use --help)"
      exit 1
      ;;
    *)
      BACKUP_DIR="$arg" ;;
  esac
done

# Create timestamped backup subdirectory
BACKUP_PATH="$BACKUP_DIR/codecrow_backup_$TIMESTAMP"
mkdir -p "$BACKUP_PATH"

echo -e "\n${BOLD}${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║         CodeCrow Full Backup                     ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════╝${NC}\n"

info "Backup directory: ${BOLD}$BACKUP_PATH${NC}"
echo

# ── Helper: check container running ────────────────────────────────────────

check_container() {
  local name="$1"
  if ! docker ps --format '{{.Names}}' | grep -q "^${name}$"; then
    warn "Container '$name' is not running — skipping."
    return 1
  fi
  return 0
}

# ── 1. PostgreSQL Backup ──────────────────────────────────────────────────

if $DO_PG; then
  header "1. PostgreSQL Database"
  if check_container "$PG_CONTAINER"; then
    # Read credentials from .env file if available
    DB_NAME="codecrow_ai"
    DB_USER="codecrow_user"
    if [[ -f "$DEPLOYMENT_DIR/.env" ]]; then
      DB_NAME=$(grep -E '^POSTGRES_DB=' "$DEPLOYMENT_DIR/.env" | cut -d= -f2 || echo "codecrow_ai")
      DB_USER=$(grep -E '^POSTGRES_USER=' "$DEPLOYMENT_DIR/.env" | cut -d= -f2 || echo "codecrow_user")
      DB_NAME="${DB_NAME:-codecrow_ai}"
      DB_USER="${DB_USER:-codecrow_user}"
    fi

    PG_DUMP_FILE="$BACKUP_PATH/postgresql_${DB_NAME}.sql.gz"
    info "Dumping database '$DB_NAME' (user: $DB_USER)..."
    docker exec "$PG_CONTAINER" pg_dump -h localhost -U "$DB_USER" -d "$DB_NAME" | gzip > "$PG_DUMP_FILE"

    PG_SIZE=$(du -h "$PG_DUMP_FILE" | cut -f1)
    success "PostgreSQL backup: $PG_SIZE (compressed)"
  fi
fi

# ── 2. Qdrant Vector Backup ──────────────────────────────────────────────

if $DO_QDRANT; then
  header "2. Qdrant Vectors"
  if check_container "$QDRANT_CONTAINER"; then
    QDRANT_DIR="$BACKUP_PATH/qdrant"
    mkdir -p "$QDRANT_DIR"

    # Get all collections
    COLLECTIONS=$(curl -s http://localhost:6333/collections 2>/dev/null | grep -o '"name":"[^"]*"' | cut -d'"' -f4)

    if [[ -z "$COLLECTIONS" ]]; then
      warn "No Qdrant collections found or Qdrant not accessible on localhost:6333."
    else
      for collection in $COLLECTIONS; do
        info "Creating snapshot for collection: $collection"
        SNAPSHOT_RESPONSE=$(curl -s -X POST "http://localhost:6333/collections/$collection/snapshots" 2>/dev/null)
        SNAPSHOT_NAME=$(echo "$SNAPSHOT_RESPONSE" | grep -o '"name":"[^"]*"' | head -1 | cut -d'"' -f4)

        if [[ -n "$SNAPSHOT_NAME" ]]; then
          info "Downloading snapshot: $SNAPSHOT_NAME"
          curl -s "http://localhost:6333/collections/$collection/snapshots/$SNAPSHOT_NAME" \
            --output "$QDRANT_DIR/${collection}_${SNAPSHOT_NAME}" 2>/dev/null
          SNAP_SIZE=$(du -h "$QDRANT_DIR/${collection}_${SNAPSHOT_NAME}" | cut -f1)
          success "Qdrant '$collection': $SNAP_SIZE"
        else
          warn "Failed to create snapshot for '$collection'"
        fi
      done
    fi
  fi
fi

# ── 3. Redis Backup ─────────────────────────────────────────────────────

if $DO_REDIS; then
  header "3. Redis Cache"
  if check_container "$REDIS_CONTAINER"; then
    REDIS_FILE="$BACKUP_PATH/redis_dump.rdb"

    # Trigger a background save
    docker exec "$REDIS_CONTAINER" redis-cli BGSAVE > /dev/null 2>&1
    sleep 2

    # Copy the RDB file
    docker cp "$REDIS_CONTAINER:/data/dump.rdb" "$REDIS_FILE" 2>/dev/null || {
      warn "Redis RDB file not found (AOF-only or empty)."
      DO_REDIS_DONE=false
    }

    if [[ -f "$REDIS_FILE" ]]; then
      REDIS_SIZE=$(du -h "$REDIS_FILE" | cut -f1)
      success "Redis backup: $REDIS_SIZE"
    fi
  fi
fi

# ── 4. Config Files ─────────────────────────────────────────────────────

if $DO_CONFIG; then
  header "4. Configuration Files"
  CONFIG_FILE="$BACKUP_PATH/config_files.tar.gz"

  CONFIG_FILES=()
  [[ -f "$DEPLOYMENT_DIR/config/java-shared/application.properties" ]] && \
    CONFIG_FILES+=("$DEPLOYMENT_DIR/config/java-shared/application.properties")
  [[ -f "$DEPLOYMENT_DIR/config/inference-orchestrator/.env" ]] && \
    CONFIG_FILES+=("$DEPLOYMENT_DIR/config/inference-orchestrator/.env")
  [[ -f "$DEPLOYMENT_DIR/config/rag-pipeline/.env" ]] && \
    CONFIG_FILES+=("$DEPLOYMENT_DIR/config/rag-pipeline/.env")
  [[ -f "$DEPLOYMENT_DIR/config/web-frontend/.env" ]] && \
    CONFIG_FILES+=("$DEPLOYMENT_DIR/config/web-frontend/.env")
  [[ -f "$DEPLOYMENT_DIR/.env" ]] && \
    CONFIG_FILES+=("$DEPLOYMENT_DIR/.env")

  if [[ ${#CONFIG_FILES[@]} -gt 0 ]]; then
    tar czf "$CONFIG_FILE" "${CONFIG_FILES[@]}" 2>/dev/null
    CONFIG_SIZE=$(du -h "$CONFIG_FILE" | cut -f1)
    success "Config backup: $CONFIG_SIZE (${#CONFIG_FILES[@]} files)"
  else
    warn "No config files found."
  fi
fi

# ── Summary ──────────────────────────────────────────────────────────────

header "Backup Summary"
TOTAL_SIZE=$(du -sh "$BACKUP_PATH" | cut -f1)

echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║            Backup Complete! 🎉                   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo
echo -e "  Location:   ${BOLD}$BACKUP_PATH${NC}"
echo -e "  Total size: ${BOLD}$TOTAL_SIZE${NC}"
echo -e "  Timestamp:  ${DIM}$TIMESTAMP${NC}"
echo
ls -lh "$BACKUP_PATH/" | tail -n +2 | while read -r line; do
  echo -e "  ${DIM}$line${NC}"
done
echo
echo -e "${YELLOW}To restore from this backup:${NC}"
echo -e "  ${DIM}./restore-all.sh $BACKUP_PATH${NC}"
echo
echo -e "${YELLOW}Security note:${NC} This backup contains secrets and credentials."
echo -e "Store it in a ${BOLD}secure, encrypted location${NC}."
