import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    PointVectors,
    VectorParams,
)

from rag_pipeline.core.generation_manifest import (
    GENERATION_SCHEMA,
    GenerationManifestError,
    build_generation_manifest_node,
    compute_index_selection_policy_sha256,
    collect_generation_members,
    compute_generation_member_digest,
    compute_generation_members_digest,
    seal_generation_members,
)
from rag_pipeline.core.index_representation import (
    INDEX_REPRESENTATION_PAYLOAD_KEY,
)
from rag_pipeline.core.index_manager.manager import RAGIndexManager
from rag_pipeline.core.repository_overlay import IncrementalIndexPreconditionError
from rag_pipeline.core.revision_preflight import (
    read_repository_revision_preflight,
)


COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
SOURCE_TREE_SHA256 = "c" * 64
INDEX_INCLUDE_PATTERNS = ["app/code/**"]
INDEX_EXCLUDE_PATTERNS = ["**/Test/**", "vendor/**"]
INDEX_SELECTION_POLICY_SHA256 = compute_index_selection_policy_sha256(
    INDEX_INCLUDE_PATTERNS,
    INDEX_EXCLUDE_PATTERNS,
)


def _facts_content(commit=COMMIT):
    return json.dumps(
        {
            "revision": commit,
            "paths": ["app/code/Acme/Module/Model/Example.php"],
            "markerContents": {"composer.json": '{"require":{"magento/framework":"*"}}'},
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _identity_payload(**overrides):
    payload = {
        "branch": "main",
        "commit": COMMIT,
        "plugin_ids": ["php", "magento"],
        "plugin_fingerprint": "sha256:selection",
        "plugin_descriptor_fingerprint": "sha256:descriptor",
        "plugin_implementation_fingerprint": "sha256:implementation",
        INDEX_REPRESENTATION_PAYLOAD_KEY: "sha256:representation",
    }
    payload.update(overrides)
    return payload


def _client_with_points(*payloads):
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name="repository",
        vectors_config=VectorParams(size=2, distance=Distance.COSINE),
    )
    client.upsert(
        collection_name="repository",
        points=[
            PointStruct(id=index + 1, vector=[0.0, 0.0], payload=payload)
            for index, payload in enumerate(payloads)
        ],
    )
    return client


def _complete_revision_payloads():
    content = _facts_content()
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return _sealed_payloads(
        _identity_payload(
            path="app/code/Acme/Module/Model/Example.php",
            text="<?php class Example {}",
        ),
        _identity_payload(
            path="__analysis_state__/repository-facts/000000.state",
            repository_facts_state=True,
            facts_part=0,
            facts_parts=1,
            facts_content_sha256=digest,
            text=content,
        ),
    )


def _sealed_payloads(*payloads):
    member_payloads = []
    members = []
    for point_id, payload in enumerate(payloads, start=1):
        sealed_payload = dict(payload)
        sealed_payload["generation_member_sha256"] = (
            compute_generation_member_digest(
                point_id,
                sealed_payload,
                [0.0, 0.0],
            )
        )
        member_payloads.append(sealed_payload)
        members.append((
            point_id,
            sealed_payload["generation_member_sha256"],
        ))
    members_sha256 = compute_generation_members_digest(members)
    node = build_generation_manifest_node(
        workspace="workspace",
        project="project",
        branch="main",
        commit=COMMIT,
        member_count=len(member_payloads),
        members_sha256=members_sha256,
        source_tree_sha256=SOURCE_TREE_SHA256,
        index_include_patterns=INDEX_INCLUDE_PATTERNS,
        index_exclude_patterns=INDEX_EXCLUDE_PATTERNS,
        identity_metadata={
            key: value
            for key, value in _identity_payload().items()
            if key in {
                "plugin_ids",
                "plugin_fingerprint",
                "plugin_descriptor_fingerprint",
                "plugin_implementation_fingerprint",
                INDEX_REPRESENTATION_PAYLOAD_KEY,
            }
        },
    )
    manifest_payload = {**node.metadata, "text": node.text}
    manifest_payload["generation_member_sha256"] = (
        compute_generation_member_digest(
            len(member_payloads) + 1,
            manifest_payload,
            [0.0, 0.0],
        )
    )
    return (*member_payloads, manifest_payload)


def test_exact_revision_preflight_returns_verified_state_identity():
    client = _client_with_points(*_complete_revision_payloads())

    result = read_repository_revision_preflight(
        client,
        "repository",
        "main",
        COMMIT,
    )

    assert result == {
        "workspace": "workspace",
        "project": "project",
        "branch": "main",
        "commit": COMMIT,
        "point_count": 3,
        "repository_revision": COMMIT,
        "repository_facts_sha256": hashlib.sha256(
            _facts_content().encode("utf-8")
        ).hexdigest(),
        "plugin_ids": ["php", "magento"],
        "plugin_fingerprint": "sha256:selection",
        "plugin_descriptor_fingerprint": "sha256:descriptor",
        "plugin_implementation_fingerprint": "sha256:implementation",
        "index_representation_fingerprint": "sha256:representation",
        "generation_schema": GENERATION_SCHEMA,
        "generation_member_count": 2,
        "generation_members_sha256": compute_generation_members_digest([
            (index + 1, payload["generation_member_sha256"])
            for index, payload in enumerate(
                _complete_revision_payloads()[:-1]
            )
        ]),
        "generation_manifest_sha256": (
            _complete_revision_payloads()[-1]["generation_manifest_sha256"]
        ),
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "index_include_patterns": INDEX_INCLUDE_PATTERNS,
        "index_exclude_patterns": INDEX_EXCLUDE_PATTERNS,
        "index_selection_policy_sha256": INDEX_SELECTION_POLICY_SHA256,
    }
    assert read_repository_revision_preflight(
        client,
        "repository",
        "main",
        OTHER_COMMIT,
    ) is None


def test_exact_revision_preflight_rejects_incomplete_repository_state():
    content = _facts_content()
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    client = _client_with_points(*_sealed_payloads(
        _identity_payload(path="Example.php", text="<?php"),
        _identity_payload(
            repository_facts_state=True,
            facts_part=0,
            facts_parts=2,
            facts_content_sha256=digest,
            text=content,
        ),
    ))

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="incomplete",
    ):
        read_repository_revision_preflight(
            client,
            "repository",
            "main",
            COMMIT,
        )


