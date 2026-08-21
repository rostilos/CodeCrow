"""
Point operations for embedding and upserting vectors.

Handles embedding generation, point creation, and batch upsert operations.
"""

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Dict, Optional, Tuple

from llama_index.core.schema import TextNode
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from ..generation_manifest import (
    GENERATION_MEMBER_DIGEST_PAYLOAD_KEY,
    compute_generation_member_digest,
)

logger = logging.getLogger(__name__)

EMBEDDING_INPUT_HASH_PAYLOAD_KEY = "embedding_input_sha256"
EMBEDDING_FINGERPRINT_PAYLOAD_KEY = "embedding_fingerprint"


class PointWriteInfrastructureError(RuntimeError):
    """Raised when Qdrant is unavailable or rejects the index representation."""


@dataclass(frozen=True)
class PointWriteResult:
    successful: int = 0
    skipped_points: tuple[PointStruct, ...] = ()

    @property
    def failed(self) -> int:
        return len(self.skipped_points)


class PointOperations:
    """Handles point embedding and upsert operations."""
    
    def __init__(
        self,
        client: QdrantClient,
        embed_model,
        batch_size: int = 50,
        max_upsert_payload_bytes: int = 8 * 1024 * 1024,
        embedding_batch_size: Optional[int] = None,
        max_embedding_workers: int = 1,
        embedding_dim: int | None = None,
        embedding_fingerprint: Optional[str] = None,
        upsert_max_attempts: int = 3,
        upsert_retry_base_seconds: float = 0.25,
    ):
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if max_upsert_payload_bytes <= 0:
            raise ValueError("max_upsert_payload_bytes must be positive")
        if embedding_batch_size is not None and embedding_batch_size <= 0:
            raise ValueError("embedding_batch_size must be positive")
        if max_embedding_workers <= 0:
            raise ValueError("max_embedding_workers must be positive")
        if upsert_max_attempts <= 0:
            raise ValueError("upsert_max_attempts must be positive")
        if upsert_retry_base_seconds < 0:
            raise ValueError("upsert_retry_base_seconds cannot be negative")
        self.client = client
        self.embed_model = embed_model
        # ``batch_size`` remains the public/legacy Qdrant write batch setting.
        self.batch_size = batch_size
        self.max_upsert_payload_bytes = max_upsert_payload_bytes
        self.embedding_batch_size = embedding_batch_size or batch_size
        self.max_embedding_workers = max_embedding_workers
        self.embedding_dim = embedding_dim
        self.embedding_fingerprint = (
            embedding_fingerprint or self._derive_embedding_fingerprint()
        )
        self.upsert_max_attempts = upsert_max_attempts
        self.upsert_retry_base_seconds = upsert_retry_base_seconds
        self._metrics_lock = threading.Lock()

    def _derive_embedding_fingerprint(self) -> str:
        """Identify only settings that can change a semantic vector."""
        config = getattr(self.embed_model, "_config", None)
        if not isinstance(config, dict):
            config = {}
        projection = {
            "backend": (
                f"{type(self.embed_model).__module__}."
                f"{type(self.embed_model).__qualname__}"
            ),
            "dimension": self.embedding_dim,
            "max_chars": config.get("max_chars"),
            "model": config.get("model", getattr(self.embed_model, "model", None)),
            "text_contract": "TextNode.text;provider-strip-truncate",
        }
        encoded = json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _is_architecture_chunk(chunk: TextNode) -> bool:
        return bool(
            chunk.metadata.get("architecture_context")
            or chunk.metadata.get("architecture_source")
            or chunk.metadata.get("repository_snapshot")
            or chunk.metadata.get("repository_facts_state")
            or chunk.metadata.get("repository_generation_manifest")
            or chunk.metadata.get("pr_overlay_generation_manifest")
        )

    @staticmethod
    def _embedding_input_hash(text: str) -> str:
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _reuse_records_for_ids(
        self,
        collection_name: Optional[str],
        point_ids: Iterable[str],
    ) -> list:
        if not collection_name:
            return []
        ids = list(point_ids)
        records = []
        try:
            for offset in range(0, len(ids), 256):
                records.extend(self.client.retrieve(
                    collection_name=collection_name,
                    ids=ids[offset:offset + 256],
                    with_payload=True,
                    with_vectors=True,
                ))
        except Exception as exception:
            # Reuse is an optimization. A read failure must fall back to the
            # established embedding path; acknowledged writes still determine
            # whether the pending generation can be activated.
            logger.warning(
                "Vector reuse lookup failed collection=%s points=%s; "
                "embedding normally: %s",
                collection_name,
                len(ids),
                exception,
            )
            return []
        return records

    def _reusable_vectors(
        self,
        semantic_data: List[Tuple[str, TextNode]],
        *,
        reuse_collection_name: Optional[str],
        reuse_records: Optional[Iterable],
    ) -> Dict[str, List[float]]:
        expected = {
            str(point_id): (
                chunk,
                self._embedding_input_hash(chunk.text),
            )
            for point_id, chunk in semantic_data
        }
        if not expected:
            return {}
        records = list(reuse_records or ())
        known_ids = {str(record.id) for record in records}
        missing_ids = [point_id for point_id in expected if point_id not in known_ids]
        records.extend(
            self._reuse_records_for_ids(reuse_collection_name, missing_ids)
        )

        reusable = {}
        for record in records:
            point_id = str(record.id)
            target = expected.get(point_id)
            if target is None:
                continue
            chunk, input_hash = target
            payload = record.payload or {}
            if (
                payload.get(EMBEDDING_INPUT_HASH_PAYLOAD_KEY) != input_hash
                or payload.get(EMBEDDING_FINGERPRINT_PAYLOAD_KEY)
                != self.embedding_fingerprint
            ):
                continue
            # Point IDs already include tenant/project/branch. Verify payload
            # identity too so a malformed legacy point can never cross scopes.
            if any(
                payload.get(key) != chunk.metadata.get(key)
                for key in ("workspace", "project", "branch")
            ):
                continue
            vector = record.vector
            if not isinstance(vector, list):
                continue
            if self.embedding_dim and len(vector) != self.embedding_dim:
                continue
            reusable[point_id] = vector
        return reusable

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
        chunk_data: List[Tuple[str, TextNode]],
        *,
        reuse_collection_name: Optional[str] = None,
        reuse_records: Optional[Iterable] = None,
        metrics: Optional[dict] = None,
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
        semantic_data = [
            (point_id, chunk)
            for point_id, chunk in chunk_data
            if not self._is_architecture_chunk(chunk)
        ]
        reusable = self._reusable_vectors(
            semantic_data,
            reuse_collection_name=reuse_collection_name,
            reuse_records=reuse_records,
        )
        chunks_to_embed = [
            chunk for point_id, chunk in semantic_data
            if str(point_id) not in reusable
        ]
        semantic_embeddings = (
            self.embed_model.get_text_embedding_batch(
                [chunk.text for chunk in chunks_to_embed]
            )
            if chunks_to_embed
            else []
        )
        embeddings = iter(semantic_embeddings)
        embedded_by_id = {
            str(point_id): next(embeddings)
            for point_id, _ in semantic_data
            if str(point_id) not in reusable
        }
        if metrics is not None:
            with self._metrics_lock:
                metrics["reused"] = metrics.get("reused", 0) + len(reusable)
                metrics["embedded"] = metrics.get("embedded", 0) + len(
                    embedded_by_id
                )

        # Build points with embeddings
        points = []
        for point_id, chunk in chunk_data:
            if self._is_architecture_chunk(chunk):
                if not self.embedding_dim:
                    raise RuntimeError(
                        "embedding dimension is required for architecture context storage"
                    )
                embedding = [0.0] * self.embedding_dim
            else:
                point_key = str(point_id)
                embedding = reusable.get(point_key, embedded_by_id.get(point_key))
                if embedding is None:
                    raise RuntimeError(
                        f"missing embedding result for point {point_id}"
                    )
            payload = {
                **chunk.metadata,
                "text": chunk.text,
            }
            if not self._is_architecture_chunk(chunk):
                payload[EMBEDDING_INPUT_HASH_PAYLOAD_KEY] = (
                    self._embedding_input_hash(chunk.text)
                )
                payload[EMBEDDING_FINGERPRINT_PAYLOAD_KEY] = (
                    self.embedding_fingerprint
                )
            if not (
                chunk.metadata.get("repository_snapshot")
                or chunk.metadata.get("repository_facts_state")
            ):
                payload["_node_content"] = chunk.text
            payload[GENERATION_MEMBER_DIGEST_PAYLOAD_KEY] = (
                compute_generation_member_digest(point_id, payload, embedding)
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
        result = self.upsert_points_detailed(collection_name, points)
        return result.successful, result.failed

    def _seal_persisted_point_digests(
        self,
        collection_name: str,
        points: List[PointStruct],
    ) -> list[tuple[object, str]]:
        """Bind digests to the vectors exactly as persisted by Qdrant."""
        if not points:
            return []
        expected = {str(point.id): point for point in points}
        records = self.client.retrieve(
            collection_name=collection_name,
            ids=[point.id for point in points],
            with_payload=True,
            with_vectors=True,
        )
        if {str(record.id) for record in records} != set(expected):
            raise RuntimeError("persisted point set is incomplete before sealing")
        sealed = []
        replacements = []
        for record in records:
            payload = dict(record.payload or {})
            digest = compute_generation_member_digest(
                record.id, payload, record.vector
            )
            payload[GENERATION_MEMBER_DIGEST_PAYLOAD_KEY] = digest
            replacements.append(PointStruct(
                id=record.id,
                vector=record.vector,
                payload=payload,
            ))
            sealed.append((record.id, digest))
        self.client.upsert(
            collection_name=collection_name,
            points=replacements,
            wait=True,
        )
        return sealed

    def upsert_points_detailed(
        self,
        collection_name: str,
        points: List[PointStruct],
    ) -> PointWriteResult:
        """Upsert points and retain the identities of quarantined points."""
        successful = 0
        skipped_points: list[PointStruct] = []

        for i in range(0, len(points), self.batch_size):
            batch = points[i:i + self.batch_size]
            batch_result = self._upsert_resilient(
                collection_name,
                batch,
                batch_offset=i,
            )
            successful += batch_result.successful
            skipped_points.extend(batch_result.skipped_points)

        return PointWriteResult(successful, tuple(skipped_points))

    @staticmethod
    def _serialized_point_size(point: PointStruct) -> int:
        """Return the exact compact JSON size of one point on the REST wire."""
        payload = point.model_dump(mode="json", exclude_none=True)
        return len(json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"))

    def _payload_bounded_batches(
        self,
        points: List[PointStruct],
    ) -> list[tuple[List[PointStruct], int]]:
        """Pack points below a conservative request-body budget.

        Qdrant enforces a byte limit independently of the configured point-count
        batch. Opaque repository snapshots can be hundreds of kilobytes each,
        so a count-only batch may otherwise allocate and transmit a 30-50 MB
        request only to have Qdrant reject and recursively retry it.
        """
        if not points:
            return []
        # Reserve space for the REST request envelope and JSON separators.
        request_overhead = 256
        batches: list[tuple[List[PointStruct], int]] = []
        current: List[PointStruct] = []
        current_size = request_overhead
        for point in points:
            point_size = self._serialized_point_size(point) + 1
            if (
                current
                and current_size + point_size > self.max_upsert_payload_bytes
            ):
                batches.append((current, current_size))
                current = []
                current_size = request_overhead
            current.append(point)
            current_size += point_size
        if current:
            batches.append((current, current_size))
        return batches

    @staticmethod
    def _status_code(exception: Exception) -> int | None:
        status_code = getattr(exception, "status_code", None)
        if isinstance(status_code, int):
            return status_code
        response = getattr(exception, "response", None)
        status_code = getattr(response, "status_code", None)
        return status_code if isinstance(status_code, int) else None

    @classmethod
    def _is_batch_shape_failure(cls, exception: Exception | None) -> bool:
        """Return whether a smaller request can isolate a rejected point."""
        if exception is None:
            return False
        message = str(exception).casefold()
        if any(
            marker in message
            for marker in (
                "vector dimension",
                "dimension mismatch",
                "expected dimension",
                "vector name",
                "collection not found",
                "doesn't exist",
                "does not exist",
                "api key",
                "authentication",
                "model not found",
                "unsupported parameter",
                "unknown field",
                "provider routing",
            )
        ):
            return False
        status_code = cls._status_code(exception)
        if status_code in {400, 413, 422}:
            return True
        return any(
            marker in message
            for marker in (
                "bad request",
                "payload too large",
                "request entity too large",
                "request too large",
                "validation error",
            )
        )

    @staticmethod
    def _point_label(point: PointStruct) -> str:
        payload = point.payload or {}
        path = payload.get("path", "<unknown>")
        return f"id={point.id} path={path}"

    def _upsert_resilient(
        self,
        collection_name: str,
        points: List[PointStruct],
        *,
        batch_offset: int,
    ) -> PointWriteResult:
        """Write a batch with bounded retry and rejected-request subdivision."""
        if not points:
            return PointWriteResult()

        bounded_batches = self._payload_bounded_batches(points)
        if len(bounded_batches) > 1:
            logger.info(
                "Splitting %s Qdrant points into %s byte-bounded requests "
                "before write (estimated_bytes=%s limit=%s)",
                len(points),
                len(bounded_batches),
                sum(size for _, size in bounded_batches),
                self.max_upsert_payload_bytes,
            )
            successful = 0
            skipped_points: list[PointStruct] = []
            offset = batch_offset
            for bounded_batch, _estimated_bytes in bounded_batches:
                batch_result = self._upsert_resilient(
                    collection_name,
                    bounded_batch,
                    batch_offset=offset,
                )
                successful += batch_result.successful
                skipped_points.extend(batch_result.skipped_points)
                offset += len(bounded_batch)
            return PointWriteResult(successful, tuple(skipped_points))

        error = None
        for attempt in range(1, self.upsert_max_attempts + 1):
            try:
                self.client.upsert(
                    collection_name=collection_name,
                    points=points,
                    wait=True,
                )
                return PointWriteResult(successful=len(points))
            except Exception as exception:
                error = exception
                if self._is_batch_shape_failure(exception):
                    break
                if attempt >= self.upsert_max_attempts:
                    logger.error(
                        "Qdrant upsert failed after %s attempts at point "
                        "offset %s (%s points): %s",
                        self.upsert_max_attempts,
                        batch_offset,
                        len(points),
                        exception,
                    )
                    raise PointWriteInfrastructureError(
                        "Qdrant index storage is unavailable or incompatible "
                        f"after {self.upsert_max_attempts} attempts"
                    ) from exception
                delay = self.upsert_retry_base_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Qdrant upsert failed at point offset %s "
                    "(%s points, attempt %s/%s); retrying in %.2fs: %s",
                    batch_offset,
                    len(points),
                    attempt,
                    self.upsert_max_attempts,
                    delay,
                    exception,
                )
                if delay:
                    time.sleep(delay)

        if len(points) == 1:
            logger.error(
                "Skipping one exact index point rejected by Qdrant "
                "(%s): %s",
                self._point_label(points[0]),
                error,
            )
            return PointWriteResult(skipped_points=(points[0],))

        midpoint = len(points) // 2
        left = points[:midpoint]
        right = points[midpoint:]

        logger.warning(
            "Qdrant rejected a %s-point request at offset %s; "
            "isolating it into %s and %s points: %s",
            len(points),
            batch_offset,
            len(left),
            len(right),
            error,
        )
        left_result = self._upsert_resilient(
            collection_name,
            left,
            batch_offset=batch_offset,
        )
        right_result = self._upsert_resilient(
            collection_name,
            right,
            batch_offset=batch_offset + midpoint,
        )
        return PointWriteResult(
            successful=left_result.successful + right_result.successful,
            skipped_points=(
                *left_result.skipped_points,
                *right_result.skipped_points,
            ),
        )

    def _embed_resilient(
        self,
        chunk_data: List[Tuple[str, TextNode]],
        *,
        reuse_collection_name: Optional[str] = None,
        reuse_records: Optional[Iterable] = None,
        metrics: Optional[dict] = None,
    ) -> tuple[List[PointStruct], int]:
        """Embed a slice, isolating only provider-rejected input chunks."""
        if not chunk_data:
            return [], 0
        try:
            return self.embed_and_create_points(
                chunk_data,
                reuse_collection_name=reuse_collection_name,
                reuse_records=reuse_records,
                metrics=metrics,
            ), 0
        except MemoryError:
            raise
        except Exception as exception:
            if not self._is_batch_shape_failure(exception):
                raise
            if len(chunk_data) == 1:
                chunk = chunk_data[0][1]
                logger.error(
                    "Skipping one embedding input rejected by the provider "
                    "(path=%s): %s",
                    chunk.metadata.get("path", "<unknown>"),
                    exception,
                )
                return [], 1

            midpoint = len(chunk_data) // 2
            logger.warning(
                "Embedding provider rejected a %s-chunk request; "
                "isolating it into %s and %s chunks: %s",
                len(chunk_data),
                midpoint,
                len(chunk_data) - midpoint,
                exception,
            )
            left_points, left_skipped = self._embed_resilient(
                chunk_data[:midpoint],
                reuse_collection_name=reuse_collection_name,
                reuse_records=reuse_records,
                metrics=metrics,
            )
            right_points, right_skipped = self._embed_resilient(
                chunk_data[midpoint:],
                reuse_collection_name=reuse_collection_name,
                reuse_records=reuse_records,
                metrics=metrics,
            )
            return (
                [*left_points, *right_points],
                left_skipped + right_skipped,
            )
    
    def process_and_upsert_chunks(
        self,
        chunks: List[TextNode],
        collection_name: str,
        workspace: str,
        project: str,
        branch: str,
        *,
        reuse_collection_name: Optional[str] = None,
        operation_id: Optional[str] = None,
        metrics: Optional[dict] = None,
    ) -> Tuple[int, int]:
        """Full pipeline: prepare, embed, and upsert chunks.
        
        Returns:
            Tuple of (successful_count, failed_count)
        """
        # Prepare chunks with IDs
        chunk_data = self.prepare_chunks_for_embedding(
            chunks, workspace, project, branch
        )
        
        operation_id = operation_id or uuid.uuid4().hex
        operation_metrics = metrics if metrics is not None else {}
        successful = 0
        failed = 0

        batches = [
            chunk_data[offset:offset + self.embedding_batch_size]
            for offset in range(0, len(chunk_data), self.embedding_batch_size)
        ]
        pending: Dict[Future, tuple[int, float]] = {}
        next_batch = 0
        write_buffer: list[PointStruct] = []
        started = time.perf_counter()

        # Deterministic context uses zero vectors and can contain large opaque
        # snapshot payloads. Running those batches through the embedding pool
        # creates several complete PointStruct batches concurrently for no
        # benefit and was a major source of transient memory pressure. Build
        # and persist one bounded slice at a time instead.
        if chunk_data and all(
            self._is_architecture_chunk(chunk)
            for _, chunk in chunk_data
        ):
            for offset in range(0, len(chunk_data), self.embedding_batch_size):
                point_batch = self.embed_and_create_points(
                    chunk_data[offset:offset + self.embedding_batch_size],
                    reuse_collection_name=reuse_collection_name,
                    metrics=operation_metrics,
                )
                batch_result = self.upsert_points_detailed(
                    collection_name,
                    point_batch,
                )
                successful += batch_result.successful
                failed += batch_result.failed
                logger.info(
                    "RAG deterministic-context batch completed "
                    "operation_id=%s points=%s skipped=%s",
                    operation_id,
                    len(point_batch),
                    batch_result.failed,
                )
            logger.info(
                "RAG point pipeline completed operation_id=%s chunks=%s "
                "successful=%s failed=%s reused=0 embedded=0 duration_ms=%s "
                "embedding_concurrency=0 embedding_batch_size=%s "
                "qdrant_batch_size=%s",
                operation_id,
                len(chunk_data),
                successful,
                failed,
                round((time.perf_counter() - started) * 1000),
                self.embedding_batch_size,
                self.batch_size,
            )
            return successful, failed

        def submit_available(executor: ThreadPoolExecutor) -> None:
            nonlocal next_batch
            while (
                next_batch < len(batches)
                and len(pending) < self.max_embedding_workers
            ):
                batch_number = next_batch
                batch = batches[batch_number]
                future = executor.submit(
                    self._embed_resilient,
                    batch,
                    reuse_collection_name=reuse_collection_name,
                    metrics=operation_metrics,
                )
                pending[future] = (batch_number, time.perf_counter())
                next_batch += 1

        with ThreadPoolExecutor(
            max_workers=self.max_embedding_workers,
            thread_name_prefix="rag-embed",
        ) as executor:
            submit_available(executor)
            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    batch_number, batch_started = pending.pop(future)
                    point_batch, embedding_skipped = future.result()
                    failed += embedding_skipped
                    write_buffer.extend(point_batch)
                    logger.info(
                        "RAG embedding batch completed operation_id=%s "
                        "batch=%s/%s points=%s skipped=%s reusable_total=%s "
                        "embedded_total=%s duration_ms=%s",
                        operation_id,
                        batch_number + 1,
                        len(batches),
                        len(point_batch),
                        embedding_skipped,
                        operation_metrics.get("reused", 0),
                        operation_metrics.get("embedded", 0),
                        round((time.perf_counter() - batch_started) * 1000),
                    )
                while len(write_buffer) >= self.batch_size:
                    qdrant_started = time.perf_counter()
                    write_batch = write_buffer[:self.batch_size]
                    del write_buffer[:self.batch_size]
                    batch_result = self._upsert_resilient(
                        collection_name,
                        write_batch,
                        batch_offset=successful + failed,
                    )
                    successful += batch_result.successful
                    failed += batch_result.failed
                    logger.info(
                        "RAG Qdrant batch completed operation_id=%s points=%s "
                        "skipped=%s duration_ms=%s",
                        operation_id,
                        len(write_batch),
                        batch_result.failed,
                        round((time.perf_counter() - qdrant_started) * 1000),
                    )
                submit_available(executor)

        if write_buffer:
            qdrant_started = time.perf_counter()
            batch_result = self._upsert_resilient(
                collection_name,
                write_buffer,
                batch_offset=successful + failed,
            )
            successful += batch_result.successful
            failed += batch_result.failed
            logger.info(
                "RAG Qdrant batch completed operation_id=%s points=%s "
                "skipped=%s duration_ms=%s",
                operation_id,
                len(write_buffer),
                batch_result.failed,
                round((time.perf_counter() - qdrant_started) * 1000),
            )

        logger.info(
            "RAG point pipeline completed operation_id=%s chunks=%s "
            "successful=%s failed=%s reused=%s embedded=%s duration_ms=%s "
            "embedding_concurrency=%s embedding_batch_size=%s "
            "qdrant_batch_size=%s",
            operation_id,
            len(chunk_data),
            successful,
            failed,
            operation_metrics.get("reused", 0),
            operation_metrics.get("embedded", 0),
            round((time.perf_counter() - started) * 1000),
            self.max_embedding_workers,
            self.embedding_batch_size,
            self.batch_size,
        )

        return successful, failed
