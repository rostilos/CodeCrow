#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
group="$1"
venv_path="$2"
bootstrap="$3"
case "$group" in
    rag) requirements_dir="$ROOT_DIR/python-ecosystem/rag-pipeline" ;;
    inference) requirements_dir="$ROOT_DIR/python-ecosystem/inference-orchestrator/src" ;;
    *) echo "Unknown Python test group: $group" >&2; exit 2 ;;
esac

python_bin="$venv_path/bin/python"
stamp="$venv_path/.requirements.sha256"
runtime="$("$bootstrap" -c 'import sys; print(sys.executable); print(sys.version)')"
# shortcut: all current includes are sibling requirements* files, expand the key if includes move.
key="$({
    printf '%s\n' "$runtime"
    sha256sum "$ROOT_DIR/deployment/ci/install-python-test-dependencies.sh"
    find "$requirements_dir" -maxdepth 1 -type f -name 'requirements*' -exec sha256sum {} + | sort
} | sha256sum)"

if [ -x "$python_bin" ] && [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$key" ] \
    && "$python_bin" -m pip check; then
    echo "Reusing Python test environment: $group"
else
    echo "Preparing Python test environment: $group"
    rm -f "$stamp"
    "$bootstrap" -m venv --clear "$venv_path"
    PYTHON_BIN="$python_bin" PYTHON_TEST_GROUP="$group" \
        "$ROOT_DIR/deployment/ci/install-python-test-dependencies.sh"
    printf '%s\n' "$key" > "$stamp"
fi
