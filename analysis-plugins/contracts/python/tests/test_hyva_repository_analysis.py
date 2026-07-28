from __future__ import annotations

from pathlib import Path

from codecrow_plugins import (
    CandidateClaim,
    FileArtifact,
    PluginCatalog,
    PluginRuntime,
    ProjectSelector,
    RepositoryFacts,
    ValidationDecision,
)


PLUGINS_ROOT = Path(__file__).resolve().parents[3]


def _artifacts() -> dict[str, str]:
    return {
        "app/etc/config.php": """<?php return ['modules' => [
            'Acme_Sales' => 1,
            'Hyva_Theme' => 1,
        ]];""",
        "bin/magento": "#!/usr/bin/env php\n<?php",
        "composer.json": """{
          "require": {
            "hyva-themes/magento2-theme-module": "*"
          }
        }""",
        "app/code/Acme/Sales/etc/module.xml": (
            '<config><module name="Acme_Sales" /></config>'
        ),
        "app/code/Acme/Sales/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::MODULE,
                'Acme_Sales',
                __DIR__
            );
        """,
        "app/code/Acme/Sales/etc/di.xml": r"""
            <config>
              <preference
                for="Acme\Sales\Api\OrderInfoInterface"
                type="Acme\Sales\Model\OrderInfo" />
            </config>
        """,
        "app/code/Acme/Sales/etc/webapi.xml": r"""
            <routes>
              <route url="/V1/acme/orders/list/" method="POST">
                <service
                  class="Acme\Sales\Api\OrderInfoInterface"
                  method="getOrderInfo" />
                <resources><resource ref="self" /></resources>
              </route>
            </routes>
        """,
        "app/design/frontend/Acme/custom/theme.xml": (
            "<theme><title>Acme</title><parent>Hyva/default</parent></theme>"
        ),
        "app/design/frontend/Acme/custom/registration.php": """<?php
            ComponentRegistrar::register(
                ComponentRegistrar::THEME,
                'frontend/Acme/custom',
                __DIR__
            );
        """,
        "app/design/frontend/Acme/custom/Acme_Sales/layout/acme_orders.xml": """
            <page><body>
              <referenceBlock name="orders.root">
                <block name="orders.init"
                  template="Acme_Sales::orders/init.phtml" />
                <block name="orders.items"
                  template="Acme_Sales::orders/items.phtml" />
              </referenceBlock>
            </body></page>
        """,
        "app/design/frontend/Acme/custom/Acme_Sales/templates/orders/init.phtml": r"""<?php
use Hyva\Theme\Model\ViewModelRegistry;
use Acme\Sales\ViewModel\Orders;

/** @var ViewModelRegistry $viewModels */
$orders = $viewModels->require(Orders::class);
?>
<script>
fetch('<?= $escaper->escapeUrl(
    $orders->getRestUrl("rest/V1/acme/orders/list")
) ?>', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'}
}).then(data => {
    this.ordersData = data.items;
});
</script>
""",
        "app/design/frontend/Acme/custom/Acme_Sales/templates/orders/items.phtml": (
            '<template x-for="order in ordersData">'
            '<span x-text="order.display_price"></span>'
            '</template>'
        ),
        "app/design/frontend/Acme/custom/Magento_Cookie/templates/notices.phtml": (
            "<?php /** @var $hyvaCsp HyvaCsp */ ?>\n"
            "<script>window.cookieNotice = true</script>\n"
            "<?php $hyvaCsp->registerInlineScript() ?>\n"
        ),
        "app/design/frontend/Acme/luma/theme.xml": (
            "<theme><title>Acme Luma</title><parent>Magento/luma</parent></theme>"
        ),
        "app/design/frontend/Acme/luma/Magento_Cookie/templates/notices.phtml": (
            "<?php $hyvaCsp->registerInlineScript() ?>\n"
        ),
        "app/code/Acme/Sales/view/frontend/templates/module-notice.phtml": (
            "<?php $hyvaCsp->registerInlineScript() ?>\n"
        ),
        "app/code/Acme/Sales/Api/OrderInfoInterface.php": r"""<?php
