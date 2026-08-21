from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from codecrow_plugins import (
    FileArtifact,
    OutcomeStatus,
    PluginCatalog,
    RepositoryAnalysis,
    SymbolDefinition,
)


PLUGINS_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ROOT = PLUGINS_ROOT.parent


def _symbols() -> tuple[SymbolDefinition, ...]:
    values = (
        SymbolDefinition(
            "Acme\\Checkout\\Api\\CartInterface",
            "interface",
            "app/code/Acme/Checkout/Api/CartInterface.php",
            methods=("save",),
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Block\\Cart",
            "class",
            "app/code/Acme/Checkout/Block/Cart.php",
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Controller\\Cart\\Save",
            "class",
            "app/code/Acme/Checkout/Controller/Cart/Save.php",
            methods=("execute",),
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Controller\\Adminhtml\\Cart\\Index",
            "class",
            (
                "app/code/Acme/Checkout/Controller/Adminhtml/"
                "Cart/Index.php"
            ),
            methods=("execute",),
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Ui\\DataProvider\\Cart",
            "class",
            "app/code/Acme/Checkout/Ui/DataProvider/Cart.php",
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Cron\\Cleanup",
            "class",
            "app/code/Acme/Checkout/Cron/Cleanup.php",
            methods=("execute",),
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Model\\Cart",
            "class",
            "app/code/Acme/Checkout/Model/Cart.php",
            parents=("Acme\\Checkout\\Api\\CartInterface",),
            methods=("save",),
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Model\\FrontendCart",
            "class",
            "app/code/Acme/Checkout/Model/FrontendCart.php",
            parents=("Acme\\Checkout\\Model\\Cart",),
            methods=("save",),
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Model\\Service",
            "class",
            "app/code/Acme/Checkout/Model/Service.php",
            constructor_types=("Acme\\Checkout\\Api\\CartInterface",),
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Model\\ConfigReader",
            "class",
            "app/code/Acme/Checkout/Model/ConfigReader.php",
            methods=("mode",),
            attributes=tuple(
                (
                    f"php-literal-instance-call-reference:{index:04d}",
                    json.dumps(
                        {
                            "caller": "mode",
                            "line": line_number,
                            "literalStringArguments": {
                                "0": config_path,
                            },
                            "method": method,
                            "receiverResolution": "declared-property",
                            "target": (
                                "Magento\\Framework\\App\\Config\\"
                                "ScopeConfigInterface"
                            ),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                for index, (
                    line_number,
                    method,
                    config_path,
                ) in enumerate((
                    (18, "getValue", "acme/cart/runtime_mode"),
                    (22, "isSetFlag", "acme/cart/enabled"),
                ))
            ),
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Model\\QueueHandler",
            "class",
            "app/code/Acme/Checkout/Model/QueueHandler.php",
            methods=("process",),
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Observer\\Audit",
            "class",
            "app/code/Acme/Checkout/Observer/Audit.php",
            methods=("execute",),
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Plugin\\CartAudit",
            "class",
            "app/code/Acme/Checkout/Plugin/CartAudit.php",
            methods=("aroundSave", "beforeSave"),
        ),
        SymbolDefinition(
            "Acme\\CheckoutGraphQl\\Model\\Resolver\\Cart",
            "class",
            "app/code/Acme/CheckoutGraphQl/Model/Resolver/Cart.php",
            methods=("resolve",),
        ),
    )
    return tuple(sorted(values))


def _artifacts() -> dict[str, str]:
    return {
        "app/etc/config.php": """<?php return ['modules' => [
            'Acme_Checkout' => 1,
            'Acme_Audit' => 1,
            'Acme_CheckoutGraphQl' => 1,
            'Acme_Disabled' => 0,
        ]];""",
        "app/code/Acme/Checkout/etc/module.xml": """
            <config><module name="Acme_Checkout" /></config>
        """,
        "app/code/Acme/Audit/etc/module.xml": """
            <config><module name="Acme_Audit"><sequence>
              <module name="Acme_Checkout" />
            </sequence></module></config>
        """,
        "app/code/Acme/Disabled/etc/module.xml": """
            <config><module name="Acme_Disabled" /></config>
        """,
        "app/code/Acme/Checkout/registration.php": """<?php
            ComponentRegistrar::register(ComponentRegistrar::MODULE, 'Acme_Checkout', __DIR__);
        """,
        "app/code/Acme/Checkout/etc/di.xml": r"""
            <config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
              <preference for="Acme\Checkout\Api\CartInterface" type="Acme\Checkout\Model\Cart" />
              <type name="Acme\Checkout\Api\CartInterface">
                <plugin name="cart_audit" type="Acme\Checkout\Plugin\CartAudit" sortOrder="20" />
              </type>
              <type name="Acme\Checkout\Model\Service">
                <arguments><argument name="cart" xsi:type="object">Acme\Checkout\Api\CartInterface</argument></arguments>
              </type>
            </config>
        """,
        "app/code/Acme/Audit/etc/frontend/di.xml": r"""
            <config>
              <preference for="Acme\Checkout\Api\CartInterface" type="Acme\Checkout\Model\FrontendCart" />
              <type name="Acme\Checkout\Api\CartInterface">
                <plugin name="cart_audit" disabled="true" />
              </type>
            </config>
        """,
        "app/code/Acme/Disabled/etc/di.xml": r"""
            <config><preference for="Acme\Checkout\Api\CartInterface" type="Broken\Cart" /></config>
        """,
        "app/code/Acme/Checkout/etc/events.xml": r"""
            <config><event name="checkout_submit_all_after">
              <observer name="acme_audit" instance="Acme\Checkout\Observer\Audit" />
            </event></config>
        """,
        "app/code/Acme/Audit/etc/frontend/events.xml": """
            <config><event name="checkout_submit_all_after">
              <observer name="acme_audit" disabled="true" />
            </event></config>
        """,
        "app/code/Acme/Checkout/etc/frontend/routes.xml": """
            <config><router id="standard"><route id="checkout" frontName="checkout">
              <module name="Acme_Checkout" />
            </route></router></config>
        """,
        "app/code/Acme/Checkout/etc/adminhtml/routes.xml": """
            <config><router id="admin"><route id="acme" frontName="acme">
              <module name="Acme_Checkout" />
            </route></router></config>
        """,
        "app/code/Acme/Checkout/etc/adminhtml/menu.xml": """
            <config><menu>
              <add id="Acme_Checkout::root" title="Checkout"
                module="Acme_Checkout" sortOrder="10"
                resource="Acme_Checkout::cart" />
              <add id="Acme_Checkout::cart" title="Manage Cart"
                module="Acme_Checkout" parent="Acme_Checkout::root"
                sortOrder="20" action="acme/cart/index"
                resource="Acme_Checkout::cart"
                dependsOnModule="Acme_Checkout"
                dependsOnConfig="acme/cart/enabled" />
            </menu></config>
        """,
        "app/code/Acme/Audit/etc/adminhtml/menu.xml": """
            <config><menu>
              <update id="Acme_Checkout::cart" title="Audited Cart"
                sortOrder="30" />
            </menu></config>
        """,
        (
            "app/code/Acme/Checkout/Controller/Adminhtml/"
            "Cart/Index.php"
        ): """<?php
            namespace Acme\\Checkout\\Controller\\Adminhtml\\Cart;
            final class Index {
                public function execute() {}
            }
        """,
        "app/code/Acme/Checkout/view/frontend/layout/checkout_cart_save.xml": r"""
            <page><body><block class="Acme\Checkout\Block\Cart" name="cart"
              ifconfig="acme/cart/runtime_mode"
              aclResource="Acme_Checkout::cart"
              template="Acme_Checkout::cart.phtml" /></body></page>
        """,
        "app/code/Acme/Checkout/view/frontend/layout/checkout_shared.xml": "<page><body /></page>",
        "app/code/Acme/Checkout/view/frontend/layout/checkout_wrapper.xml": "<page><update handle=\"checkout_shared\" /></page>",
        "app/code/Acme/Checkout/view/frontend/templates/cart.phtml": "<div>cart</div>",
        "app/code/Acme/Checkout/view/adminhtml/ui_component/acme_cart_form.xml": r"""
            <form xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
              <aclResource>Acme_Checkout::cart</aclResource>
              <dataSource name="cart_data_source" component="Magento_Ui/js/form/provider">
                <dataProvider class="Acme\Checkout\Ui\DataProvider\Cart" name="cart_data_source" />
              </dataSource>
              <fieldset name="general">
                <field name="sku" component="Acme_Checkout/js/form/element/sku" />
              </fieldset>
            </form>
        """,
        "app/code/Acme/Checkout/view/adminhtml/web/js/form/element/sku.js": "define([], function () {});",
        "vendor/magento/theme-frontend-blank/theme.xml": "<theme><title>Blank</title></theme>",
        "vendor/magento/theme-frontend-blank/registration.php": """<?php
            ComponentRegistrar::register(ComponentRegistrar::THEME, 'frontend/Magento/blank', __DIR__);
        """,
        "app/design/frontend/Acme/custom/theme.xml": """
            <theme><title>Custom</title><parent>Magento/blank</parent></theme>
        """,
        "app/design/frontend/Acme/custom/registration.php": """<?php
            ComponentRegistrar::register(ComponentRegistrar::THEME, 'frontend/Acme/custom', __DIR__);
        """,
        "app/design/frontend/Acme/custom/Acme_Checkout/layout/checkout_cart_save.xml": """
            <page><body><referenceBlock name="cart" template="Acme_Checkout::cart.phtml" /></body></page>
        """,
        "app/design/frontend/Acme/custom/Acme_Checkout/templates/cart.phtml": "<div>overridden cart</div>",
        "app/code/Acme/Checkout/etc/acl.xml": """
            <config><acl><resources><resource id="Magento_Backend::admin">
              <resource id="Acme_Checkout::cart" title="Cart" />
            </resource></resources></acl></config>
        """,
        "app/code/Acme/Checkout/etc/webapi.xml": r"""
            <routes><route url="/V1/acme/cart" method="POST">
              <service class="Acme\Checkout\Api\CartInterface" method="save" />
              <resources><resource ref="Acme_Checkout::cart" /></resources>
            </route></routes>
        """,
        "app/code/Acme/Checkout/etc/crontab.xml": r"""
            <config><group id="default"><job name="acme_cleanup" instance="Acme\Checkout\Cron\Cleanup" method="execute">
              <schedule>0 * * * *</schedule>
            </job></group></config>
        """,
        "app/code/Acme/Checkout/etc/communication.xml": r"""
            <config><topic name="acme.cart.save" request="string">
              <handler name="cart" type="Acme\Checkout\Model\QueueHandler" method="process" />
            </topic></config>
        """,
        "app/code/Acme/Checkout/etc/queue_consumer.xml": r"""
            <config><consumer name="acme.cart.consumer"
              queue="acme.cart.save" connection="amqp"
              handler="Acme\Checkout\Model\QueueHandler::process" /></config>
        """,
        "app/code/Acme/Checkout/etc/queue_topology.xml": """
            <config><exchange name="magento" connection="amqp"><binding
              id="acme" topic="acme.cart.save" destinationType="queue"
              destination="acme.cart.save" /></exchange></config>
        """,
        "app/code/Acme/Checkout/etc/db_schema.xml": """
            <schema xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><table name="acme_cart" resource="default">
              <column xsi:type="int" name="entity_id" identity="true" nullable="false" />
              <constraint xsi:type="foreign" referenceId="ACME_CART_STORE" column="store_id" referenceTable="store" referenceColumn="store_id" />
            </table></schema>
        """,
        "app/code/Acme/Checkout/etc/extension_attributes.xml": r"""
            <config><extension_attributes for="Acme\Checkout\Api\CartInterface">
              <attribute code="audit_data" type="Acme\Checkout\Api\Data\AuditInterface" />
            </extension_attributes></config>
        """,
        "app/code/Acme/Checkout/etc/indexer.xml": r"""
            <config><indexer id="acme_cart" view_id="acme_cart" class="Acme\Checkout\Model\Cart" shared_index="acme">
              <title>Acme Cart</title>
            </indexer></config>
        """,
        "app/code/Acme/Audit/etc/indexer.xml": """
            <config><indexer id="acme_cart"><dependencies>
              <indexer id="catalog_product_price" />
            </dependencies></indexer></config>
        """,
        "app/code/Acme/Checkout/etc/mview.xml": r"""
            <config><view id="acme_cart" class="Acme\Checkout\Model\Cart" group="indexer">
              <subscriptions><table name="acme_cart" entity_column="entity_id" /></subscriptions>
            </view></config>
        """,
        "app/code/Acme/Checkout/etc/adminhtml/system.xml": r"""
            <config><system><section id="acme">
              <resource>Acme_Checkout::cart</resource>
              <group id="cart"><field id="mode" type="select" extends="base_mode">
                <config_path>acme/cart/runtime_mode</config_path>
                <source_model>Acme\Checkout\Model\Cart</source_model>
                <backend_model>Acme\Checkout\Model\Service</backend_model>
                <frontend_model>Acme\Checkout\Block\Cart</frontend_model>
                <depends><field id="acme/cart/enabled">1</field></depends>
              </field><field id="enabled" type="select">
                <source_model>Acme\Checkout\Model\Cart</source_model>
              </field></group>
            </section></system></config>
        """,
        "app/code/Acme/Checkout/etc/config.xml": """
            <config><default><acme><cart>
              <runtime_mode>safe</runtime_mode>
              <enabled>1</enabled>
            </cart></acme></default>
            <websites><base><acme><cart>
              <runtime_mode>website-safe</runtime_mode>
            </cart></acme></base></websites>
            <stores><default_store><acme><cart>
              <runtime_mode>store-safe</runtime_mode>
            </cart></acme></default_store></stores></config>
        """,
        "app/code/Acme/Checkout/etc/validation.xml": r"""
            <config><validator class="Acme\Checkout\Model\Cart" /></config>
        """,
        "app/code/Acme/CheckoutGraphQl/etc/module.xml": """
            <config><module name="Acme_CheckoutGraphQl"><sequence><module name="Acme_Checkout" /></sequence></module></config>
        """,
        "app/code/Acme/CheckoutGraphQl/etc/schema.graphqls": """
            type Query {
              acmeCart(id: Int!): String @resolver(class: "Acme\\CheckoutGraphQl\\Model\\Resolver\\Cart")
            }
        """,
    }


def _resolve(
    artifacts: dict[str, str] | None = None,
    symbols: tuple[SymbolDefinition, ...] | None = None,
    catalog: PluginCatalog | None = None,
):
    catalog = catalog or PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    start = plugin.start_repository_analysis("0123456789abcdef")
    assert start.status is OutcomeStatus.HANDLED
    session = start.value
    selected_artifacts = _artifacts() if artifacts is None else artifacts
    artifact_inputs = tuple(
        FileArtifact(path, content)
        for path, content in sorted(selected_artifacts.items())
    )
    session.ingest(artifact_inputs)
    outcome = session.finish(RepositoryAnalysis(
        symbols=_symbols() if symbols is None else symbols,
    ))
    assert outcome.status is OutcomeStatus.HANDLED
    return outcome.value


def test_magento_repository_reports_timed_substages():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    session = plugin.start_repository_analysis("progress-test").value
    session.ingest((FileArtifact(
        "app/code/Acme/Checkout/etc/module.xml",
        '<config><module name="Acme_Checkout" /></config>',
    ),))
    events = []
    session.set_progress_callback(events.append)

    outcome = session.finish(RepositoryAnalysis())

    assert outcome.status is OutcomeStatus.HANDLED
    assert any(
        event.get("substage") == "module discovery"
        and event.get("status") == "started"
        for event in events
    )
    assert any(
        event.get("substage") == "packet materialization"
        and event.get("status") == "completed"
        and isinstance(event.get("durationMs"), int)
        for event in events
    )


def test_magento_repository_honors_host_finalization_deadline():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    session = plugin.start_repository_analysis("timeout-test").value
    session.ingest((FileArtifact(
        "app/code/Acme/Checkout/etc/module.xml",
        '<config><module name="Acme_Checkout" /></config>',
    ),))
    session.set_analysis_deadline(0.0)

    try:
        session.finish(RepositoryAnalysis())
    except TimeoutError as exception:
        assert "module discovery" in str(exception)
    else:
        raise AssertionError("expired architecture deadline was not enforced")


def _factory_artifacts(
    *,
    checkout_enabled: bool = True,
    include_preferences: bool = True,
) -> dict[str, str]:
    artifacts = {
        "app/etc/config.php": """<?php return ['modules' => [
            'Acme_Checkout' => %s,
        ]];""" % ("1" if checkout_enabled else "0"),
        "app/code/Acme/Checkout/etc/module.xml": """
            <config><module name="Acme_Checkout" /></config>
        """,
    }
    if include_preferences:
        artifacts.update({
            "app/code/Acme/Checkout/etc/di.xml": r"""
                <config><preference
                    for="Acme\Checkout\Api\ItemInterface"
                    type="Acme\Checkout\Model\Item" /></config>
            """,
            "app/code/Acme/Checkout/etc/frontend/di.xml": r"""
                <config><preference
                    for="Acme\Checkout\Api\ItemInterface"
                    type="Acme\Checkout\Model\FrontendItem" /></config>
            """,
        })
    return artifacts


def test_email_templates_link_declaration_files_theme_fallback_and_consumers():
    artifacts = {
        "app/etc/config.php": """<?php return ['modules' => [
            'Acme_Email' => 1,
            'Acme_Disabled' => 0,
        ]];""",
        "app/code/Acme/Email/etc/module.xml": """
            <config><module name="Acme_Email" /></config>
        """,
        "app/code/Acme/Disabled/etc/module.xml": """
            <config><module name="Acme_Disabled" /></config>
        """,
        "app/code/Acme/Email/etc/email_templates.xml": """
            <config>
              <template id="acme_order"
                label="Order email"
                file="order.html"
                type="html"
                module="Acme_Email"
                area="frontend" />
              <template id="unsafe"
                file="../outside.html"
                module="Acme_Email"
                area="frontend" />
            </config>
        """,
        "app/code/Acme/Email/etc/config.xml": """
            <config><default><sales><email>
              <template>acme_order</template>
            </email></sales></default></config>
        """,
        "app/code/Acme/Email/view/frontend/email/order.html": """
            <!-- module email -->
        """,
        "app/code/Acme/Disabled/etc/email_templates.xml": """
            <config><template id="disabled_email"
              file="disabled.html"
              module="Acme_Disabled"
              area="frontend" /></config>
        """,
        "app/code/Acme/Disabled/view/frontend/email/disabled.html": """
            disabled
        """,
        "vendor/acme/theme-parent/theme.xml": """
            <theme><title>Parent</title></theme>
        """,
        "vendor/acme/theme-parent/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Acme/parent',
                __DIR__
            );
        """,
        "vendor/acme/theme-parent/Acme_Email/email/order.html": """
            <!-- parent override -->
        """,
        "app/design/frontend/Acme/child/theme.xml": """
            <theme><title>Child</title><parent>Acme/parent</parent></theme>
        """,
        "app/design/frontend/Acme/child/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Acme/child',
                __DIR__
            );
        """,
        "app/design/frontend/Acme/child/Acme_Email/email/order.html": """
            <!-- child override -->
        """,
    }
    consumer = SymbolDefinition(
        "Acme\\Email\\Model\\Sender",
        "class",
        "app/code/Acme/Email/Model/Sender.php",
        methods=("send",),
        attributes=((
            "php-literal-instance-call-reference:0000",
            json.dumps(
                {
                    "caller": "send",
                    "line": 42,
                    "literalStringArguments": {"0": "acme_order"},
                    "method": "setTemplateIdentifier",
                    "receiverResolution": "declared-property",
                    "target": (
                        "Magento\\Framework\\Mail\\Template\\"
                        "TransportBuilder"
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),),
    )

    analysis = _resolve(artifacts, (consumer,))
    facts = [
        fact
        for packet in analysis.packets
        for fact in packet.facts
    ]
    declaration = next(
        fact
        for fact in facts
        if fact.kind == "magento-email-template"
    )
    assert declaration.source == "acme_order"
    assert declaration.target == "Acme_Email::order.html"
    assert dict(declaration.attributes) == {
        "area": "frontend",
        "file": "order.html",
        "label": "Order email",
        "module": "Acme_Email",
        "order": "0",
        "type": "html",
    }
    assert not any(fact.source == "unsafe" for fact in facts)
    assert not any(fact.source == "disabled_email" for fact in facts)

    overrides = [
        fact
        for fact in facts
        if fact.kind == "magento-email-template-override"
    ]
    assert {
        (fact.source, fact.path)
        for fact in overrides
    } == {
        (
            "Acme/child:acme_order",
            (
                "app/design/frontend/Acme/child/"
                "Acme_Email/email/order.html"
            ),
        ),
        (
            "Acme/parent:acme_order",
            "vendor/acme/theme-parent/Acme_Email/email/order.html",
        ),
    }
    child_packet = next(
        packet
        for packet in analysis.packets
        if any(
            fact.kind == "magento-email-template-override"
            and fact.source == "Acme/child:acme_order"
            for fact in packet.facts
        )
    )
    assert (
        "vendor/acme/theme-parent/Acme_Email/email/order.html"
        in child_packet.paths
    )

    selection = next(
        fact
        for fact in facts
        if fact.kind == "magento-email-config-default"
    )
    assert selection.source == "config/default/sales/email/template"
    assert selection.target == "acme_order"

    runtime_consumer = next(
        fact
        for fact in facts
        if fact.kind == "magento-email-template-consumer"
    )
    assert runtime_consumer.source == "Acme\\Email\\Model\\Sender"
    assert runtime_consumer.target == "acme_order"
    assert runtime_consumer.line == 42
    consumer_packet = next(
        packet
        for packet in analysis.packets
        if runtime_consumer in packet.facts
    )
    assert {
        "app/code/Acme/Email/etc/email_templates.xml",
        "app/code/Acme/Email/view/frontend/email/order.html",
        "app/design/frontend/Acme/child/Acme_Email/email/order.html",
        "vendor/acme/theme-parent/Acme_Email/email/order.html",
    } <= set(consumer_packet.paths)

    sys.path.insert(
        0,
        str(PROJECT_ROOT / "python-ecosystem" / "rag-pipeline" / "src"),
    )
    from rag_pipeline.core.index_manager.indexer import RepositoryIndexer

    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    repository_plugins = ("php", "magento")
    capabilities = SimpleNamespace(
        repository_plugins=repository_plugins,
        fingerprint="sha256:" + "0" * 64,
        descriptor_fingerprint=(
            catalog.registry.fingerprint_for(repository_plugins)
        ),
        implementation_fingerprint=(
            catalog.implementation_fingerprint(repository_plugins)
        ),
    )
    nodes = RepositoryIndexer._architecture_nodes(
        analysis,
        capabilities,
        "ws",
        "project",
        "main",
        "commit",
        capabilities.implementation_fingerprint,
    )
    consumer_nodes = [
        node
        for node in nodes
        if any(
            fact["kind"] == "magento-email-template-consumer"
            for fact in node.metadata["plugin_graph_facts"]
        )
    ]
    assert len(consumer_nodes) == 1
    assert "acme_order" in consumer_nodes[0].text
    assert (
        "app/code/Acme/Email/view/frontend/email/order.html"
        in consumer_nodes[0].metadata["architecture_paths"]
    )

    magento = catalog.implementation("magento")
    restored = magento.restore_repository_analysis(
        "email-overlay",
        analysis.snapshots,
    )
    child_override = (
        "app/design/frontend/Acme/child/"
        "Acme_Email/email/order.html"
    )
    restored.value.ingest((
        FileArtifact(child_override, "", deleted=True),
    ))
    overlay = restored.value.finish(
        RepositoryAnalysis(symbols=(consumer,))
    ).value
    assert all(
        child_override not in packet.paths
        for packet in overlay.packets
    )
    assert any(
        fact.kind == "magento-email-template-override"
        and fact.source == "Acme/parent:acme_order"
        for packet in overlay.packets
        for fact in packet.facts
    )


def _factory_symbols(
    *additional: SymbolDefinition,
    constructor_type: str = (
        "Acme\\Checkout\\Api\\ItemInterfaceFactory"
    ),
) -> tuple[SymbolDefinition, ...]:
    return tuple(sorted((
        SymbolDefinition(
            "Acme\\Checkout\\Api\\ItemInterface",
            "interface",
            "app/code/Acme/Checkout/Api/ItemInterface.php",
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Model\\FrontendItem",
            "class",
            "app/code/Acme/Checkout/Model/FrontendItem.php",
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Model\\Item",
            "class",
            "app/code/Acme/Checkout/Model/Item.php",
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Model\\Service",
            "class",
            "app/code/Acme/Checkout/Model/Service.php",
            line=7,
            constructor_types=(constructor_type,),
        ),
        *additional,
    )))


def _price_pool_artifacts(
    registered_code: str = "price_per_specific_weight",
) -> dict[str, str]:
    return {
        "app/etc/config.php": """<?php return ['modules' => [
            'Perspective_Prices' => 1,
            'Perspective_SeoMarkup' => 1,
        ]];""",
        "app/code/Perspective/Prices/etc/module.xml": """
            <config><module name="Perspective_Prices" /></config>
        """,
        "app/code/Perspective/SeoMarkup/etc/module.xml": """
            <config><module name="Perspective_SeoMarkup">
              <sequence><module name="Perspective_Prices" /></sequence>
            </module></config>
        """,
        "app/code/Perspective/Prices/etc/di.xml": rf"""
            <config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
              <virtualType name="Magento\Catalog\Pricing\Price\Pool">
                <arguments>
                  <argument name="prices" xsi:type="array">
                    <item name="{registered_code}" xsi:type="string">Perspective\Prices\Pricing\Price\WeightPrice</item>
                  </argument>
                </arguments>
              </virtualType>
            </config>
        """,
        "app/code/Perspective/SeoMarkup/etc/frontend/di.xml": r"""
            <config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
              <type name="Perspective\SeoMarkup\Converter">
                <arguments>
                  <argument name="enabled" xsi:type="boolean">true</argument>
                </arguments>
              </type>
            </config>
        """,
        (
            "app/code/Perspective/Prices/Pricing/Price/WeightPrice.php"
        ): "<?php class WeightPrice {}",
        (
            "app/code/Perspective/SeoMarkup/Converter.php"
        ): "<?php class Converter {}",
    }


def _price_pool_symbols(
    *,
    argument_of: str = "getPrice",
) -> tuple[SymbolDefinition, ...]:
    provider = (
        "Perspective\\Prices\\Pricing\\Price\\WeightPrice"
    )
    return tuple(sorted((
        SymbolDefinition(
            provider,
            "class",
            (
                "app/code/Perspective/Prices/Pricing/Price/"
                "WeightPrice.php"
            ),
            line=5,
            attributes=((
                "php-class-constant:0000",
                json.dumps(
                    {
                        "line": 7,
                        "name": "PRICE_CODE",
                        "value": "price_per_specific_weight",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),),
        ),
        SymbolDefinition(
            "Perspective\\SeoMarkup\\Converter",
            "class",
            "app/code/Perspective/SeoMarkup/Converter.php",
            line=5,
            attributes=((
                "php-class-constant-reference:0000",
                json.dumps(
                    {
                        "argumentOf": argument_of,
                        "constant": "PRICE_CODE",
                        "line": 17,
                        "target": provider,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),),
        ),
    )))


def test_magento_price_pool_links_exact_price_code_consumer_and_provider():
    analysis = _resolve(
        artifacts=_price_pool_artifacts(),
        symbols=_price_pool_symbols(),
    )
    selected_facts = [
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind.startswith("magento-price-pool")
    ]
    facts = {
        fact.kind: fact
        for fact in selected_facts
    }

    assert len(selected_facts) == 2
    assert set(facts) == {
        "magento-price-pool-reference",
        "magento-price-pool-registration",
    }
    registration = facts["magento-price-pool-registration"]
    assert registration.source == (
        "Magento\\Catalog\\Pricing\\Price\\Pool"
    )
    assert registration.relation == "registers-price-model"
    assert registration.target == (
        "Perspective\\Prices\\Pricing\\Price\\WeightPrice"
    )
    assert dict(registration.attributes)["priceCode"] == (
        "price_per_specific_weight"
    )
    assert dict(registration.attributes)["area"] == "global"

    reference = facts["magento-price-pool-reference"]
    assert reference.source == "Perspective\\SeoMarkup\\Converter"
    assert reference.relation == "requests-registered-price"
    assert reference.target == registration.target
    assert reference.line == 17
    assert set(reference.related_paths) == {
        "app/code/Perspective/Prices/Pricing/Price/WeightPrice.php",
        "app/code/Perspective/Prices/etc/di.xml",
    }


def test_magento_price_pool_abstains_without_exact_code_or_get_price_call():
    mismatched_code = _resolve(
        artifacts=_price_pool_artifacts("different_code"),
        symbols=_price_pool_symbols(),
    )
    different_call = _resolve(
        artifacts=_price_pool_artifacts(),
        symbols=_price_pool_symbols(argument_of="getSomethingElse"),
    )

    assert not any(
        fact.kind == "magento-price-pool-reference"
        for analysis in (mismatched_code, different_call)
        for packet in analysis.packets
        for fact in packet.facts
    )
    assert any(
        fact.kind == "magento-price-pool-registration"
        for packet in different_call.packets
        for fact in packet.facts
    )


def test_generated_factory_links_consumer_to_effective_area_implementations():
    analysis = _resolve(
        artifacts=_factory_artifacts(),
        symbols=_factory_symbols(),
    )
    facts = tuple(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind.startswith("magento-generated-factory")
    )

    dependency = next(
        fact
        for fact in facts
        if fact.kind == "magento-generated-factory"
    )
    assert (
        dependency.source,
        dependency.relation,
        dependency.target,
        dependency.path,
        dependency.line,
    ) == (
        "Acme\\Checkout\\Model\\Service",
        "uses-generated-factory-for",
        "Acme\\Checkout\\Api\\ItemInterface",
        "app/code/Acme/Checkout/Model/Service.php",
        7,
    )
    assert dict(dependency.attributes) == {
        "consumerModule": "Acme_Checkout",
        "factoryType": (
            "Acme\\Checkout\\Api\\ItemInterfaceFactory"
        ),
        "generated": "true",
        "requestedKind": "interface",
        "targetModule": "Acme_Checkout",
    }
    assert (
        "app/code/Acme/Checkout/Api/ItemInterface.php"
        in dependency.related_paths
    )

    resolutions = {
        (
            dict(fact.attributes)["area"],
            fact.target,
            tuple(fact.related_paths),
        )
        for fact in facts
        if fact.kind == "magento-generated-factory-resolution"
    }
    assert {
        (area, target)
        for area, target, _ in resolutions
    } == {
        ("global", "Acme\\Checkout\\Model\\Item"),
        ("frontend", "Acme\\Checkout\\Model\\FrontendItem"),
    }
    global_paths = next(
        paths
        for area, _, paths in resolutions
        if area == "global"
    )
    frontend_paths = next(
        paths
        for area, _, paths in resolutions
        if area == "frontend"
    )
    assert "app/code/Acme/Checkout/etc/di.xml" in global_paths
    assert (
        "app/code/Acme/Checkout/etc/frontend/di.xml"
        in frontend_paths
    )
    assert (
        "app/code/Acme/Checkout/Model/FrontendItem.php"
        in frontend_paths
    )


def test_generated_factory_links_concrete_target_case_insensitively():
    analysis = _resolve(
        artifacts=_factory_artifacts(include_preferences=False),
        symbols=_factory_symbols(
            constructor_type=(
                "acme\\checkout\\model\\itemFactory"
            ),
        ),
    )
    facts = tuple(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind.startswith("magento-generated-factory")
    )

    assert {
        (fact.kind, fact.relation, fact.target)
        for fact in facts
    } == {
        (
            "magento-generated-factory",
            "uses-generated-factory-for",
            "Acme\\Checkout\\Model\\Item",
        ),
        (
            "magento-generated-factory-resolution",
            "creates-via-generated-factory",
            "Acme\\Checkout\\Model\\Item",
        ),
    }


def test_generated_factory_abstains_for_explicit_custom_factory():
    explicit_factory = SymbolDefinition(
        "Acme\\Checkout\\Api\\ItemInterfaceFactory",
        "class",
        "app/code/Acme/Checkout/Api/ItemInterfaceFactory.php",
        methods=("create",),
    )
    analysis = _resolve(
        artifacts=_factory_artifacts(),
        symbols=_factory_symbols(explicit_factory),
    )

    assert not any(
        fact.kind.startswith("magento-generated-factory")
        for packet in analysis.packets
        for fact in packet.facts
    )


def test_generated_factory_abstains_for_unproven_or_disabled_target():
    duplicate = SymbolDefinition(
        "Acme\\Checkout\\Api\\ItemInterface",
        "interface",
        "app/code/Acme/Checkout/Api/DuplicateItemInterface.php",
    )
    ambiguous = _resolve(
        artifacts=_factory_artifacts(),
        symbols=_factory_symbols(duplicate),
    )
    disabled = _resolve(
        artifacts=_factory_artifacts(checkout_enabled=False),
        symbols=_factory_symbols(),
    )
    noncanonical = _resolve(
        artifacts=_factory_artifacts(),
        symbols=_factory_symbols(
            constructor_type=(
                "Acme\\Checkout\\Api\\ItemInterfacefactory"
            ),
        ),
    )
    external = _resolve(
        artifacts=_factory_artifacts(),
        symbols=_factory_symbols(
            constructor_type="External\\Catalog\\ItemFactory",
        ),
    )

    for analysis in (ambiguous, disabled, noncanonical, external):
        assert not any(
            fact.kind.startswith("magento-generated-factory")
            for packet in analysis.packets
            for fact in packet.facts
        )


def test_generated_interface_factory_does_not_invent_missing_preference():
    analysis = _resolve(
        artifacts=_factory_artifacts(include_preferences=False),
        symbols=_factory_symbols(),
    )
    facts = tuple(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind.startswith("magento-generated-factory")
    )

    assert len(facts) == 1
    assert facts[0].kind == "magento-generated-factory"
    assert facts[0].target == "Acme\\Checkout\\Api\\ItemInterface"


def _proxy_artifacts(
    *,
    checkout_enabled: bool = True,
    include_preferences: bool = True,
    proxy_type: str = "Acme\\Checkout\\Api\\ItemInterface\\Proxy",
) -> dict[str, str]:
    preference = (
        r"""<preference
            for="Acme\Checkout\Api\ItemInterface"
            type="Acme\Checkout\Model\Item" />"""
        if include_preferences
        else ""
    )
    artifacts = {
        "app/etc/config.php": """<?php return ['modules' => [
            'Acme_Checkout' => %s,
        ]];""" % ("1" if checkout_enabled else "0"),
        "app/code/Acme/Checkout/etc/module.xml": """
            <config><module name="Acme_Checkout" /></config>
        """,
        "app/code/Acme/Checkout/etc/di.xml": f"""
            <config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
              {preference}
              <type name="Acme\\Checkout\\Model\\Service">
                <arguments>
                  <argument name="item" xsi:type="object">
                    {proxy_type}
                  </argument>
                </arguments>
              </type>
            </config>
        """,
    }
    if include_preferences:
        artifacts["app/code/Acme/Checkout/etc/frontend/di.xml"] = r"""
            <config><preference
                for="Acme\Checkout\Api\ItemInterface"
                type="Acme\Checkout\Model\FrontendItem" /></config>
        """
    return artifacts


def _proxy_symbols(
    *additional: SymbolDefinition,
) -> tuple[SymbolDefinition, ...]:
    return tuple(sorted((
        SymbolDefinition(
            "Acme\\Checkout\\Api\\ItemInterface",
            "interface",
            "app/code/Acme/Checkout/Api/ItemInterface.php",
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Model\\FrontendItem",
            "class",
            "app/code/Acme/Checkout/Model/FrontendItem.php",
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Model\\Item",
            "class",
            "app/code/Acme/Checkout/Model/Item.php",
        ),
        SymbolDefinition(
            "Acme\\Checkout\\Model\\Service",
            "class",
            "app/code/Acme/Checkout/Model/Service.php",
        ),
        *additional,
    )))


def test_generated_proxy_links_di_owner_to_effective_area_implementations():
    analysis = _resolve(
        artifacts=_proxy_artifacts(),
        symbols=_proxy_symbols(),
    )
    facts = tuple(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind.startswith("magento-generated-proxy")
    )

    dependency = next(
        fact
        for fact in facts
        if fact.kind == "magento-generated-proxy"
    )
    assert (
        dependency.source,
        dependency.relation,
        dependency.target,
        dependency.path,
    ) == (
        "Acme\\Checkout\\Model\\Service",
        "injects-generated-proxy-for",
        "Acme\\Checkout\\Api\\ItemInterface",
        "app/code/Acme/Checkout/etc/di.xml",
    )
    assert dict(dependency.attributes) == {
        "area": "global",
        "argument": "item",
        "configured": (
            "Acme\\Checkout\\Api\\ItemInterface\\Proxy"
        ),
        "declaredFor": "Acme\\Checkout\\Model\\Service",
        "generated": "true",
        "module": "Acme_Checkout",
        "proxyType": (
            "Acme\\Checkout\\Api\\ItemInterface\\Proxy"
        ),
        "requestedKind": "interface",
        "targetModule": "Acme_Checkout",
    }
    assert {
        (
            dict(fact.attributes)["area"],
            fact.target,
        )
        for fact in facts
        if fact.kind == "magento-generated-proxy-resolution"
    } == {
        ("global", "Acme\\Checkout\\Model\\Item"),
        ("frontend", "Acme\\Checkout\\Model\\FrontendItem"),
    }
    frontend = next(
        fact
        for fact in facts
        if fact.kind == "magento-generated-proxy-resolution"
        and dict(fact.attributes)["area"] == "frontend"
    )
    assert (
        "app/code/Acme/Checkout/etc/frontend/di.xml"
        in frontend.related_paths
    )
    assert (
        "app/code/Acme/Checkout/Model/FrontendItem.php"
        in frontend.related_paths
    )


def test_generated_proxy_abstains_for_explicit_custom_proxy():
    explicit_proxy = SymbolDefinition(
        "Acme\\Checkout\\Api\\ItemInterface\\Proxy",
        "class",
        "app/code/Acme/Checkout/Api/ItemInterface/Proxy.php",
    )
    analysis = _resolve(
        artifacts=_proxy_artifacts(),
        symbols=_proxy_symbols(explicit_proxy),
    )

    assert not any(
        fact.kind.startswith("magento-generated-proxy")
        for packet in analysis.packets
        for fact in packet.facts
    )


def test_generated_proxy_abstains_for_unproven_or_disabled_target():
    duplicate = SymbolDefinition(
        "Acme\\Checkout\\Api\\ItemInterface",
        "interface",
        "app/code/Acme/Checkout/Api/DuplicateItemInterface.php",
    )
    ambiguous = _resolve(
        artifacts=_proxy_artifacts(),
        symbols=_proxy_symbols(duplicate),
    )
    disabled = _resolve(
        artifacts=_proxy_artifacts(checkout_enabled=False),
        symbols=_proxy_symbols(),
    )
    noncanonical = _resolve(
        artifacts=_proxy_artifacts(
            proxy_type="Acme\\Checkout\\Api\\ItemInterface\\proxy",
        ),
        symbols=_proxy_symbols(),
    )
    external = _resolve(
        artifacts=_proxy_artifacts(
            proxy_type="External\\Catalog\\Item\\Proxy",
        ),
        symbols=_proxy_symbols(),
    )

    for analysis in (ambiguous, disabled, noncanonical, external):
        assert not any(
            fact.kind.startswith("magento-generated-proxy")
            for packet in analysis.packets
            for fact in packet.facts
        )


def test_generated_interface_proxy_does_not_invent_missing_preference():
    analysis = _resolve(
        artifacts=_proxy_artifacts(include_preferences=False),
        symbols=_proxy_symbols(),
    )
    facts = tuple(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind.startswith("magento-generated-proxy")
    )

    assert len(facts) == 1
    assert facts[0].kind == "magento-generated-proxy"
    assert facts[0].target == "Acme\\Checkout\\Api\\ItemInterface"


def test_magento_repository_analysis_builds_effective_architecture_graph():
    analysis = _resolve()
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)
    triples = {(fact.kind, fact.source, fact.relation, fact.target) for fact in facts}

    assert (
        "magento-di-effective-preference",
        "Acme\\Checkout\\Api\\CartInterface",
        "resolves-to",
        "Acme\\Checkout\\Model\\Cart",
    ) in triples
    assert (
        "magento-di-effective-preference",
        "Acme\\Checkout\\Api\\CartInterface",
        "resolves-to",
        "Acme\\Checkout\\Model\\FrontendCart",
    ) in triples
    assert not any(fact.target == "Broken\\Cart" for fact in facts)
    assert any(fact.kind == "magento-di-inherited-plugin" and fact.source.endswith("Model\\Cart") for fact in facts)
    assert any(fact.kind == "magento-di-effective-plugin" and fact.relation == "disables-interceptor" for fact in facts)
    assert any(fact.kind == "magento-object-resolution" and fact.target.endswith("FrontendCart") for fact in facts)
    assert any(fact.kind == "magento-effective-observer" and fact.relation == "disables-observer" for fact in facts)


def test_magento_graphql_parser_ignores_arguments_and_description_words():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": "<?php return ['modules' => ['Acme_GraphQl' => 1]];",
            "app/code/Acme/GraphQl/etc/module.xml": (
                '<config><module name="Acme_GraphQl" /></config>'
            ),
            "app/code/Acme/GraphQl/etc/schema.graphqls": r'''
                """Product type ID is documentation, not a declaration."""
                extend type Query {
                  products(search: String, pageSize: Int): Products
                    @resolver(class: "Acme\GraphQl\Model\Resolver\Products")
                }
                type Products { items: [Product!]! }
                type Product { product_type: String! }
            ''',
            "app/code/Acme/GraphQl/view/frontend/templates/products.phtml": r'''
                <?php $type = $block->getData('product_type'); ?>
                <script type="application/graphql">
                  query Products($search: String!) {
                    products(search: $search) {
                      items { product_type }
                    }
                  }
                </script>
            ''',
        },
        symbols=(),
    )
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)

    fields = {
        (fact.source, fact.target)
        for fact in facts
        if fact.kind == "magento-graphql-field"
    }
    assert fields == {
        ("Product", "product_type"),
        ("Products", "items"),
        ("Query", "products"),
    }
    resolver = next(
        fact for fact in facts if fact.kind == "magento-graphql-resolver"
    )
    assert resolver.source == "Query.products"
    assert resolver.target == "Acme\\GraphQl\\Model\\Resolver\\Products"

    client_facts = tuple(
        fact
        for fact in facts
        if fact.kind == "magento-graphql-operation-field"
    )
    assert tuple(fact.relation for fact in client_facts) == (
        "selects-schema-field",
        "selects-schema-field",
        "selects-schema-field",
    )
    assert {fact.target for fact in client_facts} == {
        "app/code/Acme/GraphQl/etc/schema.graphqls::Query.products",
        "app/code/Acme/GraphQl/etc/schema.graphqls::Products.items",
        "app/code/Acme/GraphQl/etc/schema.graphqls::Product.product_type",
    }
    assert all(
        fact.path
        == "app/code/Acme/GraphQl/view/frontend/templates/products.phtml"
        for fact in client_facts
    )


def test_magento_graphql_client_resolves_custom_query_root():
    analysis = _resolve(
        artifacts={
            "app/code/Acme/GraphQl/etc/module.xml": (
                '<config><module name="Acme_GraphQl" /></config>'
            ),
            "app/code/Acme/GraphQl/etc/schema.graphqls": """
                schema { query: StorefrontQuery }
                type StorefrontQuery { products: Products }
                type Products { total_count: Int! }
            """,
            "app/code/Acme/GraphQl/view/frontend/templates/products.phtml": """
                <script type="application/graphql">
                  { products { total_count } }
                </script>
            """,
        },
        symbols=(),
    )

    targets = {
        fact.target
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-graphql-operation-field"
    }

    assert targets == {
        "app/code/Acme/GraphQl/etc/schema.graphqls::StorefrontQuery.products",
        "app/code/Acme/GraphQl/etc/schema.graphqls::Products.total_count",
    }


def test_module_only_repository_keeps_cross_module_relationships():
    interface_path = "app/code/Acme/Contracts/Api/CartInterface.php"
    implementation_path = "app/code/Acme/Checkout/Model/Cart.php"
    analysis = _resolve(
        artifacts={
            "app/code/Acme/Contracts/etc/module.xml": (
                '<config><module name="Acme_Contracts" /></config>'
            ),
            "app/code/Acme/Checkout/etc/module.xml": (
                '<config><module name="Acme_Checkout"><sequence>'
                '<module name="Acme_Contracts" />'
                '</sequence></module></config>'
            ),
            "app/code/Acme/Checkout/etc/di.xml": r"""
                <config><preference
                  for="Acme\Contracts\Api\CartInterface"
                  type="Acme\Checkout\Model\Cart" /></config>
            """,
        },
        symbols=tuple(sorted((
            SymbolDefinition(
                "Acme\\Contracts\\Api\\CartInterface",
                "interface",
                interface_path,
            ),
            SymbolDefinition(
                "Acme\\Checkout\\Model\\Cart",
                "class",
                implementation_path,
            ),
        ))),
    )

    preference_packet = next(
        packet
        for packet in analysis.packets
        if any(
            fact.kind == "magento-di-effective-preference"
            for fact in packet.facts
        )
    )

    assert {interface_path, implementation_path} <= set(preference_packet.paths)


def test_magento_manual_nested_root_keeps_canonical_fact_paths():
    root = "magento/src/etc"
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    session = plugin.start_repository_analysis("0123456789abcdef").value
    session.set_source_root(root)
    session.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted({
            f"{root}/app/etc/config.php": (
                "<?php return ['modules' => ['Acme_Checkout' => 1]];"
            ),
            f"{root}/app/code/Acme/Checkout/etc/module.xml": (
                '<config><module name="Acme_Checkout" /></config>'
            ),
            f"{root}/app/code/Acme/Checkout/etc/di.xml": r'''
                <config><preference for="Acme\Checkout\Api\CartInterface"
                  type="Acme\Checkout\Model\Cart" /></config>
            ''',
        }.items()
    )))
    nested_symbols = tuple(
        replace(symbol, path=f"{root}/{symbol.path}")
        for symbol in _symbols()
    )
    outcome = session.finish(RepositoryAnalysis(symbols=nested_symbols))
    facts = tuple(
        fact for packet in outcome.value.packets for fact in packet.facts
    )

    preference = next(
        fact for fact in facts
        if fact.kind == "magento-di-effective-preference"
    )
    assert preference.path == (
        f"{root}/app/code/Acme/Checkout/etc/di.xml"
    )
    assert f"{root}/app/code/Acme/Checkout/Model/Cart.php" in {
        path
        for packet in outcome.value.packets
        for path in packet.paths
    }
    assert all(
        not fact.path.startswith(f"{root}/{root}/")
        for fact in facts
    )


def test_layout_binds_selected_phtml_to_exact_block_method_and_view_model(
    monkeypatch,
):
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    session = plugin.start_repository_analysis("0123456789abcdef").value
    repository_module = sys.modules[session.__class__.__module__]
    monkeypatch.setattr(
        repository_module,
        "extract_template_global_references",
        lambda content: (),
    )
    monkeypatch.setattr(
        repository_module,
        "extract_template_event_references",
        lambda content: (),
    )
    artifacts = {
        "app/etc/config.php": "<?php return ['modules' => ['Acme_Checkout' => 1]];",
        "app/code/Acme/Checkout/etc/module.xml": (
            '<config><module name="Acme_Checkout" /></config>'
        ),
        "app/code/Acme/Checkout/view/frontend/layout/checkout_index_index.xml": r'''
            <page xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>
              <block class="Acme\Checkout\Block\Cart" name="cart"
                template="Acme_Checkout::cart.phtml">
                <arguments><argument name="view_model" xsi:type="object">Acme\Checkout\ViewModel\Cart</argument></arguments>
              </block>
            </body></page>
        ''',
        "app/code/Acme/Checkout/view/frontend/templates/cart.phtml": (
            "<?= $block->getCartId() ?>\n"
            "<?= $block->privateHelper() ?>\n"
            "<?= $block->unknownDynamicMethod() ?>\n"
        ),
    }
    symbols = (
        SymbolDefinition(
            "Acme\\Checkout\\Block\\Cart",
            "class",
            "app/code/Acme/Checkout/Block/Cart.php",
            methods=("getCartId", "privateHelper"),
            attributes=(("method:privateHelper:visibility", "private"),),
        ),
        SymbolDefinition(
            "Acme\\Checkout\\ViewModel\\Cart",
            "class",
            "app/code/Acme/Checkout/ViewModel/Cart.php",
        ),
    )
    analysis = _resolve(
        artifacts=artifacts,
        symbols=symbols,
        catalog=catalog,
    )
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)

    assert any(
        fact.kind == "magento-template-block-binding"
        and fact.path.endswith("templates/cart.phtml")
        and fact.target == "Acme\\Checkout\\Block\\Cart"
        for fact in facts
    )
    method_calls = tuple(
        fact for fact in facts
        if fact.kind == "magento-template-block-method-call"
    )
    assert [fact.target for fact in method_calls] == [
        "Acme\\Checkout\\Block\\Cart::getCartId"
    ]
    assert any(
        fact.kind == "magento-template-view-model-binding"
        and fact.target == "Acme\\Checkout\\ViewModel\\Cart"
        for fact in facts
    )


def test_magento_config_php_order_is_authoritative_for_effective_merges():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return [
                'modules' => [
                    'Vendor_Zulu' => 1,
                    'Vendor_Alpha' => 1,
                ],
                'unrelated' => [
                    'Vendor_Unlisted' => 1,
                ],
            ];""",
            "app/code/Vendor/Zulu/etc/module.xml": """
                <config><module name="Vendor_Zulu" /></config>
            """,
            "app/code/Vendor/Alpha/etc/module.xml": """
                <config><module name="Vendor_Alpha" /></config>
            """,
            "app/code/Vendor/Unlisted/etc/module.xml": """
                <config><module name="Vendor_Unlisted" /></config>
            """,
            "app/code/Vendor/Zulu/etc/di.xml": r"""
                <config><preference for="Vendor\Api\Contract"
                    type="Vendor\Zulu\Implementation" /></config>
            """,
            "app/code/Vendor/Alpha/etc/di.xml": r"""
                <config><preference for="Vendor\Api\Contract"
                    type="Vendor\Alpha\Implementation" /></config>
            """,
            "app/code/Vendor/Unlisted/etc/di.xml": r"""
                <config><preference for="Vendor\Api\Contract"
                    type="Vendor\Unlisted\Implementation" /></config>
            """,
            "app/code/Vendor/Zulu/view/frontend/layout/vendor_page.xml": """
                <page><body><block name="enabled" /></body></page>
            """,
            "app/code/Vendor/Unlisted/view/frontend/layout/vendor_page.xml": """
                <page><body><block name="disabled" /></body></page>
            """,
            "app/code/Vendor/Zulu/view/adminhtml/ui_component/vendor_form.xml": """
                <form><field name="enabled"
                    component="Vendor_Zulu/js/enabled" /></form>
            """,
            "app/code/Vendor/Unlisted/view/adminhtml/ui_component/vendor_form.xml": """
                <form><field name="disabled"
                    component="Vendor_Unlisted/js/disabled" /></form>
            """,
            "app/code/Vendor/Zulu/view/frontend/requirejs-config.js": """
                var config = {
                    map: {'*': {enabledAlias: 'Vendor_Zulu/js/enabled'}}
                };
            """,
            "app/code/Vendor/Unlisted/view/frontend/requirejs-config.js": """
                var config = {
                    map: {'*': {disabledAlias: 'Vendor_Unlisted/js/disabled'}}
                };
            """,
            "app/code/Vendor/Zulu/etc/schema.graphqls": """
                type EnabledQuery { enabled: String }
            """,
            "app/code/Vendor/Unlisted/etc/schema.graphqls": """
                type DisabledQuery { disabled: String }
            """,
        },
        symbols=(),
    )
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)

    preference = next(
        fact
        for fact in facts
        if fact.kind == "magento-di-effective-preference"
        and fact.source == "Vendor\\Api\\Contract"
        and dict(fact.attributes).get("area") == "global"
    )
    assert preference.target == "Vendor\\Alpha\\Implementation"
    assert dict(preference.attributes)["module"] == "Vendor_Alpha"

    unlisted = next(
        fact
        for fact in facts
        if fact.kind == "magento-module"
        and fact.source == "Vendor_Unlisted"
    )
    assert unlisted.relation == "disabled"
    assert not any(
        fact.target == "Vendor\\Unlisted\\Implementation"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-module-effective-order"
        and fact.source == "Vendor_Alpha"
        and fact.relation == "configured-after"
        and fact.target == "Vendor_Zulu"
        for fact in facts
    )
    assert any(
        fact.path.startswith("app/code/Vendor/Zulu/view/")
        or fact.path == "app/code/Vendor/Zulu/etc/schema.graphqls"
        for fact in facts
    )
    assert not any(
        fact.path.startswith("app/code/Vendor/Unlisted/view/")
        or fact.path == "app/code/Vendor/Unlisted/etc/schema.graphqls"
        for fact in facts
    )


def test_vendorless_checkout_keeps_theme_overrides_for_configured_modules():
    layout_path = (
        "app/design/frontend/Acme/custom/Magento_Catalog/layout/"
        "catalog_category_view.xml"
    )
    template_path = (
        "app/design/frontend/Acme/custom/Magento_Catalog/templates/"
        "search-page-count.phtml"
    )
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Magento_Catalog' => 1,
                'Acme_Local' => 1,
            ]];""",
            # Keep one source-owned module so repository analysis has its normal
            # installed-module anchor. Magento_Catalog deliberately has no
            # vendor module.xml in this VCS-only checkout.
            "app/code/Acme/Local/etc/module.xml": """
                <config><module name="Acme_Local" /></config>
            """,
            "app/design/frontend/Acme/custom/theme.xml": """
                <theme><title>Custom</title></theme>
            """,
            "app/design/frontend/Acme/custom/registration.php": """<?php
                ComponentRegistrar::register(
                    ComponentRegistrar::THEME,
                    'frontend/Acme/custom',
                    __DIR__
                );
            """,
            layout_path: """
                <page><body><referenceContainer name="main.content">
                  <block name="search_page_count"
                    template="Magento_Catalog::search-page-count.phtml" />
                </referenceContainer></body></page>
            """,
            template_path: "<div>count</div>",
        },
        symbols=(),
    )
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )

    handle = next(
        fact for fact in facts
        if fact.kind == "magento-layout-handle"
        and fact.path == layout_path
    )
    block = next(
        fact for fact in facts
        if fact.kind == "magento-layout-block"
        and fact.path == layout_path
        and fact.target == "search_page_count"
    )
    assert dict(handle.attributes)["theme"] == "Acme/custom"
    assert template_path in block.related_paths


