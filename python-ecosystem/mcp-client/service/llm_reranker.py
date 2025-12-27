"""
LLM-based reranking service for RAG results.
Implements listwise reranking for improved relevance in large PRs.
"""
import os
import logging
import json
import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

LLM_RERANK_ENABLED = os.environ.get("LLM_RERANK_ENABLED", "false").lower() == "true"
# Threshold for when to use LLM reranking (file count)
LLM_RERANK_THRESHOLD = int(os.environ.get("LLM_RERANK_THRESHOLD", "20"))
# Maximum items to send to LLM for reranking
MAX_ITEMS_FOR_LLM = int(os.environ.get("LLM_RERANK_MAX_ITEMS", "20"))



@dataclass
class RerankResult:
    """Result of reranking operation."""
    original_count: int
    reranked_count: int
    processing_time_ms: float
    method: str  # "llm" or "heuristic"
    success: bool
    error: Optional[str] = None


class LLMReranker:
    """
    LLM-based reranking for RAG results.
    Uses listwise ranking approach for better relevance ordering.
    """
    
    RERANK_PROMPT_TEMPLATE = """You are an expert code reviewer. Given a PR context and code snippets from a codebase, 
rank the snippets by their RELEVANCE to reviewing this PR.

PR CONTEXT:
- Title: {pr_title}
- Description: {pr_description}
- Changed files: {changed_files}

CODE SNIPPETS TO RANK:
{snippets_json}

RANKING CRITERIA (in order of importance):
1. Direct dependency - code that imports or is imported by changed files
2. Same module/package - code in the same logical area
3. Similar functionality - code that does similar things
4. Shared patterns - code that uses similar patterns or APIs
5. Test coverage - tests for the changed functionality

Return ONLY a JSON object with this exact structure:
{{"rankings": [<id1>, <id2>, <id3>, ...], "reasoning": "<brief explanation>"}}

Where the IDs are ordered from MOST relevant to LEAST relevant.
Include ALL snippet IDs in your ranking. Return ONLY valid JSON, no other text."""

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
        
        Args:
            results: RAG search results to rerank
            pr_title: PR title for context
            pr_description: PR description for context
            changed_files: List of changed file paths
            use_llm: Whether to use LLM for reranking (falls back to heuristic if False or fails)
            
        Returns:
            Tuple of (reranked results, rerank metadata)
        """
        start_time = datetime.now()
        
        if not results:
            return results, RerankResult(
                original_count=0,
                reranked_count=0,
                processing_time_ms=0,
                method="none",
                success=True
            )
        
        # Decide reranking method
        should_use_llm = (
            use_llm and 
            self.llm_client is not None and 
            len(results) >= self.LLM_RERANK_THRESHOLD
        )
        
        try:
            if should_use_llm:
                reranked = await self._llm_rerank(
                    results[:self.MAX_ITEMS_FOR_LLM],
                    pr_title,
                    pr_description,
                    changed_files
                )
                # Append remaining results that weren't sent to LLM
                if len(results) > self.MAX_ITEMS_FOR_LLM:
                    reranked.extend(results[self.MAX_ITEMS_FOR_LLM:])
                method = "llm"
            else:
                reranked = self._heuristic_rerank(results, changed_files)
                method = "heuristic"
            
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            return reranked, RerankResult(
                original_count=len(results),
                reranked_count=len(reranked),
                processing_time_ms=elapsed_ms,
                method=method,
                success=True
            )
            
        except Exception as e:
            logger.warning(f"Reranking failed, returning original order: {e}")
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            return results, RerankResult(
                original_count=len(results),
                reranked_count=len(results),
                processing_time_ms=elapsed_ms,
                method="fallback",
                success=False,
                error=str(e)
            )
    
    async def _llm_rerank(
        self,
        results: List[Dict[str, Any]],
        pr_title: Optional[str],
        pr_description: Optional[str],
        changed_files: Optional[List[str]]
    ) -> List[Dict[str, Any]]:
        """Use LLM to rerank results."""
        # Prepare snippets for LLM
        snippets = []
        for i, result in enumerate(results):
            path = result.get("metadata", {}).get("path", "unknown")
            text_preview = result.get("text", "")
            snippets.append({
                "id": i,
                "path": path,
                "preview": text_preview,
                "original_score": result.get("score", 0)
            })
        
        # Build prompt
        prompt = self.RERANK_PROMPT_TEMPLATE.format(
            pr_title=pr_title or "Not provided",
            pr_description=(pr_description or "Not provided"),
            changed_files=", ".join(changed_files) if changed_files else "Not provided",
            snippets_json=json.dumps(snippets, indent=2)
        )
        
        # Call LLM
        response = await self.llm_client.ainvoke(prompt)
        response_text = response.content if hasattr(response, 'content') else str(response)
        
        # Parse response
        try:
            # Try to extract JSON from response
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                parsed = json.loads(json_str)
                rankings = parsed.get("rankings", [])
                
                if rankings and len(rankings) == len(results):
                    # Reorder results based on LLM ranking
                    reranked = []
                    for idx in rankings:
                        if 0 <= idx < len(results):
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
                    
                    logger.info(f"LLM reranking successful: {len(reranked)} items")
                    return reranked
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM reranking response: {e}")
        
        # Fallback to heuristic if LLM parsing failed
        logger.warning("LLM reranking parse failed, falling back to heuristic")
        return self._heuristic_rerank(results, changed_files)
    
    def _heuristic_rerank(
        self,
        results: List[Dict[str, Any]],
        changed_files: Optional[List[str]]
    ) -> List[Dict[str, Any]]:
        """
        Heuristic-based reranking when LLM is not available or for smaller result sets.
        
        Scoring factors:
        1. Original relevance score (from embedding similarity)
        2. Path proximity to changed files
        3. File type priority (implementation > test > config)
        """
        changed_dirs = set()
        changed_extensions = set()
        
        if changed_files:
            for f in changed_files:
                parts = f.rsplit('/', 1)
                if len(parts) > 1:
                    changed_dirs.add(parts[0])
                ext = f.rsplit('.', 1)[-1] if '.' in f else ''
                changed_extensions.add(ext)
        
        scored_results = []
        for result in results:
            score = result.get("score", 0)
            path = result.get("metadata", {}).get("path", "")
            
            # Boost for same directory as changed files
            result_dir = path.rsplit('/', 1)[0] if '/' in path else ''
            if result_dir in changed_dirs:
                score *= 1.3
            
            # Boost for same extension as changed files
            result_ext = path.rsplit('.', 1)[-1] if '.' in path else ''
            if result_ext in changed_extensions:
                score *= 1.1
            
            # Penalize test files (less relevant for review context)
            path_lower = path.lower()
            if 'test' in path_lower or 'spec' in path_lower:
                score *= 0.8
            
            # Penalize config files
            if any(ext in path_lower for ext in ['.json', '.yaml', '.yml', '.toml', '.ini']):
                score *= 0.7
            
            result_copy = result.copy()
            result_copy["_heuristic_score"] = score
            scored_results.append((score, result_copy))
        
        # Sort by adjusted score
        scored_results.sort(key=lambda x: x[0], reverse=True)
        
        return [r[1] for r in scored_results]
