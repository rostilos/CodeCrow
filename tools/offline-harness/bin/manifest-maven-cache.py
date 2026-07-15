#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path


def main(arguments: list[str]) -> int:
    if len(arguments) != 2:
        print(
            "usage: manifest-maven-cache.py <maven-repository> <output-manifest>",
            file=sys.stderr,
        )
        return 64
    repository_argument, output_argument = map(Path, arguments)
    if repository_argument.is_symlink():
        print("ERROR: Maven repository must not be a symlink", file=sys.stderr)
        return 1
    repository = repository_argument.resolve()
    if not repository.is_dir():
        print("ERROR: Maven repository is missing", file=sys.stderr)
        return 1
    if output_argument.is_symlink():
        print("ERROR: Maven manifest output must not be a symlink", file=sys.stderr)
        return 1

    files: list[Path] = []
    for root, directories, names in os.walk(repository, followlinks=False):
        root_path = Path(root)
        if any((root_path / name).is_symlink() for name in directories + names):
            print("ERROR: Maven repository contains a symlink", file=sys.stderr)
            return 1
        files.extend(root_path / name for name in names if (root_path / name).is_file())
    files.sort(key=lambda path: path.relative_to(repository).as_posix())
    if not files:
        print("ERROR: Maven repository contains no files", file=sys.stderr)
        return 1

    output = output_argument.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            for path in files:
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                relative = path.relative_to(repository).as_posix()
                stream.write(f"{digest.hexdigest()}  {relative}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, output)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
