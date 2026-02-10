#!/usr/bin/env python3
"""
Test PR Context - Test PR context retrieval with Smart RAG.

Tests:
1. Simple single-file PR context
2. Multi-file PR context
3. Cross-file dependency retrieval
4. Priority reranking
5. Query decomposition
6. Base branch fallback
"""
import sys
import json
import logging
import argparse
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    TEST_WORKSPACE, TEST_PROJECT, TEST_BRANCH,
    PR_SCENARIOS_DIR, THRESHOLDS, LOG_LEVEL, LOG_FORMAT
)
from utils.api_client import get_client, RAGAPIClient
from utils.result_analyzer import ResultAnalyzer, ValidationResult

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


class PRContextTest:
    """Test suite for PR context retrieval."""
    
    def __init__(self, client: RAGAPIClient):
        self.client = client
        self.analyzer = ResultAnalyzer()
    
    def run_all_scenarios(self) -> dict:
        """
        Run all PR scenario tests.
        
        Returns:
            Dict with test results
        """
        results = {
            "passed": 0,
            "failed": 0,
            "tests": []
        }
        
        # Load and run all scenarios
        scenario_files = list(PR_SCENARIOS_DIR.glob("*.json"))
        
        if not scenario_files:
            logger.warning(f"No scenario files found in {PR_SCENARIOS_DIR}")
            return results
        
        for scenario_file in scenario_files:
            try:
                scenario = json.loads(scenario_file.read_text())
                test_result = self._run_scenario(scenario)
                results["tests"].append(test_result)
                
                if test_result["passed"]:
                    results["passed"] += 1
                else:
                    results["failed"] += 1
                    
            except Exception as e:
                logger.error(f"Error loading scenario {scenario_file}: {e}")
                results["tests"].append({
                    "name": scenario_file.stem,
                    "passed": False,
                    "details": str(e)
                })
                results["failed"] += 1
        
        return results
    
    def run_scenario(self, scenario_name: str) -> dict:
        """
        Run a specific scenario by name.
        
        Args:
            scenario_name: Name of scenario file (without .json)
            
        Returns:
            Dict with test result
        """
        scenario_file = PR_SCENARIOS_DIR / f"{scenario_name}.json"
        
        if not scenario_file.exists():
            return {
                "name": scenario_name,
                "passed": False,
                "details": f"Scenario file not found: {scenario_file}"
            }
        
        scenario = json.loads(scenario_file.read_text())
        return self._run_scenario(scenario, verbose=True)
    
    def run_custom_pr(
        self,
        changed_files: list,
        diff_snippets: Optional[list] = None,
        pr_title: Optional[str] = None,
        pr_description: Optional[str] = None,
        workspace: str = TEST_WORKSPACE,
        project: str = TEST_PROJECT,
        branch: str = TEST_BRANCH,
        base_branch: Optional[str] = None,
        verbose: bool = True
    ) -> dict:
        """
        Run custom PR context retrieval.
        
        Returns:
            Dict with PR context and analysis
        """
        logger.info(f"Running PR context for {len(changed_files)} files")
        
        response = self.client.get_pr_context(
            workspace=workspace,
            project=project,
            branch=branch,
            changed_files=changed_files,
            diff_snippets=diff_snippets or [],
            pr_title=pr_title,
            pr_description=pr_description,
            top_k=15,
            enable_priority_reranking=True,
            min_relevance_score=0.65,
            base_branch=base_branch
        )
        
        if not response.success:
            return {
                "success": False,
                "error": response.error,
                "response_time_ms": response.response_time_ms
            }
        
        context = response.data.get("context", {})
        relevant_code = context.get("relevant_code", [])
        
        # Analyze results
        metrics = self.analyzer.calculate_metrics(relevant_code)
        
        result = {
            "success": True,
            "response_time_ms": response.response_time_ms,
            "result_count": len(relevant_code),
            "related_files": context.get("related_files", []),
            "branches_searched": context.get("_branches_searched", []),
            "metrics": {
                "avg_relevance": metrics.avg_relevance_score,
                "unique_files": metrics.unique_files,
                "high_priority": metrics.high_priority_count,
                "with_semantic_names": metrics.with_semantic_names
            },
            "results": relevant_code
        }
        
        if verbose:
            self._print_pr_context(result, changed_files, pr_title)
        
        return result
    
    def _run_scenario(self, scenario: dict, verbose: bool = False) -> dict:
        """Run a single scenario test."""
        name = scenario.get("name", "unknown")
        logger.info(f"Test: PR Context - {name}")
        
        response = self.client.get_pr_context(
            workspace=scenario.get("workspace", TEST_WORKSPACE),
            project=scenario.get("project", TEST_PROJECT),
            branch=scenario.get("branch", TEST_BRANCH),
            changed_files=scenario.get("changed_files", []),
            diff_snippets=scenario.get("diff_snippets", []),
            pr_title=scenario.get("pr_title"),
            pr_description=scenario.get("pr_description"),
            top_k=15,
            enable_priority_reranking=True,
            min_relevance_score=0.6,
            base_branch=scenario.get("base_branch")
        )
        
        if not response.success:
            return {
                "name": name,
                "passed": False,
                "response_time_ms": response.response_time_ms,
                "details": response.error
            }
        
        context = response.data.get("context", {})
        relevant_code = context.get("relevant_code", [])
        
        # Validate against expected results
        expected = scenario.get("expected_results", {})
        
        validation = self.analyzer.validate_expected_results(
            results=relevant_code,
            should_find=expected.get("should_find", []),
            should_not_find=expected.get("should_not_find", []),
            min_results=expected.get("min_results", 1),
            min_relevance_score=expected.get("min_relevance_score", 0.5)
        )
        
        if verbose:
            self._print_pr_context(
                {
                    "result_count": len(relevant_code),
                    "related_files": context.get("related_files", []),
                    "results": relevant_code
                },
                scenario.get("changed_files", []),
                scenario.get("pr_title")
            )
            print(self.analyzer.generate_summary(relevant_code, validation))
        
        return {
            "name": name,
            "passed": validation.passed,
            "response_time_ms": response.response_time_ms,
            "details": {
                "result_count": len(relevant_code),
                "precision": f"{validation.precision:.1%}",
                "recall": f"{validation.recall:.1%}",
                "issues": validation.issues[:3] if validation.issues else []
            }
        }
    
    def _print_pr_context(self, result: dict, changed_files: list, pr_title: Optional[str]):
        """Print PR context results."""
        print("\n" + "=" * 60)
        print("PR CONTEXT RESULTS")
        print("=" * 60)
        
        if pr_title:
            print(f"PR: {pr_title}")
        
        print(f"Changed files: {changed_files}")
        print(f"Results: {result.get('result_count', 0)}")
        print(f"Related files: {len(result.get('related_files', []))}")
        
        if 'response_time_ms' in result:
            print(f"Response time: {result['response_time_ms']:.0f}ms")
        
        print("-" * 60)
        
        for i, r in enumerate(result.get("results", [])[:10], 1):
            metadata = r.get("metadata", {})
            path = metadata.get("path", metadata.get("file_path", "unknown"))
            score = r.get("score", 0)
            primary_name = metadata.get("primary_name", "N/A")
            priority = r.get("_priority", "N/A")
            
            print(f"\n{i}. [{score:.3f}] {path}")
            print(f"   Name: {primary_name} | Priority: {priority}")
            
            text = r.get("text", "")[:120].replace("\n", " ")
            print(f"   Text: {text}...")
        
        print("\n" + "=" * 60)


