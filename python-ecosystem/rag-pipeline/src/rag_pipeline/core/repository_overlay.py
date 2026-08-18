"""Neutral repository-plugin snapshot loading and exact changed-file overlays."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from qdrant_client.models import FieldCondition, Filter, MatchValue


class IncrementalIndexPreconditionError(RuntimeError):
    """The branch requires a full reindex before an incremental mutation."""


def scroll_branch_points(
    client,
    collection_name: str,
    branch: str,
    conditions=(),
    *,
    with_vectors: bool = False,
):
    """Read all points matching an exact branch-scoped neutral filter."""
    points = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(must=[
                FieldCondition(key="branch", match=MatchValue(value=branch)),
                *conditions,
            ]),
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=with_vectors,
        )
        points.extend(batch)
        if offset is None:
            break
    return points


def load_repository_snapshots(
    client,
    collection_name: str,
    branch: str,
    *,
    include_facts: bool = False,
):
    """Load snapshots and the selected capabilities for one indexed branch."""
    from codecrow_plugins import RepositorySnapshot

    (
        _repository_facts,
        facts_plugin_ids,
        facts_fingerprint,
        facts_descriptor_fingerprint,
        facts_implementation_fingerprint,
    ) = load_repository_facts(client, collection_name, branch)
    points = scroll_branch_points(
        client,
        collection_name,
        branch,
        (FieldCondition(
            key="repository_snapshot",
            match=MatchValue(value=True),
        ),),
    )

    grouped = defaultdict(list)
    plugin_ids = facts_plugin_ids if _repository_facts is not None else None
    fingerprint = facts_fingerprint if _repository_facts is not None else None
    descriptor_fingerprint = (
        facts_descriptor_fingerprint if _repository_facts is not None else None
    )
    implementation_fingerprint = (
        facts_implementation_fingerprint if _repository_facts is not None else None
    )
    identity_initialized = _repository_facts is not None
    for point in points:
        payload = point.payload or {}
        key = (payload.get("snapshot_plugin"), payload.get("snapshot_kind"))
        if not all(isinstance(value, str) and value for value in key):
            raise IncrementalIndexPreconditionError(
                "repository snapshot point is missing plugin identity; fully "
                "reindex the branch"
            )
        grouped[key].append(payload)
        candidate_ids = tuple(payload.get("plugin_ids", ()))
        candidate_fingerprint = payload.get("plugin_fingerprint")
        candidate_descriptor_fingerprint = payload.get(
            "plugin_descriptor_fingerprint"
        )
        candidate_implementation_fingerprint = payload.get(
            "plugin_implementation_fingerprint"
        )
        if not identity_initialized:
            plugin_ids = candidate_ids
            fingerprint = candidate_fingerprint
            descriptor_fingerprint = candidate_descriptor_fingerprint
            implementation_fingerprint = candidate_implementation_fingerprint
            identity_initialized = True
        elif (
            plugin_ids != candidate_ids
        ):
            raise IncrementalIndexPreconditionError(
                "repository snapshot plugin selection is inconsistent; "
                "fully reindex the branch"
            )

    snapshots = []
    for (plugin_id, kind), payloads in sorted(grouped.items()):
        ordered = sorted(payloads, key=lambda payload: payload.get("snapshot_part", -1))
        expected_parts = ordered[0].get("snapshot_parts") if ordered else 0
        actual_parts = [payload.get("snapshot_part") for payload in ordered]
        if actual_parts != list(range(expected_parts)):
            raise IncrementalIndexPreconditionError(
                f"repository snapshot {plugin_id}:{kind} is incomplete; fully "
                "reindex the branch"
            )
        content = "".join(
            payload.get("text", payload.get("_node_content", ""))
            for payload in ordered
        )
        expected_digest = ordered[0].get("snapshot_content_sha256")
        actual_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if not expected_digest or actual_digest != expected_digest:
            raise IncrementalIndexPreconditionError(
                f"repository snapshot {plugin_id}:{kind} failed integrity "
                "validation; fully reindex the branch"
            )
        snapshots.append(RepositorySnapshot(plugin_id, kind, content))

    if plugin_ids is None:
        (
            plugin_ids,
            fingerprint,
            descriptor_fingerprint,
            implementation_fingerprint,
        ) = load_branch_capability_metadata(
            client,
            collection_name,
            branch,
        )

    result = (
        tuple(snapshots),
        tuple(plugin_ids or ()),
        fingerprint,
        descriptor_fingerprint,
        implementation_fingerprint,
    )
    return (*result, _repository_facts) if include_facts else result


def load_repository_facts(client, collection_name: str, branch: str):
    """Restore the exact neutral repository inventory used for plugin selection."""
    from codecrow_plugins import RepositoryFacts

    points = scroll_branch_points(
        client,
        collection_name,
        branch,
        (FieldCondition(
            key="repository_facts_state",
            match=MatchValue(value=True),
        ),),
    )
    if not points:
        return None, (), None, None, None

    payloads = [point.payload or {} for point in points]
    ordered = sorted(payloads, key=lambda payload: payload.get("facts_part", -1))
    expected_parts = ordered[0].get("facts_parts")
    if (
        not isinstance(expected_parts, int)
        or expected_parts < 1
        or [payload.get("facts_part") for payload in ordered]
        != list(range(expected_parts))
    ):
        raise IncrementalIndexPreconditionError(
            "repository detection facts are incomplete; fully reindex the branch"
        )

    expected_digest = ordered[0].get("facts_content_sha256")
    content = "".join(
        payload.get("text", payload.get("_node_content", ""))
        for payload in ordered
    )
    if (
        not isinstance(expected_digest, str)
        or hashlib.sha256(content.encode("utf-8")).hexdigest()
        != expected_digest
    ):
        raise IncrementalIndexPreconditionError(
            "repository detection facts failed integrity validation; fully "
            "reindex the branch"
        )

    identity = None
    plugin_ids_identity = None
    for payload in ordered:
        candidate = (
            tuple(payload.get("plugin_ids", ())),
            payload.get("plugin_fingerprint"),
            payload.get("plugin_descriptor_fingerprint"),
            payload.get("plugin_implementation_fingerprint"),
        )
        if identity is None:
            identity = candidate
            plugin_ids_identity = candidate[0]
        elif plugin_ids_identity != candidate[0]:
            raise IncrementalIndexPreconditionError(
                "repository detection facts plugin selection is inconsistent; "
                "fully reindex the branch"
            )

    try:
        decoded = json.loads(content)
        facts = RepositoryFacts(
            revision=decoded["revision"],
            paths=tuple(decoded["paths"]),
            marker_contents=decoded.get("markerContents", {}),
            project_type=decoded.get("projectType"),
            source_root=decoded.get("sourceRoot"),
        )
    except Exception as exception:
        raise IncrementalIndexPreconditionError(
            "repository detection facts are invalid; fully reindex the branch"
        ) from exception

    plugin_ids, fingerprint, descriptor_fingerprint, implementation_fingerprint = (
        identity or ((), None, None, None)
    )
    return (
        facts,
        tuple(plugin_ids),
        fingerprint,
        descriptor_fingerprint,
        implementation_fingerprint,
    )


def load_branch_capability_metadata(client, collection_name: str, branch: str):
    """Read capability identity from a normal point when no snapshots exist."""
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(must=[
                FieldCondition(key="branch", match=MatchValue(value=branch)),
            ]),
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            candidate_ids = payload.get("plugin_ids")
            candidate_fingerprint = payload.get("plugin_fingerprint")
            candidate_descriptor_fingerprint = payload.get(
                "plugin_descriptor_fingerprint"
            )
            candidate_implementation_fingerprint = payload.get(
                "plugin_implementation_fingerprint"
            )
            if candidate_ids is None and candidate_fingerprint is None:
                continue
            if not isinstance(candidate_ids, (list, tuple)) or not all(
                isinstance(plugin_id, str) and plugin_id
                for plugin_id in candidate_ids
            ):
                raise RuntimeError(
                    "indexed branch capability metadata has invalid plugin identities"
                )
            return (
                tuple(candidate_ids),
                candidate_fingerprint,
                candidate_descriptor_fingerprint,
                candidate_implementation_fingerprint,
            )
        if offset is None:
            break
    return (), None, None, None


def architecture_group_from_payload(payload):
    """Recover the neutral compaction group from stored packet metadata."""
    plugin_id = payload.get("architecture_plugin")
    kind = payload.get("architecture_kind")
    source_path = payload.get("architecture_source_path")
    if not source_path:
        facts = payload.get("plugin_graph_facts") or ()
        fact_paths = {
            fact.get("path") for fact in facts
            if isinstance(fact, dict) and fact.get("path")
        }
        if len(fact_paths) == 1:
            source_path = next(iter(fact_paths))
    if all(isinstance(value, str) and value for value in (plugin_id, kind, source_path)):
        return plugin_id, kind, source_path
    raise RuntimeError("architecture point is missing its neutral compaction identity")


def architecture_group_id(group):
    """Return the stable storage identity for one neutral graph group."""
    return hashlib.sha256("\0".join(group).encode("utf-8")).hexdigest()


def affected_architecture_groups(analysis, changed_paths):
    """Find graph groups whose contract paths intersect changed files."""
    changed = set(changed_paths)
    groups = set()
    for packet in analysis.packets:
        packet_changed = bool(changed.intersection(packet.paths))
        for fact in packet.facts:
            fact_paths = {fact.path, *fact.related_paths}
            if packet_changed or changed.intersection(fact_paths):
                groups.add((packet.plugin_id, packet.kind, fact.path))
    return groups


def build_overlay_capabilities(
    registry,
    repository_plugins: tuple[str, ...],
    fingerprint: str,
    paths: tuple[str, ...],
    *,
    revision: str | None = None,
    detection_evidence=None,
):
    """Recreate neutral capabilities for an already-selected repository."""
    from codecrow_plugins import PluginKind, ProjectCapabilities, ProjectSelector

    resolved = registry.resolve(repository_plugins)
    active_languages = tuple(
        descriptor for descriptor in resolved
        if descriptor.kind is PluginKind.LANGUAGE
    )
    file_plugins = {}
    for path in paths:
        extension = "." + path.rsplit(".", 1)[-1].casefold() if "." in path else ""
        matches = tuple(
            descriptor.id for descriptor in active_languages
            if extension in descriptor.detection.extensions
        )
        if matches:
            file_plugins[path] = matches
    if revision is not None or detection_evidence is not None:
        if not revision:
            raise ValueError(
                "effective overlay capabilities require an immutable revision"
            )
        if detection_evidence is None:
            raise ValueError(
                "effective overlay capabilities require selection evidence"
            )
        return ProjectSelector(registry).project(
            revision=revision,
            repository_plugins=repository_plugins,
            file_plugins=file_plugins,
            detection_evidence=detection_evidence,
        )

    return ProjectCapabilities(
        repository_plugins=repository_plugins,
        file_plugins=file_plugins,
        detection_evidence={},
        unavailable_capabilities=(),
        fingerprint=fingerprint,
        descriptor_fingerprint=registry.fingerprint_for(repository_plugins),
    )
