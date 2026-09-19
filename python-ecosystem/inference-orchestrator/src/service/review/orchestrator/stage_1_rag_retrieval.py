"""Stage 1 structural-generation binding and relation briefing helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from model.dtos import ReviewRequestDto
from service.review.snapshot_identity import (
    resolve_exact_structural_base_revision,
)


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


STAGE1_RELATION_BRIEFING_MAX_RELATIONS = max(
    4,
    min(
        64,
        _env_int("REVIEW_STAGE1_RELATION_BRIEFING_MAX_RELATIONS", 24),
    ),
)
STAGE1_RELATION_BRIEFING_MAX_CHARS = max(
    2_000,
    min(
        32_000,
        _env_int("REVIEW_STAGE1_RELATION_BRIEFING_MAX_CHARS", 12_000),
    ),
)


@dataclass
class Stage1RagState:
    """Review-scoped structural evidence state shared with later stages."""

    exact_evidence_by_id: Dict[str, tuple[Dict[str, Any], ...]] = field(
        default_factory=dict
    )
    deterministic_retrieval_states: List[str] = field(default_factory=list)
    relation_briefings_by_paths: Dict[
        tuple[str, ...], Dict[str, Any]
    ] = field(default_factory=dict, repr=False)


async def fetch_stage1_relation_briefing(
    rag_client: Any,
    request: ReviewRequestDto,
    focus_paths: Sequence[str],
    *,
    max_relations: int = STAGE1_RELATION_BRIEFING_MAX_RELATIONS,
) -> Optional[Dict[str, Any]]:
    """Fetch a bounded proposed-tree relation orientation for prompt packing.

    This host call makes the first useful graph facts deterministic: the model
    does not have to spend an agent turn deciding whether to ask for them.  It
    may return a very small number of exact related-source windows;
    Stage 1 admits them only when they fit the relation capsule. Exact source is
    already present for changed files and remains available through the
    request-bound file/unit tools when a relation identifies another gap.

    Structural enrichment is optional. Missing clients, incomplete bindings,
    and transport failures return no/structured degraded context and never
    reject the core source review.
    """

    # The composite backend selector already reserves its bounded relation
    # budget across anchor, direct, and second-hop facts. It is called by the
    # host—not exposed as an obligatory model turn—and its response is reduced
    # to a much smaller prompt capsule by Stage 1.
    query = getattr(rag_client, "explore_review_context", None)
    if not callable(query):
        return None
    if not has_exact_proposed_tree_binding(request):
        logger.info(
            "Optional Stage 1 relation briefing skipped because the request "
            "has no exact tenant, repository, target snapshot, and sealed "
            "base-generation binding"
        )
        return None

    structural_repo_path = getattr(request, "localRagRepoPath", None)
    if not isinstance(structural_repo_path, str) or not structural_repo_path.strip():
        structural_repo_path = request.localRepoPath
    source_revision = request.currentCommitHash or request.commitHash
    base_revision = resolve_exact_structural_base_revision(request)
    normalized_paths = list(dict.fromkeys(
        str(path).strip().replace("\\", "/").lstrip("/")
        for path in focus_paths
        if isinstance(path, str) and path.strip()
    ))
    if not normalized_paths:
        return None

    try:
        return await query(
            workspace=request.projectWorkspace,
            project=request.projectNamespace,
            target_branch=request.targetBranchName,
            base_revision=base_revision,
            source_revision=source_revision,
            target_repo_path=structural_repo_path,
            review_overlay_path=request.localReviewOverlayPath,
            focus_paths=normalized_paths,
            # Both words are selector stop-terms. Path anchors, rather than a
            # generic prose query, determine this deterministic orientation.
            question="Review changed",
            base_collection_target=getattr(
                request,
                "ragCollectionTarget",
                None,
            ),
            base_generation_manifest_sha256=getattr(
                request,
                "ragBaseGenerationManifestSha256",
                None,
            ),
            review_collection_target=getattr(
                request,
                "ragReviewCollectionTarget",
                None,
            ),
            review_generation_manifest_sha256=getattr(
                request,
                "ragReviewGenerationManifestSha256",
                None,
            ),
            focus_symbols=[],
            max_relations=max_relations,
            max_source_windows=2,
            max_source_characters=4_000,
        )
    except Exception as error:
        logger.warning(
            "Optional Stage 1 relation briefing failed for paths=%s: %s",
            normalized_paths,
            error,
        )
        return {
            "status": "error",
            "error": str(error),
            "nodes": [],
            "edges": [],
            "sourceWindows": [],
            "coverage": {
                "state": "unavailable",
                "truncated": True,
                "partialReasons": ["relation_briefing_failed"],
                "returnedNodes": 0,
                "returnedRelations": 0,
                "sourceIncluded": False,
            },
        }


def has_exact_proposed_tree_binding(request: ReviewRequestDto) -> bool:
    """Whether the host supplied every input for an exact proposed tree."""

    source_revision = request.currentCommitHash or request.commitHash
    base_revision = resolve_exact_structural_base_revision(request)
    structural_repo_path = getattr(request, "localRagRepoPath", None)
    if not isinstance(structural_repo_path, str) or not structural_repo_path.strip():
        structural_repo_path = request.localRepoPath
    return all(
        isinstance(value, str) and bool(value.strip())
        for value in (
            request.projectWorkspace,
            request.projectNamespace,
            request.targetBranchName,
            base_revision,
            source_revision,
            structural_repo_path,
            request.localReviewOverlayPath,
            getattr(request, "ragCollectionTarget", None),
            getattr(request, "ragBaseGenerationManifestSha256", None),
            getattr(request, "ragReviewCollectionTarget", None),
            getattr(request, "ragReviewGenerationManifestSha256", None),
        )
    )


def rag_response_error(
    response: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Return the sanitized failure carried by a structural client response."""

    if not isinstance(response, dict):
        return None
    if str(response.get("status", "")).strip().casefold() != "error":
        return None
    detail = str(response.get("error") or "structural request failed").strip()
    return detail or "structural request failed"


def has_exact_base_binding(request: ReviewRequestDto) -> bool:
    """Whether the request names one immutable target-head graph generation."""

    return all(
        isinstance(value, str) and bool(value.strip())
        for value in (
            resolve_exact_structural_base_revision(request),
            getattr(request, "ragCollectionTarget", None),
            getattr(request, "ragBaseGenerationManifestSha256", None),
        )
    )
