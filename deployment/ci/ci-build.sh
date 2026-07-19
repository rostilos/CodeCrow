#!/bin/bash
###############################################################################
# ci-build.sh — Runs inside the GitHub Actions runner.
#
# 1. Validates the explicitly public frontend build configuration
# 2. Packages Java artifacts when selected images need them
# 3. Copies MCP JARs to inference-orchestrator context when needed
# 4. Builds selected Docker images and pushes them to GHCR
#
# Optional env vars:
#   PUBLIC_WEB_FRONTEND_ENV     — public VITE_* values embedded in the UI;
#                                  required when web-frontend is selected
#   CODECROW_DEPLOY_SERVICES    — comma/space separated service list:
#                                  all, java, python, frontend, web-server,
#                                  pipeline-agent, inference-orchestrator,
#                                  rag-pipeline, web-frontend
#   CODECROW_IMAGE_TAG          — immutable image tag; defaults to GITHUB_SHA
###############################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/service-selection.sh"

MCP_JAR="java-ecosystem/mcp-servers/vcs-mcp/target/codecrow-vcs-mcp-1.0.jar"
PLATFORM_MCP_JAR="java-ecosystem/mcp-servers/platform-mcp/target/codecrow-platform-mcp-1.0.jar"
JAVA_DIR="java-ecosystem"
CI_LOG_DIR="${CI_LOG_DIR:-$ROOT_DIR/.ci-logs}"
CI_LOG_TAIL_LINES="${CI_LOG_TAIL_LINES:-200}"
DOCKER_BUILD_NETWORK="${DOCKER_BUILD_NETWORK:-host}"
DOCKER_BUILD_PROGRESS="${DOCKER_BUILD_PROGRESS:-auto}"
DOCKER_BUILD_RETRIES="${DOCKER_BUILD_RETRIES:-3}"
# GITHUB_REPOSITORY_OWNER is assumed to be provided for ghcr.io paths
REPO_OWNER=${GITHUB_REPOSITORY_OWNER:-codecrow}
REPO_OWNER=$(echo "$REPO_OWNER" | tr '[:upper:]' '[:lower:]')
REQUESTED_SERVICES="${CODECROW_DEPLOY_SERVICES:-${CODECROW_BUILD_SERVICES:-all}}"
CODECROW_IMAGE_TAG="${CODECROW_IMAGE_TAG:-${GITHUB_SHA:-}}"

