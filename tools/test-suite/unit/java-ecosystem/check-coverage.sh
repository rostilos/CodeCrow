#!/bin/bash

# =============================================================================
# CodeCrow Java Test Coverage Checker (JaCoCo)
# =============================================================================
# Runs tests with JaCoCo coverage and displays results in CLI
# Usage: ./check-coverage.sh [module-name]
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

# Create test-run directory
mkdir -p "$TEST_RUN_DIR"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           CodeCrow Java Coverage Checker (JaCoCo)              ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Define modules to check
LIBS_MODULES=(
    "libs/core"
    "libs/security"
    "libs/email"
    "libs/vcs-client"
    "libs/rag-engine"
    "libs/analysis-engine"
)

# Coverage report file
COVERAGE_FILE="$TEST_RUN_DIR/coverage-report-$TIMESTAMP.txt"
LATEST_COVERAGE="$TEST_RUN_DIR/coverage-report.txt"

echo "CodeCrow Java Test Coverage Report" > "$COVERAGE_FILE"
echo "===================================" >> "$COVERAGE_FILE"
echo "Timestamp: $TIMESTAMP" >> "$COVERAGE_FILE"
echo "" >> "$COVERAGE_FILE"

# Function to extract coverage from JaCoCo HTML report
extract_coverage() {
    local jacoco_html="$1"
    
    if [ -f "$jacoco_html" ]; then
        # Extract instruction coverage percentage
        local coverage=$(grep -oP 'Total</td><td class="bar">[^<]+</td><td class="ctr2">\K\d+%' "$jacoco_html" 2>/dev/null | head -1)
        if [ -n "$coverage" ]; then
            echo "$coverage"
        else
            echo "N/A"
        fi
    else
        echo "N/A"
    fi
}

# Function to extract detailed coverage
extract_detailed_coverage() {
    local jacoco_html="$1"
    
    if [ -f "$jacoco_html" ]; then
        # Extract from Total row: instructions, branches, lines, methods
        local line=$(grep -o 'Total</td>.*</tr>' "$jacoco_html" 2>/dev/null | head -1)
        
        # Extract percentages (ctr2 class contains percentages)
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

check_module_coverage() {
    local module=$1
    local module_name=$(basename "$module")
    
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Checking coverage:${NC} $module"
    
    cd "$JAVA_ECOSYSTEM_DIR"
    
    # Run tests with JaCoCo (suppress test output including expected exception stack traces)
    echo -e "  Running tests with coverage..."
    if mvn test jacoco:report -pl "$module" -q -Dsurefire.useFile=true -DtrimStackTrace=true 2>&1 | grep -v "^\s*at " | grep -v "^java\." | grep -v "^org\.junit" | grep -v "^org\.mockito" | grep -v "^org\.assertj" | grep -v "^org\.apache\.maven" | grep -v "^\[INFO\]" | grep -v "^\[ERROR\]" | grep -v "^\[WARNING\]" > /dev/null 2>&1; then
        local jacoco_html="$JAVA_ECOSYSTEM_DIR/$module/target/site/jacoco/index.html"
        
        local coverage=$(extract_coverage "$jacoco_html")
        local details=$(extract_detailed_coverage "$jacoco_html")
        
        # Color code coverage
        local coverage_num=$(echo "$coverage" | tr -d '%')
        if [ "$coverage" != "N/A" ] && [ -n "$coverage_num" ]; then
            if [ "$coverage_num" -ge 80 ]; then
                echo -e "  ${GREEN}✓ Coverage: $coverage${NC}"
                echo -e "    ${CYAN}$details${NC}"
            elif [ "$coverage_num" -ge 60 ]; then
                echo -e "  ${YELLOW}◐ Coverage: $coverage${NC}"
                echo -e "    ${CYAN}$details${NC}"
            else
                echo -e "  ${RED}✗ Coverage: $coverage${NC}"
                echo -e "    ${CYAN}$details${NC}"
            fi
        else
            echo -e "  ${YELLOW}? Coverage: N/A${NC}"
        fi
        
        echo "$module: $coverage ($details)" >> "$COVERAGE_FILE"
    else
        echo -e "  ${RED}✗ Tests failed${NC}"
        echo "$module: FAILED (tests did not pass)" >> "$COVERAGE_FILE"
    fi
    
    echo ""
}

# Print coverage bar
print_coverage_bar() {
    local percent=$1
    local width=40
    local filled=$((percent * width / 100))
    local empty=$((width - filled))
    
    printf "["
    if [ "$percent" -ge 80 ]; then
        printf "${GREEN}"
    elif [ "$percent" -ge 60 ]; then
        printf "${YELLOW}"
    else
        printf "${RED}"
    fi
    
    for ((i=0; i<filled; i++)); do printf "█"; done
    printf "${NC}"
    for ((i=0; i<empty; i++)); do printf "░"; done
    printf "] %3d%%\n" "$percent"
}

# Check if specific module requested
if [ -n "$1" ]; then
    MODULE_FOUND=false
    for module in "${LIBS_MODULES[@]}"; do
        if [[ "$module" == *"$1"* ]]; then
            check_module_coverage "$module"
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
    # Check all modules
    echo -e "${CYAN}Checking coverage for all modules...${NC}"
    echo ""
    
    declare -A COVERAGES
    
    for module in "${LIBS_MODULES[@]}"; do
        check_module_coverage "$module"
    done
fi

# Copy to latest coverage file
cp "$COVERAGE_FILE" "$LATEST_COVERAGE"

# Print summary
echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                    COVERAGE SUMMARY                            ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Coverage Legend:${NC}"
echo -e "  ${GREEN}■${NC} 80%+ - Excellent"
echo -e "  ${YELLOW}■${NC} 60-79% - Good"
echo -e "  ${RED}■${NC} <60% - Needs improvement"
echo ""
echo -e "${YELLOW}Report saved to:${NC} $COVERAGE_FILE"
echo ""
echo -e "${CYAN}To view detailed HTML report for a module:${NC}"
echo -e "  open java-ecosystem/libs/[module]/target/site/jacoco/index.html"

exit 0
