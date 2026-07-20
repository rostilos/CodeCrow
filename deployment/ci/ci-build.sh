#!/bin/bash
###############################################################################
# ci-build.sh — Runs inside the GitHub Actions runner.
#
# 1. Runs the complete Java, inference Python, and RAG Python test suites
# 2. Writes the selected services' .env files from GitHub secrets
# 3. Copies the verified Java artifacts into selected build contexts
# 4. Builds selected Docker images and pushes them to GHCR
#
# Required env vars for selected services:
#   ENV_INFERENCE_ORCHESTRATOR  — contents of inference-orchestrator/src/.env
#   ENV_RAG_PIPELINE            — contents of rag-pipeline/.env
#   ENV_WEB_FRONTEND            — public VITE_* and SERVER_PORT frontend values
#
# Optional env vars:
#   PUBLIC_WEB_FRONTEND_ENV     — backward-compatible fallback for
#                                  ENV_WEB_FRONTEND
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
PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTHON_TEST_VENV_ROOT="${PYTHON_TEST_VENV_ROOT:-$ROOT_DIR/.ci-venvs}"
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

  echo "--- 1. Running Java tests and packaging verified artifacts (mvn clean verify) ---"
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
      clean verify
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

  echo "  ✓ Java tests passed and verified artifacts were packaged (log: $log_file)"
}

run_python_suite() {
  local suite_slug="$1"
  local suite_label="$2"
  local suite_dir="$3"
  local requirements_file="$4"
  local coverage_source="$5"
  local venv_dir="$PYTHON_TEST_VENV_ROOT/$suite_slug"
  local python="$venv_dir/bin/python"
  local install_log="$CI_LOG_DIR/$suite_slug-install.log"
  local test_log="$CI_LOG_DIR/$suite_slug-tests.log"
  local status
  local test_dependencies=(
    "pytest>=8,<9"
    "pytest-asyncio>=0.23,<2"
    "pytest-cov>=5,<8"
  )

  if [ "$suite_slug" = "inference-python" ]; then
    test_dependencies+=("respx>=0.21,<1")
  fi

  echo "--- Running $suite_label dependency installation ---"
  mkdir -p "$PYTHON_TEST_VENV_ROOT" "$suite_dir/test-results"
  set +e
  (
    set -e
    "$PYTHON_BIN" -m venv --clear "$venv_dir"
    "$python" -m pip install --upgrade pip
    "$python" -m pip install \
      -r "$suite_dir/$requirements_file" \
      "${test_dependencies[@]}"
  ) > "$install_log" 2>&1
  status=$?
  set -e

  if [ "$status" -ne 0 ]; then
    echo "::error::$suite_label dependency installation failed with exit code $status. Full log: $install_log"
    echo "::group::$suite_label installation log tail"
    print_log_tail "$install_log"
    echo "::endgroup::"
    exit "$status"
  fi

  echo "--- Running $suite_label tests and coverage ---"
  set +e
  (
    set -e
    cd "$suite_dir"
    "$python" -m pytest tests integration \
      --junitxml=test-results/junit.xml \
      --cov="$coverage_source" \
      --cov-report=term-missing \
      --cov-report=xml:test-results/coverage.xml
  ) > "$test_log" 2>&1
  status=$?
  set -e

  if [ "$status" -ne 0 ]; then
    echo "::error::$suite_label tests failed with exit code $status. Full log: $test_log"
    echo "::group::$suite_label test log tail"
    print_log_tail "$test_log"
    echo "::endgroup::"
    exit "$status"
  fi

  echo "::group::$suite_label test summary"
  print_log_tail "$test_log" 100
  echo "::endgroup::"
  echo "  ✓ $suite_label tests passed (log: $test_log)"
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

write_env_file() {
  local content="$1"
  local target="$2"
  local label="$3"

  if [ -z "$content" ]; then
    echo "ERROR: Missing $label configuration." >&2
    exit 1
  fi

  mkdir -p "$(dirname "$target")"
  printf '%s' "$content" > "$target"
  chmod 600 "$target"
  echo "  ✓ $label configuration written"
}

validate_frontend_env() {
  local line

  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    if [ -z "$line" ] || [[ "$line" =~ ^[[:space:]]*# ]]; then
      continue
    fi
    if [[ ! "$line" =~ ^(VITE_[A-Z0-9_]+|SERVER_PORT)=.*$ ]]; then
      echo "ERROR: Frontend build configuration may contain only VITE_* or SERVER_PORT assignments and comments." >&2
      exit 1
    fi
  done <<< "$1"
}

prepare_service_env_files() {
  local frontend_env

  echo "--- 2. Writing selected service configuration ---"
  if codecrow_includes_service "inference-orchestrator" "${SELECTED_SERVICES[@]}"; then
    write_env_file \
      "${ENV_INFERENCE_ORCHESTRATOR:-}" \
      "python-ecosystem/inference-orchestrator/src/.env" \
      "inference-orchestrator"
  fi

  if codecrow_includes_service "rag-pipeline" "${SELECTED_SERVICES[@]}"; then
    write_env_file \
      "${ENV_RAG_PIPELINE:-}" \
      "python-ecosystem/rag-pipeline/.env" \
      "rag-pipeline"
  fi

  if codecrow_includes_service "web-frontend" "${SELECTED_SERVICES[@]}"; then
    frontend_env="${ENV_WEB_FRONTEND:-${PUBLIC_WEB_FRONTEND_ENV:-}}"
    if [ -z "$frontend_env" ]; then
      echo "ERROR: Missing web-frontend configuration." >&2
      exit 1
    fi
    validate_frontend_env "$frontend_env"
    write_env_file "$frontend_env" "frontend/.env" "web-frontend"
    PUBLIC_WEB_FRONTEND_ENV="$frontend_env"
    PUBLIC_WEB_FRONTEND_ENV_SHA256=$(printf '%s' "$frontend_env" | sha256sum | cut -d' ' -f1)
    export PUBLIC_WEB_FRONTEND_ENV PUBLIC_WEB_FRONTEND_ENV_SHA256
  fi
}

echo "=========================================="
echo "  CodeCrow CI Build"
echo "=========================================="
echo "Selected services: $SELECTED_SERVICES_LABEL"
echo "Image tag: $CODECROW_IMAGE_TAG"

# ── 1. Verify every backend package before any image build ─────────────────
# This intentionally repeats the reusable PR test workflow. ci-build.sh is
# independently invocable, and the exact artifacts pushed below must be
# produced by a process in which every Java and Python suite has passed.
run_maven_verify
run_python_suite \
  "inference-python" \
  "inference-orchestrator Python" \
  "python-ecosystem/inference-orchestrator" \
  "src/requirements.txt" \
  "src"
run_python_suite \
  "rag-python" \
  "RAG pipeline Python" \
  "python-ecosystem/rag-pipeline" \
  "requirements.txt" \
  "src/rag_pipeline"

# ── 2. Materialize selected service configuration ─────────────────────────
prepare_service_env_files

# ── 3. Copy verified MCP JARs ─────────────────────────────────────────────
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
