"""Request-scoped MCP adapter for the structural repository graph."""

from __future__ import annotations

import json
import os
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from service.rag.rag_client import RagClient


server = FastMCP(
    "CodeCrow structural repository graph",
    instructions=(
        "Begin an eligible Stage 1 review with the required compact proposed-tree "
        "context operation. Choose the smallest focused continuation that answers "
        "the concrete question: impact radius, a named "
        "query, bounded traversal, or an exact structural unit. Exact unit or "
        "source-window content is preferred over graph summaries; use the "
        "review file tool for unrepresented code or needed whole-file bytes. "
        "Empty or bounded graph results are not proof that code is absent. "
        "Repository paths, revision identity, and tenant identity are supplied "
        "by the host and cannot be changed by tool arguments."
    ),
    log_level="WARNING",
)


def _context(name: str) -> str | None:
    value = os.environ.get(f"CODECROW_RAG_MCP_{name}")
    return value.strip() if value and value.strip() else None


def _binding() -> dict[str, Any]:
    return {
        "workspace": _context("WORKSPACE") or "",
        "project": _context("PROJECT") or "",
        "branch": _context("BRANCH") or "",
        "repository_revision": _context("REVISION"),
        "repository_generation_manifest_sha256": _context("MANIFEST"),
        "collection_target": _context("COLLECTION_TARGET"),
    }


def _json_string_list(name: str) -> list[str] | None:
    encoded = _context(name)
    if not encoded:
        return None
    try:
        values = json.loads(encoded)
    except (TypeError, ValueError):
        return None
    if not isinstance(values, list):
        return None
    normalized = [
        str(value).strip()
        for value in values
        if isinstance(value, str) and value.strip()
    ]
    return normalized or None


def _review_binding() -> dict[str, Any]:
    binding: dict[str, Any] = {
        "workspace": _context("WORKSPACE") or "",
        "project": _context("PROJECT") or "",
        "target_branch": _context("BRANCH") or "",
        "base_revision": _context("REVISION") or "",
        "source_revision": _context("SOURCE_REVISION") or "",
        "target_repo_path": _context("TARGET_REPO_PATH") or "",
        "review_overlay_path": _context("REVIEW_OVERLAY_PATH") or "",
        "base_collection_target": _context("COLLECTION_TARGET"),
        "base_generation_manifest_sha256": _context("MANIFEST"),
        "review_collection_target": _context("REVIEW_COLLECTION_TARGET"),
        "review_generation_manifest_sha256": _context(
            "REVIEW_GENERATION_MANIFEST_SHA256"
        ),
    }
    for key, context_name in (
        ("include_patterns", "INCLUDE_PATTERNS_JSON"),
        ("exclude_patterns", "EXCLUDE_PATTERNS_JSON"),
    ):
        values = _json_string_list(context_name)
        if values is not None:
            binding[key] = values
    for key, context_name in (
        ("project_type", "PROJECT_TYPE"),
        ("source_root", "SOURCE_ROOT"),
    ):
        value = _context(context_name)
        if value:
            binding[key] = value
    return binding


def _json_safe(result: Any) -> dict[str, Any]:
    """Keep provider-specific scalar types off the stdio transport."""
    return json.loads(json.dumps(result, ensure_ascii=False, default=str))


@server.tool(
    name="exploreReviewContext",
    description=(
        "Explore one exact sealed proposed-tree review neighborhood. The host binds "
        "the tenant, target snapshot, PR overlay, revisions, and current batch "
        "paths. Supply the concrete review question and optional symbol names. "
        "The result combines normalized graph nodes and edges through two hops, "
        "a frontier for focused continuation, tests, framework/plugin relations, "
        "and exact related-source windows. Use graph evidence to navigate and "
        "exact source to verify; bounded absence is not proof of absence."
    ),
    structured_output=True,
)
async def explore_review_context(
    question: str,
    focusPaths: list[str],
    focusSymbols: list[str] | None = None,
    maxRelations: int = 32,
    maxSourceWindows: int = 6,
    maxSourceCharacters: int = 12_000,
) -> dict[str, Any]:
    """Delegate to the proposed-tree service with host-bound identity.

    ``focusPaths`` is present in the transport schema so the shared agent
    runtime can inject it. Stage 1 removes it from the model-visible schema and
    binds the exact batch paths on every call.
    """
    client = RagClient()
    try:
        return _json_safe(await client.explore_review_context(
            focus_paths=focusPaths,
            question=question,
            focus_symbols=focusSymbols or [],
            max_relations=maxRelations,
            max_source_windows=maxSourceWindows,
            max_source_characters=maxSourceCharacters,
            **_review_binding(),
        ))
    finally:
        await client.close()


@server.tool(
    name="getMinimalReviewContext",
    description=(
        "Return a compact proposed-tree summary for one concrete review "
        "question, plus bounded nodes/edges and suggested next operations. "
        "Use it for orientation when graph context is useful but the exact "
        "relationship target is not yet known. The host binds the batch paths "
        "and complete proposed-tree identity."
    ),
    structured_output=True,
)
async def get_minimal_review_context(
    question: str,
    focusPaths: list[str],
    focusSymbols: list[str] | None = None,
    maxRelations: int = 25,
    detailLevel: str = "minimal",
    includeSource: bool = True,
    maxSourceWindows: int = 4,
    maxSourceCharacters: int = 8_000,
) -> dict[str, Any]:
    client = RagClient()
    try:
        return _json_safe(await client.minimal_review_context(
            focus_paths=focusPaths,
            question=question,
            focus_symbols=focusSymbols or [],
            max_relations=maxRelations,
            detail_level=detailLevel,
            include_source=includeSource,
            max_source_windows=maxSourceWindows,
            max_source_characters=maxSourceCharacters,
            **_review_binding(),
        ))
    finally:
        await client.close()