def test_exact_revision_preflight_rejects_mixed_plugin_identity():
    state_payloads = list(_complete_revision_payloads())
    state_payloads[0] = {
        **state_payloads[0],
        "plugin_implementation_fingerprint": "sha256:different",
    }
    client = _client_with_points(*state_payloads)

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="inconsistent repository build identity",
    ):
        read_repository_revision_preflight(
            client,
            "repository",
            "main",
            COMMIT,
        )


def test_exact_revision_preflight_rejects_deleted_ordinary_point():
    client = _client_with_points(*_complete_revision_payloads())
    client.delete(
        collection_name="repository",
        points_selector=[1],
    )

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="generation is incomplete",
    ):
        read_repository_revision_preflight(
            client,
            "repository",
            "main",
            COMMIT,
        )


def test_exact_revision_preflight_rejects_tampered_source_tree_receipt():
    payloads = list(_complete_revision_payloads())
    payloads[-1] = {
        **payloads[-1],
        "source_tree_sha256": "f" * 64,
    }
    client = _client_with_points(*payloads)

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="manifest failed integrity",
    ):
        read_repository_revision_preflight(
            client,
            "repository",
            "main",
            COMMIT,
        )


def test_exact_revision_preflight_rejects_tampered_selection_policy():
    payloads = list(_complete_revision_payloads())
    payloads[-1] = {
        **payloads[-1],
        "index_exclude_patterns": ["generated/**"],
    }
    client = _client_with_points(*payloads)

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="selection policy failed integrity",
    ):
        read_repository_revision_preflight(
            client,
            "repository",
            "main",
            COMMIT,
        )


