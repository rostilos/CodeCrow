"""PR file indexing endpoints."""
import uuid
import logging
from datetime import datetime, timezone
from hashlib import sha256
from fastapi import APIRouter, HTTPException
from llama_index.core import Document as LlamaDocument
from qdrant_client.models import Filter, FieldCondition, MatchValue

from ..models import ContextSnapshotV1, PRIndexRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pr"])


def _get_index_manager():
    from ..api import index_manager
    return index_manager


def _request_snapshot(request: PRIndexRequest) -> ContextSnapshotV1 | None:
    """Ignore loose MagicMock/legacy attributes; only typed snapshots are exact."""
    value = getattr(request, "snapshot", None)
    return value if isinstance(value, ContextSnapshotV1) else None


@router.post("/index/pr-files")
def index_pr_files(request: PRIndexRequest):
    """
    Index PR files into the main collection with PR-specific metadata.

    Files are indexed with metadata: pr=true, pr_number, pr_branch.
    This allows hybrid queries that prioritize PR data over branch data.
    Existing PR points for the same pr_number are deleted first.
    """
    index_manager = _get_index_manager()
    try:
        snapshot = _request_snapshot(request)
        collection_name = index_manager._get_project_collection_name(
            request.workspace, request.project
        )

        index_manager._ensure_collection_exists(collection_name)

        # Delete the current execution's previous attempt. Exact overlays are
        # execution-isolated so concurrent/retried reviews cannot erase one
        # another. Legacy callers retain PR-wide replacement semantics.
        try:
            delete_conditions = [
                FieldCondition(key="pr_number", match=MatchValue(value=request.pr_number))
            ]
            if snapshot is not None:
                delete_conditions.extend([
                    FieldCondition(
                        key="snapshot_sha",
                        match=MatchValue(value=snapshot.head_sha),
                    ),
                    FieldCondition(
                        key="execution_id",
                        match=MatchValue(value=request.execution_id),
                    ),
                ])
            index_manager.qdrant_client.delete(
                collection_name=collection_name,
                points_selector=Filter(
                    must=delete_conditions
                )
            )
            logger.info(
                "Deleted existing PR overlay for PR #%s execution=%s",
                request.pr_number,
                request.execution_id if snapshot is not None else "legacy",
            )
        except Exception as e:
            logger.warning(f"Error deleting existing PR points: {e}")

        # Convert files to LlamaIndex documents
        documents = []
        for file_info in request.files:
            if not file_info.content or not file_info.content.strip():
                continue
            if file_info.change_type == "DELETED":
                continue

            doc = LlamaDocument(
                text=file_info.content,
                metadata={
                    "path": file_info.path,
                    "change_type": file_info.change_type,
                }
            )
            documents.append(doc)

        if not documents:
            return {
                "status": "skipped",
                "message": "No files to index",
                "chunks_indexed": 0
            }

        # Split documents into chunks
        chunks = index_manager.splitter.split_documents(documents)

        # Add PR metadata to all chunks
        for chunk in chunks:
            chunk.metadata["pr"] = True
            chunk.metadata["pr_number"] = request.pr_number
            chunk.metadata["pr_branch"] = request.branch
            chunk.metadata["workspace"] = request.workspace
            chunk.metadata["project"] = request.project
            chunk.metadata["branch"] = request.branch
            chunk.metadata["indexed_at"] = datetime.now(timezone.utc).isoformat()
            if snapshot is not None:
                chunk.metadata.update({
                    "snapshot_sha": snapshot.head_sha,
                    "base_sha": snapshot.base_sha,
                    "head_sha": snapshot.head_sha,
                    "merge_base_sha": snapshot.merge_base_sha,
                    "context_snapshot_id": snapshot.identity,
                    "context_identity_version": snapshot.schema_version,
                    "parser_version": snapshot.parser_version,
                    "chunker_version": snapshot.chunker_version,
                    "embedding_version": snapshot.embedding_version,
                })
            execution_id = getattr(request, "execution_id", None)
            if isinstance(execution_id, str) and execution_id:
                chunk.metadata["execution_id"] = execution_id

        # Embed and upsert using point_ops
        chunk_data = []
        chunks_by_file = {}
        for chunk in chunks:
            path = chunk.metadata.get("path", str(uuid.uuid4()))
            if path not in chunks_by_file:
                chunks_by_file[path] = []
            chunks_by_file[path].append(chunk)

        for path, file_chunks in chunks_by_file.items():
            for chunk_index, chunk in enumerate(file_chunks):
                if snapshot is not None:
                    content_digest = sha256(chunk.text.encode("utf-8")).hexdigest()
                    chunk.metadata["content_digest"] = content_digest
                    point_id = index_manager._point_ops.generate_point_id(
                        request.workspace,
                        request.project,
                        request.branch,
                        path,
                        chunk_index,
                        revision=snapshot.head_sha,
                        content_digest=content_digest,
                        identity_scope=f"pr:{request.pr_number}:{request.execution_id}",
                        processing_identity=(
                            f"{snapshot.parser_version}:{snapshot.chunker_version}:"
                            f"{snapshot.embedding_version}"
                        ),
                    )
                else:
                    key = f"pr:{request.pr_number}:{request.workspace}:{request.project}:{path}:{chunk_index}"
                    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, key))
                chunk_data.append((point_id, chunk))

        points = index_manager._point_ops.embed_and_create_points(chunk_data)
        successful, failed = index_manager._point_ops.upsert_points(collection_name, points)

        logger.info(f"Indexed PR #{request.pr_number}: {successful} chunks from {len(documents)} files")

        result = {
            "status": "indexed",
            "pr_number": request.pr_number,
            "files_processed": len(documents),
            "chunks_indexed": successful,
            "chunks_failed": failed,
        }
        if snapshot is not None:
            result.update({
                "context_snapshot_id": snapshot.identity,
                "snapshot_sha": snapshot.head_sha,
            })
        return result

    except ValueError as e:
        logger.warning(f"Invalid request for PR indexing: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Internal error indexing PR files: {e}")
        raise HTTPException(status_code=500, detail="Internal indexing error")


@router.delete("/index/pr-files/{workspace}/{project}/{pr_number}")
def delete_pr_files(
    workspace: str,
    project: str,
    pr_number: int,
    execution_id: str | None = None,
    head_sha: str | None = None,
):
    """Delete all indexed points for a specific PR."""
    index_manager = _get_index_manager()
    try:
        collection_name = index_manager._get_project_collection_name(workspace, project)

        if not index_manager._collection_manager.collection_exists(collection_name):
            return {"status": "skipped", "message": "Collection does not exist"}

        delete_conditions = [
            FieldCondition(key="pr_number", match=MatchValue(value=pr_number))
        ]
        if execution_id:
            delete_conditions.append(
                FieldCondition(key="execution_id", match=MatchValue(value=execution_id))
            )
        if head_sha:
            delete_conditions.append(
                FieldCondition(key="snapshot_sha", match=MatchValue(value=head_sha))
            )
        index_manager.qdrant_client.delete(
            collection_name=collection_name,
            points_selector=Filter(
                must=delete_conditions
            )
        )

        logger.info(f"Deleted PR #{pr_number} points from {collection_name}")

        return {
            "status": "deleted",
            "pr_number": pr_number,
            "execution_id": execution_id,
            "snapshot_sha": head_sha,
            "collection": collection_name
        }

    except Exception as e:
        logger.error(f"Error deleting PR files: {e}")
        raise HTTPException(status_code=500, detail=str(e))
