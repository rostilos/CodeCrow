from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rag_pipeline.core.exact_index import ExactIndexPreconditionError
from rag_pipeline.core.index_manager.manager import (
    RAGIndexManager,
    RepositoryIndexCancelled,
    _unsafe_delta_plugin_selection_changes,
)
from rag_pipeline.core.source_tree import (
    RepositorySourceTreeError,
    attest_repository_source_tree,
)
from rag_pipeline.core.structural_store import StructuralGraphWriter
from rag_pipeline.models.config import RAGConfig


def _receipt():
    return {
        "workspace": "ws",
        "project": "project",
        "branch": "main",
        "repository_revision": "commit",
        "generation_manifest_sha256": "a" * 64,
        "source_tree_sha256": "b" * 64,
        "collection_target": "generation-target",
        "unit_count": 7,
    }


def test_revision_preflight_reads_the_requested_structural_generation():
    manager = object.__new__(RAGIndexManager)
    manager.store = MagicMock()
    manager.store.read_receipt.return_value = _receipt()
    manager.index_representation_fingerprint = "sha256:current"

    result = manager.get_revision_preflight(
        "ws",
        "project",
        "main",
        "commit",
        collection_target="generation-target",
    )

    manager.store.read_receipt.assert_called_once_with("generation-target")
    manager.store.open_bound.assert_called_once_with(
        target="generation-target",
        workspace="ws",
        project="project",
        branch="main",
        revision="commit",
        manifest_sha256="a" * 64,
    )
    assert result["generation_manifest_sha256"] == "a" * 64
    assert result["current_index_representation_fingerprint"] == (
        "sha256:current"
    )


def test_revision_preflight_does_not_cross_generation_binding():
    manager = object.__new__(RAGIndexManager)
    manager.store = MagicMock()
    manager.store.read_receipt.return_value = _receipt()
    manager.index_representation_fingerprint = "sha256:current"

    assert manager.get_revision_preflight(
        "other-workspace",
        "project",
        "main",
        "commit",
        collection_target="generation-target",
    ) is None


def test_revision_discovery_verifies_each_tenant_bound_receipt():
    manager = object.__new__(RAGIndexManager)
    manager.store = MagicMock()
    manager.store.repository_generation_receipts.return_value = [
        _receipt(),
        {**_receipt(), "collection_target": "invalid-target"},
    ]
    manager.get_revision_preflight = MagicMock(
        side_effect=[
            {**_receipt(), "document_count": 4},
            ExactIndexPreconditionError("invalid seal"),
        ]
    )

    result = manager.discover_revision_preflights(
        "ws",
        "project",
        "main",
    )

    assert [item["collection_target"] for item in result] == [
        "generation-target"
    ]
    manager.store.repository_generation_receipts.assert_called_once_with(
        workspace="ws",
        project="project",
        branch="main",
        revision=None,
    )


def test_close_releases_the_mutation_coordinator():
    manager = object.__new__(RAGIndexManager)
    manager._mutation_coordinator = MagicMock()

    manager.close()

    manager._mutation_coordinator.close.assert_called_once_with()


def test_representation_identity_includes_catalog_and_implementation():
    manager = object.__new__(RAGIndexManager)
    manager.index_representation_fingerprint = "sha256:" + "1" * 64
    registry = SimpleNamespace(
        ordered_ids=("java", "spring"),
        fingerprint="sha256:" + "2" * 64,
    )
    catalog = SimpleNamespace(
        registry=registry,
        implementation_fingerprint=lambda plugin_ids: "sha256:" + "3" * 64,
    )
    manager.plugin_catalog = catalog

    identity = manager.current_representation_identity()

    assert identity["plugin_ids"] == ["java", "spring"]
    assert identity["plugin_descriptor_fingerprint"] == "sha256:" + "2" * 64
    assert identity["plugin_implementation_fingerprint"] == "sha256:" + "3" * 64
    assert identity["representation_identity"].startswith("sha256:")
    assert len(identity["representation_identity"]) == 71


def test_only_syntax_language_selection_changes_are_delta_safe():
    descriptors = {
        "bash": SimpleNamespace(kind="language", capabilities=("syntax",)),
        "magento": SimpleNamespace(
            kind="framework",
            capabilities=("graph", "syntax"),
        ),
    }
    registry = SimpleNamespace(descriptor=lambda plugin_id: descriptors[plugin_id])

    assert _unsafe_delta_plugin_selection_changes(
        registry,
        ("php", "magento", "bash"),
        ("php", "magento"),
    ) == ()
    assert _unsafe_delta_plugin_selection_changes(
        registry,
        ("php", "magento"),
        ("php",),
    ) == ("magento",)


