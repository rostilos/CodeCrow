#!/usr/bin/env bash
# Run once with sudo from a checkout; Docker/Compose and a deploy user must exist.
# Usage: sudo bash deployment/ci/server-init-stage.sh DEPLOY_USER [/opt/codecrow-stage]
set -euo pipefail
umask 077

DEPLOY_USER="${1:?Usage: server-init-stage.sh DEPLOY_USER [STAGE_DIRECTORY]}"
DEPLOY_DIR="${2:-/opt/codecrow-stage}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ "$DEPLOY_DIR" != /* || "$DEPLOY_DIR" == / || "$DEPLOY_DIR" == /opt/codecrow || "$DEPLOY_DIR" == /opt/codecrow/ ]]; then
  echo "ERROR: Choose an absolute staging directory separate from /opt/codecrow." >&2
  exit 1
fi
mkdir -p "$DEPLOY_DIR"/backups "$DEPLOY_DIR"/config/{java-shared/github-private-key,inference-orchestrator,rag-pipeline}
if [[ ! -f "$DEPLOY_DIR/.env" ]]; then
  cp "$SCRIPT_DIR/../config/stage/.env.sample" "$DEPLOY_DIR/.env"
fi
chown -R "$DEPLOY_USER:$(id -gn "$DEPLOY_USER")" "$DEPLOY_DIR"
chmod 700 "$DEPLOY_DIR" "$DEPLOY_DIR/config" "$DEPLOY_DIR/backups"
echo "Staging directory prepared: $DEPLOY_DIR"
echo "Grant $DEPLOY_USER Docker access, configure STAGE_* secrets, and prepare a compatible database schema before deploying."
echo "See /docs/developer/configuration#staging-deployment."
