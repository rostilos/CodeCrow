#!/bin/bash

# =============================================================================
# CodeCrow Java Unit Tests Runner
# =============================================================================
# Runs all unit tests and generates summary reports
# Usage: ./run-tests.sh [module-name]
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
NC='\033[0m' # No Color

# Create test-run directory
mkdir -p "$TEST_RUN_DIR"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           CodeCrow Java Unit Tests Runner                      ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Timestamp:${NC} $TIMESTAMP"
echo -e "${YELLOW}Java Ecosystem:${NC} $JAVA_ECOSYSTEM_DIR"
echo ""

# Define modules to test
LIBS_MODULES=(
    "libs/core"
    "libs/security"
    "libs/email"
    "libs/vcs-client"
    "libs/rag-engine"
    "libs/analysis-engine"
)

# Initialize summary file
SUMMARY_FILE="$TEST_RUN_DIR/summary-$TIMESTAMP.txt"
LATEST_SUMMARY="$TEST_RUN_DIR/summary.txt"

echo "CodeCrow Java Unit Tests Summary" > "$SUMMARY_FILE"
echo "================================" >> "$SUMMARY_FILE"
echo "Timestamp: $TIMESTAMP" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"

# Counters
TOTAL_TESTS=0
TOTAL_FAILURES=0
TOTAL_ERRORS=0
TOTAL_SKIPPED=0
MODULES_PASSED=0
MODULES_FAILED=0

run_module_tests() {
    local module=$1
    local module_name=$(basename "$module")
    local result_file="$TEST_RUN_DIR/${module_name}-results-$TIMESTAMP.txt"
    
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Testing module:${NC} $module"
    
    cd "$JAVA_ECOSYSTEM_DIR"
    
    # Run tests and capture output (suppress stack traces from expected exceptions)
    if mvn test -pl "$module" -q -Dsurefire.useFile=true -DtrimStackTrace=true 2>&1 | grep -v "^\s*at " | grep -v "^java\." | grep -v "^org\.junit" | grep -v "^org\.mockito" | grep -v "^org\.assertj" | grep -v "^org\.apache\.maven" > "$result_file" 2>&1; then
        echo -e "${GREEN}✓ $module - PASSED${NC}"
        ((MODULES_PASSED++))
        MODULE_STATUS="PASSED"
    else
        echo -e "${RED}✗ $module - FAILED${NC}"
        ((MODULES_FAILED++))
        MODULE_STATUS="FAILED"
    fi
    
    # Extract test counts from surefire reports
    local surefire_dir="$JAVA_ECOSYSTEM_DIR/$module/target/surefire-reports"
    if [ -d "$surefire_dir" ]; then
        local tests=$(grep -h "tests=" "$surefire_dir"/*.xml 2>/dev/null | grep -oP 'tests="\K\d+' | awk '{s+=$1} END {print s}' || echo "0")
        local failures=$(grep -h "failures=" "$surefire_dir"/*.xml 2>/dev/null | grep -oP 'failures="\K\d+' | awk '{s+=$1} END {print s}' || echo "0")
        local errors=$(grep -h "errors=" "$surefire_dir"/*.xml 2>/dev/null | grep -oP 'errors="\K\d+' | awk '{s+=$1} END {print s}' || echo "0")
        local skipped=$(grep -h "skipped=" "$surefire_dir"/*.xml 2>/dev/null | grep -oP 'skipped="\K\d+' | awk '{s+=$1} END {print s}' || echo "0")
        
        tests=${tests:-0}
        failures=${failures:-0}
        errors=${errors:-0}
        skipped=${skipped:-0}
        
        TOTAL_TESTS=$((TOTAL_TESTS + tests))
        TOTAL_FAILURES=$((TOTAL_FAILURES + failures))
        TOTAL_ERRORS=$((TOTAL_ERRORS + errors))
        TOTAL_SKIPPED=$((TOTAL_SKIPPED + skipped))
        
        echo "  Tests: $tests, Failures: $failures, Errors: $errors, Skipped: $skipped"
        echo "$module: $MODULE_STATUS (Tests: $tests, Failures: $failures, Errors: $errors, Skipped: $skipped)" >> "$SUMMARY_FILE"
    else
        echo "  No test results found"
        echo "$module: $MODULE_STATUS (No test results)" >> "$SUMMARY_FILE"
    fi
    
    echo ""
}

# Check if specific module requested
if [ -n "$1" ]; then
    MODULE_FOUND=false
    for module in "${LIBS_MODULES[@]}"; do
        if [[ "$module" == *"$1"* ]]; then
            run_module_tests "$module"
            MODULE_FOUND=true
            break
        fi
    done
    
    if [ "$MODULE_FOUND" = false ]; then
        echo -e "${RED}Module '$1' not found. Available modules:${NC}"
        for module in "${LIBS_MODULES[@]}"; do
            echo "  - $module"
        done
        exit 1
    fi
else
    # Run all modules
    for module in "${LIBS_MODULES[@]}"; do
        run_module_tests "$module" || true
    done
fi

# Write final summary
echo "" >> "$SUMMARY_FILE"
echo "═══════════════════════════════════════" >> "$SUMMARY_FILE"
echo "TOTAL SUMMARY" >> "$SUMMARY_FILE"
echo "═══════════════════════════════════════" >> "$SUMMARY_FILE"
echo "Total Tests:    $TOTAL_TESTS" >> "$SUMMARY_FILE"
echo "Total Failures: $TOTAL_FAILURES" >> "$SUMMARY_FILE"
echo "Total Errors:   $TOTAL_ERRORS" >> "$SUMMARY_FILE"
echo "Total Skipped:  $TOTAL_SKIPPED" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"
echo "Modules Passed: $MODULES_PASSED" >> "$SUMMARY_FILE"
echo "Modules Failed: $MODULES_FAILED" >> "$SUMMARY_FILE"

# Copy to latest summary
cp "$SUMMARY_FILE" "$LATEST_SUMMARY"

# Print final summary
echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                       TEST SUMMARY                             ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Total Tests:    ${YELLOW}$TOTAL_TESTS${NC}"
echo -e "Total Failures: ${RED}$TOTAL_FAILURES${NC}"
echo -e "Total Errors:   ${RED}$TOTAL_ERRORS${NC}"
echo -e "Total Skipped:  ${YELLOW}$TOTAL_SKIPPED${NC}"
echo ""
echo -e "Modules Passed: ${GREEN}$MODULES_PASSED${NC}"
echo -e "Modules Failed: ${RED}$MODULES_FAILED${NC}"
echo ""
echo -e "${YELLOW}Summary saved to:${NC} $SUMMARY_FILE"

# Exit with error if any failures
if [ $TOTAL_FAILURES -gt 0 ] || [ $TOTAL_ERRORS -gt 0 ]; then
    exit 1
fi

exit 0
