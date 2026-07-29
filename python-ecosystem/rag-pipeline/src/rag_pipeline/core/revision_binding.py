"""Fail-closed repository-generation leases for review retrieval."""

from __future__ import annotations

from .repository_overlay import IncrementalIndexPreconditionError


def require_repository_generation(
    index_manager,
    *,
    workspace: str,
    project: str,
    branch: str,
    revision: str,
    generation_manifest_sha256: str | None = None,
    collection_target: str | None = None,
):
    """Load one exact sealed generation and optionally match its receipt."""
    logical_collection = index_manager._get_project_collection_name(
        workspace,
        project,
    )
    active_target = (
        index_manager._collection_manager.resolve_collection_target(
            logical_collection
        )
    )
    if active_target is None:
        raise IncrementalIndexPreconditionError(
            "requested repository collection is unavailable"
        )
    if (
        collection_target is not None
        and active_target != collection_target
    ):
        raise IncrementalIndexPreconditionError(
            "requested repository generation changed while context was retrieved"
        )
    bound_target = collection_target or active_target
    result = index_manager.get_revision_preflight(
        workspace,
        project,
        branch,
        revision,
        collection_target=bound_target,
    )
    if result is None:
        raise IncrementalIndexPreconditionError(
            "requested repository revision is not available as one complete "
            f"sealed generation: {branch}@{revision}"
        )
    if (
        generation_manifest_sha256 is not None
        and result["generation_manifest_sha256"]
        != generation_manifest_sha256
    ):
        raise IncrementalIndexPreconditionError(
            "requested repository generation changed or does not match its "
            f"receipt: {branch}@{revision}"
        )
    return {
        **result,
        "_collection_target": bound_target,
    }


def require_same_repository_generation(
    index_manager,
    *,
    workspace: str,
    project: str,
    branch: str,
    revision: str,
    receipt,
):
    """Recheck a generation lease after a non-transactional retrieval/build."""
    return require_repository_generation(
        index_manager,
        workspace=workspace,
        project=project,
        branch=branch,
        revision=revision,
        generation_manifest_sha256=receipt["generation_manifest_sha256"],
        collection_target=receipt["_collection_target"],
    )
