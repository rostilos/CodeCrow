from __future__ import annotations

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


def _modules():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    package = plugin.__class__.__module__
    return (
        plugin,
        importlib.import_module(package + ".frontend_init"),
        importlib.import_module(package + ".requirejs"),
        importlib.import_module(package + ".javascript"),
    )


def test_frontend_init_parser_extracts_literal_attribute_and_script_components():
    _, frontend_init, _, _ = _modules()

    references = frontend_init.extract_frontend_initializers(r'''
        <div data-mage-init='{&quot;Vendor_Module/js/widget&quot;: {
            &quot;label&quot;: &quot;<?= $label ?>&quot;
        }}'></div>
        <script type="text/x-magento-init">
        {
            "#cart": {"Vendor_Module/js/cart": {"active": true}},
            "*": {"Magento_Ui/js/core/app": {"components": {}}}
        }
        </script>
        <script type="x-magento-init">
            {".message": {"Vendor_Module/js/message": {}}}
        </script>
    ''')

    assert {
        (
            reference.source_kind,
            reference.selector,
            reference.component,
        )
        for reference in references
    } == {
        ("data-mage-init", "self", "Vendor_Module/js/widget"),
        ("x-magento-init", "#cart", "Vendor_Module/js/cart"),
        ("x-magento-init", "*", "Magento_Ui/js/core/app"),
        ("x-magento-init", ".message", "Vendor_Module/js/message"),
    }


def test_frontend_init_parser_abstains_on_dynamic_ids_and_types_malformed_json():
    _, frontend_init, _, _ = _modules()

    dynamic = frontend_init.extract_frontend_initializers(r'''
        <div data-mage-init='{"<?= $component ?>": {}}'></div>
        <script type="application/json">
            {"*": {"Vendor_Module/js/not-magento-init": {}}}
        </script>
    ''')

    assert dynamic == ()
    with pytest.raises(frontend_init.MalformedFrontendInitSource):
        frontend_init.extract_frontend_initializers(
            '<div data-mage-init="{not-json}"></div>'
        )


def test_amd_parser_extracts_only_direct_literal_dependency_arrays():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_javascript")
    _, _, requirejs, _ = _modules()

    dependencies = requirejs.extract_amd_dependencies(r'''
        define('Vendor_Module/js/main', [
            'jquery', './helper', dynamicDependency, 'exports'
        ], function () {});
        require(['cartAlias'], function () {});
        requirejs(config, ['Vendor_Module/js/bootstrap'], function () {});
        require(dynamicDependencies);
        window.require(['Vendor_Module/js/not-direct']);
        const commonJs = require('Vendor_Module/js/not-amd');
    ''')

    assert {
        (
            dependency.consumer_kind,
            dependency.named_module,
            dependency.dependency,
            dependency.position,
        )
        for dependency in dependencies
    } == {
        ("define", "Vendor_Module/js/main", "jquery", 0),
        ("define", "Vendor_Module/js/main", "./helper", 1),
        ("require", "", "cartAlias", 0),
        ("requirejs", "", "Vendor_Module/js/bootstrap", 0),
    }

    inline = requirejs.extract_template_amd_dependencies(r'''
        <script type="application/json">{"not": "javascript"}</script>
        <script type="text/x-magento-init">
            {"*": {"Vendor_Module/js/not-amd": {}}}
        </script>
        <script type="text/javascript">
            require(['Vendor_Module/js/inline']);
        </script>
    ''')
    assert tuple(item.dependency for item in inline) == (
        "Vendor_Module/js/inline",
    )