@pytest.mark.parametrize("change", ("mutate", "delete"))
def test_generation_is_not_published_when_source_changes_after_scan(
    tmp_path,
    change,
):
    repository = tmp_path / "repository"
    source_file = repository / "src" / "example.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("def stable():\n    return True\n", encoding="utf-8")
    source_tree = attest_repository_source_tree(repository, "a" * 40)
    collection_target = f"source-change-{change}"
    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural-index"),
    ))
    # Keep this regression focused on the host's exact source lifecycle.
    manager.plugin_catalog = None
    manager.plugin_runtime = None
    manager.plugin_selector = None
    original_scan = manager.loader.iter_repository_files

    def scan_then_change(*args, **kwargs):
        yield from original_scan(*args, **kwargs)
        if change == "mutate":
            source_file.write_text(
                "def changed():\n    return False\n",
                encoding="utf-8",
            )
        else:
            source_file.unlink()

    manager.loader.iter_repository_files = scan_then_change
    try:
        with pytest.raises(
            RepositorySourceTreeError,
            match="acquisition attestation",
        ):
            manager._build_generation(
                repo_path=repository,
                workspace="workspace",
                project="project",
                branch="main",
                commit="a" * 40,
                include_patterns=None,
                exclude_patterns=None,
                source_tree=source_tree,
                collection_target=collection_target,
                progress_callback=None,
                project_type=None,
                source_root=None,
                activation_guard=lambda: None,
            )

        assert manager.store.read_receipt(collection_target) is None
        assert not manager.store.paths_for_target(
            collection_target
        ).directory.exists()
    finally:
        manager.close()


def test_generation_cancellation_at_batch_boundary_removes_pending_state(
    tmp_path,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    source_tree = SimpleNamespace(
        file_sha256_by_path={},
        tree_sha256="sha256:" + "1" * 64,
    )
    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural-index"),
    ))
    manager.plugin_catalog = None
    manager.plugin_runtime = None
    manager.plugin_selector = None
    manager.loader.iter_repository_files = MagicMock(
        return_value=iter(Path(f"src/file_{index}.py") for index in range(51))
    )
    manager.loader.load_file_batch = MagicMock(return_value=[])
    cancellation_event = Event()
    target = "cancelled-generation"

    def cancel_after_first_batch(event):
        if event["stage"] == "indexing":
            cancellation_event.set()

    try:
        with pytest.raises(
            RepositoryIndexCancelled,
            match="indexing was cancelled",
        ):
            manager._build_generation(
                repo_path=repository,
                workspace="workspace",
                project="project",
                branch="main",
                commit="a" * 40,
                include_patterns=None,
                exclude_patterns=None,
                source_tree=source_tree,
                collection_target=target,
                progress_callback=cancel_after_first_batch,
                project_type=None,
                source_root=None,
                activation_guard=lambda: None,
                cancellation_event=cancellation_event,
            )

        assert manager.loader.load_file_batch.call_count == 1
        assert manager.store.read_receipt(target) is None
        assert not manager.store.paths_for_target(target).directory.exists()
        assert tuple(manager.store.pending_root.iterdir()) == ()
    finally:
        manager.close()


def test_delta_graph_removal_cancellation_removes_pending_state(
    tmp_path,
    monkeypatch,
):
    repository = tmp_path / "repository"
    source_file = repository / "src" / "example.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("value = 1\n", encoding="utf-8")
    base_revision = "a" * 40
    proposed_revision = "b" * 40
    base_source = attest_repository_source_tree(repository, base_revision)
    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural-index"),
    ))
    manager.plugin_catalog = None
    manager.plugin_runtime = None
    manager.plugin_selector = None
    manager._mutation_coordinator.enabled = False
    base_target = "delta-cancellation-base"
    target = "delta-cancellation-target"
    cancellation_event = Event()

    try:
        manager.index_repository(
            repo_path=str(repository),
            workspace="workspace",
            project="project",
            branch="main",
            commit=base_revision,
            source_tree_sha256=base_source.tree_sha256,
            collection_target=base_target,
        )
        base_receipt = manager.store.read_receipt(base_target)
        assert base_receipt is not None

        source_file.write_text("value = 2\n", encoding="utf-8")
        proposed_source = attest_repository_source_tree(
            repository,
            proposed_revision,
        )

        def cancel_graph_removal(
            _writer,
            _paths,
            *,
            cancellation_check=None,
        ):
            assert cancellation_check is not None
            cancellation_event.set()
            cancellation_check()
            raise AssertionError("cancellation check did not stop delta removal")

        monkeypatch.setattr(
            StructuralGraphWriter,
            "remove_paths",
            cancel_graph_removal,
        )

        with pytest.raises(
            RepositoryIndexCancelled,
            match="indexing was cancelled",
        ):
            manager.index_repository_delta(
                repo_path=str(repository),
                workspace="workspace",
                project="project",
                branch="main",
                base_revision=base_revision,
                commit=proposed_revision,
                changed_paths=("src/example.py",),
                deleted_paths=(),
                source_tree_sha256=proposed_source.tree_sha256,
                collection_target=target,
                base_collection_target=base_target,
                base_generation_manifest_sha256=(
                    base_receipt["generation_manifest_sha256"]
                ),
                cancellation_event=cancellation_event,
            )

        assert manager.store.read_receipt(target) is None
        assert not manager.store.paths_for_target(target).directory.exists()
        assert tuple(manager.store.pending_root.iterdir()) == ()
    finally:
        manager.close()