namespace Acme\Sales\Api;
interface OrderInfoInterface
{
    public function getOrderInfo(): array;
}
""",
        "app/code/Acme/Sales/Model/OrderInfo.php": r"""<?php
namespace Acme\Sales\Model;
use Acme\Sales\Api\OrderInfoInterface;
use Acme\Sales\ViewModel\Orders;
class OrderInfo implements OrderInfoInterface
{
    public function __construct(private Orders $orders) {}
    public function getOrderInfo(): array
    {
        return $this->orders->prepareOrdersData();
    }
}
""",
        "app/code/Acme/Sales/ViewModel/Orders.php": r"""<?php
namespace Acme\Sales\ViewModel;
use Acme\Sales\Model\OrderItemsProcessor;
class Orders
{
    public function __construct(
        private OrderItemsProcessor $itemsProcessor
    ) {}
    public function prepareOrdersData(): array
    {
        return $this->prepareOnlineOrders();
    }
    private function prepareOnlineOrders(): array
    {
        return $this->itemsProcessor->process();
    }
    public function getRestUrl(string $path): string
    {
        return $path;
    }
}
""",
        "app/code/Acme/Sales/Model/OrderItemsProcessor.php": r"""<?php
namespace Acme\Sales\Model;
class OrderItemsProcessor
{
    public function process(): array
    {
        return [['display_price' => '10.00']];
    }
}
""",
    }


def _resolve():
    artifacts = _artifacts()
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    selector = ProjectSelector(catalog.registry)
    capabilities = selector.select(RepositoryFacts(
        revision="hyva-exact-context",
        paths=tuple(sorted(artifacts)),
        marker_contents={
            "app/etc/config.php": artifacts["app/etc/config.php"],
            "composer.json": artifacts["composer.json"],
        },
    ))
    runtime = PluginRuntime(catalog)
    handle = runtime.start_repository_analysis(
        capabilities,
        "hyva-exact-context",
    )
    handle.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(artifacts.items())
    ))
    analysis, diagnostics = handle.finish()
    return catalog, runtime, capabilities, analysis, diagnostics


def test_hyva_detection_is_dependency_stable_and_evidence_based():
    _, _, capabilities, _, diagnostics = _resolve()

    assert diagnostics == ()
    assert capabilities.repository_plugins == (
        "json",
        "php",
        "magento",
        "hyva",
    )
    assert any(
        "composer.json" in item
        and "hyva-themes/magento2-theme-module" in item
        for item in capabilities.detection_evidence["hyva"]
    )


def test_hyva_template_runtime_links_layout_sibling_to_exact_webapi_call_graph():
    _, _, _, analysis, diagnostics = _resolve()
    assert diagnostics == ()

    layout_fact = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "magento-layout-block"
        and fact.target == "orders.init"
    )
    assert dict(layout_fact.attributes)["selectedTemplatePath"] == (
        "app/design/frontend/Acme/custom/Acme_Sales/"
        "templates/orders/init.phtml"
    )

    requirement = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "hyva-view-model-requirement"
        and fact.target == "Acme\\Sales\\ViewModel\\Orders"
    )
    assert requirement.path.endswith("templates/orders/init.phtml")
    assert "app/code/Acme/Sales/ViewModel/Orders.php" in requirement.related_paths
    assert not any(
        path.endswith("templates/orders/items.phtml")
        for path in requirement.related_paths
    )

    route = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "hyva-template-webapi-reference"
    )
    attributes = dict(route.attributes)
    assert route.target == "POST /V1/acme/orders/list/"
    assert attributes["service"] == (
        "Acme\\Sales\\Api\\OrderInfoInterface::getOrderInfo"
    )
    assert attributes["implementation"] == "Acme\\Sales\\Model\\OrderInfo"
    assert attributes["resolution"] == "exact-registry-route-literal"
    assert "app/code/Acme/Sales/Model/OrderInfo.php" in route.related_paths
    assert "app/code/Acme/Sales/ViewModel/Orders.php" in route.related_paths
    assert (
        "app/code/Acme/Sales/Model/OrderItemsProcessor.php"
        in route.related_paths
    )
    assert (
        "app/design/frontend/Acme/custom/Acme_Sales/"
        "templates/orders/items.phtml"
    ) in route.related_paths
    assert {
        value
        for key, value in route.attributes
        if key.startswith("retrievalIdentifier:")
    } >= {
        "getOrderInfo",
        "prepareOrdersData",
        "prepareOnlineOrders",
        "process",
    }


def test_hyva_repository_snapshot_restores_identical_packets():
    _, runtime, capabilities, analysis, diagnostics = _resolve()
    assert diagnostics == ()

    restored = runtime.start_repository_analysis(
        capabilities,
        "hyva-exact-context-restored",
        snapshots=analysis.snapshots,
    )
    replay, replay_diagnostics = restored.finish()

    assert replay_diagnostics == ()
    assert replay.packets == analysis.packets


def test_hyva_runtime_variable_requires_exact_hyva_theme_inheritance():
    _, _, _, analysis, diagnostics = _resolve()
    assert diagnostics == ()

    runtime_variables = tuple(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "hyva-template-runtime-variable"
    )

    assert len(runtime_variables) == 1
    runtime_variable = runtime_variables[0]
    assert runtime_variable.path == (
        "app/design/frontend/Acme/custom/Magento_Cookie/"
        "templates/notices.phtml"
    )
    assert runtime_variable.target == "Hyva\\Theme\\ViewModel\\HyvaCsp"
    assert dict(runtime_variable.attributes) == {
        "method": "registerInlineScript",
        "resolution": "hyva-theme-template-contract",
        "semanticRole": "topology",
        "theme": "Acme/custom",
        "variable": "$hyvaCsp",
    }
    assert runtime_variable.related_paths == (
        "app/design/frontend/Acme/custom/theme.xml",
    )
    assert not any(
        fact.path.startswith("app/design/frontend/Acme/luma/")
        or fact.path.startswith("app/code/Acme/Sales/view/")
        for fact in runtime_variables
    )


def test_hyva_runtime_variable_parser_ignores_non_php_and_other_receivers():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("hyva")
    session = plugin.start_repository_analysis("hyva-runtime-abstention").value
    session.ingest((
        FileArtifact(
            "app/design/frontend/Acme/custom/Magento_Cookie/"
            "templates/lookalikes.phtml",
            """