def test_effective_requirejs_config_resolves_map_path_fallbacks_and_mixins():
    _, _, requirejs, javascript = _modules()
    config = requirejs.build_effective_requirejs_config(
        "frontend",
        "",
        ((
            "app/code/Vendor/Module/view/frontend/requirejs-config.js",
            (
                javascript.RequireJsRelation(
                    "map",
                    "cartAlias",
                    "maps-to",
                    "Vendor_Module/js/cart",
                    3,
                    scope="*",
                ),
                javascript.RequireJsRelation(
                    "path",
                    "Vendor_Module/js/cart",
                    "resolves-to",
                    "Vendor_Module/js/cart-primary",
                    6,
                    position=0,
                ),
                javascript.RequireJsRelation(
                    "path",
                    "Vendor_Module/js/cart",
                    "resolves-to",
                    "Vendor_Module/js/cart-fallback",
                    7,
                    position=1,
                ),
                javascript.RequireJsRelation(
                    "mixin",
                    "Vendor_Module/js/cart",
                    "mixed-by",
                    "Vendor_Module/js/cart-mixin",
                    10,
                ),
            ),
        ),),
    )

    assert config.resolve("./helper", "Vendor_Module/js/main") == (
        requirejs.RequireJsResolution("Vendor_Module/js/helper"),
    )
    resolutions = config.resolve("cartAlias", "Vendor_Module/js/main")
    assert tuple(item.identifier for item in resolutions) == (
        "Vendor_Module/js/cart-primary",
        "Vendor_Module/js/cart-fallback",
    )
    assert tuple(item.fallback_position for item in resolutions) == (0, 1)
    assert all(item.config_kinds == ("map", "path") for item in resolutions)
    assert tuple(
        item.target
        for item in config.mixins_for(
            resolutions[0].mapped_identifier,
            resolutions[0].identifier,
        )
    ) == ("Vendor_Module/js/cart-mixin",)


def _finish(artifacts: dict[str, str]):
    plugin, _, _, _ = _modules()
    session = plugin.start_repository_analysis("frontend-consumers").value
    session.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(artifacts.items())
    ))
    return session.finish(RepositoryAnalysis())


def _consumer_artifacts() -> dict[str, str]:
    return {
        "app/etc/config.php": """<?php return ['modules' => [
            'Vendor_Module' => 1,
        ]];""",
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        "app/code/Vendor/Module/etc/crontab.xml": r"""
            <config><group id="default"><job
                name="vendor_frontend_job"
                instance="Vendor\Module\Cron\Frontend"
                method="execute" /></group></config>
        """,
        "app/code/Vendor/Module/view/frontend/layout/default.xml": r"""
            <page><body><block
                name="vendor.frontend"
                class="Vendor\Module\Block\Frontend"
                template="Vendor_Module::frontend.phtml" /></body></page>
        """,
        "app/code/Vendor/Module/view/frontend/templates/frontend.phtml": r'''
            <div data-mage-init='{"cartAlias": {}}'></div>
            <script type="text/x-magento-init">
                {"#cart": {"Vendor_Module/js/direct": {}}}
            </script>
            <script>
                require(['cartAlias', dynamicDependency], function () {});
            </script>
        ''',
        "app/code/Vendor/Module/view/frontend/requirejs-config.js": r"""
            var config = {
                map: {'*': {
                    cartAlias: 'Vendor_Module/js/cart'
                }},
                config: {mixins: {
                    'Vendor_Module/js/cart': {
                        'Vendor_Module/js/cart-mixin': true
                    }
                }}
            };
        """,
        "app/code/Vendor/Module/view/frontend/web/js/cart.js": (
            "define(['./helper'], function () {});"
        ),
        "app/code/Vendor/Module/view/frontend/web/js/helper.js": (
            "define([], function () {});"
        ),
        "app/code/Vendor/Module/view/frontend/web/js/direct.js": (
            "define([], function () {});"
        ),
        "app/code/Vendor/Module/view/frontend/web/js/cart-mixin.js": (
            "define([], function () {});"
        ),
    }


def test_frontend_consumers_connect_init_and_amd_to_effective_requirejs_assets():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_javascript")
    artifacts = _consumer_artifacts()
    outcome = _finish(artifacts)

    assert outcome.status is OutcomeStatus.HANDLED
    facts = tuple(
        fact
        for packet in outcome.value.packets
        for fact in packet.facts
    )
    init = next(
        fact
        for fact in facts
        if fact.kind == "magento-frontend-init"
        and fact.source == "data-mage-init:self"
    )
    assert init.target == "Vendor_Module/js/cart"
    assert {
        "app/code/Vendor/Module/view/frontend/requirejs-config.js",
        "app/code/Vendor/Module/view/frontend/web/js/cart.js",
        "app/code/Vendor/Module/view/frontend/layout/default.xml",
    } <= set(init.related_paths)

    inline_amd = next(
        fact
        for fact in facts
        if fact.kind == "magento-amd-dependency"
        and fact.path.endswith("templates/frontend.phtml")
    )
    assert inline_amd.target == "Vendor_Module/js/cart"
    relative_amd = next(
        fact
        for fact in facts
        if fact.kind == "magento-amd-dependency"
        and fact.path.endswith("web/js/cart.js")
    )
    assert relative_amd.source == "Vendor_Module/js/cart"
    assert relative_amd.target == "Vendor_Module/js/helper"
    assert (
        "app/code/Vendor/Module/view/frontend/web/js/helper.js"
        in relative_amd.related_paths
    )

    mixin = next(
        fact
        for fact in facts
        if fact.kind == "magento-requirejs-consumer-mixin"
        and fact.path.endswith("templates/frontend.phtml")
    )
    assert mixin.target == "Vendor_Module/js/cart-mixin"
    assert (
        "app/code/Vendor/Module/view/frontend/web/js/cart-mixin.js"
        in mixin.related_paths
    )
    assert not any(
        fact.target == "dynamicDependency"
        for fact in facts
        if fact.kind in {
            "magento-amd-dependency",
            "magento-frontend-init",
        }
    )


