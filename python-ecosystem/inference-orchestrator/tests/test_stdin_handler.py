"""One-shot stdin requests close their clients within the request event loop."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from server.stdin_handler import StdinHandler


@pytest.mark.parametrize("request_fails", [False, True])
def test_request_retires_client_before_event_loop_closes(request_fails, capsys):
    loops = []

    async def process(request, token):
        loops.append(asyncio.get_running_loop())
        if request_fails:
            raise RuntimeError("processing failed")
        return {"issues": []}

    async def close():
        loops.append(asyncio.get_running_loop())

    service = SimpleNamespace(
        process_review_request=AsyncMock(side_effect=process),
        rag_client=SimpleNamespace(close=AsyncMock(side_effect=close)),
    )
    with patch("server.stdin_handler.ReviewService", return_value=service):
        handler = StdinHandler()
    with (
        patch.object(handler, "read_request_from_stdin", return_value={}),
        patch("server.stdin_handler.ReviewRequestDto", return_value="request"),
    ):
        handler.process_stdin_request()

    output = json.loads(capsys.readouterr().out)
    if request_fails:
        assert output["error"] == "Failed to process request"
    else:
        assert output == {"issues": []}
    assert len(loops) == 2
    assert loops[0] is loops[1]
    assert loops[0].is_closed()
    service.rag_client.close.assert_awaited_once_with()


def test_cleanup_failure_preserves_completed_review(capsys):
    service = SimpleNamespace(
        process_review_request=AsyncMock(return_value={"issues": []}),
        rag_client=SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close failed"))),
    )
    with patch("server.stdin_handler.ReviewService", return_value=service):
        handler = StdinHandler()
    with (
        patch.object(handler, "read_request_from_stdin", return_value={}),
        patch("server.stdin_handler.ReviewRequestDto", return_value="request"),
    ):
        handler.process_stdin_request()
    assert json.loads(capsys.readouterr().out) == {"issues": []}
