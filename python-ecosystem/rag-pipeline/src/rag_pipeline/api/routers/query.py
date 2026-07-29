"""Query endpoints — semantic search, PR context, deterministic context."""
import logging
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException
from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

from ..models import QueryRequest, PRContextRequest, DeterministicContextRequest
from ...core.index_representation import IndexCompatibilityError
from ...core.pr_overlay_manifest import (
    PR_OVERLAY_MANIFEST_PAYLOAD_KEY,
    read_pr_overlay_generation,
)
from ...core.repository_overlay import IncrementalIndexPreconditionError
from ...core.revision_binding import (
    require_repository_generation,
    require_same_repository_generation,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["query"])


def _get_singletons():
    """Get lifecycle-managed singletons from the api module."""
    from ..api import index_manager, query_service
    return index_manager, query_service


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


def _review_generation_binding(request, authoritative_branch: str):
    """Resolve an all-or-none exact base/overlay retrieval lease."""
    values = tuple(
        value if isinstance(value, str) and value else None
        for value in (
            getattr(request, "source_revision", None),
            getattr(request, "base_revision", None),
            getattr(request, "base_generation_manifest_sha256", None),
            getattr(request, "pr_generation_fingerprint", None),
            getattr(
                request,
                "pr_overlay_generation_manifest_sha256",
                None,
            ),
        )
    )
    if not any(values):
        return None
    if not isinstance(getattr(request, "pr_number", None), int) or not all(values):
        raise IncrementalIndexPreconditionError(
            "revision-bound PR retrieval requires a PR number, source revision, "
            "base revision, base generation receipt, PR generation fingerprint, "
            "and PR overlay generation receipt"
        )
    index_manager, _ = _get_singletons()
    base_receipt = require_repository_generation(
        index_manager,
        workspace=request.workspace,
        project=request.project,
        branch=authoritative_branch,
        revision=request.base_revision,
        generation_manifest_sha256=request.base_generation_manifest_sha256,
    )
    collection_name = base_receipt["_collection_target"]
    overlay_receipt = read_pr_overlay_generation(
        index_manager.qdrant_client,
        collection_name,
        workspace=request.workspace,
        project=request.project,
        pr_number=request.pr_number,
        branch=authoritative_branch,
        base_branch=authoritative_branch,
        source_revision=request.source_revision,
        base_revision=request.base_revision,
        base_generation_manifest_sha256=(
            request.base_generation_manifest_sha256
        ),
        generation_fingerprint=request.pr_generation_fingerprint,
        overlay_representation_fingerprint=(
            index_manager.pr_overlay_representation_fingerprint
        ),
        expected_manifest_sha256=(
            request.pr_overlay_generation_manifest_sha256
        ),
    )
    if overlay_receipt is None:
        raise IncrementalIndexPreconditionError(
            "the requested PR overlay generation does not exist"
        )
    return {"base": base_receipt, "overlay": overlay_receipt}


def _require_same_review_generation(
    request,
    authoritative_branch: str,
    generation_binding,
) -> None:
    """Revalidate both generation leases after all retrieval operations."""
    index_manager, _ = _get_singletons()
    require_same_repository_generation(
        index_manager,
        workspace=request.workspace,
        project=request.project,
        branch=authoritative_branch,
        revision=request.base_revision,
        receipt=generation_binding["base"],
    )
    collection_name = generation_binding["base"]["_collection_target"]
    overlay_receipt = read_pr_overlay_generation(
        index_manager.qdrant_client,
        collection_name,
        workspace=request.workspace,
        project=request.project,
        pr_number=request.pr_number,
        branch=authoritative_branch,
        base_branch=authoritative_branch,
        source_revision=request.source_revision,
        base_revision=request.base_revision,
        base_generation_manifest_sha256=(
            request.base_generation_manifest_sha256
        ),
        generation_fingerprint=request.pr_generation_fingerprint,
        overlay_representation_fingerprint=(
            index_manager.pr_overlay_representation_fingerprint
        ),
        expected_manifest_sha256=(
            request.pr_overlay_generation_manifest_sha256
        ),
    )
    if overlay_receipt != generation_binding["overlay"]:
        raise IncrementalIndexPreconditionError(
            "PR overlay generation changed while context was retrieved"
        )


def _query_generation_binding(request: QueryRequest):
    values = tuple(
        value if isinstance(value, str) and value else None
        for value in (
            getattr(request, "repository_revision", None),
            getattr(
                request,
                "repository_generation_manifest_sha256",
                None,
            ),
        )
    )
    if not any(values):
        return None
    if not all(values):
        raise IncrementalIndexPreconditionError(
            "revision-bound search requires both repository revision and "
            "generation receipt"
        )
    index_manager, _ = _get_singletons()
    return require_repository_generation(
        index_manager,
        workspace=request.workspace,
        project=request.project,
        branch=request.branch,
        revision=request.repository_revision,
        generation_manifest_sha256=(
            request.repository_generation_manifest_sha256
        ),
    )


