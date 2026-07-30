#!/usr/bin/env bash
set -euo pipefail

FRONTEND_DIR="frontend"
DOCKER_PATH="deployment"
CONFIG_PATH="deployment/config"
PYTHON_BOOTSTRAP="${PYTHON_BOOTSTRAP:-python3.11}"
LOCAL_CI_VENV_ROOT="${CODECROW_CI_VENV_ROOT:-${TMPDIR:-/tmp}/codecrow-ci-python-${UID:-local}}"

cd "$(dirname "$0")/../../"

echo "=========================================="
echo "  CodeCrow local production build"
echo "  Mirrors the CI/CD verification pipeline"
echo "=========================================="

echo "--- 1. Synchronizing the frontend submodule with origin/main ---"
git submodule update --init --recursive --remote -- "$FRONTEND_DIR"

ACTUAL_FRONTEND_COMMIT="$(git -C "$FRONTEND_DIR" rev-parse HEAD)"
FRONTEND_WORKTREE_STATUS="$(git -C "$FRONTEND_DIR" status --porcelain --untracked-files=normal)"
if [ -n "$FRONTEND_WORKTREE_STATUS" ]; then
    echo "Frontend submodule has non-ignored local changes; refusing a non-reproducible production build." >&2
    exit 1
fi
echo "Frontend at latest origin/main commit: $ACTUAL_FRONTEND_COMMIT"

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
    "$PYTHON_BOOTSTRAP" -m venv --clear "$venv_path"
    PYTHON_BIN="$python_bin" \
        PYTHON_TEST_GROUP="$group" \
        deployment/ci/install-python-test-dependencies.sh
    PYTHON_BIN="$python_bin" \
        PYTHON_TEST_GROUP="$group" \
        deployment/ci/python-tests.sh
}

echo "--- 3. Running the same isolated Python matrices as CI/CD ---"
run_python_ci_group rag
run_python_ci_group inference

echo "--- 4. Running the shared Java, plugin, and Docker CI build ---"
CODECROW_DOCKER_OUTPUT=load \
CODECROW_LOCAL_IMAGE_PREFIX=codecrow-local \
CODECROW_DEPLOY_SERVICES=all \
deployment/ci/ci-build.sh

echo "--- 5. Shutting down existing services cleanly ---"
cd "$DOCKER_PATH"
docker compose down --remove-orphans

echo "--- 6. Starting the locally loaded CI-equivalent images ---"
docker compose up -d --no-build --wait

echo "--- Deployment Complete! Services are up and healthy. ---"
docker compose ps
