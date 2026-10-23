from __future__ import annotations

import xml.etree.ElementTree as ET
import importlib
from pathlib import Path

import pytest

from codecrow_plugins import PluginCatalog
from codecrow_plugins import FileArtifact, OutcomeStatus, RepositoryAnalysis, SymbolDefinition


PLUGINS_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN = PluginCatalog.discover(PLUGINS_ROOT).implementation("magento")
_LAYOUT = importlib.import_module(_PLUGIN.__class__.__module__ + ".layout")
merge_layout = _LAYOUT.merge_layout
parse_layout_document = _LAYOUT.parse_layout_document


def _resolve_magento(
    artifacts: dict[str, str],
    symbols: tuple[SymbolDefinition, ...] = (),
):
    plugin = PluginCatalog.discover(PLUGINS_ROOT).implementation("magento")
    session = plugin.start_repository_analysis("effective-layout-test").value
    session.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(artifacts.items())
    ))
    outcome = session.finish(RepositoryAnalysis(symbols=symbols))
    assert outcome.status is OutcomeStatus.HANDLED
    return outcome.value


def _document(path: str, handle: str, content: str):
    return parse_layout_document(
        path=path,
        area="frontend",
        handle=handle,
        content=content,
        root=ET.fromstring(content),
    )


def test_effective_layout_expands_handles_and_applies_mutations_with_provenance():
    default_content = r"""
        <page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
            <head><css src="Vendor_Module::css/base.css" /></head>
            <body>
                <container name="root">
                    <container name="main">
                        <block name="product.info"
                            class="Vendor\Module\Block\Product"
                            template="Vendor_Module::product.phtml" />
                    </container>
                </container>
            </body>
        </page>
    """
    extra_content = """
        <page><body><referenceContainer name="main">
            <block name="promotion" as="promo" after="product.info" />
        </referenceContainer></body></page>
    """
    page_content = r"""
        <page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
              layout="2columns-left">
            <update handle="vendor_extra" />
            <head><remove src="Vendor_Module::css/base.css" /></head>
            <body>
                <referenceBlock name="product.info"
                    template="Vendor_Module::product/custom.phtml">
                    <arguments>
                        <argument name="view_model" xsi:type="object">Vendor\Module\ViewModel\Product</argument>
                    </arguments>
                    <action method="setMode">
                        <argument name="mode" xsi:type="string">compact</argument>
                    </action>
                </referenceBlock>
                <move element="promotion" destination="root" before="-" />
                <referenceContainer name="main" display="false" />
            </body>
        </page>
    """
    documents = {
        "default": (_document("view/frontend/layout/default.xml", "default", default_content),),
        "vendor_extra": (
            _document("view/frontend/layout/vendor_extra.xml", "vendor_extra", extra_content),
        ),
        "vendor_product_view": (
            _document(
                "view/frontend/layout/vendor_product_view.xml",
                "vendor_product_view",
                page_content,
            ),
        ),
    }

    result = merge_layout(
        documents,
        ("default", "vendor_product_view"),
    )

    assert result.expanded_handles == (
        "default",
        "vendor_extra",
        "vendor_product_view",
    )
    assert result.root_layout == "2columns-left"
    assert not result.unresolved_operations
    assert not result.diagnostics

    nodes = {node.name: node for node in result.nodes}
    product = nodes["product.info"]
    assert product.block_class == r"Vendor\Module\Block\Product"
    assert product.template == "Vendor_Module::product/custom.phtml"
    assert product.parent == "main"
    assert product.arguments[0].name == "view_model"
    assert product.arguments[0].value == r"Vendor\Module\ViewModel\Product"
    assert product.actions[0].method == "setMode"
    assert product.actions[0].arguments[0].value == "compact"
    assert {source.path for source in product.provenance} == {
        "view/frontend/layout/default.xml",
        "view/frontend/layout/vendor_product_view.xml",
    }

    promotion = nodes["promotion"]
    assert promotion.parent == "root"
    assert promotion.before == "-"
    assert promotion.order < nodes["main"].order
    assert nodes["main"].display is False

    assert result.assets[0].src == "Vendor_Module::css/base.css"
    assert result.assets[0].removed is True
    assert {source.path for source in result.assets[0].provenance} == {
        "view/frontend/layout/default.xml",
        "view/frontend/layout/vendor_product_view.xml",
    }


def test_layout_updates_are_applied_before_each_physical_documents_instructions():
    first = _document(
        "view/frontend/layout/vendor_page.first.xml",
        "vendor_page",
        '<page><body><block name="item" template="Vendor_Module::a.phtml" />'
        "</body></page>",
    )
    second = _document(
        "view/frontend/layout/vendor_page.second.xml",
        "vendor_page",
        '<page><update handle="vendor_late" /></page>',
    )
    included = _document(
        "view/frontend/layout/vendor_late.xml",
        "vendor_late",
        '<page><body><referenceBlock name="item" '
        'template="Vendor_Module::late.phtml" /></body></page>',
    )

    result = merge_layout(
        {
            "vendor_page": (first, second),
            "vendor_late": (included,),
        },
        ("vendor_page",),
    )

    assert result.nodes[0].template == "Vendor_Module::late.phtml"
    assert result.expanded_handles == ("vendor_late", "vendor_page")


def test_layout_forward_reference_survives_first_declaration_but_duplicate_resets():
    reference = _document(
        "view/frontend/layout/a_reference.xml",
        "vendor_page",
        '<page><body><referenceBlock name="item" '
        'template="Vendor_Module::referenced.phtml" /></body></page>',
    )
    declaration = _document(
        "view/frontend/layout/b_declaration.xml",
        "vendor_page",
        r'<page><body><block name="item" class="Vendor\Module\Block\Item">'
        '<block name="stale.child" /></block></body></page>',
    )
    replacement = _document(
        "view/frontend/layout/c_replacement.xml",
        "vendor_page",
        r'<page><body><block name="item" class="Vendor\Module\Block\Replacement" '
        'template="Vendor_Module::replacement.phtml" /></body></page>',
    )

    forward = merge_layout(
        {"vendor_page": (reference, declaration)},
        ("vendor_page",),
    )
    item = next(node for node in forward.nodes if node.name == "item")
    assert item.block_class == r"Vendor\Module\Block\Item"
    assert item.template == "Vendor_Module::referenced.phtml"

    duplicate = merge_layout(
        {"vendor_page": (reference, declaration, replacement)},
        ("vendor_page",),
    )
    nodes = {node.name: node for node in duplicate.nodes}
    assert nodes["item"].block_class == r"Vendor\Module\Block\Replacement"
    assert nodes["item"].template == "Vendor_Module::replacement.phtml"
    assert "stale.child" not in nodes


