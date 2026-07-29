#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TEST_GROUP="${PYTHON_TEST_GROUP:-}"

case "$TEST_GROUP" in
  rag)
    REQUIREMENTS="$ROOT_DIR/python-ecosystem/rag-pipeline/requirements.txt"
    ;;
  inference)
    REQUIREMENTS="$ROOT_DIR/python-ecosystem/inference-orchestrator/src/requirements.test.txt"
    ;;
  *)
    echo "PYTHON_TEST_GROUP must be either 'rag' or 'inference'." >&2
    exit 2
    ;;
esac

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r "$REQUIREMENTS"
"$PYTHON_BIN" -m pip check
