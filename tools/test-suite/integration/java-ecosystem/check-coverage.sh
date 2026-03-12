#!/bin/bash

# =============================================================================
# CodeCrow Java Integration Test Coverage Checker (JaCoCo)
# =============================================================================
# Runs integration tests with JaCoCo coverage and displays results in CLI.
#
# Prerequisites:
#   - Docker running (Testcontainers)
#   - Maven installed
#
# Usage:
#   ./check-coverage.sh                  # All modules
#   ./check-coverage.sh core             # Single module
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JAVA_ECOSYSTEM_DIR="$(cd "$SCRIPT_DIR/../../../.." && pwd)/java-ecosystem"
TEST_RUN_DIR="$SCRIPT_DIR/test-run"
TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

mkdir -p "$TEST_RUN_DIR"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║      CodeCrow IT Coverage Checker (JaCoCo + Failsafe)         ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Pre-flight ───────────────────────────────────────────────────────────────

if ! docker info >/dev/null 2>&1; then
    echo -e "${RED}ERROR: Docker is not running. Testcontainers need Docker.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker is running${NC}"
echo ""

# ── Module definitions ───────────────────────────────────────────────────────

LIBS_MODULES=(
    "libs/core"
    "libs/analysis-engine"
    "libs/vcs-client"
    "libs/commit-graph"
    "libs/file-content"
    "libs/email"
    "libs/rag-engine"
    "libs/security"
    "libs/queue"
)

SERVICE_MODULES=(
    "services/web-server"
    "services/pipeline-agent"
)

ALL_MODULES=("${LIBS_MODULES[@]}" "${SERVICE_MODULES[@]}")

# Coverage report file
COVERAGE_FILE="$TEST_RUN_DIR/it-coverage-report-$TIMESTAMP.txt"
LATEST_COVERAGE="$TEST_RUN_DIR/it-coverage-report.txt"

echo "CodeCrow Java Integration Test Coverage Report" > "$COVERAGE_FILE"
echo "================================================" >> "$COVERAGE_FILE"
echo "Timestamp: $TIMESTAMP" >> "$COVERAGE_FILE"
echo "" >> "$COVERAGE_FILE"

# ── Helpers ──────────────────────────────────────────────────────────────────

extract_coverage() {
    local jacoco_html="$1"
    if [ -f "$jacoco_html" ]; then
        local coverage=$(grep -oP 'Total</td><td class="bar">[^<]+</td><td class="ctr2">\K\d+%' "$jacoco_html" 2>/dev/null | head -1)
        if [ -n "$coverage" ]; then echo "$coverage"; else echo "N/A"; fi
    else
        echo "N/A"
    fi
}

extract_detailed_coverage() {
    local jacoco_html="$1"
    if [ -f "$jacoco_html" ]; then
        local line=$(grep -o 'Total</td>.*</tr>' "$jacoco_html" 2>/dev/null | head -1)
        local percentages=$(echo "$line" | grep -oP 'ctr2">\K\d+%' | head -4)
        if [ -n "$percentages" ]; then
            local instr=$(echo "$percentages" | sed -n '1p')
            local branch=$(echo "$percentages" | sed -n '2p')
            echo "Instructions: $instr, Branches: $branch"
        else
            echo "No coverage data"
        fi
    else
        echo "No JaCoCo report"
    fi
}

print_coverage_bar() {
    local percent=$1
    local width=40
    local filled=$((percent * width / 100))
    local empty=$((width - filled))

    printf "  ["
    if [ "$percent" -ge 80 ]; then printf "${GREEN}";
    elif [ "$percent" -ge 60 ]; then printf "${YELLOW}";
    else printf "${RED}"; fi

    for ((i=0; i<filled; i++)); do printf "█"; done
    printf "${NC}"
    for ((i=0; i<empty; i++)); do printf "░"; done
    printf "] %3d%%\n" "$percent"
}

# ── Per-module check ─────────────────────────────────────────────────────────

