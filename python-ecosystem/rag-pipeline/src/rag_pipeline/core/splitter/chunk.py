"""Semantic source records and lossless metadata normalization."""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
from typing import List, Optional
from .metadata import ContentType

def _stable_unique_strings(values: List[str]) -> List[str]:
    """Deduplicate metadata values while retaining AST/query source order."""
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def generate_deterministic_id(path: str, content: str, chunk_index: int = 0) -> str:
    """
    Generate a deterministic ID for a chunk based on file path and content.

    This ensures the same code chunk always gets the same ID, preventing
    duplicate structural units during re-indexing.
    """
    hash_input = f"{path}:{chunk_index}:{content}"
    return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()[:32]


def compute_file_hash(content: str) -> str:
    """Compute hash of file content for change detection."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


@dataclass
class ASTChunk:
    """Represents a chunk of code from AST parsing with rich metadata."""
    content: str
    content_type: ContentType
    language: str
    path: str

    # Identity
    symbol_names: List[str] = field(default_factory=list)
    node_type: Optional[str] = None
    namespace: Optional[str] = None

    # Location
    start_line: int = 0
    end_line: int = 0

    # Hierarchy & Context
    parent_context: List[str] = field(default_factory=list)  # Breadcrumb path

    # Documentation
    docstring: Optional[str] = None
    signature: Optional[str] = None

    # Type relationships
    extends: List[str] = field(default_factory=list)
    implements: List[str] = field(default_factory=list)

    # Dependencies
    imports: List[str] = field(default_factory=list)

    # --- RICH AST FIELDS (extracted from tree-sitter) ---

    # Methods/functions within this chunk (for classes)
    methods: List[str] = field(default_factory=list)

    # Properties/fields within this chunk (for classes)
    properties: List[str] = field(default_factory=list)

    # Parameters (for functions/methods)
    parameters: List[str] = field(default_factory=list)

    # Return type (for functions/methods)
    return_type: Optional[str] = None

    # Decorators/annotations
    decorators: List[str] = field(default_factory=list)

    # Modifiers (public, private, static, async, abstract, etc.)
    modifiers: List[str] = field(default_factory=list)

    # Called functions/methods (dependencies)
    calls: List[str] = field(default_factory=list)

    # Referenced types (type annotations, generics)
    referenced_types: List[str] = field(default_factory=list)

    # Variables declared in this chunk
    variables: List[str] = field(default_factory=list)

    # Constants defined
    constants: List[str] = field(default_factory=list)

    # Generic type parameters (e.g., <T, U>)
    type_parameters: List[str] = field(default_factory=list)

    # Explicit signal that structural metadata was admitted only partially.
    metadata_partial_reasons: List[str] = field(default_factory=list)



INVENTORY_FIELDS = (
    "symbol_names", "extends", "implements", "imports", "methods", "properties",
    "parameters", "decorators", "calls", "referenced_types", "variables", "constants",
    "type_parameters",
)


def normalize_chunk(chunk: ASTChunk) -> None:
    """Remove duplicate structural facts while preserving every distinct value."""
    for name in INVENTORY_FIELDS:
        setattr(chunk, name, _stable_unique_strings(getattr(chunk, name)))


def normalize_metadata(metadata: dict) -> None:
    for name in INVENTORY_FIELDS:
        values = metadata.get(name)
        if isinstance(values, list):
            metadata[name] = _stable_unique_strings(values)
