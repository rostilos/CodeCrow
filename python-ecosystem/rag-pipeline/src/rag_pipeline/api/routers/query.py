"""Query endpoints — semantic search, PR context, deterministic context."""
import logging
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException

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
    from qdrant_client.models import Filter, FieldCondition, MatchValue

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

        if not query_parts:
            results, _ = index_manager.qdrant_client.scroll(
                collection_name=collection_name,
                scroll_filter=pr_filter,
                limit=top_k,
                with_payload=True,
                with_vectors=False
            )
            formatted = []
            for r in results:
                path = r.payload.get("path", "")
                text = r.payload.get("text", "")
                # Skip corrupted entries with missing path or empty text
                if not path or path == "unknown" or not text or not text.strip():
                    continue
                formatted.append({
                    "path": path,
                    "text": text,
                    "semantic_name": r.payload.get("semantic_name", ""),
                    "semantic_type": r.payload.get("semantic_type", ""),
                    "branch": r.payload.get("pr_branch", ""),
                    "_source": "pr_indexed"
                })
            return formatted

        query_text = " ".join(query_parts)

        query_embedding = index_manager.embed_model.get_text_embedding(query_text)

        results = index_manager.qdrant_client.search(
            collection_name=collection_name,
            query_vector=query_embedding,
            query_filter=pr_filter,
            limit=top_k,
            with_payload=True
        )

        formatted = []
        for r in results:
            path = r.payload.get("path", "")
            text = r.payload.get("text", "")
            # Skip corrupted entries with missing path or empty text
            if not path or path == "unknown" or not text or not text.strip():
                continue
            formatted.append({
                "path": path,
                "text": text,
                "score": r.score,
                "semantic_name": r.payload.get("semantic_name", ""),
                "semantic_type": r.payload.get("semantic_type", ""),
                "branch": r.payload.get("pr_branch", ""),
                "_source": "pr_indexed"
            })

        return formatted

    except Exception as e:
        logger.warning(f"Error querying PR-indexed data: {e}")
        return []


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
            pr_changed_files=request.pr_changed_files
        )
        return {"context": context}
    except Exception as e:
        logger.error(f"Error getting deterministic context: {e}")
        raise HTTPException(status_code=500, detail=str(e))
