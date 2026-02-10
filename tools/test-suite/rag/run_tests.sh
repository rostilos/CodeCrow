#!/bin/bash
#
# RAG Test Suite Runner
#
# Usage:
#   ./run_tests.sh              # Run all tests
#   ./run_tests.sh --quick      # Run quick health check only
#   ./run_tests.sh --search     # Run search tests only
#   ./run_tests.sh --pr         # Run PR context tests only
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}RAG Test Suite${NC}"
echo -e "${GREEN}================================${NC}"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 is required${NC}"
    exit 1
fi

# Check dependencies
if ! python3 -c "import requests" 2>/dev/null; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pip install -r requirements.txt
fi

case "${1:-all}" in
    --quick|-q)
        echo -e "\n${YELLOW}Running quick check...${NC}"
        python3 quickstart.py
        ;;
    --search|-s)
        echo -e "\n${YELLOW}Running search tests...${NC}"
        python3 scripts/test_search.py
        ;;
    --pr|-p)
        echo -e "\n${YELLOW}Running PR context tests...${NC}"
        python3 scripts/test_pr_context.py
        ;;
    --indexing|-i)
        echo -e "\n${YELLOW}Running indexing tests...${NC}"
        python3 scripts/test_indexing.py
        ;;
    --deterministic|-d)
        echo -e "\n${YELLOW}Running deterministic tests...${NC}"
        python3 scripts/test_deterministic.py
        ;;
    --chunking|-c)
        echo -e "\n${YELLOW}Running chunking tests...${NC}"
        python3 scripts/test_chunking.py
        ;;
    all|--all)
        echo -e "\n${YELLOW}Running all tests...${NC}"
        python3 scripts/run_all_tests.py --save-report
        ;;
    --help|-h)
        echo "Usage: ./run_tests.sh [option]"
        echo ""
        echo "Options:"
        echo "  --quick, -q        Run quick health check"
        echo "  --search, -s       Run search tests only"
        echo "  --pr, -p           Run PR context tests only"
        echo "  --indexing, -i     Run indexing tests only"
        echo "  --deterministic, -d Run deterministic tests only"
        echo "  --chunking, -c     Run chunking tests only"
        echo "  --all, all         Run all tests (default)"
        echo "  --help, -h         Show this help"
        ;;
    *)
        echo -e "${RED}Unknown option: $1${NC}"
        echo "Use --help for usage information"
        exit 1
        ;;
esac
