from __future__ import annotations

from dataclasses import field

from codecrow_plugins import GraphFact
from codecrow_plugins.graphql import parse_schema

from .architecture import MAGENTO_AREAS, ModuleRecord, PacketGraph, attrs, line, tag
from .dependency_injection import DependencyInjectionTopology
from .events import (
    EventDispatch,
    EventEntrypoint,
    decode_event_dispatches,
    resolve_dispatch_area,
)
from .resolution_index import RepositorySourceIndex
from .resolution_models import ConfigValue, DiState


class EventTopology:
    """Build Magento events relationships from repository evidence."""

    def __init__(
        self,
        index: RepositorySourceIndex,
        graph: PacketGraph,
        di: DependencyInjectionTopology,
    ) -> None:
        self.index = index
        self.graph = graph
        self.di = di

    def event_entrypoints(
        self,
        modules: tuple[ModuleRecord, ...],
        states: dict[str, DiState],
        dispatches: tuple[EventDispatch, ...],
    ) -> tuple[EventEntrypoint, ...]:
        """Collect exact runtime-area evidence for dispatching callables."""
        entrypoints: set[EventEntrypoint] = set()
        global_state = states.get("global", DiState())

        routed_modules: dict[tuple[str, str], set[str]] = {}
        for area in ("adminhtml", "frontend"):
            for path, _, _ in self.index.ordered_configs(
                "routes.xml",
                modules,
                area,
            ):
                root = self.index.xml(path)
                if root is None:
                    continue
                for route_module in (
                    node
                    for route in root.iter()
                    if tag(route) == "route"
                    for node in route
                    if tag(node) == "module" and node.get("name")
                ):
                    routed_modules.setdefault(
                        (area, route_module.get("name")),
                        set(),
                    ).add(path)

        for dispatch in dispatches:
            if dispatch.caller.casefold() != "execute":
                continue
            module = self.index.module_for_path(dispatch.path, modules)
            if module is None or not module.enabled:
                continue
            relative = (
                dispatch.path[len(module.root):].lstrip("/")
                if module.root
                else dispatch.path
            )
            normalized = "/" + relative.casefold().strip("/")
            candidate_area = ""
            if "/controller/adminhtml/" in normalized:
                candidate_area = "adminhtml"
            elif "/controller/" in normalized:
                candidate_area = "frontend"
            for path in sorted(routed_modules.get(
                (candidate_area, module.name),
                (),
            )):
                entrypoints.add(EventEntrypoint(
                    candidate_area,
                    dispatch.owner,
                    dispatch.caller,
                    path,
                ))

        for path, _, _ in self.index.ordered_configs(
            "crontab.xml",
            modules,
            "global",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            state = states.get("crontab", global_state)
            for job in (
                node
                for node in root.iter()
                if tag(node) == "job" and node.get("instance")
            ):
                target = self.di.resolve_type(job.get("instance"), state)
                entrypoints.add(EventEntrypoint(
                    "crontab",
                    target,
                    job.get("method", "execute"),
                    path,
                ))

        for path, content in sorted(self.index.artifacts.items()):
            if not path.casefold().endswith(".graphqls"):
                continue
            module = self.index.module_for_path(path, modules)
            if module is None or not module.enabled:
                continue
            state = states.get("graphql", global_state)
            for declaration in parse_schema(content):
                directives = [
                    *declaration.directives,
                    *(
                        directive
                        for field in declaration.fields
                        for directive in field.directives
                    ),
                ]
                for directive in directives:
                    target = directive.argument("class")
                    if (
                        directive.name not in {"resolver", "typeResolver"}
                        or not target
                    ):
                        continue
                    entrypoints.add(EventEntrypoint(
                        "graphql",
                        self.di.resolve_type(target, state),
                        (
                            "resolveType"
                            if directive.name == "typeResolver"
                            else "resolve"
                        ),
                        path,
                    ))

        for path, _, _ in self.index.ordered_configs(
            "webapi.xml",
            modules,
            "global",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            for service in (
                node
                for node in root.iter()
                if tag(node) == "service"
                and node.get("class")
                and node.get("method")
            ):
                for area in ("webapi_rest", "webapi_soap"):
                    state = states.get(area, global_state)
                    entrypoints.add(EventEntrypoint(
                        area,
                        self.di.resolve_type(service.get("class"), state),
                        service.get("method"),
                        path,
                    ))
        return tuple(sorted(entrypoints))

    def events(
        self,
        modules: tuple[ModuleRecord, ...],
        states: dict[str, DiState],
    ) -> None:
        areas = {"global", *MAGENTO_AREAS}
        effective_by_area: dict[
            str,
            dict[tuple[str, str], ConfigValue],
        ] = {}
        provenance_by_area: dict[
            str,
            dict[tuple[str, str], tuple[ConfigValue, ...]],
        ] = {}
        global_state = states.get("global", DiState())
        for area in sorted(areas):
            observers: dict[tuple[str, str], ConfigValue] = {}
            provenance: dict[tuple[str, str], list[ConfigValue]] = {}
            for path, module, order in self.index.ordered_configs("events.xml", modules, area):
                root = self.index.xml(path)
                if root is None:
                    continue
                content = self.index.artifacts[path]
                module_name = module.name if module else "application"
                for event in (node for node in root.iter() if tag(node) == "event" and node.get("name")):
                    for observer in (node for node in event if tag(node) == "observer" and node.get("name")):
                        prior = observers.get((event.get("name"), observer.get("name")))
                        instance = observer.get("instance") or (prior.value if prior else "")
                        merged = dict(prior.attributes) if prior else {}
                        merged.update({
                            key: value for key, value in observer.attrib.items()
                            if key not in {"instance", "name"}
                        })
                        key = (event.get("name"), observer.get("name"))
                        declaration = ConfigValue(
                            observer.get("instance", ""),
                            path,
                            line(content, observer.get("name")),
                            module_name,
                            order,
                            tuple(sorted({
                                name: value
                                for name, value in observer.attrib.items()
                                if name not in {"instance", "name"}
                            }.items())),
                        )
                        provenance.setdefault(key, []).append(declaration)
                        observers[key] = ConfigValue(
                            instance, path, line(content, observer.get("name")), module_name, order,
                            tuple(sorted(merged.items())),
                        )
            effective_by_area[area] = observers
            provenance_by_area[area] = {
                key: tuple(values)
                for key, values in provenance.items()
            }
            state = states.get(area, global_state)
            for (event_name, observer_name), observer in sorted(observers.items()):
                observer_attrs = dict(observer.attributes)
                disabled = observer_attrs.get("disabled", "false").casefold() in {"1", "true"}
                resolved_instance = (
                    self.di.resolve_type(observer.value, state)
                    if observer.value
                    else ""
                )
                declaration_paths = tuple(sorted({
                    declaration.path
                    for declaration in provenance[(event_name, observer_name)]
                }))
                packet = self.graph.packet("magento-event", f"{area}:{event_name}", area=area)
                packet.add(GraphFact(
                    "magento-effective-observer",
                    event_name,
                    "disables-observer" if disabled else "observed-by",
                    observer.value or observer_name,
                    observer.path,
                    observer.line,
                    attrs(**{
                        "area": area,
                        "module": observer.module,
                        **observer_attrs,
                        "name": observer_name,
                        "resolvedInstance": resolved_instance,
                        "declarationCount": len(
                            provenance[(event_name, observer_name)]
                        ),
                    }),
                ),
                    self.index.symbol_path(observer.value),
                    self.index.symbol_path(resolved_instance),
                    *self.di.resolution_paths(observer.value, state),
                    *declaration_paths,
                )
                for declaration in provenance[(event_name, observer_name)][:-1]:
                    packet.add(GraphFact(
                        "magento-observer-override",
                        f"{event_name}:{observer_name}",
                        "overridden-by-observer-config",
                        observer.path,
                        declaration.path,
                        declaration.line,
                        attrs(
                            area=area,
                            disabled=disabled,
                            effectiveInstance=observer.value,
                            effectiveModule=observer.module,
                        ),
                    ), observer.path)

        dispatches = decode_event_dispatches(self.index.symbols)
        entrypoints = self.event_entrypoints(
            modules,
            states,
            dispatches,
        )
        for dispatch in dispatches:
            area_resolution = resolve_dispatch_area(dispatch, entrypoints)
            packet = self.graph.packet(
                "magento-event-dispatch",
                f"{dispatch.path}:{dispatch.line}:{dispatch.event_name}",
                event=dispatch.event_name,
            )
            packet.add(GraphFact(
                "magento-event-dispatch",
                dispatch.callable,
                "dispatches-event",
                dispatch.event_name,
                dispatch.path,
                dispatch.line,
                attrs(
                    area=area_resolution.area,
                    areaCandidates=",".join(area_resolution.candidate_areas),
                    areaResolution=area_resolution.status,
                    literalResolution=dispatch.literal_resolution,
                    receiverResolution=dispatch.receiver_resolution,
                    receiverType=dispatch.receiver_type,
                    semanticRole="topology",
                ),
            ), *area_resolution.paths)

            observer_area = (
                area_resolution.area
                if area_resolution.status == "proven"
                else "global"
            )
            possible_areas = (
                area_resolution.candidate_areas
                if area_resolution.candidate_areas
                else tuple(sorted(MAGENTO_AREAS))
            )
            for key, observer in sorted(
                effective_by_area.get(observer_area, {}).items()
            ):
                event_name, observer_name = key
                if event_name != dispatch.event_name:
                    continue
                observer_attrs = dict(observer.attributes)
                if observer_attrs.get("disabled", "false").casefold() in {
                    "1",
                    "true",
                }:
                    continue

                candidate_observers = (
                    (observer,)
                    if area_resolution.status == "proven"
                    else tuple(
                        effective_by_area.get(area, {}).get(key)
                        for area in possible_areas
                    )
                )
                if (
                    any(candidate is None for candidate in candidate_observers)
                    or len(set(candidate_observers)) != 1
                ):
                    continue
                resolved_candidates = {
                    self.di.resolve_type(
                        observer.value,
                        states.get(area, global_state),
                    )
                    for area in (
                        (observer_area,)
                        if area_resolution.status == "proven"
                        else possible_areas
                    )
                    if observer.value
                }
                if len(resolved_candidates) != 1:
                    continue
                resolved_instance = next(iter(resolved_candidates))
                configured_symbol = self.index.unique_symbol_casefold(
                    observer.value
                )
                resolved_symbol = self.index.unique_symbol_casefold(
                    resolved_instance
                )
                execution = (
                    self.index.method_symbol(resolved_symbol, "execute")
                    if resolved_symbol is not None
                    else None
                )
                if execution is not None:
                    declaring_symbol, execute_method = execution
                    target = (
                        f"{declaring_symbol.qualified_name}::{execute_method}"
                    )
                    relation = "dispatches-to-observer-execute"
                    execution_path = declaring_symbol.path
                else:
                    target = resolved_instance
                    relation = "dispatches-to-observer-class"
                    execution_path = (
                        resolved_symbol.path if resolved_symbol else ""
                    )
                declaration_paths = tuple(sorted({
                    declaration.path
                    for declaration in provenance_by_area[
                        observer_area
                    ][key]
                }))
                resolution_areas = (
                    (observer_area,)
                    if area_resolution.status == "proven"
                    else possible_areas
                )
                resolution_paths = tuple(sorted({
                    path
                    for area in resolution_areas
                    for path in self.di.resolution_paths(
                        observer.value,
                        states.get(area, global_state),
                    )
                }))
                packet.add(GraphFact(
                    "magento-event-dispatch-observer",
                    dispatch.callable,
                    relation,
                    target,
                    dispatch.path,
                    dispatch.line,
                    attrs(
                        area=area_resolution.area,
                        areaResolution=area_resolution.status,
                        configuredObserver=observer.value,
                        event=dispatch.event_name,
                        observerArea=observer_area,
                        observerName=observer_name,
                        resolvedObserver=resolved_instance,
                        semanticRole="topology",
                    ),
                ),
                    observer.path,
                    configured_symbol.path if configured_symbol else "",
                    resolved_symbol.path if resolved_symbol else "",
                    execution_path,
                    *resolution_paths,
                    *declaration_paths,
                    *area_resolution.paths,
                )
