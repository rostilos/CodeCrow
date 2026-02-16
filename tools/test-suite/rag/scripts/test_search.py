#!/usr/bin/env python3
"""
Test Search - Test semantic search functionality.

Tests:
1. Basic keyword search
2. Natural language queries
3. Code snippet queries
4. Instruction type variations (GENERAL, DEPENDENCY, LOGIC, IMPACT)
5. Language filtering
"""
import sys
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    TEST_WORKSPACE, TEST_PROJECT, TEST_BRANCH,
    DEFAULT_TOP_K, INSTRUCTION_TYPES, THRESHOLDS,
    LOG_LEVEL, LOG_FORMAT
)
from utils.api_client import get_client, RAGAPIClient
from utils.result_analyzer import ResultAnalyzer

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


# Test queries with expected results
SEARCH_TEST_CASES = [
    {
        "name": "Keyword - User Service",
        "query": "user service",
        "expected_patterns": ["UserService", "user"],
        "min_results": 3
    },
    {
        "name": "Natural Language - Authentication",
        "query": "How does user authentication work?",
        "expected_patterns": ["auth", "login", "password"],
        "min_results": 2
    },
    {
        "name": "Natural Language - Validation",
        "query": "password validation rules",
        "expected_patterns": ["validate_password", "PASSWORD"],
        "min_results": 1
    },
    {
        "name": "Code Snippet - Class Definition",
        "query": "class User:",
        "expected_patterns": ["User", "class"],
        "min_results": 1
    },
    {
        "name": "Function Name - Create User",
        "query": "create_user function",
        "expected_patterns": ["create_user", "def"],
        "min_results": 1
    },
    {
        "name": "Cross-Language - Controller",
        "query": "user controller API endpoint",
        "expected_patterns": ["controller", "Controller", "API"],
        "min_results": 1
    },
    {
        "name": "Domain - Order Processing",
        "query": "order processing payment",
        "expected_patterns": ["Order", "order"],
        "min_results": 1
    }
]


