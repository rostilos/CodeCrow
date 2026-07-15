#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


_DIGEST = r"sha256:[0-9a-f]{64}"
_RUNTIME_REFERENCE = re.compile(
    rf"^(?:[a-z0-9]+(?:[._-][a-z0-9]+)*/)*[a-z0-9]+(?:[._-][a-z0-9]+)*@{_DIGEST}$"
)
_CANONICAL_REFERENCE = re.compile(
    rf"^docker\.io/(?:[a-z0-9]+(?:[._-][a-z0-9]+)*/)+"
    rf"[a-z0-9]+(?:[._-][a-z0-9]+)*@{_DIGEST}$"
)
_IMAGE_ID = re.compile(rf"^{_DIGEST}$")


def main(arguments: list[str]) -> int:
    if len(arguments) == 2 and arguments[0] == "--print-runtime-references":
        try:
            manifest = _load_regular_json(Path(arguments[1]), "persistence image manifest")
            references = _validate_manifest(manifest)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"ERROR: invalid persistence image provenance: {error}", file=sys.stderr)
            return 1
        for runtime, _ in references:
            print(runtime)
        return 0
    if len(arguments) != 2:
        print(
            "usage: validate-persistence-images.py "
            "[--print-runtime-references] <persistence-images.json> "
            "[docker-image-inspect.json]",
            file=sys.stderr,
        )
        return 64
    manifest_path, inspect_path = map(Path, arguments)
    try:
        manifest = _load_regular_json(manifest_path, "persistence image manifest")
        inspected = _load_regular_json(inspect_path, "Docker image inspection")
        references = _validate_manifest(manifest)
        _validate_inspection(references, inspected)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: invalid persistence image provenance: {error}", file=sys.stderr)
        return 1
    print(
        f"validated {len(references)} preloaded linux/amd64 persistence images "
        "at exact approved Docker Hub digests"
    )
    return 0


def _load_regular_json(path: Path, label: str) -> object:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular, non-symlink file")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_manifest(document: object) -> list[tuple[str, str]]:
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "registry_origin",
        "authentication_origin",
        "credential_mode",
        "images",
    }:
        raise ValueError("manifest fields do not match persistence-images v1")
    if document["schema_version"] != "1.0":
        raise ValueError("manifest schema_version must be 1.0")
    origins = {
        "registry_origin": ("https://registry-1.docker.io", "registry-1.docker.io"),
        "authentication_origin": ("https://auth.docker.io", "auth.docker.io"),
    }
    for field, (approved, hostname) in origins.items():
        origin = document[field]
        if origin != approved:
            raise ValueError(f"manifest contains an unapproved {field.replace('_', ' ')}")
        parsed_origin = urlsplit(origin)
        if (
            parsed_origin.scheme != "https"
            or parsed_origin.hostname != hostname
            or parsed_origin.username
            or parsed_origin.password
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise ValueError("image pull origins must be credential-free HTTPS origins only")
    if document["credential_mode"] != "anonymous":
        raise ValueError("persistence image preload must use anonymous credentials")

    images = document["images"]
    if not isinstance(images, list) or len(images) != 3:
        raise ValueError("manifest must contain exactly three persistence images")
    references: list[tuple[str, str]] = []
    for image in images:
        if not isinstance(image, dict) or set(image) != {
            "runtime_reference",
            "canonical_reference",
            "os",
            "architecture",
        }:
            raise ValueError("persistence image fields are incomplete or unknown")
        runtime = image["runtime_reference"]
        canonical = image["canonical_reference"]
        if not isinstance(runtime, str) or not _RUNTIME_REFERENCE.fullmatch(runtime):
            raise ValueError("runtime image reference must use an exact SHA-256 digest")
        if not isinstance(canonical, str) or not _CANONICAL_REFERENCE.fullmatch(canonical):
            raise ValueError("canonical image reference must use docker.io and an exact digest")
        if _canonicalize(runtime) != canonical:
            raise ValueError("runtime and canonical image references do not identify the same image")
        if image["os"] != "linux" or image["architecture"] != "amd64":
            raise ValueError("persistence images must be pinned to linux/amd64")
        references.append((runtime, canonical))
    if len({canonical for _, canonical in references}) != len(references):
        raise ValueError("persistence image references must be unique")
    return references


def _validate_inspection(
    references: list[tuple[str, str]], inspected_document: object
) -> None:
    if not isinstance(inspected_document, list) or len(inspected_document) != len(references):
        raise ValueError("Docker inspection must contain exactly the approved images")
    approved = {canonical for _, canonical in references}
    observed: set[str] = set()
    for image in inspected_document:
        if not isinstance(image, dict):
            raise ValueError("Docker inspection contains a non-object image")
        image_id = image.get("Id")
        if not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id):
            raise ValueError("Docker inspection contains an invalid image ID")
        if image.get("Os") != "linux" or image.get("Architecture") != "amd64":
            raise ValueError("Docker inspection contains a non-linux/amd64 image")
        repo_digests = image.get("RepoDigests")
        if not isinstance(repo_digests, list):
            raise ValueError("Docker inspection is missing RepoDigests")
        matching = {
            _canonicalize(reference)
            for reference in repo_digests
            if isinstance(reference, str) and _RUNTIME_REFERENCE.fullmatch(reference)
        } & approved
        if len(matching) != 1:
            raise ValueError("Docker inspection does not prove one exact approved digest")
        observed.update(matching)
    if observed != approved:
        raise ValueError("Docker inspection did not prove every approved image digest")


def _canonicalize(reference: str) -> str:
    name, digest = reference.split("@", 1)
    if name.startswith("docker.io/"):
        path = name.removeprefix("docker.io/")
    else:
        path = name
    if "/" not in path:
        path = "library/" + path
    return f"docker.io/{path}@{digest}"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
