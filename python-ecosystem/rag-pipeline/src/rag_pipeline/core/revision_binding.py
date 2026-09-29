"""Exact repository-generation leases for structural retrieval."""

from __future__ import annotations

from .exact_index import ExactIndexPreconditionError


def require_repository_generation(
    index_manager,
    *,
    workspace: str,
    project: str,
    branch: str,
    revision: str,
    generation_manifest_sha256: str,
    collection_target: str,
):
    """Load one exact sealed generation and match its registry receipt."""
    requested_target = collection_target
    result = index_manager.get_revision_preflight(
        workspace,
        project,
        branch,
        revision,
        collection_target=requested_target,
    )
    if result is None:
        raise ExactIndexPreconditionError(
            "requested repository revision is not available as one complete "
            f"sealed generation: {branch}@{revision}"
        )
    if result["generation_manifest_sha256"] != generation_manifest_sha256:
        raise ExactIndexPreconditionError(
            "requested repository generation changed or does not match its "
            f"receipt: {branch}@{revision}"
        )
    return {
        **result,
        "_collection_target": requested_target,
        "_lease_target": requested_target,
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
        collection_target=receipt["_lease_target"],
    )
