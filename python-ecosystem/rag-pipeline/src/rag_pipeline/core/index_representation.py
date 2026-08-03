"""Content-derived identity for the neutral persisted RAG representation."""

from __future__ import annotations

import hashlib
import json
import logging
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Mapping, Optional

from qdrant_client.models import FieldCondition, Filter, MatchValue


logger = logging.getLogger(__name__)

INDEX_REPRESENTATION_PAYLOAD_KEY = "index_representation_fingerprint"

# These inputs can change persistent target-branch point text, metadata, or
# vectors. PR-only request/overlay code is intentionally excluded so a PR
# orchestration fix cannot force every repository embedding to be rebuilt.
_REPRESENTATION_SOURCE_PATHS = (
    "core/embedding_factory.py",
    "core/ollama_embedding.py",
    "core/openrouter_embedding.py",
    "core/index_manager/collection_manager.py",
    "core/index_manager/indexer.py",
    "core/index_manager/point_operations.py",
    "core/generation_manifest.py",
    "core/index_representation.py",
    "core/loader.py",
    "core/repository_overlay.py",
    "core/splitter/languages.py",
    "core/splitter/metadata.py",
    "core/splitter/query_runner.py",
    "core/splitter/splitter.py",
    "core/splitter/tree_parser.py",
    "models/config.py",
    "utils/path_identity.py",
    "utils/utils.py",
)

BRANCH_SPLITTER_PARSER_THRESHOLD = 10
BRANCH_SPLITTER_ENRICH_EMBEDDING_TEXT = True

_REPRESENTATION_DEPENDENCIES = (
    "httpx",
    "langchain-text-splitters",
    "llama-index-core",
    "llama-index-vector-stores-qdrant",
    "pydantic",
    "qdrant-client",
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


def compute_index_representation_fingerprint(
    package_root: str | Path,
    *,
    dependency_versions: Mapping[str, str],
    runtime_settings: Optional[Mapping[str, object]] = None,
) -> str:
    """Hash the exact neutral code/dependencies that produce stored points."""
    root = Path(package_root).resolve(strict=True)
    sources = []
    for relative_path in _REPRESENTATION_SOURCE_PATHS:
        path = root / relative_path
        sources.append({
            "path": relative_path,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    projection = {
        "dependencies": {
            name: str(dependency_versions.get(name, "absent"))
            for name in _REPRESENTATION_DEPENDENCIES
        },
        "runtime_settings": dict(runtime_settings or {}),
        "sources": sources,
    }
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def branch_splitter_kwargs(config) -> dict[str, object]:
    """Return the exact splitter construction that produces branch points.

    Keeping these values in a hashed representation module lets the generic
    manager remain orchestration wiring. Changes to PR-only manager behavior
    then invalidate only the bounded PR overlay rather than every repository
    embedding.
    """
    chunk_size = int(getattr(config, "chunk_size", 0))
    return {
        "max_chunk_size": chunk_size,
        "min_chunk_size": min(200, chunk_size // 4),
        "chunk_overlap": int(getattr(config, "chunk_overlap", 0)),
        "parser_threshold": BRANCH_SPLITTER_PARSER_THRESHOLD,
        "enrich_embedding_text": BRANCH_SPLITTER_ENRICH_EMBEDDING_TEXT,
    }


def _runtime_representation_settings(config) -> dict[str, object]:
    if config is None:
        return {"configuration": "unspecified"}
    provider = str(getattr(config, "embedding_provider", ""))
    model = (
        getattr(config, "ollama_model", "")
        if provider == "ollama"
        else getattr(config, "openrouter_model", "")
    )
    return {
        "chunk_overlap": int(getattr(config, "chunk_overlap", 0)),
        "chunk_size": int(getattr(config, "chunk_size", 0)),
        "embedding_dimension": int(getattr(config, "embedding_dim", 0)),
        "embedding_model": str(model),
        "embedding_provider": provider,
        "embedding_supports_instructions": bool(
            getattr(config, "embedding_supports_instructions", False)
        ),
        "excluded_patterns": sorted(
            str(value) for value in getattr(config, "excluded_patterns", ())
        ),
        "max_file_size_bytes": int(
            getattr(config, "max_file_size_bytes", 0)
        ),
        "text_chunk_overlap": int(getattr(config, "text_chunk_overlap", 0)),
        "text_chunk_size": int(getattr(config, "text_chunk_size", 0)),
        "splitter": branch_splitter_kwargs(config),
    }


def index_representation_fingerprint(config=None) -> str:
    """Return the effective build/config identity (not a release version)."""
    package_root = Path(__file__).resolve().parents[1]
    return compute_index_representation_fingerprint(
        package_root,
        dependency_versions=_installed_dependency_versions(),
        runtime_settings=_runtime_representation_settings(config),
    )


def read_branch_index_representation(
    client,
    collection_name: str,
    branch: str,
) -> tuple[bool, Optional[str]]:
    """Read one repository point's representation identity for a branch.

    One non-PR repository point is sufficient to observe branch provenance.
    The boolean distinguishes an absent branch from a legacy point with no
    identity.
    """
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="branch",
                        match=MatchValue(value=branch),
                    ),
                ],
            ),
            limit=64,
            offset=offset,
            with_payload=[INDEX_REPRESENTATION_PAYLOAD_KEY, "pr"],
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            if payload.get("pr") is True:
                continue
            value = payload.get(INDEX_REPRESENTATION_PAYLOAD_KEY)
            return True, value if isinstance(value, str) and value else None
        if offset is None:
            return False, None


def observe_branch_representation(
    client,
    collection_name: str,
    branch: str,
    *,
    expected_fingerprint: Optional[str] = None,
) -> bool:
    """Return whether a branch exists without gating it on source-code hashes.

    The fingerprint remains stored as build provenance for diagnostics.  It is
    deliberately not a compatibility boundary: operational changes to the RAG
    host must not force customers to rebuild otherwise usable embeddings.
    Structural incompatibilities are enforced by Qdrant and by the persisted
    snapshot integrity at the points where stored state is used.
    """
    exists, stored = read_branch_index_representation(
        client,
        collection_name,
        branch,
    )
    if not exists:
        return False
    expected = expected_fingerprint or index_representation_fingerprint()
    if stored != expected:
        logger.info(
            "Branch '%s' has a different or legacy neutral RAG build "
            "fingerprint; accepting the existing index without reindexing",
            branch,
        )
    return True
