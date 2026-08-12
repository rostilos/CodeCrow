from types import SimpleNamespace
from unittest.mock import MagicMock
import hashlib
import json
import pytest

from llama_index.core.schema import TextNode
from qdrant_client import QdrantClient
from qdrant_client.models import (
    CreateAlias,
    CreateAliasOperation,
    Distance,
    VectorParams,
)

from rag_pipeline.core.generation_manifest import (
    build_generation_manifest_node,
    collect_generation_members,
    compute_generation_members_digest,
    generation_manifest_point_id,
)
from rag_pipeline.core.index_manager.collection_manager import CollectionManager
from rag_pipeline.core.index_manager.manager import RAGIndexManager
from rag_pipeline.core.index_manager.point_operations import PointOperations
from rag_pipeline.core.index_manager.stats_manager import StatsManager
from rag_pipeline.core.revision_preflight import read_repository_revision_preflight
from rag_pipeline.core.repository_overlay import IncrementalIndexPreconditionError
from rag_pipeline.core.source_tree import compute_repository_source_tree_sha256


SOURCE_COMMIT = "a" * 40
TARGET_COMMIT = "b" * 40
SOURCE_TREE = "c" * 64


class _Embedding:
    def get_text_embedding_batch(self, texts):
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


class _Lease:
    token = "d" * 32

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def assert_owned(self):
        return None


class _Coordinator:
    def __init__(self):
        self.calls = []

    def acquire(self, *_args, **kwargs):
        self.calls.append((tuple(_args), kwargs))
        return _Lease()


