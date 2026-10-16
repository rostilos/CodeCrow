"""Canonical immutable generation receipts and streaming content digests."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..generation_manifest import (
    GENERATION_SCHEMA,
    canonical_index_selection_policy,
    compute_index_selection_policy_sha256,
)

from .shared import (
    STRUCTURAL_STORE_SCHEMA,
    STRUCTURAL_STORE_SCHEMA_REVISION,
    _ZERO_FINGERPRINT,
    _SHA256_RE,
    _canonical_json,
    _sha256_text,
)

def build_receipt(
    connection: sqlite3.Connection,
    *,
    workspace: str,
    project: str,
    branch: str,
    revision: str,
    source_tree_sha256: str,
    collection_target: str,
    repository_facts_json: str,
    plugin_ids: Sequence[str],
    plugin_fingerprint: str,
    plugin_descriptor_fingerprint: str,
    plugin_implementation_fingerprint: str,
    index_representation_fingerprint: str,
    include_patterns: Sequence[str] | None,
    exclude_patterns: Sequence[str] | None,
    document_count: int,
    skipped_file_count: int,
    snapshot_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Hash the canonical member array as a stream. Large framework indexes can
    # contain hundreds of thousands of edges; retaining three complete Python
    # member lists (plus a contributor map) multiplied memory by every parallel
    # indexing job without changing the generation digest.
    members_hasher = hashlib.sha256()
    members_hasher.update(b"[")
    member_count = 0
    unit_count = 0
    relation_count = 0
    snapshot_count = 0

    def add_encoded_member(encoded_member: str) -> None:
        nonlocal member_count
        if member_count:
            members_hasher.update(b",")
        members_hasher.update(encoded_member.encode("utf-8"))
        member_count += 1

    def add_member(member: tuple[str, str, str, str]) -> None:
        add_encoded_member(_canonical_json(member))

    for row in connection.execute(
        "SELECT unit_id, content_sha256, metadata_json "
        "FROM units ORDER BY unit_id"
    ):
        add_member((
            "unit",
            str(row["unit_id"]),
            str(row["content_sha256"]),
            str(row["metadata_json"]),
        ))
        unit_count += 1

    # A cloned delta changes only a bounded subset of a structural graph. The
    # canonical digest of an unchanged relation row is therefore safe to carry
    # with the clone. SQLite invalidates the cache on every relation UPDATE and
    # cascades DELETEs; new rows have no cache entry. Recompute only those
    # missing rows before streaming the exact same canonical member sequence.
    plugin_rows = iter(connection.execute(
        "SELECT relation_id, plugin_id FROM relation_plugins "
        "ORDER BY relation_id, plugin_id"
    ))
    plugin_row = next(plugin_rows, None)
    last_relation_id = ""
    while True:
        # Keyset pages bound transient Python memory even on a cold cache.
        # All members still contribute to the same complete ordered digest.
        missing_rows = connection.execute(
            "SELECT relations.* FROM relations "
            "LEFT JOIN relation_manifest_cache USING (relation_id) "
            "WHERE relations.relation_id > ? "
            "AND relation_manifest_cache.relation_id IS NULL "
            "ORDER BY relations.relation_id LIMIT 512",
            (last_relation_id,),
        ).fetchall()
        if not missing_rows:
            break
        missing_relation_members = []
        for row in missing_rows:
            payload = dict(row)
            # Endpoint IDs and the single-plugin shortcut are derived indexes. The
            # logical source/target and exact contributor array below are the
            # authoritative relation content. Excluding derived columns keeps
            # endpoint re-resolution from invalidating tens of thousands of
            # otherwise unchanged semantic relation digests on every delta.
            payload.pop("source_unit_id", None)
            payload.pop("target_unit_id", None)
            payload.pop("plugin_id", None)
            relation_id = str(row["relation_id"])
            contributors: list[str] = []
            while (
                plugin_row is not None
                and str(plugin_row["relation_id"]) < relation_id
            ):
                plugin_row = next(plugin_rows, None)
            while (
                plugin_row is not None
                and str(plugin_row["relation_id"]) == relation_id
            ):
                contributors.append(str(plugin_row["plugin_id"]))
                plugin_row = next(plugin_rows, None)
            missing_relation_members.append((
                relation_id,
                _canonical_json((
                    "relation",
                    relation_id,
                    _sha256_text(_canonical_json(payload)),
                    _canonical_json(contributors),
                )),
            ))
        connection.executemany(
            "INSERT INTO relation_manifest_cache(relation_id, member_json) "
            "VALUES (?, ?)",
            missing_relation_members,
        )
        last_relation_id = str(missing_rows[-1]["relation_id"])

    for row in connection.execute(
        "SELECT member_json FROM relation_manifest_cache ORDER BY relation_id"
    ):
        add_encoded_member(str(row["member_json"]))
        relation_count += 1

    for row in connection.execute(
        "SELECT plugin_id, kind, content_sha256 "
        "FROM repository_snapshots ORDER BY plugin_id, kind"
    ):
        add_member((
            "snapshot",
            f"{row['plugin_id']}:{row['kind']}",
            str(row["content_sha256"]),
            "",
        ))
        snapshot_count += 1
    members_hasher.update(b"]")
    members_sha256 = members_hasher.hexdigest()
    selection = canonical_index_selection_policy(include_patterns, exclude_patterns)
    selection_sha256 = compute_index_selection_policy_sha256(
        selection["includePatterns"],
        selection["excludePatterns"],
    )
    repository_facts_sha256 = _sha256_text(repository_facts_json)
    normalized_snapshot_metadata = dict(snapshot_metadata or {})
    manifest_content = {
        "schema": GENERATION_SCHEMA,
        "storeSchema": STRUCTURAL_STORE_SCHEMA,
        "storeSchemaRevision": STRUCTURAL_STORE_SCHEMA_REVISION,
        "workspace": workspace,
        "project": project,
        "branch": branch,
        "revision": revision,
        "collectionTargetSha256": _sha256_text(collection_target),
        "sourceTreeSha256": source_tree_sha256,
        "memberCount": member_count,
        "membersSha256": members_sha256,
        "documentCount": int(document_count),
        "skippedFileCount": int(skipped_file_count),
        "repositoryFactsSha256": repository_facts_sha256,
        "indexSelectionPolicySha256": selection_sha256,
        "pluginIds": list(plugin_ids),
        "pluginFingerprint": plugin_fingerprint,
        "pluginDescriptorFingerprint": plugin_descriptor_fingerprint,
        "pluginImplementationFingerprint": plugin_implementation_fingerprint,
        "indexRepresentationFingerprint": index_representation_fingerprint,
        "snapshotMetadata": normalized_snapshot_metadata,
    }
    manifest_sha256 = _sha256_text(_canonical_json(manifest_content))
    return {
        "workspace": workspace,
        "project": project,
        "branch": branch,
        # Kept alongside the explicit repository_revision field because the
        # stable preflight response contract exposes both names.
        "commit": revision,
        "repository_revision": revision,
        "repository_facts_sha256": repository_facts_sha256,
        "plugin_ids": list(plugin_ids),
        "plugin_fingerprint": plugin_fingerprint or _ZERO_FINGERPRINT,
        "plugin_descriptor_fingerprint": (
            plugin_descriptor_fingerprint or _ZERO_FINGERPRINT
        ),
        "plugin_implementation_fingerprint": (
            plugin_implementation_fingerprint or _ZERO_FINGERPRINT
        ),
        "index_representation_fingerprint": index_representation_fingerprint,
        "current_index_representation_fingerprint": index_representation_fingerprint,
        "generation_schema": GENERATION_SCHEMA,
        "store_schema": STRUCTURAL_STORE_SCHEMA,
        "store_schema_revision": STRUCTURAL_STORE_SCHEMA_REVISION,
        "generation_member_count": max(1, member_count),
        "generation_members_sha256": members_sha256,
        "generation_manifest_sha256": manifest_sha256,
        "source_tree_sha256": source_tree_sha256,
        "index_include_patterns": selection["includePatterns"],
        "index_exclude_patterns": selection["excludePatterns"],
        "index_selection_policy_sha256": selection_sha256,
        "point_count": max(1, member_count),
        "collection_target": collection_target,
        "unit_count": unit_count,
        "relation_count": relation_count,
        "snapshot_count": snapshot_count,
        "document_count": int(document_count),
        "skipped_file_count": int(skipped_file_count),
        "snapshot_metadata": normalized_snapshot_metadata,
    }


def write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    if not _SHA256_RE.fullmatch(str(receipt.get("generation_manifest_sha256", ""))):
        raise ValueError("structural generation receipt has an invalid manifest digest")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(_canonical_json(dict(receipt)), encoding="utf-8")
    os.replace(temporary, path)
