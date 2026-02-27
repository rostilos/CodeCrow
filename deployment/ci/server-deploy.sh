#!/bin/bash
###############################################################################
# server-deploy.sh — Runs ON THE LIVE SERVER via SSH from GitHub Actions.
#
# Expects the tarball to already be uploaded to /opt/codecrow/releases/
#
# Flow:
#   1. Pre-flight config checks
#   2. Backup PostgreSQL database
#   3. Load new Docker images from tarball
#   4. Stop existing services
#   5. Start services (--no-build, --wait for healthchecks)
#   6. Verify health
#   7. Cleanup old releases & backups
#
# Usage:
#   server-deploy.sh [tarball-name]
#
# Default tarball: codecrow-images.tar.gz
###############################################################################
set -euo pipefail

DEPLOY_DIR="/opt/codecrow"
RELEASES_DIR="$DEPLOY_DIR/releases"
CONFIG_DIR="$DEPLOY_DIR/config"
BACKUP_DIR="$DEPLOY_DIR/backups"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.prod.yml"
TARBALL_NAME="${1:-codecrow-images.tar.gz}"
TARBALL_PATH="$RELEASES_DIR/$TARBALL_NAME"

echo "=========================================="
echo "  CodeCrow Server Deployment"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="

# ── Pre-flight checks ─────────────────────────────────────────────────────
if [ ! -f "$TARBALL_PATH" ]; then
  echo "ERROR: Tarball not found: $TARBALL_PATH"
  exit 1
fi

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

# Read DB credentials from .env
DB_NAME="codecrow_ai"
DB_USER="codecrow_user"
if [ -f "$DEPLOY_DIR/.env" ]; then
  # shellcheck disable=SC1091
  set -a; source "$DEPLOY_DIR/.env"; set +a
  DB_NAME="${POSTGRES_DB:-codecrow_ai}"
  DB_USER="${POSTGRES_USER:-codecrow_user}"
fi

if docker compose -f "$COMPOSE_FILE" ps --status running 2>/dev/null | grep -q postgres; then
  docker compose -f "$COMPOSE_FILE" exec -T postgres \
    pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"
  BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
  echo "  ✓ Database backed up: $BACKUP_FILE ($BACKUP_SIZE)"
else
  echo "  ⚠ PostgreSQL not running — skipping backup (first deploy?)"
fi

# ── 2. Load Docker images ─────────────────────────────────────────────────
echo "--- 2. Loading Docker images from tarball ---"
docker load -i "$TARBALL_PATH"
echo "  ✓ Images loaded"

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

# ── 6. Cleanup old releases (keep last 5) ─────────────────────────────────
echo "--- 6. Cleaning up old releases and backups ---"
cd "$RELEASES_DIR"
ls -1t *.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f
REMAINING=$(ls -1 *.tar.gz 2>/dev/null | wc -l)
echo "  ✓ Keeping $REMAINING release(s)"

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
