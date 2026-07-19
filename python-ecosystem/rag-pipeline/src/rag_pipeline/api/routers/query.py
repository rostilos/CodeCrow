"""Query endpoints — semantic search, PR context, deterministic context."""
import logging
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException
from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

from ..models import QueryRequest, PRContextRequest, DeterministicContextRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["query"])


def _get_singletons():
    """Get lifecycle-managed singletons from the api module."""
    from ..api import index_manager, query_service
    return index_manager, query_service


@router.post("/query/search")
def semantic_search(request: QueryRequest):
    """Perform semantic search."""
    _, query_service = _get_singletons()
    try:
        results = query_service.semantic_search(
            query=request.query,
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            top_k=request.top_k,
            filter_language=request.filter_language
        )
        return {"results": results}
    except Exception as e:
        logger.error(f"Error performing search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query/pr-context")
def get_pr_context(request: PRContextRequest):
    """
    Get context for PR review with multi-branch support and optional hybrid mode.

    When pr_number is provided, uses HYBRID query:
    1. Query PR-indexed chunks (pr=true, pr_number=X)
    2. Query branch data, excluding files that are in the PR
    3. Merge results with PR data taking priority
    """
    index_manager, query_service = _get_singletons()
    try:
        if not request.branch:
            logger.warning("Branch not provided in PR context request, returning empty context")
            return {
                "context": {
                    "relevant_code": [],
                    "related_files": [],
                    "changed_files": request.changed_files,
                    "_metadata": {
                        "skipped_reason": "branch_not_provided",
                        "changed_files_count": len(request.changed_files),
                        "result_count": 0
                    }
                }
            }

        pr_results = []

        # HYBRID MODE: Query PR-indexed data first if pr_number is provided
        if request.pr_number:
            pr_results = _query_pr_indexed_data(
                index_manager=index_manager,
                workspace=request.workspace,
                project=request.project,
                pr_number=request.pr_number,
                changed_files=request.changed_files,
                query_texts=request.diff_snippets or [],
                pr_title=request.pr_title,
                top_k=request.top_k or 15
            )
            logger.info(f"Hybrid mode: Found {len(pr_results)} PR-specific chunks for PR #{request.pr_number}")

        # Get branch context
        context = query_service.get_context_for_pr(
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            changed_files=request.changed_files,
            diff_snippets=request.diff_snippets or [],
            pr_title=request.pr_title,
            pr_description=request.pr_description,
            top_k=request.top_k,
            enable_priority_reranking=request.enable_priority_reranking,
            min_relevance_score=request.min_relevance_score,
            base_branch=request.base_branch,
            deleted_files=request.deleted_files or [],
            exclude_pr_files=(request.all_pr_changed_files or []) if request.pr_number else []
        )

        # Merge PR results with branch results (PR first, then branch)
        if pr_results:
            pr_paths = set()
            merged_code = []

            for pr_chunk in pr_results:
                merged_code.append(pr_chunk)
                path = pr_chunk.get("path", "")
                if path:
                    pr_paths.add(path)

            for branch_chunk in context.get("relevant_code", []):
                path = branch_chunk.get("path", "")
                if path not in pr_paths:
                    merged_code.append(branch_chunk)

            context["relevant_code"] = merged_code
            context["_pr_chunks_count"] = len(pr_results)

        context["_metadata"] = {
            "priority_reranking_enabled": request.enable_priority_reranking,
            "min_relevance_score": request.min_relevance_score,
            "changed_files_count": len(request.changed_files),
            "result_count": len(context.get("relevant_code", [])),
            "branches_searched": context.get("_branches_searched", [request.branch]),
            "hybrid_mode": request.pr_number is not None,
            "pr_number": request.pr_number
        }

        return {"context": context}
    except Exception as e:
        logger.error(f"Error getting PR context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _query_pr_indexed_data(
    index_manager,
    workspace: str,
    project: str,
    pr_number: int,
    changed_files: List[str],
    query_texts: List[str],
    pr_title: Optional[str],
    top_k: int = 15
) -> List[Dict]:
    """
    Query PR-indexed chunks from the main collection.

    Filters by pr=true and pr_number to get only PR-specific data.
    When no meaningful query text exists, uses scroll() instead of
    wasting an embedding call on a fabricated query.
    """
    try:
        collection_name = index_manager._get_project_collection_name(workspace, project)

        if not index_manager._collection_manager.collection_exists(collection_name):
            return []

        query_parts = []
        if pr_title:
            query_parts.append(pr_title)
        if query_texts:
            query_parts.extend(query_texts)

        pr_filter = Filter(
            must=[
                FieldCondition(key="pr", match=MatchValue(value=True)),
                FieldCondition(key="pr_number", match=MatchValue(value=pr_number))
            ]
        )

        direct_file_results = _fetch_direct_pr_file_chunks(
            index_manager=index_manager,
            collection_name=collection_name,
            pr_filter=pr_filter,
            changed_files=changed_files,
        )

        if not query_parts:
            results, _ = index_manager.qdrant_client.scroll(
                collection_name=collection_name,
                scroll_filter=pr_filter,
                limit=top_k,
                with_payload=True,
                with_vectors=False
            )
            formatted = _format_pr_results(results)
            return _merge_pr_results(direct_file_results, formatted)

        query_text = " ".join(query_parts)

        query_embedding = index_manager.embed_model.get_text_embedding(query_text)

        response = index_manager.qdrant_client.query_points(
            collection_name=collection_name,
            query=query_embedding,
            query_filter=pr_filter,
            limit=top_k,
            with_payload=True,
        )
        results = response.points

        formatted = _format_pr_results(results)

        return _merge_pr_results(direct_file_results, formatted)

    except Exception as e:
        logger.warning(f"Error querying PR-indexed data: {e}")
        return []