def test_layout_replacement_clears_stale_argument_subtrees():
    base = _document(
        "view/frontend/layout/base.xml",
        "vendor_page",
        r'''<page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>
            <block name="item"><arguments>
                <argument name="config" xsi:type="array">
                    <item name="helper" xsi:type="object">Vendor\Module\Helper\Old</item>
                </argument>
            </arguments></block>
        </body></page>''',
    )
    override = _document(
        "view/frontend/layout/override.xml",
        "vendor_page",
        r'''<page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>
            <referenceBlock name="item"><arguments>
                <argument name="config" xsi:type="object">Vendor\Module\Config\NewConfig</argument>
            </arguments></referenceBlock>
        </body></page>''',
    )

    result = merge_layout(
        {"vendor_page": (base, override)},
        ("vendor_page",),
    )

    assert tuple(
        (argument.name, argument.value)
        for argument in result.nodes[0].arguments
    ) == (("config", r"Vendor\Module\Config\NewConfig"),)


def test_layout_parser_retains_conditions_template_actions_and_generated_blocks():
    content = r'''<page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>
        <block name="conditional" ifconfig="vendor/feature/enabled"
               aclResource="Vendor_Module::feature"
               template="Vendor_Module::base.phtml">
            <action method="setTemplate" ifconfig="vendor/template/alternate">
                <argument name="template" xsi:type="string">Vendor_Module::alternate.phtml</argument>
            </action>
            <action method="setMode" ifconfig="vendor/mode/enabled">
                <argument name="mode" xsi:type="string">compact</argument>
            </action>
        </block>
        <block name="unconditional" template="Vendor_Module::base.phtml">
            <action method="setTemplate" ifconfig="vendor/template/ignored">
                <argument name="template" xsi:type="string">Vendor_Module::ignored.phtml</argument>
            </action>
            <action method="setTemplate">
                <argument name="template" xsi:type="string">Vendor_Module::final.phtml</argument>
            </action>
        </block>
        <block name="candidate.only">
            <action method="setTemplate" ifconfig="vendor/template/candidate">
                <argument name="template" xsi:type="string">Vendor_Module::candidate.phtml</argument>
            </action>
        </block>
        <block template="Vendor_Module::anonymous.phtml" />
    </body></page>'''

    result = merge_layout(
        {"default": (_document("view/frontend/layout/default.xml", "default", content),)},
        ("default",),
    )
    nodes = {node.name: node for node in result.nodes}
    conditional = nodes["conditional"]
    conditional_attributes = dict(conditional.attributes)

    assert conditional.block_class == r"Magento\Framework\View\Element\Template"
    assert conditional.template == "Vendor_Module::base.phtml"
    assert conditional_attributes["ifconfig"] == "vendor/feature/enabled"
    assert conditional_attributes["aclResource"] == "Vendor_Module::feature"
    assert conditional_attributes["conditionalTemplateCandidate"] == (
        "Vendor_Module::alternate.phtml"
    )
    assert conditional.actions[1].ifconfig == "vendor/mode/enabled"
    assert nodes["unconditional"].template == "Vendor_Module::final.phtml"
    assert "conditionalTemplateCandidate" not in dict(
        nodes["unconditional"].attributes
    )
    assert dict(nodes["candidate.only"].attributes)[
        "conditionalTemplateCandidate"
    ] == "Vendor_Module::candidate.phtml"
    anonymous = next(
        node for node in result.nodes if node.name.startswith("@anonymous:block:")
    )
    assert dict(anonymous.attributes)["generatedName"] == "true"


def test_effective_layout_keeps_partial_result_on_update_cycles_and_unresolved_mutations():
    first = """
        <page><update handle="second" /><body>
            <container name="root" />
            <referenceBlock name="missing" template="Vendor_Module::missing.phtml" />
        </body></page>
    """
    second = """
        <page><update handle="first" /><body>
            <move element="missing" destination="root" />
        </body></page>
    """

    result = merge_layout(
        {
            "first": (_document("view/frontend/layout/first.xml", "first", first),),
            "second": (_document("view/frontend/layout/second.xml", "second", second),),
        },
        ("first",),
    )

    assert [node.name for node in result.nodes] == ["root"]
    assert {item.kind for item in result.unresolved_operations} == {
        "move",
        "reference",
    }
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "magento-layout-update-cycle"
    ]


def test_effective_layout_reports_order_cycles_without_dropping_nodes():
    content = """
        <page><body><container name="root">
            <block name="first" after="second" />
            <block name="second" after="first" />
        </container></body></page>
    """

    result = merge_layout(
        {"default": (_document("view/frontend/layout/default.xml", "default", content),)},
        ("default",),
    )

    assert {node.name for node in result.nodes} == {"root", "first", "second"}
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "magento-layout-order-cycle"
    ]


def test_layout_after_wins_when_one_instruction_also_declares_before():
    content = """
        <page><body><container name="root">
            <block name="first" />
            <block name="second" />
            <block name="positioned" before="first" after="second" />
        </container></body></page>
    """

    result = merge_layout(
        {"default": (_document("view/frontend/layout/default.xml", "default", content),)},
        ("default",),
    )
    nodes = {node.name: node for node in result.nodes}

    assert nodes["second"].order < nodes["positioned"].order
    assert not result.diagnostics


def test_effective_layout_reports_parent_cycles_without_dropping_nodes():
    content = """
        <page><body><container name="first">
            <container name="second" />
        </container><move element="first" destination="second" /></body></page>
    """

    result = merge_layout(
        {"default": (_document("view/frontend/layout/default.xml", "default", content),)},
        ("default",),
    )

    assert {node.name for node in result.nodes} == {"first", "second"}
    assert "magento-layout-parent-cycle" in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_layout_parser_retains_ui_component_and_nested_array_arguments():
    content = r"""
        <page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>
            <uiComponent name="checkout">
                <arguments>
                    <argument name="jsLayout" xsi:type="array">
                        <item name="components" xsi:type="array">
                            <item name="summary" xsi:type="object">Vendor\Module\Ui\Summary</item>
                        </item>
                    </argument>
                </arguments>
            </uiComponent>
        </body></page>
    """

    document = _document(
        "view/frontend/layout/checkout_index_index.xml",
        "checkout_index_index",
        content,
    )
    operation = next(
        operation
        for operation in document.operations
        if operation.kind == "declare"
    )

    assert operation.node_kind == "uiComponent"
    assert operation.arguments[0].name == "jsLayout.components.summary"
    assert operation.arguments[0].value_type == "object"
    assert operation.arguments[0].value == r"Vendor\Module\Ui\Summary"


