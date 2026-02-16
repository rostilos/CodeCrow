#!/usr/bin/env python3
"""
Test Chunking Quality - Validate AST-based code chunking.

Tests:
1. Chunk size boundaries
2. Semantic boundary preservation
3. Language-specific chunking
4. Overlapping context windows
5. Metadata extraction quality
"""
import sys
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Any
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    TEST_WORKSPACE, TEST_PROJECT, TEST_BRANCH,
    SAMPLE_REPO_DIR, THRESHOLDS, LOG_LEVEL, LOG_FORMAT
)
from utils.api_client import get_client, RAGAPIClient
from utils.result_analyzer import ResultAnalyzer

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


# Expected chunking characteristics per language
LANGUAGE_EXPECTATIONS = {
    "python": {
        "semantic_types": ["class_definition", "function_definition", "method"],
        "min_semantic_coverage": 0.7,
        "typical_chunk_count_per_file": (3, 15)
    },
    "typescript": {
        "semantic_types": ["class_declaration", "function_declaration", "method_definition"],
        "min_semantic_coverage": 0.7,
        "typical_chunk_count_per_file": (3, 20)
    },
    "java": {
        "semantic_types": ["class_declaration", "method_declaration", "constructor_declaration"],
        "min_semantic_coverage": 0.8,
        "typical_chunk_count_per_file": (3, 25)
    },
    "javascript": {
        "semantic_types": ["class_declaration", "function_declaration", "method_definition"],
        "min_semantic_coverage": 0.7,
        "typical_chunk_count_per_file": (2, 15)
    }
}


