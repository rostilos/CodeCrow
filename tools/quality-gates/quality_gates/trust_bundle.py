"""Verify the externally pinned P0-07 quality-contract bundle."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Mapping

from .changed_coverage import GateInputError
from .git_changes import _parse_contract_json, _read_contract_file
from .source_inventory import (
    _assert_repository_root_stable,
    _open_repository_root,
    _read_file_at,
    _repository_path,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_BUNDLE_BYTES = 1024 * 1024
_MAX_TRUSTED_FILE_BYTES = 64 * 1024 * 1024
_ROLES = {"implementation", "policy", "schema", "workflow", "runner"}
_REQUIRED_PATHS = {
    ".github/CODEOWNERS",
    ".github/workflows/offline-tests.yml",
    "java-ecosystem/libs/analysis-api/pom.xml",
    "java-ecosystem/libs/analysis-engine/pom.xml",
    "java-ecosystem/libs/ast-parser/pom.xml",
    "java-ecosystem/libs/commit-graph/pom.xml",
    "java-ecosystem/libs/core/pom.xml",
    "java-ecosystem/libs/core/src/main/resources/application.yml",
    "java-ecosystem/libs/core/src/main/resources/db/migration/managed/V2.14.0__workspace_analysis_limits.sql",
    "java-ecosystem/libs/core/src/main/resources/db/migration/managed/V2.15.0__immutable_execution_manifest.sql",
    "java-ecosystem/libs/email/pom.xml",
    "java-ecosystem/libs/events/pom.xml",
    "java-ecosystem/libs/file-content/pom.xml",
    "java-ecosystem/libs/queue/pom.xml",
    "java-ecosystem/libs/rag-engine/pom.xml",
    "java-ecosystem/libs/security/pom.xml",
    "java-ecosystem/libs/task-management/pom.xml",
    "java-ecosystem/libs/test-support/pom.xml",
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
    "java-ecosystem/libs/vcs-client/pom.xml",
    "java-ecosystem/mcp-servers/platform-mcp/pom.xml",
    "java-ecosystem/mcp-servers/vcs-mcp/pom.xml",
    "java-ecosystem/pom.xml",
    "java-ecosystem/quality/coverage-aggregate/pom.xml",
    "java-ecosystem/quality/guarded-test-runtime/mockito-extensions/org.mockito.plugins.MemberAccessor",
    "java-ecosystem/quality/guarded-test-runtime/mockito-extensions/org.mockito.plugins.MockMaker",
    "java-ecosystem/services/pipeline-agent/pom.xml",
    "java-ecosystem/services/web-server/pom.xml",
    "java-ecosystem/services/web-server/src/it/java/org/rostilos/codecrow/webserver/BaseWebServerIT.java",
    "java-ecosystem/services/web-server/src/it/java/org/rostilos/codecrow/webserver/ManagedImmutableManifestFlywayIT.java",
    "java-ecosystem/services/web-server/src/it/resources/application-it.properties",
    "java-ecosystem/services/web-server/src/main/java/org/rostilos/codecrow/webserver/WebserverApplication.java",
    "java-ecosystem/services/web-server/src/main/resources/logback-spring.xml",
    "tools/offline-harness/bin/manifest-maven-cache.py",
    "tools/offline-harness/bin/run-offline.sh",
    "tools/offline-harness/bin/validate-build-provenance.py",
    "tools/offline-harness/bin/validate-docker-image-events.py",
    "tools/offline-harness/bin/validate-ledgers.py",
    "tools/offline-harness/bin/validate-persistence-container-report.py",
    "tools/offline-harness/bin/validate-persistence-images.py",
    "tools/offline-harness/maven/settings-ci.xml",
    "tools/offline-harness/requirements/build-network-allowlist.txt",
    "tools/offline-harness/requirements/certifi-cacert.sha256",
    "tools/offline-harness/requirements/ci-test.in",
    "tools/offline-harness/requirements/ci-test.lock",
    "tools/offline-harness/requirements/ci-test.lock.sha256",
    "tools/offline-harness/requirements/persistence-images-v1.json",
    "tools/quality-gates/bin/java-legacy-it-a-supervisor.sh",
    "tools/quality-gates/bin/run-java-coverage-offline.sh",
    "tools/quality-gates/bin/run-java-legacy-it-guarded.sh",
    "tools/quality-gates/bin/run-locked-python.sh",
    "tools/quality-gates/bin/validate-p007-maven-cache.sh",
    "tools/quality-gates/config/inference.coveragerc",
    "tools/quality-gates/config/quality-gates.coveragerc",
    "tools/quality-gates/config/rag.coveragerc",
    "tools/quality-gates/policy/comparison-base-v1.json",
    "tools/quality-gates/policy/correctness-policy-v1.json",
    "tools/quality-gates/policy/coverage-baseline-v1.json",
    "tools/quality-gates/policy/coverage-domains-v1.json",
    "tools/quality-gates/policy/exclusions-v1.json",
    "tools/quality-gates/policy/java-legacy-it-container-quarantine-v1.json",
    "tools/quality-gates/policy/java-legacy-it-inventory-v1.json",
    "tools/quality-gates/policy/java-legacy-it-tools-v1.json",
    "tools/quality-gates/policy/java-modules-v1.json",
    "tools/quality-gates/policy/mutation-profile-v1.json",
    "tools/quality-gates/policy/source-inventory-policy-v1.json",
    "tools/quality-gates/policy/source-snapshot-v1.json",
    "tools/quality-gates/quality_gates/__init__.py",
    "tools/quality-gates/quality_gates/__main__.py",
    "tools/quality-gates/quality_gates/baseline.py",
    "tools/quality-gates/quality_gates/changed_coverage.py",
    "tools/quality-gates/quality_gates/cli.py",
    "tools/quality-gates/quality_gates/correctness_policy.py",
    "tools/quality-gates/quality_gates/git_changes.py",
    "tools/quality-gates/quality_gates/java_legacy_it.py",
    "tools/quality-gates/quality_gates/mutation_gate.py",
    "tools/quality-gates/quality_gates/normalized_reports.py",
    "tools/quality-gates/quality_gates/source_inventory.py",
    "tools/quality-gates/quality_gates/trust_bundle.py",
    "tools/quality-gates/schema/compensating-receipt-v1.schema.json",
    "tools/quality-gates/schema/coverage-baseline-v1.schema.json",
    "tools/quality-gates/schema/coverage-exclusions-v1.schema.json",
    "tools/quality-gates/schema/gate-result-v1.schema.json",
    "tools/quality-gates/schema/mutation-profile-v1.schema.json",
    "tools/quality-gates/schema/normalized-coverage-v1.schema.json",
    "tools/quality-gates/schema/source-inventory-v1.schema.json",
    "tools/quality-gates/schema/source-snapshot-v1.schema.json",
    "tools/quality-gates/schema/trust-bundle-v1.schema.json",
    "tools/quality-gates/tests/test_compensating_configuration_contracts.py",
    "tools/quality-gates/tests/test_real_mutation_contracts.py",
}


def _role_for_path(path: str) -> str:
    if path.startswith(".github/"):
        return "workflow"
    if "/schema/" in path:
        return "schema"
    if (
        "/policy/" in path
        or "/config/" in path
        or path.endswith("/pom.xml")
        or "/maven/" in path
        or "/requirements/" in path
    ):
        return "policy"
    if "/bin/" in path:
        return "runner"
    return "implementation"


def create_trust_bundle(*, repository_root: Path) -> Mapping[str, Any]:
    """Create the deterministic reviewed-path bundle; pinning remains external."""

    repository = Path(os.path.abspath(repository_root))
    root_descriptor = _open_repository_root(repository)
    try:
        files = []
        for path in sorted(_REQUIRED_PATHS):
            raw = _read_file_at(
                root_descriptor,
                path,
                field=f"trusted quality contract {path}",
                size_limit=_MAX_TRUSTED_FILE_BYTES,
            )
            files.append(
                {
                    "path": path,
                    "role": _role_for_path(path),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        _assert_repository_root_stable(repository, root_descriptor)
    finally:
        os.close(root_descriptor)
    return {
        "schemaVersion": 1,
        "bundleId": "p0-07-quality-contract-v1",
        "files": files,
    }


def verify_trust_bundle(
    bundle_path: Path,
    *,
    expected_sha256: str,
    repository_root: Path,
) -> Mapping[str, Any]:
    """Verify bundle bytes and every bound file through stable no-follow FDs."""

    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise GateInputError("quality trust bundle digest is malformed")
    raw = _read_contract_file(
        bundle_path,
        repository_root=repository_root,
        field="quality trust bundle",
        size_limit=_MAX_BUNDLE_BYTES,
    )
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise GateInputError("quality trust bundle digest mismatch")
    bundle = _parse_contract_json(raw, "quality trust bundle")
    if (
        not isinstance(bundle, Mapping)
        or set(bundle) != {"schemaVersion", "bundleId", "files"}
        or bundle.get("schemaVersion") != 1
        or bundle.get("bundleId") != "p0-07-quality-contract-v1"
        or not isinstance(bundle.get("files"), list)
        or not bundle["files"]
    ):
        raise GateInputError("quality trust bundle contract is malformed")

    entries: dict[str, tuple[str, str]] = {}
    previous_path = ""
    for entry in bundle["files"]:
        if not isinstance(entry, Mapping) or set(entry) != {"path", "role", "sha256"}:
            raise GateInputError("quality trust bundle entry is malformed")
        path = _repository_path(entry.get("path"), "quality trust bundle path")
        role = entry.get("role")
        digest = entry.get("sha256")
        if (
            path <= previous_path
            or not isinstance(role, str)
            or role not in _ROLES
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
        ):
            raise GateInputError("quality trust bundle entry is malformed or unsorted")
        previous_path = path
        entries[path] = (role, digest)
    missing = sorted(_REQUIRED_PATHS - set(entries))
    if missing:
        raise GateInputError(f"quality trust bundle omits required path: {missing[0]}")

    repository = Path(os.path.abspath(repository_root))
    root_descriptor = _open_repository_root(repository)
    try:
        for path, (_, expected_digest) in entries.items():
            file_raw = _read_file_at(
                root_descriptor,
                path,
                field=f"trusted quality contract {path}",
                size_limit=_MAX_TRUSTED_FILE_BYTES,
            )
            if hashlib.sha256(file_raw).hexdigest() != expected_digest:
                raise GateInputError(f"trusted quality contract drifted: {path}")
        _assert_repository_root_stable(repository, root_descriptor)
    finally:
        os.close(root_descriptor)
    return bundle
