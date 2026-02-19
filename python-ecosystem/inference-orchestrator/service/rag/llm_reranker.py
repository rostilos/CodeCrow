"""
LLM-based reranking service for RAG results.

Implements two strategies:
1. LLM listwise reranking — sends snippets to Gemini Flash for intelligent ordering
2. Heuristic fallback — PR-file proximity + directory match + type penalty

The reranker is designed to be ALWAYS-ON for any PR with ≥5 RAG chunks,
ensuring every review gets intelligent context ordering.
"""
import os
import logging
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

LLM_RERANK_ENABLED = os.environ.get("LLM_RERANK_ENABLED", "true").lower() == "true"
# Minimum number of result chunks to trigger reranking
LLM_RERANK_THRESHOLD = int(os.environ.get("LLM_RERANK_THRESHOLD", "5"))
# Maximum items to send to LLM for reranking (keep small for speed/cost)
MAX_ITEMS_FOR_LLM = int(os.environ.get("LLM_RERANK_MAX_ITEMS", "15"))


@dataclass
class RerankResult:
    """Result of reranking operation."""
    original_count: int
    reranked_count: int
    processing_time_ms: float
    method: str  # "llm", "heuristic", "none", "fallback"
    success: bool
    error: Optional[str] = None


class LLMReranker:
    """
    LLM-based reranking for RAG results.
    Uses listwise ranking via Gemini Flash for intelligent ordering,
    with heuristic fallback for speed or when LLM parsing fails.
    """

    RERANK_PROMPT_TEMPLATE = """You are an expert code reviewer reranking RAG results for a PR review.

PR CONTEXT:
- Title: {pr_title}
- Description: {pr_description}
- Changed files in this PR: {changed_files}

CODE SNIPPETS TO RANK (each has an ID, file path, and code preview):
{snippets_json}

RANKING CRITERIA (in order of importance):
1. **PR-file relevance**: Code from files IN the changed files list, or that directly import/reference those files, is MOST relevant
2. **Same module/package**: Code in the same directory or package as changed files
3. **Direct dependency**: Code that is imported by or imports the changed files
4. **Similar functionality**: Code that implements similar patterns (useful for duplication detection)
5. **Test coverage**: Tests for the changed functionality

CRITICAL RULES:
- Files from COMPLETELY UNRELATED modules/packages should be ranked LOWEST
- A file in the same directory/package as a changed file is MUCH more relevant than a file with high textual similarity from a different module
- Code that happens to use similar method names but is in a different feature area should be ranked LOW

Return ONLY a JSON object:
{{"rankings": [<id1>, <id2>, ...], "reasoning": "<brief explanation>"}}

Order IDs from MOST to LEAST relevant. Include ALL IDs. Return ONLY valid JSON."""

    def __init__(self, llm_client=None):
        """
        Initialize reranker.

        Args:
            llm_client: LangChain-compatible LLM client for reranking
        """
        self.llm_client = llm_client

    async def rerank(
        self,
        results: List[Dict[str, Any]],
        pr_title: Optional[str] = None,
        pr_description: Optional[str] = None,
        changed_files: Optional[List[str]] = None,
        use_llm: bool = True
    ) -> tuple[List[Dict[str, Any]], RerankResult]:
        """
        Rerank RAG results for better relevance.

        Decision logic:
        - LLM reranking if: enabled + client available + enough results
        - Heuristic fallback otherwise (or on LLM failure)
        - Both methods benefit from PR changed file awareness

        Args:
            results: RAG search results to rerank
            pr_title: PR title for context
            pr_description: PR description for context
            changed_files: List of changed file paths
            use_llm: Whether to use LLM for reranking

        Returns:
            Tuple of (reranked results, rerank metadata)
        """
        start_time = datetime.now()

        if not results:
            return results, RerankResult(
                original_count=0, reranked_count=0,
                processing_time_ms=0, method="none", success=True
            )

        # Decide reranking method
        should_use_llm = (
            use_llm
            and LLM_RERANK_ENABLED
            and self.llm_client is not None
            and len(results) >= LLM_RERANK_THRESHOLD
        )

        try:
            if should_use_llm:
                reranked = await self._llm_rerank(
                    results[:MAX_ITEMS_FOR_LLM],
                    pr_title, pr_description, changed_files
                )
                # Append remaining results that weren't sent to LLM
                if len(results) > MAX_ITEMS_FOR_LLM:
                    reranked.extend(results[MAX_ITEMS_FOR_LLM:])
                method = "llm"
            else:
                reranked = self._heuristic_rerank(results, changed_files)
                method = "heuristic"

            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

            return reranked, RerankResult(
                original_count=len(results),
                reranked_count=len(reranked),
                processing_time_ms=elapsed_ms,
                method=method, success=True
            )

        except Exception as e:
            logger.warning(f"Reranking failed, returning original order: {e}")
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

            return results, RerankResult(
                original_count=len(results),
                reranked_count=len(results),
                processing_time_ms=elapsed_ms,
                method="fallback", success=False, error=str(e)
            )

    async def _llm_rerank(
        self,
        results: List[Dict[str, Any]],
        pr_title: Optional[str],
        pr_description: Optional[str],
        changed_files: Optional[List[str]]
    ) -> List[Dict[str, Any]]:
        """Use LLM to rerank results with PR-aware context."""
        # Prepare snippets — truncate previews to save tokens
        snippets = []
        for i, result in enumerate(results):
            path = result.get("metadata", {}).get("path", "unknown")
            text = result.get("text", "")
            # Truncate long previews to ~500 chars for token efficiency
            preview = text[:500] + "..." if len(text) > 500 else text
            snippets.append({
                "id": i,
                "path": path,
                "preview": preview,
                "original_score": round(result.get("score", 0), 3)
            })

        # Build prompt
        prompt = self.RERANK_PROMPT_TEMPLATE.format(
            pr_title=pr_title or "Not provided",
            pr_description=(pr_description or "Not provided")[:300],
            changed_files=", ".join(changed_files) if changed_files else "Not provided",
            snippets_json=json.dumps(snippets, indent=2)
        )

        # Call LLM
        response = await self.llm_client.ainvoke(prompt)

        # Extract text from response (handles various LangChain response types)
        response_text = self._extract_response_text(response)

        # Parse response
        try:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                parsed = json.loads(json_str)
                rankings = parsed.get("rankings", [])

                if rankings:
                    # Reorder results based on LLM ranking
                    reranked = []
                    for idx in rankings:
                        if isinstance(idx, int) and 0 <= idx < len(results):
                            result = results[idx].copy()
                            result["_llm_rank"] = len(reranked) + 1
                            reranked.append(result)

                    # Add any missing items at the end
                    included_ids = set(rankings)
                    for i, result in enumerate(results):
                        if i not in included_ids:
                            result_copy = result.copy()
                            result_copy["_llm_rank"] = len(reranked) + 1
                            reranked.append(result_copy)

                    logger.info(f"LLM reranking successful: {len(reranked)} items reordered")
                    if parsed.get("reasoning"):
                        logger.debug(f"LLM reasoning: {parsed['reasoning']}")
                    return reranked
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM reranking response: {e}")

        # Fallback to heuristic if LLM parsing failed
        logger.warning("LLM reranking parse failed, falling back to heuristic")
        return self._heuristic_rerank(results, changed_files)

    @staticmethod
    def _extract_response_text(response) -> str:
        """Extract text content from various LangChain response types."""
        if hasattr(response, 'content'):
            content = response.content
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict) and 'text' in item:
                        parts.append(item['text'])
                    elif hasattr(item, 'text'):
                        parts.append(item.text)
                return "".join(parts)
            return str(content)
        return str(response)

    def _heuristic_rerank(
        self,
        results: List[Dict[str, Any]],
        changed_files: Optional[List[str]]
    ) -> List[Dict[str, Any]]:
        """
        Heuristic reranking with strong PR-file proximity awareness.

        Scoring factors (in order of impact):
        1. PR-file match: chunk IS from a changed file (+50% boost)
        2. Directory proximity: chunk is in the same directory as a changed file (+30%)
        3. Same extension: chunk shares file type with changed files (+10%)
        4. Penalty: test/spec files (-20%), config files (-30%)
        """
        # Pre-compute changed file metadata
        changed_set = set()
        changed_dirs = set()
        changed_extensions = set()
        changed_basenames = set()

        if changed_files:
            for f in changed_files:
                changed_set.add(f)
                parts = f.rsplit('/', 1)
                if len(parts) > 1:
                    changed_dirs.add(parts[0])
                    changed_basenames.add(parts[1])
                else:
                    changed_basenames.add(f)
                ext = f.rsplit('.', 1)[-1] if '.' in f else ''
                if ext:
                    changed_extensions.add(ext)

        scored_results = []
        for result in results:
            score = result.get("score", 0)
            metadata = result.get("metadata", {})
            path = metadata.get("path", result.get("path", ""))

            # Factor 1: PR-file match (strongest signal — this IS a changed file)
            is_pr_file = (
                path in changed_set
                or any(path.endswith(f) or f.endswith(path) for f in changed_set)
            )
            if is_pr_file:
                score *= 1.5

            # Factor 2: Same directory as changed files
            result_dir = path.rsplit('/', 1)[0] if '/' in path else ''
            if result_dir and result_dir in changed_dirs:
                score *= 1.3

            # Factor 3: Same extension as changed files
            result_ext = path.rsplit('.', 1)[-1] if '.' in path else ''
            if result_ext in changed_extensions:
                score *= 1.1

            # Factor 4: Penalties
            path_lower = path.lower()
            if 'test' in path_lower or 'spec' in path_lower:
                score *= 0.8
            if any(path_lower.endswith(ext) for ext in ('.json', '.yaml', '.yml', '.toml', '.ini', '.xml')):
                score *= 0.7

            result_copy = result.copy()
            result_copy["_heuristic_score"] = round(score, 4)
            scored_results.append((score, result_copy))

        # Sort by adjusted score
        scored_results.sort(key=lambda x: x[0], reverse=True)

        return [r[1] for r in scored_results]
