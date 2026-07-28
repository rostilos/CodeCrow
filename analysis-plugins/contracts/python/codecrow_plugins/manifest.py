from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .api import (
    Capability,
    ContentMarker,
    ContentPatternMarker,
    DetectionAlternative,
    DetectionRules,
    PluginDescriptor,
    PluginKind,
)


_DESCRIPTOR_FIELDS = {"id", "kind", "requires", "capabilities", "detection", "entrypoints"}
_DETECTION_FIELDS = {"extensions", "filesAll", "filesAny", "contentMarkers", "alternatives"}
_ALTERNATIVE_FIELDS = {
    "filesAll",
    "filesAny",
    "pathPatternsAll",
    "pathPatternsAny",
    "contentMarkers",
}
_ALTERNATIVE_OPTIONAL_FIELDS = {"contentPatternMarkers"}
_MARKER_FIELDS = {"path", "contains"}
_PATTERN_MARKER_FIELDS = {"pathPattern", "contains"}


def _exact_fields(data: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"invalid {label} fields: missing={missing}, unknown={unknown}")


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be an array of strings")
    return tuple(value)


def _markers(value: Any, field_name: str) -> tuple[ContentMarker, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    markers: list[ContentMarker] = []
    for raw_marker in value:
        if not isinstance(raw_marker, Mapping):
            raise ValueError("content marker must be an object")
        _exact_fields(raw_marker, _MARKER_FIELDS, "content marker")
        markers.append(ContentMarker(path=raw_marker["path"], contains=raw_marker["contains"]))
    return tuple(markers)


def _pattern_markers(value: Any, field_name: str) -> tuple[ContentPatternMarker, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    markers: list[ContentPatternMarker] = []
    for raw_marker in value:
        if not isinstance(raw_marker, Mapping):
            raise ValueError("content pattern marker must be an object")
        _exact_fields(raw_marker, _PATTERN_MARKER_FIELDS, "content pattern marker")
        markers.append(ContentPatternMarker(
            path_pattern=raw_marker["pathPattern"], contains=raw_marker["contains"],
        ))
    return tuple(markers)


def descriptor_from_mapping(data: Mapping[str, Any]) -> PluginDescriptor:
    if not isinstance(data, Mapping):
        raise ValueError("plugin descriptor must be an object")
    _exact_fields(data, _DESCRIPTOR_FIELDS, "plugin descriptor")

    detection_data = data["detection"]
    if not isinstance(detection_data, Mapping):
        raise ValueError("detection must be an object")
    _exact_fields(detection_data, _DETECTION_FIELDS, "detection")

    markers = _markers(detection_data["contentMarkers"], "contentMarkers")
    raw_alternatives = detection_data["alternatives"]
    if not isinstance(raw_alternatives, list):
        raise ValueError("alternatives must be an array")
    alternatives: list[DetectionAlternative] = []
    for raw_alternative in raw_alternatives:
        if not isinstance(raw_alternative, Mapping):
            raise ValueError("detection alternative must be an object")
        actual_fields = set(raw_alternative)
        if not _ALTERNATIVE_FIELDS.issubset(actual_fields) or not actual_fields.issubset(
            _ALTERNATIVE_FIELDS | _ALTERNATIVE_OPTIONAL_FIELDS
        ):
            missing = sorted(_ALTERNATIVE_FIELDS - actual_fields)
            unknown = sorted(actual_fields - _ALTERNATIVE_FIELDS - _ALTERNATIVE_OPTIONAL_FIELDS)
            raise ValueError(f"invalid detection alternative fields: missing={missing}, unknown={unknown}")
        alternatives.append(DetectionAlternative(
            files_all=_string_tuple(raw_alternative["filesAll"], "filesAll"),
            files_any=_string_tuple(raw_alternative["filesAny"], "filesAny"),
            path_patterns_all=_string_tuple(raw_alternative["pathPatternsAll"], "pathPatternsAll"),
            path_patterns_any=_string_tuple(raw_alternative["pathPatternsAny"], "pathPatternsAny"),
            content_markers=_markers(raw_alternative["contentMarkers"], "contentMarkers"),
            content_pattern_markers=_pattern_markers(
                raw_alternative.get("contentPatternMarkers"), "contentPatternMarkers",
            ),
        ))

    raw_entrypoints = data["entrypoints"]
    if not isinstance(raw_entrypoints, Mapping):
        raise ValueError("entrypoints must be an object")

    try:
        kind = PluginKind(data["kind"])
        capabilities = tuple(Capability(value) for value in _string_tuple(data["capabilities"], "capabilities"))
    except (TypeError, ValueError) as exception:
        raise ValueError(f"invalid plugin enum value: {exception}") from exception

    return PluginDescriptor(
        id=data["id"],
        kind=kind,
        requires=_string_tuple(data["requires"], "requires"),
        capabilities=capabilities,
        detection=DetectionRules(
            extensions=_string_tuple(detection_data["extensions"], "extensions"),
            files_all=_string_tuple(detection_data["filesAll"], "filesAll"),
            files_any=_string_tuple(detection_data["filesAny"], "filesAny"),
            content_markers=markers,
            alternatives=tuple(alternatives),
        ),
        entrypoints=dict(raw_entrypoints),
    )


def load_descriptor(path: str | Path) -> PluginDescriptor:
    with Path(path).open("r", encoding="utf-8") as handle:
        return descriptor_from_mapping(json.load(handle))


def load_descriptors(path: str | Path) -> tuple[PluginDescriptor, ...]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("plugin descriptor collection must be an array")
    return tuple(descriptor_from_mapping(item) for item in data)
