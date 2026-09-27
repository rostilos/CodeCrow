"""Pending build ownership and optional session cleanup on every exit."""
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rag_pipeline.core.index_manager.publication import pending_generation
from rag_pipeline.core.structural_store import StructuralGenerationStore


@pytest.mark.parametrize("error", [InterruptedError("cancelled"), RuntimeError("writer failed")])
def test_pending_build_closes_database_and_unfinished_session_on_failure(tmp_path, error):
    store = StructuralGenerationStore(tmp_path)
    session = SimpleNamespace(close=Mock())
    connection = None
    with pytest.raises(type(error), match=str(error)):
        with pending_generation(store, "target") as build:
            connection = build.connection
            build.analysis_handle = session
            raise error
    session.close.assert_called_once_with()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    assert list(store.pending_root.iterdir()) == []
    assert list(store.generations_root.iterdir()) == []


def test_session_cleanup_failure_does_not_hide_build_failure(tmp_path, caplog):
    store = StructuralGenerationStore(tmp_path)
    with pytest.raises(InterruptedError, match="build cancelled"):
        with pending_generation(store, "target") as build:
            build.analysis_handle = SimpleNamespace(close=Mock(side_effect=RuntimeError("cleanup failed")))
            raise InterruptedError("build cancelled")
    assert "Unfinished repository plugin session cleanup failed" in caplog.text
    assert list(store.pending_root.iterdir()) == []


def test_ownership_acquisition_failure_closes_and_removes_initialized_database(tmp_path, monkeypatch):
    store = StructuralGenerationStore(tmp_path)
    connections = []
    initialize = store.initialize

    def recording_initialize(paths):
        connection = initialize(paths)
        connections.append(connection)
        return connection

    monkeypatch.setattr(store, "initialize", recording_initialize)
    monkeypatch.setattr(store, "acquire_pending_ownership", Mock(side_effect=OSError("lock failed")))
    with pytest.raises(OSError, match="lock failed"):
        with pending_generation(store, "target"):
            pytest.fail("ownership failure must not enter the build")
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    assert list(store.pending_root.iterdir()) == []
