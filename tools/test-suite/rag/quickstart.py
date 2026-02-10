#!/usr/bin/env python3
"""
Quick Start Script - Verify RAG test suite setup.

Run this script to:
1. Check RAG API connectivity
2. Verify fixture files exist
3. Run a simple health check
"""
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    RAG_API_URL, TEST_WORKSPACE, TEST_PROJECT,
    SAMPLE_REPO_DIR, PR_SCENARIOS_DIR, EXPECTED_RESULTS_DIR
)


def check_fixtures():
    """Check that fixture files exist."""
    print("\n📁 Checking fixtures...")
    
    issues = []
    
    # Check sample repo
    if SAMPLE_REPO_DIR.exists():
        py_files = list(SAMPLE_REPO_DIR.rglob("*.py"))
        ts_files = list(SAMPLE_REPO_DIR.rglob("*.ts"))
        java_files = list(SAMPLE_REPO_DIR.rglob("*.java"))
        
        print(f"  ✓ Sample repo: {len(py_files)} Python, {len(ts_files)} TS, {len(java_files)} Java files")
    else:
        issues.append(f"Sample repo not found: {SAMPLE_REPO_DIR}")
    
    # Check PR scenarios
    if PR_SCENARIOS_DIR.exists():
        scenarios = list(PR_SCENARIOS_DIR.glob("*.json"))
        print(f"  ✓ PR scenarios: {len(scenarios)} files")
    else:
        issues.append(f"PR scenarios not found: {PR_SCENARIOS_DIR}")
    
    # Check expected results
    if EXPECTED_RESULTS_DIR.exists():
        expected = list(EXPECTED_RESULTS_DIR.glob("*.json"))
        print(f"  ✓ Expected results: {len(expected)} files")
    else:
        issues.append(f"Expected results not found: {EXPECTED_RESULTS_DIR}")
    
    return issues


def check_api():
    """Check RAG API connectivity."""
    print("\n🔌 Checking RAG API...")
    print(f"  URL: {RAG_API_URL}")
    
    try:
        from utils.api_client import get_client
        client = get_client()
        response = client.health_check()
        
        if response.success:
            print(f"  ✓ API is healthy ({response.response_time_ms:.0f}ms)")
            return []
        else:
            return [f"API health check failed: {response.error}"]
    except Exception as e:
        return [f"Could not connect to API: {e}"]


def main():
    print("=" * 60)
    print("RAG Test Suite - Quick Start Check")
    print("=" * 60)
    
    print(f"\n⚙️  Configuration:")
    print(f"  RAG API: {RAG_API_URL}")
    print(f"  Workspace: {TEST_WORKSPACE}")
    print(f"  Project: {TEST_PROJECT}")
    
    all_issues = []
    
    # Check fixtures
    fixture_issues = check_fixtures()
    all_issues.extend(fixture_issues)
    
    # Check API
    api_issues = check_api()
    all_issues.extend(api_issues)
    
    print("\n" + "=" * 60)
    
    if all_issues:
        print("⚠️  Issues found:")
        for issue in all_issues:
            print(f"  - {issue}")
        print("\nPlease fix these issues before running tests.")
        sys.exit(1)
    else:
        print("✅ All checks passed!")
        print("\nYou can now run tests:")
        print("  python scripts/run_all_tests.py")
        print("  python scripts/test_search.py --query 'your query'")
        print("  python scripts/test_pr_context.py --scenario simple_change")
        sys.exit(0)


if __name__ == "__main__":
    main()
