#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_NAMESPACE = "fixture_a9fbed3007f539cc"
_EXPECTED_ORDER = [
    ("first-generation", "postgres"),
    ("first-generation", "redis"),
    ("first-generation", "qdrant"),
    ("restarted-generation", "postgres"),
    ("restarted-generation", "redis"),
    ("restarted-generation", "qdrant"),
]


def main(arguments: list[str]) -> int:
    print_ids = len(arguments) == 2 and arguments[0] == "--print-container-ids"
    if not print_ids and len(arguments) != 1:
        print(
            "usage: validate-persistence-container-report.py "
            "[--print-container-ids] <container-report.json>",
            file=sys.stderr,
        )
        return 64
    path = Path(arguments[-1])
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("container report must be a regular, non-symlink file")
        document = json.loads(path.read_text(encoding="utf-8"))
        container_ids = _validate(document)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: invalid persistence container report: {error}", file=sys.stderr)
        return 1
    if print_ids:
        for container_id in container_ids:
            print(container_id)
    else:
        print(
            "validated 6 exact test-owned persistence container IDs: "
            "two generations absent after cleanup"
        )
    return 0


def _validate(document: object) -> list[str]:
    if not isinstance(document, dict) or set(document) != {
        "schemaVersion",
        "scenarioNamespace",
        "containers",
    }:
        raise ValueError("report fields do not match the persistence container schema")
    if document["schemaVersion"] != "1.0":
        raise ValueError("container report schemaVersion must be 1.0")
    if document["scenarioNamespace"] != _EXPECTED_NAMESPACE:
        raise ValueError("container report scenario namespace is not deterministic")
    containers = document["containers"]
    if not isinstance(containers, list) or len(containers) != len(_EXPECTED_ORDER):
        raise ValueError("container report must contain exactly six owned containers")
    ids: list[str] = []
    for index, (container, expected_identity) in enumerate(
        zip(containers, _EXPECTED_ORDER, strict=True), start=1
    ):
        if not isinstance(container, dict) or set(container) != {
            "generation",
            "service",
            "containerId",
            "status",
        }:
            raise ValueError(f"container record {index} has incomplete or unknown fields")
        if (container["generation"], container["service"]) != expected_identity:
            raise ValueError(f"container record {index} has an unexpected identity or order")
        container_id = container["containerId"]
        if not isinstance(container_id, str) or not _CONTAINER_ID.fullmatch(container_id):
            raise ValueError(f"container record {index} has an invalid full Docker ID")
        if container["status"] != "absent":
            raise ValueError(f"container record {index} was retained after cleanup")
        ids.append(container_id)
    if len(set(ids)) != len(ids):
        raise ValueError("owned persistence container IDs must be unique")
    return ids


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
