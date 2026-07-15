from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_RUNNER = REPOSITORY_ROOT / "tools/offline-harness/bin/run-offline.sh"
DERIVED_RUNNER = (
    REPOSITORY_ROOT / "tools/quality-gates/bin/run-java-coverage-offline.sh"
)
CACHE_VALIDATOR = (
    REPOSITORY_ROOT / "tools/quality-gates/bin/validate-p007-maven-cache.sh"
)
P007_CACHE = (
    REPOSITORY_ROOT / ".llm-handoff-artifacts/p0-07/dependency-cache/maven"
)
P007_MANIFEST = (
    REPOSITORY_ROOT
    / ".llm-handoff-artifacts/p0-07/cache-closure/p0-07-maven-cache-manifest.sha256"
)
P007_RECEIPT = (
    REPOSITORY_ROOT
    / ".llm-handoff-artifacts/p0-07/cache-closure/p0-07-maven-cache.receipt"
)
P003_RUNNER_SHA256 = (
    "839d8945913bc385d772b3da3bb9dacc0ff871a4195159ea1ad8a374362ee86f"
)
DERIVATION_NOTICE = f"""\
# P0-07 Java coverage offline runner, derived from the P0-03 runner.
# Source identity: tools/offline-harness/bin/run-offline.sh sha256={P003_RUNNER_SHA256}
# Sync: re-pin and re-audit this file whenever the P0-03 source identity changes;
# only the identity and attested P0-07 cache-selection block may differ.
# Rollback: remove this derived wrapper and its P0-07 tests, then invoke the pinned
# P0-03 runner with its own cache; never redirect or mutate either frozen cache.
# The P0-03 artifact/ledger root intentionally remains unchanged for exact ledger
# compatibility. This wrapper exclusively admits the receipt-bound frozen P0-07 Maven cache.
"""


SOURCE_CACHE_BLOCK = """\
DEFAULT_MAVEN_REPOSITORY="$(realpath -m "$HOST_USER_HOME/.m2/repository")"
WORKSPACE_MAVEN_REPOSITORY="$ARTIFACT_ROOT/dependency-cache/maven"
HOST_MAVEN_REPOSITORY="$(realpath -m "${CODECROW_MAVEN_REPOSITORY:-$DEFAULT_MAVEN_REPOSITORY}")"
case "$HOST_MAVEN_REPOSITORY" in
  "$DEFAULT_MAVEN_REPOSITORY"|"$WORKSPACE_MAVEN_REPOSITORY") ;;
  *)
    echo "ERROR: Maven repository must be the user cache or P0-03 workspace cache" >&2
    exit 65
    ;;
esac
MAVEN_CACHE_ARGS=(--dir /tmp/codecrow-maven-repository)
if [[ -d "$HOST_MAVEN_REPOSITORY" ]]; then
  HOST_MAVEN_REPOSITORY="$(realpath -e "$HOST_MAVEN_REPOSITORY")"
  MAVEN_CACHE_ARGS+=(--ro-bind "$HOST_MAVEN_REPOSITORY" /tmp/codecrow-maven-repository)
fi
"""


