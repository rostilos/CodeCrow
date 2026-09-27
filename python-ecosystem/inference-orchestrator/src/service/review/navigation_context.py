"""Lossless sharing of repeated unit metadata in graph navigation observations."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from typing import Any


def _encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compact_navigation_result(result: dict[str, Any]) -> dict[str, Any]:
    """Factor exact repeated units; keep every graph record and source byte.

    References retain the actual unitId for direct structural reads. unitRef is
    a content-derived digest, so the same observation has the same identity in
    other graph queries. Existing definition envelopes are left untouched.
    """
    if "unitDefinitions" in result:
        return result
    counts: Counter[str] = Counter()
    units: dict[str, dict[str, Any]] = {}
    reserved: set[str] = set()

    def count(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("unitRef"), str):
                reserved.add(value["unitRef"])
            if isinstance(value.get("unitId"), str) and len(value) > 1 and "unitRef" not in value:
                identity = _encoded(value)
                counts[identity] += 1
                units[identity] = value
            for item in value.values():
                count(item)
        elif isinstance(value, list):
            for item in value:
                count(item)

    count(result)
    references = {identity: hashlib.sha256(identity.encode("utf-8")).hexdigest()
                  for identity, occurrences in counts.items() if occurrences > 1}
    references = {identity: reference for identity, reference in references.items() if reference not in reserved}
    if not references:
        return result
    definitions: dict[str, dict[str, Any]] = {}

    def project(value: Any) -> Any:
        if isinstance(value, dict):
            if isinstance(value.get("unitId"), str) and "unitRef" not in value:
                identity = _encoded(value)
                reference = references.get(identity)
                if reference is not None:
                    definitions[reference] = deepcopy(units[identity])
                    return {"unitId": value["unitId"], "unitRef": reference}
            return {key: project(item) for key, item in value.items()}
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    projected = project(result)
    projected["unitDefinitions"] = definitions
    # Tiny unit records can cost more as references. Choose the smaller exact
    # representation; this does not impose a content, result, or token quota.
    return projected if len(_encoded(projected)) < len(_encoded(result)) else result


def expand_navigation_result(result: dict[str, Any]) -> dict[str, Any]:
    """Restore semantic records for host comparisons, not the model prompt."""
    definitions = result.get("unitDefinitions")
    if not isinstance(definitions, dict):
        return result

    def expand(value: Any) -> Any:
        if isinstance(value, dict):
            reference = value.get("unitRef")
            if set(value) == {"unitId", "unitRef"} and isinstance(reference, str):
                definition = definitions.get(reference)
                if isinstance(definition, dict) and definition.get("unitId") == value["unitId"]:
                    return deepcopy(definition)
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return {key: expand(value) for key, value in result.items() if key != "unitDefinitions"}