def test_repository_emits_effective_page_layout_template_action_and_ui_facts():
    default_layout = "app/code/Vendor/Module/view/frontend/layout/default.xml"
    page_layout = (
        "app/code/Vendor/Module/view/frontend/layout/vendor_product_view.xml"
    )
    page_wireframe = (
        "app/code/Vendor/Module/view/frontend/page_layout/two-columns.xml"
    )
    parent_wireframe = (
        "app/code/Vendor/Module/view/frontend/page_layout/one-column.xml"
    )
    template = (
        "app/code/Vendor/Module/view/frontend/templates/product/custom.phtml"
    )
    ui_component = (
        "app/code/Vendor/Module/view/frontend/ui_component/checkout.xml"
    )
    analysis = _resolve_magento(
        artifacts={
            "app/code/Vendor/Module/etc/module.xml": (
                '<config><module name="Vendor_Module" /></config>'
            ),
            "app/code/Vendor/Module/view/frontend/layouts.xml": """
                <layouts><layout id="two-columns">
                    <label translate="true">Two columns</label>
                </layout></layouts>
            """,
            parent_wireframe: """
                <layout><container name="root">
                    <container name="page.wrapper" />
                </container></layout>
            """,
            page_wireframe: """
                <layout><update handle="one-column" />
                    <referenceContainer name="page.wrapper">
                        <container name="columns" />
                    </referenceContainer>
                </layout>
            """,
            default_layout: r"""
                <page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
                    <head><css src="Vendor_Module::css/base.css" /></head>
                    <body><referenceContainer name="columns">
                        <block name="product.info"
                            class="Vendor\Module\Block\Product"
                            template="Vendor_Module::product/base.phtml" />
                        <uiComponent name="checkout" />
                    </referenceContainer></body>
                </page>
            """,
            page_layout: r"""
                <page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                      layout="two-columns">
                    <head><remove src="Vendor_Module::css/base.css" /></head>
                    <body>
                        <referenceBlock name="product.info"
                            template="Vendor_Module::product/custom.phtml">
                            <arguments>
                                <argument name="view_model" xsi:type="object">Vendor\Module\ViewModel\Product</argument>
                            </arguments>
                            <action method="setMode" />
                        </referenceBlock>
                        <move element="checkout" destination="root" before="-" />
                    </body>
                </page>
            """,
            "app/code/Vendor/Module/view/frontend/templates/product/base.phtml": (
                "<div>Base</div>"
            ),
            template: "<div>Custom</div>",
            ui_component: """
                <form><dataSource name="checkout_data_source"
                    class="Vendor\\Module\\Ui\\DataProvider" /></form>
            """,
        },
        symbols=tuple(sorted((
            SymbolDefinition(
                r"Vendor\Module\Block\Product",
                "class",
                "app/code/Vendor/Module/Block/Product.php",
                methods=("setMode",),
            ),
            SymbolDefinition(
                r"Vendor\Module\ViewModel\Product",
                "class",
                "app/code/Vendor/Module/ViewModel/Product.php",
            ),
        ))),
    )
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )

    selection = next(
        fact
        for fact in facts
        if fact.kind == "magento-page-layout-selection"
        and fact.source == "vendor_product_view"
    )
    assert selection.target == "two-columns"
    assert dict(selection.attributes)["resolved"] == "true"
    assert page_wireframe in selection.related_paths
    assert any(
        fact.kind == "magento-page-layout-declaration"
        and fact.source == "two-columns"
        and dict(fact.attributes)["label"] == "Two columns"
        for fact in facts
    )
    effective_layout = next(
        fact
        for fact in facts
        if fact.kind == "magento-effective-layout"
        and fact.source == "vendor_product_view"
    )
    assert "page_layout:one-column" in effective_layout.target.split(",")
    assert parent_wireframe in effective_layout.related_paths

    product = next(
        fact
        for fact in facts
        if fact.kind == "magento-layout-effective-node"
        and fact.target == "product.info"
        and dict(fact.attributes).get("handle") == "vendor_product_view"
    )
    assert dict(product.attributes)["blockClass"] == r"Vendor\Module\Block\Product"
    assert dict(product.attributes)["template"] == (
        "Vendor_Module::product/custom.phtml"
    )
    assert {default_layout, page_layout} <= {
        product.path,
        *product.related_paths,
    }
    binding = next(
        fact
        for fact in facts
        if fact.kind == "magento-template-effective-block-binding"
        and fact.path == template
    )
    assert binding.target == r"Vendor\Module\Block\Product"
    assert {default_layout, page_layout} <= set(binding.related_paths)
    assert any(
        fact.kind == "magento-template-effective-view-model-binding"
        and fact.path == template
        and fact.target == r"Vendor\Module\ViewModel\Product"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-layout-effective-action"
        and fact.target == r"Vendor\Module\Block\Product::setMode"
        for fact in facts
    )
    activation = next(
        fact
        for fact in facts
        if fact.kind == "magento-layout-ui-component-activation"
        and fact.target == "checkout"
    )
    assert ui_component in activation.related_paths
    assert any(
        fact.kind == "magento-layout-effective-asset"
        and fact.target == "Vendor_Module::css/base.css"
        and fact.relation == "removes-asset"
        for fact in facts
    )


def test_theme_reference_block_keeps_module_class_for_effective_template_binding():
    module_layout = (
        "app/code/Vendor/Module/view/frontend/layout/catalog_product_view.xml"
    )
    theme_layout = (
        "app/design/frontend/Acme/custom/Vendor_Module/layout/"
        "catalog_product_view.xml"
    )
    theme_template = (
        "app/design/frontend/Acme/custom/Vendor_Module/templates/product.phtml"
    )
    analysis = _resolve_magento(
        artifacts={
            "app/code/Vendor/Module/etc/module.xml": (
                '<config><module name="Vendor_Module" /></config>'
            ),
            module_layout: r"""
                <page><body><block name="product.info"
                    class="Vendor\Module\Block\Product"
                    template="Vendor_Module::product.phtml" /></body></page>
            """,
            "app/code/Vendor/Module/view/frontend/templates/product.phtml": (
                "<div>Module</div>"
            ),
            "app/design/frontend/Acme/custom/theme.xml": (
                "<theme><title>Custom</title></theme>"
            ),
            "app/design/frontend/Acme/custom/registration.php": """<?php
                ComponentRegistrar::register(
                    ComponentRegistrar::THEME,
                    'frontend/Acme/custom',
                    __DIR__
                );
            """,
            theme_layout: """
                <page><body><referenceBlock name="product.info"
                    template="Vendor_Module::product.phtml" /></body></page>
            """,
            theme_template: "<div>Theme</div>",
        },
        symbols=(SymbolDefinition(
            r"Vendor\Module\Block\Product",
            "class",
            "app/code/Vendor/Module/Block/Product.php",
        ),),
    )
    binding = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-template-effective-block-binding"
        and fact.path == theme_template
        and dict(fact.attributes).get("themeSelection") == "Acme/custom"
    )

    assert binding.target == r"Vendor\Module\Block\Product"
    assert module_layout in binding.related_paths
    assert theme_layout in binding.related_paths