<script>const example = '$hyvaCsp->registerInlineScript()'</script>
<?php
$helper->registerInlineScript();
$HYVACSP->registerInlineScript();
?>
""",
        ),
    ))

    analysis = session.finish(_resolve()[3]).value

    assert not any(
        fact.kind == "hyva-template-runtime-variable"
        for packet in analysis.packets
        for fact in packet.facts
    )


def test_hyva_template_parser_abstains_from_unproven_registry_and_dynamic_route():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("hyva")
    session = plugin.start_repository_analysis("hyva-abstention").value
    session.ingest((
        FileArtifact(
            "app/design/frontend/Acme/custom/Acme_Sales/"
            "templates/orders/dynamic.phtml",
            r"""<?php
use Acme\Sales\ViewModel\Orders;
$orders = $unknownRegistry->require(Orders::class);
?>
<script>fetch($orders->getRestUrl(route), {method: 'POST'});</script>
""",
        ),
    ))
    analysis = session.finish(
        _resolve()[3]
    ).value

    assert not analysis.packets


def test_hyva_links_unique_exact_alpine_provider_across_templates():
    artifacts = _artifacts()
    artifacts[
        "app/design/frontend/Acme/custom/Acme_Sales/"
        "templates/orders/init.phtml"
    ] += """
<script>
function initOrderClipboard() {
    return {copied: false, copyOrder() { this.copied = true }}
}
// function initOrderClipboard() { return {copied: 'comment-only'} }
window.addEventListener(
    'alpine:init',
    () => Alpine.data('initOrderClipboard', initOrderClipboard),
    {once: true}
)
</script>
<button @click="$dispatch('order-copied', {copied: true})"></button>
"""
    artifacts[
        "app/design/frontend/Acme/custom/Acme_Sales/"
        "templates/orders/items.phtml"
    ] = """
