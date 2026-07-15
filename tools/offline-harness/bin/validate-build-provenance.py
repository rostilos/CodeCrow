#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from urllib.parse import urlsplit


_MANIFEST_LINE = re.compile(r"^[0-9a-f]{64}  [^\r\n]+$")
_PYTHON_ORIGINS = {
    ("https", "pypi.org"),
    ("https", "files.pythonhosted.org"),
}
_MAVEN_ORIGINS = {("https", "repo.maven.apache.org")}


def main(arguments: list[str]) -> int:
    if len(arguments) != 5:
        print(
            "usage: validate-build-provenance.py "
            "<pip-report.json> <allowlist.txt> <maven-settings.xml> "
            "<maven-manifest.txt> <maven-repository>",
            file=sys.stderr,
        )
        return 64
    report_path, allowlist_path, settings_path, manifest_path, repository_path = map(
        Path, arguments
    )
    try:
        allowed_urls = _allowed_urls(allowlist_path)
        if not (_PYTHON_ORIGINS | _MAVEN_ORIGINS) <= allowed_urls:
            raise ValueError("build origin allowlist is missing a required ecosystem origin")
        _validate_pip_report(report_path, allowed_urls & _PYTHON_ORIGINS)
        _validate_maven_settings(settings_path, allowed_urls & _MAVEN_ORIGINS)
        _validate_maven_manifest(manifest_path, repository_path)
    except (OSError, ValueError, json.JSONDecodeError, ElementTree.ParseError) as error:
        print(f"ERROR: invalid build provenance: {error}", file=sys.stderr)
        return 1
    print("validated build provenance: approved origins and SHA-256 artifacts")
    return 0


def _allowed_urls(path: Path) -> set[tuple[str, str]]:
    origins: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parsed = urlsplit(line)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("allowlist origins must be credential-free HTTPS URLs")
        origins.add((parsed.scheme, parsed.hostname.lower()))
    if not origins:
        raise ValueError("build origin allowlist is empty")
    return origins


def _validate_pip_report(path: Path, allowed: set[tuple[str, str]]) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    installs = document.get("install")
    if not isinstance(installs, list) or not installs:
        raise ValueError("pip report contains no installed artifacts")
    for install in installs:
        download = install.get("download_info", {})
        parsed = urlsplit(download.get("url", ""))
        if (
            (parsed.scheme, (parsed.hostname or "").lower()) not in allowed
            or parsed.username
            or parsed.password
        ):
            raise ValueError("pip report contains an unapproved artifact origin")
        hashes = download.get("archive_info", {}).get("hashes", {})
        digest = hashes.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("pip report artifact is missing a SHA-256 digest")


def _validate_maven_settings(path: Path, allowed: set[tuple[str, str]]) -> None:
    root = ElementTree.parse(path).getroot()
    urls: list[str] = []
    for element in root.iter():
        kind = element.tag.rsplit("}", 1)[-1]
        if kind not in {"mirror", "repository", "pluginRepository"}:
            continue
        children = {
            child.tag.rsplit("}", 1)[-1]: child.text or "" for child in element
        }
        if kind == "mirror" and children.get("blocked", "").lower() == "true":
            continue
        if children.get("url"):
            urls.append(children["url"])
    if not urls:
        raise ValueError("effective Maven settings contain no repository origins")
    for url in urls:
        parsed = urlsplit(url)
        if (
            (parsed.scheme, (parsed.hostname or "").lower()) not in allowed
            or parsed.username
            or parsed.password
        ):
            raise ValueError("effective Maven settings contain an unapproved origin")


def _validate_maven_manifest(path: Path, repository_argument: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or any(not _MANIFEST_LINE.fullmatch(line) for line in lines):
        raise ValueError("Maven artifact manifest is empty or malformed")
    artifact_paths = [line[66:] for line in lines]
    if artifact_paths != sorted(artifact_paths) or len(artifact_paths) != len(set(artifact_paths)):
        raise ValueError("Maven artifact manifest paths must be unique and sorted")
    if repository_argument.is_symlink():
        raise ValueError("Maven repository must not be a symlink")
    repository = repository_argument.resolve()
    if not repository.is_dir():
        raise ValueError("Maven repository is missing")
    actual_paths: list[str] = []
    for root, directories, names in os.walk(repository, followlinks=False):
        root_path = Path(root)
        if any((root_path / name).is_symlink() for name in directories + names):
            raise ValueError("Maven repository contains a symlink")
        actual_paths.extend(
            (root_path / name).relative_to(repository).as_posix()
            for name in names
            if (root_path / name).is_file()
        )
    actual_paths.sort()
    if actual_paths != artifact_paths:
        raise ValueError("Maven artifact manifest does not inventory the exact repository")
    for line, relative in zip(lines, artifact_paths):
        expected = line[:64]
        digest = hashlib.sha256()
        with (repository / relative).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f"Maven artifact digest mismatch: {relative}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