def print_results(results: dict):
    """Print test results summary."""
    print("\n" + "=" * 60)
    print("PR CONTEXT TEST RESULTS")
    print("=" * 60)
    
    for test in results["tests"]:
        status = "✅ PASS" if test["passed"] else "❌ FAIL"
        print(f"\n{status} | {test['name']}")
        
        if 'response_time_ms' in test:
            print(f"   Response time: {test['response_time_ms']:.0f}ms")
        
        if isinstance(test.get("details"), dict):
            for key, value in test["details"].items():
                print(f"   {key}: {value}")
        elif test.get("details"):
            print(f"   Details: {test['details']}")
    
    print("\n" + "-" * 60)
    print(f"TOTAL: {results['passed']} passed, {results['failed']} failed")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Test RAG PR context retrieval")
    parser.add_argument("--scenario", type=str, help="Run specific scenario")
    parser.add_argument("--files", type=str, nargs="+", help="Changed files for custom PR")
    parser.add_argument("--title", type=str, help="PR title for custom PR")
    parser.add_argument("--description", type=str, help="PR description for custom PR")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    client = get_client()
    test = PRContextTest(client)
    
    if args.files:
        # Run custom PR
        test.run_custom_pr(
            changed_files=args.files,
            pr_title=args.title,
            pr_description=args.description,
            verbose=True
        )
    elif args.scenario:
        # Run specific scenario
        result = test.run_scenario(args.scenario)
        status = "✅ PASS" if result["passed"] else "❌ FAIL"
        print(f"\n{status} | {result['name']}")
        if result.get("details"):
            print(f"Details: {result['details']}")
    else:
        # Run all scenarios
        results = test.run_all_scenarios()
        print_results(results)
        sys.exit(0 if results["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
