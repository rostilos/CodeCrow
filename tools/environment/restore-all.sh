#!/bin/bash

# =============================================================================
# CodeCrow Full Restore Script
# =============================================================================
# Restores a CodeCrow backup created by backup-all.sh:
#   - PostgreSQL database (drops + recreates)
#   - Qdrant vector snapshots (via HTTP API)
#   - Redis data (RDB file copy)
#   - Config files (extracts tar archive)
#
# Usage:
#   ./restore-all.sh <backup-dir>
#   ./restore-all.sh <backup-dir> --pg-only
#   ./restore-all.sh <backup-dir> --skip-qdrant
#
# Requirements: docker, curl, tar, gunzip
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOYMENT_DIR="$PROJECT_ROOT/deployment"

# Source shared library (colors, helpers, container names)
source "$SCRIPT_DIR/backup-lib.sh"

# Defaults
DO_PG=true
DO_QDRANT=true
DO_REDIS=true
DO_CONFIG=true
BACKUP_PATH=""

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
      echo "Usage: ./restore-all.sh <backup-dir> [flags]"
      echo "  backup-dir        Path to backup directory (created by backup-all.sh)"
      echo "  --pg-only         Restore PostgreSQL only"
      echo "  --skip-qdrant     Skip Qdrant restore"
      echo "  --skip-redis      Skip Redis restore"
      echo "  --skip-config     Skip config files restore"
      echo "  --help            Show this help"
      exit 0
      ;;
    -*)
      error "Unknown flag: $arg (use --help)"
      exit 1
      ;;
    *)
      BACKUP_PATH="$arg" ;;
  esac
done

if [[ -z "$BACKUP_PATH" ]]; then
  error "Backup directory path is required."
  echo -e "${DIM}Usage: ./restore-all.sh <backup-dir>${NC}"
  echo
  # List available backups
  BACKUP_BASE="$PROJECT_ROOT/tools/environment/backups"
  if [[ -d "$BACKUP_BASE" ]]; then
    echo -e "${YELLOW}Available backups:${NC}"
    ls -d "$BACKUP_BASE"/codecrow_backup_* 2>/dev/null | while read -r dir; do
      echo -e "  ${DIM}$(basename "$dir")${NC}"
    done
  fi
  exit 1
fi

if [[ ! -d "$BACKUP_PATH" ]]; then
  error "Backup directory not found: $BACKUP_PATH"
  exit 1
fi

echo -e "\n${BOLD}${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║         CodeCrow Full Restore                    ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════╝${NC}\n"

info "Restoring from: ${BOLD}$BACKUP_PATH${NC}"
echo

# ── Warning ────────────────────────────────────────────────────────────────

echo -e "${RED}${BOLD}⚠  WARNING: This will OVERWRITE existing data!${NC}"
echo -e "${RED}   PostgreSQL database will be dropped and recreated.${NC}"
echo -e "${RED}   Qdrant collections will be overwritten.${NC}"
echo -e "${RED}   Config files will be replaced.${NC}"
echo
read -rp "$(echo -e "${BOLD}Type 'yes' to confirm: ${NC}")" CONFIRM
if [[ "$CONFIRM" != "yes" ]]; then
  echo -e "${YELLOW}Operation cancelled.${NC}"
  exit 0
fi

# ── 1. PostgreSQL Restore ────────────────────────────────────────────────

if $DO_PG; then
  header "1. PostgreSQL Database"
  PG_DUMP=$(find "$BACKUP_PATH" -name "postgresql_*.sql.gz" -o -name "postgresql_*.sql" | head -1)

  if [[ -z "$PG_DUMP" ]]; then
    warn "No PostgreSQL dump found in backup."
  elif check_container "$PG_CONTAINER"; then
    read_pg_credentials "$DEPLOYMENT_DIR" "prompt"

    info "Terminating connections to '$DB_NAME'..."
    pg_exec "$PG_CONTAINER" "$DB_USER" "$DB_PASS" "postgres" \
      -c "SELECT pg_terminate_backend(pg_stat_activity.pid)
          FROM pg_stat_activity
          WHERE pg_stat_activity.datname = '$DB_NAME'
            AND pid <> pg_backend_pid();" > /dev/null 2>&1 || true

    info "Dropping and recreating database '$DB_NAME'..."
    pg_exec "$PG_CONTAINER" "$DB_USER" "$DB_PASS" "postgres" \
      -c "DROP DATABASE IF EXISTS $DB_NAME;" > /dev/null 2>&1
    pg_exec "$PG_CONTAINER" "$DB_USER" "$DB_PASS" "postgres" \
      -c "CREATE DATABASE $DB_NAME;" > /dev/null 2>&1

    info "Importing dump (this may take a while)..."
    if [[ "$PG_DUMP" == *.gz ]]; then
      gunzip -c "$PG_DUMP" | pg_exec_stdin "$PG_CONTAINER" "$DB_USER" "$DB_PASS" "$DB_NAME" -q 2>/dev/null
    else
      pg_exec_stdin "$PG_CONTAINER" "$DB_USER" "$DB_PASS" "$DB_NAME" -q < "$PG_DUMP" 2>/dev/null
    fi

    success "PostgreSQL restored from: $(basename "$PG_DUMP")"
  fi