def test_vendorless_checkout_excludes_theme_overrides_for_disabled_modules():
    layout_path = (
        "app/design/frontend/Acme/custom/Magento_Catalog/layout/"
        "catalog_category_view.xml"
    )
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Magento_Catalog' => 0,
                'Acme_Local' => 1,
            ]];""",
            "app/code/Acme/Local/etc/module.xml": """
                <config><module name="Acme_Local" /></config>
            """,
            "app/design/frontend/Acme/custom/theme.xml": """
                <theme><title>Custom</title></theme>
            """,
            "app/design/frontend/Acme/custom/registration.php": """<?php
                ComponentRegistrar::register(
                    ComponentRegistrar::THEME,
                    'frontend/Acme/custom',
                    __DIR__
                );
            """,
            layout_path: """
                <page><body><block name="must_not_be_deployed" /></body></page>
            """,
        },
        symbols=(),
    )

    assert not any(
        fact.path == layout_path
        for packet in analysis.packets
        for fact in packet.facts
    )


def test_magento_plugin_priority_uses_sort_order_then_deployed_module_order():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Zulu' => 1,
                'Vendor_Alpha' => 1,
            ]];""",
            "app/code/Vendor/Zulu/etc/module.xml": """
                <config><module name="Vendor_Zulu" /></config>
            """,
            "app/code/Vendor/Alpha/etc/module.xml": """
                <config><module name="Vendor_Alpha" /></config>
            """,
            "app/code/Vendor/Zulu/etc/di.xml": r"""
                <config><type name="Vendor\Model\Service">
                    <plugin name="zulu" type="Vendor\Plugin\Zulu"
                        sortOrder="10" />
                    <plugin name="disabled" type="Vendor\Plugin\Disabled"
                        sortOrder="1" disabled="true" />
                </type></config>
            """,
            "app/code/Vendor/Alpha/etc/di.xml": r"""
                <config><type name="Vendor\Model\Service">
                    <plugin name="alpha" type="Vendor\Plugin\Alpha"
                        sortOrder="10" />
                    <plugin name="early" type="Vendor\Plugin\Early"
                        sortOrder="5" />
                </type></config>
            """,
        },
        symbols=(),
    )
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)
    priority = [
        fact
        for fact in facts
        if fact.kind == "magento-di-plugin-priority"
    ]

    assert [
        (fact.source, fact.relation, fact.target)
        for fact in priority
    ] == [
        (
            "Vendor\\Plugin\\Early",
            "prioritized-before",
            "Vendor\\Plugin\\Zulu",
        ),
        (
            "Vendor\\Plugin\\Zulu",
            "prioritized-before",
            "Vendor\\Plugin\\Alpha",
        ),
    ]
    assert all(
        "Vendor\\Plugin\\Disabled" not in {fact.source, fact.target}
        for fact in priority
    )
    alpha = next(
        fact
        for fact in facts
        if fact.kind == "magento-di-effective-plugin"
        and fact.target == "Vendor\\Plugin\\Alpha"
    )
    assert dict(alpha.attributes)["effectivePriorityPosition"] == "2"


