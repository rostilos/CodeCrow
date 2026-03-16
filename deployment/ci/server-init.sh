#!/bin/bash
###############################################################################
# server-init.sh — Run ONCE on the live server to prepare directory structure.
#
# Creates /opt/codecrow/ with proper layout for CI/CD deployments.
# Run as root or with sudo.
#
# Usage:
#   sudo bash server-init.sh
###############################################################################
set -euo pipefail

DEPLOY_USER="${1:-www-data}"
DEPLOY_DIR="/opt/codecrow"

echo "=========================================="
echo "  CodeCrow Server Initialization"
echo "=========================================="

# ── 1. Install required packages ───────────────────────────────────────────
echo "--- 1. Installing required packages ---"
if ! command -v zstd &>/dev/null; then
  apt-get update -qq && apt-get install -y -qq zstd
  echo "  ✓ zstd installed"
else
  echo "  ○ zstd already installed"
fi

# ── 2. Create directory structure ──────────────────────────────────────────
echo "--- 2. Creating directory structure ---"
mkdir -p "$DEPLOY_DIR"/{releases,backups,config/{java-shared/github-private-key,inference-orchestrator,rag-pipeline,web-frontend}}

# ── 3. Set ownership ─────────────────────────────────────────────────────
echo "--- 3. Setting ownership to $DEPLOY_USER ---"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$DEPLOY_DIR"

# ── 4. Create sample config files ────────────────────────────────────────
echo "--- 4. Creating sample config placeholders ---"

if [ ! -f "$DEPLOY_DIR/config/java-shared/application.properties" ]; then
  cat > "$DEPLOY_DIR/config/java-shared/application.properties" <<'SAMPLE'
# ============================================================================
# COPY YOUR application.properties HERE
# See deployment/config/java-shared/application.properties.sample for reference
# ============================================================================
SAMPLE
  echo "  ✓ Created placeholder: java-shared/application.properties"
  echo "    → EDIT THIS FILE with your actual values before deploying!"
else
  echo "  ○ java-shared/application.properties already exists (skipped)"
fi

# New Relic agent configs (one per Java service — different app_name)
for nr_svc in web-server pipeline-agent; do
  NR_FILE="$DEPLOY_DIR/config/java-shared/newrelic-${nr_svc}.yml"
  if [ ! -f "$NR_FILE" ]; then
    cat > "$NR_FILE" <<SAMPLE
# ============================================================================
# New Relic config for ${nr_svc}
# Download a template:  curl -O https://download.newrelic.com/newrelic/java-agent/newrelic-agent/current/newrelic-java.zip
# Then edit with your license_key and app_name
# ============================================================================
SAMPLE
    echo "  ✓ Created placeholder: java-shared/newrelic-${nr_svc}.yml"
    echo "    → EDIT THIS FILE with your New Relic license key and app name!"
  else
    echo "  ○ java-shared/newrelic-${nr_svc}.yml already exists (skipped)"
  fi
done

for svc in inference-orchestrator rag-pipeline; do
  if [ ! -f "$DEPLOY_DIR/config/$svc/.env" ]; then
    cat > "$DEPLOY_DIR/config/$svc/.env" <<SAMPLE
# ============================================================================
# COPY YOUR .env HERE
# See deployment/config/$svc/.env.sample for reference
# ============================================================================
SAMPLE
    echo "  ✓ Created placeholder: $svc/.env"
    echo "    → EDIT THIS FILE with your actual values before deploying!"
  else
    echo "  ○ $svc/.env already exists (skipped)"
  fi
done

# New Relic config for inference-orchestrator (Python agent)
NR_INI="$DEPLOY_DIR/config/inference-orchestrator/newrelic.ini"
if [ ! -f "$NR_INI" ]; then
  cat > "$NR_INI" <<'SAMPLE'
# ============================================================================
# New Relic Python agent config for inference-orchestrator
# See https://docs.newrelic.com/docs/apm/agents/python-agent/configuration/python-agent-configuration/
# Copy your newrelic.ini here with your license_key and app_name
# ============================================================================
[newrelic]
license_key = REPLACE_WITH_YOUR_LICENSE_KEY
app_name = CodeCrow Inference Orchestrator
monitor_mode = true
log_level = info
SAMPLE
  echo "  ✓ Created placeholder: inference-orchestrator/newrelic.ini"
  echo "    → EDIT THIS FILE with your New Relic license key!"
else
  echo "  ○ inference-orchestrator/newrelic.ini already exists (skipped)"
fi

# Docker Compose .env (DB creds, internal secrets — never committed to git)
ENV_FILE="$DEPLOY_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'SAMPLE'
# ============================================================================
# Docker Compose environment variables
# These are interpolated into docker-compose.prod.yml at runtime.
# EDIT with your actual production values!
# ============================================================================

# PostgreSQL
POSTGRES_DB=codecrow_ai
POSTGRES_USER=codecrow_user
POSTGRES_PASSWORD=CHANGE_ME_TO_YOUR_ACTUAL_DB_PASSWORD

# pgAdmin (bind to 127.0.0.1 only; expose externally only via authenticated tunnel)
PGADMIN_DEFAULT_EMAIL=pgadmin@localhost
PGADMIN_DEFAULT_PASSWORD=CHANGE_ME_TO_A_STRONG_PGADMIN_PASSWORD

# Internal API secret (service-to-service auth)
INTERNAL_API_SECRET=CHANGE_ME_GENERATE_WITH_openssl_rand_hex_32
SAMPLE
  echo "  ✓ Created placeholder: .env"
  echo "    → EDIT THIS FILE with your actual DB password and internal secret!"
else
  echo "  ○ .env already exists (skipped)"
fi

# ── 5. Print summary ─────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo "  Server initialized! Directory layout:"
echo "=========================================="
echo ""
echo "  $DEPLOY_DIR/"
echo "  ├── docker-compose.prod.yml   ← copy from repo"
echo "  ├── server-deploy.sh          ← copy from repo"
echo "  ├── releases/"
echo "  │   └── codecrow-images.tar.zst  ← uploaded by CI"
echo "  └── config/"
echo "      ├── java-shared/"
echo "      │   ├── application.properties       ← YOUR secrets"
echo "      │   ├── newrelic-web-server.yml       ← YOUR New Relic config (web-server)"
echo "      │   ├── newrelic-pipeline-agent.yml   ← YOUR New Relic config (pipeline-agent)"
echo "      │   └── github-private-key/"
echo "      │       └── *.pem               ← YOUR GitHub App key"
echo "      ├── inference-orchestrator/"
echo "      │   ├── .env                    ← YOUR secrets"
echo "      │   └── newrelic.ini             ← YOUR New Relic Python agent config"
echo "      └── rag-pipeline/"
echo "          └── .env                    ← YOUR secrets"
echo ""
echo "  NEXT STEPS:"
echo "  1. Edit all config files above with your actual values"
echo "  2. Copy your GitHub App .pem key to config/java-shared/github-private-key/"
echo "  3. Copy docker-compose.prod.yml and server-deploy.sh to $DEPLOY_DIR/"
echo "  4. Set up GitHub Actions secrets (see CI-CD-SETUP.md)"
echo ""
