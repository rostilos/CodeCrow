#!/bin/bash

# =============================================================================
# CodeCrow Full Backup Script
# =============================================================================
# Creates a complete backup of CodeCrow's durable application state:
#   - PostgreSQL database (pg_dump)
#   - Redis data (RDB snapshot)
#   - Config files (tar archive)
# Structural repository indexes are derived from repository source and are
# not part of this durable application backup. Run normal branch indexing to
# rebuild them after a restore.
#
# Usage:
#   ./backup-all.sh                      # backup to default directory
#   ./backup-all.sh /path/to/backup/dir  # backup to custom directory
#   ./backup-all.sh --pg-only            # PostgreSQL only
#
# Requirements: docker, tar
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOYMENT_DIR="$PROJECT_ROOT/deployment"
DEFAULT_BACKUP_DIR="$PROJECT_ROOT/tools/environment/backups"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')

# Source shared library (colors, helpers, container names)
source "$SCRIPT_DIR/backup-lib.sh"

# Defaults
BACKUP_DIR="$DEFAULT_BACKUP_DIR"
DO_PG=true
DO_REDIS=true
DO_CONFIG=true

# ── Parse arguments ────────────────────────────────────────────────────────

for arg in "$@"; do
  case "$arg" in
    --pg-only)
      DO_REDIS=false; DO_CONFIG=false ;;
    --skip-redis)
      DO_REDIS=false ;;
    --skip-config)
      DO_CONFIG=false ;;
    --help|-h)
      echo "Usage: ./backup-all.sh [backup-dir] [flags]"
      echo "  backup-dir        Output directory (default: tools/environment/backups)"
      echo "  --pg-only         Backup PostgreSQL only"
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

# ── 1. PostgreSQL Backup ──────────────────────────────────────────────────

if $DO_PG; then
  header "1. PostgreSQL Database"
  if check_container "$PG_CONTAINER"; then
    read_pg_credentials "$DEPLOYMENT_DIR"

    PG_DUMP_FILE="$BACKUP_PATH/postgresql_${DB_NAME}.sql.gz"
    info "Dumping database '$DB_NAME' (user: $DB_USER)..."
    docker exec "$PG_CONTAINER" pg_dump -h localhost -U "$DB_USER" -d "$DB_NAME" | gzip > "$PG_DUMP_FILE"

    PG_SIZE=$(du -h "$PG_DUMP_FILE" | cut -f1)
    success "PostgreSQL backup: $PG_SIZE (compressed)"
  fi
fi

# ── 2. Redis Backup ─────────────────────────────────────────────────────

if $DO_REDIS; then
  header "2. Redis Cache"
  if check_container "$REDIS_CONTAINER"; then
    REDIS_FILE="$BACKUP_PATH/redis_dump.rdb"

    # Resolve actual RDB path from Redis config
    resolve_redis_rdb_path "$REDIS_CONTAINER"

    # Trigger a background save
    docker exec "$REDIS_CONTAINER" redis-cli BGSAVE > /dev/null 2>&1
    sleep 2

    # Copy the RDB file
    docker cp "$REDIS_CONTAINER:$REDIS_INTERNAL_PATH" "$REDIS_FILE" 2>/dev/null || {
      warn "Redis RDB file not found at $REDIS_INTERNAL_PATH (AOF-only or empty)."
      DO_REDIS_DONE=false
    }

    if [[ -f "$REDIS_FILE" ]]; then
      REDIS_SIZE=$(du -h "$REDIS_FILE" | cut -f1)
      success "Redis backup: $REDIS_SIZE"
    fi
  fi
fi

# ── 3. Config Files ─────────────────────────────────────────────────────

if $DO_CONFIG; then
  header "3. Configuration Files"
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