def test_magento_reports_configured_order_that_violates_declared_sequence():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Dependent' => 1,
                'Vendor_Dependency' => 1,
            ]];""",
            "app/code/Vendor/Dependent/etc/module.xml": """
                <config><module name="Vendor_Dependent"><sequence>
                    <module name="Vendor_Dependency" />
                </sequence></module></config>
            """,
            "app/code/Vendor/Dependency/etc/module.xml": """
                <config><module name="Vendor_Dependency" /></config>
            """,
        },
        symbols=(),
    )

    mismatch = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-module-sequence-mismatch"
    )
    assert mismatch.source == "Vendor_Dependent"
    assert mismatch.relation == "configured-before-required-module"
    assert mismatch.target == "Vendor_Dependency"
    assert mismatch.path == "app/code/Vendor/Dependent/etc/module.xml"
    assert "app/etc/config.php" in mismatch.related_paths


def test_magento_route_before_after_selects_effective_controller():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Core' => 1,
                'Vendor_Override' => 1,
                'Vendor_After' => 1,
            ]];""",
            "app/code/Vendor/Core/etc/module.xml": """
                <config><module name="Vendor_Core" /></config>
            """,
            "app/code/Vendor/Override/etc/module.xml": """
                <config><module name="Vendor_Override" /></config>
            """,
            "app/code/Vendor/After/etc/module.xml": """
                <config><module name="Vendor_After" /></config>
            """,
            "app/code/Vendor/Core/etc/frontend/routes.xml": """
                <config><router id="standard">
                    <route id="customer" frontName="customer">
                        <module name="Vendor_Core" />
                    </route>
                </router></config>
            """,
            "app/code/Vendor/Override/etc/frontend/routes.xml": """
                <config><router id="standard">
                    <route id="customer">
                        <module name="Vendor_Override" before="Vendor_Core" />
                    </route>
                </router></config>
            """,
            "app/code/Vendor/After/etc/frontend/routes.xml": """
                <config><router id="standard">
                    <route id="customer">
                        <module name="Vendor_After" after="Vendor_Core" />
                    </route>
                </router></config>
            """,
            "app/code/Vendor/Core/view/base/layout/customer_account_login.xml": """
                <page><body><block name="shared" /></body></page>
            """,
            "app/code/Vendor/Core/view/frontend/layout/customer_account_login.xml": """
                <page><body><referenceBlock name="shared" /></body></page>
            """,
        },
        symbols=tuple(sorted((
            SymbolDefinition(
                "Vendor\\Core\\Controller\\Account\\Login",
                "class",
                "app/code/Vendor/Core/Controller/Account/Login.php",
                methods=("execute",),
            ),
            SymbolDefinition(
                "Vendor\\Override\\Controller\\Account\\Login",
                "class",
                "app/code/Vendor/Override/Controller/Account/Login.php",
                methods=("execute",),
            ),
            SymbolDefinition(
                "Vendor\\After\\Controller\\Account\\Login",
                "class",
                "app/code/Vendor/After/Controller/Account/Login.php",
                methods=("execute",),
            ),
        ))),
    )
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)

    assert {
        (fact.source, fact.relation, fact.target)
        for fact in facts
        if fact.kind == "magento-route-priority"
    } == {
        ("Vendor_Override", "searched-before", "Vendor_Core"),
        ("Vendor_Core", "searched-before", "Vendor_After"),
    }
    controller = next(
        fact for fact in facts
        if fact.kind == "magento-route-controller"
    )
    assert controller.source == "customer/account/login"
    assert (
        controller.target
        == "Vendor\\Override\\Controller\\Account\\Login"
    )
    assert {
        "app/code/Vendor/Core/view/base/layout/customer_account_login.xml",
        "app/code/Vendor/Core/view/frontend/layout/customer_account_login.xml",
    }.issubset(set(controller.related_paths))
    area_layout = next(
        fact
        for fact in facts
        if fact.kind == "magento-layout-handle"
        and fact.path.endswith(
            "view/frontend/layout/customer_account_login.xml"
        )
    )
    assert (
        "app/code/Vendor/Core/view/base/layout/customer_account_login.xml"
        in area_layout.related_paths
    )
    assert {
        fact.source
        for fact in facts
        if fact.kind == "magento-route-controller-shadowed"
    } == {
        "Vendor\\Core\\Controller\\Account\\Login",
        "Vendor\\After\\Controller\\Account\\Login",
    }


