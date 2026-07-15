from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = REPOSITORY_ROOT / "tools" / "offline-harness" / "bin" / "validate-ledgers.py"
GOLDEN = (
    REPOSITORY_ROOT
    / "tools"
    / "offline-harness"
    / "fixtures"
    / "golden"
    / "external-call-ledger-v1.json"
)


def _run(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *(str(path) for path in paths)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_validator_requires_schema_valid_zero_live_regular_ledgers(tmp_path: Path) -> None:
    passing = _run(GOLDEN)
    assert passing.returncode == 0
    assert "live=0" in passing.stdout

    live_document = json.loads(GOLDEN.read_text())
    live_document["calls"][0]["live"] = True
    live_document["calls"][0]["simulated"] = False
    live_document["live_call_count"] = 1
    live = tmp_path / "live.json"
    live.write_text(json.dumps(live_document))
    rejected = _run(live)
    assert rejected.returncode == 1
    assert "1 live call" in rejected.stderr

    lying = json.loads(GOLDEN.read_text())
    lying["calls"][0]["live"] = True
    lying["calls"][0]["simulated"] = False
    lying_path = tmp_path / "lying.json"
    lying_path.write_text(json.dumps(lying))
    inconsistent = _run(lying_path)
    assert inconsistent.returncode == 1
    assert "live counter does not match" in inconsistent.stderr

    wrong_simulated = json.loads(GOLDEN.read_text())
    wrong_simulated["simulated_call_count"] = 0
    wrong_simulated_path = tmp_path / "wrong-simulated.json"
    wrong_simulated_path.write_text(json.dumps(wrong_simulated))
    assert "simulated counter does not match" in _run(wrong_simulated_path).stderr

    wrong_sequence = json.loads(GOLDEN.read_text())
    wrong_sequence["calls"][1]["sequence"] = 1
    wrong_sequence_path = tmp_path / "wrong-sequence.json"
    wrong_sequence_path.write_text(json.dumps(wrong_sequence))
    assert "sequences must be contiguous" in _run(wrong_sequence_path).stderr

    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json")
    assert _run(malformed).returncode == 1
    assert _run(tmp_path / "missing.json").returncode == 1

    linked = tmp_path / "linked.json"
    linked.symlink_to(GOLDEN)
    assert _run(linked).returncode == 1

    for ordinal, unsafe_target in enumerate(
        (
            "https://user:secret@example.com/private?token=value",
            "customer prompt payload",
            "-leading.invalid:443",
            "example.invalid:65536",
        )
    ):
        unsafe = json.loads(GOLDEN.read_text())
        unsafe["calls"][0]["target"] = unsafe_target
        unsafe_path = tmp_path / f"unsafe-target-{ordinal}.json"
        unsafe_path.write_text(json.dumps(unsafe))
        assert _run(unsafe_path).returncode == 1


def test_validator_requires_at_least_one_ledger() -> None:
    result = _run()
    assert result.returncode == 64
    assert "usage:" in result.stderr


def test_validator_expands_nonempty_real_ledger_directories(tmp_path: Path) -> None:
    ledgers = tmp_path / "java-ledgers"
    ledgers.mkdir()
    for name in ("first.json", "second.json"):
        (ledgers / name).write_bytes(GOLDEN.read_bytes())
    nested = ledgers / "nested"
    nested.mkdir()
    (nested / "third.json").write_bytes(GOLDEN.read_bytes())
    result = _run(ledgers)
    assert result.returncode == 0
    assert result.stdout.count("validated") == 3

    empty = tmp_path / "empty"
    empty.mkdir()
    assert "directory is empty" in _run(empty).stderr

    linked_directory = tmp_path / "linked-directory"
    linked_directory.symlink_to(ledgers, target_is_directory=True)
    assert "must not be a symlink" in _run(linked_directory).stderr

    linked_ledger_directory = tmp_path / "linked-ledger-directory"
    linked_ledger_directory.mkdir()
    (linked_ledger_directory / "linked.json").symlink_to(GOLDEN)
    assert "contains a symlink" in _run(linked_ledger_directory).stderr