<section x-data="initOrderClipboard()">
  <button
    @click="copyOrder()"
    @order-copied.window="copied = $event.detail.copied"
    x-text="copied"
  ></button>
</section>
"""

    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="hyva-alpine-provider",
        paths=tuple(sorted(artifacts)),
        marker_contents={
            "app/etc/config.php": artifacts["app/etc/config.php"],
            "composer.json": artifacts["composer.json"],
        },
    ))
    runtime = PluginRuntime(catalog)
    handle = runtime.start_repository_analysis(
        capabilities,
        "hyva-alpine-provider",
    )
    handle.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(artifacts.items())
    ))
    analysis, diagnostics = handle.finish()

    assert diagnostics == ()
    reference = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "hyva-alpine-component-reference"
    )
    assert reference.target == "initOrderClipboard"
    assert reference.path.endswith("templates/orders/items.phtml")
    assert dict(reference.attributes) == {
        "definitionPath": (
            "app/design/frontend/Acme/custom/Acme_Sales/"
            "templates/orders/init.phtml"
        ),
        "factoryName": "initOrderClipboard",
        "invocation": "call",
        "layoutSource": (
            "app/design/frontend/Acme/custom/Acme_Sales/"
            "layout/acme_orders.xml"
        ),
        "resolution": "exact-alpine-data-named-factory",
        "semanticRole": "topology",
    }
    assert (
        "app/design/frontend/Acme/custom/Acme_Sales/"
        "templates/orders/init.phtml"
    ) in reference.related_paths

    event = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "hyva-alpine-event-dispatch"
    )
    assert event.target == "order-copied"
    assert event.path.endswith("templates/orders/init.phtml")
    assert (
        "app/design/frontend/Acme/custom/Acme_Sales/"
        "templates/orders/items.phtml"
    ) in event.related_paths
    assert dict(event.attributes)["resolution"] == (
        "exact-layout-window-event"
    )

    restored = runtime.start_repository_analysis(
        capabilities,
        "hyva-alpine-provider-restored",
        snapshots=analysis.snapshots,
    )
    replay, replay_diagnostics = restored.finish()
    assert replay_diagnostics == ()
    assert replay.packets == analysis.packets


def test_hyva_alpine_provider_abstains_on_ambiguous_and_dynamic_uses():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("hyva")
    session = plugin.start_repository_analysis("hyva-alpine-abstention").value
    session.ingest((
        FileArtifact(
            "one.phtml",
            """
<script>function initShared() { return {open: false} }</script>
<div x-data="condition ? initShared() : initOther()"></div>
""",
        ),
        FileArtifact(
            "two.phtml",
            """
<script>function initShared() { return {open: true} }</script>
<div x-data="initShared()"></div>
""",
        ),
    ))

    analysis = session.finish(_resolve()[3]).value

    assert not any(
        fact.kind == "hyva-alpine-component-reference"
        for packet in analysis.packets
        for fact in packet.facts
    )


def test_hyva_alpine_provider_accepts_inline_registration_only_in_script():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("hyva")
    session = plugin.start_repository_analysis("hyva-alpine-inline").value
    session.ingest((
        FileArtifact(
            "inline.phtml",
            """
