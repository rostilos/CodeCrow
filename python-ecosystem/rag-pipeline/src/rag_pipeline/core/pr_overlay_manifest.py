"""Content-addressed completeness seals for persisted pull-request overlays."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from llama_index.core.schema import TextNode
from qdrant_client.models import FieldCondition, Filter, MatchValue

from .generation_manifest import (
    GenerationManifestError,
    compute_generation_members_digest,
    verified_generation_member,
)
from .repository_overlay import IncrementalIndexPreconditionError


PR_OVERLAY_MANIFEST_PAYLOAD_KEY = "pr_overlay_generation_manifest"
PR_OVERLAY_SCHEMA = "codecrow.pr-overlay-generation"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def pr_overlay_manifest_path(pr_number: int) -> str:
    return (
        "__analysis_state__/pr-overlay-generation/"
        f"{pr_number}/000000.state"
    )


def pr_overlay_manifest_content(
    *,
    workspace: str,
    project: str,
    pr_number: int,
    branch: str,
    base_branch: str,
    source_revision: str,
    base_revision: str,
    base_generation_manifest_sha256: str,
    generation_fingerprint: str,
    overlay_representation_fingerprint: str,
    member_count: int,
    members_sha256: str,
) -> str:
    return _canonical_json({
        "baseBranch": base_branch,
        "baseGenerationManifestSha256": base_generation_manifest_sha256,
        "baseRevision": base_revision,
        "branch": branch,
        "generationFingerprint": generation_fingerprint,
        "memberCount": member_count,
        "membersSha256": members_sha256,
        "overlayRepresentationFingerprint": (
            overlay_representation_fingerprint
        ),
        "prNumber": pr_number,
        "project": project,
        "schema": PR_OVERLAY_SCHEMA,
        "sourceRevision": source_revision,
        "workspace": workspace,
    })


def build_pr_overlay_manifest_node(
    *,
    workspace: str,
    project: str,
    pr_number: int,
    branch: str,
    base_branch: str,
    source_revision: str,
    base_revision: str,
    base_generation_manifest_sha256: str,
    generation_fingerprint: str,
    overlay_representation_fingerprint: str,
    members: Sequence[tuple[object, str]],
    identity_metadata: Mapping[str, Any],
) -> tuple[TextNode, dict[str, Any]]:
    members_sha256 = compute_generation_members_digest(members)
    content = pr_overlay_manifest_content(
        workspace=workspace,
        project=project,
        pr_number=pr_number,
        branch=branch,
        base_branch=base_branch,
        source_revision=source_revision,
        base_revision=base_revision,
        base_generation_manifest_sha256=(
            base_generation_manifest_sha256
        ),
        generation_fingerprint=generation_fingerprint,
        overlay_representation_fingerprint=(
            overlay_representation_fingerprint
        ),
        member_count=len(members),
        members_sha256=members_sha256,
    )
    manifest_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    metadata = {
        "workspace": workspace,
        "project": project,
        "branch": branch,
        "path": pr_overlay_manifest_path(pr_number),
        "language": "repository-state",
        "filetype": "state",
        "pr": True,
        "pr_number": pr_number,
        "pr_branch": branch,
        "pr_source_revision": source_revision,
        "pr_base_revision": base_revision,
        "pr_base_generation_manifest_sha256": (
            base_generation_manifest_sha256
        ),
        "pr_generation_fingerprint": generation_fingerprint,
        PR_OVERLAY_MANIFEST_PAYLOAD_KEY: True,
        "pr_overlay_generation_schema": PR_OVERLAY_SCHEMA,
        "pr_overlay_generation_member_count": len(members),
        "pr_overlay_generation_members_sha256": members_sha256,
        "pr_overlay_generation_manifest_sha256": manifest_sha256,
        "pr_overlay_base_branch": base_branch,
        "pr_overlay_representation_fingerprint": (
            overlay_representation_fingerprint
        ),
        **dict(identity_metadata),
    }
    return TextNode(text=content, metadata=metadata), {
        "overlay_generation_member_count": len(members),
        "overlay_generation_members_sha256": members_sha256,
        "overlay_generation_manifest_sha256": manifest_sha256,
    }


def _pr_filter(
    *,
    workspace: str,
    project: str,
    pr_number: int,
    branch: str,
    source_revision: str,
    base_revision: str,
    base_generation_manifest_sha256: str,
    generation_fingerprint: str,
    overlay_representation_fingerprint: str,
) -> Filter:
    return Filter(must=[
        FieldCondition(key="pr", match=MatchValue(value=True)),
        FieldCondition(
            key="workspace",
            match=MatchValue(value=workspace),
        ),
        FieldCondition(
            key="project",
            match=MatchValue(value=project),
        ),
        FieldCondition(
            key="pr_number",
            match=MatchValue(value=pr_number),
        ),
        FieldCondition(
            key="branch",
            match=MatchValue(value=branch),
        ),
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
            match=MatchValue(value=base_generation_manifest_sha256),
        ),
        FieldCondition(
            key="pr_generation_fingerprint",
            match=MatchValue(value=generation_fingerprint),
        ),
        FieldCondition(
            key="pr_overlay_representation_fingerprint",
            match=MatchValue(value=overlay_representation_fingerprint),
        ),
    ])


def read_pr_overlay_generation(
    client,
    collection_name: str,
    *,
    workspace: str,
    project: str,
    pr_number: int,
    branch: str,
    base_branch: str,
    source_revision: str,
    base_revision: str,
    base_generation_manifest_sha256: str,
    generation_fingerprint: str,
    overlay_representation_fingerprint: str,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any] | None:
    """Read and verify every member of one exact PR overlay generation."""
    points = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=_pr_filter(
                workspace=workspace,
                project=project,
                pr_number=pr_number,
                branch=branch,
                source_revision=source_revision,
                base_revision=base_revision,
                base_generation_manifest_sha256=(
                    base_generation_manifest_sha256
                ),
                generation_fingerprint=generation_fingerprint,
                overlay_representation_fingerprint=(
                    overlay_representation_fingerprint
                ),
            ),
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        points.extend(batch)
        if offset is None:
            break
    if not points:
        return None

    manifest_points = [
        point
        for point in points
        if (point.payload or {}).get(PR_OVERLAY_MANIFEST_PAYLOAD_KEY) is True
    ]
    if len(manifest_points) != 1:
        raise IncrementalIndexPreconditionError(
            "PR overlay generation manifest is missing or not unique"
        )
    manifest = manifest_points[0].payload or {}
    members = [
        point
        for point in points
        if (point.payload or {}).get(PR_OVERLAY_MANIFEST_PAYLOAD_KEY) is not True
    ]
    expected_identity = {
        "workspace": workspace,
        "project": project,
        "branch": branch,
        "pr_number": pr_number,
        "pr_source_revision": source_revision,
        "pr_base_revision": base_revision,
        "pr_base_generation_manifest_sha256": (
            base_generation_manifest_sha256
        ),
        "pr_generation_fingerprint": generation_fingerprint,
        "pr_overlay_base_branch": base_branch,
        "pr_overlay_representation_fingerprint": (
            overlay_representation_fingerprint
        ),
    }
    if any(
        manifest.get(field) != expected
        for field, expected in expected_identity.items()
    ):
        raise IncrementalIndexPreconditionError(
            "PR overlay generation manifest identity does not match the request"
        )
    manifest_sha256 = manifest.get(
        "pr_overlay_generation_manifest_sha256"
    )
    member_count = manifest.get("pr_overlay_generation_member_count")
    members_sha256 = manifest.get("pr_overlay_generation_members_sha256")
    if (
        manifest.get("pr_overlay_generation_schema") != PR_OVERLAY_SCHEMA
        or manifest.get("path") != pr_overlay_manifest_path(pr_number)
        or type(member_count) is not int
        or member_count < 0
        or not isinstance(members_sha256, str)
        or _SHA256_RE.fullmatch(members_sha256) is None
        or not isinstance(manifest_sha256, str)
        or _SHA256_RE.fullmatch(manifest_sha256) is None
        or (
            expected_manifest_sha256 is not None
            and manifest_sha256 != expected_manifest_sha256
        )
    ):
        raise IncrementalIndexPreconditionError(
            "PR overlay generation manifest is invalid"
        )
    if len(members) != member_count:
        raise IncrementalIndexPreconditionError(
            "PR overlay generation membership is incomplete"
        )
    for point in members:
        payload = point.payload or {}
        if any(
            payload.get(field) != expected
            for field, expected in expected_identity.items()
            if field not in {"pr_overlay_base_branch"}
        ):
            raise IncrementalIndexPreconditionError(
                "PR overlay contains a member outside the sealed generation"
            )
        if payload.get(PR_OVERLAY_MANIFEST_PAYLOAD_KEY) is True:
            raise IncrementalIndexPreconditionError(
                "PR overlay manifest was included as an ordinary member"
            )
    try:
        observed_members = [
            verified_generation_member(point) for point in members
        ]
        observed_members_sha256 = compute_generation_members_digest(
            observed_members
        )
    except GenerationManifestError as exception:
        raise IncrementalIndexPreconditionError(
            "PR overlay generation member integrity failed"
        ) from exception
    if observed_members_sha256 != members_sha256:
        raise IncrementalIndexPreconditionError(
            "PR overlay generation membership digest does not match its seal"
        )
    expected_content = pr_overlay_manifest_content(
        workspace=workspace,
        project=project,
        pr_number=pr_number,
        branch=branch,
        base_branch=base_branch,
        source_revision=source_revision,
        base_revision=base_revision,
        base_generation_manifest_sha256=(
            base_generation_manifest_sha256
        ),
        generation_fingerprint=generation_fingerprint,
        overlay_representation_fingerprint=(
            overlay_representation_fingerprint
        ),
        member_count=member_count,
        members_sha256=members_sha256,
    )
    if hashlib.sha256(expected_content.encode("utf-8")).hexdigest() != (
        manifest_sha256
    ):
        raise IncrementalIndexPreconditionError(
            "PR overlay generation manifest content failed integrity validation"
        )
    return {
        "overlay_generation_member_count": member_count,
        "overlay_generation_members_sha256": members_sha256,
        "overlay_generation_manifest_sha256": manifest_sha256,
    }


def is_pr_overlay_fingerprint(value: object) -> bool:
    return isinstance(value, str) and _FINGERPRINT_RE.fullmatch(value) is not None
