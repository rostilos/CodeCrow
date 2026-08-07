"""Query endpoints — semantic search, PR context, deterministic context."""
import logging
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException
from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

from ..models import QueryRequest, PRContextRequest, DeterministicContextRequest
from ...core.revision_binding import (
    require_repository_generation,
    require_same_repository_generation,
)
from ...core.repository_overlay import IncrementalIndexPreconditionError
from ...core.pr_overlay_manifest import (
    PR_OVERLAY_MANIFEST_PAYLOAD_KEY,
    read_pr_overlay_generation,
)
logger = logging.getLogger(__name__)
router = APIRouter(tags=["query"])


def _get_singletons():
    """Get lifecycle-managed singletons from the api module."""
    from ..api import index_manager, query_service
    return index_manager, query_service


def _optional_string(value) -> Optional[str]:
    """Ignore absent/loose legacy DTO attributes instead of binding them."""
    return value if isinstance(value, str) and value else None


def _authoritative_pr_branch(request: PRContextRequest) -> Optional[str]:
    """Return target-branch truth for hybrid PR retrieval.

    Older inference clients sent the PR source as ``branch`` and the target as
    ``base_branch``. Once a PR number is present, the overlay already represents
    source changes, so querying source-branch repository vectors would mix a
    second, potentially stale representation into the review.
    """
    if request.pr_number and request.base_branch:
        return request.base_branch
    return request.branch


