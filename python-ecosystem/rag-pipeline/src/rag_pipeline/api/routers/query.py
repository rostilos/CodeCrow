"""Revision-bound structural graph endpoints."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException

from ...core.coordination import MutationLeaseUnavailable
from ...core.exact_index import ExactIndexPreconditionError
from ...core.review_context import (
    ProposedTreeReviewContextService,
    ProposedTreeUnavailableError,
)
from ...core.source_tree import RepositorySourceTreeError
from ..models import (
    CodeSearchRequest,
    ReviewContextRequest,
    ReviewContextResponse,
    ReviewGraphQueryRequest,
    ReviewFileRequest,
    ReviewSearchRequest,
    ReviewImpactRadiusRequest,
    ReviewMinimalContextRequest,
    ProposedTreePrepareRequest,
    ReviewTraverseRequest,
    ReviewUnitRequest,
    StructuralGraphQueryRequest,
    StructuralRelationsRequest,
    StructuralUnitRequest,
)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["query"])
_SEARCH_TERM = re.compile(r"[A-Za-z_][A-Za-z0-9_:$\\.\-/]*")
_PROPOSED_TREE_BINDING_FIELDS = (
    "target_repo_path",
    "review_overlay_path",
    "workspace",
    "project",
    "target_branch",
    "base_revision",
    "source_revision",
    "focus_paths",
    "base_collection_target",
    "base_generation_manifest_sha256",
    "review_collection_target",
    "review_generation_manifest_sha256",
    "include_patterns",
    "exclude_patterns",
    "project_type",
    "source_root",
)

_PROPOSED_TREE_PREPARATION_FIELDS = tuple(
    field
    for field in _PROPOSED_TREE_BINDING_FIELDS
    if field not in {
        "focus_paths",
        "review_collection_target",
        "review_generation_manifest_sha256",
    }
)


def _normalize_search_terms(query: str) -> list[str]:
    return list(dict.fromkeys(
        match.group(0).casefold()
        for match in _SEARCH_TERM.finditer(str(query or ""))
        if match.group(0).strip()
    ))


def _manager():
    from ..api import index_manager

    return index_manager


def _open_reader(manager, request):
    return manager.open_reader(
        workspace=request.workspace,
        project=request.project,
        branch=request.branch,
        revision=request.repository_revision,
        generation_manifest_sha256=(
            request.repository_generation_manifest_sha256
        ),
        collection_target=request.collection_target,
    )


def _proposed_tree_operation(request, method: str, **operation_arguments):
    service = ProposedTreeReviewContextService(_manager())
    arguments = {
        field: getattr(request, field)
        for field in _PROPOSED_TREE_BINDING_FIELDS
    }
    arguments.update(operation_arguments)
    try:
        return getattr(service, method)(**arguments)
    except MutationLeaseUnavailable as exception:
        logger.warning(
            "Proposed-tree graph operation mutation conflict: "
            "operation=%s workspace=%s project=%s detail=%s",
            method,
            request.workspace,
            request.project,
            exception,
        )
        raise HTTPException(status_code=409, detail=str(exception))
    except (
        ExactIndexPreconditionError,
        ProposedTreeUnavailableError,
        RepositorySourceTreeError,
    ) as exception:
        raise HTTPException(status_code=409, detail=str(exception))
    except ValueError as exception:
        raise HTTPException(status_code=400, detail=str(exception))
    except Exception as exception:
        logger.error(
            "Proposed-tree graph operation failed: operation=%s detail=%s",
            method,
            exception,
        )
        raise HTTPException(status_code=500, detail=str(exception))


@router.post("/query/relations")
def structural_relations(request: StructuralRelationsRequest):
    """Return compact one-hop AST and plugin relation metadata."""
    manager = _manager()
    try:
        with _open_reader(manager, request) as reader:
            return reader.relations_for_paths(
                request.paths,
                max_relations=request.max_relations,
            )
    except (ExactIndexPreconditionError, ValueError) as exception:
        raise HTTPException(status_code=409, detail=str(exception))
    except Exception as exception:
        logger.error("Structural relation query failed: %s", exception)
        raise HTTPException(status_code=500, detail=str(exception))


@router.post("/query/graph")
def structural_graph_query(request: StructuralGraphQueryRequest):
    """Run one exact, directional graph query over a sealed generation."""
    manager = _manager()
    try:
        with _open_reader(manager, request) as reader:
            return reader.query_graph(
                request.pattern,
                request.target,
                max_results=request.max_results,
            )
    except (ExactIndexPreconditionError, ValueError) as exception:
        raise HTTPException(status_code=409, detail=str(exception))
    except Exception as exception:
        logger.error("Structural graph query failed: %s", exception)
        raise HTTPException(status_code=500, detail=str(exception))


@router.post("/query/unit")
def structural_unit(request: StructuralUnitRequest):
    """Return one exact AST/plugin source unit selected by its opaque ID."""
    manager = _manager()
    try:
        with _open_reader(manager, request) as reader:
            result = reader.get_unit(request.unit_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Structural unit was not found")
        return result
    except HTTPException:
        raise
    except ExactIndexPreconditionError as exception:
        raise HTTPException(status_code=409, detail=str(exception))
    except Exception as exception:
        logger.error("Structural unit query failed: %s", exception)
        raise HTTPException(status_code=500, detail=str(exception))


@router.post("/query/review-context", response_model=ReviewContextResponse)
def review_context(request: ReviewContextRequest):
    """Return bounded evidence from an exact request-bound proposed tree."""
    manager = _manager()
    try:
        return ProposedTreeReviewContextService(manager).review_context(
            target_repo_path=request.target_repo_path,
            review_overlay_path=request.review_overlay_path,
            workspace=request.workspace,
            project=request.project,
            target_branch=request.target_branch,
            base_revision=request.base_revision,
            source_revision=request.source_revision,
            base_collection_target=request.base_collection_target,
            base_generation_manifest_sha256=(
                request.base_generation_manifest_sha256
            ),
            review_collection_target=request.review_collection_target,
            review_generation_manifest_sha256=(
                request.review_generation_manifest_sha256
            ),
            focus_paths=request.focus_paths,
            question=request.question,
            focus_symbols=request.focus_symbols,
            include_patterns=request.include_patterns,
            exclude_patterns=request.exclude_patterns,
            project_type=request.project_type,
            source_root=request.source_root,
            max_relations=request.max_relations,
            max_source_windows=request.max_source_windows,
            max_source_characters=request.max_source_characters,
        )
    except MutationLeaseUnavailable as exception:
        logger.warning(
            "Proposed-tree review context mutation conflict: "
            "workspace=%s project=%s detail=%s",
            request.workspace,
            request.project,
            exception,
        )
        raise HTTPException(status_code=409, detail=str(exception))
    except (
        ExactIndexPreconditionError,
        ProposedTreeUnavailableError,
        RepositorySourceTreeError,
    ) as exception:
        # Review enrichment is optional to its caller. A conflict response makes
        # exact unavailability observable without presenting target-head data as
        # proposed or failing the core review path.
        raise HTTPException(status_code=409, detail=str(exception))
    except ValueError as exception:
        raise HTTPException(status_code=400, detail=str(exception))
    except Exception as exception:
        logger.error("Proposed-tree review context failed: %s", exception)
        raise HTTPException(status_code=500, detail=str(exception))


@router.post("/query/review-generation")
def prepare_review_generation(request: ProposedTreePrepareRequest):
    """Prepare one sealed proposed-tree generation before Stage 1 fan-out."""

    service = ProposedTreeReviewContextService(_manager())
    arguments = {
        field: getattr(request, field)
        for field in _PROPOSED_TREE_PREPARATION_FIELDS
    }
    try:
        generation = service.prepare_generation_singleflight(**arguments)
        return {
            "status": "ready",
            "collection_target": generation.collection_target,
            "generation_manifest_sha256": generation.receipt[
                "generation_manifest_sha256"
            ],
            "source_revision": request.source_revision,
            "source_tree_sha256": generation.proposed_source_tree_sha256,
            "overlay_sha256": generation.overlay_sha256,
            "representation_identity": generation.representation_identity,
            "changed_paths": list(generation.changed_paths),
            "deleted_paths": list(generation.deleted_paths),
            "cache_hit": generation.cache_hit,
        }
    except MutationLeaseUnavailable as exception:
        logger.warning(
            "Proposed-tree generation mutation conflict: workspace=%s "
            "project=%s detail=%s",
            request.workspace,
            request.project,
            exception,
        )
        raise HTTPException(status_code=409, detail=str(exception))
    except (
        ExactIndexPreconditionError,
        ProposedTreeUnavailableError,
        RepositorySourceTreeError,
    ) as exception:
        raise HTTPException(status_code=409, detail=str(exception))
    except ValueError as exception:
        raise HTTPException(status_code=400, detail=str(exception))
    except Exception as exception:
        logger.error("Proposed-tree generation preparation failed: %s", exception)
        raise HTTPException(status_code=500, detail=str(exception))


@router.post("/query/review-minimal-context")
def review_minimal_context(request: ReviewMinimalContextRequest):
    """Return a compact starting map from the exact proposed-tree graph."""

    return _proposed_tree_operation(
        request,
        "minimal_review_context",
        question=request.question,
        focus_symbols=request.focus_symbols,
        max_relations=request.max_relations,
        detail_level=request.detail_level,
        include_source=request.include_source,
        max_source_windows=request.max_source_windows,
        max_source_characters=request.max_source_characters,
    )


@router.post("/query/review-impact-radius")
def review_impact_radius(request: ReviewImpactRadiusRequest):
    """Return weighted best-score impact traversal over the proposed tree."""

    return _proposed_tree_operation(
        request,
        "review_impact_radius",
        targets=request.targets,
        max_depth=request.max_depth,
        max_results=request.max_results,
        detail_level=request.detail_level,
        include_source=request.include_source,
        max_source_windows=request.max_source_windows,
        max_source_characters=request.max_source_characters,
    )


@router.post("/query/review-traverse")
def review_traverse(request: ReviewTraverseRequest):
    """Traverse proposed-tree facts with deterministic BFS or DFS."""

    return _proposed_tree_operation(
        request,
        "traverse_review_graph",
        start=request.start,
        strategy=request.strategy,
        direction=request.direction,
        relation_kinds=request.relation_kinds,
        max_depth=request.max_depth,
        max_results=request.max_results,
        token_budget=request.token_budget,
        detail_level=request.detail_level,
        include_source=request.include_source,
        max_source_windows=request.max_source_windows,
        max_source_characters=request.max_source_characters,
    )


@router.post("/query/review-graph")
def review_graph(request: ReviewGraphQueryRequest):
    """Run one exact directional query over the proposed-tree graph."""

    return _proposed_tree_operation(
        request,
        "query_review_graph",
        pattern=request.pattern,
        target=request.target,
        max_results=request.max_results,
        cursor=request.cursor,
        detail_level=request.detail_level,
        include_source=request.include_source,
        max_source_windows=request.max_source_windows,
        max_source_characters=request.max_source_characters,
    )


@router.post("/query/review-unit")
def review_unit(request: ReviewUnitRequest):
    """Return one exact AST/plugin unit from the proposed-tree graph."""

    result = _proposed_tree_operation(
        request,
        "get_review_structural_unit",
        unit_id=request.unit_id,
        offset=request.offset,
        max_characters=request.max_characters,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Review structural unit was not found",
        )
    return result


@router.post("/query/review-file")
def review_file(request: ReviewFileRequest):
    """Read exact source when graph units do not cover the needed text."""

    return _proposed_tree_operation(
        request,
        "get_review_file_content",
        path=request.path,
        side=request.side,
        start_line=request.start_line,
        end_line=request.end_line,
    )


@router.post("/query/review-search")
def review_search(request: ReviewSearchRequest):
    """Search exact proposed-tree paths and source outside indexed symbols."""

    return _proposed_tree_operation(
        request,
        "search_review_code",
        query=request.query,
        cursor=request.cursor,
        max_results=request.max_results,
    )


@router.post("/query/code-search")
def code_search(request: CodeSearchRequest):
    """Compatibility endpoint for exact lexical path/symbol navigation."""
    manager = _manager()
    try:
        result_limit = request.limit or 25
        with _open_reader(manager, request) as reader:
            units = reader.search_units(
                request.query,
                max_results=result_limit + 1,
            )
            snapshot = reader.snapshot()
            results = []
            terms = _normalize_search_terms(request.query)
            for unit in units[:result_limit]:
                detail = reader.get_unit(unit["unitId"]) or {}
                source = detail.get("unit") or {}
                text = str(source.get("content") or "")
                path_text = str(unit.get("path") or "").casefold()
                symbol_text = " ".join((
                    str(unit.get("name") or ""),
                    str(unit.get("qualifiedName") or ""),
                )).casefold()
                source_text = text.casefold()
                match_reasons = []
                if any(term in path_text for term in terms):
                    match_reasons.append("path")
                if any(term in symbol_text for term in terms):
                    match_reasons.append("symbol")
                if any(term in source_text for term in terms):
                    match_reasons.append("source")
                results.append({
                    "id": unit["unitId"],
                    "path": unit.get("path"),
                    "text": text,
                    "record_type": unit.get("recordType"),
                    "metadata": {
                        "start_line": unit.get("startLine"),
                        "end_line": unit.get("endLine"),
                        "language": unit.get("language"),
                        "kind": unit.get("kind"),
                        "name": unit.get("name"),
                        "qualified_name": unit.get("qualifiedName"),
                        "snapshot": snapshot,
                        "source_evidence": detail.get("sourceEvidence", False),
                    },
                    "matched_terms": [
                        term
                        for term in terms
                        if term in " ".join((
                            path_text,
                            symbol_text,
                            source_text,
                        ))
                    ],
                    "match_reasons": match_reasons,
                })
        truncated = len(units) > result_limit
        return {
            "snapshot": snapshot,
            "results": results,
            "coverage": {
                "complete": not truncated,
                "partial_reasons": [] if not truncated else ["result_limit"],
                "matching_results": len(results),
                "returned_results": len(results),
            },
        }
    except ExactIndexPreconditionError as exception:
        raise HTTPException(status_code=409, detail=str(exception))
    except Exception as exception:
        logger.error("Structural code search failed: %s", exception)
        raise HTTPException(status_code=500, detail=str(exception))