def test_malformed_frontend_consumer_sources_fail_open_per_file():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_javascript")
    artifacts = _consumer_artifacts()
    phtml_path = (
        "app/code/Vendor/Module/view/frontend/templates/frontend.phtml"
    )
    broken_js_path = (
        "app/code/Vendor/Module/view/frontend/web/js/broken.js"
    )
    artifacts[phtml_path] = '<div data-mage-init="{not-json}"></div>'
    artifacts[broken_js_path] = "define(['Vendor_Module/js/cart'], function ("

    outcome = _finish(artifacts)

    assert outcome.status is OutcomeStatus.HANDLED
    facts = tuple(
        fact
        for packet in outcome.value.packets
        for fact in packet.facts
    )
    assert any(fact.kind == "magento-cron-job" for fact in facts)
    assert any(
        fact.kind == "magento-amd-dependency"
        and fact.path.endswith("web/js/cart.js")
        for fact in facts
    )
    diagnostics = {
        (item.code, item.path, item.recoverable)
        for item in outcome.value.diagnostics
    }
    assert (
        "magento-frontend-init-source-malformed",
        phtml_path,
        True,
    ) in diagnostics
    assert (
        "magento-javascript-source-malformed",
        broken_js_path,
        True,
    ) in diagnostics


def test_requirejs_consumers_do_not_cross_sibling_theme_selections():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_javascript")
    module_consumer = (
        "app/code/Vendor/Module/view/frontend/web/js/consumer.js"
    )
    first_root = "app/design/frontend/Acme/first"
    second_root = "app/design/frontend/Acme/second"
    artifacts = {
        "app/etc/config.php": """<?php return ['modules' => [
            'Vendor_Module' => 1,
        ]];""",
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        module_consumer: "define(['cartAlias'], function () {});",
    }
    for root, target in (
        (first_root, "first-cart"),
        (second_root, "second-cart"),
    ):
        artifacts[f"{root}/theme.xml"] = "<theme />"
        artifacts[f"{root}/requirejs-config.js"] = f"""
            var config = {{map: {{'*': {{
                cartAlias: 'Vendor_Module/js/{target}'
            }}}}}};
        """
        artifacts[f"{root}/Vendor_Module/web/js/{target}.js"] = (
            "define([], function () {});"
        )
        artifacts[f"{root}/Vendor_Module/web/js/consumer.js"] = (
            "define(['cartAlias'], function () {});"
        )

    outcome = _finish(artifacts)
    facts = tuple(
        fact
        for packet in outcome.value.packets
        for fact in packet.facts
        if fact.kind == "magento-amd-dependency"
    )
    module_fact = next(fact for fact in facts if fact.path == module_consumer)
    assert module_fact.target == "cartAlias"
    assert dict(module_fact.attributes)["resolution"] == (
        "theme-dependent-requirejs-abstained"
    )
    assert not {
        f"{first_root}/Vendor_Module/web/js/first-cart.js",
        f"{second_root}/Vendor_Module/web/js/second-cart.js",
        f"{first_root}/requirejs-config.js",
        f"{second_root}/requirejs-config.js",
    }.intersection(module_fact.related_paths)

    first_fact = next(
        fact
        for fact in facts
        if fact.path == f"{first_root}/Vendor_Module/web/js/consumer.js"
    )
    assert first_fact.target == "Vendor_Module/js/first-cart"
    assert f"{first_root}/requirejs-config.js" in first_fact.related_paths
    assert (
        f"{first_root}/Vendor_Module/web/js/first-cart.js"
        in first_fact.related_paths
    )
    assert not any(
        path.startswith(second_root + "/")
        for path in first_fact.related_paths
    )
