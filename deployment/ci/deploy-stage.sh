#!/usr/bin/env bash
# Runs on the CI runner. Never reads unprefixed production deployment secrets.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/service-selection.sh"
codecrow_resolve_services "${CODECROW_DEPLOY_SERVICES:-all}"

for key in STAGE_DEPLOY_SSH_KEY STAGE_DEPLOY_HOST STAGE_DEPLOY_USER STAGE_DEPLOY_HOST_FINGERPRINT STAGE_ENV_DEPLOYMENT; do
  if [[ -z "${!key:-}" ]]; then
    echo "ERROR: $key is required for staging deployment." >&2
    exit 1
  fi
done

DEPLOY_PATH="${STAGE_DEPLOY_PATH:-/opt/codecrow-stage}"
DEPLOY_PORT="${STAGE_DEPLOY_PORT:-22}"
# The staging workflow must never upload its configuration over the live directory.
if [[ "$DEPLOY_PATH" != /* || "$DEPLOY_PATH" == / || "$DEPLOY_PATH" == /opt/codecrow || "$DEPLOY_PATH" == /opt/codecrow/ ]]; then
  echo "ERROR: STAGE_DEPLOY_PATH must be an absolute staging directory, separate from /opt/codecrow." >&2
  exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
BUNDLE="$WORK_DIR/bundle"
mkdir -p "$BUNDLE/config/java-shared/github-private-key" "$BUNDLE/config/inference-orchestrator" "$BUNDLE/config/rag-pipeline"

write_config() {
  local key="$1" path="$2"
  if [[ -z "${!key:-}" ]]; then
    echo "ERROR: $key is required for the selected staging services." >&2
    exit 1
  fi
  printf '%s\n' "${!key}" > "$BUNDLE/$path"
}

write_config STAGE_ENV_DEPLOYMENT .env
if codecrow_includes_service web-server "${CODECROW_RESOLVED_SERVICES[@]}" || codecrow_includes_service pipeline-agent "${CODECROW_RESOLVED_SERVICES[@]}"; then
  write_config STAGE_ENV_JAVA_SHARED config/java-shared/application.properties
fi
if codecrow_includes_service inference-orchestrator "${CODECROW_RESOLVED_SERVICES[@]}"; then
  write_config STAGE_ENV_INFERENCE_ORCHESTRATOR config/inference-orchestrator/.env
fi
if codecrow_includes_service rag-pipeline "${CODECROW_RESOLVED_SERVICES[@]}"; then
  write_config STAGE_ENV_RAG_PIPELINE config/rag-pipeline/.env
fi
if [[ -n "${STAGE_GITHUB_APP_PRIVATE_KEY:-}" ]]; then
  write_config STAGE_GITHUB_APP_PRIVATE_KEY config/java-shared/github-private-key/github-app-private-key.pem
fi

cp "$ROOT_DIR/deployment/docker-compose.stage.yml" "$BUNDLE/"
cp "$SCRIPT_DIR/server-deploy-stage.sh" "$SCRIPT_DIR/service-selection.sh" "$BUNDLE/"
printf '%s\n' "$STAGE_DEPLOY_SSH_KEY" > "$WORK_DIR/deploy_key"
printf '%s\n' "$STAGE_DEPLOY_HOST_FINGERPRINT" > "$WORK_DIR/known_hosts"
SSH=(ssh -i "$WORK_DIR/deploy_key" -p "$DEPLOY_PORT" -o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$WORK_DIR/known_hosts")
DESTINATION="$STAGE_DEPLOY_USER@$STAGE_DEPLOY_HOST"
printf -v PATH_QUOTED '%q' "$DEPLOY_PATH"
tar -C "$BUNDLE" -cf - . | "${SSH[@]}" "$DESTINATION" "umask 077; mkdir -p $PATH_QUOTED && tar -xf - -C $PATH_QUOTED"

printf -v OWNER_QUOTED '%q' "${GITHUB_REPOSITORY_OWNER:?GITHUB_REPOSITORY_OWNER is required}"
printf -v SERVICES_QUOTED '%q' "${CODECROW_DEPLOY_SERVICES:-all}"
printf -v TAG_QUOTED '%q' "${CODECROW_IMAGE_TAG:-}"
printf -v USER_QUOTED '%q' "${GHCR_USER:?GHCR_USER is required}"
# Pass the short-lived registry token over SSH stdin, never in argv or a config archive.
printf '%s\n' "${STAGE_GHCR_PAT:-${GHCR_TOKEN:?GHCR_TOKEN or STAGE_GHCR_PAT is required}}" | \
  "${SSH[@]}" "$DESTINATION" \
  "GITHUB_REPOSITORY_OWNER=$OWNER_QUOTED CODECROW_DEPLOY_SERVICES=$SERVICES_QUOTED CODECROW_IMAGE_TAG=$TAG_QUOTED CODECROW_GHCR_USER=$USER_QUOTED CODECROW_GHCR_LOGIN_STDIN=true bash $PATH_QUOTED/server-deploy-stage.sh"