<?php $notJavascript = "function outsideScript() {}"; ?>
function outsideScript() {}
<script>
document.addEventListener('alpine:init', () => {
    Alpine.data('inlinePanel', () => ({open: false}))
})
</script>
<div x-data="inlinePanel"></div>
<div x-data="outsideScript()"></div>
""",
        ),
    ))

    analysis = session.finish(_resolve()[3]).value
    references = {
        fact.target: fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "hyva-alpine-component-reference"
    }

    assert set(references) == {"inlinePanel"}
    assert dict(references["inlinePanel"].attributes)[
        "resolution"
    ] == "exact-alpine-data-inline-factory"


def test_hyva_alpine_event_abstains_for_dynamic_and_unrelated_listener():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("hyva")
    session = plugin.start_repository_analysis("hyva-event-abstention").value
    session.ingest((
        FileArtifact(
            "dispatch.phtml",
            """
<button @click="$dispatch(eventName)"></button>
<button @click="$dispatch('local-only')"></button>
""",
        ),
        FileArtifact(
            "listener.phtml",
            """
<div @local-only="handleLocalOnly()"></div>
""",
        ),
    ))

    analysis = session.finish(_resolve()[3]).value

    assert not any(
        fact.kind == "hyva-alpine-event-dispatch"
        for packet in analysis.packets
        for fact in packet.facts
    )


def test_hyva_validation_rejects_only_exact_absence_contradiction():
    catalog, runtime, capabilities, analysis, diagnostics = _resolve()
    assert diagnostics == ()
    route = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "hyva-template-webapi-reference"
    )
    base = {
        "category": "bug-risk",
        "path": route.path,
        "line": route.line,
        "evidence": (route,),
        "claim_kind": route.kind,
    }

    absent = runtime.validate(
        CandidateClaim(
            **base,
            message=(
                "POST /V1/acme/orders/list/ endpoint does not exist."
            ),
        ),
        capabilities,
    )
    semantic = runtime.validate(
        CandidateClaim(
            **base,
            message=(
                "POST /V1/acme/orders/list/ returns an incompatible payload."
            ),
        ),
        capabilities,
    )
    unrelated_variable_semantics = runtime.validate(
        CandidateClaim(
            **base,
            message=(
                "POST /V1/acme/orders/list/ returns an undefined variable "
                "in its payload."
            ),
        ),
        capabilities,
    )
    unrelated = runtime.validate(
        CandidateClaim(
            **base,
            message="A different checkout endpoint does not exist.",
        ),
        capabilities,
    )
    runtime_variable = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "hyva-template-runtime-variable"
    )
    undefined_runtime_variable = runtime.validate(
        CandidateClaim(
            category="code-quality",
            path=runtime_variable.path,
            line=runtime_variable.line,
            message="Potential undefined variable $hyvaCsp.",
            evidence=(runtime_variable,),
            claim_kind=runtime_variable.kind,
        ),
        capabilities,
    )

    assert absent[0].decision is ValidationDecision.REJECT
    assert absent[0].code == "hyva-relation-absence-contradicted"
    assert semantic[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert semantic[0].code == "hyva-presence-is-not-defect-proof"
    assert (
        unrelated_variable_semantics[0].decision
        is ValidationDecision.INSUFFICIENT_EVIDENCE
    )
    assert unrelated_variable_semantics[0].code == (
        "hyva-presence-is-not-defect-proof"
    )
    assert unrelated[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert unrelated[0].code == "hyva-cited-relation-mismatch"
    assert (
        undefined_runtime_variable[0].decision
        is ValidationDecision.REJECT
    )
    assert undefined_runtime_variable[0].code == (
        "hyva-relation-absence-contradicted"
    )

    contribution, review_diagnostics = runtime.review_contribution(
        (route.path,),
        capabilities,
    )
    assert review_diagnostics == ()
    hyva_rules = tuple(
        rule
        for rule in contribution.rules
        if "Hyva" in rule or "topology relationship" in rule
    )
    assert len(hyva_rules) == 2
    assert all("checklist" not in rule.casefold() for rule in hyva_rules)
