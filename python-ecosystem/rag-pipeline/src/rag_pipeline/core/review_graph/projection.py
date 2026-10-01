"""Compact graph identities and deterministic serialization.

Adapted portions retain their MIT attribution in this package's NOTICE.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .constants import (
    _RELATION_SEMANTIC_ALIASES,
)


def _normalized_relation_token(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _canonical_relation_name(value: Any) -> str:
    normalized = _normalized_relation_token(value)
    return _RELATION_SEMANTIC_ALIASES.get(normalized, normalized)


def _canonical_relation_semantic(relation: Mapping[str, Any]) -> str:
    """Return a recognized semantic without discarding plugin provenance kinds."""

    normalized_kind = _normalized_relation_token(relation.get("kind"))
    normalized_relation = _normalized_relation_token(relation.get("relation"))
    for candidate in (normalized_kind, normalized_relation):
        semantic = _RELATION_SEMANTIC_ALIASES.get(candidate)
        if semantic:
            return semantic
    return normalized_kind or normalized_relation


def _unit_id(unit: Mapping[str, Any] | None) -> str:
    return str(unit.get("unitId") or "") if isinstance(unit, Mapping) else ""


def _unit_key(unit: Mapping[str, Any]) -> str:
    return _unit_id(unit) or "|".join((
        str(unit.get("path") or ""),
        str(unit.get("qualifiedName") or unit.get("name") or ""),
        str(unit.get("startLine") or ""),
    ))


def _compact_unit(
    unit: Mapping[str, Any],
    *,
    detail_level: str,
    depth: int | None = None,
    source_evidence_id: str | None = None,
) -> dict[str, Any]:
    fields = ("unitId", "path", "kind", "name")
    if detail_level == "standard":
        fields += (
            "qualifiedName", "startLine", "endLine", "language", "recordType",
        )
    result = {
        field: unit.get(field)
        for field in fields
        if unit.get(field) is not None and unit.get(field) != ""
    }
    if depth is not None:
        result["depth"] = depth
    if source_evidence_id:
        result["sourceEvidenceId"] = source_evidence_id
    return result


def _compact_relation(
    relation: Mapping[str, Any],
    *,
    detail_level: str,
    depth: int | None = None,
) -> dict[str, Any]:
    source_unit = relation.get("sourceUnit")
    target_unit = relation.get("targetUnit")
    result: dict[str, Any] = {
        "evidenceId": relation.get("evidenceId"),
        "kind": relation.get("kind"),
        "source": relation.get("source"),
        "target": relation.get("target"),
        "sourceUnitId": _unit_id(source_unit),
        "targetUnitId": _unit_id(target_unit),
    }
    if detail_level == "standard":
        result.update({
            "relation": relation.get("relation"),
            "origin": dict(relation.get("origin") or {}),
            "relatedPaths": list(relation.get("relatedPaths") or ()),
        })
        attributes = relation.get("attributes")
        if isinstance(attributes, Mapping) and attributes:
            result["attributes"] = dict(attributes)
    if depth is not None:
        result["depth"] = depth
    return {
        key: value
        for key, value in result.items()
        if value is not None and value != "" and value != [] and value != {}
    }


def _json_char_length(value: Any) -> int:
    return len(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ))
