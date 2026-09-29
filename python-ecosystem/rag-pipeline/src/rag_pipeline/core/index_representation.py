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
# Only code that produces persisted source, facts, relations, schema or seals
# belongs in this identity. Review/query orchestration, transport capacities and
# reader formatting do not change an already sealed repository generation.
_REPRESENTATION_SOURCE_PATHS = (
    "core/documents.py",
    "core/generation_manifest.py",
    "core/loader.py",
    "core/source_tree.py",
    "core/index_manager/build_support.py",
    "core/index_manager/file_indexer.py",
    "core/index_manager/generation_builder.py",
    "core/index_manager/publication.py",
    "core/index_manager/repository_enrichment.py",
    "core/structural_graph/generations.py",
    "core/structural_graph/projections.py",
    "core/structural_graph/receipts.py",
    "core/structural_graph/reconciliation.py",
    "core/structural_graph/relations.py",
    "core/structural_graph/resolution.py",
    "core/structural_graph/schema.py",
    "core/structural_graph/shared.py",
    "core/structural_graph/units.py",
    "core/structural_graph/write_state.py",
    "core/structural_graph/writer.py",
    "utils/path_identity.py",
    "utils/utils.py",
)

# Discover producer collaborators so extracting implementation into a new
# module cannot silently leave it out of the identity. The manager delegates
# construction and owns admission/query orchestration; readers consume seals.
_REPRESENTATION_SOURCE_DIRECTORIES = (
    "core/splitter", "core/index_manager", "core/structural_graph",
)
_NON_PRODUCER_SOURCE_PATHS = frozenset({
    "core/index_manager/manager.py", "core/index_manager/__init__.py",
    "core/structural_graph/reader.py", "core/structural_graph/__init__.py",
})

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
        "splitter": branch_splitter_kwargs(config),
        "excluded_patterns": sorted(
            str(value) for value in getattr(config, "excluded_patterns", ())
        ),
        "max_file_size_bytes": configured_max_file_size,
        "storage": "sqlite-structural-graph",
    }


def compute_index_representation_fingerprint(
    package_root: str | Path,
    *,
    dependency_versions: Mapping[str, str],
    runtime_settings: Optional[Mapping[str, object]] = None,
) -> str:
    root = Path(package_root).resolve(strict=True)
    source_paths = set(_REPRESENTATION_SOURCE_PATHS)
    for relative_directory in _REPRESENTATION_SOURCE_DIRECTORIES:
        source_paths.update(path.relative_to(root).as_posix()
                            for path in (root / relative_directory).rglob("*.py"))
    source_paths.difference_update(_NON_PRODUCER_SOURCE_PATHS)
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
            for relative_path in sorted(source_paths)
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
