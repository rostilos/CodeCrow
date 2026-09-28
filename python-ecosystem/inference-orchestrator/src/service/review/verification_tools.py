"""Request-bound, read-only MCP tools for final review verification.

Local source does not depend on the graph service. Every graph request retains
host-owned tenant and snapshot identity; models choose only evidence targets.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Literal, Mapping, Sequence

from service.review.change_context import anchor_ranges
from service.review.local_source import LocalReviewSource
from service.review.navigation_context import NavigationContext

logger = logging.getLogger(__name__)


_GRAPH_TOOLS = frozenset({
    "queryCodeGraph", "getStructuralUnit", "getMinimalReviewContext",
    "getImpactRadius", "traverseCodeGraph",
})

_GRAPH_NAVIGATION_TOOLS = _GRAPH_TOOLS - {"getStructuralUnit"}


class VerificationTools:
    """One request's MCP inventory, source cache, and execution diagnostics."""

    def __init__(self, *, rag_client: Any, binding: Mapping[str, Any], parts: Sequence[Any],
                 context_parts: Sequence[Any] = (), focus_paths: Sequence[str] = ()):
        self.rag_client = rag_client
        self.binding = dict(binding)
        self.focus_paths = list(focus_paths)
        self.parts = {part.id: part for part in (*context_parts, *parts)}
        self.source = LocalReviewSource(binding, [part.path for part in self.parts.values()])
        self.cache: dict[str, dict[str, Any]] = {}
        self.navigation = NavigationContext()
        self.server: Any = None
        self.diagnostics: list[str] = []

    def _focus(self, paths: Sequence[str] = ()) -> list[str]:
        return list(dict.fromkeys(paths)) if paths else (self.focus_paths or sorted({part.path for part in self.parts.values()}))

    async def _graph(self, method: str, *, paths: Sequence[str] = (), **arguments: Any) -> dict[str, Any]:
        if not self.binding.get("review_collection_target") or self.rag_client is None:
            return {"status": "unavailable", "diagnostic": "Proposed-tree graph is unavailable; use local read and grep tools."}
        binding = {**self.binding, "focus_paths": self._focus(paths)}
        return await getattr(self.rag_client, method)(**binding, **arguments)

    @staticmethod
    def _source_identity(result: dict[str, Any]) -> dict[str, Any]:
        content = result.get("content")
        if isinstance(content, str):
            result = {**result, "contentSha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
        return result

    async def _complete_unit(self, unit_id: str) -> dict[str, Any]:
        result = await self._graph("get_review_structural_unit", unit_id=unit_id)
        unit = result.get("unit")
        if result.get("status") != "ready" or not isinstance(unit, dict):
            return result
        # A unit request means a complete semantic unit. Prefer exact local
        # bytes when the unit has a file range, independently of graph paging.
        path = unit.get("path")
        start, end = unit.get("startLine"), unit.get("endLine")
        derived_context = unit.get("recordType") == "plugin_context"
        whole_file = unit.get("recordType") in {"plugin_context", "structural_file", "plugin_symbol"}
        if path and (whole_file or isinstance(start, int) and isinstance(end, int) and 0 < start <= end):
            source = await asyncio.to_thread(self.source.read, path,
                                            start_line=1 if whole_file else start,
                                            end_line=None if whole_file else end)
            if source.get("status") == "ready":
                return {**self._source_identity(source), "unit": {
                    key: value for key, value in unit.items() if key not in {"content", "contentWindow"}
                }, "sourceEvidence": True}
        if not result.get("sourceEvidence") or not isinstance(unit.get("content"), str):
            return result
        content = unit["content"]
        window = unit.get("contentWindow") or {}
        next_offset = window.get("nextOffset")
        offset = int(window.get("offset") or 0)
        while next_offset is not None:
            if not isinstance(next_offset, int) or next_offset <= offset:
                return {"status": "unavailable", "unitId": unit_id,
                        "diagnostic": "Graph source continuation did not advance; read the local file instead."}
            offset = next_offset
            page = await self._graph("get_review_structural_unit", unit_id=unit_id, offset=offset)
            following = page.get("unit") or {}
            if (page.get("status") != "ready" or not page.get("sourceEvidence")
                    or following.get("unitId") != unit.get("unitId")
                    or following.get("contentSha256") != unit.get("contentSha256")
                    or not isinstance(following.get("content"), str)
                    or (following.get("contentWindow") or {}).get("offset") != offset):
                return {"status": "unavailable", "unitId": unit_id,
                        "diagnostic": "Complete graph source is unavailable; read the local file instead."}
            content += following["content"]
            next_offset = (following.get("contentWindow") or {}).get("nextOffset")
        if derived_context:
            return {"status": "ready", "sourceEvidence": False,
                    "unit": {key: value for key, value in unit.items() if key not in {"content", "contentWindow"}},
                    "context": content,
                    "diagnostic": "Derived plugin context is navigation, not exact file source; read referenced files before deciding."}
        return {"status": "ready", "side": "proposed", "origin": "sealed_graph",
                "path": path, "startLine": start, "endLine": end, "sourceEvidence": True,
                "unit": {key: value for key, value in unit.items() if key not in {"content", "contentWindow"}},
                **self._source_identity({"content": content})}

    def _server(self) -> Any:
        if self.server is not None:
            return self.server
        from mcp.server.fastmcp import FastMCP
        server = FastMCP("CodeCrow review verification", log_level="WARNING")

        @server.tool(name="queryCodeGraph", structured_output=True)
        async def query_code_graph(pattern: str, target: str, cursor: int = 0) -> dict[str, Any]:
            """Navigate a precise symbol/unit/path. Patterns: callers_of, callees_of, references_to, imports_of, importers_of, children_of, tests_for, inheritors_of, triggers_of, triggered_by, publishers_of, listeners_of, handlers_of, endpoints_for, consumers_of, relations_of, framework_relations, symbol_search, file_summary. Read resolved source separately; follow nextCursor for relevant remaining results. Empty/partial graphs do not prove absence. Navigation uses case-local unit@N handles directly usable in graph tools and getStructuralUnit. Repeated metadata appears once in unitDefinitions; unitRef selects its record."""
            return await self._graph("query_review_graph", pattern=pattern, target=target,
                                     cursor=cursor, include_source=False)

        @server.tool(name="getStructuralUnit", structured_output=True)
        async def get_structural_unit(unitId: str) -> dict[str, Any]:
            """Read one complete definition by a graph-provided unit ID. Returns exact source and its path/range when available. No source content is clipped."""
            return await self._complete_unit(unitId)

        @server.tool(name="getMinimalReviewContext", structured_output=True)
        async def get_minimal_review_context(question: str, paths: list[str],
                                             focusSymbols: list[str] | None = None) -> dict[str, Any]:
            """Orient a specific cross-file question over relevant paths/symbols. Returns navigation metadata; read exact definitions separately. Coverage describes omitted or unresolved relationships."""
            return await self._graph("minimal_review_context", paths=paths, question=question,
                                     focus_symbols=focusSymbols or [], include_source=False)

        @server.tool(name="getImpactRadius", structured_output=True)
        async def get_impact_radius(targets: list[str], maxDepth: int = 2,
                                    maxResults: int = 100) -> dict[str, Any]:
            """Find dependent callers/tests for precise symbols or paths. Use coverage/frontier for further navigation; inspect exact source before deciding. Depth/results control graph navigation, never source clipping."""
            return await self._graph("review_impact_radius", targets=targets, max_depth=maxDepth,
                                     max_results=maxResults, include_source=False)

        @server.tool(name="traverseCodeGraph", structured_output=True)
        async def traverse_code_graph(start: str, direction: str = "both",
                                      relationKinds: list[str] | None = None,
                                      maxDepth: int = 3, maxResults: int = 100) -> dict[str, Any]:
            """Navigate a multi-hop relationship when a named query is insufficient. Start at a precise unit/symbol/path; returned frontier and coverage identify incomplete branches. Read source separately."""
            return await self._graph("traverse_review_graph", start=start, direction=direction,
                                     relation_kinds=relationKinds or [], max_depth=maxDepth,
                                     max_results=maxResults, token_budget=None, include_source=False)

        @server.tool(name="readReviewFile", structured_output=True)
        async def read_review_file(path: str, startLine: int = 1, endLine: int | None = None,
                                   side: str = "proposed") -> dict[str, Any]:
            """Read exact local source for a complete definition/range; omit endLine for whole-file context. Proposed reads apply the PR overlay. Target reads inspect captured target HEAD, not necessarily merge base. No content clipping."""
            return self._source_identity(await asyncio.to_thread(self.source.read, path, side=side,
                                           start_line=startLine, end_line=endLine))

        @server.tool(name="grepReviewCode", structured_output=True)
        async def grep_review_code(query: str, mode: Literal["literal", "regex"], paths: list[str] | None = None,
                                   caseSensitive: bool = True, side: str = "proposed") -> dict[str, Any]:
            """Find source occurrences using an explicit literal or regex mode (regex supports multiline anchors and alternation). Return every matching line with its full exact text; read enclosing definitions separately. Use findReviewFiles for filename lookup. Invalid patterns are unavailable; interrupted/partial searches cannot prove absence."""
            return await asyncio.to_thread(self.source.grep, query, paths=paths or (),
                                           side=side, case_sensitive=caseSensitive, mode=mode)

        @server.tool(name="findReviewFiles", structured_output=True)
        async def find_review_files(pattern: str, paths: list[str] | None = None,
                                    side: str = "proposed") -> dict[str, Any]:
            """Find all snapshot file paths by glob, without reading their contents. A basename pattern such as '*home-logo*' matches at any depth; slash patterns are repository-relative and '**' matches any directory depth. Optional paths restrict exact files/directories. Proposed lookup applies additions/deletions; partial results cannot prove absence."""
            return await asyncio.to_thread(self.source.find_files, pattern, paths=paths or (), side=side)

        @server.tool(name="getReviewDiff", structured_output=True)
        async def get_review_diff(partIds: list[str] | None = None,
                                  paths: list[str] | None = None) -> dict[str, Any]:
            """Read complete changed hunks by supplied part IDs or exact repository paths, including inclusive anchorRanges. Both selectors form a union; request a relevant changed path rather than the whole PR."""
            part_ids = list(dict.fromkeys(partIds or []))
            requested_paths = set(paths or [])
            if not part_ids and not requested_paths:
                return {"status": "unavailable", "parts": [], "missingPartIds": [], "missingPaths": [],
                        "diagnostic": "Supply relevant changed paths or partIds to read complete hunks."}
            selected = {part_id for part_id in part_ids if part_id in self.parts}
            selected.update(part.id for part in self.parts.values() if part.path in requested_paths)
            return {"status": "ready", "parts": [
                {"id": part.id, "path": part.path, "side": part.side,
                 "anchorRanges": anchor_ranges(part.anchors), "diff": part.diff}
                for part in self.parts.values() if part.id in selected
            ], "missingPartIds": [part_id for part_id in part_ids if part_id not in self.parts],
                "missingPaths": sorted(requested_paths - {part.path for part in self.parts.values()})}

        self.server = server
        return server

    async def schemas(self) -> list[dict[str, Any]]:
        return [{"name": tool.name, "description": tool.description,
                 "inputSchema": tool.inputSchema} for tool in await self._server().list_tools()]

    async def native_schemas(self) -> list[dict[str, Any]]:
        from service.review.agent_calls import native_tool_definitions
        return native_tool_definitions(await self.schemas())

    @staticmethod
    def _log_result(name: str, result: Mapping[str, Any], *, cache_hit: bool, started: float) -> None:
        source_characters = len(result["content"]) if isinstance(result.get("content"), str) else 0
        entries = sum(len(result[key]) for key in ("results", "nodes", "edges", "parts", "candidates", "resolvedUnits", "paths")
                      if isinstance(result.get(key), list))
        logger.info("Review tool completed: tool=%s status=%s cache_hit=%s source_characters=%d result_entries=%d duration_ms=%.1f",
                    name, result.get("status"), str(cache_hit).lower(), source_characters,
                    entries, (time.perf_counter() - started) * 1000)

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        arguments = self.navigation.resolve_arguments(name, arguments)
        identity: dict[str, Any] = {"name": name, "arguments": arguments}
        if name in _GRAPH_TOOLS:
            # Graph resolution incorporates host-owned case focus even when
            # the model requests the same symbol. Local reads/diffs remain
            # reusable across cases within this tenant/snapshot request.
            explicit_paths = arguments.get("paths") if name == "getMinimalReviewContext" else ()
            paths = explicit_paths if isinstance(explicit_paths, list) and all(isinstance(path, str) for path in explicit_paths) else ()
            identity["focusPaths"] = self._focus(paths)
        key = json.dumps(identity, sort_keys=True)
        if key in self.cache:
            self._log_result(name, self.cache[key], cache_hit=True, started=started)
            return self._present(name, self.cache[key])
        try:
            result = await self._server().call_tool(name, arguments)
            if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
                result = result[1]
            elif not isinstance(result, dict):
                result = json.loads("\n".join(item.text for item in result if getattr(item, "type", "") == "text"))
            if not isinstance(result, dict):
                raise ValueError("MCP tool returned an invalid result")
        except Exception as error:
            logger.warning("Review tool failed: tool=%s error=%s", name, error, exc_info=True)
            result = {"status": "unavailable", "diagnostic": f"{name} failed ({type(error).__name__}); check the tool name/arguments or use another evidence route."}
        if result.get("status") in {"unavailable", "partial"}:
            message = f"{name}: {result.get('diagnostic') or result.get('error') or result.get('status')}"
            if message not in self.diagnostics:
                self.diagnostics.append(message)
            logger.info("Review tool observation: tool=%s status=%s diagnostic=%s",
                        name, result.get("status"), result.get("diagnostic") or result.get("error"))
        self._log_result(name, result, cache_hit=False, started=started)
        if result.get("status") in {"ready", "deleted", "missing", "binary", "ambiguous"}:
            # Outages/partial reads are observations, not immutable snapshot
            # facts. A later attempt may recover after source/service recovery.
            self.cache[key] = result
        return self._present(name, result)

    def _present(self, name: str, result: dict[str, Any]) -> dict[str, Any]:
        # Cache contains original IDs, safe to reuse across cases. Each case
        # owns its short-handle registry and can navigate every displayed node.
        return self.navigation.project(result) if name in _GRAPH_NAVIGATION_TOOLS else result
