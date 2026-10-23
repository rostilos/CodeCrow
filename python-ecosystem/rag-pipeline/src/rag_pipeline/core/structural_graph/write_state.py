"""State shared by collaborators for one pending graph transaction."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class GraphWriteState:
    connection: sqlite3.Connection
    track_mutations: bool = False
    defer_file_relation_ownership: bool = False
    buffer_file_relations: bool = False
    unit_count: int = 0
    relation_count: int = 0
    file_unit_ids: dict[str, str] = field(default_factory=dict)
    touched_relation_ids: set[str] = field(default_factory=set)
    touched_names: set[str] = field(default_factory=set)
    explicit_source_relation_ids: set[str] = field(default_factory=set)
    explicit_target_relation_ids: set[str] = field(default_factory=set)
    capturing_repository_outputs: bool = False
    repository_output_unit_ids: set[str] = field(default_factory=set)
    repository_output_relation_plugins: dict[str, set[str]] = field(default_factory=dict)
    repository_output_snapshots: set[tuple[str, str]] = field(default_factory=set)
    fts_available: bool = field(init=False)
    external_fts: bool = field(init=False)

    def __post_init__(self) -> None:
        self.fts_available = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'units_fts'"
        ).fetchone() is not None
        # Existing sealed databases keep their original self-contained FTS.
        # Detect physical capability without imposing a new schema cutoff.
        self.external_fts = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'view' "
            "AND name = 'unit_search_content'"
        ).fetchone() is not None
