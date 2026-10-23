#!/usr/bin/env bash
# Staging-only server deployment. Its directory, project and registry packages
# are independent of server-deploy.sh and the live stack.
set -euo pipefail
umask 077

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DEPLOY_DIR/service-selection.sh"
codecrow_resolve_services "${CODECROW_DEPLOY_SERVICES:-all}"
SELECTED_SERVICES=("${CODECROW_RESOLVED_SERVICES[@]}")
export GITHUB_REPOSITORY_OWNER="$(printf '%s' "${GITHUB_REPOSITORY_OWNER:?GITHUB_REPOSITORY_OWNER is required}" | tr '[:upper:]' '[:lower:]')"
cd "$DEPLOY_DIR"

WORK_DIR="$(mktemp -d "$DEPLOY_DIR/.deploy-XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
if [[ "${CODECROW_GHCR_LOGIN_STDIN:-false}" == true ]]; then
  export DOCKER_CONFIG="$WORK_DIR/docker"
  mkdir -p "$DOCKER_CONFIG"
  IFS= read -r REGISTRY_TOKEN
  printf '%s' "$REGISTRY_TOKEN" | docker login ghcr.io -u "${CODECROW_GHCR_USER:?CODECROW_GHCR_USER is required}" --password-stdin
  unset REGISTRY_TOKEN
fi

IMAGE_ENV="$WORK_DIR/images.env"
if [[ -f .images.env ]]; then
  cp .images.env "$IMAGE_ENV"
else
  : > "$IMAGE_ENV"
fi
TAG="${CODECROW_IMAGE_TAG:-}"
if [[ -z "$TAG" && ! -s .images.env ]]; then
  echo "ERROR: No deployed staging release exists. Build first or select an existing staging image_tag." >&2
  exit 1
fi
if [[ -n "$TAG" ]]; then
  if [[ ! "$TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
    echo "ERROR: Invalid staging image tag." >&2
    exit 1
  fi
  for service in "${SELECTED_SERVICES[@]}"; do
    key="$(printf '%s' "$service" | tr '[:lower:]-' '[:upper:]_')_IMAGE_TAG"
    # Preserve the deployed revisions of every unselected application service.
    sed "/^${key}=/d" "$IMAGE_ENV" > "$WORK_DIR/next.env"
    mv "$WORK_DIR/next.env" "$IMAGE_ENV"
    printf '%s=%s\n' "$key" "$TAG" >> "$IMAGE_ENV"
  done
fi
COMPOSE=(docker compose --project-name codecrow-stage --env-file "$DEPLOY_DIR/.env" --env-file "$IMAGE_ENV" -f "$DEPLOY_DIR/docker-compose.stage.yml")

echo "Staging deployment: $(codecrow_join_services ', ' "${SELECTED_SERVICES[@]}")"
echo "Deployment directory: $DEPLOY_DIR"
# Validate interpolation before changing running containers; do not log secrets.
"${COMPOSE[@]}" config --quiet

BACKUP_FILE=""
if codecrow_includes_service web-server "${SELECTED_SERVICES[@]}"; then
  if [[ -n "$("${COMPOSE[@]}" ps --status running -q postgres)" ]]; then
    mkdir -p backups
    BACKUP_FILE="$DEPLOY_DIR/backups/codecrow_stage_pre_deploy_$(date '+%Y%m%d_%H%M%S').sql.gz"
    # Use the running database container's credentials, including on credential edits.
    "${COMPOSE[@]}" exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$WORK_DIR/database.sql.gz"
    mv "$WORK_DIR/database.sql.gz" "$BACKUP_FILE"
    echo "Staging database backup: $BACKUP_FILE"
  else
    echo "Staging PostgreSQL is not running; no pre-deploy backup exists."
  fi
fi

# Pull before recreation. Dependency tags come from the per-service release file.
"${COMPOSE[@]}" pull --include-deps "${SELECTED_SERVICES[@]}"
# Recreate selected apps so mounted runtime configuration is reloaded. Compose
# starts their dependencies as needed; no stack-wide down or volume deletion.
if ! "${COMPOSE[@]}" up -d --no-build --force-recreate --wait --wait-timeout "${CODECROW_DEPLOY_WAIT_SECONDS:-600}" "${SELECTED_SERVICES[@]}"; then
  echo "ERROR: Staging services did not become healthy; inspect the statuses below." >&2
  "${COMPOSE[@]}" ps --all || true
  echo "Inspect: cd $DEPLOY_DIR && docker compose -p codecrow-stage -f docker-compose.stage.yml logs ${SELECTED_SERVICES[*]}"
  [[ -z "$BACKUP_FILE" ]] || echo "Database backup: $BACKUP_FILE"
  echo "The last successful .images.env is retained. A failed update can leave a partially updated stack; no automatic database rollback is performed."
  exit 1
fi

"${COMPOSE[@]}" ps "${SELECTED_SERVICES[@]}"
mv "$IMAGE_ENV" "$DEPLOY_DIR/.images.env"
# Retain the ten most recent successful backups in this staging directory only.
shopt -s nullglob
BACKUPS=("$DEPLOY_DIR"/backups/codecrow_stage_pre_deploy_*.sql.gz)
if (( ${#BACKUPS[@]} > 10 )); then
  rm -f -- "${BACKUPS[@]:0:${#BACKUPS[@]}-10}"
fi
echo "Staging deployment complete. Deployed image tags: $DEPLOY_DIR/.images.env"
