"""Exact target resolution, ambiguity and paginated relation lookup.

Adapted portions retain their MIT attribution in this package's NOTICE.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .constants import (
    _SOURCE_SUFFIXES,
)
from .projection import (
    _unit_key,
)


def _looks_like_path(target: str) -> bool:
    final = target.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    suffix = "." + final.rsplit(".", 1)[-1].casefold() if "." in final else ""
    return "/" in target or "\\" in target or suffix in _SOURCE_SUFFIXES


def _query_page(
    reader,
    pattern: str,
    target: str,
    *,
    max_results: int,
    cursor: int = 0,
) -> dict[str, Any]:
    """Read one deterministic page while retaining compatibility test readers."""

    try:
        return reader.query_graph(
            pattern,
            target,
            max_results=max_results,
            cursor=cursor,
        )
    except TypeError as exception:
        if cursor or "cursor" not in str(exception):
            raise
        return reader.query_graph(pattern, target, max_results=max_results)


def _relation_pages(
    reader,
    target: str,
    *,
    max_results: int,
) -> tuple[list[dict[str, Any]], bool, int | None]:
    """Read a bounded relation branch and expose a resumable cursor."""

    relations: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor = 0
    next_cursor: int | None = None
    while len(relations) < max_results:
        page_size = min(100, max_results - len(relations))
        response = _query_page(
            reader,
            "relations_of",
            target,
            max_results=page_size,
            cursor=cursor,
        )
        page = [
            dict(relation)
            for relation in response.get("results") or ()
            if isinstance(relation, Mapping)
        ]
        for relation in page:
            evidence_id = str(relation.get("evidenceId") or "")
            key = evidence_id or json.dumps(
                relation,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if key in seen:
                continue
            seen.add(key)
            relations.append(relation)
        truncated = bool(response.get("truncated"))
        if not truncated:
            return relations, False, None
        proposed_cursor = response.get("nextCursor")
        try:
            proposed_cursor = int(proposed_cursor)
        except (TypeError, ValueError):
            proposed_cursor = cursor + len(page)
        if not page or proposed_cursor <= cursor:
            return relations, True, cursor or None
        cursor = proposed_cursor
        next_cursor = cursor
    return relations, bool(next_cursor), next_cursor


def _resolve_targets(
    reader,
    targets: Sequence[str],
    *,
    max_results: int,
) -> tuple[
    list[dict[str, Any]],
    list[str],
    bool,
    list[dict[str, Any]],
]:
    units: list[dict[str, Any]] = []
    unresolved: list[str] = []
    ambiguous: list[dict[str, Any]] = []
    seen: set[str] = set()
    truncated = False
    for raw_target in targets:
        target = str(raw_target or "").strip()
        if not target:
            continue
        remaining = max_results - len(units)
        if remaining <= 0:
            truncated = True
            break
        candidates: list[Mapping[str, Any]] = []
        detail = reader.get_unit(target)
        if isinstance(detail, Mapping) and isinstance(detail.get("unit"), Mapping):
            candidates.append(detail["unit"])
        if not candidates:
            pattern = "file_summary" if _looks_like_path(target) else "symbol_search"
            resolved_exactly = False
            if pattern == "symbol_search":
                # Named relation queries use the reader's conservative target
                # resolver. Probe it before fuzzy symbol search so traversal
                # and impact never silently choose one of several symbols (or
                # widen a single symbolic root into several unrelated roots).
                resolution = _query_page(
                    reader,
                    "relations_of",
                    target,
                    max_results=1,
                )
                if resolution.get("status") == "ambiguous":
                    ambiguous.append({
                        "target": target,
                        "error": resolution.get("error"),
                        "candidates": list(resolution.get("candidates") or ()),
                        "candidateCount": resolution.get(
                            "candidateCount",
                            len(resolution.get("candidates") or ()),
                        ),
                        "candidatesTruncated": bool(
                            resolution.get("candidatesTruncated")
                            or resolution.get("truncated")
                        ),
                        "cursor": int(resolution.get("cursor") or 0),
                        "nextCursor": resolution.get("nextCursor"),
                        **(
                            {"hint": resolution.get("hint")}
                            if resolution.get("hint")
                            else {}
                        ),
                    })
                    continue
                resolved_units = [
                    item
                    for item in resolution.get("resolvedUnits") or ()
                    if isinstance(item, Mapping)
                ]
                if resolved_units:
                    truncated = truncated or len(resolved_units) > remaining
                    candidates.extend(resolved_units[:remaining])
                    resolved_exactly = True
            cursor = 0
            while remaining > 0 and not resolved_exactly:
                page_size = min(100, remaining)
                response = _query_page(
                    reader,
                    pattern,
                    target,
                    max_results=page_size,
                    cursor=cursor,
                )
                page = [
                    item
                    for item in response.get("results") or ()
                    if isinstance(item, Mapping)
                ]
                candidates.extend(page)
                remaining -= len(page)
                page_truncated = bool(response.get("truncated"))
                if not page_truncated or pattern != "file_summary" or not page:
                    truncated = truncated or page_truncated
                    break
                proposed_cursor = response.get("nextCursor")
                try:
                    proposed_cursor = int(proposed_cursor)
                except (TypeError, ValueError):
                    proposed_cursor = cursor + len(page)
                if proposed_cursor <= cursor:
                    truncated = True
                    break
                cursor = proposed_cursor
            if (
                not resolved_exactly
                and remaining <= 0
                and bool(response.get("truncated"))
            ):
                truncated = True
            if not candidates and pattern == "file_summary":
                response = _query_page(
                    reader,
                    "symbol_search",
                    target,
                    max_results=min(100, max(1, remaining)),
                )
                candidates.extend(
                    item
                    for item in response.get("results") or ()
                    if isinstance(item, Mapping)
                )
                truncated = truncated or bool(response.get("truncated"))
        matched = False
        for candidate_index, candidate in enumerate(candidates):
            key = _unit_key(candidate)
            if not key or key in seen:
                continue
            matched = True
            seen.add(key)
            units.append(dict(candidate))
            if len(units) >= max_results:
                remaining_keys = {
                    _unit_key(remaining_candidate)
                    for remaining_candidate in candidates[candidate_index + 1:]
                    if _unit_key(remaining_candidate)
                }
                truncated = truncated or bool(remaining_keys.difference(seen))
                break
        if not matched:
            unresolved.append(target)
    return units, unresolved, truncated, ambiguous


def _ambiguous_target_response(
    reader,
    *,
    operation: str,
    targets: Sequence[str],
    ambiguous: Sequence[Mapping[str, Any]],
    include_source: bool = False,
) -> dict[str, Any]:
    values = [dict(value) for value in ambiguous]
    response: dict[str, Any] = {
        "status": "ambiguous",
        "operation": operation,
        "error": "A symbolic graph root resolves to multiple structural units",
        "snapshot": reader.snapshot(),
        "targets": list(targets),
        "ambiguousTargets": values,
        "results": [],
        "sourceWindows": [],
        "coverage": {
            "state": "bounded",
            "truncated": True,
            "partialReasons": ["ambiguous_target"],
            "returnedResults": 0,
            "sourceIncluded": include_source,
            "source": {
                "state": "unavailable" if include_source else "not_requested",
                "truncated": False,
                "availableSourceUnits": 0,
                "returnedSourceUnits": 0,
                "omittedSourceUnits": 0,
                "truncatedSourceWindows": 0,
            },
        },
    }
    if len(values) == 1:
        response.update({
            "target": values[0].get("target"),
            "candidates": values[0].get("candidates", []),
            "candidateCount": values[0].get("candidateCount", 0),
            "candidatesTruncated": values[0].get(
                "candidatesTruncated",
                False,
            ),
            "cursor": int(values[0].get("cursor") or 0),
            "nextCursor": values[0].get("nextCursor"),
        })
        if values[0].get("hint"):
            response["hint"] = values[0]["hint"]
    return response