if [[ ! "$CODECROW_IMAGE_TAG" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
  echo "ERROR: CODECROW_IMAGE_TAG must be the exact 40- or 64-hex source commit." >&2
  exit 1
fi

cd "$ROOT_DIR"
mkdir -p "$CI_LOG_DIR"

codecrow_resolve_services "$REQUESTED_SERVICES"
SELECTED_SERVICES=("${CODECROW_RESOLVED_SERVICES[@]}")
SELECTED_SERVICES_LABEL="$(codecrow_join_services ", " "${SELECTED_SERVICES[@]}")"

print_log_tail() {
  local log_file="$1"
  local lines="${2:-$CI_LOG_TAIL_LINES}"

  if [ -f "$log_file" ]; then
    tail -n "$lines" "$log_file" || true
  else
    echo "Log file was not created: $log_file"
  fi
}

run_maven_package() {
  local log_file="$CI_LOG_DIR/maven-package.log"
  local status

  # Tests already passed in the required test job. This job only creates the
  # JARs needed by the selected Docker build contexts.
  echo "--- 2. Packaging Java artifacts (mvn clean package -DskipTests) ---"
  set +e
  (
    cd "$JAVA_DIR"
    mvn -B --no-transfer-progress \
      -DskipTests \
      clean package -T 1C
  ) > "$log_file" 2>&1
  status=$?
  set -e

  if [ "$status" -ne 0 ]; then
    echo "::error::Maven package failed with exit code $status. Full log: $log_file"
    echo "::group::Maven log tail"
    print_log_tail "$log_file"
    echo "::endgroup::"
    exit "$status"
  fi

  echo "  ✓ Java artifacts packaged (log: $log_file)"
}

run_logged() {
  local label="$1"
  local log_file="$2"
  local max_attempts="$3"
  local attempt=1
  local status=0
  shift 3

  : > "$log_file"
  while [ "$attempt" -le "$max_attempts" ]; do
    echo "Attempt $attempt/$max_attempts: $label" >> "$log_file"
    set +e
    "$@" >> "$log_file" 2>&1
    status=$?
    set -e

    if [ "$status" -eq 0 ]; then
      return 0
    fi

    echo "$label failed on attempt $attempt/$max_attempts (exit $status)"
    if [ "$attempt" -lt "$max_attempts" ]; then
      sleep $((attempt * 10))
    fi
    attempt=$((attempt + 1))
  done

  echo "::error::$label failed with exit code $status after $max_attempts attempt(s). Full log: $log_file"
  echo "::group::$label log tail"
  print_log_tail "$log_file"
  echo "::endgroup::"
  exit "$status"
}

set_image_definition() {
  local service="$1"

  IMAGE_NAME=""
  CONTEXT=""
  DOCKERFILE=""

  case "$service" in
    web-server)
      IMAGE_NAME="codecrow/web-server"
      CONTEXT="java-ecosystem/services/web-server"
      DOCKERFILE="Dockerfile.observable"
      ;;
    pipeline-agent)
      IMAGE_NAME="codecrow/pipeline-agent"
      CONTEXT="java-ecosystem/services/pipeline-agent"
      DOCKERFILE="Dockerfile.observable"
      ;;
    inference-orchestrator)
      IMAGE_NAME="codecrow/inference-orchestrator"
      CONTEXT="python-ecosystem/inference-orchestrator/src"
      DOCKERFILE="Dockerfile.observable"
      ;;
    rag-pipeline)
      IMAGE_NAME="codecrow/rag-pipeline"
      CONTEXT="python-ecosystem/rag-pipeline"
      ;;
    web-frontend)
      IMAGE_NAME="codecrow/web-frontend"
      CONTEXT="frontend"
      ;;
    *)
      echo "ERROR: No Docker image definition exists for service '$service'." >&2
      exit 1
      ;;
  esac
}