def test_exact_revision_preflight_rejects_extra_ordinary_point():
    payload = _identity_payload(path="unexpected.php", text="<?php")
    payload["generation_member_sha256"] = compute_generation_member_digest(
        4,
        payload,
        [0.0, 0.0],
    )
    client = _client_with_points(*_complete_revision_payloads(), payload)

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="generation is incomplete",
    ):
        read_repository_revision_preflight(
            client,
            "repository",
            "main",
            COMMIT,
        )


def test_exact_revision_preflight_rejects_substituted_member_identity():
    payloads = list(_complete_revision_payloads())
    payloads[0] = {
        **payloads[0],
        "generation_member_sha256": "f" * 64,
    }
    client = _client_with_points(*payloads)

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="member content failed integrity",
    ):
        read_repository_revision_preflight(
            client,
            "repository",
            "main",
            COMMIT,
        )


def test_exact_revision_preflight_rejects_payload_substitution_with_stale_digest():
    client = _client_with_points(*_complete_revision_payloads())
    client.set_payload(
        collection_name="repository",
        points=[1],
        payload={"text": "<?php tampered();"},
    )

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="member content failed integrity",
    ):
        read_repository_revision_preflight(
            client,
            "repository",
            "main",
            COMMIT,
        )


def test_exact_revision_preflight_rejects_vector_substitution_with_stale_digest():
    client = _client_with_points(*_complete_revision_payloads())
    client.update_vectors(
        collection_name="repository",
        points=[
            PointVectors(id=1, vector=[1.0, 1.0]),
        ],
    )

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="member content failed integrity",
    ):
        read_repository_revision_preflight(
            client,
            "repository",
            "main",
            COMMIT,
        )


def test_pending_generation_seal_rejects_payload_substitution_with_stale_digest():
    client = _client_with_points(*_complete_revision_payloads()[:-1])
    client.set_payload(
        collection_name="repository",
        points=[1],
        payload={"text": "<?php tampered();"},
    )

    with pytest.raises(GenerationManifestError, match="content digest"):
        collect_generation_members(
            client,
            "repository",
            "main",
            COMMIT,
        )


def test_pending_generation_seal_rejects_vector_substitution_with_stale_digest():
    client = _client_with_points(*_complete_revision_payloads()[:-1])
    client.update_vectors(
        collection_name="repository",
        points=[
            PointVectors(id=1, vector=[1.0, 1.0]),
        ],
    )

    with pytest.raises(GenerationManifestError, match="content digest"):
        collect_generation_members(
            client,
            "repository",
            "main",
            COMMIT,
        )


def test_pending_generation_seal_binds_digest_to_qdrant_normalized_vector():
    payload = _identity_payload(path="Example.php", text="<?php")
    # This is how the full indexer initially constructs a point: before Qdrant
    # applies COSINE normalization.  The persisted vector therefore differs.
    payload["generation_member_sha256"] = compute_generation_member_digest(
        1, payload, [3.0, 4.0]
    )
    client = _client_with_points(payload)
    client.update_vectors(
        collection_name="repository",
        points=[PointVectors(id=1, vector=[3.0, 4.0])],
    )

    assert seal_generation_members(client, "repository", "main", COMMIT) == 1
    assert len(collect_generation_members(client, "repository", "main", COMMIT)) == 1


def test_exact_revision_preflight_rejects_mixed_branch_revisions():
    mixed_payload = _identity_payload(
        commit=OTHER_COMMIT,
        path="stale.php",
        text="<?php",
    )
    client = _client_with_points(
        *_complete_revision_payloads(),
        mixed_payload,
    )

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="mixed repository revisions",
    ):
        read_repository_revision_preflight(
            client,
            "repository",
            "main",
            COMMIT,
        )


def test_exact_revision_preflight_rejects_legacy_unsealed_revision():
    client = _client_with_points(
        _identity_payload(path="Example.php", text="<?php"),
    )

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="generation manifest is missing",
    ):
        read_repository_revision_preflight(
            client,
            "repository",
            "main",
            COMMIT,
        )


