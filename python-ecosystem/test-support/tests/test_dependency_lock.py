from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LOCK = REPOSITORY_ROOT / "tools" / "offline-harness" / "requirements" / "ci-test.lock"
DIGEST = LOCK.with_suffix(".lock.sha256")
ALLOWLIST = (
    REPOSITORY_ROOT
    / "tools"
    / "offline-harness"
    / "requirements"
    / "build-network-allowlist.txt"
)
MAVEN_SETTINGS = (
    REPOSITORY_ROOT / "tools" / "offline-harness" / "maven" / "settings-ci.xml"
)
MAVEN_MANIFEST = (
    REPOSITORY_ROOT / "tools" / "offline-harness" / "bin" / "manifest-maven-cache.py"
)
PROVENANCE_VALIDATOR = (
    REPOSITORY_ROOT
    / "tools"
    / "offline-harness"
    / "bin"
    / "validate-build-provenance.py"
)
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "offline-tests.yml"


def test_ci_lock_is_exact_hashed_and_matches_committed_digest() -> None:
    lock_bytes = LOCK.read_bytes()
    expected_digest, expected_path = DIGEST.read_text().strip().split(maxsplit=1)
    assert expected_path == "tools/offline-harness/requirements/ci-test.lock"
    assert hashlib.sha256(lock_bytes).hexdigest() == expected_digest

    lines = LOCK.read_text().splitlines()
    requirements = [
        line for line in lines if line and not line[0].isspace() and not line.startswith("#")
    ]
    assert requirements
    assert all("==" in requirement and requirement.endswith(" \\") for requirement in requirements)
    assert sum("--hash=sha256:" in line for line in lines) >= len(requirements)
    assert "respx==0.22.0 \\" in requirements


def test_build_dependency_origins_are_explicit_https_and_credential_free() -> None:
    origins = [
        line
        for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert origins == [
        "https://pypi.org/simple/",
        "https://files.pythonhosted.org/",
        "https://repo.maven.apache.org/maven2/",
        "https://registry-1.docker.io/v2/",
        "https://auth.docker.io/token",
    ]
    assert all("@" not in origin.partition("://")[2] for origin in origins)

    tree = ElementTree.parse(MAVEN_SETTINGS)
    namespace = {"m": "http://maven.apache.org/SETTINGS/1.2.0"}
    urls = [element.text for element in tree.findall(".//m:url", namespace)]
    assert urls and set(urls) == {"https://repo.maven.apache.org/maven2/"}
    assert tree.findtext(".//m:mirrorOf", namespaces=namespace) == "*"
    assert tree.findall(".//m:server", namespace) == []


def test_ci_attests_complete_profile_cache_before_offline_clean_verification() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    go_offline = workflow.index("-DskipTests dependency:go-offline")
    profile_install = workflow.index("-DskipTests clean install")
    surefire_provider = workflow.index(
        "org.apache.maven.plugins:maven-dependency-plugin:3.6.1:get"
    )
    junit_launcher = workflow.index(
        "-Dartifact=org.junit.platform:junit-platform-launcher:1.10.2"
    )
    cache_manifest = workflow.index("manifest-maven-cache.py")
    assert (
        go_offline
        < profile_install
        < surefire_provider
        < junit_launcher
        < cache_manifest
    )
    assert (
        "-Dartifact=org.apache.maven.surefire:surefire-junit-platform:3.2.5"
        in workflow
    )
    assert "p007-prebuild-without-integration-execution" in workflow
    assert "-DskipITs" not in workflow
    assert "-Dtransitive=true" in workflow
    assert workflow.count("-N -B --no-transfer-progress") == 2
    assert "-o -B --no-transfer-progress" in workflow
    assert "-pl libs/test-support -am clean verify" in workflow
    assert "java-ecosystem/**/target/failsafe-reports/" in workflow


def test_maven_cache_manifest_hashes_every_regular_file_deterministically(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "z").mkdir(parents=True)
    (repository / "a.bin").write_bytes(b"a")
    (repository / "z" / "b.jar").write_bytes(b"b")
    output = tmp_path / "manifest.txt"
    result = subprocess.run(
        [sys.executable, str(MAVEN_MANIFEST), str(repository), str(output)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").splitlines() == [
        f"{hashlib.sha256(b'a').hexdigest()}  a.bin",
        f"{hashlib.sha256(b'b').hexdigest()}  z/b.jar",
    ]

    (repository / "linked.jar").symlink_to(repository / "a.bin")
    rejected = subprocess.run(
        [sys.executable, str(MAVEN_MANIFEST), str(repository), str(output)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode == 1
    assert "contains a symlink" in rejected.stderr

    empty = tmp_path / "empty"
    empty.mkdir()
    assert subprocess.run(
        [sys.executable, str(MAVEN_MANIFEST), str(empty), str(output)],
        check=False,
    ).returncode == 1

    output.unlink()
    output.symlink_to(tmp_path / "elsewhere")
    assert subprocess.run(
        [sys.executable, str(MAVEN_MANIFEST), str(repository.parent), str(output)],
        check=False,
    ).returncode == 1


def test_build_provenance_validator_rejects_unapproved_urls_and_missing_hashes(
    tmp_path: Path,
) -> None:
    report = tmp_path / "pip-report.json"
    manifest = tmp_path / "maven-manifest.txt"
    repository = tmp_path / "maven-repository"
    artifact = repository / "group" / "artifact.jar"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact")
    report.write_text(
        """{
  "install": [{
    "download_info": {
      "url": "https://files.pythonhosted.org/packages/offline.whl",
      "archive_info": {"hashes": {"sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}
    }
  }]
}
""",
        encoding="utf-8",
    )
    manifest.write_text(
        f"{hashlib.sha256(b'artifact').hexdigest()}  group/artifact.jar\n",
        encoding="utf-8",
    )

    def validate() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(PROVENANCE_VALIDATOR),
                str(report),
                str(ALLOWLIST),
                str(MAVEN_SETTINGS),
                str(manifest),
                str(repository),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    assert validate().returncode == 0
    document = json.loads(report.read_text(encoding="utf-8"))
    document["install"][0]["download_info"]["url"] = "https://evil.invalid/package.whl"
    report.write_text(json.dumps(document), encoding="utf-8")
    assert "unapproved artifact origin" in validate().stderr

    document["install"][0]["download_info"]["url"] = (
        "https://files.pythonhosted.org/packages/offline.whl"
    )
    document["install"][0]["download_info"]["archive_info"]["hashes"] = {}
    report.write_text(json.dumps(document), encoding="utf-8")
    assert "missing a SHA-256" in validate().stderr
    document["install"][0]["download_info"]["archive_info"]["hashes"] = {
        "sha256": "a" * 64
    }
    report.write_text(json.dumps(document), encoding="utf-8")
    artifact.write_bytes(b"mutated")
    assert "digest mismatch" in validate().stderr
