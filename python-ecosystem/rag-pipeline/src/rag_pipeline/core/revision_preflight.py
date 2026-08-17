"""Read-only verification for immutable repository index revisions."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from qdrant_client.models import FieldCondition, Filter, MatchValue

from .generation_manifest import (
    GENERATION_MANIFEST_PATH,
    GENERATION_MANIFEST_PAYLOAD_KEY,
    GENERATION_SCHEMA,
    GenerationManifestError,
    canonical_index_selection_policy,
    compute_index_selection_policy_sha256,
    compute_generation_members_digest,
    generation_manifest_point_id,
    generation_manifest_content,
    is_sha256_hex,
    verified_generation_member,
)
from .index_representation import INDEX_REPRESENTATION_PAYLOAD_KEY
from .repository_overlay import (
    IncrementalIndexPreconditionError,
    scroll_branch_points,
)


_IDENTITY_PAYLOAD_FIELDS = (
    "plugin_ids",
    "plugin_fingerprint",
    "plugin_descriptor_fingerprint",
    "plugin_implementation_fingerprint",
    INDEX_REPRESENTATION_PAYLOAD_KEY,
)


def _payload_identity(payload):
    plugin_ids = payload.get("plugin_ids")
    fingerprints = tuple(
        payload.get(field) for field in _IDENTITY_PAYLOAD_FIELDS[1:]
    )
    if (
        not isinstance(plugin_ids, (list, tuple))
        or not all(
            isinstance(plugin_id, str) and plugin_id
            for plugin_id in plugin_ids
        )
        or not all(
            isinstance(fingerprint, str) and fingerprint
            for fingerprint in fingerprints
        )
    ):
        raise IncrementalIndexPreconditionError(
            "exact revision point is missing repository build identity; "
            "fully reindex the revision"
        )
    return (tuple(plugin_ids), *fingerprints)


def _load_exact_repository_facts(
    client,
    collection_name: str,
    branch: str,
    commit: str,
):
    """Load and integrity-check the facts sentinel for one exact revision."""
    from codecrow_plugins import RepositoryFacts

    points = scroll_branch_points(
        client,
        collection_name,
        branch,
        (
            FieldCondition(key="commit", match=MatchValue(value=commit)),
            FieldCondition(
                key="repository_facts_state",
                match=MatchValue(value=True),
            ),
        ),
    )
    if not points:
        raise IncrementalIndexPreconditionError(
            "exact revision repository detection facts are missing; "
            "fully reindex the revision"
        )

    payloads = [point.payload or {} for point in points]
    for payload in payloads:
        if (
            type(payload.get("facts_part")) is not int
            or payload["facts_part"] < 0
            or type(payload.get("facts_parts")) is not int
            or payload["facts_parts"] < 1
        ):
            raise IncrementalIndexPreconditionError(
                "exact revision repository detection facts have invalid part "
                "metadata; fully reindex the revision"
            )
    ordered = sorted(payloads, key=lambda payload: payload.get("facts_part", -1))
    expected_parts = ordered[0].get("facts_parts")
    actual_parts = [payload.get("facts_part") for payload in ordered]
    if (
        actual_parts != list(range(expected_parts))
        or any(payload.get("facts_parts") != expected_parts for payload in ordered)
    ):
        raise IncrementalIndexPreconditionError(
            "exact revision repository detection facts are incomplete; "
            "fully reindex the revision"
        )

    expected_digest = ordered[0].get("facts_content_sha256")
    if (
        not isinstance(expected_digest, str)
        or len(expected_digest) != 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_digest
        )
        or any(
            payload.get("facts_content_sha256") != expected_digest
            for payload in ordered
        )
    ):
        raise IncrementalIndexPreconditionError(
            "exact revision repository detection facts have invalid integrity "
            "metadata; fully reindex the revision"
        )

    content_parts = [
        payload.get("text", payload.get("_node_content", ""))
        for payload in ordered
    ]
    if not all(isinstance(part, str) for part in content_parts):
        raise IncrementalIndexPreconditionError(
            "exact revision repository detection facts have invalid content; "
            "fully reindex the revision"
        )
    content = "".join(content_parts)
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_digest:
        raise IncrementalIndexPreconditionError(
            "exact revision repository detection facts failed integrity "
            "validation; fully reindex the revision"
        )

    state_identity = None
    for payload in ordered:
        candidate = _payload_identity(payload)
        if state_identity is None:
            state_identity = candidate
        elif state_identity != candidate:
            raise IncrementalIndexPreconditionError(
                "exact revision repository detection facts have inconsistent "
                "build identity; fully reindex the revision"
            )

    try:
        decoded = json.loads(content)
        repository_facts = RepositoryFacts(
            revision=decoded["revision"],
            paths=tuple(decoded["paths"]),
            marker_contents=decoded.get("markerContents", {}),
            project_type=decoded.get("projectType"),
            source_root=decoded.get("sourceRoot"),
        )
    except Exception as exception:
        raise IncrementalIndexPreconditionError(
            "exact revision repository detection facts are invalid; "
            "fully reindex the revision"
        ) from exception
    if repository_facts.revision != commit:
        raise IncrementalIndexPreconditionError(
            "exact revision repository detection facts do not match the "
            "requested commit; fully reindex the revision"
        )
    return repository_facts, expected_digest, state_identity


def _validate_generation_manifest(
    manifest_points,
    members,
    branch: str,
    commit: str,
):
    """Validate the single seal against every observed non-manifest point."""
    if len(manifest_points) != 1:
        reason = "missing" if not manifest_points else "not unique"
        raise IncrementalIndexPreconditionError(
            f"exact revision repository generation manifest is {reason}; "
            "fully reindex the revision"
        )

    manifest_payload = manifest_points[0].payload or {}
    expected_count = manifest_payload.get("generation_member_count")
    expected_members_digest = manifest_payload.get(
        "generation_members_sha256"
    )
    expected_manifest_digest = manifest_payload.get(
        "generation_manifest_sha256"
    )
    source_tree_sha256 = manifest_payload.get("source_tree_sha256")
    index_include_patterns = manifest_payload.get("index_include_patterns")
    index_exclude_patterns = manifest_payload.get("index_exclude_patterns")
    index_selection_policy_sha256 = manifest_payload.get(
        "index_selection_policy_sha256"
    )
    workspace = manifest_payload.get("workspace")
    project = manifest_payload.get("project")
    if (
        manifest_payload.get("generation_schema") != GENERATION_SCHEMA
        or manifest_payload.get("path") != GENERATION_MANIFEST_PATH
        or not isinstance(workspace, str)
        or not workspace
        or not isinstance(project, str)
        or not project
        or type(expected_count) is not int
        or expected_count < 1
        or not is_sha256_hex(expected_members_digest)
        or not is_sha256_hex(expected_manifest_digest)
        or not is_sha256_hex(source_tree_sha256)
        or not isinstance(index_include_patterns, list)
        or not isinstance(index_exclude_patterns, list)
        or not is_sha256_hex(index_selection_policy_sha256)
    ):
        raise IncrementalIndexPreconditionError(
            "exact revision repository generation manifest is invalid; "
            "fully reindex the revision"
        )
    try:
        selection_policy = canonical_index_selection_policy(
            index_include_patterns,
            index_exclude_patterns,
        )
        observed_selection_policy_sha256 = (
            compute_index_selection_policy_sha256(
                selection_policy["includePatterns"],
                selection_policy["excludePatterns"],
            )
        )
    except GenerationManifestError as exception:
        raise IncrementalIndexPreconditionError(
            "exact revision repository index selection policy is invalid; "
            "fully reindex the revision"
        ) from exception
    if (
        index_include_patterns != selection_policy["includePatterns"]
        or index_exclude_patterns != selection_policy["excludePatterns"]
        or index_selection_policy_sha256
        != observed_selection_policy_sha256
    ):
        raise IncrementalIndexPreconditionError(
            "exact revision repository index selection policy failed "
            "integrity validation; fully reindex the revision"
        )
    if expected_count != len(members):
        raise IncrementalIndexPreconditionError(
            "exact revision repository generation is incomplete: "
            f"expected={expected_count}, actual={len(members)}; "
            "fully reindex the revision"
        )

    try:
        observed_members_digest = compute_generation_members_digest(members)
    except GenerationManifestError as exception:
        raise IncrementalIndexPreconditionError(
            "exact revision repository generation members are invalid; "
            "fully reindex the revision"
        ) from exception
    if observed_members_digest != expected_members_digest:
        raise IncrementalIndexPreconditionError(
            "exact revision repository generation membership failed integrity "
            "validation; fully reindex the revision"
        )

    expected_content = generation_manifest_content(
        workspace=workspace,
        project=project,
        branch=branch,
        commit=commit,
        member_count=expected_count,
        members_sha256=expected_members_digest,
        source_tree_sha256=source_tree_sha256,
        index_include_patterns=index_include_patterns,
        index_exclude_patterns=index_exclude_patterns,
        index_selection_policy_sha256=index_selection_policy_sha256,
    )
    if (
        hashlib.sha256(expected_content.encode("utf-8")).hexdigest()
        != expected_manifest_digest
    ):
        raise IncrementalIndexPreconditionError(
            "exact revision repository generation manifest failed integrity "
            "validation; fully reindex the revision"
        )
    return {
        "workspace": workspace,
        "project": project,
        "generation_schema": GENERATION_SCHEMA,
        "generation_member_count": expected_count,
        "generation_members_sha256": expected_members_digest,
        "generation_manifest_sha256": expected_manifest_digest,
        "source_tree_sha256": source_tree_sha256,
        "index_include_patterns": index_include_patterns,
        "index_exclude_patterns": index_exclude_patterns,
        "index_selection_policy_sha256": index_selection_policy_sha256,
    }


def read_repository_generation_manifest_receipt(
    client,
    collection_name: str,
    workspace: str,
    project: str,
    branch: str,
    commit: str,
    generation_manifest_sha256: str | None = None,
):
    """Validate one registry-selected immutable seal without scanning members.

    Readable aliases are non-authoritative operator conveniences. The Java
    registry has already accepted the digest returned by the full generation
    build, so alias repair only needs to prove that the selected physical
    target still contains that exact coordinate-bound manifest. Exact review
    preflight continues to verify every member and vector separately.
    """
    if (
        generation_manifest_sha256 is not None
        and not is_sha256_hex(generation_manifest_sha256)
    ):
        raise IncrementalIndexPreconditionError(
            "repository generation manifest receipt is invalid"
        )
    records = client.retrieve(
        collection_name=collection_name,
        ids=[generation_manifest_point_id(workspace, project, branch)],
        with_payload=True,
        with_vectors=False,
    )
    if len(records) != 1:
        return None
    payload = records[0].payload or {}
    stored_manifest_sha256 = payload.get("generation_manifest_sha256")
    if any(payload.get(key) != value for key, value in (
        ("workspace", workspace),
        ("project", project),
        ("branch", branch),
        ("commit", commit),
    )):
        return None
    if (
        payload.get(GENERATION_MANIFEST_PAYLOAD_KEY) is not True
        or payload.get("generation_schema") != GENERATION_SCHEMA
        or payload.get("path") != GENERATION_MANIFEST_PATH
        or not is_sha256_hex(stored_manifest_sha256)
        or (
            generation_manifest_sha256 is not None
            and stored_manifest_sha256 != generation_manifest_sha256
        )
    ):
        return None

    # Recompute the manifest digest from its complete coordinate/membership
    # metadata. This remains O(1): member contents are intentionally not read
    # on the optional alias-repair path.
    expected_count = payload.get("generation_member_count")
    members_sha256 = payload.get("generation_members_sha256")
    source_tree_sha256 = payload.get("source_tree_sha256")
    include_patterns = payload.get("index_include_patterns")
    exclude_patterns = payload.get("index_exclude_patterns")
    selection_digest = payload.get("index_selection_policy_sha256")
    if (
        type(expected_count) is not int
        or expected_count < 1
        or not is_sha256_hex(members_sha256)
        or not is_sha256_hex(source_tree_sha256)
        or not isinstance(include_patterns, list)
        or not isinstance(exclude_patterns, list)
        or not is_sha256_hex(selection_digest)
    ):
        return None
    try:
        policy = canonical_index_selection_policy(
            include_patterns,
            exclude_patterns,
        )
    except GenerationManifestError:
        return None
    if (
        include_patterns != policy["includePatterns"]
        or exclude_patterns != policy["excludePatterns"]
        or selection_digest != compute_index_selection_policy_sha256(
            include_patterns,
            exclude_patterns,
        )
    ):
        return None
    content = generation_manifest_content(
        workspace=workspace,
        project=project,
        branch=branch,
        commit=commit,
        member_count=expected_count,
        members_sha256=members_sha256,
        source_tree_sha256=source_tree_sha256,
        index_include_patterns=include_patterns,
        index_exclude_patterns=exclude_patterns,
        index_selection_policy_sha256=selection_digest,
    )
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != stored_manifest_sha256:
        return None
    if payload.get("text", payload.get("_node_content")) != content:
        return None
    return {
        "workspace": workspace,
        "project": project,
        "branch": branch,
        "commit": commit,
        "generation_manifest_sha256": stored_manifest_sha256,
    }


def _require_unmixed_branch_revision(
    client,
    collection_name: str,
    branch: str,
    commit: str,
) -> None:
    """Reject a branch whose non-PR retrieval population spans revisions."""
    branch_filter = Filter(
        must=[
            FieldCondition(key="branch", match=MatchValue(value=branch)),
        ],
        must_not=[
            FieldCondition(key="pr", match=MatchValue(value=True)),
        ],
    )
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=branch_filter,
            limit=256,
            offset=offset,
            with_payload=["branch", "commit", "pr"],
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            if (
                payload.get("branch") != branch
                or payload.get("pr") is True
                or payload.get("commit") != commit
            ):
                raise IncrementalIndexPreconditionError(
                    "exact revision branch contains mixed repository revisions; "
                    "fully reindex the revision"
                )
        if offset is None:
            break


def read_repository_revision_preflight(
    client,
    collection_name: str,
    branch: str,
    commit: str,
):
    """Return a verified exact repository revision or ``None`` when absent.

    A revision is reusable only when every matching repository point carries
    one consistent representation/plugin identity, its content-addressed
    generation membership matches the atomic full-index seal, and its
    repository-facts sentinel is complete, digest-valid, and bound to the
    requested commit. Qdrant failures intentionally propagate so callers
    cannot mistake an unavailable store for a missing revision.
    """
    revision_filter = Filter(
        must=[
            FieldCondition(key="branch", match=MatchValue(value=branch)),
            FieldCondition(key="commit", match=MatchValue(value=commit)),
        ],
        must_not=[
            FieldCondition(key="pr", match=MatchValue(value=True)),
        ],
    )
    point_count = 0
    identity = None
    manifest_points = []
    members = []
    member_validation_error = None
    offset = None

    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=revision_filter,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        point_count += len(points)
        for point in points:
            payload = point.payload or {}
            if (
                payload.get("branch") != branch
                or payload.get("commit") != commit
                or payload.get("pr") is True
            ):
                raise IncrementalIndexPreconditionError(
                    "exact revision query returned a point outside the requested "
                    "repository snapshot"
                )
            candidate = _payload_identity(payload)
            if identity is None:
                identity = candidate
            elif identity != candidate:
                raise IncrementalIndexPreconditionError(
                    "exact revision has inconsistent repository build identity; "
                    "fully reindex the revision"
                )
            if payload.get(GENERATION_MANIFEST_PAYLOAD_KEY) is True:
                # Retain only the small manifest payload. Keeping its 4096-d
                # vector would otherwise pin one full Qdrant point until the
                # complete generation scan finishes.
                manifest_points.append(SimpleNamespace(payload=dict(payload)))
            else:
                try:
                    # Verify while this page is live and retain only the
                    # compact (point id, digest) receipt. Never accumulate
                    # complete payloads and vectors for the whole repository.
                    members.append(verified_generation_member(point))
                except GenerationManifestError as exception:
                    # Preserve validation priority from the original preflight:
                    # mixed identity/revision and missing-manifest diagnostics
                    # are established before member-content failure. Keep only
                    # the error text so its traceback cannot pin this page.
                    if member_validation_error is None:
                        member_validation_error = str(exception)
        point = None
        payload = None
        del points
        if offset is None:
            break

    if point_count == 0:
        return None

    _require_unmixed_branch_revision(
        client,
        collection_name,
        branch,
        commit,
    )
    if len(manifest_points) != 1:
        _validate_generation_manifest(
            manifest_points,
            [],
            branch,
            commit,
        )
    if member_validation_error is not None:
        raise IncrementalIndexPreconditionError(
            "exact revision repository generation member content failed "
            "integrity validation; fully reindex the revision"
        )
    generation_identity = _validate_generation_manifest(
        manifest_points,
        members,
        branch,
        commit,
    )
    repository_facts, facts_digest, state_identity = (
        _load_exact_repository_facts(
            client,
            collection_name,
            branch,
            commit,
        )
    )
    if identity != state_identity:
        raise IncrementalIndexPreconditionError(
            "exact revision points do not match repository detection build "
            "identity; fully reindex the revision"
        )

    (
        plugin_ids,
        plugin_fingerprint,
        descriptor_fingerprint,
        implementation_fingerprint,
        representation_fingerprint,
    ) = identity
    return {
        "branch": branch,
        "commit": commit,
        "point_count": point_count,
        "repository_revision": repository_facts.revision,
        "repository_facts_sha256": facts_digest,
        "plugin_ids": list(plugin_ids),
        "plugin_fingerprint": plugin_fingerprint,
        "plugin_descriptor_fingerprint": descriptor_fingerprint,
        "plugin_implementation_fingerprint": implementation_fingerprint,
        "index_representation_fingerprint": representation_fingerprint,
        **generation_identity,
    }