check_module_coverage() {
    local module=$1
    local module_name=$(basename "$module")

    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Checking IT coverage:${NC} $module"

    # Check if module has any tests at all (unit tests or integration tests)
    local it_dir="$JAVA_ECOSYSTEM_DIR/$module/src/it/java"
    local test_dir="$JAVA_ECOSYSTEM_DIR/$module/src/test/java"
    local has_its=false
    local has_uts=false
    [ -d "$it_dir" ] && [ -n "$(find "$it_dir" -name '*IT.java' -o -name '*Test.java' 2>/dev/null)" ] && has_its=true
    [ -d "$test_dir" ] && [ -n "$(find "$test_dir" -name '*Test.java' -o -name '*Tests.java' 2>/dev/null)" ] && has_uts=true

    if [ "$has_its" = false ] && [ "$has_uts" = false ]; then
        echo -e "  ${CYAN}⊘ No tests at all — skipping${NC}"
        echo "$module: SKIPPED (no tests)" >> "$COVERAGE_FILE"
        echo ""
        return 0
    fi

    if [ "$has_its" = false ]; then
        echo -e "  ${CYAN}ℹ No integration tests — measuring unit test coverage only${NC}"
    fi

    cd "$JAVA_ECOSYSTEM_DIR"

    echo -e "  Running integration tests with coverage..."

    # Run Failsafe + JaCoCo (JaCoCo agent is configured in parent POM)
    # Use -Dmaven.test.failure.ignore to ensure JaCoCo report is generated even if some tests fail
    if mvn verify jacoco:report -pl "$module" \
        -DtrimStackTrace=true \
        -Dmaven.test.failure.ignore=true \
        -q > "$TEST_RUN_DIR/${module_name}-it-coverage-$TIMESTAMP.log" 2>&1; then

        # JaCoCo reports go to target/site/jacoco by default
        local jacoco_html="$JAVA_ECOSYSTEM_DIR/$module/target/site/jacoco/index.html"
        # Failsafe sometimes writes to a different path
        local jacoco_it_html="$JAVA_ECOSYSTEM_DIR/$module/target/site/jacoco-it/index.html"

        local report_html="$jacoco_html"
        [ -f "$jacoco_it_html" ] && report_html="$jacoco_it_html"

        local coverage=$(extract_coverage "$report_html")
        local details=$(extract_detailed_coverage "$report_html")

        local coverage_num=$(echo "$coverage" | tr -d '%')
        if [ "$coverage" != "N/A" ] && [ -n "$coverage_num" ]; then
            if [ "$coverage_num" -ge 80 ]; then
                echo -e "  ${GREEN}✓ Coverage: $coverage${NC}"
            elif [ "$coverage_num" -ge 60 ]; then
                echo -e "  ${YELLOW}◐ Coverage: $coverage${NC}"
            else
                echo -e "  ${RED}✗ Coverage: $coverage${NC}"
            fi
            echo -e "    ${CYAN}$details${NC}"
            print_coverage_bar "$coverage_num"
        else
            echo -e "  ${YELLOW}? Coverage: N/A (report not generated)${NC}"
        fi

        echo "$module: $coverage ($details)" >> "$COVERAGE_FILE"
    else
        echo -e "  ${RED}✗ Tests failed — coverage not available${NC}"
        echo -e "  ${CYAN}See log: $TEST_RUN_DIR/${module_name}-it-coverage-$TIMESTAMP.log${NC}"
        echo "$module: FAILED (tests did not pass)" >> "$COVERAGE_FILE"
    fi

    echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────

if [ -n "$1" ]; then
    MODULE_FOUND=false
    for module in "${ALL_MODULES[@]}"; do
        if [[ "$module" == *"$1"* ]]; then
            check_module_coverage "$module"
            MODULE_FOUND=true
            break
        fi
    done

    if [ "$MODULE_FOUND" = false ]; then
        echo -e "${RED}Module '$1' not found. Available modules:${NC}"
        for module in "${ALL_MODULES[@]}"; do
            echo "  - $module"
        done
        exit 1
    fi
else
    echo -e "${CYAN}Checking coverage for all modules...${NC}"
    echo ""
    for module in "${ALL_MODULES[@]}"; do
        check_module_coverage "$module"
    done
fi

cp "$COVERAGE_FILE" "$LATEST_COVERAGE"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║              IT COVERAGE SUMMARY                               ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Coverage Legend:${NC}"
echo -e "  ${GREEN}■${NC} 80%+   Excellent"
echo -e "  ${YELLOW}■${NC} 60-79% Good"
echo -e "  ${RED}■${NC} <60%   Needs improvement"
echo ""
echo -e "${YELLOW}Report saved to:${NC} $COVERAGE_FILE"
echo ""
echo -e "${CYAN}To view detailed HTML report for a module:${NC}"
echo -e "  open java-ecosystem/libs/[module]/target/site/jacoco/index.html"

exit 0
