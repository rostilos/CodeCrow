#!/usr/bin/env python3
"""
Test Deterministic Context - Test metadata-based context retrieval.

Tests:
1. Direct file path lookup
2. Semantic name-based search
3. Branch filtering
4. File type filtering
5. Multi-criteria queries
"""
import sys
import logging
import argparse
from pathlib import Path
from typing import Optional, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    TEST_WORKSPACE, TEST_PROJECT, TEST_BRANCH,
    THRESHOLDS, LOG_LEVEL, LOG_FORMAT
)
from utils.api_client import get_client, RAGAPIClient
from utils.result_analyzer import ResultAnalyzer

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


# Test cases for deterministic context
DETERMINISTIC_TEST_CASES = [
    {
        "name": "file_path_exact",
        "description": "Find by exact file path",
        "criteria": {"file_paths": ["src/services/user_service.py"]},
        "expected_patterns": ["user_service", "UserService"],
        "min_results": 1
    },
    {
        "name": "file_path_partial",
        "description": "Find by partial path match",
        "criteria": {"file_paths": ["services/"]},
        "expected_patterns": ["service"],
        "min_results": 1
    },
    {
        "name": "semantic_name_class",
        "description": "Find by class semantic name",
        "criteria": {"semantic_names": ["UserService"]},
        "expected_patterns": ["class UserService", "UserService"],
        "min_results": 1
    },
    {
        "name": "semantic_name_function",
        "description": "Find by function semantic name",
        "criteria": {"semantic_names": ["get_user_by_id", "getUserById"]},
        "expected_patterns": ["get_user", "getUser"],
        "min_results": 1
    },
    {
        "name": "branch_filter",
        "description": "Filter by branch name",
        "criteria": {
            "file_paths": ["src/"],
            "branch": TEST_BRANCH
        },
        "min_results": 0  # May vary based on indexed data
    },
    {
        "name": "multiple_criteria",
        "description": "Combined file path and semantic name",
        "criteria": {
            "file_paths": ["src/models/"],
            "semantic_names": ["User", "Product"]
        },
        "expected_patterns": ["model", "class"],
        "min_results": 0
    }
]


