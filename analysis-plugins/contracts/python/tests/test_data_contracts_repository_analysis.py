from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

from codecrow_plugins import (
    FileArtifact,
    PluginCatalog,
    PluginRuntime,
    ProjectSelector,
    RepositorySnapshot,
    RepositoryFacts,
)
from codecrow_plugins.graphql import (
    parse_operations,
    parse_schema,
    parse_schema_root_types,
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
        and fact.kind == "data-contract-reference"
    } == {
        "schema/invoice.graphqls::Query.invoice",
        "schema/invoice.graphqls::InvoicePayload.amountMinor",
        "schema/invoice.graphqls::InvoicePayload.currency",
    }


def test_graphql_parser_supports_shorthand_fragments_custom_roots_and_values():
    roots = dict(parse_schema_root_types(
        "schema { query: RootQuery } type RootQuery { user: User }",
    ))
    shorthand = parse_operations("{ user { id } }", root_types=roots)
    fragments = parse_operations(
        "query User { user { ...UserFields email } } "
        "fragment UserFields on User { id name }",
        root_types=roots,
    )
    directive = parse_schema(
        "type RootQuery @policy(items: [a, b], limit: 10) { user: User }",
    )[0].directives[0]

    assert {item.segments for item in shorthand} == {
        ("user",),
        ("user", "id"),
    }
    assert {item.root for item in shorthand} == {"RootQuery"}
    assert {item.segments for item in fragments} == {
        ("user",),
        ("user", "email"),
        ("user", "id"),
        ("user", "name"),
    }
    assert directive.argument("items") == "[a,b]"
    assert directive.argument("limit") == "10"


def test_host_language_query_identifier_is_not_graphql():
    assert parse_operations(
        "function query() { user { id } }",
        embedded_only=True,
    ) == ()


def test_json_reference_uses_its_actual_source_line():
    files = {
        "schemas/base.schema.json": '{"type":"object"}',
        "schemas/user.schema.json": (
            "{\n"
            "  \"allOf\": [\n"
            "    {\"$ref\": \"base.schema.json\"}\n"
            "  ]\n"
            "}\n"
        ),
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
    reference = next(
        fact for fact in _facts(analysis)
        if fact.relation == "references-json-schema-target"
    )
    assert reference.line == 3


def test_json_reference_text_inside_a_string_is_not_a_relationship():
    files = {
        "schemas/base.schema.json": '{"type":"object"}',
        "schemas/user.schema.json": json.dumps({
            "description": 'example: "$ref": "base.schema.json"',
        }),
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
    assert not any(
        fact.relation == "references-json-schema-target"
        for fact in _facts(analysis)
    )


def test_line_only_graphql_move_does_not_emit_a_removal():
    files = {
        "schema/user.graphqls": (
            "type Query { user: User }\n"
            "type User { id: ID }\n"
        ),
        "src/query.txt": "const q = `query { user { id } }`;\n",
    }
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision=REVISION,
        paths=tuple(sorted(files)),
    ))
    base = runtime.start_repository_analysis(capabilities, REVISION)
    base.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(files.items())
    ))
    base_analysis, diagnostics = base.finish()
    assert diagnostics == ()

    overlay = runtime.start_repository_analysis(
        capabilities,
        "fedcba9876543210",
        snapshots=base_analysis.snapshots,
    )
    overlay.ingest((FileArtifact(
        "src/query.txt",
        "\nconst q = `query { user { id } }`;\n",
    ),))
    overlay_analysis, diagnostics = overlay.finish()

    assert diagnostics == ()
    assert not any(
        fact.kind == "data-contract-pr-removed-reference"
        for fact in _facts(overlay_analysis)
    )


def test_legacy_snapshot_names_remain_resolvable():
    payload = [
        {
            "path": "schema/contract.txt",
            "isContract": True,
            "declarations": [{"name": "amountMinor", "line": 2}],
            "references": [],
        },
        {
            "path": "src/consumer.py",
            "isContract": False,
            "declarations": [],
            "references": [{"name": "amountMinor", "line": 4}],
        },
    ]
    encoded = base64.b64encode(gzip.compress(
        json.dumps(payload).encode("utf-8"),
        mtime=0,
    )).decode("ascii")
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision=REVISION,
        paths=("schema/contract.txt", "src/consumer.py"),
    ))
    handle = runtime.start_repository_analysis(
        capabilities,
        "fedcba9876543210",
        snapshots=(RepositorySnapshot(
            "data-contracts",
            "data-contract-reference-graph",
            encoded,
        ),),
    )

    analysis, diagnostics = handle.finish()

    assert diagnostics == ()
    fact = next(
        fact for fact in _facts(analysis)
        if fact.kind == "data-contract-reference"
    )
    assert fact.target == "schema/contract.txt::amountMinor"
    assert fact.line == 4
