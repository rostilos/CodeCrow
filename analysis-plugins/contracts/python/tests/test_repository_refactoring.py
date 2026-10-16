"""Behavioral regressions for indexed ingestion and scoped plugin services."""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from codecrow_plugins import FileArtifact, PluginCatalog, RepositoryAnalysis, SymbolDefinition


PLUGINS_ROOT = Path(__file__).resolve().parents[3]


def _plugin_module(plugin_id: str, module_name: str):
    plugin = PluginCatalog.discover(PLUGINS_ROOT).implementation(plugin_id)
    return plugin, importlib.import_module(plugin.__class__.__module__ + "." + module_name)


def test_php_snapshot_is_identical_after_partitioned_ingestion_and_restore(monkeypatch):
    monkeypatch.setenv("CODECROW_PHP_PARSE_WORKERS", "2")
    plugin, _ = _plugin_module("php", "repository")
    artifacts = (
        FileArtifact("A.php", "<?php class A { public function a(): B {} }"),
        FileArtifact("B.php", "<?php class B { public function b() {} }"),
        FileArtifact("call.phtml", "<?= $block->label() ?>"),
    )
    whole = plugin.start_repository_analysis("first").value
    whole.ingest(artifacts)
    expected = whole.finish(RepositoryAnalysis()).value

    partitioned = plugin.start_repository_analysis("first").value
    for artifact in reversed(artifacts):
        partitioned.ingest((artifact,))
    actual = partitioned.finish(RepositoryAnalysis()).value
    assert actual == expected
    restored = plugin.restore_repository_analysis("next", expected.snapshots).value
    assert restored.finish(RepositoryAnalysis()).value == expected


def test_php_failed_batch_drops_changed_source_facts_and_closes_workers(monkeypatch):
    monkeypatch.setenv("CODECROW_PHP_PARSE_WORKERS", "2")
    plugin, repository = _plugin_module("php", "repository")
    session = plugin.start_repository_analysis("head").value
    original = FileArtifact("A.php", "<?php class A {}")
    session.ingest((original, FileArtifact("C.php", "<?php class Unchanged {}")))
    session.finish(RepositoryAnalysis())
    parse = repository._parse_artifact

    def parser(artifact):
        if artifact.path == "B.php":
            raise RuntimeError("parser unavailable")
        return parse(artifact)

    monkeypatch.setattr(repository, "_parse_artifact", parser)
    with pytest.raises(RuntimeError, match="parser unavailable"):
        session.ingest((
            FileArtifact("A.php", "<?php class ChangedA {}"),
            FileArtifact("B.php", "<?php class B {}"),
        ))
    assert session._executor is None
    assert [symbol.qualified_name for symbol in session.finish(RepositoryAnalysis()).value.symbols] == ["Unchanged"]
    monkeypatch.setattr(repository, "_parse_artifact", parse)
    session.ingest((FileArtifact("A.php", "<?php class Recovered {}"),))
    assert [symbol.qualified_name for symbol in session.finish(RepositoryAnalysis()).value.symbols] == ["Recovered", "Unchanged"]


def test_php_last_path_update_and_non_declaration_replacement_remove_old_symbols():
    plugin, _ = _plugin_module("php", "repository")
    session = plugin.start_repository_analysis("head").value
    session.ingest((
        FileArtifact("A.php", "<?php class Old {}"),
        FileArtifact("A.php", "<?php class Current {}"),
        FileArtifact("B.php", "<?php class Other {}"),
    ))
    assert {symbol.qualified_name for symbol in session.finish(RepositoryAnalysis()).value.symbols} == {"Current", "Other"}
    session.ingest((FileArtifact("A.php", "<?php echo 'no declaration';"),))
    assert {symbol.qualified_name for symbol in session.finish(RepositoryAnalysis()).value.symbols} == {"Other"}


def test_php_ast_walk_handles_deep_source_tree_and_keeps_preorder():
    _, syntax = _plugin_module("php", "syntax")
    leaf = SimpleNamespace(children=[], value=1500)
    root = leaf
    for depth in reversed(range(1500)):
        root = SimpleNamespace(children=[root], value=depth)
    sibling = SimpleNamespace(children=[], value="sibling")
    tree = SimpleNamespace(children=[root, sibling], value="root")
    values = [node.value for node in syntax._walk(tree)]
    assert values == ["root", *range(1501), "sibling"]


