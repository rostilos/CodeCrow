"""
RAG Test Suite Configuration

Configure API endpoints, credentials, and test parameters.
"""
import os
from pathlib import Path

# =============================================================================
# API Configuration
# =============================================================================

# RAG Pipeline API URL
# Options:
#   - http://localhost:8001 (local development)
#   - http://rag-pipeline:8001 (Docker network)
#   - http://your-server:8001 (remote)
RAG_API_URL = os.environ.get("RAG_API_URL", "http://localhost:8001")

# Qdrant Vector Store URL (for direct inspection)
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")

# API timeout settings (seconds)
API_TIMEOUT = int(os.environ.get("RAG_API_TIMEOUT", "60"))
INDEXING_TIMEOUT = int(os.environ.get("RAG_INDEXING_TIMEOUT", "300"))

# =============================================================================
# Test Data Configuration
# =============================================================================

# Base paths
BASE_DIR = Path(__file__).parent
FIXTURES_DIR = BASE_DIR / "fixtures"
SAMPLE_REPO_DIR = FIXTURES_DIR / "sample_repo"
PR_SCENARIOS_DIR = FIXTURES_DIR / "pr_scenarios"
EXPECTED_RESULTS_DIR = FIXTURES_DIR / "expected_results"
REPORTS_DIR = BASE_DIR / "reports"

# Test workspace/project identifiers
TEST_WORKSPACE = "rag-test"
TEST_PROJECT = "sample-repo"
TEST_BRANCH = "main"

# =============================================================================
# Indexing Configuration
# =============================================================================

# Exclude patterns for test repository
TEST_EXCLUDE_PATTERNS = [
    ".git/**",
    "__pycache__/**",
    "*.pyc",
    ".venv/**",
    "node_modules/**",
]

# =============================================================================
# Search Configuration
# =============================================================================

# Default search parameters
DEFAULT_TOP_K = 10
DEFAULT_MIN_RELEVANCE_SCORE = 0.7

# Instruction types for testing
INSTRUCTION_TYPES = ["general", "dependency", "logic", "impact"]

# =============================================================================
# Test Thresholds
# =============================================================================

# Minimum acceptable scores for test validation
THRESHOLDS = {
    "min_chunks_per_file": 1,
    "max_chunks_per_file": 20,
    "min_semantic_names_ratio": 0.5,  # % of chunks with semantic_names
    "min_search_relevance": 0.65,
    "min_pr_context_results": 1,
    "max_response_time_ms": 5000,
}

# =============================================================================
# Logging Configuration
# =============================================================================

LOG_LEVEL = os.environ.get("RAG_TEST_LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# =============================================================================
# Report Configuration
# =============================================================================

REPORT_FORMAT = "both"  # "json", "html", or "both"
SAVE_RAW_RESPONSES = True  # Save full API responses for debugging