def test_standalone_theme_resolves_its_own_effective_template_without_module_records():
    template = "Magento_Theme/templates/banner.phtml"
    analysis = _resolve_magento(artifacts={
        "composer.json": '{"type":"magento2-theme"}',
        "registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Acme/standalone',
                __DIR__
            );
        """,
        "theme.xml": "<theme><title>Standalone</title></theme>",
        "Magento_Theme/layout/default.xml": r"""
            <page><body><block name="standalone.banner"
                class="Magento\Theme\Block\Html"
                template="Magento_Theme::banner.phtml" /></body></page>
        """,
        template: "<div>Banner</div>",
    })

    effective_template = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-layout-effective-template"
        and fact.target == template
    )
    assert dict(effective_template.attributes)["themeSelection"] == (
        "Acme/standalone"
    )


def test_real_php_analysis_links_phtml_call_to_inherited_effective_block_method():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_php")
    template = "app/code/Vendor/Module/view/frontend/templates/product.phtml"
    artifacts = {
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        "app/code/Vendor/Module/view/frontend/layout/catalog_product_view.xml": r"""
            <page><body><block name="product.info"
                class="Vendor\Module\Block\Product"
                template="Vendor_Module::product.phtml" /></body></page>
        """,
        "app/code/Vendor/Module/Block/BaseProduct.php": """<?php
            namespace Vendor\\Module\\Block;
            class BaseProduct {
                public function getCartId(): string { return 'cart'; }
            }
        """,
        "app/code/Vendor/Module/Block/Product.php": """<?php
            namespace Vendor\\Module\\Block;
            class Product extends BaseProduct {}
        """,
        template: "<?= $block->getCartId() ?>",
    }
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    php_session = catalog.implementation("php").start_repository_analysis(
        "effective-layout-php-handoff"
    ).value
    php_session.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(artifacts.items())
        if path.endswith((".php", ".phtml"))
    ))
    php_outcome = php_session.finish(RepositoryAnalysis())
    assert php_outcome.status is OutcomeStatus.HANDLED

    magento_session = catalog.implementation(
        "magento"
    ).start_repository_analysis("effective-layout-php-handoff").value
    magento_session.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(artifacts.items())
    ))
    magento_outcome = magento_session.finish(php_outcome.value)
    assert magento_outcome.status is OutcomeStatus.HANDLED

    method_call = next(
        fact
        for packet in magento_outcome.value.packets
        for fact in packet.facts
        if fact.kind == "magento-template-effective-block-method-call"
    )
    assert method_call.path == template
    assert method_call.target == (
        r"Vendor\Module\Block\BaseProduct::getCartId"
    )
    assert "app/code/Vendor/Module/Block/BaseProduct.php" in (
        method_call.related_paths
    )


def test_theme_page_layout_base_override_suppresses_module_wireframe():
    module_wireframe = (
        "app/code/Vendor/Module/view/frontend/page_layout/custom.xml"
    )
    theme_override = (
        "app/design/frontend/Acme/custom/Vendor_Module/page_layout/"
        "override/base/custom.xml"
    )
    analysis = _resolve_magento(artifacts={
        "app/etc/config.php": (
            "<?php return ['modules' => ['Vendor_Module' => 1]];"
        ),
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        "app/code/Vendor/Module/view/frontend/layouts.xml": (
            '<layouts><layout id="custom"><label>Custom</label></layout></layouts>'
        ),
        module_wireframe: """
            <layout><container name="root">
                <container name="module.only" />
            </container></layout>
        """,
        "app/code/Vendor/Module/view/frontend/layout/vendor_page.xml": (
            '<page layout="custom"><body /></page>'
        ),
        "app/design/frontend/Acme/custom/theme.xml": (
            "<theme><title>Custom</title></theme>"
        ),
        "app/design/frontend/Acme/custom/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Acme/custom',
                __DIR__
            );
        """,
        theme_override: """
            <layout><container name="root">
                <container name="theme.replacement" />
            </container></layout>
        """,
    })
    selected_nodes = {
        fact.target
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-layout-effective-node"
        and dict(fact.attributes).get("handle") == "vendor_page"
        and dict(fact.attributes).get("themeSelection") == "Acme/custom"
    }

    assert "theme.replacement" in selected_nodes
    assert "module.only" not in selected_nodes
    selected_layout = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-effective-layout"
        and fact.source == "vendor_page"
        and dict(fact.attributes).get("themeSelection") == "Acme/custom"
    )
    assert theme_override in {
        selected_layout.path,
        *selected_layout.related_paths,
    }
    assert module_wireframe not in {
        selected_layout.path,
        *selected_layout.related_paths,
    }


def test_page_layout_snapshot_deletion_recomputes_selection_as_unresolved():
    page_layout = (
        "app/code/Vendor/Module/view/frontend/page_layout/custom.xml"
    )
    base = _resolve_magento(artifacts={
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        "app/code/Vendor/Module/view/frontend/layouts.xml": (
            '<layouts><layout id="custom"><label>Custom</label></layout></layouts>'
        ),
        page_layout: '<layout><container name="root" /></layout>',
        "app/code/Vendor/Module/view/frontend/layout/vendor_page.xml": (
            '<page layout="custom"><body /></page>'
        ),
    })
    plugin = PluginCatalog.discover(PLUGINS_ROOT).implementation("magento")
    restored = plugin.restore_repository_analysis(
        "effective-layout-delete",
        base.snapshots,
    )
    assert restored.status is OutcomeStatus.HANDLED
    restored.value.ingest((FileArtifact(page_layout, "", deleted=True),))

    outcome = restored.value.finish(RepositoryAnalysis())

    assert outcome.status is OutcomeStatus.HANDLED
    assert all(
        page_layout not in packet.paths
        for packet in outcome.value.packets
    )
    selection = next(
        fact
        for packet in outcome.value.packets
        for fact in packet.facts
        if fact.kind == "magento-page-layout-selection"
        and fact.source == "vendor_page"
    )
    assert dict(selection.attributes)["resolved"] == "false"


