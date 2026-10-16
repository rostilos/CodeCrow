"""Serializable CPU-build operations and process-local repository resources."""
from __future__ import annotations

from multiprocessing.util import Finalize
import logging

from fastapi import HTTPException

from ..core.index_manager import RAGIndexManager
from ..models.config import RAGConfig

logger = logging.getLogger(__name__)

_manager = None
_cancel_flags = None
_progress_queue = None


class WorkerCancellation:
    """Read a shared byte without an IPC round trip at every source/SQL check."""

    def __init__(self, slot: int):
        self.slot = slot

    def is_set(self) -> bool:
        return bool(_cancel_flags[self.slot])


def initialize_build_worker(config, cancel_flags, progress_queue, manager_factory=RAGIndexManager):
    global _manager, _cancel_flags, _progress_queue
    _cancel_flags, _progress_queue = cancel_flags, progress_queue
    _manager = manager_factory(RAGConfig.model_validate(config))
    # multiprocessing workers use its finalizer registry during normal exit.
    Finalize(None, _manager.close, exitpriority=10)


def execute_build_operation(operation: str, payload: dict, slot: int, job_id: str) -> dict:
    """Transport plain data only; managers, SQLite handles and leases stay local."""
    from .models import IndexRequest, ProposedTreePrepareRequest
    from .routers.index import index_repository
    from .routers.query import prepare_review_generation

    cancellation = WorkerCancellation(slot)

    progress_unavailable = False

    def progress(event):
        nonlocal progress_unavailable
        try:
            _progress_queue.put((job_id, event))
        except Exception:
            if not progress_unavailable:
                logger.warning(
                    "Build progress transport unavailable; continuing operation=%s job=%s",
                    operation, job_id, exc_info=True,
                )
                progress_unavailable = True

    try:
        _manager._raise_if_cancelled(cancellation)
        if operation == "index":
            result = index_repository(
                IndexRequest.model_validate(payload["request"]),
                index_manager=_manager,
                repo_path=payload["repo_path"],
                collection_target=payload["collection_target"],
                source_tree_exclusively_owned=payload.get("source_tree_exclusively_owned", False),
                progress_callback=progress,
                cancellation_event=cancellation,
            ).model_dump(mode="json")
        elif operation == "prepare_review":
            result = prepare_review_generation(
                ProposedTreePrepareRequest.model_validate(payload["request"]),
                manager=_manager,
                cancellation_event=cancellation,
            )
        else:
            raise ValueError(f"Unknown repository build operation: {operation}")
        return {"result": result}
    except HTTPException as error:
        # HTTPException is not safely reconstructed by ProcessPoolExecutor's
        # exception pickle protocol. Preserve its public status/detail as data.
        return {"error": {"status_code": error.status_code, "detail": error.detail}}
