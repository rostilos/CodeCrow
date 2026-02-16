#!/usr/bin/env python3
"""
Run All RAG Tests - Complete test suite orchestrator.

Runs all test modules and generates a combined report.
"""
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    TEST_WORKSPACE, TEST_PROJECT, TEST_BRANCH,
    SAMPLE_REPO_DIR, OUTPUT_DIR, LOG_LEVEL, LOG_FORMAT
)
from utils.api_client import get_client, RAGAPIClient

# Import test modules
from test_indexing import IndexingTest
from test_search import SearchTest
from test_pr_context import PRContextTest
from test_deterministic import DeterministicTest
from test_chunking import ChunkingTest

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


class TestSuiteRunner:
    """Orchestrate all RAG test suites."""
    
    def __init__(self, client: RAGAPIClient):
        self.client = client
        self.results: Dict[str, Any] = {}
        self.start_time = None
        self.end_time = None
    
    def run_all(
        self,
        skip_indexing: bool = False,
        skip_search: bool = False,
        skip_pr_context: bool = False,
        skip_deterministic: bool = False,
        skip_chunking: bool = False,
        reindex: bool = False
    ) -> Dict[str, Any]:
        """
        Run all test suites.
        
        Args:
            skip_indexing: Skip indexing tests
            skip_search: Skip search tests
            skip_pr_context: Skip PR context tests
            skip_deterministic: Skip deterministic tests
            skip_chunking: Skip chunking tests
            reindex: Force reindex before tests
            
        Returns:
            Combined test results
        """
        self.start_time = datetime.now()
        self.results = {
            "timestamp": self.start_time.isoformat(),
            "config": {
                "workspace": TEST_WORKSPACE,
                "project": TEST_PROJECT,
                "branch": TEST_BRANCH
            },
            "suites": {},
            "summary": {
                "total_passed": 0,
                "total_failed": 0,
                "suites_run": 0
            }
        }
        
        # Check health first
        if not self._check_health():
            self.results["error"] = "RAG API not healthy"
            return self.results
        
        # Optional reindex
        if reindex:
            logger.info("Reindexing sample repository...")
            self._reindex()
        
        # Run test suites
        if not skip_indexing:
            self._run_suite("indexing", IndexingTest)
        
        if not skip_search:
            self._run_suite("search", SearchTest)
        
        if not skip_pr_context:
            self._run_suite("pr_context", PRContextTest)
        
        if not skip_deterministic:
            self._run_suite("deterministic", DeterministicTest)
        
        if not skip_chunking:
            self._run_suite("chunking", ChunkingTest)
        
        self.end_time = datetime.now()
        self.results["duration_seconds"] = (self.end_time - self.start_time).total_seconds()
        
        return self.results
    
    def _check_health(self) -> bool:
        """Check RAG API health."""
        logger.info("Checking RAG API health...")
        response = self.client.health_check()
        
        if response.success:
            logger.info("✅ RAG API is healthy")
            return True
        else:
            logger.error(f"❌ RAG API health check failed: {response.error}")
            return False
    
    def _reindex(self):
        """Reindex sample repository."""
        # Delete existing index
        self.client.delete_index(TEST_WORKSPACE, TEST_PROJECT)
        
        # Index sample repo
        if SAMPLE_REPO_DIR.exists():
            # This would need archive creation - for now just log
            logger.info(f"Would index: {SAMPLE_REPO_DIR}")
    
    def _run_suite(self, name: str, test_class):
        """Run a test suite and record results."""
        logger.info(f"\n{'='*60}")
        logger.info(f"Running {name.upper()} tests")
        logger.info(f"{'='*60}")
        
        try:
            test = test_class(self.client)
            
            if name == "indexing":
                results = test.run_all_tests()
            elif name == "search":
                results = test.run_all_tests()
            elif name == "pr_context":
                results = test.run_all_scenarios()
            elif name == "deterministic":
                results = test.run_all_tests()
            elif name == "chunking":
                results = test.run_all_tests()
            else:
                results = {"error": f"Unknown suite: {name}"}
            
            self.results["suites"][name] = results
            self.results["summary"]["suites_run"] += 1
            self.results["summary"]["total_passed"] += results.get("passed", 0)
            self.results["summary"]["total_failed"] += results.get("failed", 0)
            
        except Exception as e:
            logger.error(f"Error running {name} suite: {e}")
            self.results["suites"][name] = {
                "error": str(e),
                "passed": 0,
                "failed": 1
            }
            self.results["summary"]["total_failed"] += 1
    
    def save_report(self, filepath: Path = None):
        """Save results to JSON file."""
        if filepath is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = OUTPUT_DIR / f"test_report_{timestamp}.json"
        
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        
        with open(filepath, "w") as f:
            json.dump(self.results, f, indent=2, default=str)
        
        logger.info(f"Report saved to: {filepath}")
        return filepath


