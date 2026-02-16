"""
Result Analyzer - Tools for analyzing RAG retrieval quality.

Provides metrics and analysis for:
- Precision and recall
- Relevance score distribution
- Content type distribution
- File coverage
"""
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from collections import Counter
import re

logger = logging.getLogger(__name__)


@dataclass
class RetrievalMetrics:
    """Metrics for evaluating retrieval quality."""
    total_results: int = 0
    unique_files: int = 0
    avg_relevance_score: float = 0.0
    min_relevance_score: float = 0.0
    max_relevance_score: float = 0.0
    
    # Content type breakdown
    functions_classes_count: int = 0
    simplified_code_count: int = 0
    fallback_count: int = 0
    
    # Metadata quality
    with_semantic_names: int = 0
    with_docstrings: int = 0
    with_parent_class: int = 0
    
    # Language distribution
    language_distribution: Dict[str, int] = field(default_factory=dict)
    
    # File priority distribution
    high_priority_count: int = 0
    medium_priority_count: int = 0
    low_priority_count: int = 0


@dataclass
class ExpectedResult:
    """Definition of an expected search result."""
    pattern: str
    reason: str
    found: bool = False
    matched_text: Optional[str] = None
    matched_score: Optional[float] = None


@dataclass 
class ValidationResult:
    """Result of validating against expected results."""
    passed: bool
    expected_found: List[ExpectedResult]
    expected_not_found: List[ExpectedResult]
    precision: float = 0.0
    recall: float = 0.0
    issues: List[str] = field(default_factory=list)


