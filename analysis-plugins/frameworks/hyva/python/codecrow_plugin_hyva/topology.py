from __future__ import annotations

from collections import deque

from codecrow_plugins import (
    ArchitecturePacket,
    GraphFact,
    PluginDiagnostic,
    RepositoryAnalysis,
)

from .dependency_index import HyvaDependencyIndex
from .template_runtime import (
    AlpineEventListener,
    AlpineProviderDefinition,
    TemplateRuntime,
    _normalized_route,
)


class HyvaTopologyResolver:
    """Build template runtime graph packets from one dependency snapshot."""

    MAX_CALL_GRAPH_STATES = 64

    def __init__(self, plugin_id: str, templates: dict[str, TemplateRuntime]) -> None:
        self.plugin_id = plugin_id
        self.templates = templates
        self.index = HyvaDependencyIndex(templates)
        self.diagnostics: list[PluginDiagnostic] = []
        self._call_context_cache: dict[tuple[str, str], tuple[set[str], set[str]]] = {}

    def _call_graph_context(
        self,
        owner: str,
        method: str,
        edges: dict[tuple[str, str], tuple[GraphFact, ...]],
    ) -> tuple[set[str], set[str]]:
        cache_key = (owner, method)
        cached = self._call_context_cache.get(cache_key)
        if cached is not None:
            return cached
        paths: set[str] = set()
        identifiers: set[str] = {method}
        pending = deque([(owner, method)])
        visited: set[tuple[str, str]] = set()
        while pending and len(visited) < self.MAX_CALL_GRAPH_STATES:
            state = pending.popleft()
            normalized = (state[0].casefold(), state[1].casefold())
            if normalized in visited:
                continue
            visited.add(normalized)
            for fact in edges.get(normalized, ()):
                attributes = dict(fact.attributes)
                target_method = attributes["targetMethod"]
                paths.add(fact.path)
                paths.update(fact.related_paths)
                identifiers.add(attributes["callerMethod"])
                identifiers.add(target_method)
                pending.append((fact.target, target_method))
        if pending and not any(
            diagnostic.code == "hyva-call-graph-state-limit"
            for diagnostic in self.diagnostics
        ):
            self.diagnostics.append(PluginDiagnostic(
                code="hyva-call-graph-state-limit",
                message=(
                    f"Hyva call-graph traversal admitted "
                    f"{self.MAX_CALL_GRAPH_STATES} states; repository "
                    "architecture context is partial"
                ),
                plugin_id=self.plugin_id,
                recoverable=True,
            ))
        self._call_context_cache[cache_key] = (paths, identifiers)
        return paths, identifiers

    def packets(
        self,
        dependencies: RepositoryAnalysis,
    ) -> tuple[ArchitecturePacket, ...]:
        self._call_context_cache.clear()
        self.diagnostics.clear()
        blocks_by_source, sources_by_template = self.index.layout_topology(
            dependencies
        )
        webapi_routes = self.index.webapi_routes(dependencies)
        call_edges = self.index.call_edges(dependencies)
        symbols = self.index.symbols(dependencies)
        hyva_themes = self.index.hyva_theme_templates(dependencies)
        packets: list[ArchitecturePacket] = []
        provider_definitions: dict[
            str,
            list[tuple[str, AlpineProviderDefinition]],
        ] = {}
        for path, runtime in sorted(self.templates.items()):
            for definition in runtime.alpine_provider_definitions:
                provider_definitions.setdefault(
                    definition.provider_name,
                    [],
                ).append((path, definition))
        event_listeners: dict[
            str,
            list[tuple[str, AlpineEventListener]],
        ] = {}
        for path, runtime in sorted(self.templates.items()):
            for listener in runtime.alpine_event_listeners:
                event_listeners.setdefault(
                    listener.event_name,
                    [],
                ).append((path, listener))

        for template_path, runtime in sorted(self.templates.items()):
            facts: set[GraphFact] = set()
            packet_paths: set[str] = {template_path}
            requirements_by_variable = {
                requirement.assigned_variable: requirement
                for requirement in runtime.requirements
                if requirement.assigned_variable
            }
            layout_sources = tuple(sorted(
                sources_by_template.get(template_path, ())
            ))
            contexts = layout_sources or ("",)
            hyva_theme = self.index.template_hyva_theme(
                template_path,
                hyva_themes,
            )

            if hyva_theme is not None:
                theme_name, theme_paths = hyva_theme
                for variable in runtime.runtime_variables:
                    facts.add(GraphFact(
                        kind="hyva-template-runtime-variable",
                        source=template_path,
                        relation="receives-hyva-runtime-variable",
                        target=variable.class_name,
                        path=template_path,
                        line=variable.line,
                        attributes=tuple(sorted((
                            ("method", variable.method_name),
                            ("resolution", "hyva-theme-template-contract"),
                            ("semanticRole", "topology"),
                            ("theme", theme_name),
                            ("variable", variable.variable_name),
                        ))),
                        related_paths=theme_paths,
                    ))
                    packet_paths.update(theme_paths)

            for dispatch in runtime.alpine_event_dispatches:
                candidates = event_listeners.get(dispatch.event_name, ())
                for layout_source in contexts:
                    listener_paths = {
                        listener_path
                        for listener_path, listener in candidates
                        if (
                            listener_path == template_path
                            or (
                                listener.window
                                and layout_source
                                and layout_source in sources_by_template.get(
                                    listener_path,
                                    (),
                                )
                            )
                        )
                    }
                    if not listener_paths:
                        continue
                    crosses_templates = any(
                        path != template_path for path in listener_paths
                    )
                    related_paths = tuple(sorted({
                        *listener_paths,
                        *((layout_source,) if layout_source else ()),
                    }))
                    facts.add(GraphFact(
                        kind="hyva-alpine-event-dispatch",
                        source=template_path,
                        relation="dispatches-to-exact-alpine-listener",
                        target=dispatch.event_name,
                        path=template_path,
                        line=dispatch.line,
                        attributes=tuple(sorted((
                            ("listenerCount", str(len(listener_paths))),
                            *(
                                (("layoutSource", layout_source),)
                                if layout_source
                                else ()
                            ),
                            (
                                "resolution",
                                (
                                    "exact-layout-window-event"
                                    if crosses_templates
                                    else "exact-local-event"
                                ),
                            ),
                            ("semanticRole", "topology"),
                        ))),
                        related_paths=related_paths,
                    ))
                    packet_paths.update(related_paths)

            for use in runtime.alpine_provider_uses:
                definitions = provider_definitions.get(
                    use.provider_name,
                    (),
                )
                if len(definitions) != 1:
                    continue
                definition_path, definition = definitions[0]
                for layout_source in contexts:
                    related_paths = tuple(sorted({
                        definition_path,
                        *((layout_source,) if layout_source else ()),
                    }))
                    facts.add(GraphFact(
                        kind="hyva-alpine-component-reference",
                        source=template_path,
                        relation="uses-exact-alpine-provider",
                        target=use.provider_name,
                        path=template_path,
                        line=use.line,
                        attributes=tuple(sorted((
                            ("definitionPath", definition_path),
                            ("factoryName", definition.factory_name),
                            ("invocation", use.invocation),
                            *(
                                (("layoutSource", layout_source),)
                                if layout_source
                                else ()
                            ),
                            ("resolution", definition.resolution),
                            ("semanticRole", "topology"),
                        ))),
                        related_paths=related_paths,
                    ))
                    packet_paths.update(related_paths)

            for requirement in runtime.requirements:
                candidates = symbols.get(
                    requirement.class_name.casefold(),
                    (),
                )
                view_model_path = (
                    candidates[0].path if len(candidates) == 1 else ""
                )
                for layout_source in contexts:
                    related_paths = tuple(sorted({
                        *((layout_source,) if layout_source else ()),
                        *((view_model_path,) if view_model_path else ()),
                    }))
                    facts.add(GraphFact(
                        kind="hyva-view-model-requirement",
                        source=template_path,
                        relation="requires-view-model",
                        target=requirement.class_name,
                        path=template_path,
                        line=requirement.line,
                        attributes=tuple(sorted((
                            ("registryVariable", requirement.registry_variable),
                            *(
                                (("assignedVariable", requirement.assigned_variable),)
                                if requirement.assigned_variable
                                else ()
                            ),
                            *((("layoutSource", layout_source),) if layout_source else ()),
                            (
                                "resolution",
                                (
                                    "exact-registry-and-layout-source"
                                    if layout_source
                                    else "exact-registry-call"
                                ),
                            ),
                            ("semanticRole", "topology"),
                            ("viewModelPath", view_model_path),
                        ))),
                        related_paths=related_paths,
                    ))
                    packet_paths.update(related_paths)

            for reference in runtime.webapi_references:
                requirement = requirements_by_variable.get(
                    reference.view_model_variable
                )
                if requirement is None:
                    continue
                route_candidates = webapi_routes.get((
                    reference.http_method,
                    _normalized_route(reference.route).casefold(),
                ), ())
                if len(route_candidates) != 1:
                    continue
                route_fact = route_candidates[0]
                route_attributes = dict(route_fact.attributes)
                contract, separator, service_method = route_fact.target.rpartition(
                    "::"
                )
                implementation = route_attributes.get("implementation", "")
                if not separator or not implementation:
                    continue
                call_paths, call_identifiers = self._call_graph_context(
                    implementation,
                    service_method,
                    call_edges,
                )
                view_model_candidates = symbols.get(
                    requirement.class_name.casefold(),
                    (),
                )
                view_model_path = (
                    view_model_candidates[0].path
                    if len(view_model_candidates) == 1
                    else ""
                )
                identifiers = tuple(sorted({
                    service_method,
                    *call_identifiers,
                }))
                for layout_source in contexts:
                    scope_templates = self.index.runtime_scope_templates(
                        template_path,
                        reference.state_identifiers,
                        layout_source,
                        blocks_by_source,
                    )
                    related_paths = tuple(sorted({
                        *scope_templates,
                        *route_fact.related_paths,
                        *call_paths,
                        route_fact.path,
                        *((layout_source,) if layout_source else ()),
                        *((view_model_path,) if view_model_path else ()),
                    }))
                    retrieval_attributes = tuple(
                        (
                            f"retrievalIdentifier:{index:04d}",
                            identifier,
                        )
                        for index, identifier in enumerate(identifiers)
                    )
                    facts.add(GraphFact(
                        kind="hyva-template-webapi-reference",
                        source=template_path,
                        relation="references-exact-webapi-route-literal",
                        target=route_fact.source,
                        path=template_path,
                        line=reference.line,
                        attributes=tuple(sorted((
                            ("httpMethod", reference.http_method),
                            ("implementation", implementation),
                            *((("layoutSource", layout_source),) if layout_source else ()),
                            ("resolution", "exact-registry-route-literal"),
                            ("route", reference.route),
                            ("semanticRole", "topology"),
                            ("service", f"{contract}::{service_method}"),
                            ("viewModelClass", requirement.class_name),
                            ("viewModelVariable", reference.view_model_variable),
                            *retrieval_attributes,
                        ))),
                        related_paths=related_paths,
                    ))
                    packet_paths.update(related_paths)

            if facts:
                packets.append(ArchitecturePacket(
                    plugin_id=self.plugin_id,
                    kind="hyva-template-runtime",
                    key=template_path,
                    paths=tuple(sorted(packet_paths)),
                    facts=tuple(sorted(facts)),
                    attributes=(("resolution", "exact-source-topology"),),
                ))
        return tuple(sorted(packets))
