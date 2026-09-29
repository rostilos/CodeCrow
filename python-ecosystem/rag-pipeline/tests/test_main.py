"""RAG service process-topology tests."""

import logging
from unittest.mock import patch

import pytest

import main
from rag_pipeline.models.config import RAGConfig
from rag_pipeline.core.index_manager.build_support import _config_int


@pytest.mark.parametrize("workers, capacity", [(3, 4), (1, 3), (3, 40)])
def test_worker_topology_preserves_configured_index_capacity(
    caplog, workers, capacity
):
    configured = {
        "UVICORN_WORKERS": str(workers),
        "RAG_FULL_INDEX_CONCURRENCY": str(capacity),
    }
    with patch.dict("os.environ", configured, clear=True):
        with caplog.at_level(logging.INFO):
            assert main.effective_uvicorn_workers() == workers
        assert dict(main.os.environ) == configured
        assert RAGConfig().full_index_concurrency == capacity

    assert f"{capacity} slot(s) per process" in caplog.text
    assert f"{workers * capacity} total slot(s)" in caplog.text


def test_default_topology_does_not_create_environment_overrides(caplog):
    with patch.dict("os.environ", {}, clear=True):
        with caplog.at_level(logging.INFO):
            assert main.effective_uvicorn_workers() == 1
        assert dict(main.os.environ) == {}
        assert RAGConfig().full_index_concurrency == 16

    assert "16 total slot(s)" in caplog.text


@pytest.mark.parametrize("raw_workers", ["invalid", "0", "-3"])
def test_invalid_uvicorn_workers_fall_back_without_environment_mutation(
    caplog, raw_workers
):
    configured = {
        "UVICORN_WORKERS": raw_workers,
        "RAG_FULL_INDEX_CONCURRENCY": "4",
    }
    with patch.dict("os.environ", configured, clear=True):
        with caplog.at_level(logging.INFO):
            assert main.effective_uvicorn_workers() == 1
        assert dict(main.os.environ) == configured
        assert RAGConfig().full_index_concurrency == 4

    assert "4 total slot(s)" in caplog.text
    if raw_workers == "invalid":
        assert "Invalid UVICORN_WORKERS" in caplog.text


def test_invalid_capacity_remains_subject_to_worker_configuration_validation(caplog):
    raw_capacity = "invalid"
    configured = {
        "UVICORN_WORKERS": "3",
        "RAG_FULL_INDEX_CONCURRENCY": raw_capacity,
    }
    with patch.dict("os.environ", configured, clear=True):
        assert main.effective_uvicorn_workers() == 3
        assert dict(main.os.environ) == configured
        with pytest.raises(ValueError):
            RAGConfig()

    assert "Invalid RAG_FULL_INDEX_CONCURRENCY" in caplog.text
    assert "total slot(s)" not in caplog.text


@pytest.mark.parametrize("raw_capacity", ["0", "-3"])
def test_nonpositive_capacity_reports_manager_normalization_without_mutation(
    caplog, raw_capacity
):
    configured = {
        "UVICORN_WORKERS": "3",
        "RAG_FULL_INDEX_CONCURRENCY": raw_capacity,
    }
    with patch.dict("os.environ", configured, clear=True):
        with caplog.at_level(logging.INFO):
            assert main.effective_uvicorn_workers() == 3
        assert dict(main.os.environ) == configured
        assert _config_int(RAGConfig(), "full_index_concurrency", 1) == 1

    assert "1 slot(s) per process" in caplog.text
    assert "3 total slot(s)" in caplog.text
