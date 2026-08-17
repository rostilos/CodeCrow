from __future__ import annotations

from pathlib import Path

from codecrow_plugins import (
    FileArtifact,
    PluginCatalog,
    PluginRuntime,
    ProjectSelector,
    RepositoryFacts,
)


PLUGINS_ROOT = Path(__file__).resolve().parents[3]
REVISION = "0123456789abcdef"


def _facts(analysis):
    return {
        fact
        for packet in analysis.packets
        for fact in packet.facts
    }


def test_graphql_reference_removal_keeps_only_the_exact_schema_path():
    files = {
        "schema/catalog.graphqls": (
            "type Query { products: Products }\n"
            "type Products { items: [Product] }\n"
            "type Product { product_type: String sku: String }\n"
        ),
        "app/design/frontend/Acme/theme/templates/product.phtml": (
            "<script>const request = `query ProductList { "
            "products { items { product_type sku } } }`;</script>\n"
        ),
        "unrelated/other.phtml": "$block->getData('product_type');\n",
    }
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision=REVISION,
        paths=tuple(sorted(files)),
    ))

    assert "data-contracts" in capabilities.repository_plugins

    base = runtime.start_repository_analysis(capabilities, REVISION)
    base.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(files.items())
    ))
    base_analysis, diagnostics = base.finish()
    assert diagnostics == ()
    assert any(
        fact.kind == "data-contract-reference"
        and fact.path == "app/design/frontend/Acme/theme/templates/product.phtml"
        and fact.target
        == "schema/catalog.graphqls::Product.product_type"
        for fact in _facts(base_analysis)
    )
    assert not any(
        fact.path == "unrelated/other.phtml"
        for fact in _facts(base_analysis)
    )

    overlay = runtime.start_repository_analysis(
        capabilities,
        "fedcba9876543210",
        snapshots=base_analysis.snapshots,
    )
    overlay.ingest((FileArtifact(
        "app/design/frontend/Acme/theme/templates/product.phtml",
        "<script>const request = `query ProductList { products { items { sku } } }`;</script>\n",
    ),))
    overlay_analysis, diagnostics = overlay.finish()
    assert diagnostics == ()

    removed = next(
        fact
        for fact in _facts(overlay_analysis)
        if fact.kind == "data-contract-pr-removed-reference"
        and fact.path == "app/design/frontend/Acme/theme/templates/product.phtml"
        and dict(fact.attributes)["field"] == "product_type"
    )
    assert removed.related_paths == (
        "schema/catalog.graphqls",
    )


def test_data_contract_snapshot_and_output_are_deterministic():
    files = {
        "schemas/base.schema.json": (
            '{"type":"object","properties":{"userId":{"type":"string"}}}'
        ),
        "schemas/user.schema.json": (
            '{"allOf":[{"$ref":"base.schema.json#/properties/userId"}]}'
        ),
    }
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision=REVISION,
        paths=tuple(sorted(files)),
    ))

    def analyze():
        handle = runtime.start_repository_analysis(capabilities, REVISION)
        handle.ingest(tuple(
            FileArtifact(path, content)
            for path, content in sorted(files.items())
        ))
        return handle.finish()

    first, first_diagnostics = analyze()
    second, second_diagnostics = analyze()

    assert first_diagnostics == ()
    assert second_diagnostics == ()
    assert first == second


def test_graphql_operations_inside_host_multiline_literals_are_structural():
    files = {
        "schema/invoice.graphqls": (
            "type Query { invoice: InvoicePayload! }\n"
            "type InvoicePayload { amountMinor: Int! currency: String! }\n"
        ),
        "worker/invoice.py": '''QUERY = """
query InvoiceLedger {
  invoice { amountMinor currency }
}
"""
''',
    }
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision=REVISION,
        paths=tuple(sorted(files)),
    ))

    handle = runtime.start_repository_analysis(capabilities, REVISION)
    handle.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(files.items())
    ))
    analysis, diagnostics = handle.finish()

    assert diagnostics == ()
    facts = _facts(analysis)
    assert {
        fact.target
        for fact in facts
        if fact.path == "worker/invoice.py"
    } == {
        "schema/invoice.graphqls::Query.invoice",
        "schema/invoice.graphqls::InvoicePayload.amountMinor",
        "schema/invoice.graphqls::InvoicePayload.currency",
    }
