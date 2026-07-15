#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = (
    REPOSITORY_ROOT
    / "tools"
    / "offline-harness"
    / "schema"
    / "external-call-ledger-v1.schema.json"
)


def main(arguments: list[str]) -> int:
    if not arguments:
        print(
            "usage: validate-ledgers.py <ledger.json|ledger-directory> [...]",
            file=sys.stderr,
        )
        return 64

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    ledger_paths: list[tuple[str, Path]] = []
    for argument in arguments:
        supplied = Path(argument)
        path = supplied.resolve(strict=False)
        if supplied.is_symlink():
            print(f"ERROR: required ledger path must not be a symlink: {argument}", file=sys.stderr)
            return 1
        if path.is_dir():
            descendants = sorted(path.rglob("*"))
            if any(child.is_symlink() for child in descendants):
                print(f"ERROR: ledger directory contains a symlink: {argument}", file=sys.stderr)
                return 1
            children = [
                child for child in descendants if child.is_file() and child.suffix == ".json"
            ]
            if not children:
                print(f"ERROR: required ledger directory is empty: {argument}", file=sys.stderr)
                return 1
            ledger_paths.extend((str(child), child.resolve()) for child in children)
        elif path.is_file():
            ledger_paths.append((argument, path))
        else:
            print(f"ERROR: required ledger is missing or not a regular file: {argument}", file=sys.stderr)
            return 1

    for display_path, path in ledger_paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            validator.validate(document)
        except (OSError, json.JSONDecodeError, ValidationError, ValueError) as error:
            print(f"ERROR: invalid external-call ledger {display_path}: {error}", file=sys.stderr)
            return 1
        calls = document["calls"]
        live_count = sum(bool(call["live"]) for call in calls)
        simulated_count = sum(bool(call["simulated"]) for call in calls)
        expected_sequences = list(range(1, len(calls) + 1))
        actual_sequences = [call["sequence"] for call in calls]
        if document["live_call_count"] != live_count:
            print(
                f"ERROR: external-call ledger {display_path} live counter does not match calls",
                file=sys.stderr,
            )
            return 1
        if document["simulated_call_count"] != simulated_count:
            print(
                f"ERROR: external-call ledger {display_path} simulated counter does not match calls",
                file=sys.stderr,
            )
            return 1
        if actual_sequences != expected_sequences:
            print(
                f"ERROR: external-call ledger {display_path} sequences must be contiguous in array order",
                file=sys.stderr,
            )
            return 1
        if live_count != 0:
            print(
                f"ERROR: external-call ledger {display_path} records "
                f"{live_count} live call(s)",
                file=sys.stderr,
            )
            return 1
        print(
            f"validated {display_path}: live=0 simulated={simulated_count}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
