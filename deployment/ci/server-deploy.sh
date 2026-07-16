#!/bin/bash
###############################################################################
# server-deploy.sh — Runs ON THE LIVE SERVER via SSH from GitHub Actions.
#
# Expects docker-compose.prod.yml to be updated to point to GHCR.
#
# Flow:
#   1. Pre-flight config checks
#   2. Backup PostgreSQL database when web-server is deployed
#   3. Pull selected Docker images from Registry
#   4. Recreate selected services, or the full stack when all services are selected
#   5. Verify health
#   6. Cleanup old backups
#
# Usage:
#   GITHUB_REPOSITORY_OWNER=username CODECROW_IMAGE_TAG=<git-sha> CODECROW_DEPLOY_SERVICES=web-frontend server-deploy.sh
###############################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_DIR="/opt/codecrow"
CONFIG_DIR="$DEPLOY_DIR/config"
BACKUP_DIR="$DEPLOY_DIR/backups"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.prod.yml"
source "$SCRIPT_DIR/service-selection.sh"

# For GHCR pulling
export GITHUB_REPOSITORY_OWNER="${GITHUB_REPOSITORY_OWNER:-codecrow}"
export GITHUB_REPOSITORY_OWNER=$(echo "$GITHUB_REPOSITORY_OWNER" | tr '[:upper:]' '[:lower:]')
export CODECROW_IMAGE_TAG="${CODECROW_IMAGE_TAG:-}"
if [[ ! "$CODECROW_IMAGE_TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "ERROR: CODECROW_IMAGE_TAG must identify the tested immutable image set." >&2
  exit 1
fi
REQUESTED_SERVICES="${CODECROW_DEPLOY_SERVICES:-all}"
codecrow_resolve_services "$REQUESTED_SERVICES"
SELECTED_SERVICES=("${CODECROW_RESOLVED_SERVICES[@]}")
SELECTED_SERVICES_LABEL="$(codecrow_join_services ", " "${SELECTED_SERVICES[@]}")"

echo "=========================================="
echo "  CodeCrow Server Deployment"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="
echo "Selected services: $SELECTED_SERVICES_LABEL"
echo "Image tag: $CODECROW_IMAGE_TAG"

# ── Pre-flight checks ─────────────────────────────────────────────────────

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "ERROR: docker-compose.prod.yml not found: $COMPOSE_FILE"
  exit 1
fi

# Check config files exist for selected services.
MISSING_CONFIGS=0
REQUIRED_CONFIGS=()

if codecrow_includes_service "web-server" "${SELECTED_SERVICES[@]}"; then
  REQUIRED_CONFIGS+=(
    "$CONFIG_DIR/java-shared/application.properties"
    "$CONFIG_DIR/java-shared/newrelic-web-server.yml"
  )
fi

if codecrow_includes_service "pipeline-agent" "${SELECTED_SERVICES[@]}"; then
  REQUIRED_CONFIGS+=(
    "$CONFIG_DIR/java-shared/application.properties"
    "$CONFIG_DIR/java-shared/newrelic-pipeline-agent.yml"
  )
fi

if codecrow_includes_service "inference-orchestrator" "${SELECTED_SERVICES[@]}"; then
  REQUIRED_CONFIGS+=(
    "$CONFIG_DIR/inference-orchestrator/.env"
    "$CONFIG_DIR/inference-orchestrator/newrelic.ini"
  )
fi

if codecrow_includes_service "rag-pipeline" "${SELECTED_SERVICES[@]}"; then
  REQUIRED_CONFIGS+=("$CONFIG_DIR/rag-pipeline/.env")
fi

for cfg in "${REQUIRED_CONFIGS[@]}"; do
  if [ ! -f "$cfg" ]; then
    echo "ERROR: Missing config file: $cfg"
    MISSING_CONFIGS=1
  fi
done
if [ "$MISSING_CONFIGS" -eq 1 ]; then
  echo "Aborting deployment due to missing config files."
  exit 1
fi

cd "$DEPLOY_DIR"

# ── 1. Backup PostgreSQL database ─────────────────────────────────────────
BACKUP_FILE=""
if codecrow_includes_service "web-server" "${SELECTED_SERVICES[@]}" || [ "${CODECROW_FORCE_DB_BACKUP:-false}" = "true" ]; then
  echo "--- 1. Backing up PostgreSQL database ---"
  mkdir -p "$BACKUP_DIR"
  BACKUP_TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
  BACKUP_FILE="$BACKUP_DIR/codecrow_pre_deploy_${BACKUP_TIMESTAMP}.sql.gz"

  # Read DB credentials from .env (grep instead of source to avoid
  # unbound-variable errors from passwords containing $ characters)
  DB_NAME="codecrow_ai"
  DB_USER="codecrow_user"
  if [ -f "$DEPLOY_DIR/.env" ]; then
    _val=$(grep -m1 '^POSTGRES_DB=' "$DEPLOY_DIR/.env" | cut -d'=' -f2- | tr -d '[:space:]') && [ -n "$_val" ] && DB_NAME="$_val"
    _val=$(grep -m1 '^POSTGRES_USER=' "$DEPLOY_DIR/.env" | cut -d'=' -f2- | tr -d '[:space:]') && [ -n "$_val" ] && DB_USER="$_val"
    unset _val
  fi

  if docker compose -f "$COMPOSE_FILE" ps --status running 2>/dev/null | grep -q postgres; then
    docker compose -f "$COMPOSE_FILE" exec -T postgres \
      pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "  ✓ Database backed up: $BACKUP_FILE ($BACKUP_SIZE)"
  else
    echo "  ⚠ PostgreSQL not running — skipping backup (first deploy?)"
  fi
else
  echo "--- 1. Skipping PostgreSQL backup (web-server not selected) ---"
fi

# ── 2. Pull Docker images ─────────────────────────────────────────────────
echo "--- 2. Pulling Docker images from registry ---"
if codecrow_is_full_service_set "${SELECTED_SERVICES[@]}"; then
  docker compose -f "$COMPOSE_FILE" pull
else
  docker compose -f "$COMPOSE_FILE" pull "${SELECTED_SERVICES[@]}"
fi
echo "  ✓ Images pulled"

# ── 3. Start selected services ────────────────────────────────────────────
if codecrow_is_full_service_set "${SELECTED_SERVICES[@]}"; then
  echo "--- 3. Stopping existing services ---"
  docker compose -f "$COMPOSE_FILE" down --remove-orphans 2>/dev/null || true
  echo "  ✓ Services stopped"

  echo "--- 4. Starting full stack ---"
  UP_ARGS=(docker compose -f "$COMPOSE_FILE" up -d --no-build --wait)
else
  echo "--- 3. Recreating selected services ---"
  UP_ARGS=(docker compose -f "$COMPOSE_FILE" up -d --no-build --wait "${SELECTED_SERVICES[@]}")
fi

if ! "${UP_ARGS[@]}"; then
  echo ""
  echo "  ✗ DEPLOYMENT FAILED — services did not become healthy!"
  echo ""
  echo "  Failing service logs:"
  docker compose -f "$COMPOSE_FILE" ps --format json 2>/dev/null \
    | grep -v '"Health":"healthy"' | head -5 || true
  echo ""
  echo "  Run manually to inspect:"
  echo "    cd $DEPLOY_DIR && docker compose -f docker-compose.prod.yml logs ${SELECTED_SERVICES[*]}"
  echo ""
  if [ -n "$BACKUP_FILE" ] && [ -f "$BACKUP_FILE" ]; then
    echo "  DB backup available for restore: $BACKUP_FILE"
    echo "    Restore: gunzip -c $BACKUP_FILE | docker compose -f docker-compose.prod.yml exec -T postgres psql -U $DB_USER $DB_NAME"
  fi
  exit 1
fi
echo "  ✓ Selected services started and healthy"

# ── 5. Verify health ──────────────────────────────────────────────────────
echo "--- 5. Service status ---"
if codecrow_is_full_service_set "${SELECTED_SERVICES[@]}"; then
  docker compose -f "$COMPOSE_FILE" ps
else
  docker compose -f "$COMPOSE_FILE" ps "${SELECTED_SERVICES[@]}"
fi

# ── 6. Cleanup old backups ────────────────────────────────────────────────
echo "--- 6. Cleaning up old backups ---"

# Cleanup old DB backups (keep last 10)
mkdir -p "$BACKUP_DIR"
cd "$BACKUP_DIR"
ls -1t *.sql.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
echo "  ✓ Old backups pruned"

# ── 7. Prune dangling images ─────────────────────────────────────────────
echo "--- 7. Pruning old Docker images ---"
docker image prune -f --filter "until=24h" 2>/dev/null || true
echo "  ✓ Pruned"

echo ""
echo "=========================================="
echo "  Deployment complete for: $SELECTED_SERVICES_LABEL"
echo "=========================================="