def test_invalid_php_template_call_metadata_is_recoverable():
    template = "app/code/Vendor/Module/view/frontend/templates/item.phtml"
    analysis = _resolve_magento(
        artifacts={
            "app/code/Vendor/Module/etc/module.xml": (
                '<config><module name="Vendor_Module" /></config>'
            ),
            "app/code/Vendor/Module/view/frontend/layout/default.xml": r"""
                <page><body><block name="item"
                    class="Vendor\Module\Block\Item"
                    template="Vendor_Module::item.phtml" /></body></page>
            """,
            template: "<?= $block->render() ?>",
        },
        symbols=(
            SymbolDefinition(
                r"Vendor\Module\Block\Item",
                "class",
                "app/code/Vendor/Module/Block/Item.php",
                methods=("render",),
            ),
            SymbolDefinition(
                f"template:{template}",
                "template",
                template,
                attributes=((
                    "php-template-instance-call-reference:0000",
                    "{not-json}",
                ),),
            ),
        ),
    )

    assert any(
        fact.kind == "magento-template-effective-block-binding"
        for packet in analysis.packets
        for fact in packet.facts
    )
    assert (
        "magento-invalid-php-template-call-metadata",
        template,
        True,
    ) in {
        (item.code, item.path, item.recoverable)
        for item in analysis.diagnostics
    }


def test_removed_layout_ancestor_suppresses_template_activation_and_binding():
    template = "app/code/Vendor/Module/view/frontend/templates/item.phtml"
    analysis = _resolve_magento(
        artifacts={
            "app/code/Vendor/Module/etc/module.xml": (
                '<config><module name="Vendor_Module" /></config>'
            ),
            "app/code/Vendor/Module/view/frontend/layout/default.xml": r"""
                <page><body>
                    <container name="root">
                        <block name="item"
                            class="Vendor\Module\Block\Item"
                            template="Vendor_Module::item.phtml" />
                    </container>
                    <referenceContainer name="root" remove="true" />
                </body></page>
            """,
            template: (
                '<div data-mage-init=\'{"Vendor_Module/js/item": {}}\'></div>'
            ),
            "app/code/Vendor/Module/view/frontend/web/js/item.js": (
                "define([], function () {});"
            ),
        },
        symbols=(SymbolDefinition(
            r"Vendor\Module\Block\Item",
            "class",
            "app/code/Vendor/Module/Block/Item.php",
        ),),
    )
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )
    item = next(
        fact
        for fact in facts
        if fact.kind == "magento-layout-effective-node"
        and fact.target == "item"
    )

    assert dict(item.attributes)["renderingState"] == "ancestor-removed"
    assert any(
        fact.kind == "magento-layout-effective-template"
        and fact.source == "item"
        and fact.relation == "declares-inactive-template"
        for fact in facts
    )
    assert not any(
        fact.kind in {
            "magento-template-effective-block-binding",
            "magento-frontend-init",
        }
        and fact.path == template
        for fact in facts
    )


def test_missing_vendor_parent_keeps_dependencies_as_conditional_uncertainty():
    template = "app/code/Vendor/Module/view/frontend/templates/item.phtml"
    analysis = _resolve_magento(
        artifacts={
            "app/code/Vendor/Module/etc/module.xml": (
                '<config><module name="Vendor_Module" /></config>'
            ),
            "app/code/Vendor/Module/view/frontend/layout/default.xml": r'''
                <page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>
                    <referenceContainer name="content">
                        <block name="item" class="Vendor\Module\Block\Item"
                               template="Vendor_Module::item.phtml">
                            <arguments><argument name="helper" xsi:type="object">Vendor\Module\Helper\Item</argument></arguments>
                            <action method="setMode" />
                        </block>
                    </referenceContainer>
                </body></page>
            ''',
            template: (
                '<div data-mage-init=\'{"Vendor_Module/js/item": {}}\'></div>'
            ),
            "app/code/Vendor/Module/view/frontend/web/js/item.js": (
                "define([], function () {});"
            ),
        },
        symbols=(SymbolDefinition(
            r"Vendor\Module\Block\Item",
            "class",
            "app/code/Vendor/Module/Block/Item.php",
            methods=("setMode",),
        ),),
    )
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )
    node = next(
        fact
        for fact in facts
        if fact.kind == "magento-layout-effective-node"
        and fact.target == "item"
    )

    assert dict(node.attributes)["renderingState"] == "unresolved-parent"
    assert dict(node.attributes)["semanticRole"] == "uncertainty"
    assert any(
        fact.kind == "magento-layout-effective-template"
        and fact.path.endswith("default.xml")
        and fact.relation == "conditionally-renders-template"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-template-effective-block-binding"
        and fact.path == template
        and fact.relation == "conditionally-rendered-by-effective-block"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-layout-effective-object-argument"
        and fact.relation == "conditionally-receives-layout-object"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-layout-effective-action"
        and fact.relation == "conditionally-calls-layout-action"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-frontend-init"
        and fact.path == template
        and fact.relation == "conditionally-initializes-component"
        and dict(fact.attributes)["activationCertainty"] == "conditional"
        for fact in facts
    )


def test_generic_layout_handle_does_not_inherit_default_page_configuration():
    analysis = _resolve_magento(artifacts={
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        "app/code/Vendor/Module/view/frontend/layout/default.xml": (
            '<page><body><block name="default.only" /></body></page>'
        ),
        "app/code/Vendor/Module/view/frontend/layout/vendor_ajax.xml": (
            '<layout><container name="ajax.root" /></layout>'
        ),
    })
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )
    ajax_nodes = {
        fact.target
        for fact in facts
        if fact.kind == "magento-layout-effective-node"
        and dict(fact.attributes).get("handle") == "vendor_ajax"
    }
    composition = next(
        fact
        for fact in facts
        if fact.kind == "magento-effective-layout"
        and fact.source == "vendor_ajax"
    )

    assert ajax_nodes == {"ajax.root"}
    assert composition.target == "vendor_ajax"
    assert dict(composition.attributes)["documentKind"] == "generic-layout"


