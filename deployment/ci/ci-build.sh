#!/bin/bash
###############################################################################
# ci-build.sh — Runs inside the GitHub Actions runner.
#
# 1. Writes .env files from GitHub secrets
# 2. Builds & tests Java artifacts (Maven)  — fails fast on test errors
# 3. Copies MCP JARs to inference-orchestrator context
# 4. Builds all 5 Docker images and pushes them to GHCR
#
# Required env vars (set by GH Actions):
#   ENV_INFERENCE_ORCHESTRATOR  — contents of inference-orchestrator/.env
#   ENV_RAG_PIPELINE            — contents of rag-pipeline/.env
#   ENV_WEB_FRONTEND            — contents of web-frontend/.env
###############################################################################
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

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

cd "$ROOT_DIR"
mkdir -p "$CI_LOG_DIR"

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

echo "=========================================="
echo "  CodeCrow CI Build"
echo "=========================================="

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
run_maven_verify

# ── 3. Copy MCP JARs ──────────────────────────────────────────────────────
echo "--- 3. Copying MCP server JARs ---"
cp "$MCP_JAR" python-ecosystem/inference-orchestrator/src/codecrow-vcs-mcp-1.0.jar
echo "  ✓ VCS MCP JAR copied"

if [ -f "$PLATFORM_MCP_JAR" ]; then
  cp "$PLATFORM_MCP_JAR" python-ecosystem/inference-orchestrator/src/codecrow-platform-mcp-1.0.jar
  echo "  ✓ Platform MCP JAR copied"
else
  echo "  ⚠ Platform MCP JAR not found (optional)"
fi

# ── 4. Build Docker Images (with GitHub Actions layer cache) ───────────────
echo "--- 4. Building Docker images ---"

IMAGES=(
  "codecrow/web-server|java-ecosystem/services/web-server|Dockerfile.observable"
  "codecrow/pipeline-agent|java-ecosystem/services/pipeline-agent|Dockerfile.observable"
  "codecrow/inference-orchestrator|python-ecosystem/inference-orchestrator/src|Dockerfile.observable"
  "codecrow/rag-pipeline|python-ecosystem/rag-pipeline"
  "codecrow/web-frontend|frontend"
)

# Use BuildKit + GitHub Actions cache backend so base-image pulls, pip install,
# npm ci, apk add, etc. are cached across CI runs.

for entry in "${IMAGES[@]}"; do
  IFS='|' read -r IMAGE_NAME CONTEXT DOCKERFILE <<< "$entry"
  # Scope cache per image to avoid collisions
  SCOPE="$(echo "$IMAGE_NAME" | tr '/' '-')"
  
  # Map codecrow to ghcr.io/<repo-owner>/codecrow-<service>
  # E.g. codecrow/web-server -> ghcr.io/username/codecrow-web-server:latest
  SERVICE_NAME=$(echo "$IMAGE_NAME" | cut -d'/' -f2)
  FULL_IMAGE_NAME="ghcr.io/$REPO_OWNER/codecrow-$SERVICE_NAME:latest"

  echo "  Building and pushing $FULL_IMAGE_NAME from $CONTEXT ..."
  BUILD_LOG="$CI_LOG_DIR/docker-$SCOPE.log"
  BUILD_ARGS=(
    docker buildx build
    --cache-from "type=gha,scope=$SCOPE"
    --cache-to "type=gha,mode=max,scope=$SCOPE"
    --network="$DOCKER_BUILD_NETWORK"
    --progress="$DOCKER_BUILD_PROGRESS"
    --push
    -t "$FULL_IMAGE_NAME"
  )

  if [ -n "${DOCKERFILE:-}" ]; then
    BUILD_ARGS+=(-f "$CONTEXT/$DOCKERFILE")
  fi

  BUILD_ARGS+=("$CONTEXT")
  run_logged "Docker build $FULL_IMAGE_NAME" "$BUILD_LOG" "$DOCKER_BUILD_RETRIES" "${BUILD_ARGS[@]}"
  echo "  ✓ $FULL_IMAGE_NAME built and pushed"
done

echo ""
echo "=========================================="
echo "  Build and push complete!"
echo "=========================================="