def test_magento_route_unresolved_and_self_constraints_match_runtime_ordering():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Magento_Backup' => 1,
            ]];""",
            "app/code/Magento/Backup/etc/module.xml": """
                <config><module name="Magento_Backup" /></config>
            """,
            "app/code/Magento/Backup/etc/adminhtml/routes.xml": """
                <config><router id="admin">
                    <route id="backup" frontName="backup">
                        <module name="Magento_Backup"
                            before="Magento_Backend" />
                    </route>
                    <route id="reports" frontName="reports">
                        <module name="Magento_Backup"
                            before="Magento_Backup" />
                    </route>
                </router></config>
            """,
            "vendor/magento/magento2-functional-testing-framework/etc/di.xml": """
                <!DOCTYPE config [
                    <!ENTITY harmless "test-only">
                ]>
                <config />
            """,
        },
        symbols=(),
    )

    routes = {
        fact.source: fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-effective-route"
    }
    assert set(routes) == {"backup", "reports"}
    assert {route.target for route in routes.values()} == {"Magento_Backup"}
    assert {
        dict(route.attributes)["area"]
        for route in routes.values()
    } == {"adminhtml"}


def test_magento_di_argument_type_changes_remove_stale_object_dependencies():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Core' => 1,
                'Vendor_Override' => 1,
            ]];""",
            "app/code/Vendor/Core/etc/module.xml": """
                <config><module name="Vendor_Core" /></config>
            """,
            "app/code/Vendor/Override/etc/module.xml": """
                <config><module name="Vendor_Override" /></config>
            """,
            "app/code/Vendor/Core/etc/di.xml": """
                <config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
                    <type name="Vendor\\Owner\\ArrayOwner">
                        <arguments>
                            <argument name="workers" xsi:type="array">
                                <item name="primary" xsi:type="object">Vendor\\Service\\OldPrimary</item>
                                <item name="group" xsi:type="array">
                                    <item name="child" xsi:type="object">Vendor\\Service\\OldNested</item>
                                </item>
                            </argument>
                        </arguments>
                    </type>
                    <type name="Vendor\\Owner\\DirectOwner">
                        <arguments>
                            <argument name="dependency" xsi:type="array">
                                <item name="old" xsi:type="object">Vendor\\Service\\OldArrayItem</item>
                            </argument>
                        </arguments>
                    </type>
                    <type name="Vendor\\Owner\\ScalarOwner">
                        <arguments>
                            <argument name="dependency" xsi:type="object">Vendor\\Service\\OldDirect</argument>
                        </arguments>
                    </type>
                </config>
            """,
            "app/code/Vendor/Override/etc/di.xml": """
                <config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
                    <type name="Vendor\\Owner\\ArrayOwner">
                        <arguments>
                            <argument name="workers" xsi:type="array">
                                <item name="primary" xsi:type="string">disabled</item>
                                <item name="group" xsi:type="array">
                                    <item name="child" xsi:type="object">Vendor\\Service\\NewNested</item>
                                </item>
                            </argument>
                        </arguments>
                    </type>
                    <type name="Vendor\\Owner\\DirectOwner">
                        <arguments>
                            <argument name="dependency" xsi:type="object">Vendor\\Service\\NewDirect</argument>
                        </arguments>
                    </type>
                    <type name="Vendor\\Owner\\ScalarOwner">
                        <arguments>
                            <argument name="dependency" xsi:type="string">not-an-object</argument>
                        </arguments>
                    </type>
                </config>
            """,
        },
        symbols=(),
    )

    arguments = {
        (
            fact.source,
            dict(fact.attributes)["argument"],
            dict(fact.attributes).get("item", ""),
            fact.target,
        )
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-di-argument"
    }
    assert arguments == {
        (
            "Vendor\\Owner\\ArrayOwner",
            "workers",
            "group/child",
            "Vendor\\Service\\NewNested",
        ),
        (
            "Vendor\\Owner\\DirectOwner",
            "dependency",
            "",
            "Vendor\\Service\\NewDirect",
        ),
    }


def test_magento_di_emits_effective_virtual_and_php_inherited_object_arguments():
    symbols = tuple(sorted((
        SymbolDefinition(
            "Vendor\\Contract\\RootInterface",
            "interface",
            "app/code/Vendor/Core/Api/RootInterface.php",
        ),
        SymbolDefinition(
            "Vendor\\Contract\\ChildInterface",
            "interface",
            "app/code/Vendor/Core/Api/ChildInterface.php",
            parents=("Vendor\\Contract\\RootInterface",),
            attributes=(
                (
                    "php-parent-interface:0000",
                    "Vendor\\Contract\\RootInterface",
                ),
            ),
        ),
        SymbolDefinition(
            "Vendor\\Model\\BasePool",
            "class",
            "app/code/Vendor/Core/Model/BasePool.php",
            parents=("Vendor\\Contract\\ChildInterface",),
            attributes=(
                (
                    "php-interface:0000",
                    "Vendor\\Contract\\ChildInterface",
                ),
            ),
        ),
        SymbolDefinition(
            "Vendor\\Model\\ConcretePool",
            "class",
            "app/code/Vendor/Core/Model/ConcretePool.php",
            parents=("Vendor\\Model\\BasePool",),
            attributes=(
                (
                    "php-parent-class",
                    "Vendor\\Model\\BasePool",
                ),
            ),
        ),
    )))
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Core' => 1,
                'Vendor_Custom' => 1,
            ]];""",
            "app/code/Vendor/Core/etc/module.xml": """
                <config><module name="Vendor_Core" /></config>
            """,
            "app/code/Vendor/Custom/etc/module.xml": """
                <config><module name="Vendor_Custom" /></config>
            """,
            "app/code/Vendor/Core/etc/di.xml": r"""
                <config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
                    <type name="Vendor\Contract\ChildInterface">
                        <arguments>
                            <argument name="interfaceValue" xsi:type="object">Vendor\Service\FromChildInterface</argument>
                        </arguments>
                    </type>
                    <type name="Vendor\Contract\RootInterface">
                        <arguments>
                            <argument name="interfaceValue" xsi:type="object">Vendor\Service\FromRootInterface</argument>
                        </arguments>
                    </type>
                    <type name="Vendor\Model\BasePool">
                        <arguments>
                            <argument name="direct" xsi:type="object">Vendor\Service\OldDirect</argument>
                            <argument name="workers" xsi:type="array">
                                <item name="keep" xsi:type="object">Vendor\Service\Keep</item>
                                <item name="replace" xsi:type="object">Vendor\Service\OldReplace</item>
                                <item name="group" xsi:type="array">
                                    <item name="child" xsi:type="object">Vendor\Service\OldNested</item>
                                    <item name="stays" xsi:type="object">Vendor\Service\Stays</item>
                                </item>
                            </argument>
                        </arguments>
                    </type>
                    <type name="Vendor\Model\ConcretePool">
                        <arguments>
                            <argument name="workers" xsi:type="array">
                                <item name="replace" xsi:type="object">Vendor\Service\ConcreteReplace</item>
                            </argument>
                        </arguments>
                    </type>
                    <virtualType name="Vendor\Pool\VirtualParent" type="Vendor\Model\ConcretePool">
                        <arguments>
                            <argument name="workers" xsi:type="array">
                                <item name="group" xsi:type="array">
                                    <item name="child" xsi:type="object">Vendor\Service\ParentNested</item>
                                </item>
                            </argument>
                        </arguments>
                    </virtualType>
                </config>
            """,
            "app/code/Vendor/Custom/etc/di.xml": r"""
                <config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
                    <virtualType name="Vendor\Pool\VirtualChild" type="Vendor\Pool\VirtualParent">
                        <arguments>
                            <argument name="direct" xsi:type="string">disabled</argument>
                            <argument name="workers" xsi:type="array">
                                <item name="replace" xsi:type="object">Vendor\Service\ChildReplace</item>
                                <item name="group" xsi:type="array">
                                    <item name="new" xsi:type="object">Vendor\Service\ChildNew</item>
                                </item>
                            </argument>
                        </arguments>
                    </virtualType>
                </config>
            """,
        },
        symbols=tuple(sorted(symbols)),
    )

    virtual_fact = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-di-virtual-type"
        and fact.source == "Vendor\\Pool\\VirtualChild"
    )
    assert virtual_fact.target == "Vendor\\Model\\ConcretePool"
    assert dict(virtual_fact.attributes)["configuredType"] == (
        "Vendor\\Pool\\VirtualParent"
    )

    child_arguments = {
        (
            dict(fact.attributes)["argument"],
            dict(fact.attributes).get("item", ""),
        ): (
            fact.target,
            dict(fact.attributes)["declaredFor"],
            dict(fact.attributes).get("inherited", ""),
        )
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-di-argument"
        and fact.source == "Vendor\\Pool\\VirtualChild"
    }
    assert child_arguments == {
        ("interfaceValue", ""): (
            "Vendor\\Service\\FromRootInterface",
            "Vendor\\Contract\\RootInterface",
            "true",
        ),
        ("workers", "group/child"): (
            "Vendor\\Service\\ParentNested",
            "Vendor\\Pool\\VirtualParent",
            "true",
        ),
        ("workers", "group/new"): (
            "Vendor\\Service\\ChildNew",
            "Vendor\\Pool\\VirtualChild",
            "",
        ),
        ("workers", "group/stays"): (
            "Vendor\\Service\\Stays",
            "Vendor\\Model\\BasePool",
            "true",
        ),
        ("workers", "keep"): (
            "Vendor\\Service\\Keep",
            "Vendor\\Model\\BasePool",
            "true",
        ),
        ("workers", "replace"): (
            "Vendor\\Service\\ChildReplace",
            "Vendor\\Pool\\VirtualChild",
            "",
        ),
    }
    assert all(
        target not in {
            "Vendor\\Service\\OldDirect",
            "Vendor\\Service\\OldNested",
            "Vendor\\Service\\OldReplace",
            "Vendor\\Service\\ConcreteReplace",
            "Vendor\\Service\\FromChildInterface",
        }
        for target, _, _ in child_arguments.values()
    )

    child_packet_paths = {
        path
        for packet in analysis.packets
        if packet.key.startswith(
            "global:argument:Vendor\\Pool\\VirtualChild:"
        )
        for path in packet.paths
    }
    assert {
        "app/code/Vendor/Core/Api/ChildInterface.php",
        "app/code/Vendor/Core/Api/RootInterface.php",
        "app/code/Vendor/Core/Model/BasePool.php",
        "app/code/Vendor/Core/Model/ConcretePool.php",
        "app/code/Vendor/Core/etc/di.xml",
        "app/code/Vendor/Custom/etc/di.xml",
    } <= child_packet_paths


def test_magento_di_virtual_type_argument_cycle_is_quarantined():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    started = plugin.start_repository_analysis("virtual-cycle")
    assert started.status is OutcomeStatus.HANDLED
    started.value.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted({
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Core' => 1,
            ]];""",
            "app/code/Vendor/Core/etc/module.xml": """
                <config><module name="Vendor_Core" /></config>
            """,
            "app/code/Vendor/Core/etc/di.xml": r"""
                <config>
                    <virtualType name="Vendor\Cycle\First" type="Vendor\Cycle\Second" />
                    <virtualType name="Vendor\Cycle\Second" type="Vendor\Cycle\First" />
                </config>
            """,
        }.items())
    ))

    outcome = started.value.finish(RepositoryAnalysis())

    assert outcome.status is OutcomeStatus.HANDLED
    assert [diagnostic.code for diagnostic in outcome.value.diagnostics] == [
        "magento-di-argument-inheritance-cycle"
    ]
    assert outcome.value.diagnostics[0].recoverable is True