def test_generation_indexes_explicit_file_to_ast_hierarchy(tmp_path):
    repository = tmp_path / "repository"
    source_file = repository / "src" / "example.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(
        "class Service:\n    def run(self):\n        return True\n",
        encoding="utf-8",
    )
    source_tree = attest_repository_source_tree(repository, "a" * 40)
    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural-index"),
    ))
    manager.plugin_catalog = None
    manager.plugin_runtime = None
    manager.plugin_selector = None
    target = "file-hierarchy"
    try:
        manager._build_generation(
            repo_path=repository,
            workspace="workspace",
            project="project",
            branch="main",
            commit="a" * 40,
            include_patterns=None,
            exclude_patterns=None,
            source_tree=source_tree,
            collection_target=target,
            progress_callback=None,
            project_type=None,
            source_root=None,
            activation_guard=lambda: None,
        )
        receipt = manager.store.read_receipt(target)
        assert receipt is not None
        with manager.open_reader(
            workspace="workspace",
            project="project",
            branch="main",
            revision="a" * 40,
            generation_manifest_sha256=receipt["generation_manifest_sha256"],
            collection_target=target,
        ) as reader:
            file_row = reader.connection.execute(
                "SELECT unit_id FROM units WHERE record_type = 'structural_file' "
                "AND path = 'src/example.py'"
            ).fetchone()
            assert file_row is not None
            children = reader.connection.execute(
                "SELECT target_unit_id FROM relations "
                "WHERE source_unit_id = ? AND kind = 'CONTAINS' "
                "AND origin = 'structural-index'",
                (file_row["unit_id"],),
            ).fetchall()
            assert children
            assert all(row["target_unit_id"] for row in children)
            relation_count = reader.connection.execute(
                "SELECT count(*) FROM relations"
            ).fetchone()[0]
            assert relation_count > 0
            assert reader.connection.execute(
                "SELECT count(*) FROM relation_scopes WHERE scope = 'file'"
            ).fetchone()[0] == relation_count
            assert reader.connection.execute(
                "SELECT count(*) FROM relations AS relation "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM relation_scopes AS ownership "
                "WHERE ownership.relation_id = relation.relation_id "
                "AND ownership.scope = 'file')"
            ).fetchone()[0] == 0
    finally:
        manager.close()


def test_revision_preflight_rejects_a_database_seal_mismatch(tmp_path):
    repository = tmp_path / "repository"
    source_file = repository / "src" / "example.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("def stable():\n    return True\n", encoding="utf-8")
    source_tree = attest_repository_source_tree(repository, "a" * 40)
    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural-index"),
    ))
    manager.plugin_catalog = None
    manager.plugin_runtime = None
    manager.plugin_selector = None
    target = "preflight-seal-mismatch"
    try:
        manager._build_generation(
            repo_path=repository,
            workspace="workspace",
            project="project",
            branch="main",
            commit="a" * 40,
            include_patterns=None,
            exclude_patterns=None,
            source_tree=source_tree,
            collection_target=target,
            progress_callback=None,
            project_type=None,
            source_root=None,
            activation_guard=lambda: None,
        )
        receipt = manager.store.read_receipt(target)
        assert receipt is not None
        assert manager.get_revision_preflight(
            "workspace",
            "project",
            "main",
            "a" * 40,
            collection_target=target,
        ) is not None

        connection = manager.store.connect(
            manager.store.paths_for_target(target).database
        )
        connection.execute(
            "UPDATE generation SET receipt_json = ? WHERE singleton = 1",
            ('{"tampered":true}',),
        )
        connection.commit()
        connection.close()

        with pytest.raises(
            ExactIndexPreconditionError,
            match="receipt does not match its database seal",
        ):
            manager.get_revision_preflight(
                "workspace",
                "project",
                "main",
                "a" * 40,
                collection_target=target,
            )
    finally:
        manager.close()
