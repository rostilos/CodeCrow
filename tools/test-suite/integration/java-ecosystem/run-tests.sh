#!/bin/bash

# =============================================================================
# CodeCrow Java Integration Tests Runner
# =============================================================================
# Runs all integration tests (src/it/java) via Maven Failsafe plugin
# and generates summary reports.
#
# Prerequisites:
#   - Docker running (Testcontainers spins up PostgreSQL + Redis containers)
#   - Maven installed
#   - java-ecosystem built: mvn install -DskipTests (at least once)
#
# Usage:
#   ./run-tests.sh                  # Run all modules
#   ./run-tests.sh core             # Run a single module
#   ./run-tests.sh --parallel       # Run all modules in parallel
#   ./run-tests.sh --failfast       # Stop on first module failure
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

# Options
PARALLEL=false
FAILFAST=false
SINGLE_MODULE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --parallel)
            PARALLEL=true
            shift
            ;;
        --failfast)
            FAILFAST=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [options] [module-name]"
            echo ""
            echo "Options:"
            echo "  --parallel   Run all modules in parallel (faster but noisier output)"
            echo "  --failfast   Stop on first module failure"
            echo "  -h, --help   Show this help message"
            echo ""
            echo "Modules:"
            echo "  libs:     core, analysis-engine, vcs-client, commit-graph,"
            echo "            file-content, email, rag-engine, security, queue"
            echo "  services: web-server, pipeline-agent"
            echo ""
            echo "Examples:"
            echo "  $0                          # Run all ITs"
            echo "  $0 core                     # Run only libs/core ITs"
            echo "  $0 web-server               # Run only web-server ITs"
            echo "  $0 --parallel               # Run all in parallel"
            echo "  $0 --failfast analysis-engine"
            exit 0
            ;;
        *)
            SINGLE_MODULE="$1"
            shift
            ;;
    esac
done

# Create test-run directory
mkdir -p "$TEST_RUN_DIR"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       CodeCrow Java Integration Tests Runner (Failsafe)       ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Timestamp:${NC}      $TIMESTAMP"
echo -e "${YELLOW}Java Ecosystem:${NC} $JAVA_ECOSYSTEM_DIR"
echo -e "${YELLOW}Parallel:${NC}       $PARALLEL"
echo -e "${YELLOW}Failfast:${NC}       $FAILFAST"
echo ""

# ── Pre-flight checks ───────────────────────────────────────────────────────

# Docker must be running for Testcontainers
if ! docker info >/dev/null 2>&1; then
    echo -e "${RED}ERROR: Docker is not running. Integration tests require Docker for Testcontainers.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker is running${NC}"

# Check java
if ! command -v java >/dev/null 2>&1; then
    echo -e "${RED}ERROR: java not found on PATH${NC}"
    exit 1
fi
JAVA_VERSION=$(java -version 2>&1 | head -1)
echo -e "${GREEN}✓ Java: $JAVA_VERSION${NC}"

# Check maven
if ! command -v mvn >/dev/null 2>&1; then
    echo -e "${RED}ERROR: mvn not found on PATH${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Maven found${NC}"
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

# ── Summary tracking ────────────────────────────────────────────────────────

SUMMARY_FILE="$TEST_RUN_DIR/it-summary-$TIMESTAMP.txt"
LATEST_SUMMARY="$TEST_RUN_DIR/it-summary.txt"

echo "CodeCrow Java Integration Tests Summary" > "$SUMMARY_FILE"
echo "========================================" >> "$SUMMARY_FILE"
echo "Timestamp: $TIMESTAMP" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"

TOTAL_TESTS=0
TOTAL_FAILURES=0
TOTAL_ERRORS=0
TOTAL_SKIPPED=0
MODULES_PASSED=0
MODULES_FAILED=0
MODULES_SKIPPED_NO_IT=0

# ── Run function ─────────────────────────────────────────────────────────────

