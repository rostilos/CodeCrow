#!/usr/bin/env bash
set -euo pipefail

BUILD_ONLY=false
case "${1:-}" in
    "") ;;
    --build-only) BUILD_ONLY=true ;;
    *) echo "Usage: $0 [--build-only]" >&2; exit 2 ;;
esac

FRONTEND_DIR="frontend"
DOCKER_PATH="deployment"
CONFIG_PATH="deployment/config"
PYTHON_BOOTSTRAP="${PYTHON_BOOTSTRAP:-python3.11}"

cd "$(dirname "$0")/../../"
LOCAL_CI_VENV_ROOT="${CODECROW_CI_VENV_ROOT:-$PWD/.work/ci-python}"
export CI_LOG_DIR="${CI_LOG_DIR:-$PWD/.ci-logs}"

# Concurrent clean builds otherwise remove one another's Maven outputs.
mkdir -p .ci-logs
exec 9>.ci-logs/production-build.lock
echo "Waiting for the local build lock..."
lock_started=$SECONDS
flock 9
echo "Build lock acquired after $((SECONDS - lock_started))s"
build_started=$SECONDS
mkdir -p "$CI_LOG_DIR"

echo "=========================================="
echo "  CodeCrow local production build"
echo "  CI verification with local observability disabled"
echo "=========================================="

echo "--- 1. Using the current frontend workspace ---"
if [ ! -f "$FRONTEND_DIR/package.json" ]; then
    echo "Frontend submodule is not initialized: $FRONTEND_DIR/package.json is missing." >&2
    echo "Run: git submodule update --init --recursive -- $FRONTEND_DIR" >&2
    exit 1
fi
ACTUAL_FRONTEND_COMMIT="$(git -C "$FRONTEND_DIR" rev-parse HEAD)"
echo "Frontend workspace commit: $ACTUAL_FRONTEND_COMMIT"

echo "--- 2. Injecting Environment Configurations ---"

echo "Copying inference-orchestrator .env..."
cp "$CONFIG_PATH/inference-orchestrator/.env" "python-ecosystem/inference-orchestrator/src/.env"

echo "Copying rag-pipeline .env..."
cp "$CONFIG_PATH/rag-pipeline/.env" "python-ecosystem/rag-pipeline/.env"

echo "Copying web-frontend .env..."
# Using the variable ensures we target the folder defined at the top
cp "$CONFIG_PATH/web-frontend/.env" "$FRONTEND_DIR/.env"

if ! command -v "$PYTHON_BOOTSTRAP" >/dev/null 2>&1; then
    echo "Python 3.11 is required to mirror GitHub Actions; '$PYTHON_BOOTSTRAP' was not found." >&2
    exit 1
fi

run_python_ci_group() {
    local group="$1"
    local venv_path="$LOCAL_CI_VENV_ROOT/$group"
    local python_bin="$venv_path/bin/python"

    echo "--- Python CI matrix: $group ---"
    local started=$SECONDS
    bash deployment/ci/prepare-python-test-environment.sh "$group" "$venv_path" "$PYTHON_BOOTSTRAP"
    PYTHON_BIN="$python_bin" \
        PYTHON_TEST_GROUP="$group" \
        deployment/ci/python-tests.sh
    echo "Python CI matrix $group passed in $((SECONDS - started))s"
}

echo "--- 3. Running Python matrices and the shared Java/plugin/Docker build concurrently ---"
echo "Python logs: $CI_LOG_DIR/python-{rag,inference}.log"
# Always join every job before releasing the workspace lock, including on failure.
trap 'wait' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
run_python_ci_group rag > "$CI_LOG_DIR/python-rag.log" 2>&1 &
rag_pid=$!
run_python_ci_group inference > "$CI_LOG_DIR/python-inference.log" 2>&1 &
inference_pid=$!
(
    started=$SECONDS
    CODECROW_DOCKER_OUTPUT=load \
    CODECROW_DOCKER_OBSERVABILITY=disabled \
    CODECROW_LOCAL_IMAGE_PREFIX=codecrow-local \
    CODECROW_DEPLOY_SERVICES=all \
    deployment/ci/ci-build.sh
    echo "Java/plugin/Docker build passed in $((SECONDS - started))s"
) &
ci_pid=$!

status=0
for job in "rag:$rag_pid" "inference:$inference_pid" "ci:$ci_pid"; do
    name="${job%%:*}"
    if wait "${job#*:}"; then
        echo "  ✓ $name completed"
    else
        status=$?
        echo "ERROR: $name failed (exit $status). Logs: $CI_LOG_DIR" >&2
        if [ "$name" != ci ]; then
            tail -n 80 "$CI_LOG_DIR/python-$name.log" >&2
        fi
    fi
done
trap - EXIT INT TERM
if [ "$status" -ne 0 ]; then
    exit "$status"
fi
echo "Full build and verification passed in $((SECONDS - build_started))s"

if [ "$BUILD_ONLY" = true ]; then
    echo "--- Build complete. Tests passed and images loaded; services were not restarted. ---"
    exit 0
fi

echo "--- 4. Starting the locally loaded CI-equivalent images ---"
cd "$DOCKER_PATH"
# The local database may contain migrations from another pre-release checkout.
# Keep migrations enabled while allowing that existing local history.
docker compose -f docker-compose.yml -f docker-compose.local-build.yml up -d --no-build --wait

echo "--- Deployment Complete! Services are up and healthy. ---"
docker compose -f docker-compose.yml -f docker-compose.local-build.yml ps
