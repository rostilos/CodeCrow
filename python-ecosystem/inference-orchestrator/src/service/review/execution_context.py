"""Bind one immutable execution context at every review ingress.

The v1 manifest is the sole authority for execution and revision aliases.  A
legacy request remains available only through an explicit, unexpired adapter;
that adapter never manufactures a v1 manifest.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

from model.dtos import ExecutionManifestV1, ReviewRequestDto


_SHA_256 = re.compile(r"[0-9a-f]{64}")
_LEGACY_COMPATIBILITY_SUNSET = datetime(
    2026, 9, 30, tzinfo=timezone.utc
)


class ExecutionContextBindingError(ValueError):
    """A parsed review request cannot be bound to an allowed execution."""


class ExecutionEventBindingError(ExecutionContextBindingError):
    """A candidate event is missing or conflicts with its execution manifest."""


def bind_owned_execution_event(
    event: Mapping[str, Any],
    manifest: ExecutionManifestV1 | None,
) -> dict[str, Any]:
    """Construct one trusted local event with immutable execution identity.

    This helper is only for events owned by this process.  Conflicting producer
    values are rejected; absent identity is added from the already validated
    manifest.  Untrusted callbacks must use ``require_execution_event_binding``
    instead so missing identity can never be laundered.
    """

    if not isinstance(event, Mapping):
        raise ExecutionEventBindingError("candidate event must be an object")
    owned_event = dict(event)
    if manifest is None:
        return owned_event
    _require_compatible_event_field(
        owned_event.get("executionId"), manifest.executionId, "executionId"
    )
    _require_compatible_event_field(
        owned_event.get("artifactManifestDigest"),
        manifest.artifactManifestDigest,
        "artifactManifestDigest",
    )
    owned_event["executionId"] = manifest.executionId
    owned_event["artifactManifestDigest"] = manifest.artifactManifestDigest
    return owned_event


def require_execution_event_binding(
    event: Mapping[str, Any],
    manifest: ExecutionManifestV1 | None,
) -> dict[str, Any]:
    """Validate an untrusted producer event without filling either identity."""

    if not isinstance(event, Mapping):
        raise ExecutionEventBindingError("candidate event must be an object")
    forwarded_event = dict(event)
    if manifest is None:
        return forwarded_event
    _require_exact_event_field(
        forwarded_event.get("executionId"), manifest.executionId, "executionId"
    )
    _require_exact_event_field(
        forwarded_event.get("artifactManifestDigest"),
        manifest.artifactManifestDigest,
        "artifactManifestDigest",
    )
    return forwarded_event


def _require_compatible_event_field(
    observed: Any,
    expected: str,
    field: str,
) -> None:
    if observed is not None and (
        not isinstance(observed, str) or observed != expected
    ):
        raise ExecutionEventBindingError(
            f"candidate event {field} conflicts with executionManifest"
        )


def _require_exact_event_field(
    observed: Any,
    expected: str,
    field: str,
) -> None:
    if not isinstance(observed, str):
        raise ExecutionEventBindingError(
            f"candidate event {field} is missing or malformed"
        )
    if field == "artifactManifestDigest" and _SHA_256.fullmatch(observed) is None:
        raise ExecutionEventBindingError(
            "candidate event artifactManifestDigest is missing or malformed"
        )
    if observed != expected:
        raise ExecutionEventBindingError(
            f"candidate event {field} conflicts with executionManifest"
        )


def is_manifest_bound_v1(request: Any) -> bool:
    """Return true only for a parsed, validated execution-manifest-v1 request."""

    return isinstance(getattr(request, "executionManifest", None), ExecutionManifestV1)


def context_snapshot_v1(request: Any) -> dict[str, Any] | None:
    """Build the exact RAG/AST snapshot coordinates for a candidate review."""

    manifest = getattr(request, "executionManifest", None)
    if not isinstance(manifest, ExecutionManifestV1):
        return None
    rag_context = getattr(request, "ragContext", None)
    if rag_context is None:
        raise ExecutionContextBindingError(
            "manifest-bound execution is missing its frozen RAG context"
        )
    return {
        "schema_version": 1,
        "base_sha": manifest.baseSha,
        "head_sha": manifest.headSha,
        "merge_base_sha": manifest.mergeBaseSha,
        "parser_version": rag_context.parserVersion,
        "chunker_version": rag_context.chunkerVersion,
        "embedding_version": rag_context.embeddingVersion,
    }


def context_branch_labels(request: Any) -> tuple[str | None, str | None]:
    """Return routing labels while snapshot coordinates provide correctness."""

    if is_manifest_bound_v1(request):
        return (
            getattr(request, "sourceBranchName", None),
            getattr(request, "targetBranchName", None),
        )
    return request.get_rag_branch(), request.get_rag_base_branch()


def bind_execution_context(
    request: ReviewRequestDto,
    *,
    transport_execution_id: str | None = None,
    now: datetime | None = None,
) -> ReviewRequestDto:
    """Return a request whose compatibility aliases are bound exactly once.

    Manifest aliases are always overwritten from the already validated,
    immutable manifest.  The queue's transport identifier is deliberately
    ignored for v1 and is available only to the bounded legacy adapter.
    """

    manifest = request.executionManifest
    if manifest is not None:
        review_context = (
            request.enrichmentData.reviewContext
            if request.enrichmentData is not None
            else None
        )
        return request.model_copy(
            update={
                "executionId": manifest.executionId,
                "baseRevision": manifest.baseSha,
                "headRevision": manifest.headSha,
                "previousCommitHash": manifest.baseSha,
                "currentCommitHash": manifest.headSha,
                "commitHash": manifest.headSha,
                "sourceBranchName": (
                    review_context.sourceBranchName
                    if review_context is not None
                    else manifest.headSha
                ),
                "targetBranchName": (
                    review_context.targetBranchName
                    if review_context is not None
                    else manifest.baseSha
                ),
                "policyVersion": manifest.policyVersion,
            }
        )

    compatibility = request.legacyCompatibility
    if compatibility is None:
        raise ExecutionContextBindingError(
            "request requires executionManifest or legacyCompatibility"
        )

    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("execution-context clock must be timezone-aware")
    if compatibility.deadline > _LEGACY_COMPATIBILITY_SUNSET:
        raise ExecutionContextBindingError(
            "legacyCompatibility.deadline exceeds the server compatibility sunset"
        )
    if compatibility.deadline <= observed_at:
        raise ExecutionContextBindingError(
            "legacyCompatibility.deadline has expired"
        )

    return request.model_copy(
        update={
            "executionId": request.executionId or transport_execution_id,
            "baseRevision": request.baseRevision or request.previousCommitHash,
            "headRevision": (
                request.headRevision
                or request.currentCommitHash
                or request.commitHash
            ),
        }
    )


def bind_manifest_file_revision(
    request: Any,
    requested_revision: str | None,
) -> str | None:
    """Translate a v1 MCP file read to its exact manifest head or base SHA.

    Non-review and explicit legacy requests retain their existing behavior.
    Unknown mutable refs in a manifest execution fail closed rather than
    allowing an LLM-provided branch name to escape the immutable snapshot.
    """

    manifest = getattr(request, "executionManifest", None)
    if manifest is None:
        return requested_revision

    if requested_revision in (manifest.headSha, manifest.baseSha):
        return requested_revision
    if requested_revision in (None, getattr(request, "sourceBranchName", None)):
        return manifest.headSha
    if requested_revision == getattr(request, "targetBranchName", None):
        return manifest.baseSha
    raise ExecutionContextBindingError(
        "manifest file read requested a revision outside the bound head/base snapshot"
    )