def test_default_block_class_is_resolved_through_effective_area_di():
    template = "app/code/Vendor/Module/view/frontend/templates/item.phtml"
    preferred_class = r"Vendor\Module\Block\Preferred"
    analysis = _resolve_magento(
        artifacts={
            "app/code/Vendor/Module/etc/module.xml": (
                '<config><module name="Vendor_Module" /></config>'
            ),
            "app/code/Vendor/Module/etc/frontend/di.xml": r'''
                <config><preference
                    for="Magento\Framework\View\Element\Template"
                    type="Vendor\Module\Block\Preferred" /></config>
            ''',
            "app/code/Vendor/Module/view/frontend/layout/default.xml": (
                '<page><body><block name="item" '
                'template="Vendor_Module::item.phtml" /></body></page>'
            ),
            template: "<?= $block->render() ?>",
        },
        symbols=(
            SymbolDefinition(
                preferred_class,
                "class",
                "app/code/Vendor/Module/Block/Preferred.php",
                methods=("render",),
            ),
            SymbolDefinition(
                f"template:{template}",
                "template",
                template,
                attributes=((
                    "php-template-instance-call-reference:0000",
                    '{"line":1,"literalStringArguments":{},'
                    '"method":"render","receiver":"block"}',
                ),),
            ),
        ),
    )
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )
    block_class = next(
        fact
        for fact in facts
        if fact.kind == "magento-layout-effective-block-class"
        and fact.source == "item"
    )

    assert block_class.target == preferred_class
    assert dict(block_class.attributes)["blockClassDefaulted"] == "true"
    assert dict(block_class.attributes)["configuredBlockClass"] == (
        r"Magento\Framework\View\Element\Template"
    )
    assert any(
        fact.kind == "magento-template-effective-block-binding"
        and fact.target == preferred_class
        and fact.path == template
        for fact in facts
    )
    assert any(
        fact.kind == "magento-template-effective-block-method-call"
        and fact.target == preferred_class + "::render"
        for fact in facts
    )


def test_conditional_layout_dependencies_are_not_emitted_as_unconditional_runtime_facts():
    base_template = (
        "app/code/Vendor/Module/view/frontend/templates/base.phtml"
    )
    alternate_template = (
        "app/code/Vendor/Module/view/frontend/templates/alternate.phtml"
    )
    analysis = _resolve_magento(
        artifacts={
            "app/code/Vendor/Module/etc/module.xml": (
                '<config><module name="Vendor_Module" /></config>'
            ),
            "app/code/Vendor/Module/view/frontend/layout/default.xml": r'''
                <page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>
                    <block name="conditional"
                        class="Vendor\Module\Block\Conditional"
                        template="Vendor_Module::base.phtml"
                        ifconfig="vendor/feature/enabled"
                        aclResource="Vendor_Module::feature">
                        <arguments><argument name="view_model" xsi:type="object">Vendor\Module\ViewModel\Conditional</argument></arguments>
                        <action method="setTemplate" ifconfig="vendor/template/alternate">
                            <argument name="template" xsi:type="string">Vendor_Module::alternate.phtml</argument>
                        </action>
                        <action method="setMode" ifconfig="vendor/mode/enabled" />
                    </block>
                    <block name="candidate.only">
                        <action method="setTemplate" ifconfig="vendor/template/candidate">
                            <argument name="template" xsi:type="string">Vendor_Module::candidate.phtml</argument>
                        </action>
                    </block>
                </body></page>
            ''',
            base_template: (
                '<div data-mage-init=\'{"Vendor_Module/js/base": {}}\'></div>'
            ),
            alternate_template: "<div>Alternate</div>",
            "app/code/Vendor/Module/view/frontend/templates/candidate.phtml": (
                "<div>Candidate</div>"
            ),
            "app/code/Vendor/Module/view/frontend/web/js/base.js": (
                "define([], function () {});"
            ),
        },
        symbols=(
            SymbolDefinition(
                r"Vendor\Module\Block\Conditional",
                "class",
                "app/code/Vendor/Module/Block/Conditional.php",
                methods=("setMode", "setTemplate"),
            ),
            SymbolDefinition(
                r"Vendor\Module\ViewModel\Conditional",
                "class",
                "app/code/Vendor/Module/ViewModel/Conditional.php",
                parents=(
                    r"Magento\Framework\View\Element\Block\ArgumentInterface",
                ),
            ),
        ),
    )
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )
    node = next(
        fact
        for fact in facts
        if fact.kind == "magento-layout-effective-node"
        and fact.target == "conditional"
    )

    assert dict(node.attributes)["renderingState"] == (
        "conditional-ifconfig-acl"
    )
    assert any(
        fact.kind == "magento-layout-effective-block-class"
        and fact.relation == "conditionally-uses-block-class"
        for fact in facts
    )
    assert {
        fact.target
        for fact in facts
        if fact.kind == "magento-layout-effective-template"
        and fact.relation == "conditionally-renders-template"
    } == {
        base_template,
        alternate_template,
        "app/code/Vendor/Module/view/frontend/templates/candidate.phtml",
    }
    assert any(
        fact.kind == "magento-layout-effective-object-argument"
        and fact.relation == "conditionally-receives-layout-object"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-layout-effective-action"
        and fact.relation == "conditionally-calls-layout-action"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-template-effective-block-binding"
        and fact.path == base_template
        and fact.relation == "conditionally-rendered-by-effective-block"
        for fact in facts
    )
    assert not any(
        fact.kind == "magento-template-effective-block-binding"
        and fact.path == base_template
        and fact.relation == "rendered-by-effective-block"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-frontend-init"
        and fact.path == base_template
        and fact.relation == "conditionally-initializes-component"
        for fact in facts
    )


def test_magic_block_getter_links_exact_layout_argument_and_retains_unresolved_call():
    template = "app/code/Vendor/Module/view/frontend/templates/item.phtml"
    analysis = _resolve_magento(
        artifacts={
            "app/code/Vendor/Module/etc/module.xml": (
                '<config><module name="Vendor_Module" /></config>'
            ),
            "app/code/Vendor/Module/view/frontend/layout/default.xml": r'''
                <page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>
                    <block name="item" class="Vendor\Module\Block\Missing"
                           template="Vendor_Module::item.phtml">
                        <arguments><argument name="view_model" xsi:type="object">Vendor\Module\ViewModel\Item</argument></arguments>
                    </block>
                </body></page>
            ''',
            template: "<?= $block->getViewModel() ?>",
        },
        symbols=(SymbolDefinition(
            f"template:{template}",
            "template",
            template,
            attributes=((
                "php-template-instance-call-reference:0000",
                '{"line":1,"literalStringArguments":{},'
                '"method":"getViewModel","receiver":"block"}',
            ),),
        ),),
    )
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )

    assert any(
        fact.kind == "magento-template-effective-layout-argument-read"
        and dict(fact.attributes)["argument"] == "view_model"
        for fact in facts
    )
    assert any(
        fact.kind == (
            "magento-template-effective-block-method-call-unresolved"
        )
        and fact.target == r"Vendor\Module\Block\Missing::getViewModel"
        for fact in facts
    )