run_module_it() {
    local module=$1
    local module_name=$(basename "$module")
    local result_file="$TEST_RUN_DIR/${module_name}-it-results-$TIMESTAMP.txt"

    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Testing module (IT):${NC} $module"

    # Check if module has integration tests
    local it_dir="$JAVA_ECOSYSTEM_DIR/$module/src/it/java"
    if [ ! -d "$it_dir" ] || [ -z "$(find "$it_dir" -name '*IT.java' 2>/dev/null)" ]; then
        echo -e "  ${CYAN}⊘ No integration tests found — skipping${NC}"
        echo "$module: SKIPPED (no integration tests)" >> "$SUMMARY_FILE"
        ((MODULES_SKIPPED_NO_IT++))
        echo ""
        return 0
    fi

    local it_count=$(find "$it_dir" -name '*IT.java' | wc -l)
    local test_count=$(grep -rh "@Test" "$it_dir" --include="*.java" 2>/dev/null | wc -l)
    echo -e "  ${CYAN}Found $it_count IT files, ~$test_count @Test methods${NC}"

    cd "$JAVA_ECOSYSTEM_DIR"

    # Run Failsafe (integration-test + verify)
    local MVN_CMD="mvn verify -pl $module -Dsurefire.skip=true -DtrimStackTrace=true"

    if $PARALLEL; then
        MVN_CMD="$MVN_CMD -T 1C"
    fi

    local start_time=$(date +%s)

    if eval "$MVN_CMD" > "$result_file" 2>&1; then
        local end_time=$(date +%s)
        local duration=$((end_time - start_time))
        echo -e "  ${GREEN}✓ $module — PASSED${NC} (${duration}s)"
        ((MODULES_PASSED++))
        MODULE_STATUS="PASSED"
    else
        local end_time=$(date +%s)
        local duration=$((end_time - start_time))
        echo -e "  ${RED}✗ $module — FAILED${NC} (${duration}s)"
        ((MODULES_FAILED++))
        MODULE_STATUS="FAILED"

        # Show last 20 lines of failure
        echo -e "  ${RED}Last 20 lines of output:${NC}"
        tail -20 "$result_file" | sed 's/^/    /'
    fi

    # Extract test counts from failsafe reports
    local failsafe_dir="$JAVA_ECOSYSTEM_DIR/$module/target/failsafe-reports"
    if [ -d "$failsafe_dir" ]; then
        local tests=$(grep -h 'tests=' "$failsafe_dir"/*.xml 2>/dev/null | grep -oP 'tests="\K\d+' | awk '{s+=$1} END {print s+0}')
        local failures=$(grep -h 'failures=' "$failsafe_dir"/*.xml 2>/dev/null | grep -oP 'failures="\K\d+' | awk '{s+=$1} END {print s+0}')
        local errors=$(grep -h 'errors=' "$failsafe_dir"/*.xml 2>/dev/null | grep -oP 'errors="\K\d+' | awk '{s+=$1} END {print s+0}')
        local skipped=$(grep -h 'skipped=' "$failsafe_dir"/*.xml 2>/dev/null | grep -oP 'skipped="\K\d+' | awk '{s+=$1} END {print s+0}')

        tests=${tests:-0}
        failures=${failures:-0}
        errors=${errors:-0}
        skipped=${skipped:-0}

        TOTAL_TESTS=$((TOTAL_TESTS + tests))
        TOTAL_FAILURES=$((TOTAL_FAILURES + failures))
        TOTAL_ERRORS=$((TOTAL_ERRORS + errors))
        TOTAL_SKIPPED=$((TOTAL_SKIPPED + skipped))

        echo "  Tests: $tests, Failures: $failures, Errors: $errors, Skipped: $skipped"
        echo "$module: $MODULE_STATUS (Tests: $tests, Failures: $failures, Errors: $errors, Skipped: $skipped) [${duration}s]" >> "$SUMMARY_FILE"
    else
        echo "  No failsafe report found"
        echo "$module: $MODULE_STATUS (No failsafe report) [${duration}s]" >> "$SUMMARY_FILE"
    fi

    echo ""

    # Failfast support
    if $FAILFAST && [ "$MODULE_STATUS" = "FAILED" ]; then
        echo -e "${RED}FAILFAST: Stopping after first module failure.${NC}"
        print_summary
        exit 1
    fi
}

# ── Summary printer ──────────────────────────────────────────────────────────

print_summary() {
    echo "" >> "$SUMMARY_FILE"
    echo "═══════════════════════════════════════" >> "$SUMMARY_FILE"
    echo "TOTAL SUMMARY" >> "$SUMMARY_FILE"
    echo "═══════════════════════════════════════" >> "$SUMMARY_FILE"
    echo "Total Tests:    $TOTAL_TESTS" >> "$SUMMARY_FILE"
    echo "Total Failures: $TOTAL_FAILURES" >> "$SUMMARY_FILE"
    echo "Total Errors:   $TOTAL_ERRORS" >> "$SUMMARY_FILE"
    echo "Total Skipped:  $TOTAL_SKIPPED" >> "$SUMMARY_FILE"
    echo "" >> "$SUMMARY_FILE"
    echo "Modules Passed:  $MODULES_PASSED" >> "$SUMMARY_FILE"
    echo "Modules Failed:  $MODULES_FAILED" >> "$SUMMARY_FILE"
    echo "Modules No ITs:  $MODULES_SKIPPED_NO_IT" >> "$SUMMARY_FILE"

    cp "$SUMMARY_FILE" "$LATEST_SUMMARY"

    echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║                  INTEGRATION TEST SUMMARY                      ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "Total Tests:    ${YELLOW}$TOTAL_TESTS${NC}"
    echo -e "Total Failures: $([ $TOTAL_FAILURES -gt 0 ] && echo "${RED}" || echo "${GREEN}")$TOTAL_FAILURES${NC}"
    echo -e "Total Errors:   $([ $TOTAL_ERRORS -gt 0 ] && echo "${RED}" || echo "${GREEN}")$TOTAL_ERRORS${NC}"
    echo -e "Total Skipped:  ${YELLOW}$TOTAL_SKIPPED${NC}"
    echo ""
    echo -e "Modules Passed:  ${GREEN}$MODULES_PASSED${NC}"
    echo -e "Modules Failed:  $([ $MODULES_FAILED -gt 0 ] && echo "${RED}" || echo "${GREEN}")$MODULES_FAILED${NC}"
    echo -e "Modules No ITs:  ${CYAN}$MODULES_SKIPPED_NO_IT${NC}"
    echo ""
    echo -e "${YELLOW}Summary saved to:${NC} $SUMMARY_FILE"
    echo -e "${YELLOW}Per-module logs:${NC}  $TEST_RUN_DIR/*-it-results-$TIMESTAMP.txt"
    echo ""

    # Hint about Docker cleanup
    echo -e "${CYAN}Tip: Testcontainers are marked reusable. To reclaim disk space:${NC}"
    echo -e "  docker rm -f \$(docker ps -aq --filter label=org.testcontainers)"
}

# ── Main ─────────────────────────────────────────────────────────────────────

if [ -n "$SINGLE_MODULE" ]; then
    MODULE_FOUND=false
    for module in "${ALL_MODULES[@]}"; do
        if [[ "$module" == *"$SINGLE_MODULE"* ]]; then
            run_module_it "$module"
            MODULE_FOUND=true
            break
        fi
    done

    if [ "$MODULE_FOUND" = false ]; then
        echo -e "${RED}Module '$SINGLE_MODULE' not found. Available modules:${NC}"
        for module in "${ALL_MODULES[@]}"; do
            echo "  - $module"
        done
        exit 1
    fi
else
    for module in "${ALL_MODULES[@]}"; do
        run_module_it "$module" || true
    done
fi

print_summary

# Exit with error if any failures
if [ $TOTAL_FAILURES -gt 0 ] || [ $TOTAL_ERRORS -gt 0 ] || [ $MODULES_FAILED -gt 0 ]; then
    exit 1
fi

exit 0
