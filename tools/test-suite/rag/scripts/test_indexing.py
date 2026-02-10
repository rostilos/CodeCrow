#!/usr/bin/env python3
"""
Test Indexing - Index sample repository and validate results.

Tests:
1. Full repository indexing
2. Chunk count validation
3. AST metadata extraction
4. Tree-sitter parsing verification
"""
import sys
import logging
import argparse
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    SAMPLE_REPO_DIR, TEST_WORKSPACE, TEST_PROJECT, TEST_BRANCH,
    TEST_EXCLUDE_PATTERNS, THRESHOLDS, LOG_LEVEL, LOG_FORMAT
)
from utils.api_client import get_client, RAGAPIClient
from utils.result_analyzer import ResultAnalyzer

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


class IndexingTest:
    """Test suite for RAG indexing functionality."""
    
    def __init__(self, client: RAGAPIClient, repo_path: str):
        self.client = client
        self.repo_path = repo_path
        self.workspace = TEST_WORKSPACE
        self.project = TEST_PROJECT
        self.branch = TEST_BRANCH
        self.analyzer = ResultAnalyzer()
    
    def run_all_tests(self, reindex: bool = False) -> dict:
        """
        Run all indexing tests.
        
        Args:
            reindex: Force reindex even if data exists
            
        Returns:
            Dict with test results
        """
        results = {
            "passed": 0,
            "failed": 0,
            "tests": []
        }
        
        # Test 1: Health check
        test_result = self._test_health_check()
        results["tests"].append(test_result)
        if test_result["passed"]:
            results["passed"] += 1
        else:
            results["failed"] += 1
            return results  # Can't continue without API
        
        # Test 2: Check existing index or reindex
        if reindex:
            test_result = self._test_delete_index()
            results["tests"].append(test_result)
            if test_result["passed"]:
                results["passed"] += 1
            else:
                results["failed"] += 1
        
        # Test 3: Index repository
        test_result = self._test_index_repository()
        results["tests"].append(test_result)
        if test_result["passed"]:
            results["passed"] += 1
        else:
            results["failed"] += 1
            return results  # Can't continue without index
        
        # Test 4: Validate index stats
        test_result = self._test_index_stats()
        results["tests"].append(test_result)
        if test_result["passed"]:
            results["passed"] += 1
        else:
            results["failed"] += 1
        
        # Test 5: Validate chunk metadata
        test_result = self._test_chunk_metadata()
        results["tests"].append(test_result)
        if test_result["passed"]:
            results["passed"] += 1
        else:
            results["failed"] += 1
        
        return results
    
    def _test_health_check(self) -> dict:
        """Test API health."""
        logger.info("Test: Health Check")
        
        response = self.client.health_check()
        
        passed = response.success and response.data.get("status") == "healthy"
        
        return {
            "name": "Health Check",
            "passed": passed,
            "response_time_ms": response.response_time_ms,
            "details": response.data if response.success else response.error
        }
    
    def _test_delete_index(self) -> dict:
        """Test deleting existing index."""
        logger.info("Test: Delete Index")
        
        response = self.client.delete_branch(self.workspace, self.project, self.branch)
        
        # Success or "not found" both count as pass
        passed = response.success or "not found" in str(response.data).lower()
        
        return {
            "name": "Delete Index",
            "passed": passed,
            "response_time_ms": response.response_time_ms,
            "details": response.data if response.success else response.error
        }
    
    def _test_index_repository(self) -> dict:
        """Test indexing the sample repository."""
        logger.info(f"Test: Index Repository ({self.repo_path})")
        
        response = self.client.index_repository(
            repo_path=self.repo_path,
            workspace=self.workspace,
            project=self.project,
            branch=self.branch,
            commit="test-commit-001",
            exclude_patterns=TEST_EXCLUDE_PATTERNS
        )
        
        if not response.success:
            return {
                "name": "Index Repository",
                "passed": False,
                "response_time_ms": response.response_time_ms,
                "details": response.error
            }
        
        stats = response.data
        document_count = stats.get("document_count", 0)
        chunk_count = stats.get("chunk_count", 0)
        
        # Validate counts
        min_chunks = THRESHOLDS.get("min_chunks_per_file", 1)
        passed = document_count > 0 and chunk_count >= document_count * min_chunks
        
        return {
            "name": "Index Repository",
            "passed": passed,
            "response_time_ms": response.response_time_ms,
            "details": {
                "document_count": document_count,
                "chunk_count": chunk_count,
                "workspace": self.workspace,
                "project": self.project,
                "branch": self.branch
            }
        }
    
    def _test_index_stats(self) -> dict:
        """Test retrieving index statistics."""
        logger.info("Test: Index Stats")
        
        response = self.client.get_branch_stats(self.workspace, self.project, self.branch)
        
        if not response.success:
            return {
                "name": "Index Stats",
                "passed": False,
                "response_time_ms": response.response_time_ms,
                "details": response.error
            }
        
        stats = response.data
        chunk_count = stats.get("chunk_count", stats.get("point_count", 0))
        
        passed = chunk_count > 0
        
        return {
            "name": "Index Stats",
            "passed": passed,
            "response_time_ms": response.response_time_ms,
            "details": stats
        }
    
    def _test_chunk_metadata(self) -> dict:
        """Test that chunks have proper AST metadata."""
        logger.info("Test: Chunk Metadata Quality")
        
        # Use semantic search to get some chunks
        response = self.client.semantic_search(
            query="user service function",
            workspace=self.workspace,
            project=self.project,
            branch=self.branch,
            top_k=20
        )
        
        if not response.success:
            return {
                "name": "Chunk Metadata Quality",
                "passed": False,
                "response_time_ms": response.response_time_ms,
                "details": response.error
            }
        
        results = response.data.get("results", [])
        
        if not results:
            return {
                "name": "Chunk Metadata Quality",
                "passed": False,
                "response_time_ms": response.response_time_ms,
                "details": "No results returned - index may be empty"
            }
        
        # Analyze metadata quality
        with_semantic_names = 0
        with_content_type = 0
        with_language = 0
        
        for result in results:
            metadata = result.get("metadata", {})
            if metadata.get("semantic_names"):
                with_semantic_names += 1
            if metadata.get("content_type"):
                with_content_type += 1
            if metadata.get("language"):
                with_language += 1
        
        total = len(results)
        semantic_ratio = with_semantic_names / total if total > 0 else 0
        
        min_ratio = THRESHOLDS.get("min_semantic_names_ratio", 0.5)
        passed = semantic_ratio >= min_ratio
        
        return {
            "name": "Chunk Metadata Quality",
            "passed": passed,
            "response_time_ms": response.response_time_ms,
            "details": {
                "total_chunks": total,
                "with_semantic_names": with_semantic_names,
                "with_content_type": with_content_type,
                "with_language": with_language,
                "semantic_names_ratio": f"{semantic_ratio:.1%}"
            }
        }