@router.post("/query/search")
def semantic_search(request: QueryRequest):
    """Perform semantic search."""
    index_manager, query_service = _get_singletons()
    try:
        repository_revision = _optional_string(
            request.repository_revision
        )
        generation_manifest = _optional_string(
            request.repository_generation_manifest_sha256
        )
        collection_target = _optional_string(request.collection_target)
        receipt = None
        if repository_revision:
            receipt = require_repository_generation(
                index_manager=index_manager,
                workspace=request.workspace,
                project=request.project,
                branch=request.branch,
                revision=repository_revision,
                generation_manifest_sha256=generation_manifest,
                collection_target=collection_target,
            )
        results = query_service.semantic_search(
            query=request.query,
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            top_k=request.top_k,
            filter_language=request.filter_language,
            expected_revision=repository_revision,
            collection_target=(
                receipt["_collection_target"] if receipt else collection_target
            ),
        )
        if receipt:
            require_same_repository_generation(
                index_manager,
                workspace=request.workspace,
                project=request.project,
                branch=request.branch,
                revision=repository_revision,
                receipt=receipt,
            )
        return {"results": results}
    except IncrementalIndexPreconditionError as e:
        raise HTTPException(status_code=409, detail=str(e))
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
        authoritative_branch = _authoritative_pr_branch(request)
        if not authoritative_branch:
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

        source_revision = _optional_string(request.source_revision)
        base_revision = _optional_string(request.base_revision)
        base_generation_manifest = _optional_string(
            request.base_generation_manifest_sha256
        )
        pr_generation_fingerprint = _optional_string(
            request.pr_generation_fingerprint
        )
        pr_overlay_manifest = _optional_string(
            request.pr_overlay_generation_manifest_sha256
        )
        collection_target = _optional_string(request.collection_target)
        receipt = None
        overlay_receipt = None
        if base_revision:
            receipt = require_repository_generation(
                index_manager,
                workspace=request.workspace,
                project=request.project,
                branch=authoritative_branch,
                revision=base_revision,
                generation_manifest_sha256=base_generation_manifest,
                collection_target=collection_target,
            )
        pr_results = []
        collection_name = (
            receipt["_collection_target"]
            if receipt
            else collection_target
            or index_manager._get_project_collection_name(
                request.workspace,
                request.project,
            )
        )
        preflight_branches = [authoritative_branch]
        if query_service._collection_or_alias_exists(collection_name):
            query_service._observe_branches(
                collection_name,
                preflight_branches,
            )

        # HYBRID MODE: Query PR-indexed data first if pr_number is provided
        if request.pr_number:
            if all((
                source_revision,
                base_revision,
                base_generation_manifest,
                pr_generation_fingerprint,
                pr_overlay_manifest,
            )):
                overlay_receipt = read_pr_overlay_generation(
                    index_manager.qdrant_client,
                    collection_name,
                    workspace=request.workspace,
                    project=request.project,
                    pr_number=request.pr_number,
                    branch=request.branch or authoritative_branch,
                    base_branch=authoritative_branch,
                    source_revision=source_revision,
                    base_revision=base_revision,
                    base_generation_manifest_sha256=base_generation_manifest,
                    generation_fingerprint=pr_generation_fingerprint,
                    overlay_representation_fingerprint=(
                        index_manager.pr_overlay_representation_fingerprint
                    ),
                    expected_manifest_sha256=(
                        pr_overlay_manifest
                    ),
                )
                if overlay_receipt is None:
                    raise IncrementalIndexPreconditionError(
                        "requested PR overlay generation is unavailable"
                    )
            pr_results = _query_pr_indexed_data(
                index_manager=index_manager,
                query_service=query_service,
                workspace=request.workspace,
                project=request.project,
                pr_number=request.pr_number,
                changed_files=request.changed_files,
                query_texts=request.diff_snippets or [],
                pr_title=request.pr_title,
                top_k=request.top_k or 15,
                collection_target=collection_name,
                source_revision=source_revision,
                base_revision=base_revision,
                base_generation_manifest_sha256=base_generation_manifest,
                pr_generation_fingerprint=pr_generation_fingerprint,
            )
            logger.info(f"Hybrid mode: Found {len(pr_results)} PR-specific chunks for PR #{request.pr_number}")

        # Get branch context
        context = query_service.get_context_for_pr(
            workspace=request.workspace,
            project=request.project,
            branch=authoritative_branch,
            changed_files=request.changed_files,
            diff_snippets=request.diff_snippets or [],
            pr_title=request.pr_title,
            pr_description=request.pr_description,
            top_k=request.top_k,
            enable_priority_reranking=request.enable_priority_reranking,
            min_relevance_score=request.min_relevance_score,
            # Hybrid PR retrieval is target branch + PR overlay. The source
            # branch must not enter the repository branch query a second time.
            base_branch=None if request.pr_number else request.base_branch,
            deleted_files=request.deleted_files or [],
            exclude_pr_files=(request.all_pr_changed_files or []) if request.pr_number else [],
            expected_revisions=(
                {authoritative_branch: base_revision}
                if base_revision else None
            ),
            collection_target=collection_name,
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
                metadata = branch_chunk.get("metadata")
                path = branch_chunk.get("path", "") or (
                    metadata.get("path", "")
                    if isinstance(metadata, dict)
                    else ""
                )
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

        if receipt:
            require_same_repository_generation(
                index_manager,
                workspace=request.workspace,
                project=request.project,
                branch=authoritative_branch,
                revision=base_revision,
                receipt=receipt,
            )
        if request.pr_number and overlay_receipt:
            current_overlay = read_pr_overlay_generation(
                index_manager.qdrant_client,
                collection_name,
                workspace=request.workspace,
                project=request.project,
                pr_number=request.pr_number,
                branch=request.branch or authoritative_branch,
                base_branch=authoritative_branch,
                source_revision=source_revision,
                base_revision=base_revision,
                base_generation_manifest_sha256=base_generation_manifest,
                generation_fingerprint=pr_generation_fingerprint,
                overlay_representation_fingerprint=(
                    index_manager.pr_overlay_representation_fingerprint
                ),
                expected_manifest_sha256=None,
            )
            if current_overlay != overlay_receipt:
                raise IncrementalIndexPreconditionError(
                    "PR overlay generation changed while context was retrieved"
                )

        return {"context": context}
    except IncrementalIndexPreconditionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting PR context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _query_pr_indexed_data(
    index_manager,
    query_service,
    workspace: str,
    project: str,
    pr_number: int,
    changed_files: List[str],
    query_texts: List[str],
    pr_title: Optional[str],
    top_k: int = 15,
    collection_target: Optional[str] = None,
    source_revision: Optional[str] = None,
    base_revision: Optional[str] = None,
    base_generation_manifest_sha256: Optional[str] = None,
    pr_generation_fingerprint: Optional[str] = None,
) -> List[Dict]:
    """
    Query PR-indexed chunks from the main collection.

    Filters by pr=true and pr_number to get only PR-specific data.
    When no meaningful query text exists, uses scroll() instead of
    wasting an embedding call on a fabricated query.
    """
    try:
        collection_name = (
            collection_target
            or index_manager._get_project_collection_name(workspace, project)
        )

        exact_binding = all((
            source_revision,
            base_revision,
            base_generation_manifest_sha256,
            pr_generation_fingerprint,
        ))
        if not index_manager._collection_manager.collection_exists(collection_name):
            if exact_binding:
                raise IncrementalIndexPreconditionError(
                    "revision-bound PR overlay collection is unavailable"
                )
            return []

        query_parts = []
        if pr_title:
            query_parts.append(pr_title)
        if query_texts:
            query_parts.extend(query_texts)

        pr_filter = Filter(
            must=[
                FieldCondition(key="pr", match=MatchValue(value=True)),
                FieldCondition(key="pr_number", match=MatchValue(value=pr_number)),
                *(
                    [
                        FieldCondition(key="pr_source_revision", match=MatchValue(value=source_revision)),
                        FieldCondition(key="pr_base_revision", match=MatchValue(value=base_revision)),
                        FieldCondition(key="pr_base_generation_manifest_sha256", match=MatchValue(value=base_generation_manifest_sha256)),
                        FieldCondition(key="pr_generation_fingerprint", match=MatchValue(value=pr_generation_fingerprint)),
                    ]
                    if exact_binding else []
                ),
            ],
            must_not=[FieldCondition(
                key=PR_OVERLAY_MANIFEST_PAYLOAD_KEY,
                match=MatchValue(value=True),
            )],
        )

        direct_file_results = _fetch_direct_pr_file_chunks(
            index_manager=index_manager,
            query_service=query_service,
            collection_name=collection_name,
            pr_filter=pr_filter,
            changed_files=changed_files,
        )

        if not query_parts:
            results, _ = index_manager.qdrant_client.scroll(
                collection_name=collection_name,
                scroll_filter=pr_filter,
                limit=max(top_k * 4, top_k),
                with_payload=True,
                with_vectors=False
            )
            if exact_binding:
                for point in results:
                    payload = point.payload or {}
                    if any(payload.get(key) != value for key, value in (
                        ("pr_source_revision", source_revision),
                        ("pr_base_revision", base_revision),
                        ("pr_base_generation_manifest_sha256", base_generation_manifest_sha256),
                        ("pr_generation_fingerprint", pr_generation_fingerprint),
                    )):
                        raise IncrementalIndexPreconditionError(
                            "PR point is outside the requested overlay generation"
                        )
            accepted = query_service._accept_stored_points(results)
            if not isinstance(accepted, list):
                accepted = results
            formatted = _format_pr_results(accepted[:top_k])
            return _merge_pr_results(direct_file_results, formatted)

        query_text = " ".join(query_parts)

        query_embedding = index_manager.embed_model.get_text_embedding(query_text)

        response = index_manager.qdrant_client.query_points(
            collection_name=collection_name,
            query=query_embedding,
            query_filter=pr_filter,
            limit=max(top_k * 4, top_k),
            with_payload=True,
        )
        results = query_service._accept_stored_points(
            response.points
        )[:top_k]

        formatted = _format_pr_results(results)

        return _merge_pr_results(direct_file_results, formatted)

    except Exception as e:
        if exact_binding:
            raise
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


def _fetch_direct_pr_file_chunks(
    index_manager,
    query_service,
    collection_name: str,
    pr_filter: Filter,
    changed_files: List[str],
) -> List[Dict]:
    path_candidates = _normalize_changed_file_candidates(changed_files)
    if not path_candidates:
        return []

    direct_filter = Filter(
        must=[
            *pr_filter.must,
            FieldCondition(key="path", match=MatchAny(any=path_candidates)),
        ]
    )

    result_limit = max(32, len(path_candidates) * 8)
    results, _ = index_manager.qdrant_client.scroll(
        collection_name=collection_name,
        scroll_filter=direct_filter,
        limit=result_limit * 4,
        with_payload=True,
        with_vectors=False,
    )

    accepted = query_service._accept_stored_points(results)
    formatted = _format_pr_results(
        accepted[:result_limit],
        forced_match_type="changed_file",
        forced_score=1.0,
    )
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
    index_manager, query_service = _get_singletons()
    try:
        target_branch = request.branches[0] if request.branches else None
        source_revision = _optional_string(request.source_revision)
        base_revision = _optional_string(request.base_revision)
        base_generation_manifest = _optional_string(
            request.base_generation_manifest_sha256
        )
        pr_generation_fingerprint = _optional_string(
            request.pr_generation_fingerprint
        )
        pr_overlay_manifest = _optional_string(
            request.pr_overlay_generation_manifest_sha256
        )
        collection_target = _optional_string(request.collection_target)
        receipt = None
        overlay_receipt = None
        if base_revision and target_branch:
            if len(request.branches) != 1:
                raise IncrementalIndexPreconditionError(
                    "revision-bound deterministic context requires exactly one authoritative branch"
                )
            receipt = require_repository_generation(
                index_manager,
                workspace=request.workspace,
                project=request.project,
                branch=target_branch,
                revision=base_revision,
                generation_manifest_sha256=base_generation_manifest,
                collection_target=collection_target,
            )
        exact_overlay_binding = all((
            request.pr_number,
            source_revision,
            base_revision,
            base_generation_manifest,
            pr_generation_fingerprint,
            pr_overlay_manifest,
        ))
        if exact_overlay_binding:
            overlay_receipt = read_pr_overlay_generation(
                index_manager.qdrant_client,
                receipt["_collection_target"],
                workspace=request.workspace,
                project=request.project,
                pr_number=request.pr_number,
                branch=target_branch,
                base_branch=target_branch,
                source_revision=source_revision,
                base_revision=base_revision,
                base_generation_manifest_sha256=base_generation_manifest,
                generation_fingerprint=pr_generation_fingerprint,
                overlay_representation_fingerprint=(
                    index_manager.pr_overlay_representation_fingerprint
                ),
                expected_manifest_sha256=(
                    pr_overlay_manifest
                ),
            )
            if overlay_receipt is None:
                raise IncrementalIndexPreconditionError(
                    "requested PR overlay generation is unavailable"
                )
        context = query_service.get_deterministic_context(
            workspace=request.workspace,
            project=request.project,
            branches=request.branches,
            file_paths=request.file_paths,
            limit_per_file=request.limit_per_file or 10,
            pr_number=request.pr_number,
            pr_changed_files=request.pr_changed_files,
            additional_identifiers=request.additional_identifiers,
            expected_revisions=(
                {target_branch: base_revision}
                if base_revision and target_branch else None
            ),
            pr_source_revision=source_revision,
            pr_base_revision=base_revision,
            pr_base_generation_manifest_sha256=base_generation_manifest,
            pr_generation_fingerprint=pr_generation_fingerprint,
            collection_target=(
                receipt["_collection_target"] if receipt else collection_target
            ),
        )
        if receipt:
            require_same_repository_generation(
                index_manager,
                workspace=request.workspace,
                project=request.project,
                branch=target_branch,
                revision=base_revision,
                receipt=receipt,
            )
        if overlay_receipt:
            second_overlay = read_pr_overlay_generation(
                index_manager.qdrant_client,
                receipt["_collection_target"],
                workspace=request.workspace,
                project=request.project,
                pr_number=request.pr_number,
                branch=target_branch,
                base_branch=target_branch,
                source_revision=source_revision,
                base_revision=base_revision,
                base_generation_manifest_sha256=base_generation_manifest,
                generation_fingerprint=pr_generation_fingerprint,
                overlay_representation_fingerprint=(
                    index_manager.pr_overlay_representation_fingerprint
                ),
            )
            if second_overlay != overlay_receipt:
                raise IncrementalIndexPreconditionError(
                    "PR overlay generation changed while context was retrieved"
                )
        return {"context": context}
    except IncrementalIndexPreconditionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting deterministic context: {e}")
        raise HTTPException(status_code=500, detail=str(e))