DERIVED_CACHE_BLOCK = """\
DEFAULT_MAVEN_REPOSITORY="$(realpath -m "$HOST_USER_HOME/.m2/repository")"
WORKSPACE_MAVEN_REPOSITORY="$REPOSITORY_ROOT/.llm-handoff-artifacts/p0-07/dependency-cache/maven"
REQUESTED_MAVEN_REPOSITORY="${CODECROW_MAVEN_REPOSITORY:-$DEFAULT_MAVEN_REPOSITORY}"
HOST_MAVEN_REPOSITORY="$(
  CODECROW_MAVEN_REPOSITORY="$REQUESTED_MAVEN_REPOSITORY" \\
    "$REPOSITORY_ROOT/tools/quality-gates/bin/validate-p007-maven-cache.sh"
)"
MAVEN_CACHE_ARGS=(--dir /tmp/codecrow-maven-repository)
MAVEN_CACHE_ARGS+=(--ro-bind "$HOST_MAVEN_REPOSITORY" /tmp/codecrow-maven-repository)
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_derived_runner(source: str) -> str:
    anchor = "set -euo pipefail\n"
    assert source.count(anchor) == 1
    assert source.count(SOURCE_CACHE_BLOCK) == 1
    expected = source.replace(anchor, anchor + "\n" + DERIVATION_NOTICE, 1)
    expected = expected.replace(
        "usage: run-offline.sh <application-test-command> [args...]",
        "usage: run-java-coverage-offline.sh <application-test-command> [args...]",
        1,
    )
    return expected.replace(SOURCE_CACHE_BLOCK, DERIVED_CACHE_BLOCK, 1)


def _run_wrapper(
    *command: str,
    environment: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    selected_environment = os.environ.copy()
    selected_environment["CODECROW_MAVEN_REPOSITORY"] = str(P007_CACHE)
    selected_environment["CODECROW_P007_CACHE_RECEIPT_SHA256"] = _sha256(P007_RECEIPT)
    if environment:
        selected_environment.update(environment)
    return subprocess.run(
        [str(DERIVED_RUNNER), *command],
        cwd=REPOSITORY_ROOT,
        env=selected_environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def test_derived_wrapper_is_exact_audited_transform_of_p003() -> None:
    assert _sha256(SOURCE_RUNNER) == P003_RUNNER_SHA256
    assert DERIVED_RUNNER.is_file()
    assert os.access(DERIVED_RUNNER, os.X_OK)
    source = SOURCE_RUNNER.read_text(encoding="utf-8")
    derived = DERIVED_RUNNER.read_text(encoding="utf-8")
    assert derived == _expected_derived_runner(source)


def test_p007_frozen_cache_matches_the_receipt_bound_complete_manifest() -> None:
    assert not P007_CACHE.is_symlink()
    assert P007_CACHE.is_dir()
    assert not P007_MANIFEST.is_symlink()
    assert P007_MANIFEST.is_file()
    assert not P007_RECEIPT.is_symlink()
    assert P007_RECEIPT.is_file()

    receipt = dict(
        line.split("=", 1)
        for line in P007_RECEIPT.read_text(encoding="utf-8").splitlines()
    )
    assert receipt["schemaVersion"] == "1"
    assert receipt["cachePath"] == (
        ".llm-handoff-artifacts/p0-07/dependency-cache/maven"
    )
    assert _sha256(P007_MANIFEST) == receipt["cacheManifestSha256"]

    manifest_lines = P007_MANIFEST.read_text(encoding="utf-8").splitlines()
    entry_count = int(receipt["entryCount"])
    assert len(manifest_lines) == entry_count
    manifest_paths = [line.split("  ", 1)[1] for line in manifest_lines]
    assert len(set(manifest_paths)) == entry_count

    cache_entries = list(P007_CACHE.rglob("*"))
    assert not [path for path in cache_entries if path.is_symlink()]
    assert not [path for path in cache_entries if path.name.endswith(".lastUpdated")]
    assert not [path for path in [P007_CACHE, *cache_entries] if path.stat().st_mode & 0o222]
    cache_files = {
        path.relative_to(P007_CACHE).as_posix()
        for path in cache_entries
        if path.is_file()
    }
    assert cache_files == set(manifest_paths)

    verification = subprocess.run(
        [
            "/usr/bin/sha256sum",
            "--check",
            "--strict",
            "--quiet",
            str(P007_MANIFEST),
        ],
        cwd=P007_CACHE,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert verification.returncode == 0, verification.stderr


def test_wrapper_rejects_every_cache_selector_except_the_exact_p007_path() -> None:
    default_environment = os.environ.copy()
    default_environment.pop("CODECROW_MAVEN_REPOSITORY", None)
    default_environment["CODECROW_P007_CACHE_RECEIPT_SHA256"] = _sha256(P007_RECEIPT)
    default_result = subprocess.run(
        [str(DERIVED_RUNNER), "/usr/bin/true"],
        cwd=REPOSITORY_ROOT,
        env=default_environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert default_result.returncode == 65
    assert "P0-07 frozen workspace cache" in default_result.stderr

    p003_result = _run_wrapper(
        "/usr/bin/true",
        environment={
            "CODECROW_MAVEN_REPOSITORY": str(
                REPOSITORY_ROOT
                / ".llm-handoff-artifacts/p0-03/dependency-cache/maven"
            )
        },
        timeout=10,
    )
    assert p003_result.returncode == 65
    assert "P0-07 frozen workspace cache" in p003_result.stderr

    with tempfile.TemporaryDirectory(prefix="p007-arbitrary-cache-") as directory:
        arbitrary_result = _run_wrapper(
            "/usr/bin/true",
            environment={"CODECROW_MAVEN_REPOSITORY": directory},
            timeout=10,
        )
    assert arbitrary_result.returncode == 65
    assert "P0-07 frozen workspace cache" in arbitrary_result.stderr

    with tempfile.TemporaryDirectory(prefix="p007-cache-link-") as directory:
        cache_link = Path(directory) / "maven"
        cache_link.symlink_to(P007_CACHE, target_is_directory=True)
        symlink_result = _run_wrapper(
            "/usr/bin/true",
            environment={"CODECROW_MAVEN_REPOSITORY": str(cache_link)},
            timeout=10,
        )
    assert symlink_result.returncode == 65
    assert "must not be selected through a symlink" in symlink_result.stderr


def test_cache_validator_requires_the_external_receipt_identity() -> None:
    environment = os.environ.copy()
    environment["CODECROW_MAVEN_REPOSITORY"] = str(P007_CACHE)
    environment.pop("CODECROW_P007_CACHE_RECEIPT_SHA256", None)
    missing = subprocess.run(
        [str(CACHE_VALIDATOR)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert missing.returncode == 65
    assert "receipt identity is required" in missing.stderr

    environment["CODECROW_P007_CACHE_RECEIPT_SHA256"] = "0" * 64
    mismatched = subprocess.run(
        [str(CACHE_VALIDATOR)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert mismatched.returncode == 65
    assert "receipt identity mismatch" in mismatched.stderr


def test_wrapper_mounts_cache_read_only_unshares_network_and_clears_env() -> None:
    ledger_root = REPOSITORY_ROOT / ".llm-handoff-artifacts/p0-03/test-ledgers"
    ledger_path = ledger_root / "p0-07-java-coverage-wrapper.json"
    ledger_directory = ledger_root / "p0-07-java-coverage-wrapper"
    host_network_namespace = os.readlink("/proc/self/ns/net")
    program = """