def test_magento_base_view_configuration_is_related_to_area_variant():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Module' => 1,
            ]];""",
            "app/code/Vendor/Module/etc/module.xml": """
                <config><module name="Vendor_Module" /></config>
            """,
            "app/code/Vendor/Module/view/base/ui_component/customer_form.xml": """
                <form><fieldset name="shared" /></form>
            """,
            "app/code/Vendor/Module/view/adminhtml/ui_component/customer_form.xml": """
                <form><fieldset name="admin" /></form>
            """,
            "app/code/Vendor/Module/view/base/requirejs-config.js": """
                var config = {
                    map: {'*': {sharedAlias: 'Vendor_Module/js/shared'}}
                };
            """,
            "app/code/Vendor/Module/view/adminhtml/requirejs-config.js": """
                var config = {
                    map: {'*': {adminAlias: 'Vendor_Module/js/admin'}}
                };
            """,
        },
        symbols=(),
    )

    area_component = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-ui-component"
        and fact.path.endswith(
            "view/adminhtml/ui_component/customer_form.xml"
        )
    )
    assert (
        "app/code/Vendor/Module/view/base/ui_component/customer_form.xml"
        in area_component.related_paths
    )
    requirejs_maps = {
        (fact.path, fact.source, fact.target)
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-requirejs-map"
    }
    assert (
        "app/code/Vendor/Module/view/base/requirejs-config.js",
        "sharedAlias",
        "Vendor_Module/js/shared",
    ) in requirejs_maps
    assert (
        "app/code/Vendor/Module/view/adminhtml/requirejs-config.js",
        "adminAlias",
        "Vendor_Module/js/admin",
    ) in requirejs_maps
    # Distinct aliases are merged independently. Linking every area declaration
    # to every base config inflated context without proving an override.
    assert not any(
        fact.kind == "magento-requirejs-override"
        for packet in analysis.packets
        for fact in packet.facts
    )


def test_magento_requirejs_precedence_uses_modules_and_selected_theme_chain():
    module_a = "app/code/Vendor/A/view/base/requirejs-config.js"
    module_b = "app/code/Vendor/B/view/frontend/requirejs-config.js"
    parent = "app/design/frontend/Acme/parent/Vendor_A/requirejs-config.js"
    child = "app/design/frontend/Acme/child/requirejs-config.js"
    sibling = "app/design/frontend/Other/sibling/requirejs-config.js"
    disabled = "app/code/Vendor/Disabled/view/frontend/requirejs-config.js"
    disabled_theme_module = (
        "app/design/frontend/Acme/child/"
        "Vendor_Disabled/requirejs-config.js"
    )
    static_asset = (
        "app/code/Vendor/A/view/frontend/web/requirejs-config.js"
    )
    artifacts = {
        "app/etc/config.php": """<?php return ['modules' => [
            'Vendor_A' => 1,
            'Vendor_B' => 1,
        ]];""",
        "app/code/Vendor/A/etc/module.xml": """
            <config><module name="Vendor_A" /></config>
        """,
        "app/code/Vendor/B/etc/module.xml": """
            <config><module name="Vendor_B" /></config>
        """,
        "app/code/Vendor/Disabled/etc/module.xml": """
            <config><module name="Vendor_Disabled" /></config>
        """,
        module_a: """
            var config = {
                map: {'*': {checkoutAlias: 'Vendor_A/js/base'}}
            };
        """,
        module_b: """
            var config = {
                map: {
                    '*': {checkoutAlias: 'Vendor_B/js/frontend'},
                    'checkout/view': {
                        checkoutAlias: 'Vendor_B/js/scoped'
                    }
                }
            };
        """,
        disabled: """
            var config = {
                map: {'*': {checkoutAlias: 'Vendor_Disabled/js/value'}}
            };
        """,
        disabled_theme_module: """
            var config = {
                map: {
                    '*': {
                        checkoutAlias: 'Vendor_Disabled/js/theme-value'
                    }
                }
            };
        """,
        static_asset: """
            var config = {
                map: {'*': {checkoutAlias: 'Vendor_A/js/not-a-config'}}
            };
        """,
        "app/design/frontend/Acme/parent/theme.xml": """
            <theme><title>Parent</title></theme>
        """,
        "app/design/frontend/Acme/parent/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Acme/parent',
                __DIR__
            );
        """,
        parent: """
            var config = {
                map: {'*': {checkoutAlias: 'Vendor_A/js/parent'}}
            };
        """,
        "app/design/frontend/Acme/child/theme.xml": """
            <theme><title>Child</title><parent>Acme/parent</parent></theme>
        """,
        "app/design/frontend/Acme/child/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Acme/child',
                __DIR__
            );
        """,
        child: """
            var config = {
                map: {'*': {checkoutAlias: 'Vendor_A/js/child'}}
            };
        """,
        "app/design/frontend/Other/sibling/theme.xml": """
            <theme><title>Sibling</title></theme>
        """,
        "app/design/frontend/Other/sibling/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Other/sibling',
                __DIR__
            );
        """,
        sibling: """
            var config = {
                map: {'*': {checkoutAlias: 'Vendor_A/js/sibling'}}
            };
        """,
    }

    analysis = _resolve(artifacts=artifacts, symbols=())
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )
    requirejs_facts = tuple(
        fact
        for fact in facts
        if fact.kind.startswith("magento-requirejs-")
    )
    assert not any(fact.path == disabled for fact in requirejs_facts)
    assert not any(
        fact.path == disabled_theme_module
        for fact in requirejs_facts
    )
    assert not any(fact.path == static_asset for fact in requirejs_facts)
    assert any(fact.path == module_a for fact in requirejs_facts)
    assert any(fact.path == module_b for fact in requirejs_facts)

    precedence = tuple(
        fact
        for fact in facts
        if fact.kind == "magento-requirejs-override"
    )
    assert any(
        fact.source == module_a
        and fact.target == module_b
        and "theme" not in dict(fact.attributes)
        for fact in precedence
    )
    assert any(
        fact.source == parent
        and fact.target == child
        and dict(fact.attributes).get("theme") == "Acme/child"
        for fact in precedence
    )
    assert not any(
        {fact.source, fact.target} == {parent, sibling}
        for fact in precedence
    )

    scoped = next(
        fact
        for fact in requirejs_facts
        if fact.kind == "magento-requirejs-map"
        and fact.target == "Vendor_B/js/scoped"
    )
    assert dict(scoped.attributes)["mapScope"] == "checkout/view"
    assert not any(
        fact.kind == "magento-requirejs-override"
        and dict(fact.attributes).get("identity")
        == "checkout/view:checkoutAlias"
        and fact.source == module_a
        for fact in facts
    )


def test_magento_theme_context_follows_parent_chain_without_sibling_fanout():
    artifacts = {
        "app/etc/config.php": """<?php return ['modules' => [
            'Acme_Module' => 1,
        ]];""",
        "app/code/Acme/Module/etc/module.xml": """
            <config><module name="Acme_Module" /></config>
        """,
        "vendor/magento/theme-frontend-blank/theme.xml": """
            <theme><title>Blank</title></theme>
        """,
        "vendor/magento/theme-frontend-blank/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Magento/blank',
                __DIR__
            );
        """,
        "app/design/frontend/Acme/parent/theme.xml": """
            <theme><title>Parent</title><parent>Magento/blank</parent></theme>
        """,
        "app/design/frontend/Acme/parent/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Acme/parent',
                __DIR__
            );
        """,
        "app/design/frontend/Acme/child/theme.xml": """
            <theme><title>Child</title><parent>Acme/parent</parent></theme>
        """,
        "app/design/frontend/Acme/child/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Acme/child',
                __DIR__
            );
        """,
        "app/design/frontend/Other/sibling/theme.xml": """
            <theme><title>Sibling</title><parent>Magento/blank</parent></theme>
        """,
        "app/design/frontend/Other/sibling/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Other/sibling',
                __DIR__
            );
        """,
    }
    view_roots = (
        "app/code/Acme/Module/view/frontend",
        "app/design/frontend/Acme/parent/Acme_Module",
        "app/design/frontend/Acme/child/Acme_Module",
        "app/design/frontend/Other/sibling/Acme_Module",
    )
    for root in view_roots:
        artifacts[f"{root}/layout/acme_page.xml"] = """
            <page><body><block name="sample"
                template="Acme_Module::sample.phtml" /></body></page>
        """
        artifacts[f"{root}/templates/sample.phtml"] = (
            f"<div>{root}</div>"
        )
        artifacts[f"{root}/ui_component/acme_form.xml"] = """
            <form><field name="sample"
                component="Acme_Module/js/widget" /></form>
        """
        artifacts[f"{root}/web/js/widget.js"] = (
            "define([], function () { return {}; });"
        )

    for root in (
        "app/design/frontend/Acme/parent",
        "app/design/frontend/Acme/child",
        "app/design/frontend/Other/sibling",
    ):
        artifacts[f"{root}/requirejs-config.js"] = """
            var config = {
                map: {'*': {widgetAlias: 'Acme_Module/js/widget'}}
            };
        """

    child_base_override = (
        "app/design/frontend/Acme/child/Acme_Module/layout/"
        "override/base/acme_page.xml"
    )
    child_parent_override = (
        "app/design/frontend/Acme/child/Acme_Module/layout/"
        "override/theme/Acme/parent/acme_page.xml"
    )
    invalid_sibling_override = (
        "app/design/frontend/Acme/child/Acme_Module/layout/"
        "override/theme/Other/sibling/acme_page.xml"
    )
    artifacts[child_base_override] = """
        <page><body><referenceBlock name="module-replacement" /></body></page>
    """
    artifacts[child_parent_override] = """
        <page><body><referenceBlock name="parent-replacement" /></body></page>
    """
    artifacts[invalid_sibling_override] = """
        <page><body><referenceBlock name="invalid-replacement" /></body></page>
    """

    analysis = _resolve(artifacts=artifacts, symbols=())
    child_root = "app/design/frontend/Acme/child/Acme_Module"
    parent_root = "app/design/frontend/Acme/parent/Acme_Module"
    sibling_root = "app/design/frontend/Other/sibling/Acme_Module"
    module_root = "app/code/Acme/Module/view/frontend"

    child_handle = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-layout-handle"
        and fact.path == f"{child_root}/layout/acme_page.xml"
    )
    assert f"{module_root}/layout/acme_page.xml" not in child_handle.related_paths
    assert f"{parent_root}/layout/acme_page.xml" not in child_handle.related_paths
    assert child_base_override in child_handle.related_paths
    assert child_parent_override in child_handle.related_paths
    assert invalid_sibling_override not in child_handle.related_paths
    assert f"{sibling_root}/layout/acme_page.xml" not in child_handle.related_paths

    base_override_fact = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-layout-override"
        and fact.path == child_base_override
    )
    assert base_override_fact.target == "module:Acme_Module:acme_page.xml"
    assert (
        f"{module_root}/layout/acme_page.xml"
        in base_override_fact.related_paths
    )
    parent_override_fact = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-layout-override"
        and fact.path == child_parent_override
    )
    assert (
        parent_override_fact.target
        == "theme:Acme/parent:Acme_Module:acme_page.xml"
    )
    assert (
        f"{parent_root}/layout/acme_page.xml"
        in parent_override_fact.related_paths
    )
    invalid_override_fact = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-layout-override-unresolved"
        and fact.path == invalid_sibling_override
    )
    assert (
        invalid_override_fact.target
        == "theme:Other/sibling:Acme_Module:acme_page.xml"
    )
    assert not invalid_override_fact.related_paths

    child_block = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-layout-block"
        and fact.path == f"{child_root}/layout/acme_page.xml"
    )
    assert f"{module_root}/templates/sample.phtml" in child_block.related_paths
    assert f"{parent_root}/templates/sample.phtml" in child_block.related_paths
    assert f"{sibling_root}/templates/sample.phtml" not in child_block.related_paths

    child_component = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-ui-component"
        and fact.path == f"{child_root}/ui_component/acme_form.xml"
    )
    assert f"{module_root}/ui_component/acme_form.xml" in child_component.related_paths
    assert f"{parent_root}/ui_component/acme_form.xml" in child_component.related_paths
    assert (
        f"{sibling_root}/ui_component/acme_form.xml"
        not in child_component.related_paths
    )

    child_ui_asset = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-ui-relationship"
        and fact.path == f"{child_root}/ui_component/acme_form.xml"
    )
    assert f"{module_root}/web/js/widget.js" in child_ui_asset.related_paths
    assert f"{parent_root}/web/js/widget.js" in child_ui_asset.related_paths
    assert f"{sibling_root}/web/js/widget.js" not in child_ui_asset.related_paths

    child_requirejs = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-requirejs-map"
        and fact.path == "app/design/frontend/Acme/child/requirejs-config.js"
    )
    assert f"{module_root}/web/js/widget.js" in child_requirejs.related_paths
    assert f"{parent_root}/web/js/widget.js" in child_requirejs.related_paths
    assert f"{sibling_root}/web/js/widget.js" not in child_requirejs.related_paths


def test_magento_interceptor_facts_respect_php_applicability_and_direct_overrides():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Module' => 1,
            ]];""",
            "app/code/Vendor/Module/etc/module.xml": """
                <config><module name="Vendor_Module" /></config>
            """,
            "app/code/Vendor/Module/etc/di.xml": r"""
                <config>
                    <type name="Vendor\Model\Base">
                        <plugin name="guard" type="Vendor\Plugin\Guard" />
                    </type>
                    <type name="Vendor\Model\Child">
                        <plugin name="guard" type="Vendor\Plugin\Guard"
                            disabled="true" />
                    </type>
                    <type name="Vendor\Model\FinalTarget">
                        <plugin name="final_guard" type="Vendor\Plugin\Guard" />
                    </type>
                </config>
            """,
        },
        symbols=tuple(sorted((
            SymbolDefinition(
                "Vendor\\Model\\Base",
                "class",
                "app/code/Vendor/Module/Model/Base.php",
                methods=("save",),
                attributes=(("method:save:visibility", "public"),),
            ),
            SymbolDefinition(
                "Vendor\\Model\\Child",
                "class",
                "app/code/Vendor/Module/Model/Child.php",
                parents=("Vendor\\Model\\Base",),
            ),
            SymbolDefinition(
                "Vendor\\Model\\FinalTarget",
                "class",
                "app/code/Vendor/Module/Model/FinalTarget.php",
                methods=("save",),
                attributes=(
                    ("method:save:visibility", "public"),
                    ("type:final", "true"),
                ),
            ),
            SymbolDefinition(
                "Vendor\\Plugin\\Guard",
                "class",
                "app/code/Vendor/Module/Plugin/Guard.php",
                methods=("beforeSave",),
                attributes=(("method:beforeSave:visibility", "public"),),
            ),
        ))),
    )
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)

    assert any(
        fact.kind == "magento-di-effective-plugin"
        and fact.source == "Vendor\\Model\\Child"
        and fact.relation == "disables-interceptor"
        for fact in facts
    )
    assert not any(
        fact.kind == "magento-di-inherited-plugin"
        and fact.source == "Vendor\\Model\\Child"
        and fact.relation == "intercepted-by"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-intercepted-method"
        and fact.target == "Vendor\\Model\\Base::save"
        for fact in facts
    )
    inapplicable = next(
        fact
        for fact in facts
        if fact.kind == "magento-interceptor-inapplicable"
    )
    assert inapplicable.target == "Vendor\\Model\\FinalTarget::save"
    assert dict(inapplicable.attributes)["reason"] == "final-class"
    assert not any(
        fact.kind == "magento-intercepted-method"
        and fact.target == "Vendor\\Model\\FinalTarget::save"
        for fact in facts
    )


def test_magento_message_queue_resolves_effective_publisher_binding_and_consumer():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Base' => 1,
                'Vendor_Override' => 1,
            ]];""",
            "app/code/Vendor/Base/etc/module.xml": """
                <config><module name="Vendor_Base" /></config>
            """,
            "app/code/Vendor/Override/etc/module.xml": """
                <config><module name="Vendor_Override" /></config>
            """,
            "app/code/Vendor/Base/etc/communication.xml": r"""
                <config>
                    <topic name="orders.created" request="string">
                        <handler name="base"
                            type="Vendor\Base\Model\TopicHandler"
                            method="process" />
                    </topic>
                </config>
            """,
            "app/code/Vendor/Base/etc/queue_publisher.xml": """
                <config>
                    <publisher topic="orders.created">
                        <connection name="amqp" exchange="orders" />
                    </publisher>
                </config>
            """,
            "app/code/Vendor/Base/etc/queue_topology.xml": """
                <config>
                    <exchange name="orders" connection="amqp">
                        <binding id="wildcard" topic="orders.#"
                            destinationType="queue"
                            destination="orders.all" />
                    </exchange>
                </config>
            """,
            "app/code/Vendor/Override/etc/queue_topology.xml": """
                <config>
                    <exchange name="orders" connection="amqp">
                        <binding id="disable-wildcard" topic="orders.#"
                            destinationType="queue"
                            destination="orders.all" disabled="true" />
                        <binding id="created" topic="orders.created"
                            destinationType="queue"
                            destination="orders.created" />
                    </exchange>
                </config>
            """,
            "app/code/Vendor/Override/etc/queue_consumer.xml": r"""
                <config>
                    <consumer name="orders.created.consumer"
                        queue="orders.created" connection="amqp"
                        handler="Vendor\Override\Model\Consumer::process" />
                </config>
            """,
        },
        symbols=(
            SymbolDefinition(
                "Vendor\\Base\\Model\\TopicHandler",
                "class",
                "app/code/Vendor/Base/Model/TopicHandler.php",
            ),
            SymbolDefinition(
                "Vendor\\Override\\Model\\Consumer",
                "class",
                "app/code/Vendor/Override/Model/Consumer.php",
            ),
        ),
    )
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)

    effective_route = next(
        fact
        for fact in facts
        if fact.kind == "magento-message-effective-route"
        and fact.source == "orders.created"
    )
    assert effective_route.target == "orders.created"
    assert dict(effective_route.attributes) == {
        "connectionResolved": "True",
        "exchange": "orders",
        "publisherConnection": "amqp",
        "topologyConnection": "amqp",
    }
    assert {
        "app/code/Vendor/Base/etc/communication.xml",
        "app/code/Vendor/Base/etc/queue_publisher.xml",
        "app/code/Vendor/Base/etc/queue_topology.xml",
        "app/code/Vendor/Override/etc/queue_topology.xml",
    }.issubset(set(effective_route.related_paths))

    effective_consumer = next(
        fact
        for fact in facts
        if fact.kind == "magento-message-effective-consumer"
        and fact.source == "orders.created"
    )
    assert effective_consumer.target == "orders.created.consumer"
    assert (
        "app/code/Vendor/Override/Model/Consumer.php"
        in effective_consumer.related_paths
    )
    effective_handler = next(
        fact
        for fact in facts
        if fact.kind == "magento-message-effective-handler"
        and fact.source == "orders.created"
    )
    assert effective_handler.target == (
        "Vendor\\Override\\Model\\Consumer::process"
    )
    assert dict(effective_handler.attributes) == {
        "connectionResolved": "True",
        "consumer": "orders.created.consumer",
        "consumerImplementationResolved": "True",
        "consumerInstance": (
            "Magento\\Framework\\MessageQueue\\Consumer"
        ),
        "handlerSelection": "override",
        "handlerSource": "queue-consumer",
        "handlerValid": "True",
    }
    assert {
        "app/code/Vendor/Base/etc/communication.xml",
        "app/code/Vendor/Base/etc/queue_publisher.xml",
        "app/code/Vendor/Override/etc/queue_topology.xml",
        "app/code/Vendor/Override/etc/queue_consumer.xml",
        "app/code/Vendor/Override/Model/Consumer.php",
    }.issubset(set(effective_handler.related_paths))
    assert not any(
        fact.kind == "magento-message-effective-handler"
        and fact.target
        == "Vendor\\Base\\Model\\TopicHandler::process"
        for fact in facts
    )
    assert not any(
        fact.kind == "magento-message-effective-route"
        and fact.target == "orders.all"
        for fact in facts
    )


def _resolve_message_queue(
    *,
    communication_handlers: str = "",
    consumer_attributes: str = "",
    exchange_name: str = "orders",
    include_publisher: bool = True,
    topology_connection: str = "amqp",
    symbols: tuple[SymbolDefinition, ...] = (),
):
    topology_connection_attribute = (
        f' connection="{topology_connection}"'
        if topology_connection
        else ""
    )
    artifacts = {
        "app/etc/config.php": """<?php return ['modules' => [
            'Vendor_Queue' => 1,
        ]];""",
        "app/code/Vendor/Queue/etc/module.xml": """
            <config><module name="Vendor_Queue" /></config>
        """,
        "app/code/Vendor/Queue/etc/communication.xml": f"""
            <config>
                <topic name="orders.created" request="string">
                    {communication_handlers}
                </topic>
            </config>
        """,
        "app/code/Vendor/Queue/etc/queue_topology.xml": f"""
            <config>
                <exchange name="{exchange_name}"{
                    topology_connection_attribute
                }>
                    <binding id="created" topic="orders.created"
                        destination="orders.created" />
                </exchange>
            </config>
        """,
        "app/code/Vendor/Queue/etc/queue_consumer.xml": f"""
            <config>
                <consumer name="orders.consumer"
                    queue="orders.created" connection="amqp"
                    {consumer_attributes} />
            </config>
        """,
    }
    if include_publisher:
        artifacts[
            "app/code/Vendor/Queue/etc/queue_publisher.xml"
        ] = f"""
            <config>
                <publisher topic="orders.created">
                    <connection name="amqp"
                        exchange="{exchange_name}" />
                </publisher>
            </config>
        """
    return _resolve(
        artifacts=artifacts,
        symbols=tuple(sorted(symbols)),
    )