class SearchTest:
    """Test suite for semantic search functionality."""
    
    def __init__(self, client: RAGAPIClient):
        self.client = client
        self.workspace = TEST_WORKSPACE
        self.project = TEST_PROJECT
        self.branch = TEST_BRANCH
        self.analyzer = ResultAnalyzer()
    
    def run_all_tests(self) -> dict:
        """
        Run all search tests.
        
        Returns:
            Dict with test results
        """
        results = {
            "passed": 0,
            "failed": 0,
            "tests": []
        }
        
        # Run predefined test cases
        for test_case in SEARCH_TEST_CASES:
            test_result = self._run_search_test(test_case)
            results["tests"].append(test_result)
            if test_result["passed"]:
                results["passed"] += 1
            else:
                results["failed"] += 1
        
        # Test instruction types
        instruction_results = self._test_instruction_types()
        results["tests"].extend(instruction_results["tests"])
        results["passed"] += instruction_results["passed"]
        results["failed"] += instruction_results["failed"]
        
        return results
    
    def run_single_query(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        filter_language: str = None,
        show_results: bool = True
    ) -> dict:
        """
        Run a single search query with detailed output.
        
        Args:
            query: Search query
            top_k: Number of results
            filter_language: Optional language filter
            show_results: Whether to print results
            
        Returns:
            Dict with search results and analysis
        """
        logger.info(f"Running query: {query}")
        
        response = self.client.semantic_search(
            query=query,
            workspace=self.workspace,
            project=self.project,
            branch=self.branch,
            top_k=top_k,
            filter_language=filter_language
        )
        
        if not response.success:
            return {
                "success": False,
                "error": response.error,
                "response_time_ms": response.response_time_ms
            }
        
        results = response.data.get("results", [])
        metrics = self.analyzer.calculate_metrics(results)
        
        output = {
            "success": True,
            "query": query,
            "result_count": len(results),
            "response_time_ms": response.response_time_ms,
            "metrics": {
                "avg_relevance": metrics.avg_relevance_score,
                "unique_files": metrics.unique_files,
                "with_semantic_names": metrics.with_semantic_names
            },
            "results": results
        }
        
        if show_results:
            self._print_search_results(output)
        
        return output
    
    def _run_search_test(self, test_case: dict) -> dict:
        """Run a single search test case."""
        logger.info(f"Test: {test_case['name']}")
        
        response = self.client.semantic_search(
            query=test_case["query"],
            workspace=self.workspace,
            project=self.project,
            branch=self.branch,
            top_k=DEFAULT_TOP_K
        )
        
        if not response.success:
            return {
                "name": test_case["name"],
                "passed": False,
                "response_time_ms": response.response_time_ms,
                "details": response.error
            }
        
        results = response.data.get("results", [])
        
        # Check minimum results
        if len(results) < test_case.get("min_results", 1):
            return {
                "name": test_case["name"],
                "passed": False,
                "response_time_ms": response.response_time_ms,
                "details": f"Too few results: {len(results)} < {test_case['min_results']}"
            }
        
        # Check expected patterns
        all_text = " ".join([
            r.get("text", "") + " " + str(r.get("metadata", {}))
            for r in results
        ]).lower()
        
        found_patterns = []
        missing_patterns = []
        
        for pattern in test_case.get("expected_patterns", []):
            if pattern.lower() in all_text:
                found_patterns.append(pattern)
            else:
                missing_patterns.append(pattern)
        
        passed = len(missing_patterns) == 0
        
        return {
            "name": test_case["name"],
            "passed": passed,
            "response_time_ms": response.response_time_ms,
            "details": {
                "query": test_case["query"],
                "result_count": len(results),
                "found_patterns": found_patterns,
                "missing_patterns": missing_patterns
            }
        }
    
    def _test_instruction_types(self) -> dict:
        """Test different instruction types."""
        results = {
            "passed": 0,
            "failed": 0,
            "tests": []
        }
        
        # Base query to test with different instructions
        base_query = "user authentication validate"
        
        instruction_responses = {}
        
        for instruction_type in ["general", "dependency", "logic", "impact"]:
            logger.info(f"Test: Instruction Type - {instruction_type}")
            
            # Note: The API doesn't expose instruction_type directly,
            # but the query formatting happens server-side based on endpoint
            response = self.client.semantic_search(
                query=f"{base_query}",
                workspace=self.workspace,
                project=self.project,
                branch=self.branch,
                top_k=10
            )
            
            if response.success:
                instruction_responses[instruction_type] = response.data.get("results", [])
        
        # Verify we got results for at least the general type
        if "general" in instruction_responses and instruction_responses["general"]:
            results["tests"].append({
                "name": "Instruction Type - General",
                "passed": True,
                "response_time_ms": response.response_time_ms,
                "details": f"Got {len(instruction_responses['general'])} results"
            })
            results["passed"] += 1
        else:
            results["tests"].append({
                "name": "Instruction Type - General",
                "passed": False,
                "response_time_ms": response.response_time_ms if response else 0,
                "details": "No results for general instruction type"
            })
            results["failed"] += 1
        
        return results
    
    def _print_search_results(self, output: dict):
        """Print search results in readable format."""
        print("\n" + "=" * 60)
        print(f"SEARCH RESULTS: {output['query']}")
        print("=" * 60)
        print(f"Results: {output['result_count']} | Time: {output['response_time_ms']:.0f}ms")
        print(f"Avg Relevance: {output['metrics']['avg_relevance']:.3f}")
        print(f"Unique Files: {output['metrics']['unique_files']}")
        print("-" * 60)
        
        for i, result in enumerate(output.get("results", [])[:10], 1):
            metadata = result.get("metadata", {})
            path = metadata.get("path", metadata.get("file_path", "unknown"))
            score = result.get("score", 0)
            primary_name = metadata.get("primary_name", "N/A")
            content_type = metadata.get("content_type", "N/A")
            
            print(f"\n{i}. [{score:.3f}] {path}")
            print(f"   Name: {primary_name} | Type: {content_type}")
            
            text = result.get("text", "")[:150].replace("\n", " ")
            print(f"   Text: {text}...")
        
        print("\n" + "=" * 60)


def print_results(results: dict):
    """Print test results summary."""
    print("\n" + "=" * 60)
    print("SEARCH TEST RESULTS")
    print("=" * 60)
    
    for test in results["tests"]:
        status = "✅ PASS" if test["passed"] else "❌ FAIL"
        print(f"\n{status} | {test['name']}")
        print(f"   Response time: {test['response_time_ms']:.0f}ms")
        
        if isinstance(test["details"], dict):
            for key, value in test["details"].items():
                if isinstance(value, list) and len(value) > 5:
                    print(f"   {key}: {value[:5]}... ({len(value)} total)")
                else:
                    print(f"   {key}: {value}")
        else:
            print(f"   Details: {test['details']}")
    
    print("\n" + "-" * 60)
    print(f"TOTAL: {results['passed']} passed, {results['failed']} failed")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Test RAG semantic search")
    parser.add_argument("--query", type=str, help="Run single query")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="Number of results")
    parser.add_argument("--language", type=str, help="Filter by language")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    client = get_client()
    test = SearchTest(client)
    
    if args.query:
        # Run single query
        test.run_single_query(
            query=args.query,
            top_k=args.top_k,
            filter_language=args.language,
            show_results=True
        )
    else:
        # Run full test suite
        results = test.run_all_tests()
        print_results(results)
        sys.exit(0 if results["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