fi

# ── 2. Qdrant Restore ───────────────────────────────────────────────────

if $DO_QDRANT; then
  header "2. Qdrant Vectors"
  QDRANT_DIR="$BACKUP_PATH/qdrant"

  if [[ ! -d "$QDRANT_DIR" ]] || [[ -z "$(ls -A "$QDRANT_DIR" 2>/dev/null)" ]]; then
    warn "No Qdrant snapshots found in backup."
  elif check_container "$QDRANT_CONTAINER"; then
    read_qdrant_api_key "$DEPLOYMENT_DIR"
    for snapshot_file in "$QDRANT_DIR"/*; do
      FILENAME=$(basename "$snapshot_file")
      # Extract collection name (format: collectionname_snapshotname)
      COLLECTION=$(echo "$FILENAME" | sed 's/_[^_]*$//')

      info "Restoring Qdrant collection: $COLLECTION"

      # Copy snapshot into container
      docker cp "$snapshot_file" "$QDRANT_CONTAINER:/qdrant/snapshots/${FILENAME}"

      # Recover from snapshot
      RECOVER_RESPONSE=$(curl -s $(qdrant_auth_header) -X PUT "http://localhost:6333/collections/$COLLECTION/snapshots/recover" \
        -H "Content-Type: application/json" \
        -d "{\"location\": \"/qdrant/snapshots/$FILENAME\"}" 2>/dev/null)

      if echo "$RECOVER_RESPONSE" | grep -q '"status":"ok"'; then
        success "Qdrant '$COLLECTION' restored"
      else
        warn "Qdrant restore for '$COLLECTION' may have issues: $RECOVER_RESPONSE"
      fi
    done
  fi
fi

# ── 3. Redis Restore ────────────────────────────────────────────────────

if $DO_REDIS; then
  header "3. Redis Cache"
  REDIS_FILE="$BACKUP_PATH/redis_dump.rdb"

  if [[ ! -f "$REDIS_FILE" ]]; then
    warn "No Redis dump found in backup."
  elif check_container "$REDIS_CONTAINER"; then
    # Resolve actual RDB path from Redis config
    resolve_redis_rdb_path "$REDIS_CONTAINER"

    info "Stopping Redis to replace RDB file..."
    docker exec "$REDIS_CONTAINER" redis-cli SHUTDOWN NOSAVE > /dev/null 2>&1 || true
    sleep 2

    # Copy RDB file and restart
    docker cp "$REDIS_FILE" "$REDIS_CONTAINER:$REDIS_INTERNAL_PATH"
    docker start "$REDIS_CONTAINER" > /dev/null 2>&1

    # Wait for Redis to be ready
    for i in $(seq 1 10); do
      if docker exec "$REDIS_CONTAINER" redis-cli ping > /dev/null 2>&1; then
        break
      fi
      sleep 1
    done

    success "Redis restored"
  fi
fi

# ── 4. Config Files ─────────────────────────────────────────────────────

if $DO_CONFIG; then
  header "4. Configuration Files"
  CONFIG_FILE="$BACKUP_PATH/config_files.tar.gz"

  if [[ ! -f "$CONFIG_FILE" ]]; then
    warn "No config backup found."
  else
    info "Extracting config files..."
    tar xzf "$CONFIG_FILE" -C / 2>/dev/null || tar xzf "$CONFIG_FILE" 2>/dev/null
    success "Config files restored"
  fi
fi

# ── Summary ──────────────────────────────────────────────────────────────

header "Restore Summary"

echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║            Restore Complete! 🎉                  ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo
echo -e "  Restored from: ${BOLD}$BACKUP_PATH${NC}"
echo
echo -e "${YELLOW}Next steps:${NC}"
echo -e "  1. Restart all services:  ${DIM}cd deployment && docker compose restart${NC}"
echo -e "  2. Verify health:         ${DIM}docker compose ps${NC}"
echo -e "  3. Check logs:            ${DIM}docker compose logs --tail 20${NC}"
