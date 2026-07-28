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


def test_cross_language_contract_reference_removal_keeps_exact_consumers():
    files = {
        "contract/invoice-payload.txt": (
            "Invoice payload contract\n"
            "amountMinor: integer amount in the currency minor unit\n"
            "currency: ISO 4217 currency code\n"
        ),
        "backend/InvoicePayload.java": (
            "return Map.of(\"amountMinor\", amountMinor, \"currency\", currency);\n"
        ),
        "worker/invoice_ledger.py": (
            "def amount(payload):\n"
            "    return payload[\"amountMinor\"]\n"
        ),
        "worker/test_invoice_ledger.py": (
            "def test_amount():\n"
            "    assert amount({\"amountMinor\": 1299}) == 1299\n"
        ),
    }
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision=REVISION,
        paths=tuple(sorted(files)),
    ))

    assert capabilities.repository_plugins == (
        "data-contracts",
        "java",
        "python",
    )

    base = runtime.start_repository_analysis(capabilities, REVISION)
    base.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(files.items())
    ))
    base_analysis, diagnostics = base.finish()
    assert diagnostics == ()
    assert any(
        fact.kind == "data-contract-reference"
        and fact.path == "backend/InvoicePayload.java"
        and fact.target
        == "contract/invoice-payload.txt::amountMinor"
        for fact in _facts(base_analysis)
    )

    overlay = runtime.start_repository_analysis(
        capabilities,
        "fedcba9876543210",
        snapshots=base_analysis.snapshots,
    )
    overlay.ingest((FileArtifact(
        "backend/InvoicePayload.java",
        'return Map.of("amount", amountMinor, "currency", currency);\n',
    ),))
    overlay_analysis, diagnostics = overlay.finish()
    assert diagnostics == ()

    removed = next(
        fact
        for fact in _facts(overlay_analysis)
        if fact.kind == "data-contract-pr-removed-reference"
        and fact.path == "backend/InvoicePayload.java"
        and dict(fact.attributes)["field"] == "amountMinor"
    )
    assert removed.related_paths == (
        "contract/invoice-payload.txt",
        "worker/invoice_ledger.py",
        "worker/test_invoice_ledger.py",
    )


def test_data_contract_snapshot_and_output_are_deterministic():
    files = {
        "schemas/user.schema.json": (
            '{"type":"object","properties":{"userId":{"type":"string"}}}'
        ),
        "src/user.ts": (
            "export interface User { userId: string }\n"
            "export const id = (user: User) => user.userId;\n"
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
