from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = QUALITY_ROOT.parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates import trust_bundle as trust  # noqa: E402


GUARDED_EVIDENCE_RUNTIME_PATHS = {
    "java-ecosystem/libs/core/src/main/resources/application.yml",
    "java-ecosystem/libs/core/src/main/resources/db/migration/managed/V2.14.0__workspace_analysis_limits.sql",
    "java-ecosystem/libs/core/src/main/resources/db/migration/managed/V2.15.0__immutable_execution_manifest.sql",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/base/IntegrationTest.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/cleanup/DatabaseCleaner.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/containers/SharedPostgresContainer.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/initializer/PostgresContainerInitializer.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/legacy/LegacyContainerEndpoints.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/legacy/LegacyContainerItContract.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/legacy/LegacyContainerItLauncherSessionListener.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/legacy/LegacyContainerItRuntime.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/legacy/LegacyContainerItSession.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/legacy/LegacyContainerLedgerExporter.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/legacy/LegacyContainerModuleVisibility.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/legacy/LegacyContainerSafePaths.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/legacy/LegacyContainerVisibility.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/offline/ExternalCall.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/offline/ExternalCallLedger.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/offline/ExternalCallLedgerDocument.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/offline/NetworkDenyGuard.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/offline/OfflineNetworkBoundary.java",
    "java-ecosystem/libs/test-support/src/main/java/org/rostilos/codecrow/testsupport/offline/UnexpectedExternalCall.java",
    "java-ecosystem/libs/test-support/src/main/resources/META-INF/services/org.junit.platform.launcher.LauncherSessionListener",
    "java-ecosystem/quality/guarded-test-runtime/mockito-extensions/org.mockito.plugins.MemberAccessor",
    "java-ecosystem/quality/guarded-test-runtime/mockito-extensions/org.mockito.plugins.MockMaker",
    "java-ecosystem/services/web-server/src/it/java/org/rostilos/codecrow/webserver/BaseWebServerIT.java",
    "java-ecosystem/services/web-server/src/it/java/org/rostilos/codecrow/webserver/ManagedImmutableManifestFlywayIT.java",
    "java-ecosystem/services/web-server/src/it/resources/application-it.properties",
    "java-ecosystem/services/web-server/src/main/java/org/rostilos/codecrow/webserver/WebserverApplication.java",
    "java-ecosystem/services/web-server/src/main/resources/logback-spring.xml",
}


def _bundle(repository: Path, entries: list[tuple[str, str]]) -> tuple[Path, str]:
    value = {
        "schemaVersion": 1,
        "bundleId": "p0-07-quality-contract-v1",
        "files": [
            {
                "path": path,
                "role": role,
                "sha256": hashlib.sha256((repository / path).read_bytes()).hexdigest(),
            }
            for path, role in entries
        ],
    }
    path = repository / "bundle.json"
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("path", "role"),
    [
        (".github/CODEOWNERS", "workflow"),
        ("tools/schema/value.json", "schema"),
        ("tools/policy/value.json", "policy"),
        ("tools/config/value.ini", "policy"),
        ("java/module/pom.xml", "policy"),
        ("tools/maven/settings.xml", "policy"),
        ("tools/requirements/lock.txt", "policy"),
        ("tools/bin/run.sh", "runner"),
        ("tools/quality/gate.py", "implementation"),
    ],
)
def test_trust_bundle_roles_are_deterministic(path: str, role: str) -> None:
    assert trust._role_for_path(path) == role