class ResultAnalyzer:
    """
    Analyzer for RAG retrieval results.
    
    Evaluates:
    - Result quality metrics
    - Expected result matching
    - Content type distribution
    - Metadata quality
    """
    
    # Priority patterns (matching RAG implementation)
    HIGH_PRIORITY_PATTERNS = [
        'service', 'controller', 'handler', 'api', 'core', 'auth', 'security',
        'permission', 'repository', 'dao', 'migration'
    ]
    MEDIUM_PRIORITY_PATTERNS = [
        'model', 'entity', 'dto', 'schema', 'util', 'helper', 'common',
        'shared', 'component', 'hook', 'client', 'integration'
    ]
    LOW_PRIORITY_PATTERNS = [
        'test', 'spec', 'config', 'mock', 'fixture', 'stub'
    ]
    
    def __init__(self):
        logger.info("ResultAnalyzer initialized")
    
    def calculate_metrics(self, results: List[Dict[str, Any]]) -> RetrievalMetrics:
        """
        Calculate retrieval metrics from results.
        
        Args:
            results: List of retrieval results with text, score, metadata
            
        Returns:
            RetrievalMetrics with calculated values
        """
        if not results:
            return RetrievalMetrics()
        
        metrics = RetrievalMetrics()
        metrics.total_results = len(results)
        
        scores = []
        files = set()
        languages = Counter()
        
        for result in results:
            metadata = result.get('metadata', {})
            
            # Score
            score = result.get('score', 0.0)
            scores.append(score)
            
            # File
            file_path = metadata.get('path', metadata.get('file_path', ''))
            if file_path:
                files.add(file_path)
            
            # Content type
            content_type = metadata.get('content_type', 'fallback')
            if content_type == 'functions_classes':
                metrics.functions_classes_count += 1
            elif content_type == 'simplified_code':
                metrics.simplified_code_count += 1
            else:
                metrics.fallback_count += 1
            
            # Metadata quality
            if metadata.get('semantic_names'):
                metrics.with_semantic_names += 1
            if metadata.get('docstring'):
                metrics.with_docstrings += 1
            if metadata.get('parent_class'):
                metrics.with_parent_class += 1
            
            # Language
            lang = metadata.get('language', 'unknown')
            languages[lang] += 1
            
            # Priority
            file_path_lower = file_path.lower()
            if any(p in file_path_lower for p in self.HIGH_PRIORITY_PATTERNS):
                metrics.high_priority_count += 1
            elif any(p in file_path_lower for p in self.MEDIUM_PRIORITY_PATTERNS):
                metrics.medium_priority_count += 1
            elif any(p in file_path_lower for p in self.LOW_PRIORITY_PATTERNS):
                metrics.low_priority_count += 1
            else:
                metrics.medium_priority_count += 1
        
        metrics.unique_files = len(files)
        metrics.avg_relevance_score = sum(scores) / len(scores) if scores else 0.0
        metrics.min_relevance_score = min(scores) if scores else 0.0
        metrics.max_relevance_score = max(scores) if scores else 0.0
        metrics.language_distribution = dict(languages)
        
        return metrics
    
    def validate_expected_results(
        self,
        results: List[Dict[str, Any]],
        should_find: List[Dict[str, str]],
        should_not_find: Optional[List[Dict[str, str]]] = None,
        min_results: int = 1,
        min_relevance_score: float = 0.5
    ) -> ValidationResult:
        """
        Validate results against expected patterns.
        
        Args:
            results: Retrieved results
            should_find: Patterns expected to be found
            should_not_find: Patterns that should NOT be found
            min_results: Minimum expected results
            min_relevance_score: Minimum acceptable relevance score
            
        Returns:
            ValidationResult with pass/fail and details
        """
        validation = ValidationResult(
            passed=True,
            expected_found=[],
            expected_not_found=[]
        )
        should_not_find = should_not_find or []
        
        # Combine all result text for pattern matching
        result_texts = []
        for r in results:
            text = r.get('text', '')
            metadata = r.get('metadata', {})
            path = metadata.get('path', metadata.get('file_path', ''))
            semantic_names = metadata.get('semantic_names', [])
            primary_name = metadata.get('primary_name', '')
            
            # Combine searchable content
            combined = f"{text} {path} {' '.join(semantic_names)} {primary_name}"
            result_texts.append((combined, r.get('score', 0), r))
        
        # Check patterns that should be found
        for expected in should_find:
            pattern = expected.get('pattern', '')
            reason = expected.get('reason', '')
            
            exp_result = ExpectedResult(pattern=pattern, reason=reason)
            
            for combined_text, score, result in result_texts:
                if re.search(pattern, combined_text, re.IGNORECASE):
                    exp_result.found = True
                    exp_result.matched_text = result.get('text', '')[:100]
                    exp_result.matched_score = score
                    break
            
            validation.expected_found.append(exp_result)
            
            if not exp_result.found:
                validation.passed = False
                validation.issues.append(f"Expected pattern not found: '{pattern}' ({reason})")
        
        # Check patterns that should NOT be found
        for not_expected in should_not_find:
            pattern = not_expected.get('pattern', '')
            reason = not_expected.get('reason', '')
            
            exp_result = ExpectedResult(pattern=pattern, reason=reason)
            
            for combined_text, score, result in result_texts:
                if re.search(pattern, combined_text, re.IGNORECASE):
                    exp_result.found = True
                    exp_result.matched_text = result.get('text', '')[:100]
                    exp_result.matched_score = score
                    break
            
            validation.expected_not_found.append(exp_result)
            
            if exp_result.found:
                validation.passed = False
                validation.issues.append(f"Unexpected pattern found: '{pattern}' ({reason})")
        
        # Check minimum results
        if len(results) < min_results:
            validation.passed = False
            validation.issues.append(f"Too few results: {len(results)} < {min_results}")
        
        # Check relevance scores
        low_score_results = [r for r in results if r.get('score', 0) < min_relevance_score]
        if low_score_results and len(low_score_results) > len(results) / 2:
            validation.issues.append(
                f"Many low-relevance results: {len(low_score_results)} below {min_relevance_score}"
            )
        
        # Calculate precision/recall
        found_count = sum(1 for e in validation.expected_found if e.found)
        total_expected = len(validation.expected_found)
        
        validation.recall = found_count / total_expected if total_expected > 0 else 1.0
        
        # Precision: ratio of relevant results (high score) to total
        relevant_results = [r for r in results if r.get('score', 0) >= min_relevance_score]
        validation.precision = len(relevant_results) / len(results) if results else 0.0
        
        return validation
    
    def analyze_chunk_quality(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze the quality of retrieved chunks.
        
        Returns analysis of:
        - Chunk sizes
        - Metadata completeness
        - Content types
        """
        if not results:
            return {"error": "No results to analyze"}
        
        chunk_sizes = []
        metadata_scores = []
        
        for result in results:
            text = result.get('text', '')
            metadata = result.get('metadata', {})
            
            chunk_sizes.append(len(text))
            
            # Metadata completeness score (0-1)
            meta_score = 0.0
            if metadata.get('path') or metadata.get('file_path'):
                meta_score += 0.2
            if metadata.get('language'):
                meta_score += 0.1
            if metadata.get('semantic_names'):
                meta_score += 0.2
            if metadata.get('primary_name'):
                meta_score += 0.2
            if metadata.get('content_type'):
                meta_score += 0.1
            if metadata.get('parent_class'):
                meta_score += 0.1
            if metadata.get('docstring'):
                meta_score += 0.1
            
            metadata_scores.append(meta_score)
        
        return {
            "chunk_count": len(results),
            "avg_chunk_size": sum(chunk_sizes) / len(chunk_sizes) if chunk_sizes else 0,
            "min_chunk_size": min(chunk_sizes) if chunk_sizes else 0,
            "max_chunk_size": max(chunk_sizes) if chunk_sizes else 0,
            "avg_metadata_completeness": sum(metadata_scores) / len(metadata_scores) if metadata_scores else 0,
            "chunks_with_full_metadata": sum(1 for s in metadata_scores if s >= 0.8)
        }
    
    def generate_summary(
        self,
        results: List[Dict[str, Any]],
        validation: Optional[ValidationResult] = None
    ) -> str:
        """
        Generate human-readable summary of results.
        
        Args:
            results: Retrieved results
            validation: Optional validation result
            
        Returns:
            Formatted summary string
        """
        metrics = self.calculate_metrics(results)
        quality = self.analyze_chunk_quality(results)
        
        lines = [
            "=" * 60,
            "RAG RETRIEVAL ANALYSIS SUMMARY",
            "=" * 60,
            "",
            "📊 RESULT METRICS",
            f"   Total results: {metrics.total_results}",
            f"   Unique files: {metrics.unique_files}",
            f"   Avg relevance: {metrics.avg_relevance_score:.3f}",
            f"   Score range: {metrics.min_relevance_score:.3f} - {metrics.max_relevance_score:.3f}",
            "",
            "📁 CONTENT TYPES",
            f"   Functions/Classes: {metrics.functions_classes_count}",
            f"   Simplified Code: {metrics.simplified_code_count}",
            f"   Fallback: {metrics.fallback_count}",
            "",
            "🏷️ METADATA QUALITY",
            f"   With semantic names: {metrics.with_semantic_names}",
            f"   With docstrings: {metrics.with_docstrings}",
            f"   With parent class: {metrics.with_parent_class}",
            "",
            "⚡ FILE PRIORITIES",
            f"   High priority: {metrics.high_priority_count}",
            f"   Medium priority: {metrics.medium_priority_count}",
            f"   Low priority: {metrics.low_priority_count}",
            "",
            "📝 CHUNK QUALITY",
            f"   Avg chunk size: {quality['avg_chunk_size']:.0f} chars",
            f"   Size range: {quality['min_chunk_size']} - {quality['max_chunk_size']}",
            f"   Metadata completeness: {quality['avg_metadata_completeness']:.1%}",
        ]
        
        if metrics.language_distribution:
            lines.extend([
                "",
                "🌐 LANGUAGES",
            ])
            for lang, count in sorted(metrics.language_distribution.items(), key=lambda x: -x[1]):
                lines.append(f"   {lang}: {count}")
        
        if validation:
            lines.extend([
                "",
                "✅ VALIDATION",
                f"   Status: {'PASSED ✓' if validation.passed else 'FAILED ✗'}",
                f"   Precision: {validation.precision:.1%}",
                f"   Recall: {validation.recall:.1%}",
            ])
            
            if validation.issues:
                lines.append("   Issues:")
                for issue in validation.issues:
                    lines.append(f"     - {issue}")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