@server.tool(
    name="getImpactRadius",
    description=(
        "Trace bounded weighted dependents and tests affected by the host-bound "
        "changed paths, or by precise optional targets, in the exact proposed "
        "tree. Use this when the review question is about blast radius."
    ),
    structured_output=True,
)
async def get_impact_radius(
    focusPaths: list[str],
    targets: list[str] | None = None,
    maxDepth: int = 2,
    maxResults: int = 100,
    detailLevel: str = "standard",
    includeSource: bool = True,
    maxSourceWindows: int = 6,
    maxSourceCharacters: int = 12_000,
) -> dict[str, Any]:
    client = RagClient()
    try:
        return _json_safe(await client.review_impact_radius(
            focus_paths=focusPaths,
            targets=targets or [],
            max_depth=maxDepth,
            max_results=maxResults,
            detail_level=detailLevel,
            include_source=includeSource,
            max_source_windows=maxSourceWindows,
            max_source_characters=maxSourceCharacters,
            **_review_binding(),
        ))
    finally:
        await client.close()


@server.tool(
    name="traverseCodeGraph",
    description=(
        "Run a bounded BFS or DFS from one precise symbol, unit ID, or path in "
        "the exact proposed tree. Prefer queryCodeGraph for a known named "
        "relationship; use traversal when a multi-hop neighborhood is required."
    ),
    structured_output=True,
)
async def traverse_code_graph(
    start: str,
    focusPaths: list[str],
    direction: str = "both",
    strategy: str = "bfs",
    relationKinds: list[Annotated[str, Field(max_length=128)]] | None = None,
    maxDepth: int = 3,
    maxResults: int = 100,
    tokenBudget: Annotated[int, Field(ge=512, le=16_000)] = 2_000,
    detailLevel: str = "standard",
    includeSource: bool = True,
    maxSourceWindows: int = 6,
    maxSourceCharacters: int = 12_000,
) -> dict[str, Any]:
    client = RagClient()
    try:
        return _json_safe(await client.traverse_review_graph(
            focus_paths=focusPaths,
            start=start,
            direction=direction,
            strategy=strategy,
            relation_kinds=relationKinds or [],
            max_depth=maxDepth,
            max_results=maxResults,
            token_budget=tokenBudget,
            detail_level=detailLevel,
            include_source=includeSource,
            max_source_windows=maxSourceWindows,
            max_source_characters=maxSourceCharacters,
            **_review_binding(),
        ))
    finally:
        await client.close()


@server.tool(
    name="getStructuralRelations",
    description=(
        "Return a compact relation neighborhood for exact repository paths. "
        "The result contains AST/plugin unit names, kinds, paths, line ranges, "
        "relation kinds, and unit IDs without bulk source chunks. Use it to "
        "identify a concrete related unit, then inspect that unit or its file."
    ),
    structured_output=True,
)
async def get_structural_relations(
    paths: list[str],
    maxRelations: int = 80,
) -> dict[str, Any]:
    client = RagClient()
    try:
        return _json_safe(await client.get_structural_relations(
            paths=paths,
            max_relations=maxRelations,
            **_binding(),
        ))
    finally:
        await client.close()


@server.tool(
    name="queryCodeGraph",
    description=(
        "Traverse one named structural relation around an exact unit ID, "
        "qualified symbol, or repository path. pattern must be one of "
        "callers_of, callees_of, references_to, imports_of, importers_of, "
        "children_of, tests_for, inheritors_of, triggers_of, triggered_by, "
        "publishers_of, listeners_of, handlers_of, endpoints_for, "
        "consumers_of, relations_of, "
        "framework_relations, symbol_search, or file_summary. Use a precise "
        "target; bounded absence is not proof that code is absent. If the "
        "response is ambiguous, inspect candidateCount, follow nextCursor only "
        "when another candidate page is needed, then retry with an exact "
        "candidate unit ID. Successful named responses expose resolvedUnits."
    ),
    structured_output=True,
)
async def query_code_graph(
    pattern: str,
    target: str,
    focusPaths: list[str],
    maxResults: int = 25,
    cursor: int = 0,
    detailLevel: str = "standard",
    includeSource: bool = True,
    maxSourceWindows: int = 6,
    maxSourceCharacters: int = 12_000,
) -> dict[str, Any]:
    client = RagClient()
    try:
        return _json_safe(await client.query_review_graph(
            pattern=pattern,
            target=target,
            focus_paths=focusPaths,
            max_results=maxResults,
            cursor=cursor,
            detail_level=detailLevel,
            include_source=includeSource,
            max_source_windows=maxSourceWindows,
            max_source_characters=maxSourceCharacters,
            **_review_binding(),
        ))
    finally:
        await client.close()


@server.tool(
    name="getStructuralUnit",
    description=(
        "Open one structural unit by a unit ID returned by a proposed-tree graph "
        "operation. The unit includes exact proposed-tree source when represented "
        "and reports sourceEvidence accordingly. Use getReviewFileContent only "
        "for unrepresented code, needed whole-file bytes, or a missing range."
    ),
    structured_output=True,
)
async def get_structural_unit(
    unitId: str,
    focusPaths: list[str],
    offset: int = 0,
    maxCharacters: int = 12_000,
) -> dict[str, Any]:
    client = RagClient()
    try:
        return _json_safe(await client.get_review_structural_unit(
            unit_id=unitId,
            focus_paths=focusPaths,
            offset=offset,
            max_characters=maxCharacters,
            **_review_binding(),
        ))
    finally:
        await client.close()


if __name__ == "__main__":
    server.run("stdio")
