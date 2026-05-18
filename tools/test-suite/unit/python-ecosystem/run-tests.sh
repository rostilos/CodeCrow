#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Run Python unit tests for inference-orchestrator and rag-pipeline
#
# Usage:
#   ./run-tests.sh                  # run both packages
#   ./run-tests.sh io               # inference-orchestrator only
#   ./run-tests.sh rag              # rag-pipeline only
#   ./run-tests.sh --parallel       # run both in parallel
#   ./run-tests.sh --failfast       # stop on first failure
#   ./run-tests.sh -v               # verbose pytest output
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
PYTHON_ROOT="$REPO_ROOT/python-ecosystem"
VENV_DIR="$REPO_ROOT/.venv"
RESULTS_DIR="$SCRIPT_DIR/test-run"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# ── Colours ───────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Defaults ──────────────────────────────────────────────────
TARGET="all"          # all | io | rag
PARALLEL=false
FAILFAST=""
VERBOSE=""
EXTRA_ARGS=()

# ── Parse arguments ───────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        io|inference-orchestrator)  TARGET="io";       shift ;;
        rag|rag-pipeline)           TARGET="rag";      shift ;;
        --parallel|-p)              PARALLEL=true;     shift ;;
        --failfast|-x)              FAILFAST="-x";     shift ;;
        -v|--verbose)               VERBOSE="-v";      shift ;;
        -vv)                        VERBOSE="-vv";     shift ;;
        *)                          EXTRA_ARGS+=("$1"); shift ;;
    esac
done

# ── Resolve Python ────────────────────────────────────────────
if [[ -f "$VENV_DIR/bin/python" ]]; then
    PYTHON="$VENV_DIR/bin/python"
else
    PYTHON="$(command -v python3 || command -v python)"
fi

echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║   CodeCrow Python Unit Tests                        ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Python:     ${YELLOW}$($PYTHON --version 2>&1)${NC}"
echo -e "  Target:     ${YELLOW}${TARGET}${NC}"
echo -e "  Timestamp:  ${YELLOW}${TIMESTAMP}${NC}"
echo ""

mkdir -p "$RESULTS_DIR"

# ── Test runner ───────────────────────────────────────────────
run_tests() {
    local name="$1"
    local dir="$2"
    local log_file="$RESULTS_DIR/${name}_${TIMESTAMP}.log"

    if [[ ! -d "$dir" ]]; then
        echo -e "  ${RED}✗ $name — directory not found: $dir${NC}"
        return 1
    fi

    echo -e "${BOLD}── $name ──────────────────────────────────────${NC}"
    local start_time=$SECONDS

    if "$PYTHON" -m pytest "$dir/tests/" \
        $FAILFAST $VERBOSE \
        --tb=short \
        -q \
        "${EXTRA_ARGS[@]}" \
        2>&1 | tee "$log_file"; then

        local elapsed=$(( SECONDS - start_time ))
        local count
        count=$(grep -oP '\d+ passed' "$log_file" | head -1 || echo "? passed")
        echo -e "  ${GREEN}✓ $name — ${count} (${elapsed}s)${NC}"
        echo ""
        return 0
    else
        local elapsed=$(( SECONDS - start_time ))
        echo -e "  ${RED}✗ $name — FAILURES detected (${elapsed}s)${NC}"
        echo ""
        return 1
    fi
}

# ── Execute ───────────────────────────────────────────────────
FAIL_COUNT=0

run_package() {
    local pkg="$1"
    case "$pkg" in
        io)  run_tests "inference-orchestrator" "$PYTHON_ROOT/inference-orchestrator" || ((FAIL_COUNT++)) ;;
        rag) run_tests "rag-pipeline"           "$PYTHON_ROOT/rag-pipeline"           || ((FAIL_COUNT++)) ;;
    esac
}

if [[ "$TARGET" == "all" ]]; then
    if $PARALLEL; then
        echo -e "${YELLOW}Running both packages in parallel...${NC}"
        echo ""
        run_package io &
        PID_IO=$!
        run_package rag &
        PID_RAG=$!
        wait $PID_IO || ((FAIL_COUNT++))
        wait $PID_RAG || ((FAIL_COUNT++))
    else
        run_package io
        run_package rag
    fi
elif [[ "$TARGET" == "io" ]]; then
    run_package io
elif [[ "$TARGET" == "rag" ]]; then
    run_package rag
fi

# ── Summary ───────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}──────────────────────────────────────────────────────${NC}"
if [[ $FAIL_COUNT -eq 0 ]]; then
    echo -e "  ${GREEN}${BOLD}ALL TESTS PASSED${NC}"
else
    echo -e "  ${RED}${BOLD}${FAIL_COUNT} PACKAGE(S) HAD FAILURES${NC}"
fi
echo -e "  Logs: ${YELLOW}${RESULTS_DIR}/${NC}"
echo ""

exit $FAIL_COUNT