validate_public_frontend_env() {
  local line

  if [ -z "${PUBLIC_WEB_FRONTEND_ENV:-}" ]; then
    echo "ERROR: PUBLIC_WEB_FRONTEND_ENV is required to build web-frontend." >&2
    exit 1
  fi

  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    if [ -z "$line" ] || [[ "$line" =~ ^[[:space:]]*# ]]; then
      continue
    fi
    if [[ ! "$line" =~ ^VITE_[A-Z0-9_]+=.*$ ]]; then
      echo "ERROR: Frontend build configuration may contain only VITE_* assignments and comments." >&2
      exit 1
    fi
  done <<< "$PUBLIC_WEB_FRONTEND_ENV"
}

echo "=========================================="
echo "  CodeCrow CI Build"
echo "=========================================="
echo "Selected services: $SELECTED_SERVICES_LABEL"
echo "Image tag: $CODECROW_IMAGE_TAG"

# ── 1. Validate public frontend build input ──────────────────────────────────────
if codecrow_includes_service "web-frontend" "${SELECTED_SERVICES[@]}"; then
  echo "--- 1. Validating public frontend build configuration ---"
  validate_public_frontend_env
  PUBLIC_WEB_FRONTEND_ENV_SHA256=$(printf '%s' "$PUBLIC_WEB_FRONTEND_ENV" | sha256sum | cut -d' ' -f1)
  export PUBLIC_WEB_FRONTEND_ENV
  echo "  ✓ Public VITE_* configuration validated"
else
  echo "--- 1. Skipping frontend configuration (web-frontend not selected) ---"
fi

# ── 2. Build & Test Java Artifacts ─────────────────────────────────────────
if codecrow_requires_java_artifacts "${SELECTED_SERVICES[@]}"; then
  run_maven_package
else
  echo "--- 2. Skipping Java package (no selected image needs Java artifacts) ---"
fi

# ── 3. Copy MCP JARs ──────────────────────────────────────────────────────
if codecrow_includes_service "inference-orchestrator" "${SELECTED_SERVICES[@]}"; then
  echo "--- 3. Copying MCP server JARs ---"
  cp "$MCP_JAR" python-ecosystem/inference-orchestrator/src/codecrow-vcs-mcp-1.0.jar
  echo "  ✓ VCS MCP JAR copied"

  if [ -f "$PLATFORM_MCP_JAR" ]; then
    cp "$PLATFORM_MCP_JAR" python-ecosystem/inference-orchestrator/src/codecrow-platform-mcp-1.0.jar
    echo "  ✓ Platform MCP JAR copied"
  else
    echo "  ⚠ Platform MCP JAR not found (optional)"
  fi
else
  echo "--- 3. Skipping MCP JAR copy (inference-orchestrator not selected) ---"
fi

# ── 4. Build Docker Images (with GitHub Actions layer cache) ───────────────
echo "--- 4. Building Docker images ---"
RELEASE_IMAGE_MANIFEST="$CI_LOG_DIR/release-images.txt"
: > "$RELEASE_IMAGE_MANIFEST"

# Use BuildKit + GitHub Actions cache backend so base-image pulls, pip install,
# npm ci, apk add, etc. are cached across CI runs.

for SERVICE in "${SELECTED_SERVICES[@]}"; do
  set_image_definition "$SERVICE"
  # Scope cache per image to avoid collisions
  SCOPE="$(echo "$IMAGE_NAME" | tr '/' '-')"
  
  # Map codecrow to ghcr.io/<repo-owner>/codecrow-<service>
  # E.g. codecrow/web-server -> ghcr.io/username/codecrow-web-server:<git-sha>
  SERVICE_NAME=$(echo "$IMAGE_NAME" | cut -d'/' -f2)
  FULL_IMAGE_NAME="ghcr.io/$REPO_OWNER/codecrow-$SERVICE_NAME:$CODECROW_IMAGE_TAG"

  echo "  Building and pushing $FULL_IMAGE_NAME from $CONTEXT ..."
  BUILD_LOG="$CI_LOG_DIR/docker-$SCOPE.log"
  BUILD_METADATA="$CI_LOG_DIR/docker-$SCOPE-metadata.json"
  BUILD_ARGS=(
    docker buildx build
    --cache-from "type=gha,scope=$SCOPE"
    --cache-to "type=gha,mode=max,scope=$SCOPE"
    --network="$DOCKER_BUILD_NETWORK"
    --progress="$DOCKER_BUILD_PROGRESS"
    --push
    --metadata-file "$BUILD_METADATA"
    -t "$FULL_IMAGE_NAME"
  )

  if [ "$SERVICE" = "web-frontend" ]; then
    BUILD_ARGS+=(
      --build-arg "PUBLIC_WEB_FRONTEND_ENV_SHA256=$PUBLIC_WEB_FRONTEND_ENV_SHA256"
      --secret "id=web_frontend_env,env=PUBLIC_WEB_FRONTEND_ENV"
    )
  fi

  if [ -n "${DOCKERFILE:-}" ]; then
    BUILD_ARGS+=(-f "$CONTEXT/$DOCKERFILE")
  fi

  BUILD_ARGS+=("$CONTEXT")
  run_logged "Docker build $FULL_IMAGE_NAME" "$BUILD_LOG" "$DOCKER_BUILD_RETRIES" "${BUILD_ARGS[@]}"
  IMAGE_DIGEST=$(jq -r '."containerimage.digest" // empty' "$BUILD_METADATA")
  if [[ ! "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "ERROR: Build metadata did not contain an immutable digest for $FULL_IMAGE_NAME." >&2
    exit 1
  fi
  echo "$FULL_IMAGE_NAME@$IMAGE_DIGEST" >> "$RELEASE_IMAGE_MANIFEST"
  echo "  ✓ $FULL_IMAGE_NAME@$IMAGE_DIGEST built and pushed"
done

echo ""
echo "=========================================="
echo "  Build and push complete for: $SELECTED_SERVICES_LABEL"
echo "  Immutable image manifest: $RELEASE_IMAGE_MANIFEST"
echo "=========================================="
