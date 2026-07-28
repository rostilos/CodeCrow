from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from codecrow_plugins import PluginCatalog


pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_javascript")

PLUGINS_ROOT = Path(__file__).resolve().parents[3]


def test_requirejs_parser_extracts_mixins_maps_paths_and_shim_dependencies():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    javascript = importlib.import_module(plugin.__class__.__module__ + ".javascript")

    relations = javascript.extract_requirejs_relations(r"""
        var config = {
            paths: {
                checkoutAlias: [
                    'Acme_Checkout/js/checkout-primary',
                    'Acme_Checkout/js/checkout-fallback'
                ]
            },
            map: {
                '*': {
                    cartAction: 'Acme_Checkout/js/action/cart'
                },
                'Magento_Checkout/js/view/cart': {
                    cartAction: 'Acme_Checkout/js/action/scoped-cart'
                }
            },
            shim: {
                'Acme_Checkout/js/legacy': {
                    deps: ['jquery', 'mage/url']
                },
                'Acme_Checkout/js/shorthand': ['jquery']
            },
            deps: ['Acme_Checkout/js/bootstrap'],
            config: {
                mixins: {
                    'Magento_Checkout/js/action/place-order': {
                        'Acme_Checkout/js/action/place-order-mixin': true
                    }
                }
            }
        };
    """)
    triples = {
        (relation.kind, relation.source, relation.relation, relation.target)
        for relation in relations
    }

    assert (
        "path",
        "checkoutAlias",
        "resolves-to",
        "Acme_Checkout/js/checkout-primary",
    ) in triples
    assert (
        "path",
        "checkoutAlias",
        "resolves-to",
        "Acme_Checkout/js/checkout-fallback",
    ) in triples
    assert ("map", "cartAction", "maps-to", "Acme_Checkout/js/action/cart") in triples
    assert (
        "mixin",
        "Magento_Checkout/js/action/place-order",
        "mixed-by",
        "Acme_Checkout/js/action/place-order-mixin",
    ) in triples
    assert ("shim", "Acme_Checkout/js/legacy", "depends-on", "jquery") in triples
    assert ("shim", "Acme_Checkout/js/legacy", "depends-on", "mage/url") in triples
    assert (
        "shim",
        "Acme_Checkout/js/shorthand",
        "depends-on",
        "jquery",
    ) in triples
    assert (
        "dependency",
        "requirejs-config",
        "loads",
        "Acme_Checkout/js/bootstrap",
    ) in triples

    scoped_map = next(
        relation
        for relation in relations
        if relation.kind == "map"
        and relation.target == "Acme_Checkout/js/action/scoped-cart"
    )
    assert scoped_map.scope == "Magento_Checkout/js/view/cart"
    path_positions = {
        relation.target: relation.position
        for relation in relations
        if relation.kind == "path"
    }
    assert path_positions == {
        "Acme_Checkout/js/checkout-primary": 0,
        "Acme_Checkout/js/checkout-fallback": 1,
    }


def test_template_global_parser_keeps_only_direct_definitions_and_calls():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    javascript = importlib.import_module(plugin.__class__.__module__ + ".javascript")

    references = javascript.extract_template_global_references(r"""
        <div><?= $block->escapeHtml($label) ?></div>
        <script>
            function acceptsMany(items) {
                return Array.from(items);
            }
            window.fixBannerExternalLinks = acceptsMany;
            if (typeof window.fixBannerExternalLinks === 'function') {
                window.fixBannerExternalLinks(
                    document.querySelectorAll('.banner')
                );
            }
            window[dynamicName]();
            localOnly();
        </script>
    """)

    assert references == (
        javascript.TemplateGlobalReference(
            "fixBannerExternalLinks",
            "calls",
            9,
        ),
        javascript.TemplateGlobalReference(
            "fixBannerExternalLinks",
            "defines",
            7,
        ),
    )


def test_template_event_parser_keeps_only_direct_literal_browser_events():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    javascript = importlib.import_module(plugin.__class__.__module__ + ".javascript")

    references = javascript.extract_template_event_references(r"""
        <script>
            window.addEventListener("ga4-cart-event", event => use(event.detail));
            window.dispatchEvent(new CustomEvent('ga4-cart-event', {
                detail: { quantity: 2 }
            }));
            document.addEventListener('private-content-loaded', update);
            document.dispatchEvent(new Event("checkout:ready"));

            const dynamicName = "dynamic-event";
            window.addEventListener(dynamicName, update);
            window.dispatchEvent(new CustomEvent(dynamicName));
            element.addEventListener("element-local", update);
            $(window).trigger("jquery-event");
            window.dispatchEvent(new CustomEvent("<?= $eventName ?>"));
        </script>
    """)

    assert references == (
        javascript.TemplateEventReference(
            "document",
            "checkout:ready",
            "dispatches",
            8,
        ),
        javascript.TemplateEventReference(
            "document",
            "private-content-loaded",
            "listens",
            7,
        ),
        javascript.TemplateEventReference(
            "window",
            "ga4-cart-event",
            "dispatches",
            4,
        ),
        javascript.TemplateEventReference(
            "window",
            "ga4-cart-event",
            "listens",
            3,
        ),
    )
