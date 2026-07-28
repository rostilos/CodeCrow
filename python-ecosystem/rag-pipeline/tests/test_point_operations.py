from unittest.mock import MagicMock

from llama_index.core.schema import TextNode

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
