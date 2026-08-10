from types import SimpleNamespace

import pytest
from llama_index.core.schema import TextNode
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from rag_pipeline.core.index_manager.indexer import FileOperations
from rag_pipeline.core.index_manager.point_operations import PointOperations
from rag_pipeline.core.pr_overlay_manifest import read_pr_overlay_generation
from rag_pipeline.core.repository_overlay import (
    IncrementalIndexPreconditionError,
)


SOURCE_REVISION = "a" * 40
BASE_REVISION = "b" * 40
BASE_MANIFEST = "c" * 64
GENERATION_FINGERPRINT = "sha256:" + "d" * 64
OVERLAY_REPRESENTATION = "sha256:" + "e" * 64
NEXT_SOURCE_REVISION = "1" * 40
NEXT_GENERATION_FINGERPRINT = "sha256:" + "2" * 64


def _operations():
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name="overlay",
        vectors_config=VectorParams(size=2, distance=Distance.COSINE),
    )
    embed_model = SimpleNamespace(
        get_text_embedding_batch=lambda texts: [
            [0.1, 0.2] for _ in texts
        ],
    )
    point_operations = PointOperations(
        client,
        embed_model,
        embedding_dim=2,
    )
    file_operations = FileOperations(
        client,
        point_operations,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    return client, file_operations


def _node(
    text="final class Service {}",
    *,
    source_revision=SOURCE_REVISION,
    generation_fingerprint=GENERATION_FINGERPRINT,
):
    return TextNode(
        text=text,
        metadata={
            "workspace": "workspace",
            "project": "project",
            "branch": "main",
            "path": "app/code/Service.php",
            "pr": True,
            "pr_number": 42,
            "pr_branch": "main",
            "pr_source_revision": source_revision,
            "pr_base_revision": BASE_REVISION,
            "pr_base_generation_manifest_sha256": BASE_MANIFEST,
            "pr_generation_fingerprint": generation_fingerprint,
            "pr_overlay_representation_fingerprint": (
                OVERLAY_REPRESENTATION
            ),
        },
    )


def _read(
    client,
    manifest_sha256=None,
    *,
    source_revision=SOURCE_REVISION,
    generation_fingerprint=GENERATION_FINGERPRINT,
):
    return read_pr_overlay_generation(
        client,
        "overlay",
        workspace="workspace",
        project="project",
        pr_number=42,
        branch="main",
        base_branch="main",
        source_revision=source_revision,
        base_revision=BASE_REVISION,
        base_generation_manifest_sha256=BASE_MANIFEST,
        generation_fingerprint=generation_fingerprint,
        overlay_representation_fingerprint=OVERLAY_REPRESENTATION,
        expected_manifest_sha256=manifest_sha256,
    )


def test_pr_overlay_seal_binds_exact_persisted_membership():
    client, operations = _operations()

    count, receipt = operations.replace_pr_overlay_generation(
        [_node()],
        [],
        "overlay",
        "workspace",
        "project",
        "__pr__/42/main",
        pr_number=42,
        branch="main",
        base_branch="main",
        source_revision=SOURCE_REVISION,
        base_revision=BASE_REVISION,
        base_generation_manifest_sha256=BASE_MANIFEST,
        generation_fingerprint=GENERATION_FINGERPRINT,
        overlay_representation_fingerprint=OVERLAY_REPRESENTATION,
        identity_metadata={
            "index_representation_fingerprint": "sha256:" + "f" * 64,
        },
    )

    assert count == 1
    assert receipt["overlay_generation_member_count"] == 1
    assert len(receipt["overlay_generation_manifest_sha256"]) == 64
    assert _read(
        client,
        receipt["overlay_generation_manifest_sha256"],
    ) == receipt


def test_pr_overlay_rejects_tampered_member_with_same_count():
    client, operations = _operations()
    _, receipt = operations.replace_pr_overlay_generation(
        [_node()],
        [],
        "overlay",
        "workspace",
        "project",
        "__pr__/42/main",
        pr_number=42,
        branch="main",
        base_branch="main",
        source_revision=SOURCE_REVISION,
        base_revision=BASE_REVISION,
        base_generation_manifest_sha256=BASE_MANIFEST,
        generation_fingerprint=GENERATION_FINGERPRINT,
        overlay_representation_fingerprint=OVERLAY_REPRESENTATION,
        identity_metadata={},
    )
    points, _ = client.scroll(
        collection_name="overlay",
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    member = next(
        point
        for point in points
        if not (point.payload or {}).get("pr_overlay_generation_manifest")
    )
    client.set_payload(
        collection_name="overlay",
        payload={"text": "tampered"},
        points=[member.id],
    )

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="member integrity",
    ):
        _read(client, receipt["overlay_generation_manifest_sha256"])


def test_pr_overlay_without_unique_manifest_is_not_reusable():
    client, operations = _operations()
    chunk_data = operations.point_ops.prepare_chunks_for_embedding(
        [_node()],
        "workspace",
        "project",
        "__pr__/42/main",
    )
    points = operations.point_ops.embed_and_create_points(chunk_data)
    operations.point_ops.upsert_points("overlay", points)
    operations.point_ops._seal_persisted_point_digests("overlay", points)

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="manifest is missing",
    ):
        _read(client)


