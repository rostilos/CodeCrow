"""PR file indexing endpoints."""
import uuid
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from llama_index.core import Document as LlamaDocument
from qdrant_client.models import Filter, FieldCondition, MatchValue

from ..models import PRIndexRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pr"])


def _get_index_manager():
    from ..api import index_manager
    return index_manager


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
        collection_name = index_manager._get_project_collection_name(
            request.workspace, request.project
        )

        index_manager._ensure_collection_exists(collection_name)

        # Delete existing points for this PR first (handles re-analysis)
        try:
            index_manager.qdrant_client.delete(
                collection_name=collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(key="pr_number", match=MatchValue(value=request.pr_number))
                    ]
                )
            )
            logger.info(f"Deleted existing PR points for PR #{request.pr_number}")
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
                key = f"pr:{request.pr_number}:{request.workspace}:{request.project}:{path}:{chunk_index}"
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, key))
                chunk_data.append((point_id, chunk))

        points = index_manager._point_ops.embed_and_create_points(chunk_data)
        successful, failed = index_manager._point_ops.upsert_points(collection_name, points)

        logger.info(f"Indexed PR #{request.pr_number}: {successful} chunks from {len(documents)} files")

        return {
            "status": "indexed",
            "pr_number": request.pr_number,
            "files_processed": len(documents),
            "chunks_indexed": successful,
            "chunks_failed": failed
        }

    except ValueError as e:
        logger.warning(f"Invalid request for PR indexing: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Internal error indexing PR files: {e}")
        raise HTTPException(status_code=500, detail="Internal indexing error")


@router.delete("/index/pr-files/{workspace}/{project}/{pr_number}")
def delete_pr_files(workspace: str, project: str, pr_number: int):
    """Delete all indexed points for a specific PR."""
    index_manager = _get_index_manager()
    try:
        collection_name = index_manager._get_project_collection_name(workspace, project)

        if not index_manager._collection_manager.collection_exists(collection_name):
            return {"status": "skipped", "message": "Collection does not exist"}

        index_manager.qdrant_client.delete(
            collection_name=collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(key="pr_number", match=MatchValue(value=pr_number))
                ]
            )
        )

        logger.info(f"Deleted PR #{pr_number} points from {collection_name}")

        return {
            "status": "deleted",
            "pr_number": pr_number,
            "collection": collection_name
        }

    except Exception as e:
        logger.error(f"Error deleting PR files: {e}")
        raise HTTPException(status_code=500, detail=str(e))
