"""Complete plugin facts survive normal and bulk structural ingestion."""
from pathlib import Path

import pytest

from codecrow_plugins import (
    FileArtifact, PluginCatalog, PluginRuntime, ProjectSelector, RepositoryFacts,
)
from rag_pipeline.core.documents import Document
from rag_pipeline.core.index_manager.file_indexer import FileIndexer
from rag_pipeline.core.splitter import ASTCodeSplitter
from rag_pipeline.core.structural_store import StructuralGenerationStore, StructuralGraphWriter


@pytest.mark.parametrize("buffered", [False, True])
def test_long_javascript_callee_survives_plugin_composition_and_storage(tmp_path, caplog, buffered):
    # Bundled libraries can invoke an anonymous function whose complete AST
    # callee text exceeds the former 4,096-character fact-string ceiling.
    path = "app/code/Example/view/frontend/web/js/library.js"
    callee = "(function () { /* " + "long source α " * 600 + " */ return finish(); })"
    source = callee + "();\n"
    catalog = PluginCatalog.discover(Path(__file__).resolve().parents[3] / "analysis-plugins")
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="revision", paths=(path,),
    ))
    facts, diagnostics = runtime.graph_facts(FileArtifact(path, source), capabilities)
    call = next(fact for fact in facts if fact.kind == "javascript-call" and len(fact.target) > 4096)
    assert call.target == callee
    assert diagnostics == ()

    store = StructuralGenerationStore(tmp_path / "structural")
    connection = store.initialize(store.pending_paths("target"), bulk_load=buffered)
    writer = StructuralGraphWriter(connection, buffer_file_relations=buffered)
    indexer = FileIndexer(loader=None, splitter=ASTCodeSplitter(plugin_runtime=runtime),
                          plugin_runtime=runtime)
    try:
        indexer.index_document(Document(source, {"path": path}), writer=writer,
                               dispositions={}, capabilities=capabilities,
                               skipped_paths=set(), check_cancelled=lambda: None)
        writer.build_lookup_indexes()
        writer.resolve_relations()
        stored = connection.execute(
            "SELECT target FROM relations WHERE origin='plugin' AND kind=? AND target=?",
            (call.kind, callee),
        ).fetchall()
        assert [row[0] for row in stored] == [callee]
        source_units = connection.execute(
            "SELECT content FROM units WHERE path=? AND record_type='source_unit'", (path,),
        ).fetchall()
        assert any(row[0].strip() == source.strip() for row in source_units)
        assert "plugin-index-output-limit" not in caplog.text
        assert "Plugin graph extraction failed open" not in caplog.text
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()