def print_summary(results: Dict[str, Any]):
    """Print test summary."""
    print("\n" + "=" * 70)
    print("RAG TEST SUITE - COMPLETE SUMMARY")
    print("=" * 70)
    
    print(f"\nTimestamp: {results.get('timestamp', 'N/A')}")
    print(f"Duration: {results.get('duration_seconds', 0):.1f} seconds")
    
    config = results.get("config", {})
    print(f"\nConfiguration:")
    print(f"  Workspace: {config.get('workspace', 'N/A')}")
    print(f"  Project: {config.get('project', 'N/A')}")
    print(f"  Branch: {config.get('branch', 'N/A')}")
    
    print("\n" + "-" * 70)
    print("SUITE RESULTS")
    print("-" * 70)
    
    for suite_name, suite_results in results.get("suites", {}).items():
        if "error" in suite_results and suite_results.get("passed", 0) == 0:
            status = "❌ ERROR"
            print(f"\n{status} | {suite_name.upper()}")
            print(f"  Error: {suite_results['error']}")
        else:
            passed = suite_results.get("passed", 0)
            failed = suite_results.get("failed", 0)
            total = passed + failed
            
            if failed == 0:
                status = "✅ PASS"
            elif passed == 0:
                status = "❌ FAIL"
            else:
                status = "⚠️  PARTIAL"
            
            print(f"\n{status} | {suite_name.upper()}")
            print(f"  Tests: {passed}/{total} passed")
            
            # Print individual test names
            for test in suite_results.get("tests", [])[:5]:
                test_status = "✓" if test.get("passed") else "✗"
                test_name = test.get("name", "unknown")
                print(f"    {test_status} {test_name}")
    
    summary = results.get("summary", {})
    total_passed = summary.get("total_passed", 0)
    total_failed = summary.get("total_failed", 0)
    total_tests = total_passed + total_failed
    
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print(f"  Suites run: {summary.get('suites_run', 0)}")
    print(f"  Total tests: {total_tests}")
    print(f"  Passed: {total_passed} ({total_passed/total_tests*100:.0f}%)" if total_tests > 0 else "  Passed: 0")
    print(f"  Failed: {total_failed}")
    
    if total_failed == 0:
        print("\n🎉 ALL TESTS PASSED!")
    else:
        print(f"\n⚠️  {total_failed} tests failed")
    
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Run complete RAG test suite")
    parser.add_argument("--skip-indexing", action="store_true", help="Skip indexing tests")
    parser.add_argument("--skip-search", action="store_true", help="Skip search tests")
    parser.add_argument("--skip-pr-context", action="store_true", help="Skip PR context tests")
    parser.add_argument("--skip-deterministic", action="store_true", help="Skip deterministic tests")
    parser.add_argument("--skip-chunking", action="store_true", help="Skip chunking tests")
    parser.add_argument("--reindex", action="store_true", help="Force reindex before tests")
    parser.add_argument("--save-report", action="store_true", help="Save JSON report")
    parser.add_argument("--output", type=str, help="Output file path for report")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    client = get_client()
    runner = TestSuiteRunner(client)
    
    results = runner.run_all(
        skip_indexing=args.skip_indexing,
        skip_search=args.skip_search,
        skip_pr_context=args.skip_pr_context,
        skip_deterministic=args.skip_deterministic,
        skip_chunking=args.skip_chunking,
        reindex=args.reindex
    )
    
    print_summary(results)
    
    if args.save_report:
        output_path = Path(args.output) if args.output else None
        runner.save_report(output_path)
    
    # Exit with failure code if any tests failed
    summary = results.get("summary", {})
    sys.exit(0 if summary.get("total_failed", 1) == 0 else 1)


if __name__ == "__main__":
    main()