def _normalize_changed_file_candidates(changed_files: List[str]) -> List[str]:
    candidates = []
    seen = set()

    for path in changed_files or []:
        if not path:
            continue

        for candidate in (path, path.lstrip("/")):
            if candidate and candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)

    return candidates


def _fetch_direct_pr_file_chunks(index_manager, collection_name: str, pr_filter: Filter, changed_files: List[str]) -> List[Dict]:
    path_candidates = _normalize_changed_file_candidates(changed_files)
    if not path_candidates:
        return []

    direct_filter = Filter(
        must=[
            *pr_filter.must,
            FieldCondition(key="path", match=MatchAny(any=path_candidates)),
        ]
    )

    limit = max(32, len(path_candidates) * 8)
    results, _ = index_manager.qdrant_client.scroll(
        collection_name=collection_name,
        scroll_filter=direct_filter,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )

    formatted = _format_pr_results(results, forced_match_type="changed_file", forced_score=1.0)
    if formatted:
        logger.info("Hybrid mode: force-including %d PR-indexed chunk(s) for %d changed file(s)", len(formatted), len(path_candidates))
    return formatted


def _format_pr_results(results, forced_match_type: Optional[str] = None, forced_score: Optional[float] = None) -> List[Dict]:
    formatted = []
    for r in results:
        payload = getattr(r, "payload", None) or {}
        path = payload.get("path", "")
        text = payload.get("text", "")
        if not path or path == "unknown" or not text or not text.strip():
            continue

        item = {
            "path": path,
            "text": text,
            "semantic_name": payload.get("semantic_name", ""),
            "semantic_type": payload.get("semantic_type", ""),
            "branch": payload.get("pr_branch", ""),
            "_source": "pr_indexed",
        }

        score = getattr(r, "score", None)
        if forced_score is not None:
            item["score"] = forced_score
        elif score is not None:
            item["score"] = score

        if forced_match_type:
            item["_match_type"] = forced_match_type

        formatted.append(item)

    return formatted


def _merge_pr_results(priority_results: List[Dict], semantic_results: List[Dict]) -> List[Dict]:
    merged = []
    seen = set()

    for chunk in [*(priority_results or []), *(semantic_results or [])]:
        key = (chunk.get("path", ""), chunk.get("text", "")[:200])
        if key in seen:
            continue
        seen.add(key)
        merged.append(chunk)

    return merged


@router.post("/query/deterministic")
def get_deterministic_context(request: DeterministicContextRequest):
    """
    Get context using DETERMINISTIC metadata-based retrieval.

    No language-specific parsing needed - tree-sitter already did it during indexing.
    Predictable: same input = same output.
    """
    _, query_service = _get_singletons()
    try:
        context = query_service.get_deterministic_context(
            workspace=request.workspace,
            project=request.project,
            branches=request.branches,
            file_paths=request.file_paths,
            limit_per_file=request.limit_per_file or 10,
            pr_number=request.pr_number,
            pr_changed_files=request.pr_changed_files,
            additional_identifiers=request.additional_identifiers
        )
        return {"context": context}
    except Exception as e:
        logger.error(f"Error getting deterministic context: {e}")
        raise HTTPException(status_code=500, detail=str(e))