def test_magento_message_queue_uses_communication_handler_as_default_fallback():
    analysis = _resolve_message_queue(
        communication_handlers=r"""
            <handler name="enabled"
                type="Vendor\Queue\Model\EnabledHandler"
                method="process" />
            <handler name="disabled"
                type="Vendor\Queue\Model\DisabledHandler"
                method="process" disabled="true" />
        """,
        symbols=(
            SymbolDefinition(
                "Vendor\\Queue\\Model\\EnabledHandler",
                "class",
                "app/code/Vendor/Queue/Model/EnabledHandler.php",
            ),
            SymbolDefinition(
                "Vendor\\Queue\\Model\\DisabledHandler",
                "class",
                "app/code/Vendor/Queue/Model/DisabledHandler.php",
            ),
        ),
    )
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )
    handlers = [
        fact
        for fact in facts
        if fact.kind == "magento-message-effective-handler"
    ]

    assert [fact.target for fact in handlers] == [
        "Vendor\\Queue\\Model\\EnabledHandler::process"
    ]
    assert dict(handlers[0].attributes)["handlerSource"] == (
        "communication"
    )
    assert dict(handlers[0].attributes)["handlerSelection"] == "fallback"
    assert (
        "app/code/Vendor/Queue/Model/EnabledHandler.php"
        in handlers[0].related_paths
    )
    assert not any(
        fact.kind == "magento-message-effective-handler"
        and "DisabledHandler" in fact.target
        for fact in facts
    )


def test_magento_inbound_queue_resolves_without_local_publisher():
    analysis = _resolve_message_queue(
        include_publisher=False,
        consumer_attributes=(
            r'handler="Vendor\Queue\Model\QueueHandler::process"'
        ),
        symbols=(
            SymbolDefinition(
                "Vendor\\Queue\\Model\\QueueHandler",
                "class",
                "app/code/Vendor/Queue/Model/QueueHandler.php",
            ),
        ),
    )
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )

    consumer = next(
        fact
        for fact in facts
        if fact.kind == "magento-message-effective-consumer"
    )
    handler = next(
        fact
        for fact in facts
        if fact.kind == "magento-message-effective-handler"
    )
    assert consumer.source == "orders.created"
    assert consumer.target == "orders.consumer"
    assert "publisherConnection" not in dict(consumer.attributes)
    assert handler.target == (
        "Vendor\\Queue\\Model\\QueueHandler::process"
    )
    assert {
        "app/code/Vendor/Queue/etc/communication.xml",
        "app/code/Vendor/Queue/etc/queue_topology.xml",
        "app/code/Vendor/Queue/etc/queue_consumer.xml",
        "app/code/Vendor/Queue/Model/QueueHandler.php",
    }.issubset(set(handler.related_paths))
    assert any(
        fact.kind == "magento-message-route-unresolved"
        and fact.relation == "has-no-publisher"
        for fact in facts
    )


def test_magento_inbound_queue_keeps_unresolved_connection_as_candidate():
    analysis = _resolve_message_queue(
        include_publisher=False,
        topology_connection="",
        consumer_attributes=(
            r'handler="Vendor\Queue\Model\QueueHandler::process"'
        ),
    )
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )

    candidate = next(
        fact
        for fact in facts
        if fact.kind == "magento-message-handler-candidate"
    )
    assert candidate.target == (
        "Vendor\\Queue\\Model\\QueueHandler::process"
    )
    assert dict(candidate.attributes)["connectionResolved"] == "False"
    assert not any(
        fact.kind == "magento-message-effective-handler"
        for fact in facts
    )


def test_magento_empty_exchange_name_is_preserved_by_runtime_routing():
    analysis = _resolve_message_queue(
        exchange_name="",
        consumer_attributes=(
            r'handler="Vendor\Queue\Model\QueueHandler::process"'
        ),
    )
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )

    route = next(
        fact
        for fact in facts
        if fact.kind == "magento-message-effective-route"
    )
    handler = next(
        fact
        for fact in facts
        if fact.kind == "magento-message-effective-handler"
    )
    assert route.target == "orders.created"
    assert dict(route.attributes)["exchange"] == (
        "broker-default-exchange"
    )
    assert handler.target == (
        "Vendor\\Queue\\Model\\QueueHandler::process"
    )
    assert not any(
        fact.kind == "magento-message-route-unresolved"
        and fact.relation == "has-empty-publisher-exchange"
        for fact in facts
    )


def test_magento_mass_consumer_uses_both_handler_sources():
    analysis = _resolve_message_queue(
        communication_handlers=r"""
            <handler name="topic"
                type="Vendor\Queue\Model\TopicHandler"
                method="process" />
        """,
        consumer_attributes=(
            r'consumerInstance="Magento\AsynchronousOperations'
            r'\Model\MassConsumer" '
            r'handler="Vendor\Queue\Model\QueueHandler::process"'
        ),
        symbols=(
            SymbolDefinition(
                "Vendor\\Queue\\Model\\TopicHandler",
                "class",
                "app/code/Vendor/Queue/Model/TopicHandler.php",
            ),
            SymbolDefinition(
                "Vendor\\Queue\\Model\\QueueHandler",
                "class",
                "app/code/Vendor/Queue/Model/QueueHandler.php",
            ),
        ),
    )
    handlers = [
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-message-effective-handler"
    ]

    assert {
        (
            fact.target,
            dict(fact.attributes)["handlerSource"],
            dict(fact.attributes)["handlerSelection"],
        )
        for fact in handlers
    } == {
        (
            "Vendor\\Queue\\Model\\TopicHandler::process",
            "communication",
            "additive",
        ),
        (
            "Vendor\\Queue\\Model\\QueueHandler::process",
            "queue-consumer",
            "additive",
        ),
    }


def test_magento_custom_consumer_keeps_handler_use_as_candidates():
    analysis = _resolve_message_queue(
        communication_handlers=r"""
            <handler name="topic"
                type="Vendor\Queue\Model\TopicHandler"
                method="process" />
        """,
        consumer_attributes=(
            r'consumerInstance="Vendor\Queue\Model\CustomConsumer" '
            r'handler="Vendor\Queue\Model\QueueHandler::process"'
        ),
        symbols=(
            SymbolDefinition(
                "Vendor\\Queue\\Model\\CustomConsumer",
                "class",
                "app/code/Vendor/Queue/Model/CustomConsumer.php",
            ),
            SymbolDefinition(
                "Vendor\\Queue\\Model\\TopicHandler",
                "class",
                "app/code/Vendor/Queue/Model/TopicHandler.php",
            ),
            SymbolDefinition(
                "Vendor\\Queue\\Model\\QueueHandler",
                "class",
                "app/code/Vendor/Queue/Model/QueueHandler.php",
            ),
        ),
    )
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )
    candidates = [
        fact
        for fact in facts
        if fact.kind == "magento-message-handler-candidate"
    ]

    assert {
        fact.target for fact in candidates
    } == {
        "Vendor\\Queue\\Model\\TopicHandler::process",
        "Vendor\\Queue\\Model\\QueueHandler::process",
    }
    assert all(
        dict(fact.attributes)["consumerImplementationResolved"] == "False"
        for fact in candidates
    )
    dependent = next(
        fact
        for fact in facts
        if fact.kind
        == "magento-message-handler-resolution-dependent"
    )
    assert dependent.target == "Vendor\\Queue\\Model\\CustomConsumer"
    assert (
        "app/code/Vendor/Queue/Model/CustomConsumer.php"
        in dependent.related_paths
    )
    assert not any(
        fact.kind == "magento-message-effective-handler"
        for fact in facts
    )


def test_magento_standard_consumer_preserves_missing_handler_as_unresolved():
    analysis = _resolve_message_queue()
    unresolved = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-message-handler-unresolved"
        and fact.relation == "has-no-configured-handler"
    )

    assert unresolved.target == "orders.consumer"
    assert dict(unresolved.attributes)["reason"] == "no-enabled-handler"


def test_magento_message_handler_is_replaced_after_snapshot_overlay():
    symbols = tuple(sorted((
        SymbolDefinition(
            "Vendor\\Queue\\Model\\CommunicationHandler",
            "class",
            "app/code/Vendor/Queue/Model/CommunicationHandler.php",
        ),
        SymbolDefinition(
            "Vendor\\Queue\\Model\\NewHandler",
            "class",
            "app/code/Vendor/Queue/Model/NewHandler.php",
        ),
        SymbolDefinition(
            "Vendor\\Queue\\Model\\OldHandler",
            "class",
            "app/code/Vendor/Queue/Model/OldHandler.php",
        ),
    )))
    base = _resolve_message_queue(
        communication_handlers=r"""
            <handler name="topic"
                type="Vendor\Queue\Model\CommunicationHandler"
                method="process" />
        """,
        include_publisher=False,
        consumer_attributes=(
            r'handler="Vendor\Queue\Model\OldHandler::process"'
        ),
        symbols=symbols,
    )
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    restored = plugin.restore_repository_analysis(
        "fedcba9876543210",
        base.snapshots,
    )
    assert restored.status is OutcomeStatus.HANDLED
    restored.value.ingest((FileArtifact(
        "app/code/Vendor/Queue/etc/queue_consumer.xml",
        r"""
            <config>
                <consumer name="orders.consumer"
                    queue="orders.created" connection="amqp"
                    handler="Vendor\Queue\Model\NewHandler::process" />
            </config>
        """,
    ),))

    outcome = restored.value.finish(
        RepositoryAnalysis(symbols=symbols)
    )
    assert outcome.status is OutcomeStatus.HANDLED
    handlers = [
        fact
        for packet in outcome.value.packets
        for fact in packet.facts
        if fact.kind == "magento-message-effective-handler"
    ]

    assert [fact.target for fact in handlers] == [
        "Vendor\\Queue\\Model\\NewHandler::process"
    ]
    assert (
        "app/code/Vendor/Queue/Model/NewHandler.php"
        in handlers[0].related_paths
    )
    assert (
        "app/code/Vendor/Queue/Model/OldHandler.php"
        not in handlers[0].related_paths
    )
    assert not any(
        "OldHandler" in fact.target for fact in handlers
    )


def test_magento_message_queue_marks_deployment_connection_candidates():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Queue' => 1,
            ]];""",
            "app/code/Vendor/Queue/etc/module.xml": """
                <config><module name="Vendor_Queue" /></config>
            """,
            "app/code/Vendor/Queue/etc/communication.xml": """
                <config><topic name="orders.created" request="string" /></config>
            """,
            "app/code/Vendor/Queue/etc/queue_publisher.xml": """
                <config><publisher topic="orders.created" /></config>
            """,
            "app/code/Vendor/Queue/etc/queue_topology.xml": """
                <config>
                    <exchange name="magento" connection="amqp">
                        <binding id="created" topic="orders.*"
                            destination="orders.created" />
                    </exchange>
                </config>
            """,
        },
        symbols=(),
    )
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)

    candidate = next(
        fact
        for fact in facts
        if fact.kind == "magento-message-route-candidate"
    )
    assert candidate.source == "orders.created"
    assert candidate.target == "orders.created"
    assert dict(candidate.attributes)["publisherConnection"] == (
        "deployment-default"
    )
    assert dict(candidate.attributes)["topologyConnection"] == "amqp"
    assert dict(candidate.attributes)["connectionResolved"] == "False"


def test_magento_message_queue_preserves_empty_exchange_and_queue_evidence():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Queue' => 1,
            ]];""",
            "app/code/Vendor/Queue/etc/module.xml": """
                <config><module name="Vendor_Queue" /></config>
            """,
            "app/code/Vendor/Queue/etc/communication.xml": """
                <config><topic name="orders.created" request="string" /></config>
            """,
            "app/code/Vendor/Queue/etc/queue_publisher.xml": """
                <config><publisher topic="orders.created">
                    <connection name="amqp" exchange="" />
                </publisher></config>
            """,
            "app/code/Vendor/Queue/etc/queue_consumer.xml": """
                <config><consumer name="orders.consumer" queue="" /></config>
            """,
        },
        symbols=(),
    )
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)

    assert any(
        fact.kind == "magento-message-publisher"
        and fact.relation == "publishes-through"
        and fact.target == "broker-default-exchange"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-message-route-unresolved"
        and fact.relation == "has-no-matching-binding"
        and fact.target == "broker-default-exchange"
        for fact in facts
    )
    assert any(
        fact.kind == "magento-message-consumer-invalid"
        and fact.relation == "has-empty-queue"
        and fact.target == "orders.consumer"
        for fact in facts
    )


def test_magento_extension_attribute_joins_use_effective_merged_configuration():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Base' => 1,
                'Vendor_Override' => 1,
            ]];""",
            "app/code/Vendor/Base/etc/module.xml": """
                <config><module name="Vendor_Base" /></config>
            """,
            "app/code/Vendor/Override/etc/module.xml": """
                <config><module name="Vendor_Override"><sequence>
                    <module name="Vendor_Base" />
                </sequence></module></config>
            """,
            "app/code/Vendor/Base/etc/extension_attributes.xml": r"""
                <config>
                  <extension_attributes for="Vendor\Api\OrderInterface">
                    <attribute code="account"
                        type="Vendor\Api\Data\LegacyAccountInterface">
                      <join reference_table="legacy_account"
                          reference_field="entity_id"
                          join_on_field="customer_id">
                        <field>legacy_code</field>
                      </join>
                    </attribute>
                  </extension_attributes>
                </config>
            """,
            "app/code/Vendor/Override/etc/extension_attributes.xml": r"""
                <config>
                  <extension_attributes for="Vendor\Api\OrderInterface">
                    <attribute code="account"
                        type="Vendor\Api\Data\AccountInterface">
                      <resources>
                        <resource ref="Vendor_Override::account" />
                      </resources>
                      <join reference_table="customer_account"
                          reference_field="customer_id"
                          join_on_field="customer_id">
                        <field>label</field>
                        <field column="customer_group_code">code</field>
                      </join>
                    </attribute>
                  </extension_attributes>
                </config>
            """,
            "app/code/Vendor/Override/etc/db_schema.xml": """
                <schema xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
                  <table name="customer_account">
                    <column xsi:type="int" name="customer_id" />
                    <column xsi:type="text" name="label" />
                    <column xsi:type="text" name="customer_group_code" />
                  </table>
                </schema>
            """,
        },
        symbols=(
            SymbolDefinition(
                "Vendor\\Api\\Data\\AccountInterface",
                "interface",
                "app/code/Vendor/Api/Data/AccountInterface.php",
            ),
            SymbolDefinition(
                "Vendor\\Api\\OrderInterface",
                "interface",
                "app/code/Vendor/Api/OrderInterface.php",
            ),
        ),
    )
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)

    attributes = [
        fact for fact in facts
        if fact.kind == "magento-extension-attribute"
        and fact.source == "Vendor\\Api\\OrderInterface"
        and fact.target == "account"
    ]
    assert len(attributes) == 1
    assert dict(attributes[0].attributes)["dataType"] == (
        "Vendor\\Api\\Data\\AccountInterface"
    )
    join = next(
        fact for fact in facts
        if fact.kind == "magento-extension-attribute-join"
    )
    assert join.target == "customer_account"
    assert dict(join.attributes) == {
        "dataType": "Vendor\\Api\\Data\\AccountInterface",
        "joinOnField": "customer_id",
        "referenceField": "customer_id",
        "tableAlias": "extension_attribute_account",
    }
    fields = {
        (fact.target, dict(fact.attributes)["property"])
        for fact in facts
        if fact.kind == "magento-extension-attribute-join-field"
    }
    assert fields == {
        ("customer_account.label", "label"),
        ("customer_account.customer_group_code", "code"),
    }
    assert {
        "app/code/Vendor/Base/etc/extension_attributes.xml",
        "app/code/Vendor/Override/etc/extension_attributes.xml",
        "app/code/Vendor/Override/etc/db_schema.xml",
        "app/code/Vendor/Api/OrderInterface.php",
        "app/code/Vendor/Api/Data/AccountInterface.php",
    }.issubset(set(join.related_paths))


def test_magento_extension_attribute_array_join_is_not_effective():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Module' => 1,
            ]];""",
            "app/code/Vendor/Module/etc/module.xml": """
                <config><module name="Vendor_Module" /></config>
            """,
            "app/code/Vendor/Module/etc/extension_attributes.xml": r"""
                <config>
                  <extension_attributes for="Vendor\Api\OrderInterface">
                    <attribute code="accounts"
                        type="Vendor\Api\Data\AccountInterface[]">
                      <join reference_table="customer_account"
                          reference_field="customer_id"
                          join_on_field="customer_id">
                        <field>label</field>
                      </join>
                    </attribute>
                  </extension_attributes>
                </config>
            """,
        },
        symbols=(),
    )
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)

    assert any(
        fact.kind == "magento-extension-attribute-join-inapplicable"
        and fact.relation == "cannot-hydrate-array-type"
        for fact in facts
    )
    assert not any(
        fact.kind == "magento-extension-attribute-join"
        for fact in facts
    )


