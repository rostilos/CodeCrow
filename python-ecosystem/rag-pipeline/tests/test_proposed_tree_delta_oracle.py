"""End-to-end oracle for exact repository structural deltas."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from rag_pipeline.core.index_manager.manager import RAGIndexManager
from rag_pipeline.core.source_tree import attest_repository_source_tree
from rag_pipeline.models.config import RAGConfig


_SEMANTIC_TABLES = (
    "unit_names",
    "relations",
    "relation_plugins",
    "relation_paths",
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _canonical_rows(
    connection: sqlite3.Connection,
    table: str,
) -> tuple[str, ...]:
    columns = tuple(
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})")
    )
    rows = connection.execute(
        f"SELECT {', '.join(columns)} FROM {table}"
    ).fetchall()
    return tuple(sorted(
        json.dumps(
            {column: row[column] for column in columns},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        for row in rows
    ))


def _canonical_units(
    connection: sqlite3.Connection,
) -> tuple[str, ...]:
    """Compare all unit fields while isolating revision-only provenance.

    Unchanged units cloned from the base correctly retain their original
    ``metadata_json.commit`` value, while an independent full build stamps all
    files with the proposed revision. The commit is provenance rather than
    semantic graph content. No other metadata field is weakened here.
    """

    columns = tuple(
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(units)")
    )
    canonical = []
    for row in connection.execute(
        f"SELECT {', '.join(columns)} FROM units"
    ):
        value = {column: row[column] for column in columns}
        metadata = json.loads(value["metadata_json"])
        if "commit" in metadata:
            metadata["commit"] = "<repository-revision>"
        value["metadata_json"] = json.dumps(
            metadata,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        canonical.append(json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ))
    return tuple(sorted(canonical))


def _canonical_fts(connection: sqlite3.Connection) -> tuple[str, ...]:
    columns = (
        "unit_id",
        "path",
        "name",
        "qualified_name",
        "symbols",
        "content",
    )
    rows = connection.execute(
        f"SELECT {', '.join(columns)} FROM units_fts"
    ).fetchall()
    return tuple(sorted(
        json.dumps(
            {column: row[column] for column in columns},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        for row in rows
    ))


def test_repository_delta_matches_independent_full_plugin_index(tmp_path):
    base_repository = tmp_path / "base-repository"
    proposed_repository = tmp_path / "proposed-repository"
    _write(
        base_repository / "src" / "contracts.ts",
        "export function allow(value: string): boolean {\n"
        "  return value.length > 0;\n"
        "}\n",
    )
    _write(
        base_repository / "src" / "service.ts",
        "import { allow } from './contracts';\n\n"
        "export function submit(value: string): boolean {\n"
        "  return allow(value);\n"
        "}\n",
    )
    _write(
        base_repository / "src" / "unchanged.ts",
        "import { allow } from './contracts';\n\n"
        "export function canQueue(value: string): boolean {\n"
        "  return allow(value);\n"
        "}\n",
    )
    _write(
        base_repository / "src" / "legacy.ts",
        "export function legacy(): string {\n"
        "  return 'legacy';\n"
        "}\n",
    )
    shutil.copytree(base_repository, proposed_repository)
    _write(
        proposed_repository / "src" / "contracts.ts",
        "export function validate(value: string): boolean {\n"
        "  return value.trim().length > 2;\n"
        "}\n",
    )
    _write(
        proposed_repository / "src" / "service.ts",
        "import { validate } from './contracts';\n\n"
        "export function submit(value: string): boolean {\n"
        "  return validate(value);\n"
        "}\n",
    )
    _write(
        proposed_repository / "src" / "new-consumer.ts",
        "import { validate } from './contracts';\n\n"
        "export function canPublish(value: string): boolean {\n"
        "  return validate(value);\n"
        "}\n",
    )
    (proposed_repository / "src" / "legacy.ts").unlink()

    base_revision = "1" * 40
    proposed_revision = "2" * 40
    base_source = attest_repository_source_tree(
        base_repository,
        base_revision,
    )
    proposed_source = attest_repository_source_tree(
        proposed_repository,
        proposed_revision,
    )
    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural-index"),
    ))
    # This test owns a private filesystem store, so cross-process Redis
    # coordination would add no correctness signal.
    manager._mutation_coordinator.enabled = False

    base_target = "oracle-base"
    delta_target = "oracle-proposed-delta"
    full_target = "oracle-proposed-full"
    try:
        manager.index_repository(
            repo_path=str(base_repository),
            workspace="workspace",
            project="project",
            branch="main",
            commit=base_revision,
            source_tree_sha256=base_source.tree_sha256,
            collection_target=base_target,
        )
        base_receipt = manager.store.read_receipt(base_target)
        assert base_receipt is not None
        assert base_receipt["plugin_ids"] == ["typescript"]
        assert base_receipt["snapshot_count"] == 1

        manager.index_repository_delta(
            repo_path=str(proposed_repository),
            workspace="workspace",
            project="project",
            branch="main",
            base_revision=base_revision,
            commit=proposed_revision,
            changed_paths=(
                "src/contracts.ts",
                "src/new-consumer.ts",
                "src/service.ts",
            ),
            deleted_paths=("src/legacy.ts",),
            source_tree_sha256=proposed_source.tree_sha256,
            collection_target=delta_target,
            base_collection_target=base_target,
            base_generation_manifest_sha256=(
                base_receipt["generation_manifest_sha256"]
            ),
        )
        manager.index_repository(
            repo_path=str(proposed_repository),
            workspace="workspace",
            project="project",
            branch="main",
            commit=proposed_revision,
            source_tree_sha256=proposed_source.tree_sha256,
            collection_target=full_target,
        )
        delta_receipt = manager.store.read_receipt(delta_target)
        full_receipt = manager.store.read_receipt(full_target)
        assert delta_receipt is not None
        assert full_receipt is not None
        assert delta_receipt["plugin_fingerprint"] == (
            full_receipt["plugin_fingerprint"]
        )
        assert delta_receipt["plugin_fingerprint"] != (
            base_receipt["plugin_fingerprint"]
        )

        delta_path = manager.store.paths_for_target(delta_target).database
        full_path = manager.store.paths_for_target(full_target).database
        with (
            manager.store.connect(delta_path, read_only=True) as delta,
            manager.store.connect(full_path, read_only=True) as full,
        ):
            assert _canonical_units(delta) == _canonical_units(full)
            for table in _SEMANTIC_TABLES:
                assert _canonical_rows(delta, table) == _canonical_rows(
                    full,
                    table,
                )
            assert _canonical_fts(delta) == _canonical_fts(full)
            assert _canonical_rows(
                delta,
                "repository_snapshots",
            ) == _canonical_rows(full, "repository_snapshots")
    finally:
        manager.close()


def test_repository_delta_finalizer_failure_drops_stale_base_plugin_output(
    tmp_path,
    monkeypatch,
):
    base_repository = tmp_path / "base-repository"
    proposed_repository = tmp_path / "proposed-repository"
    _write(
        base_repository / "src" / "Button.jsx",
        "export default function Button({label}) {\n"
        "  return <button>{label}</button>;\n"
        "}\n",
    )
    _write(
        base_repository / "src" / "App.jsx",
        "import Button from './Button';\n"
        "export default function App() {\n"
        "  return <Button label='ok' />;\n"
        "}\n",
    )
    shutil.copytree(base_repository, proposed_repository)
    _write(
        proposed_repository / "src" / "Button.jsx",
        "export const buttonWasRemoved = true;\n",
    )
    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural-index"),
    ))
    manager._mutation_coordinator.enabled = False
    try:
        manager.index_repository(
            repo_path=str(base_repository),
            workspace="workspace",
            project="project",
            branch="main",
            commit="base-revision",
            collection_target="finalizer-base",
        )
        base_receipt = manager.store.read_receipt("finalizer-base")
        assert base_receipt is not None
        with manager.open_reader(
            workspace="workspace",
            project="project",
            branch="main",
            revision="base-revision",
            generation_manifest_sha256=base_receipt[
                "generation_manifest_sha256"
            ],
            collection_target="finalizer-base",
        ) as base_reader:
            base_kinds = {
                relation["kind"]
                for relation in base_reader.relations_for_paths(
                    ["src/App.jsx"],
                    max_relations=200,
                )["relations"]
            }
        assert "javascript-component-resolution" in base_kinds

        class FailingRepositoryAnalysis:
            active = True

            @staticmethod
            def ingest(_artifacts):
                return None

            @staticmethod
            def finish(*, deadline=None):
                raise RuntimeError("forced repository finalizer failure")

        monkeypatch.setattr(
            manager.plugin_runtime,
            "start_repository_analysis",
            lambda *args, **kwargs: FailingRepositoryAnalysis(),
        )
        manager.index_repository_delta(
            repo_path=str(proposed_repository),
            workspace="workspace",
            project="project",
            branch="main",
            base_revision="base-revision",
            commit="source-revision",
            changed_paths=("src/Button.jsx",),
            deleted_paths=(),
            source_tree_sha256=None,
            collection_target="finalizer-delta",
            base_collection_target="finalizer-base",
            base_generation_manifest_sha256=base_receipt[
                "generation_manifest_sha256"
            ],
        )
        delta_receipt = manager.store.read_receipt("finalizer-delta")
        assert delta_receipt is not None
        with manager.open_reader(
            workspace="workspace",
            project="project",
            branch="main",
            revision="source-revision",
            generation_manifest_sha256=delta_receipt[
                "generation_manifest_sha256"
            ],
            collection_target="finalizer-delta",
        ) as delta_reader:
            delta_kinds = {
                relation["kind"]
                for relation in delta_reader.relations_for_paths(
                    ["src/App.jsx"],
                    max_relations=200,
                )["relations"]
            }
    finally:
        manager.close()

    assert "javascript-component-resolution" not in delta_kinds
    assert "javascript-jsx-prop-contract" not in delta_kinds
    assert delta_receipt["snapshot_count"] == 0
