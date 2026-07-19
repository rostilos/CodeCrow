"""Execution-scoped MCP server for the bounded agentic review gateway.

The adapter uses the official MCP SDK's in-memory transport.  It therefore
exercises real ``tools/list`` and ``tools/call`` protocol routes without adding
a child process, listening socket, or a second source of repository state.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping, Optional

from service.review.agentic.tool_gateway import AgenticToolError


_SERVER_NAME = "codecrow-agentic-review"
_SERVER_VERSION = "1"


class AgenticMcpAdapter:
    """Expose one immutable review gateway over an in-process MCP session.

    The bounded gateway remains the security authority.  MCP schemas contain
    only model-supplied search intent; repository and execution coordinates stay
    captured inside that gateway and cannot be supplied through the protocol.
    """

    def __init__(self, bounded_gateway: Any) -> None:
        # Keep SDK loading local so pure-logic tooling can import the review
        # package without eagerly loading optional provider/runtime dependencies.
        from mcp import types
        from mcp.server.lowlevel import Server
        from mcp.shared.memory import create_connected_server_and_client_session

        self._gateway = bounded_gateway
        self._types = types
        self._create_connected_session = create_connected_server_and_client_session
        self.server = Server(
            _SERVER_NAME,
            version=_SERVER_VERSION,
            instructions=(
                "Read-only tools for one immutable pull-request review execution."
            ),
        )
        self._register_routes()

    def _register_routes(self) -> None:
        types = self._types

        @self.server.list_tools()
        async def list_tools():
            return [
                types.Tool(
                    name=definition["name"],
                    description=definition.get("description"),
                    inputSchema=deepcopy(
                        definition.get("inputSchema", {"type": "object"})
                    ),
                    annotations=types.ToolAnnotations(
                        readOnlyHint=True,
                        destructiveHint=False,
                        idempotentHint=True,
                        openWorldHint=False,
                    ),
                )
                for definition in self._gateway.tool_definitions()
            ]

        # Input validation remains in AgenticToolGateway so direct and MCP calls
        # have identical budget accounting and stable AgenticToolError codes.
        @self.server.call_tool(validate_input=False)
        async def call_tool(
            name: str, arguments: Optional[dict[str, Any]]
        ):
            try:
                return await self._gateway.invoke(name, arguments or {})
            except AgenticToolError as error:
                return self._error_result(error.code, str(error))
            except Exception:
                return self._error_result(
                    "TOOL_FAILURE", "agentic review tool failed safely"
                )

    def _error_result(self, code: str, message: str):
        types = self._types
        payload = {"error": {"code": code, "message": message}}
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                )
            ],
            structuredContent=payload,
            isError=True,
        )

    async def mcp_tool_definitions(self) -> list[dict[str, Any]]:
        """List tool schemas through the MCP ``tools/list`` route."""

        async with self._create_connected_session(
            self.server, raise_exceptions=True
        ) as session:
            result = await session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": deepcopy(tool.inputSchema),
            }
            for tool in result.tools
        ]

    async def invoke(
        self, tool_name: str, arguments: Optional[Mapping[str, Any]] = None
    ) -> dict[str, Any]:
        """Invoke a bounded tool through the MCP ``tools/call`` route."""

        async with self._create_connected_session(
            self.server, raise_exceptions=True
        ) as session:
            result = await session.call_tool(tool_name, dict(arguments or {}))
        structured = result.structuredContent
        if result.isError:
            error = structured.get("error", {}) if isinstance(structured, dict) else {}
            code = error.get("code", "MCP_TOOL_ERROR")
            message = error.get("message", "agentic MCP tool call failed safely")
            raise AgenticToolError(str(code), str(message))
        if not isinstance(structured, dict):
            raise AgenticToolError(
                "MCP_PROTOCOL_ERROR", "agentic MCP tool returned no structured result"
            )
        return structured

    def tool_definitions(self) -> list[dict[str, Any]]:
        """Synchronous compatibility view; runtime uses ``mcp_tool_definitions``."""

        return self._gateway.tool_definitions()

    def langchain_tool_definitions(self) -> list[dict[str, Any]]:
        """Compatibility view for callers that cannot await ``tools/list``."""

        return self._gateway.langchain_tool_definitions()

    @property
    def snapshot_id(self) -> Any:
        return self._gateway.snapshot_id

    @property
    def telemetry_summary(self) -> Any:
        return self._gateway.telemetry_summary

    @property
    def receipts(self) -> Any:
        return self._gateway.receipts

    def validate_proof(
        self, proof: Mapping[str, Any], *, expected_path: Optional[str] = None
    ) -> bool:
        return self._gateway.validate_proof(proof, expected_path=expected_path)
