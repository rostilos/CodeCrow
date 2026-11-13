from __future__ import annotations

import importlib
import json
from pathlib import Path

from codecrow_plugins import (
    FileArtifact,
    PluginCatalog,
    PluginRuntime,
    ProjectSelector,
    RepositoryFacts,
    SymbolDefinition,
)


PLUGINS_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN = PluginCatalog.discover(PLUGINS_ROOT).implementation("magento")
_EVENTS = importlib.import_module(_PLUGIN.__class__.__module__ + ".events")


def _literal_dispatch(
    event_name: str,
    *,
    line: int,
    target: str = r"Magento\Framework\Event\ManagerInterface",
    resolution: str = "direct-literal",
) -> str:
    payload = {
        "caller": "execute",
        "line": line,
        "literalStringArguments": {"0": event_name},
        "method": "dispatch",
        "receiverResolution": "declared-property",
        "target": target,
    }
    if resolution != "direct-literal":
        payload["literalArgumentResolution"] = {"0": resolution}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def test_event_dispatch_decoder_keeps_only_exact_unambiguous_manager_calls():
    symbols = (
        SymbolDefinition(
            "Acme\\Event\\Controller\\Index\\Save",
            "class",
            "app/code/Acme/Event/Controller/Index/Save.php",
            methods=("execute",),
            attributes=tuple(sorted((
                (
                    "php-literal-instance-call-reference:0000",
                    _literal_dispatch("checkout_saved", line=20),
                ),
                (
                    "php-literal-instance-call-reference:0001",
                    _literal_dispatch("ambiguous_first", line=30),
                ),
                (
                    "php-literal-instance-call-reference:0002",
                    _literal_dispatch("ambiguous_second", line=30),
                ),
                (
                    "php-literal-instance-call-reference:0003",
                    _literal_dispatch(
                        "wrong_receiver",
                        line=40,
                        target="Acme\\Event\\Manager",
                    ),
                ),
                (
                    "php-literal-instance-call-reference:0004",
                    _literal_dispatch(
                        "uncertain_local",
                        line=50,
                        resolution="conditional-assignment",
                    ),
                ),
            ))),
        ),
    )

    dispatches = _EVENTS.decode_event_dispatches(symbols)

    assert len(dispatches) == 1
    assert dispatches[0].event_name == "checkout_saved"
    assert dispatches[0].callable.endswith("Save::execute")


def test_event_area_resolution_requires_one_exact_entrypoint_area():
    dispatch = _EVENTS.EventDispatch(
        owner="Acme\\Event\\Api\\Service",
        caller="save",
        path="app/code/Acme/Event/Model/Service.php",
        line=20,
        event_name="acme_saved",
        receiver_type="Magento\\Framework\\Event\\ManagerInterface",
        receiver_resolution="declared-property",
        literal_resolution="direct-literal",
    )
    entrypoints = (
        _EVENTS.EventEntrypoint(
            "webapi_rest",
            dispatch.owner,
            dispatch.caller,
            "app/code/Acme/Event/etc/webapi.xml",
        ),
        _EVENTS.EventEntrypoint(
            "webapi_soap",
            dispatch.owner,
            dispatch.caller,
            "app/code/Acme/Event/etc/webapi.xml",
        ),
    )

    resolution = _EVENTS.resolve_dispatch_area(dispatch, entrypoints)

    assert resolution.status == "ambiguous"
    assert resolution.area == ""
    assert resolution.candidate_areas == (
        "webapi_rest",
        "webapi_soap",
    )


def _integration_artifacts() -> dict[str, str]:
    return {
        "app/etc/config.php": """<?php return ['modules' => [
            'Acme_Event' => 1,
        ]];""",
        "bin/magento": "#!/usr/bin/env php\n<?php",
        "composer.json": '{"require":{"magento/framework":"*"}}',
        "app/code/Acme/Event/etc/module.xml": (
            '<config><module name="Acme_Event" /></config>'
        ),
        "app/code/Acme/Event/etc/frontend/routes.xml": """
            <config><router id="standard"><route id="acme" frontName="acme">
                <module name="Acme_Event" />
            </route></router></config>
        """,
        "app/code/Acme/Event/etc/events.xml": r"""
            <config><event name="acme_checkout_saved">
                <observer name="audit"
                    instance="Acme\Event\Api\ObserverContract" />
                <observer name="suppressed"
                    instance="Acme\Event\Observer\SuppressedObserver" />
                <observer name="global_stable"
                    instance="Acme\Event\Observer\StableObserver" />
            </event></config>
        """,
        "app/code/Acme/Event/etc/frontend/events.xml": r"""
            <config><event name="acme_checkout_saved">
                <observer name="audit" shared="false" />
                <observer name="suppressed" disabled="true" />
                <observer name="frontend_only"
                    instance="Acme\Event\Observer\FrontendOnlyObserver" />
            </event></config>
        """,
        "app/code/Acme/Event/etc/adminhtml/events.xml": r"""
            <config><event name="acme_checkout_saved">
                <observer name="admin_only"
                    instance="Acme\Event\Observer\AdminObserver" />
            </event></config>
        """,
        "app/code/Acme/Event/etc/di.xml": r"""
            <config><preference
                for="Acme\Event\Api\ObserverContract"
                type="Acme\Event\Observer\GlobalObserver" /></config>
        """,
        "app/code/Acme/Event/etc/frontend/di.xml": r"""
            <config><preference
                for="Acme\Event\Api\ObserverContract"
                type="Acme\Event\Observer\FrontendObserver" /></config>
        """,
        "app/code/Acme/Event/Controller/Index/Save.php": r"""<?php
namespace Acme\Event\Controller\Index;

use Magento\Framework\Event\ManagerInterface;

final class Save
{
    public function __construct(private ManagerInterface $eventManager) {}

    public function execute(): void
    {
        $this->eventManager->dispatch('acme_checkout_saved');
        $this->eventManager->dispatch($dynamicEvent);
    }
}
""",
        "app/code/Acme/Event/Model/Publisher.php": r"""<?php
namespace Acme\Event\Model;

use Magento\Framework\Event\ManagerInterface;

final class Publisher
{
    public function __construct(private ManagerInterface $eventManager) {}

    public function publish(): void
    {
        $eventName = 'acme_checkout_saved';
        $this->eventManager->dispatch($eventName);
    }
}
""",
        "app/code/Acme/Event/Api/ObserverContract.php": r"""<?php
namespace Acme\Event\Api;
interface ObserverContract { public function execute(): void; }
""",
        **{
            f"app/code/Acme/Event/Observer/{name}.php": f"""<?php
namespace Acme\\Event\\Observer;
final class {name} {{ public function execute(): void {{}} }}
"""
            for name in (
                "AdminObserver",
                "FrontendObserver",
                "FrontendOnlyObserver",
                "GlobalObserver",
                "StableObserver",
                "SuppressedObserver",
            )
        },
    }


