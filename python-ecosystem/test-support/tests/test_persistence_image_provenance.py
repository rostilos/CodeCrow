from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = (
    REPOSITORY_ROOT
    / "tools"
    / "offline-harness"
    / "requirements"
    / "persistence-images-v1.json"
)
VALIDATOR = (
    REPOSITORY_ROOT
    / "tools"
    / "offline-harness"
    / "bin"
    / "validate-persistence-images.py"
)
EVENT_VALIDATOR = (
    REPOSITORY_ROOT
    / "tools"
    / "offline-harness"
    / "bin"
    / "validate-docker-image-events.py"
)
CONTAINER_REPORT_VALIDATOR = (
    REPOSITORY_ROOT
    / "tools"
    / "offline-harness"
    / "bin"
    / "validate-persistence-container-report.py"
)
JAVA_SUPPORT = (
    REPOSITORY_ROOT
    / "java-ecosystem"
    / "libs"
    / "test-support"
    / "src"
    / "main"
    / "java"
    / "org"
    / "rostilos"
    / "codecrow"
    / "testsupport"
    / "offline"
    / "OfflinePersistenceSupport.java"
)


def _inspection(manifest: dict[str, object]) -> list[dict[str, object]]:
    images = manifest["images"]
    assert isinstance(images, list)
    return [
        {
            "Id": f"sha256:{index:064x}",
            "RepoDigests": [image["runtime_reference"]],
            "Os": "linux",
            "Architecture": "amd64",
        }
        for index, image in enumerate(images, start=1)
    ]


def _validate(
    manifest_path: Path, inspect_path: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(manifest_path), str(inspect_path)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_persistence_manifest_is_exact_and_matches_java_runtime_references(
    tmp_path: Path,
) -> None:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert document["registry_origin"] == "https://registry-1.docker.io"
    assert document["authentication_origin"] == "https://auth.docker.io"
    assert document["credential_mode"] == "anonymous"
    images = document["images"]
    assert len(images) == 3
    assert all(image["os"] == "linux" for image in images)
    assert all(image["architecture"] == "amd64" for image in images)
    java_source = JAVA_SUPPORT.read_text(encoding="utf-8")
    assert all(image["runtime_reference"] in java_source for image in images)

    inspection = tmp_path / "inspect.json"
    inspection.write_text(json.dumps(_inspection(document)), encoding="utf-8")
    result = _validate(MANIFEST, inspection)
    assert result.returncode == 0, result.stderr
    assert "validated 3 preloaded linux/amd64" in result.stdout
    listed = subprocess.run(
        [sys.executable, str(VALIDATOR), "--print-runtime-references", str(MANIFEST)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.splitlines() == [
        image["runtime_reference"] for image in images
    ]


def test_persistence_validator_rejects_digest_platform_and_manifest_drift(
    tmp_path: Path,
) -> None:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    inspection_path = tmp_path / "inspect.json"
    inspection = _inspection(document)
    inspection_path.write_text(json.dumps(inspection), encoding="utf-8")
    assert _validate(manifest, inspection_path).returncode == 0

    wrong_digest = copy.deepcopy(inspection)
    wrong_digest[0]["RepoDigests"] = ["postgres@sha256:" + "f" * 64]
    inspection_path.write_text(json.dumps(wrong_digest), encoding="utf-8")
    assert "one exact approved digest" in _validate(manifest, inspection_path).stderr

    wrong_platform = copy.deepcopy(inspection)
    wrong_platform[0]["Architecture"] = "arm64"
    inspection_path.write_text(json.dumps(wrong_platform), encoding="utf-8")
    assert "non-linux/amd64" in _validate(manifest, inspection_path).stderr

    drifted_manifest = copy.deepcopy(document)
    drifted_manifest["registry_origin"] = "https://unapproved.invalid"
    manifest.write_text(json.dumps(drifted_manifest), encoding="utf-8")
    inspection_path.write_text(json.dumps(inspection), encoding="utf-8")
    assert "unapproved registry origin" in _validate(manifest, inspection_path).stderr


def test_persistence_validator_rejects_symlink_inputs(tmp_path: Path) -> None:
    inspection = tmp_path / "inspect.json"
    inspection.write_text("[]", encoding="utf-8")
    linked_manifest = tmp_path / "manifest-link.json"
    linked_manifest.symlink_to(MANIFEST)
    result = _validate(linked_manifest, inspection)
    assert result.returncode == 1
    assert "regular, non-symlink" in result.stderr


def test_runtime_image_event_validator_accepts_no_egress_and_rejects_pull_push(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events.jsonl"

    def validate() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(EVENT_VALIDATOR), str(events)],
            text=True,
            capture_output=True,
            check=False,
        )

    events.write_text("", encoding="utf-8")
    result = validate()
    assert result.returncode == 0, result.stderr
    assert "no pull or push" in result.stdout

    events.write_text(
        json.dumps({"Type": "image", "Action": "tag"}) + "\n",
        encoding="utf-8",
    )
    assert validate().returncode == 0

    for action in ("pull", "push"):
        events.write_text(
            json.dumps({"Type": "image", "Action": action}) + "\n",
            encoding="utf-8",
        )
        rejected = validate()
        assert rejected.returncode == 1
        assert f"forbidden Docker image {action}" in rejected.stderr

    events.write_text(json.dumps({"Type": "container", "Action": "start"}) + "\n")
    assert "malformed Docker image event" in validate().stderr


def test_container_report_validator_requires_exact_absent_owned_ids(
    tmp_path: Path,
) -> None:
    identities = [
        ("first-generation", "postgres"),
        ("first-generation", "redis"),
        ("first-generation", "qdrant"),
        ("restarted-generation", "postgres"),
        ("restarted-generation", "redis"),
        ("restarted-generation", "qdrant"),
    ]
    report = {
        "schemaVersion": "1.0",
        "scenarioNamespace": "fixture_a9fbed3007f539cc",
        "containers": [
            {
                "generation": generation,
                "service": service,
                "containerId": f"{index:064x}",
                "status": "absent",
            }
            for index, (generation, service) in enumerate(identities, start=1)
        ],
    }
    path = tmp_path / "container-report.json"

    def validate(*prefix: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CONTAINER_REPORT_VALIDATOR), *prefix, str(path)],
            text=True,
            capture_output=True,
            check=False,
        )

    path.write_text(json.dumps(report), encoding="utf-8")
    result = validate()
    assert result.returncode == 0, result.stderr
    listed = validate("--print-container-ids")
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.splitlines() == [
        container["containerId"] for container in report["containers"]
    ]

    retained = copy.deepcopy(report)
    retained["containers"][2]["status"] = "present:running"
    path.write_text(json.dumps(retained), encoding="utf-8")
    assert "retained after cleanup" in validate().stderr

    duplicate = copy.deepcopy(report)
    duplicate["containers"][5]["containerId"] = duplicate["containers"][0]["containerId"]
    path.write_text(json.dumps(duplicate), encoding="utf-8")
    assert "must be unique" in validate().stderr

    reordered = copy.deepcopy(report)
    reordered["containers"][0], reordered["containers"][1] = (
        reordered["containers"][1],
        reordered["containers"][0],
    )
    path.write_text(json.dumps(reordered), encoding="utf-8")
    assert "unexpected identity or order" in validate().stderr