def test_failed_new_generation_never_overwrites_or_hides_prior_lease():
    client, operations = _operations()
    _, prior_receipt = operations.replace_pr_overlay_generation(
        [_node()],
        [],
        "overlay",
        "workspace",
        "project",
        f"__pr__/42/main/{GENERATION_FINGERPRINT}",
        pr_number=42,
        branch="main",
        base_branch="main",
        source_revision=SOURCE_REVISION,
        base_revision=BASE_REVISION,
        base_generation_manifest_sha256=BASE_MANIFEST,
        generation_fingerprint=GENERATION_FINGERPRINT,
        overlay_representation_fingerprint=OVERLAY_REPRESENTATION,
        identity_metadata={},
    )
    prior_points, _ = client.scroll(
        collection_name="overlay",
        limit=20,
        with_payload=True,
        with_vectors=True,
    )

    original_upsert = operations.point_ops.upsert_points
    observed_prior_receipts = []

    def fail_new_manifest(collection_name, points):
        if any(
            (point.payload or {}).get("pr_overlay_generation_manifest")
            for point in points
        ):
            # The new generation's members are already persisted here. An
            # exact reader of the prior lease must still see only prior IDs.
            observed_prior_receipts.append(
                _read(
                    client,
                    prior_receipt[
                        "overlay_generation_manifest_sha256"
                    ],
                )
            )
            return 0, len(points)
        return original_upsert(collection_name, points)

    operations.point_ops.upsert_points = fail_new_manifest
    with pytest.raises(RuntimeError, match="manifest write was incomplete"):
        operations.replace_pr_overlay_generation(
            [
                _node(
                    "final class ServiceV2 {}",
                    source_revision=NEXT_SOURCE_REVISION,
                    generation_fingerprint=NEXT_GENERATION_FINGERPRINT,
                ),
            ],
            prior_points,
            "overlay",
            "workspace",
            "project",
            f"__pr__/42/main/{NEXT_GENERATION_FINGERPRINT}",
            pr_number=42,
            branch="main",
            base_branch="main",
            source_revision=NEXT_SOURCE_REVISION,
            base_revision=BASE_REVISION,
            base_generation_manifest_sha256=BASE_MANIFEST,
            generation_fingerprint=NEXT_GENERATION_FINGERPRINT,
            overlay_representation_fingerprint=OVERLAY_REPRESENTATION,
            identity_metadata={},
        )

    assert observed_prior_receipts == [prior_receipt]
    assert _read(
        client,
        prior_receipt["overlay_generation_manifest_sha256"],
    ) == prior_receipt
