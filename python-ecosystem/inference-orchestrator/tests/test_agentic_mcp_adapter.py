"""MCP protocol contracts for the execution-scoped agentic tools."""

from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("mcp", reason="MCP SDK is a declared runtime dependency")
from mcp.shared.memory import create_connected_server_and_client_session

from model.dtos import ReviewRequestDto
from service.review.agentic.engine import AgenticReviewEngine
from service.review.agentic.mcp_adapter import AgenticMcpAdapter
from service.review.agentic.tool_gateway import AgenticToolError
from utils.diff_processor import DiffProcessor


_DEFINITIONS = [
    {
        "name": "rag_search",
        "description": "Search the execution-bound RAG snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path_pattern": {"type": "string", "default": "*"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }
]


class _BoundedGateway:
    snapshot_id = "snapshot-1"

    def __init__(self) -> None:
        self.invoke = AsyncMock(side_effect=self._invoke)

    @staticmethod
    def tool_definitions():
        return deepcopy(_DEFINITIONS)

    @staticmethod
    def langchain_tool_definitions():
        return [
            {
                "type": "function",
                "function": {
                    "name": definition["name"],
                    "description": definition["description"],
                    "parameters": deepcopy(definition["inputSchema"]),
                },
            }
            for definition in _DEFINITIONS
        ]

    async def _invoke(self, name, arguments):
        if set(arguments) - {"query", "path_pattern"}:
            raise AgenticToolError(
                "INVALID_ARGUMENTS", "invalid arguments for rag_search"
            )
        return {
            "tool": name,
            "results": [{"path": "src/payments.py", "start_line": 12}],
        }

    @property
    def telemetry_summary(self):
        return {"calls_used": self.invoke.await_count}

    @property
    def receipts(self):
        return []

    def validate_proof(self, proof, *, expected_path=None):
        return proof == {"valid": True} and expected_path == "src/payments.py"


@pytest.mark.asyncio
async def test_mcp_list_tools_publishes_only_model_supplied_arguments():
    bounded = _BoundedGateway()
    adapter = AgenticMcpAdapter(bounded)

    async with create_connected_server_and_client_session(
        adapter.server, raise_exceptions=True
    ) as session:
        listed = await session.list_tools()

    assert [tool.name for tool in listed.tools] == ["rag_search"]
    schema = listed.tools[0].inputSchema
    assert set(schema["properties"]) == {"query", "path_pattern"}
    assert schema["additionalProperties"] is False
    serialized = repr(listed).lower()
    for injected_coordinate in (
        "execution_id",
        "repository",
        "revision",
        "snapshot_id",
        "workspace",
        "head_sha",
    ):
        assert injected_coordinate not in serialized
    annotations = listed.tools[0].annotations
    assert annotations is not None
    assert annotations.readOnlyHint is True
    assert annotations.destructiveHint is False
    assert annotations.openWorldHint is False


@pytest.mark.asyncio
async def test_mcp_call_tool_delegates_to_the_bounded_gateway():
    bounded = _BoundedGateway()
    adapter = AgenticMcpAdapter(bounded)

    async with create_connected_server_and_client_session(
        adapter.server, raise_exceptions=True
    ) as session:
        result = await session.call_tool(
            "rag_search",
            {"query": "where is payment validation?", "path_pattern": "src/*"},
        )

    assert result.isError is False
    assert result.structuredContent == {
        "tool": "rag_search",
        "results": [{"path": "src/payments.py", "start_line": 12}],
    }
    bounded.invoke.assert_awaited_once_with(
        "rag_search",
        {"query": "where is payment validation?", "path_pattern": "src/*"},
    )


@pytest.mark.asyncio
async def test_mcp_call_cannot_override_injected_execution_coordinates():
    bounded = _BoundedGateway()
    adapter = AgenticMcpAdapter(bounded)

    async with create_connected_server_and_client_session(
        adapter.server, raise_exceptions=True
    ) as session:
        result = await session.call_tool(
            "rag_search",
            {
                "query": "validation",
                "workspace": "attacker/repository",
                "revision": "0" * 40,
            },
        )

    assert result.isError is True
    assert result.structuredContent == {
        "error": {
            "code": "INVALID_ARGUMENTS",
            "message": "invalid arguments for rag_search",
        }
    }
    bounded.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_gateway_api_routes_definition_and_call_through_mcp():
    bounded = _BoundedGateway()
    adapter = AgenticMcpAdapter(bounded)

    definitions = await adapter.mcp_tool_definitions()
    result = await adapter.invoke("rag_search", {"query": "validation"})

    assert definitions == _DEFINITIONS
    assert result["tool"] == "rag_search"
    assert adapter.langchain_tool_definitions()[0]["function"]["name"] == "rag_search"
    assert adapter.telemetry_summary == {"calls_used": 1}
    assert adapter.receipts == []
    assert adapter.validate_proof(
        {"valid": True}, expected_path="src/payments.py"
    ) is True


@pytest.mark.asyncio
async def test_agentic_engine_lists_and_calls_tools_through_mcp_adapter():
    bounded = _BoundedGateway()
    adapter = AgenticMcpAdapter(bounded)
    adapter.mcp_tool_definitions = AsyncMock(
        wraps=adapter.mcp_tool_definitions
    )

    class _BoundModel:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    content="",
                    tool_calls=[
                        {
                            "id": "rag-1",
                            "name": "rag_search",
                            "args": {"query": "payment validation"},
                        }
                    ],
                )
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "comment": "reviewed",
                        "reviewedWorkItemIds": [],
                        "unreviewableWorkItems": [],
                        "findings": [],
                        "previousFindingDecisions": [],
                    }
                ),
                tool_calls=[],
            )

    class _Model:
        def __init__(self):
            self.bound = _BoundModel()
            self.tools = None

        def bind_tools(self, tools):
            self.tools = tools
            return self.bound

    model = _Model()
    request = ReviewRequestDto.model_construct(
        rawDiff="",
        changedFiles=[],
        previousCodeAnalysisIssues=[],
        pullRequestId=17,
    )
    engine = AgenticReviewEngine(
        llm=model,
        gateway=adapter,
        request=request,
        processed_diff=DiffProcessor().process(""),
    )

    result = await engine._run_tool_loop([], previous_findings=[])

    assert result.comment == "reviewed"
    assert model.tools == [
        {
            "type": "function",
            "function": {
                "name": "rag_search",
                "description": "Search the execution-bound RAG snapshot.",
                "parameters": _DEFINITIONS[0]["inputSchema"],
            },
        }
    ]
    adapter.mcp_tool_definitions.assert_awaited_once_with()
    bounded.invoke.assert_awaited_once_with(
        "rag_search", {"query": "payment validation"}
    )
