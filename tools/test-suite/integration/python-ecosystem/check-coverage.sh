#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# check-coverage.sh — Integration test coverage for Python packages
#
# Usage:
#   ./check-coverage.sh              # both packages, default 60% threshold
#   ./check-coverage.sh io           # inference-orchestrator only
#   ./check-coverage.sh rag 70       # rag-pipeline with 70% threshold
#   ./check-coverage.sh --html       # generate HTML report
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
PYTHON_ROOT="$REPO_ROOT/python-ecosystem"
VENV_DIR="$REPO_ROOT/.venv"
LOG_DIR="$SCRIPT_DIR/test-run"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }

# ── Parse arguments ───────────────────────────────────────────
TARGET="both"
THRESHOLD=60
HTML_REPORT=false

for arg in "$@"; do
    case "$arg" in
        io|IO)        TARGET="io" ;;
        rag|RAG)      TARGET="rag" ;;
        both|all)     TARGET="both" ;;
        --html)       HTML_REPORT=true ;;
        [0-9]*)       THRESHOLD="$arg" ;;
    esac
done

# ── Activate venv ─────────────────────────────────────────────
if [[ -f "$VENV_DIR/bin/activate" ]]; then
    source "$VENV_DIR/bin/activate"
    info "Activated venv: $VENV_DIR"
else
    warn "No venv found at $VENV_DIR — using system Python"
fi

mkdir -p "$LOG_DIR"

# ── Coverage runner ───────────────────────────────────────────
SRC_MAP_IO="src"
SRC_MAP_RAG="src/rag_pipeline"

check_coverage() {
    local package="$1"
    local src_dir="$2"
    local test_dir="$PYTHON_ROOT/$package/integration"
    local log_file="$LOG_DIR/${package}-integration-coverage-${TIMESTAMP}.log"

    if [[ ! -d "$test_dir" ]]; then
        warn "No integration/ directory for $package — skipping"
        return 0
    fi

    info "Checking integration coverage: $package (threshold: ${THRESHOLD}%)"

    local html_args=""
    if [[ "$HTML_REPORT" == true ]]; then
        html_args="--cov-report=html:$LOG_DIR/${package}-integration-htmlcov"
    fi

    set +e
    (
        cd "$PYTHON_ROOT/$package"
        python -m pytest integration/ \
            --cov="$src_dir" \
            --cov-report=term-missing \
            --cov-fail-under="$THRESHOLD" \
            $html_args \
            --tb=short -q 2>&1
    ) | tee "$log_file"
    local rc=${PIPESTATUS[0]}
    set -e

    if [[ $rc -eq 0 ]]; then
        info "$package integration coverage ≥ ${THRESHOLD}% ✅"
    else
        fail "$package integration coverage < ${THRESHOLD}% or tests failed ❌"
    fi
    info "Log: $log_file"
    return $rc
}

# ── Main ──────────────────────────────────────────────────────
overall_rc=0

case "$TARGET" in
    io)
        check_coverage "inference-orchestrator" "$SRC_MAP_IO" || overall_rc=1
        ;;
    rag)
        check_coverage "rag-pipeline" "$SRC_MAP_RAG" || overall_rc=1
        ;;
    both)
        check_coverage "inference-orchestrator" "$SRC_MAP_IO" || overall_rc=1
        check_coverage "rag-pipeline" "$SRC_MAP_RAG" || overall_rc=1
        ;;
esac

echo ""
if [[ $overall_rc -eq 0 ]]; then
    info "All integration coverage checks passed ✅"
else
    fail "Some coverage checks failed ❌"
fi
exit $overall_rc