def test_malformed_selected_page_layout_is_reported_unresolved():
    page_layout = (
        "app/code/Vendor/Module/view/frontend/page_layout/custom.xml"
    )
    analysis = _resolve_magento(artifacts={
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        page_layout: "<layout><container",
        "app/code/Vendor/Module/view/frontend/layout/vendor_page.xml": (
            '<page layout="custom"><body /></page>'
        ),
    })
    selection = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-page-layout-selection"
        and fact.source == "vendor_page"
    )

    assert dict(selection.attributes)["resolved"] == "false"
    assert any(
        diagnostic.code == "magento-invalid-xml"
        and diagnostic.path == page_layout
        for diagnostic in analysis.diagnostics
    )


def test_layout_snapshot_deletion_removes_stale_effective_nodes():
    layout = "app/code/Vendor/Module/view/frontend/layout/default.xml"
    base = _resolve_magento(artifacts={
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        layout: '<page><body><block name="stale" /></body></page>',
    })
    plugin = PluginCatalog.discover(PLUGINS_ROOT).implementation("magento")
    restored = plugin.restore_repository_analysis(
        "effective-layout-delete-node",
        base.snapshots,
    )
    assert restored.status is OutcomeStatus.HANDLED
    restored.value.ingest((FileArtifact(layout, "", deleted=True),))

    outcome = restored.value.finish(RepositoryAnalysis())

    assert outcome.status is OutcomeStatus.HANDLED
    assert not any(
        fact.kind == "magento-layout-effective-node"
        and fact.target == "stale"
        for packet in outcome.value.packets
        for fact in packet.facts
    )


def test_disabled_module_ui_component_is_not_attached_to_layout_activation():
    disabled_ui = (
        "app/code/Vendor/Disabled/view/frontend/ui_component/checkout.xml"
    )
    analysis = _resolve_magento(artifacts={
        "app/etc/config.php": """<?php return ['modules' => [
            'Vendor_Active' => 1,
            'Vendor_Disabled' => 0,
        ]];""",
        "app/code/Vendor/Active/etc/module.xml": (
            '<config><module name="Vendor_Active" /></config>'
        ),
        "app/code/Vendor/Disabled/etc/module.xml": (
            '<config><module name="Vendor_Disabled" /></config>'
        ),
        "app/code/Vendor/Active/view/frontend/layout/default.xml": (
            '<page><body><uiComponent name="checkout" /></body></page>'
        ),
        disabled_ui: "<form />",
    })
    activation = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-layout-ui-component-activation"
    )

    assert dict(activation.attributes)["resolved"] == "false"
    assert disabled_ui not in activation.related_paths
    assert all(disabled_ui not in packet.paths for packet in analysis.packets)


def test_page_handle_packets_emit_only_deltas_from_the_default_tree():
    default_layout = (
        "app/code/Vendor/Module/view/frontend/layout/default.xml"
    )
    artifacts = {
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        default_layout: (
            "<page><body>"
            + "".join(
                f'<container name="default.{position}" />'
                for position in range(40)
            )
            + "</body></page>"
        ),
        **{
            (
                "app/code/Vendor/Module/view/frontend/layout/"
                f"vendor_page_{position:02d}.xml"
            ): "<page><body /></page>"
            for position in range(12)
        },
    }
    analysis = _resolve_magento(artifacts=artifacts)
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )
    effective_nodes = tuple(
        fact
        for fact in facts
        if fact.kind == "magento-layout-effective-node"
    )
    compositions = {
        fact.source: dict(fact.attributes)["emissionMode"]
        for fact in facts
        if fact.kind == "magento-effective-layout"
    }

    assert len(effective_nodes) == 40
    assert {
        dict(fact.attributes)["handle"] for fact in effective_nodes
    } == {"default"}
    assert compositions["default"] == "full"
    assert all(
        compositions[f"vendor_page_{position:02d}"]
        == "delta-from-default"
        for position in range(12)
    )


