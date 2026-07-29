from unittest.mock import MagicMock, patch

from llama_index.core.schema import TextNode
import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from rag_pipeline.core.index_manager.point_operations import PointOperations


def test_architecture_context_uses_zero_vector_without_embedding_request():
    client = MagicMock()
    embed_model = MagicMock()
    embed_model.get_text_embedding_batch.return_value = [[0.1, 0.2, 0.3]]
    operations = PointOperations(
        client,
        embed_model,
        embedding_dim=3,
    )
    chunks = [
        ("code", TextNode(text="class Service {}", metadata={"path": "Service.php"})),
        (
            "architecture",
            TextNode(
                text="Service requests Contract",
                metadata={
                    "path": "__analysis_architecture__/php/fact.context",
                    "architecture_context": True,
                },
            ),
        ),
        (
            "facts",
            TextNode(
                text='{"paths":["Service.php"]}',
                metadata={
                    "path": "__analysis_state__/repository-facts/000000.state",
                    "repository_facts_state": True,
                },
            ),
        ),
    ]

    points = operations.embed_and_create_points(chunks)

    embed_model.get_text_embedding_batch.assert_called_once_with(["class Service {}"])
    assert points[0].vector == [0.1, 0.2, 0.3]
    assert points[1].vector == [0.0, 0.0, 0.0]
    assert points[2].vector == [0.0, 0.0, 0.0]
    assert "_node_content" not in points[2].payload
    assert all(
        len(point.payload["generation_member_sha256"]) == 64
        for point in points
    )


def test_process_streams_embedding_and_pending_writes_in_point_batches():
    client = MagicMock()
    embed_model = MagicMock()
    embed_model.get_text_embedding_batch.side_effect = [
        [[0.1, 0.2, 0.3], [0.2, 0.3, 0.4]],
        [[0.3, 0.4, 0.5]],
    ]
    operations = PointOperations(
        client,
        embed_model,
        embedding_dim=3,
        batch_size=2,
    )
    operations._seal_persisted_point_digests = MagicMock(
        side_effect=lambda _collection, points: [
            (point.id, point.payload["generation_member_sha256"])
            for point in points
        ]
    )
    chunks = [
        TextNode(text=f"chunk-{index}", metadata={"path": "Service.php"})
        for index in range(3)
    ]

    successful, failed = operations.process_and_upsert_chunks(
        chunks,
        "pending",
        "workspace",
        "project",
        "main",
    )

    assert (successful, failed) == (3, 0)
    assert embed_model.get_text_embedding_batch.call_count == 2
    assert [call.kwargs["collection_name"] for call in client.upsert.call_args_list] == [
        "pending",
        "pending",
    ]
    assert [len(call.kwargs["points"]) for call in client.upsert.call_args_list] == [
        2,
        1,
    ]
    assert operations._seal_persisted_point_digests.call_count == 2


def test_process_stops_embedding_after_pending_write_failure():
    client = MagicMock()
    client.upsert.side_effect = RuntimeError("qdrant unavailable")
    embed_model = MagicMock()
    embed_model.get_text_embedding_batch.return_value = [
        [0.1, 0.2, 0.3],
        [0.2, 0.3, 0.4],
    ]
    operations = PointOperations(
        client,
        embed_model,
        embedding_dim=3,
        batch_size=2,
    )
    chunks = [
        TextNode(text=f"chunk-{index}", metadata={"path": "Service.php"})
        for index in range(4)
    ]

    successful, failed = operations.process_and_upsert_chunks(
        chunks,
        "pending",
        "workspace",
        "project",
        "main",
    )

    assert (successful, failed) == (0, 2)
    embed_model.get_text_embedding_batch.assert_called_once()


def test_repository_generation_manifest_seals_exact_written_members():
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name="pending",
        vectors_config=VectorParams(size=2, distance=Distance.COSINE),
    )
    embed_model = MagicMock()
    embed_model.get_text_embedding_batch.return_value = [
        [0.1, 0.2],
        [0.2, 0.3],
    ]
    operations = PointOperations(
        client,
        embed_model,
        embedding_dim=2,
    )
    identity = {
        "plugin_ids": ["php", "magento"],
        "plugin_fingerprint": "sha256:selection",
        "plugin_descriptor_fingerprint": "sha256:descriptor",
        "plugin_implementation_fingerprint": "sha256:implementation",
        "index_representation_fingerprint": "sha256:representation",
    }
    nodes = [
        TextNode(
            text=f"chunk-{index}",
            metadata={
                "workspace": "workspace",
                "project": "project",
                "branch": "main",
                "commit": "a" * 40,
                "path": f"Service{index}.php",
                **identity,
            },
        )
        for index in range(2)
    ]
    expected_members = []
    assert operations.process_and_upsert_chunks(
        nodes,
        "pending",
        "workspace",
        "project",
        "main",
        generation_members=expected_members,
    ) == (2, 0)

    manifest = operations.write_repository_generation_manifest(
        "pending",
        "workspace",
        "project",
        "main",
        "a" * 40,
        expected_member_count=2,
        expected_members=expected_members,
        source_tree_sha256="c" * 64,
        index_include_patterns=["app/code/**"],
        index_exclude_patterns=["vendor/**"],
        identity_metadata=identity,
    )

    assert manifest["generation_member_count"] == 2
    assert len(manifest["generation_members_sha256"]) == 64
    points, _ = client.scroll(
        collection_name="pending",
        with_payload=True,
        with_vectors=False,
        limit=10,
    )
    assert len(points) == 3
    assert sum(
        (point.payload or {}).get("repository_generation_manifest") is True
        for point in points
    ) == 1


@patch(
    "rag_pipeline.core.index_manager.point_operations."
    "collect_generation_members"
)
def test_repository_generation_manifest_rejects_same_count_substitution(
    mock_collect,
):
    expected_members = [("one", "a" * 64), ("two", "b" * 64)]
    mock_collect.return_value = [
        ("one", "a" * 64),
        ("unexpected", "c" * 64),
    ]
    operations = PointOperations(
        MagicMock(),
        MagicMock(),
        embedding_dim=2,
    )

    with pytest.raises(RuntimeError, match="produced point identities"):
        operations.write_repository_generation_manifest(
            "pending",
            "workspace",
            "project",
            "main",
            "a" * 40,
            expected_member_count=2,
            expected_members=expected_members,
            source_tree_sha256="c" * 64,
            index_include_patterns=[],
            index_exclude_patterns=[],
            identity_metadata={},
        )
