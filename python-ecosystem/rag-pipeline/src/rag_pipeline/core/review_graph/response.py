"""Traversal evidence selection and response coverage accounting.

Adapted portions retain their MIT attribution in this package's NOTICE.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .constants import (
    _MAX_RETURNED_FRONTIER,
    _MIN_TRAVERSAL_TOKEN_BUDGET,
)
from .projection import (
    _compact_unit,
    _json_char_length,
    _unit_id,
)
from .source import (
    _bounded_source_windows,
    _source_evidence_by_unit,
)


from .selection import select_graph_evidence


def _walk_response(
    reader,
    *,
    operation: str,
    targets: Sequence[str],
    roots: Sequence[Mapping[str, Any]],
    unresolved: Sequence[str],
    root_truncated: bool,
    walked: Mapping[str, Any],
    changed_paths: Sequence[str],
    max_depth: int,
    max_results: int,
    token_budget: int | None,
    detail_level: str,
    include_source: bool,
    max_source_windows: int,
    max_source_characters: int,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    visited = walked["visited"]
    relations = walked["relations"]
    if token_budget is not None:
        token_budget = max(_MIN_TRAVERSAL_TOKEN_BUDGET, min(int(token_budget), 16000))
    response_character_budget = token_budget * 4 if token_budget is not None else None
    graph_character_budget = (
        None if response_character_budget is None
        else response_character_budget if not include_source
        else max(512, response_character_budget * 2 // 3)
    )

    selection = select_graph_evidence(
        visited, relations, detail_level=detail_level,
        character_budget=graph_character_budget,
    )
    selected_units = selection.units
    selected_relations = selection.relations
    token_omitted_units = selection.omitted_units
    token_omitted_relation_ids = selection.omitted_relation_ids
    unit_values = [unit for _unit_id_value, unit, _depth in selected_units]
    relation_values = [relation for relation, _depth in selected_relations]
    graph_payload_characters = selection.serialized_characters
    source_character_budget = (
        max_source_characters if response_character_budget is None else min(
            max_source_characters,
            max(0, response_character_budget - graph_payload_characters),
        )
    )
    windows, source_coverage = (
        _bounded_source_windows(
            reader,
            unit_values,
            relation_values,
            changed_paths=changed_paths,
            max_source_windows=max_source_windows,
            max_source_characters=source_character_budget,
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

    def evidence_payload_characters() -> int:
        return (
            graph_payload_characters
            + len(',"sourceWindows":')
            + _json_char_length(windows)
        )

    while (response_character_budget is not None and windows
           and evidence_payload_characters() > response_character_budget):
        overflow = evidence_payload_characters() - response_character_budget
        window = windows[-1]
        content = str(window.get("content") or "")
        if len(content) > overflow + 64:
            selected = content[:len(content) - overflow - 64]
            if "\n" in selected:
                selected = selected.rsplit("\n", 1)[0] + "\n"
            window["content"] = selected
            window["truncated"] = True
            start_line = max(1, int(window.get("startLine") or 1))
            window["endLine"] = max(
                start_line,
                start_line + selected.count("\n") - int(selected.endswith("\n")),
            )
            break
        removed = windows.pop()
        omitted_ids = list(source_coverage.get("omittedUnitIds") or ())
        omitted_ids.append(str(removed.get("unitId") or ""))
        source_coverage["omittedUnitIds"] = [
            unit_id for unit_id in dict.fromkeys(omitted_ids) if unit_id
        ][:20]

    if include_source:
        source_coverage.update({
            "returnedSourceUnits": len(windows),
            "omittedSourceUnits": max(
                int(source_coverage.get("omittedSourceUnits") or 0),
                int(source_coverage.get("availableSourceUnits") or 0) - len(windows),
            ),
            "truncatedSourceWindows": sum(
                bool(window.get("truncated")) for window in windows
            ),
            "returnedSourceCharacters": sum(
                len(str(window.get("content") or "")) for window in windows
            ),
        })
        source_coverage["truncated"] = bool(
            source_coverage["omittedSourceUnits"]
            or source_coverage["truncatedSourceWindows"]
        )
        source_coverage["state"] = (
            "bounded" if source_coverage["truncated"] else "complete"
        )

    source_by_unit = _source_evidence_by_unit(windows)
    partial_reasons = list(walked["partialReasons"])
    if root_truncated:
        partial_reasons.append("root_result_limit")
    if token_omitted_units or token_omitted_relation_ids:
        partial_reasons.append("token_budget")
    if source_coverage.get("truncated"):
        partial_reasons.append("source_window_limit")
    partial_reasons = list(dict.fromkeys(partial_reasons))
    frontier_values = [
        (unit, depth, "token_budget", None)
        for unit, depth in token_omitted_units
    ]
    if token_omitted_relation_ids and not frontier_values and selected_units:
        _unit_id_value, unit, depth = selected_units[-1]
        frontier_values.append((unit, depth, "token_budget", None))
    frontier_values.extend(walked["frontier"].values())
    frontier_response = []
    seen_frontier: set[tuple[str, str]] = set()

    def traversal_continuation_arguments(
        unit: Mapping[str, Any],
        depth: int,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "start": _unit_id(unit),
            "strategy": extra.get("strategy", "bfs"),
            "direction": extra.get("direction", "both"),
            "maxDepth": max(0, max_depth - depth),
            "maxResults": max_results,
            "tokenBudget": token_budget,
            "detailLevel": detail_level,
            "includeSource": include_source,
            "maxSourceWindows": max_source_windows,
            "maxSourceCharacters": max_source_characters,
        }
        if extra.get("relationKinds"):
            arguments["relationKinds"] = list(extra["relationKinds"])
        return arguments

    for unit, depth, reason, next_cursor in frontier_values:
        frontier_key = (_unit_id(unit), reason)
        if frontier_key in seen_frontier:
            continue
        seen_frontier.add(frontier_key)
        item = {
            **_compact_unit(unit, detail_level="minimal", depth=depth),
            "reason": reason,
        }
        if next_cursor is not None and _unit_id(unit):
            item["continuation"] = {
                "tool": "queryCodeGraph",
                "arguments": {
                    "pattern": "relations_of",
                    "target": _unit_id(unit),
                    "cursor": next_cursor,
                    "maxResults": min(100, max_results),
                    "detailLevel": detail_level,
                },
            }
        elif reason == "token_budget" and _unit_id(unit):
            item["continuation"] = {
                "tool": "traverseCodeGraph",
                "arguments": traversal_continuation_arguments(unit, depth),
            }
        frontier_response.append(item)

    response = {
        "status": "ready",
        "operation": operation,
        "snapshot": reader.snapshot(),
        "targets": list(targets),
        "unresolvedTargets": list(unresolved),
        "roots": [
            _compact_unit(unit, detail_level=detail_level, depth=0)
            for unit in roots
        ],
        "nodes": [
            {
                **compact,
                **({"sourceEvidenceId": source_by_unit[unit_id]}
                   if unit_id in source_by_unit else {}),
            }
            for (unit_id, _unit, _depth), compact in zip(selected_units, selection.nodes)
        ],
        "edges": [dict(edge) for edge in selection.edges],
        "frontier": frontier_response[:_MAX_RETURNED_FRONTIER if token_budget is not None else None],
        "sourceWindows": windows,
        "coverage": {
            "state": "bounded" if partial_reasons else "complete",
            "truncated": bool(partial_reasons),
            "partialReasons": partial_reasons,
            "maxDepth": max_depth,
            "depthReached": walked["depthReached"],
            "maxResults": max_results,
            "tokenBudget": token_budget,
            "serializedCharacters": 0,
            "estimatedTokens": 0,
            "discoveredNodes": len(visited),
            "returnedNodes": len(selected_units),
            "omittedNodes": max(0, len(visited) - len(selected_units)),
            "discoveredRelations": len(relations),
            "returnedRelations": len(selected_relations),
            "omittedRelations": len(relations) - len(selected_relations),
            "omittedFrontier": max(
                0,
                len(frontier_response) - (_MAX_RETURNED_FRONTIER if token_budget is not None else len(frontier_response)),
            ),
            "sourceIncluded": include_source,
            "source": source_coverage,
        },
        **dict(extra),
    }

    total_frontier = len(frontier_response)

    def mark_token_bounded() -> None:
        coverage = response["coverage"]
        reasons = coverage["partialReasons"]
        if "token_budget" not in reasons:
            reasons.append("token_budget")
        coverage["state"] = "bounded"
        coverage["truncated"] = True

    def refresh_source_coverage() -> None:
        source = response["coverage"]["source"]
        returned_windows = response["sourceWindows"]
        if not include_source:
            return
        source.update({
            "returnedSourceUnits": len(returned_windows),
            "omittedSourceUnits": max(
                int(source.get("omittedSourceUnits") or 0),
                int(source.get("availableSourceUnits") or 0)
                - len(returned_windows),
            ),
            "truncatedSourceWindows": sum(
                bool(window.get("truncated"))
                for window in returned_windows
            ),
            "returnedSourceCharacters": sum(
                len(str(window.get("content") or ""))
                for window in returned_windows
            ),
        })
        source["truncated"] = bool(
            source["omittedSourceUnits"]
            or source["truncatedSourceWindows"]
        )
        source["state"] = "bounded" if source["truncated"] else "complete"
        if source["truncated"]:
            reasons = response["coverage"]["partialReasons"]
            if "source_window_limit" not in reasons:
                reasons.append("source_window_limit")
            response["coverage"]["state"] = "bounded"
            response["coverage"]["truncated"] = True
        valid_evidence_ids = {
            str(window.get("evidenceId") or "")
            for window in returned_windows
        }
        for node in response["nodes"]:
            if node.get("sourceEvidenceId") not in valid_evidence_ids:
                node.pop("sourceEvidenceId", None)

    def refresh_response_counts() -> None:
        coverage = response["coverage"]
        coverage["returnedNodes"] = len(response["nodes"])
        coverage["omittedNodes"] = max(
            0,
            int(coverage["discoveredNodes"]) - len(response["nodes"]),
        )
        coverage["returnedRelations"] = len(response["edges"])
        coverage["omittedRelations"] = max(
            0,
            int(coverage["discoveredRelations"]) - len(response["edges"]),
        )
        coverage["omittedFrontier"] = max(
            0,
            total_frontier - len(response["frontier"]),
        )

    def update_serialized_size() -> int:
        coverage = response["coverage"]
        serialized_characters = _json_char_length(response)
        for _iteration in range(8):
            estimated_tokens = (serialized_characters + 3) // 4
            previous = (
                coverage["serializedCharacters"],
                coverage["estimatedTokens"],
            )
            coverage["serializedCharacters"] = serialized_characters
            coverage["estimatedTokens"] = estimated_tokens
            if previous == (serialized_characters, estimated_tokens):
                break
            serialized_characters += (
                len(str(serialized_characters)) + len(str(estimated_tokens))
                - len(str(previous[0])) - len(str(previous[1]))
            )
        return int(coverage["serializedCharacters"])

    refresh_source_coverage()
    refresh_response_counts()
    serialized_characters = update_serialized_size()
    if response_character_budget is None:
        # Semantic depth/results already selected this neighborhood. Without
        # an explicit caller budget, preserve every selected identity, edge,
        # source window and frontier entry; account for size, never fit it.
        return response
    if serialized_characters > response_character_budget:
        mark_token_bounded()
        if not any(
            item.get("reason") == "token_budget"
            for item in response["frontier"]
        ) and selected_units:
            _frontier_id, frontier_unit, frontier_depth = selected_units[-1]
            response["frontier"].insert(0, {
                **_compact_unit(
                    frontier_unit,
                    detail_level="minimal",
                    depth=frontier_depth,
                ),
                "reason": "token_budget",
                "continuation": {
                    "tool": "traverseCodeGraph",
                    "arguments": traversal_continuation_arguments(
                        frontier_unit,
                        frontier_depth,
                    ),
                },
            })
            total_frontier += 1

    for _iteration in range(1000):
        refresh_source_coverage()
        refresh_response_counts()
        serialized_characters = update_serialized_size()
        if serialized_characters <= response_character_budget:
            break
        mark_token_bounded()

        if len(response["frontier"]) > 1:
            response["frontier"].pop()
            continue
        if response["sourceWindows"]:
            overflow = serialized_characters - response_character_budget
            window = response["sourceWindows"][-1]
            content = str(window.get("content") or "")
            if len(content) > overflow + 64:
                selected = content[:len(content) - overflow - 64]
                if "\n" in selected:
                    selected = selected.rsplit("\n", 1)[0] + "\n"
                window["content"] = selected
                window["truncated"] = True
                start_line = max(1, int(window.get("startLine") or 1))
                window["endLine"] = max(
                    start_line,
                    start_line
                    + selected.count("\n")
                    - int(selected.endswith("\n")),
                )
            else:
                removed = response["sourceWindows"].pop()
                omitted_ids = list(
                    response["coverage"]["source"].get("omittedUnitIds")
                    or ()
                )
                omitted_ids.append(str(removed.get("unitId") or ""))
                response["coverage"]["source"]["omittedUnitIds"] = [
                    unit_id
                    for unit_id in dict.fromkeys(omitted_ids)
                    if unit_id
                ][:20]
            continue
        if response["edges"]:
            response["edges"].pop()
            continue
        if len(response["nodes"]) > 1:
            removed_node = response["nodes"].pop()
            removed_unit_id = str(removed_node.get("unitId") or "")
            response["edges"] = [
                edge
                for edge in response["edges"]
                if removed_unit_id not in {
                    str(edge.get("sourceUnitId") or ""),
                    str(edge.get("targetUnitId") or ""),
                }
            ]
            continue
        if response["frontier"] and response["frontier"][0].get("continuation"):
            response["frontier"][0].pop("continuation", None)
            continue

        # Evidence has already been reduced as far as possible. Bound echoed
        # request controls and descriptive identity fields as a final envelope
        # step; callers already own the original request, so repeating an
        # arbitrarily long symbolic label must never defeat tokenBudget.
        echo_changed = False
        for key in ("targets", "unresolvedTargets"):
            values = response.get(key)
            if not isinstance(values, list):
                continue
            bounded_values = [
                str(value)[:160]
                for value in values[:4]
            ]
            if bounded_values != values:
                response[key] = bounded_values
                echo_changed = True
        if echo_changed:
            continue
        if response.get("relationKinds"):
            response["relationKinds"].pop()
            continue

        identity_changed = False
        for key in ("frontier", "roots", "nodes"):
            values = response.get(key)
            if not isinstance(values, list):
                continue
            compact_values = []
            for value in values:
                if not isinstance(value, Mapping):
                    continue
                compact = {
                    field: value.get(field)
                    for field in (
                        "unitId",
                        "depth",
                        "reason",
                        "sourceEvidenceId",
                    )
                    if value.get(field) is not None
                }
                if isinstance(compact.get("unitId"), str):
                    compact["unitId"] = compact["unitId"][:256]
                compact_values.append(compact)
            if compact_values != values:
                response[key] = compact_values
                identity_changed = True
        if identity_changed:
            continue

        snapshot = response.get("snapshot")
        if isinstance(snapshot, Mapping):
            bounded_snapshot = {
                str(key)[:80]: (
                    str(value)[:256] if isinstance(value, str) else value
                )
                for key, value in snapshot.items()
            }
            if bounded_snapshot != snapshot:
                response["snapshot"] = bounded_snapshot
                continue

        source_coverage_value = response["coverage"].get("source")
        if (
            isinstance(source_coverage_value, dict)
            and source_coverage_value.get("omittedUnitIds")
        ):
            source_coverage_value.pop("omittedUnitIds", None)
            continue
        if response.get("unresolvedTargets"):
            response["unresolvedTargets"].pop()
            continue
        if response.get("targets"):
            response["targets"].pop()
            continue
        break

    refresh_source_coverage()
    refresh_response_counts()
    serialized_characters = update_serialized_size()
    if serialized_characters > response_character_budget:
        # Preserve a small, fail-open diagnostic envelope if mandatory metadata
        # ever grows beyond the requested estimate. The original request is
        # already known to the caller; evidence counts and snapshot identity are
        # more useful here than failing an otherwise optional graph operation.
        previous_coverage = response["coverage"]
        snapshot_value = response.get("snapshot")
        compact_snapshot = {}
        if isinstance(snapshot_value, Mapping):
            compact_snapshot = {
                key: str(snapshot_value[key])[:160]
                for key in (
                    "kind",
                    "branch",
                    "revision",
                    "generationManifestSha256",
                )
                if snapshot_value.get(key) is not None
            }
        response = {
            "status": "ready",
            "operation": operation,
            "snapshot": compact_snapshot,
            "roots": [],
            "nodes": [],
            "edges": [],
            "frontier": [],
            "sourceWindows": [],
            "coverage": {
                "state": "bounded",
                "truncated": True,
                "partialReasons": ["token_budget"],
                "tokenBudget": token_budget,
                "serializedCharacters": 0,
                "estimatedTokens": 0,
                "discoveredNodes": previous_coverage["discoveredNodes"],
                "returnedNodes": 0,
                "omittedNodes": previous_coverage["discoveredNodes"],
                "discoveredRelations": previous_coverage[
                    "discoveredRelations"
                ],
                "returnedRelations": 0,
                "omittedRelations": previous_coverage[
                    "discoveredRelations"
                ],
                "sourceIncluded": include_source,
                "source": {
                    "state": "bounded" if include_source else "not_requested",
                    "truncated": include_source,
                    "returnedSourceUnits": 0,
                },
            },
            "strategy": extra.get("strategy", "bfs"),
            "direction": extra.get("direction", "both"),
        }
        update_serialized_size()
    return response