def test_theme_layout_override_deletion_restores_module_fallback():
    module_layout = (
        "app/code/Vendor/Module/view/frontend/layout/vendor_page.xml"
    )
    theme_override = (
        "app/design/frontend/Acme/custom/Vendor_Module/layout/"
        "override/base/vendor_page.xml"
    )
    base = _resolve_magento(artifacts={
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        module_layout: (
            '<page><body><container name="module.fallback" /></body></page>'
        ),
        "app/design/frontend/Acme/custom/theme.xml": (
            "<theme><title>Custom</title></theme>"
        ),
        "app/design/frontend/Acme/custom/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Acme/custom',
                __DIR__
            );
        """,
        theme_override: (
            '<page><body><container name="theme.override" /></body></page>'
        ),
    })
    plugin = PluginCatalog.discover(PLUGINS_ROOT).implementation("magento")
    restored = plugin.restore_repository_analysis(
        "theme-layout-override-delete",
        base.snapshots,
    )
    assert restored.status is OutcomeStatus.HANDLED
    restored.value.ingest((FileArtifact(theme_override, "", deleted=True),))

    outcome = restored.value.finish(RepositoryAnalysis())
    selected_nodes = {
        fact.target
        for packet in outcome.value.packets
        for fact in packet.facts
        if fact.kind == "magento-layout-effective-node"
        and dict(fact.attributes).get("handle") == "vendor_page"
        and dict(fact.attributes).get("themeSelection") == "Acme/custom"
    }

    assert outcome.status is OutcomeStatus.HANDLED
    assert selected_nodes == {"module.fallback"}
    assert all(theme_override not in packet.paths for packet in outcome.value.packets)


def test_layouts_xml_snapshot_deletion_removes_stale_declaration():
    layouts_xml = "app/code/Vendor/Module/view/frontend/layouts.xml"
    base = _resolve_magento(artifacts={
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        layouts_xml: (
            '<layouts><layout id="custom"><label>Custom</label></layout>'
            "</layouts>"
        ),
        "app/code/Vendor/Module/view/frontend/page_layout/custom.xml": (
            '<layout><container name="root" /></layout>'
        ),
        "app/code/Vendor/Module/view/frontend/layout/vendor_page.xml": (
            '<page layout="custom"><body /></page>'
        ),
    })
    plugin = PluginCatalog.discover(PLUGINS_ROOT).implementation("magento")
    restored = plugin.restore_repository_analysis(
        "layouts-xml-delete",
        base.snapshots,
    )
    assert restored.status is OutcomeStatus.HANDLED
    restored.value.ingest((FileArtifact(layouts_xml, "", deleted=True),))

    outcome = restored.value.finish(RepositoryAnalysis())

    assert outcome.status is OutcomeStatus.HANDLED
    assert not any(
        fact.kind == "magento-page-layout-declaration"
        and fact.source == "custom"
        for packet in outcome.value.packets
        for fact in packet.facts
    )
    selection = next(
        fact
        for packet in outcome.value.packets
        for fact in packet.facts
        if fact.kind == "magento-page-layout-selection"
    )
    assert dict(selection.attributes)["resolved"] == "true"
    assert all(layouts_xml not in packet.paths for packet in outcome.value.packets)


def test_restored_php_and_magento_snapshots_remove_stale_phtml_method_calls():
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_php")
    template = "app/code/Vendor/Module/view/frontend/templates/item.phtml"
    artifacts = {
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        "app/code/Vendor/Module/view/frontend/layout/default.xml": r'''
            <page><body><block name="item"
                class="Vendor\Module\Block\Item"
                template="Vendor_Module::item.phtml" /></body></page>
        ''',
        "app/code/Vendor/Module/Block/Item.php": r'''<?php
            namespace Vendor\Module\Block;
            class Item { public function render(): string { return ''; } }
        ''',
        template: "<?= $block->render() ?>",
    }
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    php = catalog.implementation("php")
    php_session = php.start_repository_analysis(
        "combined-template-snapshot"
    ).value
    php_session.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(artifacts.items())
        if path.endswith((".php", ".phtml"))
    ))
    php_base = php_session.finish(RepositoryAnalysis()).value

    magento = catalog.implementation("magento")
    magento_session = magento.start_repository_analysis(
        "combined-template-snapshot"
    ).value
    magento_session.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(artifacts.items())
    ))
    magento_base = magento_session.finish(php_base).value
    assert any(
        fact.kind == "magento-template-effective-block-method-call"
        for packet in magento_base.packets
        for fact in packet.facts
    )

    php_restored = php.restore_repository_analysis(
        "combined-template-snapshot-overlay",
        php_base.snapshots,
    )
    assert php_restored.status is OutcomeStatus.HANDLED
    php_restored.value.ingest((FileArtifact(template, "<div>Static</div>"),))
    php_overlay = php_restored.value.finish(RepositoryAnalysis()).value

    magento_restored = magento.restore_repository_analysis(
        "combined-template-snapshot-overlay",
        magento_base.snapshots,
    )
    assert magento_restored.status is OutcomeStatus.HANDLED
    magento_restored.value.ingest((
        FileArtifact(template, "<div>Static</div>"),
    ))
    overlay = magento_restored.value.finish(php_overlay)

    assert overlay.status is OutcomeStatus.HANDLED
    assert any(
        fact.kind == "magento-template-effective-block-binding"
        and fact.path == template
        for packet in overlay.value.packets
        for fact in packet.facts
    )
    assert not any(
        fact.kind in {
            "magento-template-effective-block-method-call",
            "magento-template-effective-block-method-call-unresolved",
        }
        and fact.path == template
        for packet in overlay.value.packets
        for fact in packet.facts
    )


def test_default_delta_falls_back_to_full_state_and_emits_redeclaration_tombstones():
    template = "app/code/Vendor/Module/view/frontend/templates/item.phtml"
    analysis = _resolve_magento(artifacts={
        "app/code/Vendor/Module/etc/module.xml": (
            '<config><module name="Vendor_Module" /></config>'
        ),
        "app/code/Vendor/Module/view/frontend/layout/default.xml": (
            '<page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>'
            '<container name="root"><block name="item" '
            'template="Vendor_Module::item.phtml" />'
            '<block name="conditional.item"><action method="setTemplate" '
            'ifconfig="vendor/template/candidate"><argument name="template" '
            'xsi:type="string">Vendor_Module::candidate.phtml</argument>'
            '</action></block></container></body></page>'
        ),
        "app/code/Vendor/Module/view/frontend/layout/vendor_removed.xml": (
            '<page><body><referenceContainer name="root" remove="true" />'
            "</body></page>"
        ),
        "app/code/Vendor/Module/view/frontend/layout/vendor_redeclared.xml": (
            '<page><body><container name="root" /></body></page>'
        ),
        template: "<div>Item</div>",
        "app/code/Vendor/Module/view/frontend/templates/candidate.phtml": (
            "<div>Candidate</div>"
        ),
    })
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )
    compositions = {
        fact.source: dict(fact.attributes)["emissionMode"]
        for fact in facts
        if fact.kind == "magento-effective-layout"
    }
    removed_item = next(
        fact
        for fact in facts
        if fact.kind == "magento-layout-effective-node"
        and fact.target == "item"
        and dict(fact.attributes).get("handle") == "vendor_removed"
    )

    assert compositions["vendor_removed"] == "full"
    assert dict(removed_item.attributes)["renderingState"] == (
        "ancestor-removed"
    )
    assert any(
        fact.kind == "magento-layout-effective-template"
        and fact.source == "item"
        and dict(fact.attributes).get("handle") == "vendor_removed"
        and fact.relation == "declares-inactive-template"
        for fact in facts
    )
    assert compositions["vendor_redeclared"] == "full"
    assert any(
        fact.kind == "magento-layout-effective-node"
        and fact.target == "item"
        and fact.relation == "suppresses-inherited-node"
        and dict(fact.attributes).get("handle") == "vendor_redeclared"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-template-effective-block-binding"
        and fact.path == template
        and fact.relation == "suppressed-in-effective-layout"
        and dict(fact.attributes).get("handle") == "vendor_redeclared"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-layout-effective-template"
        and fact.source == "conditional.item"
        and fact.relation == "suppresses-inherited-template"
        and fact.target.endswith("templates/candidate.phtml")
        for fact in facts
    )
    assert any(
        fact.kind == "magento-template-effective-block-binding"
        and fact.path.endswith("templates/candidate.phtml")
        and fact.relation == "suppressed-in-effective-layout"
        for fact in facts
    )
