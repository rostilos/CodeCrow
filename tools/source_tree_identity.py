"""Dependency-free access to the RAG pipeline's canonical source-tree identity."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_TREE_MODULE = (
    _REPOSITORY_ROOT
    / "python-ecosystem"
    / "rag-pipeline"
    / "src"
    / "rag_pipeline"
    / "core"
    / "source_tree.py"
)
_MODULE_NAME = "_codecrow_canonical_source_tree"


def _load_source_tree_module():
    existing = sys.modules.get(_MODULE_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        _MODULE_NAME,
        _SOURCE_TREE_MODULE,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"cannot load canonical source-tree implementation: "
            f"{_SOURCE_TREE_MODULE}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(_MODULE_NAME, None)
        raise
    return module


compute_repository_source_tree_sha256 = (
    _load_source_tree_module().compute_repository_source_tree_sha256
)


__all__ = ["compute_repository_source_tree_sha256"]
