#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${COVERAGE_BOOTSTRAP_PYTHON:-}" ]]; then
  PYTHON_BIN="$COVERAGE_BOOTSTRAP_PYTHON"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN="python3.11"
else
  PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/repository_coverage.py" "$@"
