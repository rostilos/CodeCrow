#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Check Python unit-test coverage for IO and RAG pipeline
#
# Usage:
#   ./check-coverage.sh              # both packages
#   ./check-coverage.sh io           # inference-orchestrator only
#   ./check-coverage.sh rag          # rag-pipeline only
#   ./check-coverage.sh --threshold 80   # fail if below 80%
#   ./check-coverage.sh --html       # generate HTML reports
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
TARGET="all"
THRESHOLD=80
HTML=false

# ── Parse arguments ───────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        io|inference-orchestrator)  TARGET="io";        shift ;;
        rag|rag-pipeline)           TARGET="rag";       shift ;;
        --threshold|-t)             THRESHOLD="$2";     shift 2 ;;
        --html)                     HTML=true;          shift ;;
        *)                          shift ;;
    esac
done

# ── Resolve Python ────────────────────────────────────────────
if [[ -f "$VENV_DIR/bin/python" ]]; then
    PYTHON="$VENV_DIR/bin/python"
else
    PYTHON="$(command -v python3 || command -v python)"
fi

echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║   CodeCrow Python Coverage Check                    ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Python:     ${YELLOW}$($PYTHON --version 2>&1)${NC}"
echo -e "  Target:     ${YELLOW}${TARGET}${NC}"
echo -e "  Threshold:  ${YELLOW}${THRESHOLD}%${NC}"
echo ""

mkdir -p "$RESULTS_DIR"

# ── Coverage runner ───────────────────────────────────────────
OVERALL_PASS=true

check_coverage() {
    local name="$1"
    local dir="$2"
    local log_file="$RESULTS_DIR/${name}_coverage_${TIMESTAMP}.log"

    if [[ ! -d "$dir" ]]; then
        echo -e "  ${RED}✗ $name — directory not found: $dir${NC}"
        OVERALL_PASS=false
        return 1
    fi

    echo -e "${BOLD}── $name ──────────────────────────────────────${NC}"

    local html_flag=""
    if $HTML; then
        local html_dir="$RESULTS_DIR/${name}_htmlcov_${TIMESTAMP}"
        html_flag="--cov-report=html:$html_dir"
    fi

    cd "$dir"

    if ! "$PYTHON" -m pytest tests/ \
        --cov=. \
        --cov-report=term-missing \
        --cov-fail-under="$THRESHOLD" \
        $html_flag \
        --tb=no \
        -q \
        2>&1 | tee "$log_file"; then

        OVERALL_PASS=false
    fi

    # Extract summary line
    local total_line
    total_line=$(grep -E "^TOTAL" "$log_file" || echo "")
    if [[ -n "$total_line" ]]; then
        local pct
        pct=$(echo "$total_line" | grep -oP '\d+%' | tail -1)
        local tests_line
        tests_line=$(grep -oP '\d+ passed' "$log_file" | head -1 || echo "0 passed")

        if [[ "${pct//%/}" -ge "$THRESHOLD" ]]; then
            echo -e "  ${GREEN}✓ $name — ${pct} coverage, ${tests_line} (threshold: ${THRESHOLD}%)${NC}"
        else
            echo -e "  ${RED}✗ $name — ${pct} coverage, ${tests_line} (threshold: ${THRESHOLD}%)${NC}"
            OVERALL_PASS=false
        fi
    fi

    if $HTML && [[ -d "${html_dir:-}" ]]; then
        echo -e "  HTML report: ${YELLOW}${html_dir}/index.html${NC}"
    fi

    echo ""
    cd - > /dev/null
}

# ── Execute ───────────────────────────────────────────────────
case "$TARGET" in
    all)
        check_coverage "inference-orchestrator" "$PYTHON_ROOT/inference-orchestrator"
        check_coverage "rag-pipeline"           "$PYTHON_ROOT/rag-pipeline"
        ;;
    io)
        check_coverage "inference-orchestrator" "$PYTHON_ROOT/inference-orchestrator"
        ;;
    rag)
        check_coverage "rag-pipeline"           "$PYTHON_ROOT/rag-pipeline"
        ;;
esac

# ── Summary ───────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}──────────────────────────────────────────────────────${NC}"
if $OVERALL_PASS; then
    echo -e "  ${GREEN}${BOLD}ALL PACKAGES MEET ${THRESHOLD}% COVERAGE THRESHOLD${NC}"
    echo ""
    exit 0
else
    echo -e "  ${RED}${BOLD}COVERAGE CHECK FAILED (threshold: ${THRESHOLD}%)${NC}"
    echo ""
    exit 1
fi
