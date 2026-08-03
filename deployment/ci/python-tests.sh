#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RESULTS_DIR="${PYTHON_TEST_RESULTS_DIR:-$ROOT_DIR/.ci-test-results/python}"
TEST_GROUP="${PYTHON_TEST_GROUP:-all}"

mkdir -p "$RESULTS_DIR"

failures=0

case "$TEST_GROUP" in
  all|rag|inference)
    ;;
  *)
    echo "Unknown PYTHON_TEST_GROUP '$TEST_GROUP'; expected all, rag, or inference" >&2
    exit 2
    ;;
esac

run_suite() {
  local name="$1"
  local working_directory="$2"
  local test_path="$3"

  echo "::group::Python test suite: $name"
  (
    cd "$working_directory"
    PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -m pytest "$test_path" \
      --tb=short \
      --junitxml="$RESULTS_DIR/$name.xml"
  )
  local status=$?
  echo "::endgroup::"
  if [ "$status" -ne 0 ]; then
    failures=$((failures + 1))
  fi
}

if [ "$TEST_GROUP" = "all" ] || [ "$TEST_GROUP" = "rag" ]; then
  run_suite \
    "analysis-plugin-contracts" \
    "$ROOT_DIR/analysis-plugins/contracts/python" \
    "tests"
  run_suite \
    "rag-pipeline-unit" \
    "$ROOT_DIR/python-ecosystem/rag-pipeline" \
    "tests"
  run_suite \
    "rag-pipeline-integration" \
    "$ROOT_DIR/python-ecosystem/rag-pipeline" \
    "integration"

  echo "::group::Python plugin-boundary validation"
  (
    cd "$ROOT_DIR"
    PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" tools/validate_plugin_boundaries.py
  )
  boundary_status=$?
  echo "::endgroup::"
  if [ "$boundary_status" -ne 0 ]; then
    failures=$((failures + 1))
  fi
fi

if [ "$TEST_GROUP" = "all" ] || [ "$TEST_GROUP" = "inference" ]; then
  run_suite \
    "inference-orchestrator-unit" \
    "$ROOT_DIR/python-ecosystem/inference-orchestrator" \
    "tests"
  run_suite \
    "inference-orchestrator-integration" \
    "$ROOT_DIR/python-ecosystem/inference-orchestrator" \
    "integration"
  run_suite \
    "review-quality-tools" \
    "$ROOT_DIR" \
    "tools/review_quality/tests"
fi

if [ "$failures" -ne 0 ]; then
  echo "$failures Python test suite(s) or validation gate(s) failed"
  exit 1
fi

echo "Python test group '$TEST_GROUP' passed"