class DeterministicTest:
    """Test suite for deterministic context retrieval."""
    
    def __init__(self, client: RAGAPIClient):
        self.client = client
        self.analyzer = ResultAnalyzer()
    
    def run_all_tests(self) -> dict:
        """
        Run all deterministic test cases.
        
        Returns:
            Dict with test results
        """
        results = {
            "passed": 0,
            "failed": 0,
            "tests": []
        }
        
        for test_case in DETERMINISTIC_TEST_CASES:
            test_result = self._run_test_case(test_case)
            results["tests"].append(test_result)
            
            if test_result["passed"]:
                results["passed"] += 1
            else:
                results["failed"] += 1
        
        return results
    
    def run_single_test(self, test_name: str) -> dict:
        """
        Run a specific test by name.
        
        Args:
            test_name: Name of test case
            
        Returns:
            Dict with test result
        """
        for test_case in DETERMINISTIC_TEST_CASES:
            if test_case["name"] == test_name:
                return self._run_test_case(test_case, verbose=True)
        
        return {
            "name": test_name,
            "passed": False,
            "details": "Test case not found"
        }
    
    def run_custom_query(
        self,
        file_paths: Optional[List[str]] = None,
        semantic_names: Optional[List[str]] = None,
        branch: Optional[str] = None,
        workspace: str = TEST_WORKSPACE,
        project: str = TEST_PROJECT,
        verbose: bool = True
    ) -> dict:
        """
        Run custom deterministic query.
        
        Returns:
            Dict with query results and analysis
        """
        criteria = {}
        if file_paths:
            criteria["file_paths"] = file_paths
        if semantic_names:
            criteria["semantic_names"] = semantic_names
        if branch:
            criteria["branch"] = branch
        
        logger.info(f"Running deterministic query: {criteria}")
        
        response = self.client.get_deterministic_context(
            workspace=workspace,
            project=project,
            criteria=criteria,
            top_k=20
        )
        
        if not response.success:
            return {
                "success": False,
                "error": response.error,
                "response_time_ms": response.response_time_ms
            }
        
        context = response.data.get("context", {})
        results = context.get("relevant_code", [])
        
        # Calculate metrics
        metrics = self.analyzer.calculate_metrics(results)
        
        result = {
            "success": True,
            "response_time_ms": response.response_time_ms,
            "result_count": len(results),
            "criteria_used": context.get("criteria_used", criteria),
            "metrics": {
                "avg_relevance": metrics.avg_relevance_score,
                "unique_files": metrics.unique_files,
                "with_semantic_names": metrics.with_semantic_names
            },
            "results": results
        }
        
        if verbose:
            self._print_results(result, criteria)
        
        return result
    
    def _run_test_case(self, test_case: dict, verbose: bool = False) -> dict:
        """Run a single test case."""
        name = test_case["name"]
        criteria = test_case["criteria"]
        
        logger.info(f"Test: Deterministic - {name}")
        
        response = self.client.get_deterministic_context(
            workspace=TEST_WORKSPACE,
            project=TEST_PROJECT,
            criteria=criteria,
            top_k=20
        )
        
        if not response.success:
            return {
                "name": name,
                "passed": False,
                "response_time_ms": response.response_time_ms,
                "details": response.error
            }
        
        context = response.data.get("context", {})
        results = context.get("relevant_code", [])
        
        # Validate results
        passed = True
        issues = []
        
        # Check minimum results
        min_results = test_case.get("min_results", 0)
        if len(results) < min_results:
            passed = False
            issues.append(f"Expected at least {min_results} results, got {len(results)}")
        
        # Check expected patterns
        expected_patterns = test_case.get("expected_patterns", [])
        if expected_patterns and results:
            all_text = " ".join(r.get("text", "") for r in results)
            
            for pattern in expected_patterns:
                if pattern.lower() not in all_text.lower():
                    issues.append(f"Pattern not found: {pattern}")
        
        if issues:
            passed = False
        
        if verbose:
            self._print_results({"results": results, "result_count": len(results)}, criteria)
        
        return {
            "name": name,
            "passed": passed,
            "response_time_ms": response.response_time_ms,
            "details": {
                "result_count": len(results),
                "issues": issues[:3] if issues else [],
                "criteria": criteria
            }
        }
    
    def _print_results(self, result: dict, criteria: dict):
        """Print deterministic query results."""
        print("\n" + "=" * 60)
        print("DETERMINISTIC CONTEXT RESULTS")
        print("=" * 60)
        
        print(f"Criteria: {criteria}")
        print(f"Results: {result.get('result_count', 0)}")
        
        if 'response_time_ms' in result:
            print(f"Response time: {result['response_time_ms']:.0f}ms")
        
        print("-" * 60)
        
        for i, r in enumerate(result.get("results", [])[:10], 1):
            metadata = r.get("metadata", {})
            path = metadata.get("path", metadata.get("file_path", "unknown"))
            score = r.get("score", 0)
            primary_name = metadata.get("primary_name", "N/A")
            
            print(f"\n{i}. [{score:.3f}] {path}")
            print(f"   Name: {primary_name}")
            
            text = r.get("text", "")[:120].replace("\n", " ")
            print(f"   Text: {text}...")
        
        print("\n" + "=" * 60)


def print_results(results: dict):
    """Print test results summary."""
    print("\n" + "=" * 60)
    print("DETERMINISTIC CONTEXT TEST RESULTS")
    print("=" * 60)
    
    for test in results["tests"]:
        status = "✅ PASS" if test["passed"] else "❌ FAIL"
        print(f"\n{status} | {test['name']}")
        
        if 'response_time_ms' in test:
            print(f"   Response time: {test['response_time_ms']:.0f}ms")
        
        if isinstance(test.get("details"), dict):
            details = test["details"]
            print(f"   Results: {details.get('result_count', 0)}")
            if details.get("issues"):
                for issue in details["issues"]:
                    print(f"   ⚠️  {issue}")
        elif test.get("details"):
            print(f"   Details: {test['details']}")
    
    print("\n" + "-" * 60)
    print(f"TOTAL: {results['passed']} passed, {results['failed']} failed")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Test RAG deterministic context")
    parser.add_argument("--test", type=str, help="Run specific test by name")
    parser.add_argument("--files", type=str, nargs="+", help="File paths to search")
    parser.add_argument("--names", type=str, nargs="+", help="Semantic names to search")
    parser.add_argument("--branch", type=str, help="Branch to filter")
    parser.add_argument("--list", action="store_true", help="List available tests")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    if args.list:
        print("\nAvailable deterministic tests:")
        for tc in DETERMINISTIC_TEST_CASES:
            print(f"  - {tc['name']}: {tc['description']}")
        return
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    client = get_client()
    test = DeterministicTest(client)
    
    if args.files or args.names:
        # Run custom query
        test.run_custom_query(
            file_paths=args.files,
            semantic_names=args.names,
            branch=args.branch,
            verbose=True
        )
    elif args.test:
        # Run specific test
        result = test.run_single_test(args.test)
        status = "✅ PASS" if result["passed"] else "❌ FAIL"
        print(f"\n{status} | {result['name']}")
        if result.get("details"):
            print(f"Details: {result['details']}")
    else:
        # Run all tests
        results = test.run_all_tests()
        print_results(results)
        sys.exit(0 if results["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