@router.post("/query/search")
def semantic_search(request: QueryRequest):
    """Perform semantic search."""
    index_manager, query_service = _get_singletons()
    try:
        generation_receipt = _query_generation_binding(request)
        expected_revision = (
            request.repository_revision
            if generation_receipt is not None
            else None
        )
        results = query_service.semantic_search(
            query=request.query,
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            top_k=request.top_k,
            filter_language=request.filter_language,
            expected_revision=expected_revision,
            collection_target=(
                generation_receipt["_collection_target"]
                if generation_receipt is not None
                else None
            ),
        )
        if generation_receipt is not None:
            require_same_repository_generation(
                index_manager,
                workspace=request.workspace,
                project=request.project,
                branch=request.branch,
                revision=request.repository_revision,
                receipt=generation_receipt,
            )
        return {"results": results}
    except (IndexCompatibilityError, IncrementalIndexPreconditionError) as e:
        logger.warning("Rejected search against incompatible index: %s", e)
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

        pr_results = []
        collection_name = index_manager._get_project_collection_name(
            request.workspace,
            request.project,
        )
        preflight_branches = [authoritative_branch]
        if query_service._collection_or_alias_exists(collection_name):
            query_service._require_compatible_branches(
                collection_name,
                preflight_branches,
            )
        generation_receipt = _review_generation_binding(
            request,
            authoritative_branch,
        )
        source_revision = (
            request.source_revision if generation_receipt is not None else None
        )
        base_revision = (
            request.base_revision if generation_receipt is not None else None
        )
        base_generation_manifest_sha256 = (
            request.base_generation_manifest_sha256
            if generation_receipt is not None
            else None
        )
        pr_generation_fingerprint = (
            request.pr_generation_fingerprint
            if generation_receipt is not None
            else None
        )
        collection_target = (
            generation_receipt["base"]["_collection_target"]
            if generation_receipt is not None
            else None
        )

        # HYBRID MODE: Query PR-indexed data first if pr_number is provided
        if request.pr_number:
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
                source_revision=source_revision,
                base_revision=base_revision,
                base_generation_manifest_sha256=(
                    base_generation_manifest_sha256
                ),
                pr_generation_fingerprint=pr_generation_fingerprint,
                collection_target=collection_target,
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
                if generation_receipt is not None
                else None
            ),
            collection_target=collection_target,
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
            "pr_number": request.pr_number,
            "base_revision": base_revision,
            "base_generation_manifest_sha256": (
                base_generation_manifest_sha256
            ),
            "pr_generation_fingerprint": pr_generation_fingerprint,
            "pr_overlay_generation_manifest_sha256": (
                request.pr_overlay_generation_manifest_sha256
                if generation_receipt is not None
                else None
            ),
        }

        if generation_receipt is not None:
            _require_same_review_generation(
                request,
                authoritative_branch,
                generation_receipt,
            )
        return {"context": context}
    except (IndexCompatibilityError, IncrementalIndexPreconditionError) as e:
        logger.warning("Rejected PR context against incompatible index: %s", e)
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
    source_revision: Optional[str] = None,
    base_revision: Optional[str] = None,
    base_generation_manifest_sha256: Optional[str] = None,
    pr_generation_fingerprint: Optional[str] = None,
    collection_target: Optional[str] = None,
) -> List[Dict]:
    """
    Query PR-indexed chunks from the main collection.

    Filters by pr=true and pr_number to get only PR-specific data.
    When no meaningful query text exists, uses scroll() instead of
    wasting an embedding call on a fabricated query.
    """
    revision_bound = any(
        value is not None
        for value in (
            source_revision,
            base_revision,
            base_generation_manifest_sha256,
            pr_generation_fingerprint,
        )
    )
    try:
        collection_name = (
            collection_target
            or index_manager._get_project_collection_name(workspace, project)
        )

        if not index_manager._collection_manager.collection_exists(collection_name):
            if revision_bound:
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
                        FieldCondition(
                            key="pr_source_revision",
                            match=MatchValue(value=source_revision),
                        ),
                        FieldCondition(
                            key="pr_base_revision",
                            match=MatchValue(value=base_revision),
                        ),
                        FieldCondition(
                            key="pr_base_generation_manifest_sha256",
                            match=MatchValue(
                                value=base_generation_manifest_sha256
                            ),
                        ),
                        FieldCondition(
                            key="pr_generation_fingerprint",
                            match=MatchValue(
                                value=pr_generation_fingerprint
                            ),
                        ),
                    ]
                    if pr_generation_fingerprint
                    else []
                ),
            ],
            must_not=[
                FieldCondition(
                    key=PR_OVERLAY_MANIFEST_PAYLOAD_KEY,
                    match=MatchValue(value=True),
                ),
            ],
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
            compatible = query_service._filter_plugin_compatible_points(results)
            formatted = _format_pr_results(compatible[:top_k])
            merged = _merge_pr_results(direct_file_results, formatted)
            _require_pr_results_bound(
                merged,
                source_revision=source_revision,
                base_revision=base_revision,
                base_generation_manifest_sha256=(
                    base_generation_manifest_sha256
                ),
                pr_generation_fingerprint=pr_generation_fingerprint,
            )
            return merged

        query_text = " ".join(query_parts)

        query_embedding = index_manager.embed_model.get_text_embedding(query_text)

        response = index_manager.qdrant_client.query_points(
            collection_name=collection_name,
            query=query_embedding,
            query_filter=pr_filter,
            limit=max(top_k * 4, top_k),
            with_payload=True,
        )
        results = query_service._filter_plugin_compatible_points(
            response.points
        )[:top_k]

        formatted = _format_pr_results(results)

        merged = _merge_pr_results(direct_file_results, formatted)
        _require_pr_results_bound(
            merged,
            source_revision=source_revision,
            base_revision=base_revision,
            base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            pr_generation_fingerprint=pr_generation_fingerprint,
        )
        return merged

    except (IndexCompatibilityError, IncrementalIndexPreconditionError):
        raise
    except Exception as e:
        logger.warning(f"Error querying PR-indexed data: {e}")
        if revision_bound:
            raise
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
        ],
        must_not=list(pr_filter.must_not or ()),
    )

    result_limit = max(32, len(path_candidates) * 8)
    results, _ = index_manager.qdrant_client.scroll(
        collection_name=collection_name,
        scroll_filter=direct_filter,
        limit=result_limit * 4,
        with_payload=True,
        with_vectors=False,
    )

    compatible = query_service._filter_plugin_compatible_points(results)
    formatted = _format_pr_results(
        compatible[:result_limit],
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
            "metadata": payload,
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


def _require_pr_results_bound(
    results: List[Dict],
    *,
    source_revision: Optional[str],
    base_revision: Optional[str],
    base_generation_manifest_sha256: Optional[str],
    pr_generation_fingerprint: Optional[str],
) -> None:
    if not pr_generation_fingerprint:
        return
    for result in results:
        payload = result.get("metadata") or {}
        if (
            payload.get("pr_source_revision") != source_revision
            or payload.get("pr_base_revision") != base_revision
            or payload.get("pr_base_generation_manifest_sha256")
            != base_generation_manifest_sha256
            or payload.get("pr_generation_fingerprint")
            != pr_generation_fingerprint
        ):
            raise IncrementalIndexPreconditionError(
                "PR retrieval returned a point outside the requested overlay "
                "generation"
            )


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
        authoritative_branch = request.branches[0] if request.branches else ""
        generation_receipt = (
            _review_generation_binding(request, authoritative_branch)
            if authoritative_branch
            else None
        )
        requested_branches = list(dict.fromkeys(
            branch for branch in request.branches if branch
        ))
        if (
            generation_receipt is not None
            and requested_branches != [authoritative_branch]
        ):
            raise IncrementalIndexPreconditionError(
                "revision-bound deterministic retrieval requires exactly one "
                "authoritative repository branch"
            )
        retrieval_branches = (
            [authoritative_branch]
            if generation_receipt is not None
            else request.branches
        )
        source_revision = (
            request.source_revision if generation_receipt is not None else None
        )
        base_revision = (
            request.base_revision if generation_receipt is not None else None
        )
        base_generation_manifest_sha256 = (
            request.base_generation_manifest_sha256
            if generation_receipt is not None
            else None
        )
        pr_generation_fingerprint = (
            request.pr_generation_fingerprint
            if generation_receipt is not None
            else None
        )
        context = query_service.get_deterministic_context(
            workspace=request.workspace,
            project=request.project,
            branches=retrieval_branches,
            file_paths=request.file_paths,
            limit_per_file=request.limit_per_file or 10,
            pr_number=request.pr_number,
            pr_changed_files=request.pr_changed_files,
            additional_identifiers=request.additional_identifiers,
            expected_revisions=(
                {authoritative_branch: base_revision}
                if generation_receipt is not None
                else None
            ),
            pr_source_revision=source_revision,
            pr_base_revision=base_revision,
            pr_base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            pr_generation_fingerprint=pr_generation_fingerprint,
            collection_target=(
                generation_receipt["base"]["_collection_target"]
                if generation_receipt is not None
                else None
            ),
        )
        if generation_receipt is not None:
            _require_same_review_generation(
                request,
                authoritative_branch,
                generation_receipt,
            )
        context.setdefault("_metadata", {}).update({
            "base_revision": base_revision,
            "base_generation_manifest_sha256": (
                base_generation_manifest_sha256
            ),
            "pr_generation_fingerprint": pr_generation_fingerprint,
            "pr_overlay_generation_manifest_sha256": (
                request.pr_overlay_generation_manifest_sha256
                if generation_receipt is not None
                else None
            ),
        })
        return {"context": context}
    except (IndexCompatibilityError, IncrementalIndexPreconditionError) as e:
        logger.warning(
            "Rejected deterministic context against incompatible index: %s",
            e,
        )
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting deterministic context: {e}")
        raise HTTPException(status_code=500, detail=str(e))
