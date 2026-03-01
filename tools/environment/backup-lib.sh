#!/bin/bash
# =============================================================================
# CodeCrow Backup/Restore Shared Library
# =============================================================================
# Sourced by backup-all.sh, restore-all.sh, and db-import.sh to avoid
# duplicating container checks, credential reading, and PostgreSQL helpers.
#
# Usage:  source "$(dirname "${BASH_SOURCE[0]}")/backup-lib.sh"
# =============================================================================

# ─── Colors ──────────────────────────────────────────────────────────────────

RED='\033[0;31m'  GREEN='\033[0;32m'  YELLOW='\033[1;33m'
BLUE='\033[0;34m'  CYAN='\033[0;36m'  BOLD='\033[1m'  DIM='\033[2m'  NC='\033[0m'

info()    { echo -e "${BLUE}ℹ${NC}  $*"; }
success() { echo -e "${GREEN}✔${NC}  $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✖${NC}  $*" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}── $* ──${NC}\n"; }

# ─── Container names ────────────────────────────────────────────────────────

PG_CONTAINER="${PG_CONTAINER:-codecrow-postgres}"
QDRANT_CONTAINER="${QDRANT_CONTAINER:-codecrow-qdrant}"
REDIS_CONTAINER="${REDIS_CONTAINER:-codecrow-redis}"

# ─── Container health ───────────────────────────────────────────────────────

check_container() {
  local name="$1"
  if ! docker ps --format '{{.Names}}' | grep -q "^${name}$"; then
    warn "Container '$name' is not running — skipping."
    return 1
  fi
  return 0
}

# ─── PostgreSQL credential reading ──────────────────────────────────────────

# Reads POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD from deployment/.env
# Sets: DB_NAME, DB_USER, DB_PASS
# Arguments:
#   $1 — path to the deployment directory (must contain .env)
#   $2 — if "prompt", will prompt for password when missing
read_pg_credentials() {
  local deployment_dir="$1"
  local mode="${2:-}"

  DB_NAME="codecrow_ai"
  DB_USER="codecrow_user"
  DB_PASS=""

  if [[ -f "$deployment_dir/.env" ]]; then
    DB_NAME=$(grep -E '^POSTGRES_DB=' "$deployment_dir/.env" | cut -d= -f2 || echo "codecrow_ai")
    DB_USER=$(grep -E '^POSTGRES_USER=' "$deployment_dir/.env" | cut -d= -f2 || echo "codecrow_user")
    DB_PASS=$(grep -E '^POSTGRES_PASSWORD=' "$deployment_dir/.env" | cut -d= -f2 || true)
    DB_NAME="${DB_NAME:-codecrow_ai}"
    DB_USER="${DB_USER:-codecrow_user}"
  fi

  if [[ -z "$DB_PASS" && "$mode" == "prompt" ]]; then
    read -rsp "$(echo -e "${BOLD}PostgreSQL password for $DB_USER: ${NC}")" DB_PASS
    echo
  fi
}

# ─── PostgreSQL execution via .pgpass ───────────────────────────────────────

# Runs psql inside the container using a temporary .pgpass file instead of
# exposing the password via PGPASSWORD in the process environment.
# Arguments:
#   $1 — container name
#   $2 — database user
#   $3 — database password
#   $4 — database name
#   $5…$N — additional psql arguments (e.g., -c "SELECT 1")
pg_exec() {
  local container="$1" user="$2" pass="$3" dbname="$4"
  shift 4

  # Create .pgpass inside container, run psql, then clean up
  docker exec "$container" bash -c "
    echo 'localhost:5432:*:${user}:${pass}' > /tmp/.pgpass_codecrow && \
    chmod 600 /tmp/.pgpass_codecrow && \
    PGPASSFILE=/tmp/.pgpass_codecrow psql -h localhost -U '${user}' -d '${dbname}' \"\$@\" ; \
    RC=\$? ; rm -f /tmp/.pgpass_codecrow ; exit \$RC
  " -- "$@"
}

# Runs psql with piped stdin (for restoring dumps)
pg_exec_stdin() {
  local container="$1" user="$2" pass="$3" dbname="$4"
  shift 4

  docker exec -i "$container" bash -c "
    echo 'localhost:5432:*:${user}:${pass}' > /tmp/.pgpass_codecrow && \
    chmod 600 /tmp/.pgpass_codecrow && \
    PGPASSFILE=/tmp/.pgpass_codecrow psql -h localhost -U '${user}' -d '${dbname}' \"\$@\" ; \
    RC=\$? ; rm -f /tmp/.pgpass_codecrow ; exit \$RC
  " -- "$@"
}

# ─── Qdrant API key reading ──────────────────────────────────────────────────

# Reads QDRANT_API_KEY from deployment/.env
# Sets: QDRANT_API_KEY
# Arguments:
#   $1 — path to the deployment directory (must contain .env)
read_qdrant_api_key() {
  local deployment_dir="$1"
  QDRANT_API_KEY=""

  if [[ -f "$deployment_dir/.env" ]]; then
    QDRANT_API_KEY=$(grep -E '^QDRANT_API_KEY=' "$deployment_dir/.env" | cut -d= -f2 || true)
  fi

  if [[ -z "$QDRANT_API_KEY" ]]; then
    warn "QDRANT_API_KEY not found in $deployment_dir/.env — Qdrant requests will be unauthenticated."
  fi
}

# Helper: returns curl auth header args for Qdrant (empty if no key set)
qdrant_auth_header() {
  if [[ -n "${QDRANT_API_KEY:-}" ]]; then
    echo "-H" "api-key: $QDRANT_API_KEY"
  fi
}

# ─── Redis path resolution ──────────────────────────────────────────────────

# Resolves the actual RDB dump path inside the Redis container by querying
# Redis CONFIG instead of assuming /data/dump.rdb.
# Sets: REDIS_INTERNAL_PATH
resolve_redis_rdb_path() {
  local container="$1"
  local redis_dir redis_dbfile

  redis_dir=$(docker exec "$container" redis-cli CONFIG GET dir 2>/dev/null | tail -1)
  redis_dbfile=$(docker exec "$container" redis-cli CONFIG GET dbfilename 2>/dev/null | tail -1)

  redis_dir="${redis_dir:-/data}"
  redis_dbfile="${redis_dbfile:-dump.rdb}"

  REDIS_INTERNAL_PATH="${redis_dir}/${redis_dbfile}"
}
