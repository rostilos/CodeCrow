"""
PR context retrieval module for RAG query service.

Orchestrates multi-branch semantic search for pull request reviews:
query decomposition, execution, deduplication, scoring, and ranking.
"""
from typing import Dict, List, Optional
from collections import defaultdict
import os
import logging

from .base import RAGQueryBase
from .duplication import generate_duplication_queries
from ..models.instructions import InstructionType
from ..models.scoring_config import get_scoring_config

logger = logging.getLogger(__name__)


class PRContextMixin:
    """PR context retrieval capabilities for RAGQueryService.

    Provides get_context_for_pr() — the main entry point for PR review
    context. Also includes query decomposition and result ranking.
    """

    def get_context_for_pr(
            self: RAGQueryBase,
            workspace: str,
            project: str,
            branch: str,
            changed_files: List[str],
            diff_snippets: Optional[List[str]] = None,
            pr_title: Optional[str] = None,
            pr_description: Optional[str] = None,
            top_k: int = 15,
            enable_priority_reranking: bool = True,
            min_relevance_score: float = 0.7,
            base_branch: Optional[str] = None,
            deleted_files: Optional[List[str]] = None,
            exclude_pr_files: Optional[List[str]] = None
    ) -> Dict:
        """
        Get relevant context for PR review using Smart RAG with multi-branch support.

        Queries both target branch and base branch to preserve cross-file relationships.
        Results are deduplicated with target branch taking priority for same files.

        Args:
            branch: Target branch (the PR's source branch)
            base_branch: Base branch (the PR's target, e.g., 'main'). If None, uses fallback logic.
            deleted_files: Files that were deleted in target branch (excluded from results)
            exclude_pr_files: Files indexed separately as PR data (excluded to avoid duplication)
        """
        diff_snippets = diff_snippets or []
        deleted_files = deleted_files or []
        exclude_pr_files = exclude_pr_files or []

        all_excluded_paths = list(set(deleted_files + exclude_pr_files))

        # Determine branches to search
        branches_to_search = [branch]
        effective_base_branch = base_branch

        collection_name = self._get_project_collection_name(workspace, project)

        if not self._collection_or_alias_exists(collection_name):
            logger.warning(f"Collection {collection_name} does not exist")
            return {
                "relevant_code": [],
                "related_files": [],
                "changed_files": changed_files,
                "_error": "collection_not_found"
            }

        # Add base branch to search
        if base_branch:
            branches_to_search.append(base_branch)
        else:
            fallback = self._get_fallback_branch(workspace, project, branch)
            if fallback:
                branches_to_search.append(fallback)
                effective_base_branch = fallback

        branches_to_search = list(dict.fromkeys(branches_to_search))

        logger.info(
            f"Smart RAG: Multi-branch query for {len(changed_files)} files "
            f"(branches={branches_to_search}, priority_reranking={enable_priority_reranking})")

        # 1. Decompose into multiple targeted queries
        queries = self._decompose_queries(
            pr_title=pr_title,
            pr_description=pr_description,
            diff_snippets=diff_snippets,
            changed_files=changed_files
        )

        logger.info(f"Generated {len(queries)} queries for PR context")
        for i, (q_text, q_weight, q_top_k, q_type) in enumerate(queries):
            logger.info(f"  Query {i+1}: weight={q_weight}, top_k={q_top_k}, text='{q_text[:80]}...'")

        all_results = []

        # 2. Execute queries with multi-branch search
        for i, (q_text, q_weight, q_top_k, q_instruction_type) in enumerate(queries):
            if not q_text.strip():
                continue

            results = self.semantic_search_multi_branch(
                query=q_text,
                workspace=workspace,
                project=project,
                branches=branches_to_search,
                top_k=q_top_k,
                instruction_type=q_instruction_type,
                excluded_paths=all_excluded_paths
            )

            logger.info(f"Query {i+1}/{len(queries)} returned {len(results)} results")

            for r in results:
                r["_query_weight"] = q_weight

            all_results.extend(results)

        # 3. Deduplicate by branch priority
        deduped_results = self._dedupe_by_branch_priority(
            all_results,
            target_branch=branch,
            base_branch=effective_base_branch
        )

        # 4. Merge, filter, and rank
        final_results = self._merge_and_rank_results(
            deduped_results,
            min_score_threshold=min_relevance_score if enable_priority_reranking else 0.5
        )

        # 5. Fallback if filtering was too aggressive
        if not final_results and deduped_results:
            logger.info("Smart RAG: threshold too strict, falling back to top raw results")
            raw_sorted = sorted(deduped_results, key=lambda x: x['score'], reverse=True)
            seen = set()
            unique_fallback = []
            for r in raw_sorted:
                content_hash = f"{r['metadata'].get('file_path', '')}:{r['text']}"
                if content_hash not in seen:
                    seen.add(content_hash)
                    unique_fallback.append(r)
            final_results = unique_fallback[:5]

        # Group by file for final output
        relevant_code = []
        related_files = set()

        for result in final_results:
            relevant_code.append({
                "text": result["text"],
                "score": result["score"],
                "metadata": result["metadata"]
            })

            if "path" in result["metadata"]:
                related_files.add(result["metadata"]["path"])

        logger.info(f"Smart RAG: Final context has {len(relevant_code)} chunks from {len(related_files)} files")
        for i, r in enumerate(relevant_code[:5]):
            path = r["metadata"].get("path", "unknown")
            primary_name = r["metadata"].get("primary_name", "N/A")
            logger.info(f"  Chunk {i+1}: score={r['score']:.3f}, name={primary_name}, path=...{path[-60:]}")

        return {
            "relevant_code": relevant_code,
            "related_files": list(related_files),
            "changed_files": changed_files,
            "_branches_searched": branches_to_search
        }

    def _decompose_queries(
            self,
            pr_title: Optional[str],
            pr_description: Optional[str],
            diff_snippets: List[str],
            changed_files: List[str]
    ) -> List[tuple]:
        """Generate a list of (query_text, weight, top_k, instruction_type) tuples."""
        queries = []

        # A. Intent Query (High Level) - Weight 1.0
        intent_parts = []
        if pr_title:
            intent_parts.append(pr_title)
        if pr_description:
            intent_parts.append(pr_description)

        if intent_parts:
            queries.append((" ".join(intent_parts), 1.0, 10, InstructionType.GENERAL))

        # B. File Context Queries (Mid Level) - Weight 0.8
        dir_groups = defaultdict(list)
        for f in changed_files:
            d = os.path.dirname(f)
            d = d if d else "root"
            dir_groups[d].append(os.path.basename(f))

        sorted_dirs = sorted(dir_groups.items(), key=lambda x: len(x[1]), reverse=True)

        for dir_path, files in sorted_dirs[:8]:
            display_files = files[:10]
            files_str = ", ".join(display_files)
            if len(files) > 10:
                files_str += "..."

            clean_path = "root directory" if dir_path == "root" else dir_path
            q = f"logic in {clean_path} related to {files_str}"

            queries.append((q, 0.8, 5, InstructionType.LOGIC))

        # C. Snippet Queries (Low Level) - Weight 1.2
        for snippet in diff_snippets[:10]:
            lines = []
            for line in snippet.split('\n'):
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith(('diff --git', '---', '+++', '@@', 'index ')):
                    continue
                if stripped.startswith('+') or stripped.startswith('-'):
                    code_line = stripped[1:].strip()
                    if code_line and len(code_line) > 3:
                        lines.append(code_line)
                elif stripped:
                    lines.append(stripped)

            if lines:
                clean_snippet = " ".join(lines[:10])
                if len(clean_snippet) > 15:
                    queries.append((clean_snippet, 1.2, 8, InstructionType.DEPENDENCY))

        # D. Duplication Detection Queries - Weight 1.3
        duplication_queries = generate_duplication_queries(
            diff_snippets=diff_snippets,
            changed_files=changed_files
        )
        queries.extend(duplication_queries)

        logger.debug(f"Decomposed into {len(queries)} queries: {[(q[0][:50], q[1]) for q in queries]}")
        return queries

    def _merge_and_rank_results(self, results: List[Dict], min_score_threshold: float = 0.75) -> List[Dict]:
        """
        Deduplicate matches and apply content-type score adjustment.

        Only content-type boost is applied here (functions_classes > fallback > oversized > simplified).
        Intelligent reranking is handled downstream by the LLM reranker.
        """
        scoring_config = get_scoring_config()
        grouped = {}

        for r in results:
            key = f"{r['metadata'].get('file_path', 'unknown')}_{hash(r['text'])}"
            if key not in grouped:
                grouped[key] = r
            else:
                if r['score'] > grouped[key]['score']:
                    grouped[key] = r

        unique_results = list(grouped.values())

        for result in unique_results:
            metadata = result.get('metadata', {})
            content_type = metadata.get('content_type', 'fallback')
            density = metadata.get('information_density', -1.0)

            boosted_score, _ = scoring_config.calculate_boosted_score(
                base_score=result['score'],
                content_type=content_type,
                information_density=density,
            )

            result['score'] = boosted_score
            result['_content_type'] = content_type

        filtered = [r for r in unique_results if r['score'] >= min_score_threshold]
        filtered.sort(key=lambda x: x['score'], reverse=True)

        return filtered
