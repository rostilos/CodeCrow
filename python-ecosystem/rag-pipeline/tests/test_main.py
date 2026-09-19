"""RAG service process-topology tests."""

from unittest.mock import patch

import main


def test_uvicorn_workers_normalize_per_process_index_capacity(caplog):
    with patch.dict(
        "os.environ",
        {
            "UVICORN_WORKERS": "3",
            "RAG_FULL_INDEX_CONCURRENCY": "4",
        },
    ):
        assert main.effective_uvicorn_workers() == 3
        assert main.os.environ["RAG_FULL_INDEX_CONCURRENCY"] == "1"

    assert "capacity is not multiplicative" in caplog.text


def test_single_uvicorn_worker_keeps_configured_thread_capacity():
    with patch.dict(
        "os.environ",
        {
            "UVICORN_WORKERS": "1",
            "RAG_FULL_INDEX_CONCURRENCY": "3",
        },
    ):
        assert main.effective_uvicorn_workers() == 1
        assert main.os.environ["RAG_FULL_INDEX_CONCURRENCY"] == "3"


def test_invalid_uvicorn_workers_falls_back_to_one(caplog):
    with patch.dict("os.environ", {"UVICORN_WORKERS": "invalid"}):
        assert main.effective_uvicorn_workers() == 1

    assert "Invalid UVICORN_WORKERS" in caplog.text
