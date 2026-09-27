"""Public bound-reader graph operations.

Adapted portions retain their MIT attribution in this package's NOTICE.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .projection import (
    _compact_relation,
    _compact_unit,
)
from .source import (
    _bounded_source_windows,
)
from .lookup import (
    _ambiguous_target_response,
    _query_page,
    _resolve_targets,
)
from .walk import (
    _walk,
)
from .response import (
    _walk_response,
)


def traverse_review_graph(
    reader,
    *,
    start: str,
    strategy: str = "bfs",
    direction: str = "both",
    relation_kinds: Sequence[str] = (),
    changed_paths: Sequence[str] = (),
    max_depth: int = 3,
    max_results: int = 100,
    token_budget: int | None = None,
    detail_level: str = "standard",
    include_source: bool = False,
    max_source_windows: int = 6,
    max_source_characters: int = 12000,
) -> dict[str, Any]:
    """Traverse exact AST/plugin relations using deterministic BFS or DFS."""

    # The API applies the same cardinality limit. Keep the algorithm itself
    # robust for direct callers and prevent invalid, arbitrarily long filter
    # labels from dominating the bounded response envelope.
    bounded_relation_kinds = tuple(dict.fromkeys(
        str(kind).strip()[:128]
        for kind in relation_kinds[:50]
        if str(kind).strip()
    ))

    roots, unresolved, root_truncated, ambiguous = _resolve_targets(
        reader,
        [start],
        max_results=1,
    )
    if ambiguous:
        return _ambiguous_target_response(
            reader,
            operation="traverse_review_graph",
            targets=[start],
            ambiguous=ambiguous,
            include_source=include_source,
        )
    walked = _walk(
        reader,
        roots,
        strategy=strategy,
        direction=direction,
        relation_kinds=bounded_relation_kinds,
        max_depth=max_depth,
        max_results=max_results,
    )
    return _walk_response(
        reader,
        operation="traverse_review_graph",
        targets=[start],
        roots=roots,
        unresolved=unresolved,
        root_truncated=root_truncated,
        walked=walked,
        changed_paths=changed_paths,
        max_depth=max_depth,
        max_results=max_results,
        token_budget=token_budget,
        detail_level=detail_level,
        include_source=include_source,
        max_source_windows=max_source_windows,
        max_source_characters=max_source_characters,
        extra={
            "strategy": strategy,
            "direction": direction,
            "relationKinds": list(bounded_relation_kinds),
        },
    )


def query_review_graph(
    reader,
    *,
    pattern: str,
    target: str,
    changed_paths: Sequence[str] = (),
    max_results: int = 25,
    cursor: int = 0,
    detail_level: str = "standard",
    include_source: bool = False,
    max_source_windows: int = 6,
    max_source_characters: int = 12000,
) -> dict[str, Any]:
    """Run one exact proposed-tree query, preserving full facts in standard mode."""

    exact = _query_page(
        reader,
        pattern,
        target,
        max_results=max_results,
        cursor=cursor,
    )
    if exact.get("status") == "error":
        return {
            **dict(exact),
            "operation": "query_review_graph",
            "snapshot": reader.snapshot(),
        }
    if exact.get("status") == "ambiguous":
        source_coverage = {
            "state": "unavailable" if include_source else "not_requested",
            "truncated": False,
            "availableSourceUnits": 0,
            "returnedSourceUnits": 0,
            "omittedSourceUnits": 0,
            "truncatedSourceWindows": 0,
        }
        return {
            **dict(exact),
            "operation": "query_review_graph",
            "snapshot": reader.snapshot(),
            "sourceWindows": [],
            "coverage": {
                "state": "bounded",
                "truncated": True,
                "partialReasons": ["ambiguous_target"],
                "maxResults": max_results,
                "returnedResults": 0,
                "sourceIncluded": include_source,
                "source": source_coverage,
            },
        }
    values = [item for item in exact.get("results") or () if isinstance(item, Mapping)]
    resolved_units = [
        dict(item)
        for item in exact.get("resolvedUnits") or ()
        if isinstance(item, Mapping)
    ]
    unit_results = pattern.strip().casefold() in {
        "symbol_search", "symbols", "file_summary",
    }
    units: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    if unit_results:
        units = [dict(item) for item in values]
    else:
        relations = [dict(item) for item in values]
        for relation in relations:
            for endpoint in (relation.get("sourceUnit"), relation.get("targetUnit")):
                if isinstance(endpoint, Mapping):
                    units.append(dict(endpoint))
    windows, source_coverage = (
        _bounded_source_windows(
            reader,
            units,
            relations,
            changed_paths=changed_paths,
            max_source_windows=max_source_windows,
            max_source_characters=max_source_characters,
        )
        if include_source
        else ([], {
            "state": "not_requested",
            "truncated": False,
            "availableSourceUnits": 0,
            "returnedSourceUnits": 0,
            "omittedSourceUnits": 0,
            "truncatedSourceWindows": 0,
        })
    )
    if detail_level == "minimal":
        response_results = (
            [_compact_unit(item, detail_level="minimal") for item in units]
            if unit_results
            else [_compact_relation(item, detail_level="minimal") for item in relations]
        )
    else:
        response_results = values
    response_resolved_units = (
        [
            _compact_unit(item, detail_level="minimal")
            for item in resolved_units
        ]
        if detail_level == "minimal"
        else resolved_units
    )
    partial_reasons = []
    if exact.get("truncated"):
        partial_reasons.append("result_limit")
    if source_coverage.get("truncated"):
        partial_reasons.append("source_window_limit")
    return {
        "status": "ready",
        "operation": "query_review_graph",
        "snapshot": reader.snapshot(),
        "pattern": exact.get("pattern") or pattern,
        "target": target,
        "cursor": int(exact.get("cursor") or cursor),
        "nextCursor": exact.get("nextCursor"),
        "resolvedUnits": response_resolved_units,
        "results": response_results,
        "sourceWindows": windows,
        "coverage": {
            "state": "bounded" if partial_reasons else "complete",
            "truncated": bool(partial_reasons),
            "partialReasons": partial_reasons,
            "maxResults": max_results,
            "returnedResults": len(response_results),
            "sourceIncluded": include_source,
            "source": source_coverage,
        },
    }


def get_review_structural_unit(
    reader,
    *,
    unit_id: str,
    offset: int = 0,
    max_characters: int = 12000,
) -> dict[str, Any] | None:
    """Read one bounded exact-content window from an AST or plugin unit."""

    detail = reader.get_unit(unit_id)
    if detail is None:
        return None
    response = dict(detail)
    unit = response.get("unit")
    if isinstance(unit, Mapping):
        bounded_unit = dict(unit)
        content = bounded_unit.get("content")
        if isinstance(content, str):
            offset = max(0, int(offset))
            max_characters = max(1, min(int(max_characters), 60000))
            total_characters = len(content)
            bounded_offset = min(offset, total_characters)
            end_offset = min(
                total_characters,
                bounded_offset + max_characters,
            )
            bounded_unit["content"] = content[bounded_offset:end_offset]
            bounded_unit["contentWindow"] = {
                "offset": bounded_offset,
                "endOffset": end_offset,
                "totalCharacters": total_characters,
                "truncated": end_offset < total_characters,
                "nextOffset": end_offset if end_offset < total_characters else None,
            }
            response["unit"] = bounded_unit
    return {
        **response,
        "status": "ready",
        "operation": "get_review_structural_unit",
        # An unchanged unit may physically come from the sealed base reader, but
        # the observable snapshot is always the composed proposed-tree session.
        "snapshot": reader.snapshot(),
    }