import os
import socket
import sys
from pathlib import Path

assert 'P007_HOSTILE_SECRET' not in os.environ
assert os.environ['CODECROW_EXTERNAL_CALL_LEDGER'] == sys.argv[1]
assert os.environ['CODECROW_EXTERNAL_CALL_LEDGER_DIR'] == sys.argv[2]
assert '/.llm-handoff-artifacts/p0-03/' in os.environ['CODECROW_EXTERNAL_CALL_LEDGER']
assert '/.llm-handoff-artifacts/p0-07/' not in os.environ['CODECROW_EXTERNAL_CALL_LEDGER']
assert os.environ['MAVEN_OPTS'] == (
    '-Dmaven.repo.local=/tmp/codecrow-maven-repository '
    '-Duser.home=/tmp/codecrow-home'
)
assert os.environ['HOME'] == '/tmp/codecrow-home'
assert os.environ['JAVA_HOME']
assert os.environ['CODECROW_INTERNAL_SECRET'] == 'test-secret-token'
assert Path('/tmp/codecrow-maven-repository').is_dir()
assert not os.access('/tmp/codecrow-maven-repository', os.W_OK)

mount = next(
    line for line in Path('/proc/self/mountinfo').read_text().splitlines()
    if line.split()[4] == '/tmp/codecrow-maven-repository'
)
assert 'ro' in mount.split()[5].split(',')
network_namespace = os.readlink('/proc/self/ns/net')
assert network_namespace != sys.argv[3]
probe = socket.socket()
probe.settimeout(0.05)
assert probe.connect_ex(('192.0.2.1', 443)) != 0
print(network_namespace)
print('offline-wrapper-isolated')
"""
    result = _run_wrapper(
        "/usr/bin/python3",
        "-I",
        "-S",
        "-c",
        program,
        str(ledger_path),
        str(ledger_directory),
        host_network_namespace,
        environment={
            "CODECROW_EXTERNAL_CALL_LEDGER": str(ledger_path),
            "CODECROW_EXTERNAL_CALL_LEDGER_DIR": str(ledger_directory),
            "P007_HOSTILE_SECRET": "must-not-enter-namespace",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "offline-wrapper-isolated"
    assert ledger_directory.is_dir()


def test_wrapper_runs_a_successful_offline_maven_smoke() -> None:
    result = _run_wrapper("/usr/bin/mvn", "--offline", "--version")
    assert result.returncode == 0, result.stderr
    assert "Apache Maven" in result.stdout
    assert "Java version: 17" in result.stdout