def _sealed_source(client, point_ops, collection):
    identity = {
        "plugin_ids": [],
        "plugin_fingerprint": "sha256:" + "1" * 64,
        "plugin_descriptor_fingerprint": "sha256:" + "2" * 64,
        "plugin_implementation_fingerprint": "sha256:" + "3" * 64,
        "index_representation_fingerprint": "sha256:" + "4" * 64,
    }
    facts = json.dumps(
        {
            "revision": SOURCE_COMMIT,
            "paths": ["src/A.java", "src/B.java"],
            "markerContents": {},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    nodes = [
        TextNode(text="alpha", metadata={
            "workspace": "ws", "project": "project",
            "path": "src/A.java", "branch": "develop",
            "commit": SOURCE_COMMIT, **identity,
        }),
        TextNode(text="beta", metadata={
            "workspace": "ws", "project": "project",
            "path": "src/B.java", "branch": "develop",
            "commit": SOURCE_COMMIT, **identity,
        }),
        TextNode(text=facts, metadata={
            "workspace": "ws", "project": "project",
            "path": "__analysis_state__/repository-facts/000000.state",
            "branch": "develop", "commit": SOURCE_COMMIT,
            "repository_facts_state": True,
            "facts_part": 0,
            "facts_parts": 1,
            "facts_content_sha256": hashlib.sha256(
                facts.encode("utf-8")
            ).hexdigest(),
            **identity,
        }),
    ]
    assert point_ops.process_and_upsert_chunks(
        nodes, collection, "ws", "project", "develop"
    ) == (3, 0)
    members = collect_generation_members(
        client, collection, "develop", SOURCE_COMMIT
    )
    manifest = build_generation_manifest_node(
        workspace="ws",
        project="project",
        branch="develop",
        commit=SOURCE_COMMIT,
        member_count=len(members),
        members_sha256=compute_generation_members_digest(members),
        source_tree_sha256=SOURCE_TREE,
        index_include_patterns=(),
        index_exclude_patterns=(),
        identity_metadata=identity,
    )
    assert point_ops.process_and_upsert_chunks(
        [manifest], collection, "ws", "project", "develop"
    ) == (1, 0)


def _manifest_digest(client, collection):
    records = client.retrieve(
        collection_name=collection,
        ids=[generation_manifest_point_id("ws", "project", "develop")],
        with_payload=True,
        with_vectors=False,
    )
    return records[0].payload["generation_manifest_sha256"]


def test_copy_on_write_advance_keeps_source_and_seals_target_revision(tmp_path):
    client = QdrantClient(":memory:")
    source_physical = "develop_source_physical"
    source_alias = "develop_source"
    target_alias = "develop_target"
    client.create_collection(
        collection_name=source_physical,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    client.update_collection_aliases(change_aliases_operations=[
        CreateAliasOperation(create_alias=CreateAlias(
            alias_name=source_alias,
            collection_name=source_physical,
        ))
    ])
    point_ops = PointOperations(
        client, _Embedding(), batch_size=50, embedding_dim=4
    )
    _sealed_source(client, point_ops, source_physical)

    manager = object.__new__(RAGIndexManager)
    manager.qdrant_client = client
    manager._collection_manager = CollectionManager(client, 4)
    manager._mutation_coordinator = _Coordinator()
    manager._file_ops = MagicMock()
    manager._point_ops = point_ops
    manager._stats_manager = StatsManager(client, "rag")
    manager.config = SimpleNamespace(qdrant_collection_prefix="rag")
    target_tree = compute_repository_source_tree_sha256(tmp_path)

    result = manager.advance_generation(
        source_collection_target=source_alias,
        target_collection_target=target_alias,
        source_commit=SOURCE_COMMIT,
        source_tree_sha256=target_tree,
        updated_file_paths=[],
        deleted_file_paths=[],
        repo_base=str(tmp_path),
        workspace="ws",
        project="project",
        branch="develop",
        commit=TARGET_COMMIT,
        publish_branch_alias=True,
    )

    source = read_repository_revision_preflight(
        client, source_alias, "develop", SOURCE_COMMIT
    )
    target = read_repository_revision_preflight(
        client, target_alias, "develop", TARGET_COMMIT
    )
    assert source is not None
    assert target is not None
    assert target["generation_manifest_sha256"] == (
        result.generation_manifest_sha256
    )
    assert target["generation_manifest_sha256"] != (
        source["generation_manifest_sha256"]
    )
    assert result.collection_target == target_alias
    aliases = {
        alias.alias_name: alias.collection_name
        for alias in client.get_aliases().aliases
    }
    assert aliases["rag_ws__project__develop"] == aliases[target_alias]
    manager._file_ops.apply_changes.assert_called_once()
    assert manager._mutation_coordinator.calls[-1][1]["publication_scope"] == (
        "branch-head:develop"
    )


def test_advance_is_idempotent_when_exact_target_already_exists():
    manager = object.__new__(RAGIndexManager)
    manager._mutation_coordinator = _Coordinator()
    manager._collection_manager = MagicMock()
    manager._collection_manager.resolve_collection_target.side_effect = [
        "source-physical", "target-physical"
    ]
    manager.qdrant_client = MagicMock()
    manager._stats_manager = MagicMock()
    expected = SimpleNamespace(generation_manifest_sha256="e" * 64)
    manager._stats_manager.get_branch_stats.return_value = expected
    manager.get_revision_preflight = MagicMock(return_value={
        "generation_manifest_sha256": "e" * 64,
        "source_tree_sha256": SOURCE_TREE,
    })

    result = manager.advance_generation(
        "source", "target", SOURCE_COMMIT, SOURCE_TREE, [], [], None,
        "ws", "project", "develop", TARGET_COMMIT,
    )

    assert result is expected
    manager._collection_manager.create_pending_collection.assert_not_called()


def test_existing_target_is_not_published_when_tenant_preflight_rejects_it():
    manager = object.__new__(RAGIndexManager)
    manager._mutation_coordinator = _Coordinator()
    manager._collection_manager = MagicMock()
    manager._collection_manager.resolve_collection_target.side_effect = [
        "source-physical", "foreign-target-physical"
    ]
    manager.qdrant_client = MagicMock()
    manager._stats_manager = MagicMock()
    manager.config = SimpleNamespace(qdrant_collection_prefix="rag")
    manager.get_revision_preflight = MagicMock(side_effect=[
        {"generation_manifest_sha256": "1" * 64},
        IncrementalIndexPreconditionError(
            "repository generation coordinates do not match the requested tenant"
        ),
    ])

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="coordinates do not match",
    ):
        manager.advance_generation(
            "source", "foreign-target", SOURCE_COMMIT, SOURCE_TREE,
            [], [], None, "ws", "project", "develop", TARGET_COMMIT,
            publish_branch_alias=True,
        )

    manager._collection_manager.atomic_assign_aliases.assert_not_called()


def test_exact_generation_delete_verifies_tenant_coordinates():
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name="foreign_generation",
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    point_ops = PointOperations(client, _Embedding(), embedding_dim=4)
    point_ops.process_and_upsert_chunks(
        [TextNode(text="secret", metadata={
            "workspace": "other-ws",
            "project": "other-project",
            "branch": "develop",
            "path": "Secret.java",
        })],
        "foreign_generation",
        "other-ws",
        "other-project",
        "develop",
    )
    manager = object.__new__(RAGIndexManager)
    manager.qdrant_client = client
    manager._collection_manager = CollectionManager(client, 4)
    manager._mutation_coordinator = _Coordinator()

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="does not match the registry generation receipt",
    ):
        manager.delete_collection_target(
            "ws",
            "project",
            "develop",
            "foreign_generation",
            SOURCE_COMMIT,
            "1" * 64,
        )

    assert manager._collection_manager.collection_exists("foreign_generation")


def test_exact_generation_delete_allows_tenant_owned_pr_overlay_points():
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name="review_generation",
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    point_ops = PointOperations(client, _Embedding(), embedding_dim=4)
    _sealed_source(client, point_ops, "review_generation")
    point_ops.process_and_upsert_chunks(
        [TextNode(text="pull request change", metadata={
            "workspace": "ws",
            "project": "project",
            "branch": "feature/review",
            "path": "src/Changed.java",
            "pr": True,
            "pr_number": 42,
        })],
        "review_generation",
        "ws",
        "project",
        "feature/review",
    )
    manifest_digest = _manifest_digest(client, "review_generation")
    client.scroll = MagicMock(
        side_effect=AssertionError("exact generation deletion must remain O(1)")
    )
    manager = object.__new__(RAGIndexManager)
    manager.qdrant_client = client
    manager._collection_manager = CollectionManager(client, 4)
    manager._mutation_coordinator = _Coordinator()

    assert manager.delete_collection_target(
        "ws",
        "project",
        "develop",
        "review_generation",
        SOURCE_COMMIT,
        manifest_digest,
    ) is True
    assert not manager._collection_manager.collection_exists("review_generation")
