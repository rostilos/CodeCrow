from __future__ import annotations

import builtins
import importlib
from pathlib import Path

import pytest

from codecrow_plugins import (
    FileArtifact,
    OutcomeStatus,
    PluginCatalog,
    RepositoryAnalysis,
)


PLUGINS_ROOT = Path(__file__).resolve().parents[3]


def _artifacts(
    *,
    phtml: str = "<script>window.checkoutReady = function () {};</script>",
    requirejs: str | None = None,
) -> dict[str, str]:
    artifacts = {
        "app/etc/config.php": """<?php return ['modules' => [
            'Vendor_Module' => 1,
        ]];""",
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        "app/code/Vendor/Module/etc/crontab.xml": r"""
            <config><group id="default"><job
                name="vendor_module_job"
                instance="Vendor\Module\Cron\Job"
                method="execute" /></group></config>
        """,
        "app/code/Vendor/Module/view/frontend/layout/default.xml": r"""
            <page><body><block
                name="vendor.module.sample"
                class="Vendor\Module\Block\Sample"
                template="Vendor_Module::sample.phtml" /></body></page>
        """,
        "app/code/Vendor/Module/view/frontend/templates/sample.phtml": phtml,
    }
    if requirejs is not None:
        artifacts[
            "app/code/Vendor/Module/view/frontend/requirejs-config.js"
        ] = requirejs
    return artifacts


def _finish(artifacts: dict[str, str]):
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    session = plugin.start_repository_analysis("frontend-fail-open").value
    session.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(artifacts.items())
    ))
    return plugin, session.finish(RepositoryAnalysis())


def _fact_kinds(outcome) -> set[str]:
    return {
        fact.kind
        for packet in outcome.value.packets
        for fact in packet.facts
    }


def test_javascript_parser_import_failure_has_a_typed_recoverable_boundary(
    monkeypatch,
):
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    javascript = importlib.import_module(
        plugin.__class__.__module__ + ".javascript"
    )
    real_import = builtins.__import__

    def import_without_javascript_parser(name, *args, **kwargs):
        if name == "tree_sitter_javascript":
            raise ImportError("simulated missing optional parser")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_javascript_parser)

    with pytest.raises(javascript.JavaScriptParserUnavailable) as failure:
        javascript.extract_requirejs_relations("var config = {};")

    assert failure.value.diagnostic_code == (
        "magento-javascript-parser-unavailable"
    )
    assert failure.value.source_specific is False


def test_missing_optional_parser_skips_stage_and_later_core_stages_continue(
    monkeypatch,
):
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    session = plugin.start_repository_analysis("frontend-parser-missing").value
    session.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(_artifacts(
            requirejs="var config = {deps: ['Vendor_Module/js/bootstrap']};",
        ).items())
    ))
    real_import = builtins.__import__

    def import_without_javascript_parser(name, *args, **kwargs):
        if name == "tree_sitter_javascript":
            raise ImportError("simulated missing optional parser")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(
        builtins,
        "__import__",
        import_without_javascript_parser,
    )

    outcome = session.finish(RepositoryAnalysis())

    assert outcome.status is OutcomeStatus.HANDLED
    assert {"magento-module", "magento-cron-job"} <= _fact_kinds(outcome)
    diagnostics = tuple(
        item
        for item in outcome.value.diagnostics
        if item.code == "magento-javascript-parser-unavailable"
    )
    assert len(diagnostics) == 1
    assert diagnostics[0].path is None
    assert diagnostics[0].recoverable is True
    assert "template globals enrichment skipped" in diagnostics[0].message


def test_malformed_optional_sources_are_quarantined_per_file():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_javascript")
    phtml_path = (
        "app/code/Vendor/Module/view/frontend/templates/sample.phtml"
    )
    requirejs_path = (
        "app/code/Vendor/Module/view/frontend/requirejs-config.js"
    )

    artifacts = _artifacts(
        phtml="<script>window.checkoutReady = () => {</script>",
        requirejs="var config = { paths: { checkout: ",
    )
    artifacts["app/etc/config.php"] = """<?php return ['modules' => [
        'Vendor_Module' => 1,
        'Vendor_Healthy' => 1,
    ]];"""
    artifacts["app/code/Vendor/Healthy/etc/module.xml"] = (
        '<config><module name="Vendor_Healthy" /></config>'
    )
    artifacts[
        "app/code/Vendor/Healthy/view/frontend/requirejs-config.js"
    ] = """
        var config = {map: {'*': {
            healthyAlias: 'Vendor_Healthy/js/healthy'
        }}};
    """

    _, outcome = _finish(artifacts)

    assert outcome.status is OutcomeStatus.HANDLED
    assert {
        "magento-module",
        "magento-layout-block",
        "magento-cron-job",
        "magento-requirejs-map",
    } <= _fact_kinds(outcome)
    diagnostics = tuple(
        item
        for item in outcome.value.diagnostics
        if item.code == "magento-javascript-source-malformed"
    )
    assert {item.path for item in diagnostics} == {
        phtml_path,
        requirejs_path,
    }
    assert len(diagnostics) == 2
    assert all(item.recoverable for item in diagnostics)


def test_unexpected_frontend_programming_failure_remains_fatal(monkeypatch):
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    repository = importlib.import_module(
        plugin.__class__.__module__ + ".frontend_topology"
    )

    def broken_invariant(_content):
        raise AssertionError("simulated frontend invariant failure")

    monkeypatch.setattr(
        repository,
        "extract_template_global_references",
        broken_invariant,
    )

    with pytest.raises(RuntimeError, match=(
        "Magento template globals enrichment failed: AssertionError: "
        "simulated frontend invariant failure"
    )):
        _finish(_artifacts())
