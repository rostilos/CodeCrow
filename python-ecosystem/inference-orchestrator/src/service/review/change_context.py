"""Lossless compact representations of changed-code context."""

from typing import Any, Mapping


def anchor_ranges(lines: Any) -> list[list[int]]:
    """Losslessly describe changed lines without repeating every line number."""
    ranges: list[list[int]] = []
    for line in sorted(lines):
        if ranges and line == ranges[-1][1] + 1:
            ranges[-1][1] = line
        else:
            ranges.append([line, line])
    return ranges



def resolve_changed_anchor(value: Mapping[str, Any], parts: Mapping[str, Any]) -> tuple[Any, int] | None:
    """Resolve a model report to an exact active anchor, without guessing a line.

    A missing/mistyped bookkeeping ID does not lose an otherwise unambiguous
    file/line. Ambiguous coordinates still need source resolution by the agent.
    """
    try:
        line = int(value.get("line"))
    except (TypeError, ValueError, OverflowError):
        return None
    matches = [part for part in parts.values() if value.get("file") == part.path
               and line in part.anchors and (not value.get("side") or value["side"] == part.side)]
    named = next((part for part in matches if part.id == value.get("partId")), None)
    part = named or (matches[0] if len(matches) == 1 else None)
    return (part, line) if part is not None else None