def test_required_trust_inventory_covers_every_runtime_contract_and_java_pom() -> None:
    required = set(trust._REQUIRED_PATHS)
    expected: set[str] = set()
    for directory in ("bin", "config", "quality_gates", "schema"):
        expected.update(
            path.relative_to(REPOSITORY_ROOT).as_posix()
            for path in (QUALITY_ROOT / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    expected.update(
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in (QUALITY_ROOT / "policy").glob("*.json")
        if path.name != "trust-bundle-v1.json"
    )
    expected.update(
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in (REPOSITORY_ROOT / "java-ecosystem").rglob("pom.xml")
        if "target" not in path.parts
    )
    expected.update(
        {
            ".github/CODEOWNERS",
            ".github/workflows/offline-tests.yml",
            "tools/quality-gates/tests/test_real_mutation_contracts.py",
        }
    )
    assert expected <= required
    assert GUARDED_EVIDENCE_RUNTIME_PATHS <= required


def test_trust_bundle_binds_exact_sorted_required_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    implementation = tmp_path / "gate.py"
    policy = tmp_path / "policy.json"
    implementation.write_text("VALUE = 1\n", encoding="utf-8")
    policy.write_text("{}\n", encoding="utf-8")
    entries = [("gate.py", "implementation"), ("policy.json", "policy")]
    monkeypatch.setattr(trust, "_REQUIRED_PATHS", {path for path, _ in entries})
    bundle, digest = _bundle(tmp_path, entries)

    verified = trust.verify_trust_bundle(
        bundle, expected_sha256=digest, repository_root=tmp_path
    )
    assert verified["bundleId"] == "p0-07-quality-contract-v1"

    implementation.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(GateInputError, match="trusted quality contract drifted"):
        trust.verify_trust_bundle(
            bundle, expected_sha256=digest, repository_root=tmp_path
        )


def test_trust_bundle_capture_is_deterministic_complete_and_role_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = {
        ".github/workflows/gate.yml": "workflow",
        "java/pom.xml": "policy",
        "tools/bin/run.sh": "runner",
        "tools/policy/gate.json": "policy",
        "tools/quality/gate.py": "implementation",
        "tools/schema/gate.json": "schema",
    }
    for path in paths:
        artifact = tmp_path / path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(path + "\n", encoding="utf-8")
    monkeypatch.setattr(trust, "_REQUIRED_PATHS", set(paths))

    first = trust.create_trust_bundle(repository_root=tmp_path)
    second = trust.create_trust_bundle(repository_root=tmp_path)
    assert first == second
    assert [entry["path"] for entry in first["files"]] == sorted(paths)
    assert {entry["path"]: entry["role"] for entry in first["files"]} == paths


def test_trust_bundle_rejects_digest_omission_order_symlink_fifo_and_root_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.write_text("a\n", encoding="utf-8")
    second.write_text("b\n", encoding="utf-8")
    monkeypatch.setattr(trust, "_REQUIRED_PATHS", {"a", "b"})
    bundle, digest = _bundle(tmp_path, [("a", "policy"), ("b", "schema")])
    with pytest.raises(GateInputError, match="bundle digest mismatch"):
        trust.verify_trust_bundle(
            bundle, expected_sha256="0" * 64, repository_root=tmp_path
        )

    value = json.loads(bundle.read_text(encoding="utf-8"))
    value["files"].reverse()
    bundle.write_text(json.dumps(value), encoding="utf-8")
    reversed_digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    with pytest.raises(GateInputError, match="malformed or unsorted"):
        trust.verify_trust_bundle(
            bundle, expected_sha256=reversed_digest, repository_root=tmp_path
        )

    bundle, _ = _bundle(tmp_path, [("a", "policy"), ("b", "schema")])
    malformed_role = json.loads(bundle.read_text(encoding="utf-8"))
    malformed_role["files"][0]["role"] = []
    bundle.write_text(json.dumps(malformed_role), encoding="utf-8")
    malformed_role_digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    with pytest.raises(GateInputError, match="malformed or unsorted"):
        trust.verify_trust_bundle(
            bundle,
            expected_sha256=malformed_role_digest,
            repository_root=tmp_path,
        )

    bundle, digest = _bundle(tmp_path, [("a", "policy")])
    with pytest.raises(GateInputError, match="omits required path: b"):
        trust.verify_trust_bundle(
            bundle, expected_sha256=digest, repository_root=tmp_path
        )

    linked = tmp_path / "linked-bundle.json"
    linked.symlink_to(bundle)
    with pytest.raises(GateInputError, match="trusted regular file"):
        trust.verify_trust_bundle(
            linked, expected_sha256=digest, repository_root=tmp_path
        )

    fifo = tmp_path / "bundle.fifo"
    os.mkfifo(fifo)
    with pytest.raises(GateInputError, match="regular file"):
        trust.verify_trust_bundle(
            fifo, expected_sha256=digest, repository_root=tmp_path
        )

    root_link = tmp_path.parent / f"{tmp_path.name}-link"
    root_link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(GateInputError, match="repository root is not trusted"):
        trust.verify_trust_bundle(
            Path("bundle.json"), expected_sha256=digest, repository_root=root_link
        )


def test_trust_bundle_rejects_malformed_digest_contract_and_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "a"
    artifact.write_text("a\n", encoding="utf-8")
    monkeypatch.setattr(trust, "_REQUIRED_PATHS", {"a"})
    bundle, digest = _bundle(tmp_path, [("a", "policy")])

    with pytest.raises(GateInputError, match="digest is malformed"):
        trust.verify_trust_bundle(bundle, expected_sha256="bad", repository_root=tmp_path)

    bundle.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "bundleId": "p0-07-quality-contract-v1",
                "files": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(GateInputError, match="contract is malformed"):
        trust.verify_trust_bundle(
            bundle,
            expected_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
            repository_root=tmp_path,
        )

    bundle.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "bundleId": "p0-07-quality-contract-v1",
                "files": [{"path": "a", "role": "policy"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(GateInputError, match="entry is malformed"):
        trust.verify_trust_bundle(
            bundle,
            expected_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
            repository_root=tmp_path,
        )
