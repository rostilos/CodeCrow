from pathlib import Path
from unittest.mock import patch

import pytest
from llama_index.core.schema import Document

from codecrow_plugins import (
    FileArtifact,
    FileDisposition,
    PluginCatalog,
    PluginRuntime,
    ProjectSelector,
    RepositoryFacts,
)
from rag_pipeline.core.splitter import ASTCodeSplitter
from rag_pipeline.core.repository_overlay import build_overlay_capabilities


PLUGINS_ROOT = Path(__file__).resolve().parents[3] / "analysis-plugins"


def test_overlay_projects_complete_repository_plugins_for_inference():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    effective_ids = tuple(
        descriptor.id
        for descriptor in catalog.registry.resolve(
            ("data-contracts", "java", "python", "typescript")
        )
    )
    evidence = {
        plugin_id: (
            f"indexed-target:main:sha256:target:{plugin_id}",
        )
        for plugin_id in effective_ids
    }

    capabilities = build_overlay_capabilities(
        catalog.registry,
        effective_ids,
        "ignored-legacy-fingerprint",
        (
            "backend/Invoice.java",
            "web/invoice.ts",
            "worker/invoice.py",
        ),
        revision="0123456789abcdef",
        detection_evidence=evidence,
    )

    assert ProjectSelector(catalog.registry).validate(
        capabilities,
        "0123456789abcdef",
    ) == capabilities
    assert capabilities.repository_plugins == effective_ids
    assert capabilities.file_plugins == {
        "backend/Invoice.java": ("java",),
        "web/invoice.ts": ("typescript",),
        "worker/invoice.py": ("python",),
    }


@pytest.mark.parametrize(
    ("path", "language_id", "source"),
    (
        (
            "src/service.py",
            "python",
            "class Service:\n    def run(self):\n        return True\n",
        ),
        (
            "src/Service.java",
            "java",
            "public class Service {\n public boolean run() {\n  return true;\n }\n}\n",
        ),
        (
            "src/service.js",
            "javascript",
            "export function run() {\n const value = true;\n return value;\n}\n",
        ),
        (
            "src/service.ts",
            "typescript",
            "export function run(): boolean {\n const value: boolean = true;\n return value;\n}\n",
        ),
        (
            "cmd/main.go",
            "go",
            "package main\n\nfunc run() bool {\n return true\n}\n",
        ),
        (
            "src/Service.php",
            "php",
            "<?php\nclass Service {\n public function run(): bool {\n  return true;\n }\n}\n",
        ),
    ),
)
def test_selected_language_plugins_produce_semantic_chunks(
    path,
    language_id,
    source,
):
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=(path,),
    ))
    splitter = ASTCodeSplitter(plugin_runtime=runtime)

    chunks = splitter.split_documents(
        [Document(text=source, metadata={"path": path})],
        capabilities=capabilities,
    )

    assert chunks
    assert any(
        chunk.metadata.get("content_type") == "functions_classes"
        for chunk in chunks
    )
    assert all(
        chunk.metadata.get("plugin_syntax") == {
            "plugin": language_id,
            "language": language_id,
        }
        for chunk in chunks
    )


def test_magento_polyglot_repository_indexes_htaccess_with_neutral_fallback():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=(
            ".htaccess",
            "app/code/Acme/Checkout/Controller/Save.php",
            "app/code/Acme/Checkout/etc/module.xml",
            "app/code/Acme/Checkout/view/frontend/web/js/checkout.js",
            "app/etc/config.php",
            "bin/magento",
            "composer.json",
        ),
        marker_contents={
            "composer.json": '{"require":{"magento/framework":"*"}}',
        },
    ))
    splitter = ASTCodeSplitter(parser_threshold=1000, plugin_runtime=runtime)

    chunks = splitter.split_documents(
        [Document(
            text="RewriteEngine on\nRewriteRule .* index.php [L]\n",
            metadata={"path": ".htaccess", "language": "text"},
        )],
        capabilities=capabilities,
    )

    assert {"javascript", "magento", "php"} <= set(
        capabilities.repository_plugins
    )
    assert ".htaccess" not in capabilities.file_plugins
    assert chunks
    assert all("plugin_syntax" not in chunk.metadata for chunk in chunks)


