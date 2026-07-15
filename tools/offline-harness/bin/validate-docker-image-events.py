#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print(
            "usage: validate-docker-image-events.py <docker-image-events.jsonl>",
            file=sys.stderr,
        )
        return 64
    path = Path(arguments[0])
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Docker event evidence must be a regular, non-symlink file")
        events = _load_events(path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: invalid Docker image-event evidence: {error}", file=sys.stderr)
        return 1
    print(
        f"validated {len(events)} Docker image event(s): "
        "no pull or push during persistence application tests"
    )
    return 0


def _load_events(path: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            raise ValueError(f"blank Docker event line at {line_number}")
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError(f"non-object Docker event at line {line_number}")
        event_type = event.get("Type", event.get("type"))
        action = event.get("Action", event.get("status"))
        if event_type != "image" or not isinstance(action, str) or not action:
            raise ValueError(f"malformed Docker image event at line {line_number}")
        if action.lower() in {"pull", "push"}:
            raise ValueError(
                f"forbidden Docker image {action.lower()} event at line {line_number}"
            )
        events.append(event)
    return events


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
