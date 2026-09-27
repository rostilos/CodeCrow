"""Sealing and exact concurrent-publication reconciliation for pending graphs."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..exact_index import ExactIndexPreconditionError
from ..structural_store import (
    StructuralGenerationStore,
    StructuralGraphWriter,
    GenerationPaths,
    write_receipt,
)


@dataclass
class GenerationPublisher:
    store: StructuralGenerationStore
    get_revision_preflight: Callable[..., dict[str, Any] | None]

    def publish(
        self, writer: StructuralGraphWriter, pending: GenerationPaths,
        receipt: dict[str, Any], *, workspace: str, project: str,
        branch: str, commit: str, collection_target: str,
        activation_guard: Callable[[], None], check_cancelled: Callable[[], None],
    ) -> dict[str, Any]:
        check_cancelled()
        writer.seal(receipt)
        check_cancelled()
        writer.connection.close()
        write_receipt(pending.receipt, receipt)
        check_cancelled()
        activation_guard()
        check_cancelled()
        try:
            self.store.publish(pending)
        except FileExistsError:
            existing = self.get_revision_preflight(
                workspace,
                project,
                branch,
                commit,
                collection_target=collection_target,
            )
            if existing is None or existing.get(
                "generation_manifest_sha256"
            ) != receipt["generation_manifest_sha256"]:
                raise ExactIndexPreconditionError(
                    "structural generation target was published concurrently "
                    "with different content"
                )
            self.store.remove_pending(pending)
            receipt = existing
        return receipt


@dataclass
class PendingGeneration:
    paths: GenerationPaths
    connection: sqlite3.Connection
    base_receipt: dict[str, Any]
    analysis_handle: Any = None


@contextmanager
def pending_generation(
    store: StructuralGenerationStore, target: str, *,
    base_binding: Mapping[str, Any] | None = None,
):
    """Own every pending database, file lock and unfinished plugin session.

    The private path may be removed unconditionally at exit: publication moves
    it to the immutable generation directory, which cleanup never touches.
    """
    paths = store.pending_paths(target)
    connection = None
    ownership = None
    resources = None
    try:
        if base_binding is None:
            connection = store.initialize(paths)
            ownership = store.acquire_pending_ownership(paths)
            base_receipt = {}
        else:
            paths, connection, base_receipt, ownership = store.clone_bound_to_pending(
                target=target, **base_binding,
            )
        resources = PendingGeneration(paths, connection, base_receipt)
        yield resources
    finally:
        if resources is not None and resources.analysis_handle is not None:
            close = getattr(resources.analysis_handle, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logging.getLogger(__name__).warning(
                        "Unfinished repository plugin session cleanup failed",
                        exc_info=True,
                    )
        try:
            if connection is not None:
                connection.close()
        finally:
            try:
                store.remove_pending(paths)
            finally:
                store.release_pending_ownership(ownership)