class ChunkingTest:
    """Test suite for AST-based chunking quality."""
    
    def __init__(self, client: RAGAPIClient):
        self.client = client
        self.analyzer = ResultAnalyzer()
    
    def run_all_tests(self) -> dict:
        """
        Run all chunking quality tests.
        
        Returns:
            Dict with test results
        """
        results = {
            "passed": 0,
            "failed": 0,
            "tests": []
        }
        
        # Test 1: Chunk size validation
        result = self._test_chunk_sizes()
        results["tests"].append(result)
        if result["passed"]:
            results["passed"] += 1
        else:
            results["failed"] += 1
        
        # Test 2: Semantic boundary preservation
        result = self._test_semantic_boundaries()
        results["tests"].append(result)
        if result["passed"]:
            results["passed"] += 1
        else:
            results["failed"] += 1
        
        # Test 3: Metadata quality
        result = self._test_metadata_quality()
        results["tests"].append(result)
        if result["passed"]:
            results["passed"] += 1
        else:
            results["failed"] += 1
        
        # Test 4: Language coverage
        result = self._test_language_coverage()
        results["tests"].append(result)
        if result["passed"]:
            results["passed"] += 1
        else:
            results["failed"] += 1
        
        # Test 5: Primary name extraction
        result = self._test_primary_name_extraction()
        results["tests"].append(result)
        if result["passed"]:
            results["passed"] += 1
        else:
            results["failed"] += 1
        
        return results
    
    def analyze_chunks(
        self,
        workspace: str = TEST_WORKSPACE,
        project: str = TEST_PROJECT,
        verbose: bool = True
    ) -> dict:
        """
        Analyze chunks in the index.
        
        Returns:
            Dict with chunk analysis
        """
        # Get index stats
        stats_response = self.client.get_index_stats(workspace, project)
        
        if not stats_response.success:
            return {
                "success": False,
                "error": stats_response.error
            }
        
        # Search for all chunks with broad query
        search_response = self.client.search(
            query="code function class method",
            workspace=workspace,
            project=project,
            branch=TEST_BRANCH,
            top_k=100
        )
        
        if not search_response.success:
            return {
                "success": False,
                "error": search_response.error
            }
        
        results = search_response.data.get("results", [])
        
        # Analyze chunks
        analysis = self._analyze_chunk_quality(results)
        
        if verbose:
            self._print_analysis(analysis, stats_response.data)
        
        return {
            "success": True,
            "stats": stats_response.data,
            "analysis": analysis
        }
    
    def _test_chunk_sizes(self) -> dict:
        """Test that chunks are within acceptable size limits."""
        logger.info("Test: Chunk size validation")
        
        search_response = self.client.search(
            query="function class method",
            workspace=TEST_WORKSPACE,
            project=TEST_PROJECT,
            branch=TEST_BRANCH,
            top_k=50
        )
        
        if not search_response.success:
            return {
                "name": "chunk_sizes",
                "passed": False,
                "details": search_response.error
            }
        
        results = search_response.data.get("results", [])
        
        if not results:
            return {
                "name": "chunk_sizes",
                "passed": False,
                "details": "No results to analyze"
            }
        
        # Check chunk sizes
        issues = []
        sizes = []
        
        for r in results:
            text = r.get("text", "")
            size = len(text)
            sizes.append(size)
            
            if size > THRESHOLDS["max_chunk_size"]:
                issues.append(f"Chunk too large: {size} chars")
            elif size < THRESHOLDS["min_chunk_size"]:
                issues.append(f"Chunk too small: {size} chars")
        
        avg_size = sum(sizes) / len(sizes) if sizes else 0
        
        passed = len(issues) <= len(results) * 0.1  # Allow 10% violations
        
        return {
            "name": "chunk_sizes",
            "passed": passed,
            "details": {
                "analyzed": len(results),
                "avg_size": int(avg_size),
                "min_size": min(sizes) if sizes else 0,
                "max_size": max(sizes) if sizes else 0,
                "violations": len(issues)
            }
        }
    
    def _test_semantic_boundaries(self) -> dict:
        """Test that chunks respect semantic boundaries."""
        logger.info("Test: Semantic boundary preservation")
        
        search_response = self.client.search(
            query="class definition function method",
            workspace=TEST_WORKSPACE,
            project=TEST_PROJECT,
            branch=TEST_BRANCH,
            top_k=50
        )
        
        if not search_response.success:
            return {
                "name": "semantic_boundaries",
                "passed": False,
                "details": search_response.error
            }
        
        results = search_response.data.get("results", [])
        
        # Check semantic completeness
        complete_count = 0
        partial_count = 0
        
        for r in results:
            text = r.get("text", "")
            
            # Check for complete semantic units
            has_def = "def " in text or "function " in text or "class " in text
            has_end = text.strip().endswith("}") or text.strip().endswith(":") or "return" in text
            
            if has_def:
                complete_count += 1
            else:
                partial_count += 1
        
        # Calculate semantic coverage
        total = complete_count + partial_count
        coverage = complete_count / total if total > 0 else 0
        
        passed = coverage >= THRESHOLDS["min_semantic_coverage"]
        
        return {
            "name": "semantic_boundaries",
            "passed": passed,
            "details": {
                "complete_units": complete_count,
                "partial_units": partial_count,
                "semantic_coverage": f"{coverage:.1%}"
            }
        }
    
    def _test_metadata_quality(self) -> dict:
        """Test metadata extraction quality."""
        logger.info("Test: Metadata quality")
        
        search_response = self.client.search(
            query="function class service",
            workspace=TEST_WORKSPACE,
            project=TEST_PROJECT,
            branch=TEST_BRANCH,
            top_k=30
        )
        
        if not search_response.success:
            return {
                "name": "metadata_quality",
                "passed": False,
                "details": search_response.error
            }
        
        results = search_response.data.get("results", [])
        
        # Check required metadata fields
        required_fields = ["path", "language", "workspace", "project"]
        optional_fields = ["primary_name", "chunk_type", "start_line", "end_line"]
        
        issues = []
        field_coverage = defaultdict(int)
        
        for r in results:
            metadata = r.get("metadata", {})
            
            for field in required_fields:
                if field in metadata and metadata[field]:
                    field_coverage[field] += 1
                else:
                    issues.append(f"Missing required: {field}")
            
            for field in optional_fields:
                if field in metadata and metadata[field]:
                    field_coverage[field] += 1
        
        total = len(results)
        
        # Calculate coverage percentages
        coverage = {
            field: f"{count/total:.1%}" if total > 0 else "0%"
            for field, count in field_coverage.items()
        }
        
        # Pass if all required fields have 90%+ coverage
        required_coverage = all(
            field_coverage.get(f, 0) >= total * 0.9
            for f in required_fields
        )
        
        return {
            "name": "metadata_quality",
            "passed": required_coverage,
            "details": {
                "analyzed": total,
                "field_coverage": coverage,
                "issues": len(issues)
            }
        }
    
    def _test_language_coverage(self) -> dict:
        """Test that multiple languages are properly chunked."""
        logger.info("Test: Language coverage")
        
        search_response = self.client.search(
            query="code implementation",
            workspace=TEST_WORKSPACE,
            project=TEST_PROJECT,
            branch=TEST_BRANCH,
            top_k=50
        )
        
        if not search_response.success:
            return {
                "name": "language_coverage",
                "passed": False,
                "details": search_response.error
            }
        
        results = search_response.data.get("results", [])
        
        # Count languages
        languages = defaultdict(int)
        
        for r in results:
            metadata = r.get("metadata", {})
            lang = metadata.get("language", "unknown")
            languages[lang] += 1
        
        passed = len(languages) >= 1  # At least one language should be detected
        
        return {
            "name": "language_coverage",
            "passed": passed,
            "details": {
                "analyzed": len(results),
                "languages": dict(languages)
            }
        }
    
    def _test_primary_name_extraction(self) -> dict:
        """Test primary name (semantic name) extraction."""
        logger.info("Test: Primary name extraction")
        
        search_response = self.client.search(
            query="class function method handler service",
            workspace=TEST_WORKSPACE,
            project=TEST_PROJECT,
            branch=TEST_BRANCH,
            top_k=30
        )
        
        if not search_response.success:
            return {
                "name": "primary_name_extraction",
                "passed": False,
                "details": search_response.error
            }
        
        results = search_response.data.get("results", [])
        
        with_name = 0
        meaningful_names = 0
        
        for r in results:
            metadata = r.get("metadata", {})
            primary_name = metadata.get("primary_name", "")
            
            if primary_name:
                with_name += 1
                
                # Check if name is meaningful (not just generic)
                generic_names = ["module", "chunk", "block", "code"]
                if not any(g in primary_name.lower() for g in generic_names):
                    meaningful_names += 1
        
        total = len(results)
        name_coverage = with_name / total if total > 0 else 0
        meaningful_ratio = meaningful_names / with_name if with_name > 0 else 0
        
        passed = name_coverage >= 0.5 and meaningful_ratio >= 0.5
        
        return {
            "name": "primary_name_extraction",
            "passed": passed,
            "details": {
                "analyzed": total,
                "with_primary_name": with_name,
                "meaningful_names": meaningful_names,
                "name_coverage": f"{name_coverage:.1%}",
                "meaningful_ratio": f"{meaningful_ratio:.1%}"
            }
        }
    
    def _analyze_chunk_quality(self, results: List[Dict[str, Any]]) -> dict:
        """Analyze overall chunk quality."""
        if not results:
            return {"error": "No results to analyze"}
        
        sizes = []
        languages = defaultdict(int)
        with_metadata = 0
        with_semantic_name = 0
        
        for r in results:
            text = r.get("text", "")
            metadata = r.get("metadata", {})
            
            sizes.append(len(text))
            
            lang = metadata.get("language", "unknown")
            languages[lang] += 1
            
            if metadata.get("path") and metadata.get("language"):
                with_metadata += 1
            
            if metadata.get("primary_name"):
                with_semantic_name += 1
        
        total = len(results)
        
        return {
            "total_chunks": total,
            "size_stats": {
                "avg": int(sum(sizes) / len(sizes)) if sizes else 0,
                "min": min(sizes) if sizes else 0,
                "max": max(sizes) if sizes else 0
            },
            "languages": dict(languages),
            "metadata_coverage": f"{with_metadata/total:.1%}" if total else "0%",
            "semantic_name_coverage": f"{with_semantic_name/total:.1%}" if total else "0%"
        }
    
    def _print_analysis(self, analysis: dict, stats: dict):
        """Print chunk analysis."""
        print("\n" + "=" * 60)
        print("CHUNK QUALITY ANALYSIS")
        print("=" * 60)
        
        if stats:
            print(f"\nIndex Stats:")
            print(f"  Total points: {stats.get('points_count', 'N/A')}")
        
        print(f"\nChunk Analysis ({analysis.get('total_chunks', 0)} samples):")
        
        size_stats = analysis.get("size_stats", {})
        print(f"\nSize Distribution:")
        print(f"  Average: {size_stats.get('avg', 0)} chars")
        print(f"  Min: {size_stats.get('min', 0)} chars")
        print(f"  Max: {size_stats.get('max', 0)} chars")
        
        print(f"\nLanguages:")
        for lang, count in analysis.get("languages", {}).items():
            print(f"  {lang}: {count}")
        
        print(f"\nQuality Metrics:")
        print(f"  Metadata coverage: {analysis.get('metadata_coverage', 'N/A')}")
        print(f"  Semantic name coverage: {analysis.get('semantic_name_coverage', 'N/A')}")
        
        print("=" * 60)


def print_results(results: dict):
    """Print test results summary."""
    print("\n" + "=" * 60)
    print("CHUNKING QUALITY TEST RESULTS")
    print("=" * 60)
    
    for test in results["tests"]:
        status = "✅ PASS" if test["passed"] else "❌ FAIL"
        print(f"\n{status} | {test['name']}")
        
        if isinstance(test.get("details"), dict):
            for key, value in test["details"].items():
                if isinstance(value, dict):
                    print(f"   {key}:")
                    for k, v in value.items():
                        print(f"     {k}: {v}")
                else:
                    print(f"   {key}: {value}")
        elif test.get("details"):
            print(f"   Details: {test['details']}")
    
    print("\n" + "-" * 60)
    print(f"TOTAL: {results['passed']} passed, {results['failed']} failed")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Test RAG chunking quality")
    parser.add_argument("--analyze", action="store_true", help="Run chunk analysis")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    client = get_client()
    test = ChunkingTest(client)
    
    if args.analyze:
        test.analyze_chunks(verbose=True)
    else:
        results = test.run_all_tests()
        print_results(results)
        sys.exit(0 if results["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
