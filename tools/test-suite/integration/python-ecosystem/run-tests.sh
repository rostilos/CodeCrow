#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# run-tests.sh — Run integration tests for Python ecosystem packages
#
# Usage:
#   ./run-tests.sh              # run both packages
#   ./run-tests.sh io           # inference-orchestrator only
#   ./run-tests.sh rag          # rag-pipeline only
#   ./run-tests.sh --parallel   # run both in parallel
#   ./run-tests.sh -v           # verbose output
#   ./run-tests.sh --failfast   # stop on first failure
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
PYTHON_ROOT="$REPO_ROOT/python-ecosystem"
VENV_DIR="$REPO_ROOT/.venv"
LOG_DIR="$SCRIPT_DIR/test-run"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# ── Colours ───────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }

# ── Parse arguments ───────────────────────────────────────────
TARGET="both"
VERBOSE=""
FAILFAST=""
PARALLEL=false
EXTRA_ARGS=()

for arg in "$@"; do
    case "$arg" in
        io|IO)          TARGET="io" ;;
        rag|RAG)        TARGET="rag" ;;
        both|all)       TARGET="both" ;;
        -v|--verbose)   VERBOSE="-v" ;;
        --failfast|-x)  FAILFAST="-x" ;;
        --parallel|-p)  PARALLEL=true ;;
        *)              EXTRA_ARGS+=("$arg") ;;
    esac
done

# ── Activate venv ─────────────────────────────────────────────
if [[ -f "$VENV_DIR/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    info "Activated venv: $VENV_DIR"
else
    warn "No venv found at $VENV_DIR — using system Python"
fi

mkdir -p "$LOG_DIR"

# ── Test runner ───────────────────────────────────────────────
run_integration_tests() {
    local package="$1"
    local test_dir="$PYTHON_ROOT/$package/integration"
    local log_file="$LOG_DIR/${package}-integration-${TIMESTAMP}.log"

    if [[ ! -d "$test_dir" ]]; then
        warn "No integration/ directory for $package — skipping"
        return 0
    fi

    info "Running integration tests: $package"
    set +e
    (
        cd "$PYTHON_ROOT/$package"
        python -m pytest integration/ $VERBOSE $FAILFAST --tb=short "${EXTRA_ARGS[@]}" 2>&1
    ) | tee "$log_file"
    local rc=${PIPESTATUS[0]}
    set -e

    if [[ $rc -eq 0 ]]; then
        info "$package integration tests PASSED ✅"
    else
        fail "$package integration tests FAILED ❌ (exit $rc)"
    fi
    info "Log: $log_file"
    return $rc
}

# ── Main ──────────────────────────────────────────────────────
overall_rc=0

if [[ "$PARALLEL" == true && "$TARGET" == "both" ]]; then
    info "Running integration tests in parallel…"
    run_integration_tests "inference-orchestrator" &
    PID_IO=$!
    run_integration_tests "rag-pipeline" &
    PID_RAG=$!

    wait $PID_IO  || overall_rc=1
    wait $PID_RAG || overall_rc=1
else
    case "$TARGET" in
        io)
            run_integration_tests "inference-orchestrator" || overall_rc=1
            ;;
        rag)
            run_integration_tests "rag-pipeline" || overall_rc=1
            ;;
        both)
            run_integration_tests "inference-orchestrator" || overall_rc=1
            run_integration_tests "rag-pipeline" || overall_rc=1
            ;;
    esac
fi

echo ""
if [[ $overall_rc -eq 0 ]]; then
    info "All integration tests passed ✅"
else
    fail "Some integration tests failed ❌"
fi
exit $overall_rc
