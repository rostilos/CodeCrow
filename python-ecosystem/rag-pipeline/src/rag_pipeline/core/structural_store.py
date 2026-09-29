"""SQLite-backed immutable structural repository generations.

The node/edge storage and bounded directional-query model are adapted from
``code-review-graph`` 2.3.8 (MIT, Copyright (c) 2026 Tirth Kanani). CodeCrow
keeps its own schema because its neutral plugin facts, immutable target-head
generation receipts, and tenant binding are not represented by the upstream
local-development database.

This module intentionally contains no embeddings and no vector-store adapter.
Source units are produced by CodeCrow's existing Tree-sitter/plugin pipeline;
SQLite stores their exact structure, logical relations, and source bodies.
"""

# Preserve established imports while implementations own focused responsibilities.
import sqlite3

from .structural_graph.generations import GenerationPaths, StructuralGenerationStore
from .structural_graph.reader import (
    StructuralGraphReader,
)
from .structural_graph.projections import relation_to_manifest, unit_to_manifest
from .structural_graph.receipts import build_receipt, write_receipt
from .structural_graph.shared import (
    STRUCTURAL_STORE_SCHEMA,
    STRUCTURAL_STORE_SCHEMA_REVISION,
)
from .structural_graph.writer import StructuralGraphWriter

__all__ = [
    "GenerationPaths",
    "STRUCTURAL_STORE_SCHEMA",
    "STRUCTURAL_STORE_SCHEMA_REVISION",
    "StructuralGenerationStore",
    "StructuralGraphReader",
    "StructuralGraphWriter",
    "build_receipt",
    "relation_to_manifest",
    "unit_to_manifest",
    "write_receipt",
]
