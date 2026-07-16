#!/bin/bash
###############################################################################
# ci-build.sh — Runs inside the GitHub Actions runner.
#
# 1. Writes .env files from GitHub secrets
# 2. Builds & tests Java artifacts when selected images need them
# 3. Copies MCP JARs to inference-orchestrator context when needed
# 4. Builds selected Docker images and pushes them to GHCR
#
# Required env vars (set by GH Actions):
#   ENV_INFERENCE_ORCHESTRATOR  — contents of inference-orchestrator/.env
#   ENV_RAG_PIPELINE            — contents of rag-pipeline/.env
#   ENV_WEB_FRONTEND            — contents of web-frontend/.env
#
# Optional env vars:
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
CI_TEST_LOG_LEVEL="${CI_TEST_LOG_LEVEL:-WARN}"
DOCKER_BUILD_NETWORK="${DOCKER_BUILD_NETWORK:-host}"
DOCKER_BUILD_PROGRESS="${DOCKER_BUILD_PROGRESS:-auto}"
DOCKER_BUILD_RETRIES="${DOCKER_BUILD_RETRIES:-3}"
# GITHUB_REPOSITORY_OWNER is assumed to be provided for ghcr.io paths
REPO_OWNER=${GITHUB_REPOSITORY_OWNER:-codecrow}
REPO_OWNER=$(echo "$REPO_OWNER" | tr '[:upper:]' '[:lower:]')
REQUESTED_SERVICES="${CODECROW_DEPLOY_SERVICES:-${CODECROW_BUILD_SERVICES:-all}}"
CODECROW_IMAGE_TAG="${CODECROW_IMAGE_TAG:-${GITHUB_SHA:-}}"

if [[ ! "$CODECROW_IMAGE_TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "ERROR: CODECROW_IMAGE_TAG must be an immutable OCI-compatible tag." >&2
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

print_test_failure_reports() {
  local found=0

  echo "::group::Java test failure summaries"
  while IFS= read -r report; do
    if grep -Eq "Failures: [1-9]|Errors: [1-9]|<<< FAILURE!|<<< ERROR!" "$report"; then
      found=1
      echo ""
      echo "----- $report -----"
      sed -n '1,220p' "$report"
    fi
  done < <(find "$JAVA_DIR" -path "*/target/*-reports/*.txt" -type f | sort)

  if [ "$found" -eq 0 ]; then
    echo "No failing surefire/failsafe text reports were found."
  fi
  echo "::endgroup::"
}

run_maven_verify() {
  local log_file="$CI_LOG_DIR/maven-verify.log"
  local status

  echo "--- 2. Building & testing Java artifacts (mvn clean verify) ---"
  set +e
  (
    cd "$JAVA_DIR"
    mvn -B --no-transfer-progress \
      -Dspring.main.banner-mode=off \
      -Dspring.main.log-startup-info=false \
      -Dlogging.level.root="$CI_TEST_LOG_LEVEL" \
      -Dlogging.level.org.rostilos.codecrow="$CI_TEST_LOG_LEVEL" \
      -Dlogging.level.org.springframework=WARN \
      -Dlogging.level.org.springframework.security=WARN \
      -Dlogging.level.org.hibernate=WARN \
      -Dlogging.level.org.hibernate.SQL=OFF \
      clean verify -T 1C
  ) > "$log_file" 2>&1
  status=$?
  set -e

  if [ "$status" -ne 0 ]; then
    echo "::error::Maven verify failed with exit code $status. Full log: $log_file"
    print_test_failure_reports
    echo "::group::Maven log tail"
    print_log_tail "$log_file"
    echo "::endgroup::"
    exit "$status"
  fi

  echo "  ✓ Java build & tests complete (log: $log_file)"
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

echo "=========================================="
echo "  CodeCrow CI Build"
echo "=========================================="
echo "Selected services: $SELECTED_SERVICES_LABEL"
echo "Image tag: $CODECROW_IMAGE_TAG"

# ── 1. Inject .env files from secrets ──────────────────────────────────────
echo "--- 1. Writing .env files from CI secrets ---"

if [ -n "${ENV_INFERENCE_ORCHESTRATOR:-}" ]; then
  echo "$ENV_INFERENCE_ORCHESTRATOR" > python-ecosystem/inference-orchestrator/src/.env
  echo "  ✓ inference-orchestrator/src/.env written"
fi

if [ -n "${ENV_RAG_PIPELINE:-}" ]; then
  echo "$ENV_RAG_PIPELINE" > python-ecosystem/rag-pipeline/.env
  echo "  ✓ rag-pipeline/.env written"
fi

if [ -n "${ENV_WEB_FRONTEND:-}" ]; then
  echo "$ENV_WEB_FRONTEND" > frontend/.env
  echo "  ✓ web-frontend/.env written"
fi

# ── 2. Build & Test Java Artifacts ─────────────────────────────────────────
if codecrow_requires_java_artifacts "${SELECTED_SERVICES[@]}"; then
  run_maven_verify
else
  echo "--- 2. Skipping Java build & tests (no selected image needs Java artifacts) ---"
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
