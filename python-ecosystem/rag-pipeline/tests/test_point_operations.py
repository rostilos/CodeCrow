import uuid
from unittest.mock import MagicMock

import pytest
from llama_index.core.schema import TextNode
from qdrant_client.models import PointStruct

from rag_pipeline.core.index_manager.point_operations import (
    PointOperations,
    PointWriteInfrastructureError,
)


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
        upsert_max_attempts=1,
        upsert_retry_base_seconds=0,
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


def test_process_fails_on_qdrant_wide_unavailability():
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
        upsert_max_attempts=1,
        upsert_retry_base_seconds=0,
    )
    chunks = [
        TextNode(text=f"chunk-{index}", metadata={"path": "Service.php"})
        for index in range(4)
    ]

    with pytest.raises(PointWriteInfrastructureError):
        operations.process_and_upsert_chunks(
            chunks,
            "pending",
            "workspace",
            "project",
            "main",
        )

    embed_model.get_text_embedding_batch.assert_called_once()


def _points(count: int):
    return [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=[0.1, 0.2, 0.3],
            payload={"path": f"src/chunk-{index}.php"},
        )
        for index in range(count)
    ]


def test_upsert_retries_transient_qdrant_failure_without_losing_points():
    client = MagicMock()
    client.upsert.side_effect = [
        RuntimeError("temporary transport failure"),
        None,
    ]
    operations = PointOperations(
        client,
        MagicMock(),
        batch_size=4,
        upsert_max_attempts=3,
        upsert_retry_base_seconds=0,
    )

    successful, failed = operations.upsert_points("pending", _points(4))

    assert (successful, failed) == (4, 0)
    assert client.upsert.call_count == 2


def test_upsert_splits_rejected_request_until_smaller_slices_succeed():
    class RequestTooLarge(RuntimeError):
        status_code = 413

    client = MagicMock()

    def reject_multi_point_request(**kwargs):
        if len(kwargs["points"]) > 1:
            raise RequestTooLarge("request entity too large")

    client.upsert.side_effect = reject_multi_point_request
    operations = PointOperations(
        client,
        MagicMock(),
        batch_size=4,
        upsert_max_attempts=3,
        upsert_retry_base_seconds=0,
    )

    successful, failed = operations.upsert_points("pending", _points(4))

    assert (successful, failed) == (4, 0)
    assert client.upsert.call_count == 7
    accepted = [
        call.kwargs["points"]
        for call in client.upsert.call_args_list
        if len(call.kwargs["points"]) == 1
    ]
    assert len(accepted) == 4


def test_upsert_isolates_one_rejected_point_without_losing_valid_siblings():
    class InvalidPoint(RuntimeError):
        status_code = 400

    points = _points(4)
    rejected_id = points[2].id
    client = MagicMock()

    def reject_one_point(**kwargs):
        if any(point.id == rejected_id for point in kwargs["points"]):
            raise InvalidPoint("bad request")

    client.upsert.side_effect = reject_one_point
    operations = PointOperations(
        client,
        MagicMock(),
        batch_size=4,
        upsert_retry_base_seconds=0,
    )

    successful, failed = operations.upsert_points("pending", points)

    assert (successful, failed) == (3, 1)
    assert any(
        call.kwargs["points"] == [points[2]]
        for call in client.upsert.call_args_list
    )


def test_upsert_stops_subdivision_when_qdrant_is_globally_unavailable():
    client = MagicMock()
    client.upsert.side_effect = RuntimeError("qdrant unavailable")
    operations = PointOperations(
        client,
        MagicMock(),
        batch_size=4,
        upsert_max_attempts=2,
        upsert_retry_base_seconds=0,
    )

    with pytest.raises(PointWriteInfrastructureError):
        operations.upsert_points("pending", _points(4))

    assert client.upsert.call_count == 2


def test_vector_dimension_mismatch_is_systemic_not_a_skippable_point():
    class DimensionMismatch(RuntimeError):
        status_code = 400

    client = MagicMock()
    client.upsert.side_effect = DimensionMismatch(
        "vector dimension mismatch: expected dimension 4"
    )
    operations = PointOperations(
        client,
        MagicMock(),
        batch_size=4,
        upsert_max_attempts=2,
        upsert_retry_base_seconds=0,
    )

    with pytest.raises(PointWriteInfrastructureError):
        operations.upsert_points("pending", _points(4))

    assert client.upsert.call_count == 2


def test_process_isolates_provider_rejected_embedding_input():
    class InvalidEmbeddingInput(RuntimeError):
        status_code = 400

    client = MagicMock()
    embed_model = MagicMock()

    def embed(texts):
        if "invalid" in texts:
            raise InvalidEmbeddingInput("bad request")
        return [[0.1, 0.2, 0.3] for _ in texts]

    embed_model.get_text_embedding_batch.side_effect = embed
    operations = PointOperations(
        client,
        embed_model,
        embedding_dim=3,
        batch_size=3,
        upsert_retry_base_seconds=0,
    )
    chunks = [
        TextNode(text="valid-one", metadata={"path": "one.php"}),
        TextNode(text="invalid", metadata={"path": "bad.php"}),
        TextNode(text="valid-two", metadata={"path": "two.php"}),
    ]

    successful, failed = operations.process_and_upsert_chunks(
        chunks,
        "pending",
        "workspace",
        "project",
        "main",
    )

    assert (successful, failed) == (2, 1)
    written_paths = {
        point.payload["path"]
        for call in client.upsert.call_args_list
        for point in call.kwargs["points"]
    }
    assert written_paths == {"one.php", "two.php"}