def test_magento_declarative_schema_emits_only_effective_elements_and_removals():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_Base' => 1,
                'Vendor_Override' => 1,
            ]];""",
            "app/code/Vendor/Base/etc/module.xml": """
                <config><module name="Vendor_Base" /></config>
            """,
            "app/code/Vendor/Override/etc/module.xml": """
                <config><module name="Vendor_Override"><sequence>
                    <module name="Vendor_Base" />
                </sequence></module></config>
            """,
            "app/code/Vendor/Base/etc/db_schema.xml": """
                <schema xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
                  <table name="vendor_order" resource="default">
                    <column xsi:type="int" name="entity_id" />
                    <column xsi:type="text" name="obsolete" />
                    <index referenceId="VENDOR_ORDER_OBSOLETE">
                      <column name="obsolete" />
                    </index>
                    <constraint xsi:type="foreign"
                        referenceId="VENDOR_ORDER_CUSTOMER"
                        column="customer_id"
                        referenceTable="customer_entity"
                        referenceColumn="entity_id" />
                  </table>
                </schema>
            """,
            "app/code/Vendor/Override/etc/db_schema.xml": """
                <schema xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
                  <table name="vendor_order" engine="innodb">
                    <column xsi:type="bigint" name="entity_id" />
                    <column name="obsolete" disabled="true" />
                    <index referenceId="VENDOR_ORDER_OBSOLETE"
                        disabled="true" />
                  </table>
                  <table name="customer_entity" disabled="true" />
                </schema>
            """,
            "app/code/Vendor/Override/etc/db_schema_whitelist.json": """
                {
                  "vendor_order": {
                    "column": {"obsolete": true}
                  },
                  "customer_entity": {
                    "column": {"entity_id": true}
                  }
                }
            """,
        },
        symbols=(),
    )
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)

    entity = next(
        fact for fact in facts
        if fact.kind == "magento-db-column"
        and fact.target == "entity_id"
    )
    assert dict(entity.attributes)["dataType"] == "bigint"
    assert not any(
        fact.kind == "magento-db-column"
        and fact.target == "obsolete"
        for fact in facts
    )
    removals = {
        (fact.relation, fact.target): dict(fact.attributes)
        for fact in facts
        if fact.kind == "magento-db-removal"
    }
    assert removals[("disables-column", "obsolete")][
        "destructiveOperationAllowed"
    ] == "true"
    assert removals[("disables-index", "VENDOR_ORDER_OBSOLETE")][
        "destructiveOperationAllowed"
    ] == "false"
    assert removals[("disables-table", "customer_entity")][
        "destructiveOperationAllowed"
    ] == "true"
    invalid_fk = next(
        fact for fact in facts
        if fact.kind == "magento-db-foreign-key-invalid"
    )
    assert invalid_fk.target == "customer_entity"
    assert invalid_fk.relation == "references-disabled-table"


def test_magento_repository_analysis_connects_entrypoints_and_resources():
    analysis = _resolve()
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)

    expected_kinds = {
        "magento-route-controller",
        "magento-layout-block",
        "magento-layout-acl-condition",
        "magento-layout-config-condition",
        "magento-webapi-route",
        "magento-webapi-acl",
        "magento-cron-job",
        "magento-message-handler",
        "magento-message-consumer",
        "magento-db-table",
        "magento-db-foreign-key",
        "magento-graphql-resolver",
        "magento-extension-attribute",
        "magento-theme",
        "magento-theme-parent",
        "magento-ui-component",
        "magento-ui-php-class",
        "magento-ui-relationship",
        "magento-indexer-class",
        "magento-indexer-dependency",
        "magento-mview-class",
        "magento-mview-subscription",
        "magento-system-config-field",
        "magento-system-config-model",
        "magento-system-config-acl",
        "magento-system-config-dependency",
        "magento-system-config-default",
        "magento-system-config-consumer",
        "magento-admin-menu-item",
        "magento-admin-menu-parent",
        "magento-admin-menu-acl",
        "magento-admin-menu-config-condition",
        "magento-admin-menu-module-condition",
        "magento-admin-menu-action",
        "magento-config-class-reference",
    }
    assert expected_kinds <= {fact.kind for fact in facts}
    route_packet = next(
        packet for packet in analysis.packets
        if packet.kind == "magento-route"
        and packet.key == "frontend:checkout"
    )
    assert "app/code/Acme/Checkout/Controller/Cart/Save.php" in route_packet.paths
    assert "app/code/Acme/Checkout/view/frontend/layout/checkout_cart_save.xml" in route_packet.paths
    assert "app/design/frontend/Acme/custom/Acme_Checkout/layout/checkout_cart_save.xml" in route_packet.paths
    layout_packet = next(
        packet for packet in analysis.packets
        if packet.kind == "magento-layout" and packet.key == "frontend:checkout_cart_save"
    )
    assert "app/code/Acme/Checkout/Block/Cart.php" in layout_packet.paths
    assert (
        "app/code/Acme/Checkout/etc/adminhtml/system.xml"
        in layout_packet.paths
    )
    assert "app/code/Acme/Checkout/etc/config.xml" in layout_packet.paths
    assert any(
        fact.kind == "magento-layout-config-condition"
        and fact.relation == "visible-when-config-enabled"
        and fact.target == "acme/cart/runtime_mode"
        for fact in layout_packet.facts
    )
    assert any(
        fact.kind == "magento-layout-acl-condition"
        and fact.relation == "visible-to-resource"
        and fact.target == "Acme_Checkout::cart"
        for fact in layout_packet.facts
    )
    assert "app/code/Acme/Checkout/etc/acl.xml" in layout_packet.paths
    assert "app/code/Acme/Checkout/view/frontend/templates/cart.phtml" in layout_packet.paths
    assert "app/design/frontend/Acme/custom/Acme_Checkout/templates/cart.phtml" in layout_packet.paths
    ui_packet = next(packet for packet in analysis.packets if packet.kind == "magento-ui-component")
    assert "app/code/Acme/Checkout/Ui/DataProvider/Cart.php" in ui_packet.paths
    assert "app/code/Acme/Checkout/view/adminhtml/web/js/form/element/sku.js" in ui_packet.paths
    assert "app/code/Acme/Checkout/etc/acl.xml" in ui_packet.paths
    menu_packet = next(
        packet for packet in analysis.packets
        if (
            packet.kind == "magento-admin-menu"
            and packet.key == "Acme_Checkout::cart"
        )
    )
    assert {
        "app/code/Acme/Checkout/etc/adminhtml/menu.xml",
        "app/code/Acme/Audit/etc/adminhtml/menu.xml",
        "app/code/Acme/Checkout/etc/adminhtml/routes.xml",
        (
            "app/code/Acme/Checkout/Controller/Adminhtml/"
            "Cart/Index.php"
        ),
        "app/code/Acme/Checkout/etc/acl.xml",
        "app/code/Acme/Checkout/etc/adminhtml/system.xml",
        "app/code/Acme/Checkout/etc/module.xml",
    } <= set(menu_packet.paths)
    menu_facts = {
        (fact.kind, fact.relation, fact.target)
        for fact in menu_packet.facts
    }
    assert {
        (
            "magento-admin-menu-item",
            "navigates-to",
            "acme/cart/index",
        ),
        (
            "magento-admin-menu-parent",
            "child-of-menu-item",
            "Acme_Checkout::root",
        ),
        (
            "magento-admin-menu-acl",
            "requires-resource",
            "Acme_Checkout::cart",
        ),
        (
            "magento-admin-menu-config-condition",
            "visible-when-config-enabled",
            "acme/cart/enabled",
        ),
        (
            "magento-admin-menu-module-condition",
            "visible-when-module-enabled",
            "Acme_Checkout",
        ),
        (
            "magento-admin-menu-action",
            "dispatches-admin-action",
            "acme/cart/index",
        ),
    } <= menu_facts
    menu_item = next(
        fact for fact in menu_packet.facts
        if fact.kind == "magento-admin-menu-item"
    )
    assert dict(menu_item.attributes)["title"] == "Audited Cart"
    admin_route = next(
        fact for fact in facts
        if (
            fact.kind == "magento-route-controller"
            and dict(fact.attributes).get("area") == "adminhtml"
        )
    )
    assert admin_route.source == "acme/cart"
    assert (
        admin_route.target
        == "Acme\\Checkout\\Controller\\Adminhtml\\Cart\\Index"
    )
    indexer_packet = next(packet for packet in analysis.packets if packet.kind == "magento-indexer")
    assert "app/code/Acme/Checkout/Model/Cart.php" in indexer_packet.paths
    assert "app/code/Acme/Checkout/etc/mview.xml" in indexer_packet.paths
    system_packet = next(
        packet for packet in analysis.packets
        if (
            packet.kind == "magento-system-config"
            and packet.key == "field:acme/cart/mode"
        )
    )
    assert {
        "app/code/Acme/Checkout/etc/adminhtml/system.xml",
        "app/code/Acme/Checkout/etc/config.xml",
        "app/code/Acme/Checkout/Model/Cart.php",
        "app/code/Acme/Checkout/Model/Service.php",
        "app/code/Acme/Checkout/Block/Cart.php",
    } <= set(system_packet.paths)
    system_facts = {
        (fact.kind, fact.relation, fact.target)
        for fact in system_packet.facts
    }
    assert {
        (
            "magento-system-config-field",
            "declared-by-admin-field",
            "acme/cart/mode",
        ),
        (
            "magento-system-config-model",
            "uses-source-model",
            "Acme\\Checkout\\Model\\Cart",
        ),
        (
            "magento-system-config-model",
            "uses-backend-model",
            "Acme\\Checkout\\Model\\Service",
        ),
        (
            "magento-system-config-model",
            "uses-frontend-model",
            "Acme\\Checkout\\Block\\Cart",
        ),
        (
            "magento-system-config-dependency",
            "depends-on-config-field",
            "acme/cart/enabled",
        ),
        (
            "magento-system-config-extension",
            "extends-config-node",
            "base_mode",
        ),
        (
            "magento-system-config-default",
            "has-default-declaration",
            "default",
        ),
        (
            "magento-system-config-default",
            "has-default-declaration",
            "websites:base",
        ),
        (
            "magento-system-config-default",
            "has-default-declaration",
            "stores:default_store",
        ),
    } <= system_facts
    system_acl_packet = next(
        packet for packet in analysis.packets
        if (
            packet.kind == "magento-system-config"
            and packet.key == "section:acme"
        )
    )
    assert "app/code/Acme/Checkout/etc/acl.xml" in system_acl_packet.paths
    assert any(
        fact.kind == "magento-system-config-acl"
        and fact.target == "Acme_Checkout::cart"
        for fact in system_acl_packet.facts
    )
    system_consumer_packets = tuple(
        packet for packet in analysis.packets
        if (
            packet.kind == "magento-system-config"
            and packet.key.startswith(
                "consumer:Acme\\Checkout\\Model\\ConfigReader:"
            )
        )
    )
    assert len(system_consumer_packets) == 2
    assert {
        "app/code/Acme/Checkout/Model/ConfigReader.php",
        "app/code/Acme/Checkout/etc/adminhtml/system.xml",
        "app/code/Acme/Checkout/etc/config.xml",
    } <= set().union(*(
        set(packet.paths) for packet in system_consumer_packets
    ))
    assert {
        (fact.relation, fact.target)
        for packet in system_consumer_packets
        for fact in packet.facts
        if fact.kind == "magento-system-config-consumer"
    } == {
        ("reads-config-value", "acme/cart/runtime_mode"),
        ("checks-config-flag", "acme/cart/enabled"),
    }
    mview_packet = next(
        packet for packet in analysis.packets
        if packet.kind == "magento-materialized-view"
    )
    assert "app/code/Acme/Checkout/etc/db_schema.xml" in mview_packet.paths


def test_magento_repository_analysis_preserves_fact_level_related_paths():
    analysis = _resolve()
    inherited = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-di-inherited-plugin"
        and fact.source == "Acme\\Checkout\\Model\\Cart"
        and fact.relation == "intercepted-by"
    )

    assert inherited.related_paths == ("app/code/Acme/Checkout/Model/Cart.php",)


def test_magento_admin_menu_models_command_chains_and_abstains_on_invalid_items():
    analysis = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Vendor_First' => 1,
                'Vendor_Second' => 1,
            ]];""",
            "app/code/Vendor/First/etc/module.xml": """
                <config><module name="Vendor_First" /></config>
            """,
            "app/code/Vendor/Second/etc/module.xml": """
                <config><module name="Vendor_Second" /></config>
            """,
            "app/code/Vendor/First/etc/adminhtml/menu.xml": """
                <config><menu>
                  <add id="Vendor_First::root" title="Root"
                    module="Vendor_First"
                    resource="Vendor_First::root" />
                  <add id="Vendor_First::child" title="Child"
                    module="Vendor_First"
                    resource="Vendor_First::child"
                    parent="Vendor_First::root" />
                  <add id="Vendor_First::duplicate" title="First"
                    module="Vendor_First"
                    resource="Vendor_First::duplicate" />
                  <add id="Vendor_First::orphan" title="Orphan"
                    module="Vendor_First"
                    resource="Vendor_First::orphan"
                    parent="Vendor_First::missing" />
                  <update id="Vendor_First::late"
                    title="Earlier update wins" />
                </menu></config>
            """,
            "app/code/Vendor/Second/etc/adminhtml/menu.xml": """
                <config><menu>
                  <remove id="Vendor_First::root" />
                  <add id="Vendor_First::duplicate" title="Second"
                    module="Vendor_Second"
                    resource="Vendor_First::duplicate" />
                  <add id="Vendor_First::late" title="Add title"
                    module="Vendor_Second"
                    resource="Vendor_First::late" />
                </menu></config>
            """,
        },
        symbols=(),
    )
    by_key = {
        packet.key: packet
        for packet in analysis.packets
        if packet.kind == "magento-admin-menu"
    }

    root = by_key["Vendor_First::root"].facts[0]
    assert root.kind == "magento-admin-menu-removed"
    assert dict(root.attributes)["reason"] == "removed"

    child = by_key["Vendor_First::child"].facts[0]
    assert child.kind == "magento-admin-menu-suppressed"
    assert dict(child.attributes)["reason"] == "parent-removed"

    duplicate = by_key["Vendor_First::duplicate"].facts[0]
    assert duplicate.kind == "magento-admin-menu-invalid"
    assert dict(duplicate.attributes)["reason"] == "duplicate-add"
    assert {
        "app/code/Vendor/First/etc/adminhtml/menu.xml",
        "app/code/Vendor/Second/etc/adminhtml/menu.xml",
    } <= set(by_key["Vendor_First::duplicate"].paths)

    orphan = by_key["Vendor_First::orphan"].facts[0]
    assert orphan.kind == "magento-admin-menu-invalid"
    assert dict(orphan.attributes)["reason"] == "missing-parent"

    late_item = next(
        fact for fact in by_key["Vendor_First::late"].facts
        if fact.kind == "magento-admin-menu-item"
    )
    assert dict(late_item.attributes)["title"] == "Earlier update wins"


def _template_global_artifacts(
    *,
    second_definition: bool = False,
    definition_handle: str = "cms_index_index",
) -> dict[str, str]:
    layout_blocks = """
        <block name="banner.caller"
            template="Acme_Checkout::banner/caller.phtml" />
    """
    if definition_handle == "cms_index_index":
        layout_blocks += """
            <block name="banner.helper"
                template="Acme_Checkout::banner/helper.phtml" />
        """
    if second_definition:
        layout_blocks += """
            <block name="banner.other-helper"
                template="Acme_Checkout::banner/other-helper.phtml" />
        """
    artifacts = {
        "app/etc/config.php": """<?php return ['modules' => [
            'Acme_Checkout' => 1,
        ]];""",
        "app/code/Acme/Checkout/etc/module.xml": """
            <config><module name="Acme_Checkout" /></config>
        """,
        "app/code/Acme/Checkout/view/frontend/layout/cms_index_index.xml": (
            f"<page><body>{layout_blocks}</body></page>"
        ),
        "app/code/Acme/Checkout/view/frontend/templates/banner/caller.phtml": """
            <script>
                if (typeof window.fixBannerExternalLinks === 'function') {
                    window.fixBannerExternalLinks(
                        document.querySelectorAll('.banner')
                    );
                }
            </script>
        """,
        "app/code/Acme/Checkout/view/frontend/templates/banner/helper.phtml": """
            <script>
                function fixExternalLinks(items) {
                    return Array.from(items);
                }
                window.fixBannerExternalLinks = fixExternalLinks;
            </script>
        """,
    }
    if definition_handle != "cms_index_index":
        artifacts[
            "app/code/Acme/Checkout/view/frontend/layout/cms_page_view.xml"
        ] = """
            <page><body><block name="banner.helper"
                template="Acme_Checkout::banner/helper.phtml" /></body></page>
        """
    if second_definition:
        artifacts[
            "app/code/Acme/Checkout/view/frontend/templates/banner/other-helper.phtml"
        ] = """
            <script>
                window.fixBannerExternalLinks = function (item) {
                    return item;
                };
            </script>
        """
    return artifacts


def test_magento_template_global_links_unique_co_declared_definition():
    analysis = _resolve(
        artifacts=_template_global_artifacts(),
        symbols=(),
    )
    facts = tuple(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind.startswith("magento-template-global-")
    )
    call = next(
        fact
        for fact in facts
        if fact.kind == "magento-template-global-call"
    )
    definition = next(
        fact
        for fact in facts
        if fact.kind == "magento-template-global-definition"
    )

    assert call.source == "window.fixBannerExternalLinks"
    assert call.relation == "calls-unique-co-declared-definition"
    assert call.path.endswith("templates/banner/caller.phtml")
    assert call.related_paths == (
        "app/code/Acme/Checkout/view/frontend/templates/banner/helper.phtml",
    )
    assert dict(call.attributes)["resolution"] == "exact-layout-source"
    assert dict(call.attributes)["layoutSources"].endswith(
        "view/frontend/layout/cms_index_index.xml"
    )
    assert definition.path.endswith("templates/banner/helper.phtml")
    assert definition.related_paths == (call.path,)


def test_magento_template_global_abstains_for_ambiguous_or_separate_layouts():
    ambiguous = _resolve(
        artifacts=_template_global_artifacts(second_definition=True),
        symbols=(),
    )
    separate = _resolve(
        artifacts=_template_global_artifacts(
            definition_handle="cms_page_view",
        ),
        symbols=(),
    )
    runtime_theme_unknown_artifacts = _template_global_artifacts()
    runtime_theme_unknown_artifacts.update({
        "app/design/frontend/Acme/custom/theme.xml": (
            "<theme><title>Custom</title></theme>"
        ),
        "app/design/frontend/Acme/custom/registration.php": (
            "<?php ComponentRegistrar::register("
            "ComponentRegistrar::THEME, 'frontend/Acme/custom', __DIR__);"
        ),
        (
            "app/design/frontend/Acme/custom/Acme_Checkout/templates/"
            "banner/helper.phtml"
        ): """
            <script>
                window.fixBannerExternalLinks = function (item) {
                    return item;
                };
            </script>
        """,
    })
    runtime_theme_unknown = _resolve(
        artifacts=runtime_theme_unknown_artifacts,
        symbols=(),
    )

    assert not any(
        fact.kind == "magento-template-global-call"
        for packet in ambiguous.packets
        for fact in packet.facts
    )
    assert not any(
        fact.kind == "magento-template-global-call"
        for packet in separate.packets
        for fact in packet.facts
    )
    assert not any(
        fact.kind == "magento-template-global-call"
        for packet in runtime_theme_unknown.packets
        for fact in packet.facts
    )


def _template_event_artifacts(
    *,
    duplicate_listener: bool = False,
    listener_handle: str = "default",
) -> dict[str, str]:
    listener_blocks = """
        <block name="cart.listener"
            template="Acme_Checkout::events/listener.phtml" />
    """
    if duplicate_listener:
        listener_blocks += """
            <block name="cart.other-listener"
                template="Acme_Checkout::events/other-listener.phtml" />
        """
    artifacts = {
        "app/etc/config.php": """<?php return ['modules' => [
            'Acme_Checkout' => 1,
        ]];""",
        "app/code/Acme/Checkout/etc/module.xml": """
            <config><module name="Acme_Checkout" /></config>
        """,
        "app/code/Acme/Checkout/view/frontend/layout/cms_index_index.xml": """
            <page><body><block name="cart.dispatcher"
                template="Acme_Checkout::events/dispatcher.phtml" /></body></page>
        """,
        (
            "app/code/Acme/Checkout/view/frontend/layout/"
            f"{listener_handle}.xml"
        ): f"<page><body>{listener_blocks}</body></page>",
        "app/code/Acme/Checkout/view/frontend/templates/events/dispatcher.phtml": """
            <script>
                window.dispatchEvent(new CustomEvent('ga4-cart-event', {
                    detail: { quantity: 2 }
                }));
            </script>
        """,
        "app/code/Acme/Checkout/view/frontend/templates/events/listener.phtml": """
            <script>
                window.addEventListener('ga4-cart-event', function (event) {
                    consume(event.detail);
                });
            </script>
        """,
    }
    if duplicate_listener:
        artifacts[
            (
                "app/code/Acme/Checkout/view/frontend/templates/events/"
                "other-listener.phtml"
            )
        ] = """
            <script>
                window.addEventListener('ga4-cart-event', consumeAgain);
            </script>
        """
    return artifacts


