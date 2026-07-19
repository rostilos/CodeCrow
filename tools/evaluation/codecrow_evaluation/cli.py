from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Sequence

from ._util import canonical_bytes, sha256_file
from .adapters import import_martian_snapshot
from .comparison import compare_approaches
from .registry import SplitRegistry
from .scoring import score_evaluation


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: Any) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_bytes(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codecrow-evaluation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    score = subparsers.add_parser("score", help="score a local evaluation bundle")
    score.add_argument("--input", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)

    compare = subparsers.add_parser(
        "compare-approaches",
        help="score and compare CLASSIC and AGENTIC bundles over identical cases",
    )
    compare.add_argument("--classic", type=Path, required=True)
    compare.add_argument("--agentic", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)

    commit = subparsers.add_parser(
        "commit-bundle",
        help="emit opaque commitments without copying protected bundle contents",
    )
    commit.add_argument("--split-id", required=True)
    commit.add_argument("--identities", type=Path, required=True)
    commit.add_argument("--labels", type=Path, required=True)
    commit.add_argument("--outcomes", type=Path, required=True)
    commit.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser(
        "validate-registry", help="validate custody and split invariants"
    )
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--policy-context-output", type=Path)

    martian = subparsers.add_parser(
        "import-martian", help="verify and import a pinned public Martian snapshot"
    )
    martian.add_argument("--descriptor", type=Path, required=True)
    martian.add_argument("--snapshot-root", type=Path, required=True)
    martian.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "score":
        _write_json(arguments.output, score_evaluation(_load_json(arguments.input)))
        return 0
    if arguments.command == "compare-approaches":
        _write_json(
            arguments.output,
            compare_approaches(
                _load_json(arguments.classic),
                _load_json(arguments.agentic),
            ),
        )
        return 0
    if arguments.command == "commit-bundle":
        for path in (arguments.identities, arguments.labels, arguments.outcomes):
            if not path.is_file():
                raise ValueError(f"protected bundle component does not exist: {path}")
        _write_json(
            arguments.output,
            {
                "identitiesCommitmentSha256": sha256_file(arguments.identities),
                "labelsCommitmentSha256": sha256_file(arguments.labels),
                "outcomesCommitmentSha256": sha256_file(arguments.outcomes),
                "schemaVersion": 1,
                "splitId": arguments.split_id,
            },
        )
        return 0
    if arguments.command == "validate-registry":
        registry = SplitRegistry.from_mapping(_load_json(arguments.input))
        if arguments.policy_context_output:
            _write_json(arguments.policy_context_output, registry.policy_context())
        return 0
    if arguments.command == "import-martian":
        _write_json(
            arguments.output,
            import_martian_snapshot(
                descriptor_path=arguments.descriptor,
                snapshot_root=arguments.snapshot_root,
            ),
        )
        return 0
    raise AssertionError(f"unhandled command {arguments.command}")
