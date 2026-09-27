"""Source evidence lookup and explicitly bounded source windows.

Adapted portions retain their MIT attribution in this package's NOTICE.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .constants import (
    _MAX_SOURCE_WINDOW_CHARACTERS,
)
from .projection import (
    _unit_id,
)


def _source_detail(reader, unit: Mapping[str, Any]) -> Mapping[str, Any] | None:
    resolver = getattr(reader, "source_detail_for_manifest", None)
    if callable(resolver):
        detail = resolver(unit)
    else:
        unit_id = _unit_id(unit)
        detail = reader.get_unit(unit_id) if unit_id else None
    return detail if isinstance(detail, Mapping) and detail.get("sourceEvidence") else None


def _bounded_source_windows(
    reader,
    units: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
    *,
    changed_paths: Sequence[str],
    max_source_windows: int,
    max_source_characters: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    changed = set(changed_paths)
    relation_ids_by_unit: dict[str, list[str]] = {}
    for relation in relations:
        evidence_id = str(relation.get("evidenceId") or "")
        if not evidence_id:
            continue
        for endpoint in (relation.get("sourceUnit"), relation.get("targetUnit")):
            endpoint_id = _unit_id(endpoint)
            if endpoint_id:
                relation_ids_by_unit.setdefault(endpoint_id, []).append(evidence_id)

    windows: list[dict[str, Any]] = []
    window_by_source_id: dict[str, dict[str, Any]] = {}
    available_source_ids: set[str] = set()
    omitted_source_ids: list[str] = []
    remaining = max_source_characters
    # Stage 1 already contains bounded current source for the host-owned changed
    # paths. Prefer related unchanged units so an explicit includeSource request
    # fills a real cross-file context gap before duplicating the prompt.
    ordered_candidates = [
        candidate
        for _index, candidate in sorted(
            enumerate(units),
            key=lambda item: (
                str(item[1].get("path") or "") in changed,
                item[0],
            ),
        )
    ]
    for candidate in ordered_candidates:
        detail = _source_detail(reader, candidate)
        source = detail.get("unit") if isinstance(detail, Mapping) else None
        if not isinstance(source, Mapping):
            continue
        source_id = _unit_id(source)
        if not source_id:
            continue
        content = source.get("content")
        if not isinstance(content, str) or not content:
            continue
        available_source_ids.add(source_id)
        candidate_id = _unit_id(candidate)
        relation_ids = [
            *relation_ids_by_unit.get(candidate_id, ()),
            *relation_ids_by_unit.get(source_id, ()),
        ]
        existing_window = window_by_source_id.get(source_id)
        if existing_window is not None:
            existing_window["selectedUnitIds"] = list(dict.fromkeys(
                unit_id
                for unit_id in (
                    *existing_window.get("selectedUnitIds", ()),
                    candidate_id,
                    source_id,
                )
                if unit_id
            ))
            existing_window["relationEvidenceIds"] = list(dict.fromkeys((
                *existing_window.get("relationEvidenceIds", ()),
                *relation_ids,
            )))
            continue
        if len(windows) >= max_source_windows or remaining <= 0:
            omitted_source_ids.append(source_id)
            continue
        ceiling = min(remaining, _MAX_SOURCE_WINDOW_CHARACTERS)
        selected = content[:ceiling]
        truncated = len(content) > ceiling
        if truncated and "\n" in selected:
            selected = selected.rsplit("\n", 1)[0] + "\n"
        if not selected:
            continue
        remaining -= len(selected)
        start_line = max(1, int(source.get("startLine") or 1))
        end_line = start_line + selected.count("\n")
        if not selected.endswith("\n"):
            end_line += 1
        path = str(source.get("path") or "")
        window = {
            "evidenceId": "source:" + source_id,
            "unitId": source_id,
            "selectedUnitIds": list(dict.fromkeys(
                unit_id for unit_id in (candidate_id, source_id) if unit_id
            )),
            "path": path,
            "startLine": start_line,
            "endLine": max(start_line, end_line - 1),
            "content": selected,
            "contentSha256": source.get("contentSha256"),
            "changedFile": path in changed,
            "truncated": truncated,
            "relationEvidenceIds": list(dict.fromkeys(relation_ids)),
        }
        windows.append(window)
        window_by_source_id[source_id] = window
    truncated_windows = sum(bool(window["truncated"]) for window in windows)
    omitted_unique = list(dict.fromkeys(omitted_source_ids))
    bounded = bool(omitted_unique or truncated_windows)
    return windows, {
        "state": "bounded" if bounded else "complete",
        "truncated": bounded,
        "availableSourceUnits": len(available_source_ids),
        "returnedSourceUnits": len(windows),
        "omittedSourceUnits": len(omitted_unique),
        "truncatedSourceWindows": truncated_windows,
        "omittedUnitIds": omitted_unique[:20],
        "maxSourceWindows": max_source_windows,
        "maxSourceCharacters": max_source_characters,
        "returnedSourceCharacters": sum(
            len(str(window.get("content") or "")) for window in windows
        ),
    }


def _source_evidence_by_unit(
    windows: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    evidence_by_unit: dict[str, str] = {}
    for window in windows:
        evidence_id = str(window.get("evidenceId") or "")
        if not evidence_id:
            continue
        for unit_id in (
            window.get("unitId"),
            *(window.get("selectedUnitIds") or ()),
        ):
            normalized = str(unit_id or "")
            if normalized:
                evidence_by_unit[normalized] = evidence_id
    return evidence_by_unit
