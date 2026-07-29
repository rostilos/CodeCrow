"""
Point operations for embedding and upserting vectors.

Handles embedding generation, point creation, and batch upsert operations.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Mapping, Tuple

from llama_index.core.schema import TextNode
from qdrant_client import QdrantClient
from qdrant_client.models import (
    PointStruct,
    SetPayload,
    SetPayloadOperation,
    UpdateStatus,
)

from ..generation_manifest import (
    GENERATION_MANIFEST_PAYLOAD_KEY,
    GENERATION_MEMBER_DIGEST_PAYLOAD_KEY,
    build_generation_manifest_node,
    collect_generation_members,
    compute_generation_member_digest,
    compute_generation_members_digest,
)
from ..pr_overlay_manifest import PR_OVERLAY_MANIFEST_PAYLOAD_KEY

logger = logging.getLogger(__name__)


class PointOperations:
    """Handles point embedding and upsert operations."""
    
    def __init__(
        self,
        client: QdrantClient,
        embed_model,
        batch_size: int = 50,
        embedding_dim: int | None = None,
    ):
        self.client = client
        self.embed_model = embed_model
        self.batch_size = batch_size
        self.embedding_dim = embedding_dim
    
    @staticmethod
    def generate_point_id(
        workspace: str,
        project: str,
        branch: str,
        path: str,
        chunk_index: int
    ) -> str:
        """Generate deterministic point ID for upsert (same content = same ID = replace)."""
        key = f"{workspace}:{project}:{branch}:{path}:{chunk_index}"
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))
    
    def prepare_chunks_for_embedding(
        self,
        chunks: List[TextNode],
        workspace: str,
        project: str,
        branch: str
    ) -> List[Tuple[str, TextNode]]:
        """Prepare chunks with deterministic IDs for embedding.
        
        Returns list of (point_id, chunk) tuples.
        """
        # Group chunks by file path
        chunks_by_file: Dict[str, List[TextNode]] = {}
        for chunk in chunks:
            path = chunk.metadata.get("path")
            if not path:
                # Log warning - missing path breaks idempotency but shouldn't happen in normal flow
                logger.warning("Chunk missing 'path' metadata - using non-deterministic ID (breaks idempotency)")
                path = f"__unknown__{uuid.uuid4()}"
            if path not in chunks_by_file:
                chunks_by_file[path] = []
            chunks_by_file[path].append(chunk)
        
        # Assign deterministic IDs
        chunk_data = []
        for path, file_chunks in chunks_by_file.items():
            for chunk_index, chunk in enumerate(file_chunks):
                point_id = self.generate_point_id(workspace, project, branch, path, chunk_index)
                chunk.metadata["indexed_at"] = datetime.now(timezone.utc).isoformat()
                chunk_data.append((point_id, chunk))
        
        return chunk_data
    
    def embed_and_create_points(
        self,
        chunk_data: List[Tuple[str, TextNode]]
    ) -> List[PointStruct]:
        """Embed chunks and create Qdrant points.
        
        Args:
            chunk_data: List of (point_id, chunk) tuples
            
        Returns:
            List of PointStruct ready for upsert
        """
        if not chunk_data:
            return []
        
        # Architecture packets are retrieved only through exact metadata edges.
        # Giving them a zero vector avoids a paid embedding request and keeps
        # them out of similarity ranking without creating a second storage API.
        semantic_chunks = [
            chunk
            for _, chunk in chunk_data
            if not (
                chunk.metadata.get("architecture_context")
                or chunk.metadata.get("architecture_source")
                or chunk.metadata.get("repository_snapshot")
                or chunk.metadata.get("repository_facts_state")
                or chunk.metadata.get(GENERATION_MANIFEST_PAYLOAD_KEY)
                or chunk.metadata.get(PR_OVERLAY_MANIFEST_PAYLOAD_KEY)
            )
        ]
        semantic_embeddings = (
            self.embed_model.get_text_embedding_batch(
                [chunk.text for chunk in semantic_chunks]
            )
            if semantic_chunks
            else []
        )
        embeddings = iter(semantic_embeddings)
        
        # Build points with embeddings
        points = []
        for point_id, chunk in chunk_data:
            if (
                chunk.metadata.get("architecture_context")
                or chunk.metadata.get("architecture_source")
                or chunk.metadata.get("repository_snapshot")
                or chunk.metadata.get("repository_facts_state")
                or chunk.metadata.get(GENERATION_MANIFEST_PAYLOAD_KEY)
                or chunk.metadata.get(PR_OVERLAY_MANIFEST_PAYLOAD_KEY)
            ):
                if not self.embedding_dim:
                    raise RuntimeError(
                        "embedding dimension is required for architecture context storage"
                    )
                embedding = [0.0] * self.embedding_dim
            else:
                embedding = next(embeddings)
            payload = {
                **chunk.metadata,
                "text": chunk.text,
            }
            if not (
                chunk.metadata.get("repository_snapshot")
                or chunk.metadata.get("repository_facts_state")
                or chunk.metadata.get(GENERATION_MANIFEST_PAYLOAD_KEY)
                or chunk.metadata.get(PR_OVERLAY_MANIFEST_PAYLOAD_KEY)
            ):
                payload["_node_content"] = chunk.text
            payload[GENERATION_MEMBER_DIGEST_PAYLOAD_KEY] = (
                compute_generation_member_digest(
                    point_id,
                    payload,
                    embedding,
                )
            )
            points.append(PointStruct(
                id=point_id,
                vector=embedding,
                payload=payload,
            ))
        
        return points
    
    def upsert_points(
        self,
        collection_name: str,
        points: List[PointStruct]
    ) -> Tuple[int, int]:
        """Upsert points to collection in batches.
        
        Returns:
            Tuple of (successful_count, failed_count)
        """
        successful = 0
        failed = 0
        
        for i in range(0, len(points), self.batch_size):
            batch = points[i:i + self.batch_size]
            try:
                self.client.upsert(
                    collection_name=collection_name,
                    points=batch
                )
                successful += len(batch)
            except Exception as e:
                logger.error(f"Failed to upsert batch starting at {i}: {e}")
                failed += len(batch)
        
        return successful, failed

    def _seal_persisted_point_digests(
        self,
        collection_name: str,
        points: List[PointStruct],
    ) -> List[Tuple[object, str]]:
        """Bind member digests to Qdrant's exact persisted payload and vector."""
        if not points:
            return []
        expected_ids = [str(point.id) for point in points]
        records = self.client.retrieve(
            collection_name=collection_name,
            ids=[point.id for point in points],
            with_payload=True,
            with_vectors=True,
        )
        records_by_id = {str(record.id): record for record in records}
        if (
            len(records_by_id) != len(expected_ids)
            or set(records_by_id) != set(expected_ids)
        ):
            raise RuntimeError(
                "Persisted point digest sealing is incomplete: "
                f"expected={len(expected_ids)}, actual={len(records_by_id)}"
            )

        members = []
        operations = []
        for point_id in expected_ids:
            record = records_by_id[point_id]
            payload = record.payload or {}
            digest = compute_generation_member_digest(
                record.id,
                payload,
                record.vector,
            )
            members.append((record.id, digest))
            operations.append(SetPayloadOperation(
                set_payload=SetPayload(
                    payload={
                        GENERATION_MEMBER_DIGEST_PAYLOAD_KEY: digest,
                    },
                    points=[record.id],
                ),
            ))
        results = self.client.batch_update_points(
            collection_name=collection_name,
            update_operations=operations,
            wait=True,
        )
        if (
            len(results) != len(operations)
            or any(result.status != UpdateStatus.COMPLETED for result in results)
        ):
            raise RuntimeError(
                "Persisted point digest update was incomplete: "
                f"expected={len(operations)}, actual={len(results)}"
            )
        return members

    def write_repository_generation_manifest(
        self,
        collection_name: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        *,
        expected_member_count: int,
        expected_members: List[Tuple[object, str]],
        source_tree_sha256: str,
        index_include_patterns: List[str],
        index_exclude_patterns: List[str],
        identity_metadata: Mapping[str, object],
    ) -> dict[str, object]:
        """Seal an exact pending generation after every ordinary point exists."""
        if len(expected_members) != expected_member_count:
            raise RuntimeError(
                "Produced repository generation membership is incomplete: "
                f"expected={expected_member_count}, "
                f"actual={len(expected_members)}"
            )
        expected_members_sha256 = compute_generation_members_digest(
            expected_members
        )
        observed_members = collect_generation_members(
            self.client,
            collection_name,
            branch,
            commit,
        )
        if len(observed_members) != expected_member_count:
            raise RuntimeError(
                "Pending target-branch generation membership is incomplete: "
                f"branch={branch}, expected={expected_member_count}, "
                f"actual={len(observed_members)}"
            )
        observed_members_sha256 = compute_generation_members_digest(
            observed_members
        )
        if observed_members_sha256 != expected_members_sha256:
            raise RuntimeError(
                "Pending target-branch generation membership does not match "
                "the produced point identities"
            )
        node = build_generation_manifest_node(
            workspace=workspace,
            project=project,
            branch=branch,
            commit=commit,
            member_count=len(expected_members),
            members_sha256=expected_members_sha256,
            source_tree_sha256=source_tree_sha256,
            index_include_patterns=index_include_patterns,
            index_exclude_patterns=index_exclude_patterns,
            identity_metadata=identity_metadata,
        )
        successful, failed = self.process_and_upsert_chunks(
            [node],
            collection_name,
            workspace,
            project,
            branch,
        )
        if failed or successful != 1:
            raise RuntimeError(
                "Repository generation manifest write was incomplete: "
                f"successful={successful}, failed={failed}"
            )
        return {
            "generation_schema": node.metadata["generation_schema"],
            "generation_member_count": len(expected_members),
            "generation_members_sha256": expected_members_sha256,
            "generation_manifest_sha256": (
                node.metadata["generation_manifest_sha256"]
            ),
            "source_tree_sha256": source_tree_sha256,
            "index_include_patterns": node.metadata[
                "index_include_patterns"
            ],
            "index_exclude_patterns": node.metadata[
                "index_exclude_patterns"
            ],
            "index_selection_policy_sha256": node.metadata[
                "index_selection_policy_sha256"
            ],
        }
    
    def process_and_upsert_chunks(
        self,
        chunks: List[TextNode],
        collection_name: str,
        workspace: str,
        project: str,
        branch: str,
        *,
        generation_members: List[Tuple[object, str]] | None = None,
    ) -> Tuple[int, int]:
        """Full pipeline: prepare, embed, and upsert chunks.
        
        Returns:
            Tuple of (successful_count, failed_count)
        """
        # Prepare chunks with IDs
        chunk_data = self.prepare_chunks_for_embedding(
            chunks, workspace, project, branch
        )
        
        successful = 0
        failed = 0

        # Embed and write bounded point slices. The collection is still pending
        # and cannot become active until the index manager verifies the complete
        # work count, but operators can now observe steady progress instead of
        # waiting for every chunk from a 50-file document batch to finish.
        for i in range(0, len(chunk_data), self.batch_size):
            point_batch = self.embed_and_create_points(
                chunk_data[i:i + self.batch_size]
            )
            batch_successful, batch_failed = self.upsert_points(
                collection_name,
                point_batch,
            )
            successful += batch_successful
            failed += batch_failed
            if batch_failed:
                break
            sealed_members = self._seal_persisted_point_digests(
                collection_name,
                point_batch,
            )
            if generation_members is not None:
                generation_members.extend(sealed_members)

        return successful, failed