def print_results(results: dict):
    """Print test results in a readable format."""
    print("\n" + "=" * 60)
    print("INDEXING TEST RESULTS")
    print("=" * 60)
    
    for test in results["tests"]:
        status = "✅ PASS" if test["passed"] else "❌ FAIL"
        print(f"\n{status} | {test['name']}")
        print(f"   Response time: {test['response_time_ms']:.0f}ms")
        
        if isinstance(test["details"], dict):
            for key, value in test["details"].items():
                print(f"   {key}: {value}")
        else:
            print(f"   Details: {test['details']}")
    
    print("\n" + "-" * 60)
    print(f"TOTAL: {results['passed']} passed, {results['failed']} failed")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Test RAG indexing")
    parser.add_argument("--reindex", action="store_true", help="Force reindex")
    parser.add_argument("--repo-path", type=str, help="Custom repository path")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Determine repo path
    repo_path = args.repo_path or str(SAMPLE_REPO_DIR)
    
    # Check if repo exists locally (for validation)
    if Path(repo_path).exists():
        logger.info(f"Repository path exists: {repo_path}")
    else:
        logger.warning(f"Repository path does not exist locally: {repo_path}")
        logger.warning("Make sure the path is accessible from the RAG pipeline container")
    
    # Initialize client and run tests
    client = get_client()
    test = IndexingTest(client, repo_path)
    
    results = test.run_all_tests(reindex=args.reindex)
    print_results(results)
    
    # Exit with error code if tests failed
    sys.exit(0 if results["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