def test_php_runtime_dispatch_metadata_reaches_effective_magento_observers():
    artifacts = _integration_artifacts()
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="event-topology",
        paths=tuple(sorted(artifacts)),
        marker_contents={"composer.json": artifacts["composer.json"]},
    ))
    assert runtime.repository_analysis_plugins(capabilities) == (
        "php",
        "magento",
    )
    handle = runtime.start_repository_analysis(
        capabilities,
        "event-topology",
    )
    handle.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(artifacts.items())
    ))

    analysis, diagnostics = handle.finish()

    assert diagnostics == ()
    facts = tuple(
        fact for packet in analysis.packets for fact in packet.facts
    )
    dispatches = tuple(
        fact for fact in facts
        if fact.kind == "magento-event-dispatch"
    )
    assert len(dispatches) == 2
    controller_dispatch = next(
        fact for fact in dispatches
        if "Controller\\Index\\Save" in fact.source
    )
    publisher_dispatch = next(
        fact for fact in dispatches
        if "Model\\Publisher" in fact.source
    )
    assert dict(controller_dispatch.attributes)["area"] == "frontend"
    assert dict(controller_dispatch.attributes)["areaResolution"] == "proven"
    assert dict(publisher_dispatch.attributes)["areaResolution"] == "unresolved"
    assert controller_dispatch.target == "acme_checkout_saved"
    assert "dynamicEvent" not in {fact.target for fact in dispatches}

    controller_edges = tuple(
        fact for fact in facts
        if fact.kind == "magento-event-dispatch-observer"
        and "Controller\\Index\\Save" in fact.source
    )
    assert {fact.target for fact in controller_edges} == {
        "Acme\\Event\\Observer\\FrontendObserver::execute",
        "Acme\\Event\\Observer\\FrontendOnlyObserver::execute",
        "Acme\\Event\\Observer\\StableObserver::execute",
    }
    audit_edge = next(
        fact for fact in controller_edges
        if dict(fact.attributes)["observerName"] == "audit"
    )
    assert dict(audit_edge.attributes)["configuredObserver"] == (
        "Acme\\Event\\Api\\ObserverContract"
    )
    assert dict(audit_edge.attributes)["resolvedObserver"] == (
        "Acme\\Event\\Observer\\FrontendObserver"
    )
    assert {
        "app/code/Acme/Event/etc/events.xml",
        "app/code/Acme/Event/etc/frontend/events.xml",
        "app/code/Acme/Event/etc/frontend/di.xml",
        "app/code/Acme/Event/etc/frontend/routes.xml",
        "app/code/Acme/Event/Observer/FrontendObserver.php",
    } <= set(audit_edge.related_paths)

    publisher_edges = tuple(
        fact for fact in facts
        if fact.kind == "magento-event-dispatch-observer"
        and "Model\\Publisher" in fact.source
    )
    assert {fact.target for fact in publisher_edges} == {
        "Acme\\Event\\Observer\\StableObserver::execute",
    }
    assert dict(publisher_edges[0].attributes)["observerArea"] == "global"

    disabled = next(
        fact for fact in facts
        if fact.kind == "magento-effective-observer"
        and fact.relation == "disables-observer"
        and dict(fact.attributes).get("area") == "frontend"
        and dict(fact.attributes).get("name") == "suppressed"
    )
    assert {
        "app/code/Acme/Event/etc/events.xml",
        "app/code/Acme/Event/etc/frontend/events.xml",
    } <= set(disabled.related_paths)
    assert any(
        fact.kind == "magento-observer-override"
        and dict(fact.attributes).get("area") == "frontend"
        for fact in facts
    )
