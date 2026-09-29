"""Content-derived identity for the SQLite structural representation."""

from __future__ import annotations

import hashlib
import json
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Mapping, Optional

from ..models.config import DEFAULT_MAX_FILE_SIZE_BYTES


INDEX_REPRESENTATION_PAYLOAD_KEY = "index_representation_fingerprint"
BRANCH_SPLITTER_PARSER_THRESHOLD = 10

_REPRESENTATION_SOURCE_PATHS = (
    "core/documents.py",
    "core/generation_manifest.py",
    "core/index_representation.py",
    "core/loader.py",
    "core/review_context.py",
    "core/source_tree.py",
    "core/structural_store.py",
    "core/splitter/languages.py",
    "core/splitter/metadata.py",
    "core/splitter/query_runner.py",
    "core/splitter/splitter.py",
    "core/splitter/tree_parser.py",
    "models/config.py",
    "utils/path_identity.py",
    "utils/utils.py",
)

_REPRESENTATION_DEPENDENCIES = (
    "langchain-text-splitters",
    "pydantic",
    "tree-sitter",
    "tree-sitter-c",
    "tree-sitter-c-sharp",
    "tree-sitter-cpp",
    "tree-sitter-go",
    "tree-sitter-java",
    "tree-sitter-javascript",
    "tree-sitter-php",
    "tree-sitter-python",
    "tree-sitter-ruby",
    "tree-sitter-rust",
    "tree-sitter-typescript",
)


def _installed_dependency_versions() -> dict[str, str]:
    versions = {}
    for distribution in _REPRESENTATION_DEPENDENCIES:
        try:
            versions[distribution] = importlib_metadata.version(distribution)
        except importlib_metadata.PackageNotFoundError:
            versions[distribution] = "absent"
    return versions


def branch_splitter_kwargs(config) -> dict[str, object]:
    chunk_size = int(getattr(config, "chunk_size", 0))
    return {
        "max_chunk_size": chunk_size,
        "min_chunk_size": min(200, chunk_size // 4),
        "chunk_overlap": int(getattr(config, "chunk_overlap", 0)),
        "parser_threshold": BRANCH_SPLITTER_PARSER_THRESHOLD,
    }


def _runtime_representation_settings(config) -> dict[str, object]:
    if config is None:
        return {"configuration": "unspecified"}
    configured_max_file_size = getattr(
        config,
        "max_file_size_bytes",
        DEFAULT_MAX_FILE_SIZE_BYTES,
    )
    if (
        isinstance(configured_max_file_size, bool)
        or not isinstance(configured_max_file_size, int)
        or configured_max_file_size < 1
    ):
        configured_max_file_size = DEFAULT_MAX_FILE_SIZE_BYTES
    return {
        "chunk_overlap": int(getattr(config, "chunk_overlap", 0)),
        "chunk_size": int(getattr(config, "chunk_size", 0)),
        "excluded_patterns": sorted(
            str(value) for value in getattr(config, "excluded_patterns", ())
        ),
        "max_file_size_bytes": configured_max_file_size,
        "splitter": branch_splitter_kwargs(config),
        "storage": "sqlite-structural-graph",
    }


def compute_index_representation_fingerprint(
    package_root: str | Path,
    *,
    dependency_versions: Mapping[str, str],
    runtime_settings: Optional[Mapping[str, object]] = None,
) -> str:
    root = Path(package_root).resolve(strict=True)
    projection = {
        "dependencies": {
            name: str(dependency_versions.get(name, "absent"))
            for name in _REPRESENTATION_DEPENDENCIES
        },
        "runtime_settings": dict(runtime_settings or {}),
        "sources": [
            {
                "path": relative_path,
                "sha256": hashlib.sha256(
                    (root / relative_path).read_bytes()
                ).hexdigest(),
            }
            for relative_path in _REPRESENTATION_SOURCE_PATHS
        ],
    }
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def index_representation_fingerprint(config=None) -> str:
    package_root = Path(__file__).resolve().parents[1]
    return compute_index_representation_fingerprint(
        package_root,
        dependency_versions=_installed_dependency_versions(),
        runtime_settings=_runtime_representation_settings(config),
    )
