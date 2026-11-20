from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from llm.provider_errors import raise_if_outcome_unknown


def test_uncertain_outcome_survives_wrapping_but_schema_errors_can_be_repaired():
    error = RuntimeError("agent failed")
    error.__cause__ = httpx.ReadTimeout("response lost")
    with pytest.raises(RuntimeError, match="agent failed"):
        raise_if_outcome_unknown(error)
    raise_if_outcome_unknown(ValueError("invalid JSON"))


def test_httpx2_transport_used_by_current_provider_sdks_is_also_uncertain():
    import httpx2
    with pytest.raises(httpx2.ReadTimeout):
        raise_if_outcome_unknown(httpx2.ReadTimeout("response lost"))


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [1, 2])
async def test_review_transport_failure_is_not_returned_as_a_repairable_empty_result(monkeypatch, stage):
    from service.review.orchestrator import stage_1_file_review as first, stage_2_cross_file as second
    module = first if stage == 1 else second
    invoke = AsyncMock(side_effect=TimeoutError("accepted request lost its response"))
    monkeypatch.setattr(module, "invoke_structured_output", invoke)
    monkeypatch.setattr(module, "_supports_structured_output" if stage == 1 else "supports_structured_output", lambda _: True)
    llm = MagicMock()
    with pytest.raises(TimeoutError):
        if stage == 1:
            await first._invoke_stage_1_batch_llm(llm, "review", ["a.py"])
        else:
            await second._invoke_stage_2_llm(llm, "review", "primary")
    invoke.assert_awaited_once()
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_qa_agent_timeout_does_not_start_direct_generation():
    from service.qa_documentation.qa_doc_orchestrator import QaDocOrchestrator
    agent = SimpleNamespace(available_tool_names=frozenset(), execute=AsyncMock(side_effect=TimeoutError()))
    llm = SimpleNamespace(ainvoke=AsyncMock())
    orchestrator = QaDocOrchestrator(llm, agent_service=agent)
    with pytest.raises(TimeoutError):
        await orchestrator._invoke_document([{"role": "user", "content": "review"}])
    agent.execute.assert_awaited_once()
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["ask", "summarize"])
async def test_command_agent_timeout_does_not_start_direct_fallback(monkeypatch, command):
    import service.command.command_service as module
    calls = []

    async def stream(_request):
        calls.append("provider attempt")
        raise TimeoutError("response lost")
        yield

    monkeypatch.setattr(module, "AgentExecutionService", lambda **kwargs: SimpleNamespace(stream=stream))
    service = module.CommandService()
    service._guarded_direct_invoke = AsyncMock()
    with pytest.raises(TimeoutError):
        if command == "ask":
            await service._execute_ask(MagicMock(), MagicMock(), "question", None)
        else:
            await service._execute_summarize(MagicMock(), MagicMock(), "summary", False, None)
    assert len(calls) == 1
    service._guarded_direct_invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_json_repair_timeout_does_not_enter_another_repair(monkeypatch):
    from service.review.orchestrator import json_utils as module
    from model.multi_stage import FileReviewBatchOutput
    invoke = AsyncMock(side_effect=TimeoutError("repair response lost"))
    repair = AsyncMock()
    monkeypatch.setattr(module, "supports_structured_output", lambda _: True)
    monkeypatch.setattr(module, "invoke_structured_output", invoke)
    monkeypatch.setattr(module, "repair_json_with_llm", repair)
    with pytest.raises(TimeoutError):
        await module.parse_llm_response("invalid JSON", FileReviewBatchOutput, MagicMock())
    invoke.assert_awaited_once()
    repair.assert_not_awaited()
