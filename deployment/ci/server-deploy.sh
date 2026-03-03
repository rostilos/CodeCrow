#!/bin/bash
###############################################################################
# server-deploy.sh — Runs ON THE LIVE SERVER via SSH from GitHub Actions.
#
# Expects docker-compose.prod.yml to be updated to point to GHCR.
#
# Flow:
#   1. Pre-flight config checks
#   2. Backup PostgreSQL database
#   3. Pull new Docker images from Registry
#   4. Stop existing services
#   5. Start services (--no-build, --wait for healthchecks)
#   6. Verify health
#   7. Cleanup old backups
#
# Usage:
#   GITHUB_REPOSITORY_OWNER=username server-deploy.sh
###############################################################################
set -euo pipefail

DEPLOY_DIR="/opt/codecrow"
CONFIG_DIR="$DEPLOY_DIR/config"
BACKUP_DIR="$DEPLOY_DIR/backups"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.prod.yml"

# For GHCR pulling
export GITHUB_REPOSITORY_OWNER="${GITHUB_REPOSITORY_OWNER:-codecrow}"
export GITHUB_REPOSITORY_OWNER=$(echo "$GITHUB_REPOSITORY_OWNER" | tr '[:upper:]' '[:lower:]')

echo "=========================================="
echo "  CodeCrow Server Deployment"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="

# ── Pre-flight checks ─────────────────────────────────────────────────────

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "ERROR: docker-compose.prod.yml not found: $COMPOSE_FILE"
  exit 1
fi

# Check config files exist
MISSING_CONFIGS=0
for cfg in \
  "$CONFIG_DIR/java-shared/application.properties" \
  "$CONFIG_DIR/java-shared/newrelic-web-server.yml" \
  "$CONFIG_DIR/java-shared/newrelic-pipeline-agent.yml" \
  "$CONFIG_DIR/inference-orchestrator/.env" \
  "$CONFIG_DIR/rag-pipeline/.env"; do
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

# ── 2. Pull Docker images ─────────────────────────────────────────────────
echo "--- 2. Pulling Docker images from registry ---"
docker compose -f "$COMPOSE_FILE" pull
echo "  ✓ Images pulled"

# ── 3. Stop existing services ─────────────────────────────────────────────
echo "--- 3. Stopping existing services ---"
docker compose -f docker-compose.prod.yml down --remove-orphans 2>/dev/null || true
echo "  ✓ Services stopped"

# ── 4. Start services ─────────────────────────────────────────────────────
echo "--- 4. Starting services ---"
if ! docker compose -f docker-compose.prod.yml up -d --no-build --wait; then
  echo ""
  echo "  ✗ DEPLOYMENT FAILED — services did not become healthy!"
  echo ""
  echo "  Failing service logs:"
  docker compose -f docker-compose.prod.yml ps --format json 2>/dev/null \
    | grep -v '"Health":"healthy"' | head -5 || true
  echo ""
  echo "  Run manually to inspect:"
  echo "    cd $DEPLOY_DIR && docker compose -f docker-compose.prod.yml logs"
  echo ""
  if [ -f "$BACKUP_FILE" ]; then
    echo "  DB backup available for restore: $BACKUP_FILE"
    echo "    Restore: gunzip -c $BACKUP_FILE | docker compose -f docker-compose.prod.yml exec -T postgres psql -U $DB_USER $DB_NAME"
  fi
  exit 1
fi
echo "  ✓ Services started and healthy"

# ── 5. Verify health ──────────────────────────────────────────────────────
echo "--- 5. Service status ---"
docker compose -f docker-compose.prod.yml ps

# ── 6. Cleanup old backups ────────────────────────────────────────────────
echo "--- 6. Cleaning up old backups ---"

# Cleanup old DB backups (keep last 10)
cd "$BACKUP_DIR"
ls -1t *.sql.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
echo "  ✓ Old backups pruned"

# ── 7. Prune dangling images ─────────────────────────────────────────────
echo "--- 7. Pruning old Docker images ---"
docker image prune -f --filter "until=24h" 2>/dev/null || true
echo "  ✓ Pruned"

echo ""
echo "=========================================="
echo "  Deployment complete!"
echo "=========================================="
