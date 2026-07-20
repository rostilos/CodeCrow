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
#   4. Cancel work from the old unversioned review runtime
#   5. Recreate selected services, or the full stack when all services are selected
#   6. Verify health and clean up old backups
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
DEPLOY_ENV_FILE="$DEPLOY_DIR/.env"
source "$SCRIPT_DIR/service-selection.sh"

# For GHCR pulling
export GITHUB_REPOSITORY_OWNER="${GITHUB_REPOSITORY_OWNER:-codecrow}"
export GITHUB_REPOSITORY_OWNER=$(echo "$GITHUB_REPOSITORY_OWNER" | tr '[:upper:]' '[:lower:]')
export CODECROW_IMAGE_TAG="${CODECROW_IMAGE_TAG:-}"
if [[ ! "$CODECROW_IMAGE_TAG" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
  echo "ERROR: CODECROW_IMAGE_TAG must identify the tested source commit." >&2
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

# Check Compose and service config files before changing any running service.
MISSING_CONFIGS=0
REQUIRED_CONFIGS=("$DEPLOY_ENV_FILE")

if codecrow_includes_service "web-server" "${SELECTED_SERVICES[@]}"; then
  REQUIRED_CONFIGS+=(
    "$CONFIG_DIR/java-shared/application.properties"
    "$CONFIG_DIR/java-shared/github-private-key/github-app-private-key.pem"
    "$CONFIG_DIR/java-shared/newrelic-web-server.yml"
  )
fi

if codecrow_includes_service "pipeline-agent" "${SELECTED_SERVICES[@]}"; then
  REQUIRED_CONFIGS+=(
    "$CONFIG_DIR/java-shared/application.properties"
    "$CONFIG_DIR/java-shared/github-private-key/github-app-private-key.pem"
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
  if [ ! -f "$cfg" ] || [ ! -s "$cfg" ] || [ ! -r "$cfg" ]; then
    echo "ERROR: Config file must exist, be non-empty, and be readable: $cfg"
    MISSING_CONFIGS=1
  fi
done
if [ "$MISSING_CONFIGS" -eq 1 ]; then
  echo "Aborting deployment due to missing config files."
  exit 1
fi

cd "$DEPLOY_DIR"
COMPOSE=(docker compose --env-file "$DEPLOY_ENV_FILE" -f "$COMPOSE_FILE")
if ! "${COMPOSE[@]}" config --quiet; then
  echo "ERROR: Docker Compose configuration is invalid. No services were changed." >&2
  exit 1
fi
echo "  ✓ Root .env and selected service configuration validated"

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
  if [ -f "$DEPLOY_ENV_FILE" ]; then
    _val=$(grep -m1 '^POSTGRES_DB=' "$DEPLOY_ENV_FILE" | cut -d'=' -f2- | tr -d '[:space:]') && [ -n "$_val" ] && DB_NAME="$_val"
    _val=$(grep -m1 '^POSTGRES_USER=' "$DEPLOY_ENV_FILE" | cut -d'=' -f2- | tr -d '[:space:]') && [ -n "$_val" ] && DB_USER="$_val"
    unset _val
  fi

  # Starting only the data service makes the backup reliable even when the
  # application stack was intentionally stopped before deployment.
  "${COMPOSE[@]}" up -d --no-build --wait postgres
  "${COMPOSE[@]}" exec -T postgres \
    pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"
  BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
  echo "  ✓ Database backed up: $BACKUP_FILE ($BACKUP_SIZE)"
else
  echo "--- 1. Skipping PostgreSQL backup (web-server not selected) ---"
fi

# ── 2. Pull Docker images ─────────────────────────────────────────────────
echo "--- 2. Pulling Docker images from registry ---"
if codecrow_is_full_service_set "${SELECTED_SERVICES[@]}"; then
  "${COMPOSE[@]}" pull
else
  "${COMPOSE[@]}" pull "${SELECTED_SERVICES[@]}"
fi
echo "  ✓ Images pulled"

if codecrow_includes_service "inference-orchestrator" "${SELECTED_SERVICES[@]}"; then
  echo "--- 3. Replacing the unversioned backend runtime ---"
  echo "  Stopping all backend queue producers and consumers..."
  "${COMPOSE[@]}" stop web-server pipeline-agent
  "${COMPOSE[@]}" stop inference-orchestrator rag-pipeline

  # Old queue payloads must never cross a release boundary. Starting Redis is
  # safe here and also handles a first deploy with a persisted Redis volume.
  "${COMPOSE[@]}" up -d --no-build --wait redis
  REVIEW_QUEUE_DEPTH=$("${COMPOSE[@]}" exec -T redis \
    redis-cli --raw -n 1 LLEN codecrow:analysis:jobs)
  COMMAND_QUEUE_DEPTH=$("${COMPOSE[@]}" exec -T redis \
    redis-cli --raw -n 1 LLEN codecrow:queue:commands)
  RAG_QUEUE_DEPTH=$("${COMPOSE[@]}" exec -T redis \
    redis-cli --raw -n 1 LLEN codecrow:queue:rag)
  "${COMPOSE[@]}" exec -T redis \
    redis-cli --raw -n 1 DEL \
      codecrow:analysis:jobs codecrow:queue:commands codecrow:queue:rag >/dev/null
  DELETED_EVENT_STREAMS=$("${COMPOSE[@]}" exec -T redis \
    redis-cli --raw -n 1 EVAL \
      "local cursor = '0'
       local deleted = 0
       repeat
         local page = redis.call('SCAN', cursor, 'MATCH',
           'codecrow:analysis:events:*', 'COUNT', 1000)
         cursor = page[1]
         if #page[2] > 0 then
           deleted = deleted + redis.call('DEL', unpack(page[2]))
         end
       until cursor == '0'
       return deleted" 0)
  CANCELLED_JOBS=$((REVIEW_QUEUE_DEPTH + COMMAND_QUEUE_DEPTH + RAG_QUEUE_DEPTH))
  echo "  Cancelled queued runtime jobs: $CANCELLED_JOBS"
  echo "  Removed runtime event streams: $DELETED_EVENT_STREAMS"

  # All users of these shared volumes are stopped, so no process can race the
  # release-boundary cleanup. RAG queue payloads contain paths under /tmp.
  "${COMPOSE[@]}" run --rm --no-deps --entrypoint sh \
    fix-permissions -c \
      'rm -rf /agentic/* /agentic/.[!.]* /agentic/..?* /tmp/codecrow-*'

  echo "  ✓ Old backend runtime stopped; staged workspaces cleared"
fi

# ── 3. Start selected services ────────────────────────────────────────────
if codecrow_is_full_service_set "${SELECTED_SERVICES[@]}"; then
  echo "--- 3. Stopping existing services ---"
  "${COMPOSE[@]}" down --remove-orphans 2>/dev/null || true
  echo "  ✓ Services stopped"

  echo "--- 4. Starting full stack ---"
  UP_ARGS=("${COMPOSE[@]}" up -d --no-build --wait)
else
  echo "--- 3. Recreating selected services ---"
  UP_ARGS=("${COMPOSE[@]}" up -d --no-build --wait --force-recreate "${SELECTED_SERVICES[@]}")
fi

if ! "${UP_ARGS[@]}"; then
  echo ""
  echo "  ✗ DEPLOYMENT FAILED — services did not become healthy!"
  echo ""
  echo "  Failing service logs:"
  "${COMPOSE[@]}" ps --format json 2>/dev/null \
    | grep -v '"Health":"healthy"' | head -5 || true
  echo ""
  echo "  Run manually to inspect:"
  echo "    cd $DEPLOY_DIR && GITHUB_REPOSITORY_OWNER=$GITHUB_REPOSITORY_OWNER CODECROW_IMAGE_TAG=$CODECROW_IMAGE_TAG docker compose --env-file .env -f docker-compose.prod.yml logs ${SELECTED_SERVICES[*]}"
  echo ""
  if [ -n "$BACKUP_FILE" ] && [ -f "$BACKUP_FILE" ]; then
    echo "  DB backup available for restore: $BACKUP_FILE"
    echo "    Restore: gunzip -c $BACKUP_FILE | GITHUB_REPOSITORY_OWNER=$GITHUB_REPOSITORY_OWNER CODECROW_IMAGE_TAG=$CODECROW_IMAGE_TAG docker compose --env-file .env -f docker-compose.prod.yml exec -T postgres psql -U $DB_USER $DB_NAME"
  fi
  exit 1
fi
echo "  ✓ Selected services started and healthy"

# Confirm that the selected Python services can read the runtime configuration
# that is bind-mounted over /app/.env. Do not print its contents.
if codecrow_includes_service "inference-orchestrator" "${SELECTED_SERVICES[@]}"; then
  "${COMPOSE[@]}" exec -T inference-orchestrator sh -c 'test -r /app/.env && test -s /app/.env'
  "${COMPOSE[@]}" exec -T rag-pipeline sh -c 'test -r /app/.env && test -s /app/.env'
  echo "  ✓ Python service .env mounts are readable inside both containers"
fi

# ── 5. Verify health ──────────────────────────────────────────────────────
echo "--- 5. Service status ---"
if codecrow_is_full_service_set "${SELECTED_SERVICES[@]}"; then
  "${COMPOSE[@]}" ps
else
  "${COMPOSE[@]}" ps "${SELECTED_SERVICES[@]}"
fi

# ── 6. Cleanup old backups ────────────────────────────────────────────────
echo "--- 6. Cleaning up old backups ---"

# Cleanup old DB backups (keep last 10). The timestamped names sort oldest
# first, and nullglob keeps a first deploy with no backups successful.
mkdir -p "$BACKUP_DIR"
cd "$BACKUP_DIR"
shopt -s nullglob
BACKUPS=(codecrow_pre_deploy_*.sql.gz)
if [ "${#BACKUPS[@]}" -gt 10 ]; then
  PRUNE_COUNT=$((${#BACKUPS[@]} - 10))
  for ((i = 0; i < PRUNE_COUNT; i++)); do
    rm -f -- "${BACKUPS[$i]}"
  done
fi
echo "  ✓ Old backups pruned"

# ── 7. Prune dangling images ─────────────────────────────────────────────
echo "--- 7. Pruning old Docker images ---"
docker image prune -f --filter "until=24h" 2>/dev/null || true
echo "  ✓ Pruned"

echo ""
echo "=========================================="
echo "  Deployment complete for: $SELECTED_SERVICES_LABEL"
echo "=========================================="