def test_magento_source_index_preserves_ambiguity_and_template_call_evidence():
    _, module = _plugin_module("magento", "resolution_index")
    duplicate_a = SymbolDefinition("Vendor\\Thing", "class", "a.php")
    duplicate_b = SymbolDefinition("Vendor\\Thing", "class", "b.php")
    template = SymbolDefinition(
        "template:one.phtml", "template", "one.phtml",
        attributes=(("php-template-instance-call-reference:0000", '{"receiver":"$block","method":"label","line":9}'),),
    )
    index = module.RepositorySourceIndex("magento", {}, (duplicate_a, duplicate_b, template))
    assert index.symbol("Vendor\\Thing") == duplicate_a
    assert index.unique_symbol_casefold("vendor\\thing") is None
    assert [(call.method, call.line) for call in index.template_php_calls("one.phtml", "$block")] == [("label", 9)]
    assert index.template_php_calls("missing.phtml", "$block") == ()
    assert index.template_php_calls("one.phtml", "$other") == ()


def test_hyva_conflicting_layout_declaration_stays_ambiguous():
    from codecrow_plugins import ArchitecturePacket, GraphFact

    _, module = _plugin_module("hyva", "dependency_index")
    path = "app/code/Vendor/Module/view/frontend/layout/default.xml"
    facts = tuple(
        GraphFact(
            "magento-layout-block", "block", "declares", "block", path, number,
            (("name", "block"), ("selectedTemplatePath", template)),
        )
        for number, template in enumerate(("one.phtml", "two.phtml", "one.phtml"), 1)
    )
    dependencies = RepositoryAnalysis(packets=(ArchitecturePacket(
        "magento", "magento-layout", "default", (path,), facts,
    ),))
    blocks, _sources = module.HyvaDependencyIndex.layout_topology(dependencies)
    assert "block" not in blocks[path]


def test_hyva_failed_changed_template_does_not_retain_restored_metadata(monkeypatch):
    plugin, repository = _plugin_module("hyva", "repository")
    template_runtime = importlib.import_module(plugin.__class__.__module__ + ".template_runtime")
    record = template_runtime.TemplateRuntime(alpine_identifiers=("old",))
    session = repository.HyvaRepositorySession("hyva", "new-head", {"one.phtml": record})

    def broken(_content):
        raise RuntimeError("template extraction unavailable")

    monkeypatch.setattr(repository, "extract_template_runtime", broken)
    with pytest.raises(RuntimeError, match="template extraction unavailable"):
        session.ingest((FileArtifact("one.phtml", "changed"),))
    assert "one.phtml" not in session.templates


def test_magento_source_ownership_uses_nearest_exact_root_and_refreshes_indexes():
    _, module = _plugin_module("magento", "resolution_index")
    broad = module.ModuleRecord("Broad", "", "etc/module.xml", (), True, 0)
    parent = module.ModuleRecord("Parent", "app/code/Parent", "parent.xml", (), True, 1)
    nested = module.ModuleRecord("Nested", "app/code/Parent/Nested", "nested.xml", (), True, 2)
    index = module.RepositorySourceIndex("magento", {}, ())
    modules = (broad, parent, nested)
    assert index.module_for_path("app/code/Parent/Nested/Service.php", modules) == nested
    assert index.module_for_path("app/code/Parent/Service.php", modules) == parent
    assert index.module_for_path("app/code/ParentOther/Service.php", modules) == broad
    assert index.module_for_path("app/code/Parent/Service.php", (broad,)) == broad
    parent_theme = module.ThemeRecord("parent", "frontend", "app/design/theme", "theme.xml")
    child_theme = module.ThemeRecord("child", "frontend", "app/design/theme/child", "child.xml")
    themes = (parent_theme, child_theme)
    assert index.theme_for_path("app/design/theme/child/template.phtml", themes) == child_theme
    assert index.theme_for_path("app/design/theme/template.phtml", themes) == parent_theme
    assert index.theme_for_path("app/design/themeOther/template.phtml", themes) is None