def test_magento_config_uses_repository_graph_not_semantic_chunk_injection():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=(
            "app/code/Acme/Checkout/etc/di.xml",
            "app/code/Acme/Checkout/etc/module.xml",
            "app/etc/config.php",
            "bin/magento",
            "composer.json",
        ),
        marker_contents={"composer.json": '{"require":{"magento/framework":"*"}}'},
    ))
    splitter = ASTCodeSplitter(parser_threshold=1000, plugin_runtime=runtime)
    document = Document(
        text=(
            '<config><preference for="Acme\\Api\\CartInterface" '
            'type="Acme\\Model\\Cart" /></config>'
        ),
        metadata={"path": "app/code/Acme/Checkout/etc/di.xml", "language": "xml"},
    )

    chunks = splitter.split_documents([document], capabilities=capabilities)

    assert chunks
    assert "magento" in capabilities.repository_plugins
    assert runtime.file_disposition(
        "app/code/Acme/Checkout/etc/di.xml", capabilities
    ) is FileDisposition.ARCHITECTURE_ONLY
    assert "plugin_fact_kinds" not in chunks[0].metadata
    assert "Plugin graph facts:" not in chunks[0].text

    handle = runtime.start_repository_analysis(capabilities, "0123456789abcdef")
    handle.ingest(tuple(sorted((
        FileArtifact(
            "app/code/Acme/Checkout/etc/di.xml",
            document.text,
        ),
        FileArtifact(
            "app/code/Acme/Checkout/etc/module.xml",
            '<config><module name="Acme_Checkout" /></config>',
        ),
        FileArtifact(
            "app/etc/config.php",
            "<?php return ['modules' => ['Acme_Checkout' => 1]];",
        ),
    ), key=lambda artifact: artifact.path)))
    analysis, diagnostics = handle.finish()

    assert diagnostics == ()
    assert any(
        fact.kind == "magento-di-effective-preference"
        for packet in analysis.packets
        for fact in packet.facts
    )


def test_language_facts_stay_in_metadata_without_polluting_semantic_text():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=("src/service.py",),
    ))
    splitter = ASTCodeSplitter(parser_threshold=1000, plugin_runtime=runtime)
    source = """\
def review():
    validate()

def validate():
    return True
"""
    document = Document(
        text=source,
        metadata={"path": "src/service.py", "language": "python"},
    )

    chunks = splitter.split_documents([document], capabilities=capabilities)

    assert chunks
    assert "python" in capabilities.repository_plugins
    assert any(
        chunk.metadata.get("plugin_graph_facts")
        for chunk in chunks
    )
    assert all(
        "Plugin graph facts:" not in chunk.text
        for chunk in chunks
    )
    rendered_source = "\n".join(chunk.text for chunk in chunks)
    assert "def review():" in rendered_source
    assert "def validate():" in rendered_source


def test_selected_language_syntax_does_not_depend_on_legacy_language_selection():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=("src/service.py",),
    ))
    splitter = ASTCodeSplitter(plugin_runtime=runtime)
    document = Document(
        text="""\
class Service:
    def review(self):
        return True
""",
        metadata={"path": "src/service.py", "language": "python"},
    )

    with patch(
        "rag_pipeline.core.splitter.splitter.get_language_from_path",
        return_value=None,
    ):
        chunks = splitter.split_documents(
            [document],
            capabilities=capabilities,
        )

    assert chunks
    assert any(
        chunk.metadata.get("plugin_syntax") == {
            "plugin": "python",
            "language": "python",
        }
        for chunk in chunks
    )
    assert any(
        chunk.metadata.get("content_type") == "functions_classes"
        for chunk in chunks
    )