@patch(
    "rag_pipeline.core.revision_preflight.read_repository_revision_preflight"
)
def test_index_manager_publishes_coordinates_after_build_compatibility_check(
    mock_read,
):
    manager = object.__new__(RAGIndexManager)
    manager.config = SimpleNamespace(qdrant_collection_prefix="code")
    manager.qdrant_client = MagicMock()
    manager._collection_manager = MagicMock()
    manager._collection_manager.collection_exists.return_value = True
    manager._collection_manager.resolve_collection_target.return_value = (
        "code_workspace__project_active"
    )
    manager.index_representation_fingerprint = "sha256:representation"
    manager.plugin_catalog = MagicMock()
    manager.plugin_catalog.registry.fingerprint_for.return_value = (
        "sha256:descriptor"
    )
    manager.plugin_catalog.implementation_fingerprint.return_value = (
        "sha256:implementation"
    )
    mock_read.return_value = {
        "workspace": "workspace",
        "project": "project",
        "branch": "main",
        "commit": COMMIT,
        "point_count": 2,
        "repository_revision": COMMIT,
        "repository_facts_sha256": "c" * 64,
        "plugin_ids": ["php", "magento"],
        "plugin_fingerprint": "sha256:selection",
        "plugin_descriptor_fingerprint": "sha256:descriptor",
        "plugin_implementation_fingerprint": "sha256:implementation",
        "index_representation_fingerprint": "sha256:representation",
        "generation_schema": GENERATION_SCHEMA,
        "generation_member_count": 1,
        "generation_members_sha256": "d" * 64,
        "generation_manifest_sha256": "e" * 64,
    }

    result = manager.get_revision_preflight(
        "workspace",
        "project",
        "main",
        COMMIT,
    )

    assert result["workspace"] == "workspace"
    assert result["project"] == "project"
    assert result["point_count"] == 2
    mock_read.assert_called_once_with(
        manager.qdrant_client,
        "code_workspace__project_active",
        "main",
        COMMIT,
    )


@patch(
    "rag_pipeline.core.revision_preflight.read_repository_revision_preflight"
)
def test_index_manager_rejects_generation_from_different_project(mock_read):
    manager = object.__new__(RAGIndexManager)
    manager.config = SimpleNamespace(qdrant_collection_prefix="code")
    manager.qdrant_client = MagicMock()
    manager._collection_manager = MagicMock()
    manager._collection_manager.resolve_collection_target.return_value = (
        "code_workspace__project_active"
    )
    manager.index_representation_fingerprint = "sha256:representation"
    manager.plugin_catalog = None
    mock_read.return_value = {
        "workspace": "different-workspace",
        "project": "different-project",
        "index_representation_fingerprint": "sha256:representation",
    }

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="coordinates",
    ):
        manager.get_revision_preflight(
            "workspace",
            "project",
            "main",
            COMMIT,
        )


@patch(
    "rag_pipeline.core.revision_preflight.read_repository_revision_preflight"
)
def test_index_manager_retains_stale_representation_as_provenance(mock_read):
    manager = object.__new__(RAGIndexManager)
    manager.config = SimpleNamespace(qdrant_collection_prefix="code")
    manager.qdrant_client = MagicMock()
    manager._collection_manager = MagicMock()
    manager._collection_manager.collection_exists.return_value = True
    manager._collection_manager.resolve_collection_target.return_value = (
        "code_workspace__project_active"
    )
    manager.index_representation_fingerprint = "sha256:current"
    manager.plugin_catalog = None
    mock_read.return_value = {
        "workspace": "workspace",
        "project": "project",
        "index_representation_fingerprint": "sha256:stale",
    }

    result = manager.get_revision_preflight(
        "workspace",
        "project",
        "main",
        COMMIT,
    )

    assert result["index_representation_fingerprint"] == "sha256:stale"