def test_magento_template_event_links_unique_default_handle_listener():
    analysis = _resolve(
        artifacts=_template_event_artifacts(),
        symbols=(),
    )
    facts = tuple(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind.startswith("magento-template-event-")
    )
    dispatch = next(
        fact
        for fact in facts
        if fact.kind == "magento-template-event-dispatch"
    )
    listener = next(
        fact
        for fact in facts
        if fact.kind == "magento-template-event-listener"
    )

    dispatcher_path = (
        "app/code/Acme/Checkout/view/frontend/templates/events/"
        "dispatcher.phtml"
    )
    listener_path = (
        "app/code/Acme/Checkout/view/frontend/templates/events/"
        "listener.phtml"
    )
    default_layout = (
        "app/code/Acme/Checkout/view/frontend/layout/default.xml"
    )
    page_layout = (
        "app/code/Acme/Checkout/view/frontend/layout/cms_index_index.xml"
    )
    assert dispatch.source == "window:ga4-cart-event"
    assert dispatch.relation == "dispatches-to-unique-layout-listener"
    assert dispatch.path == dispatcher_path
    assert {listener_path, default_layout, page_layout} <= set(
        dispatch.related_paths
    )
    assert dict(dispatch.attributes)["resolution"] == (
        "default-handle-coactivation"
    )
    assert dict(dispatch.attributes)["semanticRole"] == "topology"

    assert listener.path == listener_path
    assert listener.relation == "listens-to-layout-dispatchers"
    assert {dispatcher_path, default_layout, page_layout} <= set(
        listener.related_paths
    )
    assert dict(listener.attributes)["dispatcherCount"] == "1"


def test_magento_template_event_abstains_without_coactivation_or_unique_listener():
    separate = _resolve(
        artifacts=_template_event_artifacts(
            listener_handle="cms_page_view",
        ),
        symbols=(),
    )
    ambiguous = _resolve(
        artifacts=_template_event_artifacts(
            duplicate_listener=True,
        ),
        symbols=(),
    )
    sibling_themes = _resolve(
        artifacts={
            "app/etc/config.php": """<?php return ['modules' => [
                'Acme_Checkout' => 1,
            ]];""",
            "app/code/Acme/Checkout/etc/module.xml": """
                <config><module name="Acme_Checkout" /></config>
            """,
            "app/design/frontend/Acme/first/theme.xml": (
                "<theme><title>First</title></theme>"
            ),
            "app/design/frontend/Acme/first/registration.php": (
                "<?php ComponentRegistrar::register("
                "ComponentRegistrar::THEME, 'frontend/Acme/first', __DIR__);"
            ),
            "app/design/frontend/Acme/second/theme.xml": (
                "<theme><title>Second</title></theme>"
            ),
            "app/design/frontend/Acme/second/registration.php": (
                "<?php ComponentRegistrar::register("
                "ComponentRegistrar::THEME, 'frontend/Acme/second', __DIR__);"
            ),
            (
                "app/design/frontend/Acme/first/Acme_Checkout/layout/"
                "default.xml"
            ): """
                <page><body><block name="listener"
                    template="Acme_Checkout::events/listener.phtml"
                /></body></page>
            """,
            (
                "app/design/frontend/Acme/second/Acme_Checkout/layout/"
                "cms_index_index.xml"
            ): """
                <page><body><block name="dispatcher"
                    template="Acme_Checkout::events/dispatcher.phtml"
                /></body></page>
            """,
            (
                "app/design/frontend/Acme/first/Acme_Checkout/templates/"
                "events/listener.phtml"
            ): """
                <script>
                    window.addEventListener('ga4-cart-event', consume);
                </script>
            """,
            (
                "app/design/frontend/Acme/second/Acme_Checkout/templates/"
                "events/dispatcher.phtml"
            ): """
                <script>
                    window.dispatchEvent(
                        new CustomEvent('ga4-cart-event')
                    );
                </script>
            """,
        },
        symbols=(),
    )

    assert not any(
        fact.kind.startswith("magento-template-event-")
        for packet in separate.packets
        for fact in packet.facts
    )
    assert not any(
        fact.kind.startswith("magento-template-event-")
        for packet in ambiguous.packets
        for fact in packet.facts
    )
    assert not any(
        fact.kind.startswith("magento-template-event-")
        for packet in sibling_themes.packets
        for fact in packet.facts
    )


def test_magento_repository_snapshot_recomputes_effective_pr_overlay():
    base = _resolve()
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    restored = plugin.restore_repository_analysis(
        "fedcba9876543210",
        base.snapshots,
    )
    assert restored.status is OutcomeStatus.HANDLED
    changed_path = "app/code/Acme/Checkout/etc/di.xml"
    restored.value.ingest((FileArtifact(
        changed_path,
        r"""
        <config>
          <preference for="Acme\Checkout\Api\CartInterface" type="Acme\Checkout\Model\FrontendCart" />
        </config>
        """,
    ),))

    outcome = restored.value.finish(RepositoryAnalysis(symbols=_symbols()))

    assert outcome.status is OutcomeStatus.HANDLED
    global_preference = next(
        packet for packet in outcome.value.packets
        if packet.kind == "magento-di"
        and packet.key == "global:preference:Acme\\Checkout\\Api\\CartInterface"
    )
    assert global_preference.facts[0].target == "Acme\\Checkout\\Model\\FrontendCart"
    service_packet = next(
        packet for packet in outcome.value.packets
        if packet.kind == "magento-object-graph"
        and packet.key == "global:Acme\\Checkout\\Model\\Service"
    )
    assert changed_path in service_packet.paths
    assert any(
        fact.kind == "magento-object-resolution"
        and fact.target == "Acme\\Checkout\\Model\\FrontendCart"
        for fact in service_packet.facts
    )


def test_magento_repository_snapshot_applies_deletions():
    base = _resolve()
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("magento")
    restored = plugin.restore_repository_analysis(
        "fedcba9876543210",
        base.snapshots,
    )
    deleted_path = "app/code/Acme/Checkout/view/frontend/layout/checkout_wrapper.xml"
    restored.value.ingest((FileArtifact(deleted_path, "", deleted=True),))

    outcome = restored.value.finish(RepositoryAnalysis(symbols=_symbols()))

    assert outcome.status is OutcomeStatus.HANDLED
    assert all(deleted_path not in packet.paths for packet in outcome.value.packets)


def test_magento_architecture_reaches_stage_1_as_focused_fresh_context():
    """Exercise plugin graph -> storage nodes -> exact retrieval -> prompt text."""
    sys.path.insert(
        0,
        str(PROJECT_ROOT / "python-ecosystem" / "rag-pipeline" / "src"),
    )
    sys.path.insert(
        0,
        str(PROJECT_ROOT / "python-ecosystem" / "inference-orchestrator" / "src"),
    )
    from rag_pipeline.core.index_manager.indexer import RepositoryIndexer
    from rag_pipeline.core.index_representation import (
        INDEX_REPRESENTATION_PAYLOAD_KEY,
    )
    from rag_pipeline.services.base import RAGQueryBase
    from rag_pipeline.services.deterministic_context import DeterministicContextMixin
    from rag_pipeline.core.review_grouping import (
        review_groups_from_architecture_payloads,
    )
    from qdrant_client.http.models import FieldCondition, MatchValue
    context_helpers_path = (
        PROJECT_ROOT
        / "python-ecosystem"
        / "inference-orchestrator"
        / "src"
        / "service"
        / "review"
        / "orchestrator"
        / "context_helpers.py"
    )
    module_spec = importlib.util.spec_from_file_location(
        "codecrow_test_context_helpers",
        context_helpers_path,
    )
    assert module_spec and module_spec.loader
    context_helpers = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(context_helpers)

    analysis = _resolve()
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    repository_plugins = ("php", "magento")
    capabilities = SimpleNamespace(
        repository_plugins=repository_plugins,
        fingerprint="sha256:" + "0" * 64,
        descriptor_fingerprint=catalog.registry.fingerprint_for(repository_plugins),
        implementation_fingerprint=catalog.implementation_fingerprint(
            repository_plugins
        ),
    )
    nodes = RepositoryIndexer._architecture_nodes(
        analysis,
        capabilities,
        "ws",
        "project",
        "feature",
        "commit",
        capabilities.implementation_fingerprint,
    )
    changed_path = "app/code/Acme/Checkout/Plugin/CartAudit.php"
    matching_nodes = [
        node for node in nodes
        if changed_path in node.metadata["architecture_paths"]
    ]
    assert matching_nodes

    points = [
        SimpleNamespace(
            id=f"packet-{index}",
            payload={
                **node.metadata,
                "text": node.text,
                "branch": "feature",
                "pr": True,
                "pr_number": 42,
            },
        )
        for index, node in enumerate(matching_nodes)
    ]

    class FakeQdrant:
        def __init__(self, records=points):
            self.calls = 0
            self.records = records

        def scroll(self, **_kwargs):
            self.calls += 1
            return (
                (self.records, None)
                if self.calls == 1
                else ([], None)
            )

    service = object.__new__(type(
        "MagentoContextService",
        (DeterministicContextMixin, RAGQueryBase),
        {},
    ))
    service.plugin_catalog = catalog
    service.index_representation_fingerprint = matching_nodes[0].metadata[
        INDEX_REPRESENTATION_PAYLOAD_KEY
    ]
    service._plugin_identity_cache = {}
    service.qdrant_client = FakeQdrant()
    architecture_context = {}
    architecture_related = {}
    chunks = []
    stats = service._query_architecture_context(
        "collection",
        FieldCondition(key="branch", match=MatchValue(value="feature")),
        [changed_path],
        5,
        ["feature", "main"],
        "feature",
        set(),
        {changed_path},
        set(),
        chunks,
        architecture_context,
        architecture_related,
        set(),
    )

    expected_facts = [
        fact
        for node in matching_nodes
        for fact in node.metadata["plugin_graph_facts"]
        if changed_path in {
            fact["path"],
            *fact.get("related_paths", []),
        }
    ]
    retrieved_facts = [
        fact
        for chunk in chunks
        for fact in chunk["metadata"]["plugin_graph_facts"]
    ]
    assert len(retrieved_facts) == len(expected_facts)
    assert all(
        changed_path in {fact["path"], *fact.get("related_paths", [])}
        for fact in retrieved_facts
    )
    assert stats["truncated"] is False

    # Stage 1's separately tested normalization maps payload pr=true to this
    # freshness label before invoking the formatter.
    normalized = [
        {
            **chunk,
            "_source": (
                "pr_indexed"
                if chunk["metadata"].get("pr") is True
                else "deterministic"
            ),
        }
        for chunk in chunks
    ]
    prompt_context = context_helpers.format_rag_context(
        {"relevant_code": normalized},
        pr_changed_files=[changed_path],
    )

    assert normalized
    assert all(chunk["_source"] == "pr_indexed" for chunk in normalized)
    assert "Acme\\Checkout\\Plugin\\CartAudit" in prompt_context
    assert "intercepted-by" in prompt_context
    assert len(prompt_context) <= 32_000

    queue_handler_path = (
        "app/code/Acme/Checkout/Model/QueueHandler.php"
    )
    queue_nodes = [
        node for node in nodes
        if queue_handler_path
        in node.metadata["architecture_paths"]
        and any(
            fact["kind"]
            == "magento-message-effective-handler"
            for fact in node.metadata["plugin_graph_facts"]
        )
    ]
    assert queue_nodes
    queue_points = [
        SimpleNamespace(
            id=f"queue-packet-{index}",
            payload={
                **node.metadata,
                "text": node.text,
                "branch": "feature",
                "pr": True,
                "pr_number": 42,
            },
        )
        for index, node in enumerate(queue_nodes)
    ]
    service.qdrant_client = FakeQdrant(queue_points)
    queue_chunks = []
    service._query_architecture_context(
        "collection",
        FieldCondition(key="branch", match=MatchValue(value="feature")),
        [queue_handler_path],
        5,
        ["feature", "main"],
        "feature",
        set(),
        {queue_handler_path},
        set(),
        queue_chunks,
        {},
        {},
        set(),
    )
    queue_prompt_context = context_helpers.format_rag_context(
        {
            "relevant_code": [
                {**chunk, "_source": "pr_indexed"}
                for chunk in queue_chunks
            ]
        },
        pr_changed_files=[queue_handler_path],
    )

    assert "magento-message-effective-handler" in queue_prompt_context
    assert (
        "Acme\\Checkout\\Model\\QueueHandler::process"
        in queue_prompt_context
    )
    assert len(queue_prompt_context) <= 32_000
    assert review_groups_from_architecture_payloads(
        (node.metadata for node in queue_nodes),
        (
            "app/code/Acme/Checkout/etc/queue_consumer.xml",
            queue_handler_path,
        ),
    ) == [[
        "app/code/Acme/Checkout/Model/QueueHandler.php",
        "app/code/Acme/Checkout/etc/queue_consumer.xml",
    ]]

    system_path = (
        "app/code/Acme/Checkout/etc/adminhtml/system.xml"
    )
    conditional_layout_path = (
        "app/code/Acme/Checkout/view/frontend/layout/"
        "checkout_cart_save.xml"
    )
    system_nodes = [
        node for node in nodes
        if (
            system_path in node.metadata["architecture_paths"]
            and any(
                (
                    fact["kind"].startswith("magento-system-config-")
                    or fact["kind"] in {
                        "magento-layout-acl-condition",
                        "magento-layout-config-condition",
                    }
                )
                for fact in node.metadata["plugin_graph_facts"]
            )
        )
    ]
    assert system_nodes
    system_points = [
        SimpleNamespace(
            id=f"system-packet-{index}",
            payload={
                **node.metadata,
                "text": node.text,
                "branch": "feature",
                "pr": True,
                "pr_number": 42,
            },
        )
        for index, node in enumerate(system_nodes)
    ]
    service.qdrant_client = FakeQdrant(system_points)
    system_chunks = []
    service._query_architecture_context(
        "collection",
        FieldCondition(key="branch", match=MatchValue(value="feature")),
        [system_path, conditional_layout_path],
        10,
        ["feature", "main"],
        "feature",
        set(),
        {system_path, conditional_layout_path},
        set(),
        system_chunks,
        {},
        {},
        set(),
    )
    system_prompt_context = context_helpers.format_rag_context(
        {
            "relevant_code": [
                {**chunk, "_source": "pr_indexed"}
                for chunk in system_chunks
            ]
        },
        pr_changed_files=[system_path, conditional_layout_path],
    )

    assert "magento-system-config-field" in system_prompt_context
    assert "acme/cart/runtime_mode" in system_prompt_context
    assert "uses-source-model" in system_prompt_context
    assert "Acme\\Checkout\\Model\\Cart" in system_prompt_context
    assert "uses-backend-model" in system_prompt_context
    assert "Acme\\Checkout\\Model\\Service" in system_prompt_context
    assert "has-default-declaration" in system_prompt_context
    assert "Acme_Checkout::cart" in system_prompt_context
    assert "visible-when-config-enabled" in system_prompt_context
    assert "visible-to-resource" in system_prompt_context
    assert "checkout_cart_save" in system_prompt_context
    assert "reads-config-value" in system_prompt_context
    assert "checks-config-flag" in system_prompt_context
    assert "Acme\\Checkout\\Model\\ConfigReader::mode" in (
        system_prompt_context
    )
    assert len(system_prompt_context) <= 32_000
    assert review_groups_from_architecture_payloads(
        (node.metadata for node in system_nodes),
        (
            system_path,
            "app/code/Acme/Checkout/etc/config.xml",
            "app/code/Acme/Checkout/Model/ConfigReader.php",
            "app/code/Acme/Checkout/Model/Service.php",
            conditional_layout_path,
        ),
    ) == [[
        "app/code/Acme/Checkout/Model/ConfigReader.php",
        "app/code/Acme/Checkout/Model/Service.php",
        "app/code/Acme/Checkout/etc/adminhtml/system.xml",
        "app/code/Acme/Checkout/etc/config.xml",
        conditional_layout_path,
    ]]

    menu_path = "app/code/Acme/Checkout/etc/adminhtml/menu.xml"
    menu_controller_path = (
        "app/code/Acme/Checkout/Controller/Adminhtml/Cart/Index.php"
    )
    menu_changed_paths = (
        menu_path,
        menu_controller_path,
        "app/code/Acme/Checkout/etc/adminhtml/routes.xml",
        "app/code/Acme/Checkout/etc/adminhtml/system.xml",
        "app/code/Acme/Checkout/etc/acl.xml",
    )
    menu_nodes = [
        node for node in nodes
        if (
            menu_path in node.metadata["architecture_paths"]
            and any(
                fact["kind"].startswith("magento-admin-menu-")
                for fact in node.metadata["plugin_graph_facts"]
            )
        )
    ]
    assert menu_nodes
    service.qdrant_client = FakeQdrant([
        SimpleNamespace(
            id=f"menu-packet-{index}",
            payload={
                **node.metadata,
                "text": node.text,
                "branch": "feature",
                "pr": True,
                "pr_number": 42,
            },
        )
        for index, node in enumerate(menu_nodes)
    ])
    menu_chunks = []
    service._query_architecture_context(
        "collection",
        FieldCondition(key="branch", match=MatchValue(value="feature")),
        list(menu_changed_paths),
        10,
        ["feature", "main"],
        "feature",
        set(),
        set(menu_changed_paths),
        set(),
        menu_chunks,
        {},
        {},
        set(),
    )
    menu_prompt_context = context_helpers.format_rag_context(
        {
            "relevant_code": [
                {**chunk, "_source": "pr_indexed"}
                for chunk in menu_chunks
            ]
        },
        pr_changed_files=list(menu_changed_paths),
    )
    assert "magento-admin-menu-item" in menu_prompt_context
    assert "magento-admin-menu-action" in menu_prompt_context
    assert "acme/cart/index" in menu_prompt_context
    assert "Acme_Checkout::cart" in menu_prompt_context
    assert "acme/cart/enabled" in menu_prompt_context
    assert menu_controller_path in menu_prompt_context
    assert len(menu_prompt_context) <= 32_000
    menu_groups = review_groups_from_architecture_payloads(
        (node.metadata for node in menu_nodes),
        menu_changed_paths,
    )
    assert len(menu_groups) == 1
    assert set(menu_groups[0]) == set(menu_changed_paths)
