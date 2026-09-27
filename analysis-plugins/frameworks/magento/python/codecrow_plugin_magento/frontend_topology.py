from __future__ import annotations

import re
from pathlib import PurePosixPath

from codecrow_plugins import GraphFact

from .architecture import ModuleRecord, PacketGraph, attrs
from .frontend_init import extract_frontend_initializers
from .javascript import (
    OptionalJavaScriptEnrichmentError,
    TemplateEventReference,
    TemplateGlobalReference,
    extract_requirejs_relations,
    extract_template_event_references,
    extract_template_global_references,
)
from .requirejs import (
    EffectiveRequireJsConfig,
    RequireJsResolution,
    build_effective_requirejs_config,
    extract_amd_dependencies,
    extract_template_amd_dependencies,
    normalize_amd_dependency,
)
from .resolution_index import RepositorySourceIndex
from .resolution_models import LayoutSources, ThemeRecord


class FrontendTopology:
    """Build Magento frontend relationships from repository evidence."""

    def __init__(
        self,
        index: RepositorySourceIndex,
        graph: PacketGraph,
        layout_sources: LayoutSources,
    ) -> None:
        self.index = index
        self.graph = graph
        self.layout_sources = layout_sources
        self._requirejs_configs: dict[tuple[str, str], EffectiveRequireJsConfig] = {}

    def template_globals(self) -> None:
        """Link PHTML browser globals only through one co-declaring layout.

        A repository-wide name match is not runtime evidence. Both templates
        must be referenced by the same concrete layout XML source, and that
        source must expose exactly one defining template for the global name.
        """

        references_by_path = {
            path: self.index.optional_frontend_source(
                "template globals",
                path,
                extract_template_global_references,
            )
            for path in sorted(self.layout_sources.templates)
            if path.casefold().endswith(".phtml")
            and path in self.index.artifacts
        }
        definitions_by_name: dict[
            str,
            list[tuple[str, TemplateGlobalReference]],
        ] = {}
        calls: list[tuple[str, TemplateGlobalReference]] = []
        for path, references in references_by_path.items():
            for reference in references:
                if reference.relation == "defines":
                    definitions_by_name.setdefault(
                        reference.name,
                        [],
                    ).append((path, reference))
                elif reference.relation == "calls":
                    calls.append((path, reference))

        definition_callers: dict[
            tuple[str, str, int],
            set[str],
        ] = {}
        for caller_path, call in sorted(
            calls,
            key=lambda item: (item[0], item[1]),
        ):
            caller_layouts = self.layout_sources.templates[caller_path]
            candidates: dict[
                str,
                tuple[
                    TemplateGlobalReference,
                    set[tuple[str, str, str]],
                ],
            ] = {}
            for definition_path, definition in definitions_by_name.get(
                call.name,
                (),
            ):
                shared_layouts = caller_layouts.intersection(
                    self.layout_sources.templates[definition_path]
                )
                if not shared_layouts:
                    continue
                prior = candidates.get(definition_path)
                if prior is None or definition.line < prior[0].line:
                    candidates[definition_path] = (
                        definition,
                        set(shared_layouts),
                    )
                else:
                    prior[1].update(shared_layouts)
            if len(candidates) != 1:
                continue

            definition_path, (
                definition,
                shared_layouts,
            ) = next(iter(candidates.items()))
            global_name = f"window.{call.name}"
            layout_sources = ",".join(
                source_path
                for source_path, _, _ in sorted(shared_layouts)
            )
            packet = self.graph.packet(
                "magento-template-global",
                f"{global_name}:{definition_path}",
                globalName=global_name,
            )
            packet.add(GraphFact(
                "magento-template-global-call",
                global_name,
                "calls-unique-co-declared-definition",
                global_name,
                caller_path,
                call.line,
                attrs(
                    definitionLine=definition.line,
                    definitionPath=definition_path,
                    layoutSources=layout_sources,
                    resolution="exact-layout-source",
                    semanticRole="topology",
                    **{"retrievalIdentifier:0000": global_name},
                ),
            ), definition_path)
            definition_callers.setdefault(
                (call.name, definition_path, definition.line),
                set(),
            ).add(caller_path)

        for (
            name,
            definition_path,
            definition_line,
        ), caller_paths in sorted(definition_callers.items()):
            packet = self.graph.packet(
                "magento-template-global",
                f"window.{name}:{definition_path}",
                globalName=f"window.{name}",
            )
            packet.add(GraphFact(
                "magento-template-global-definition",
                f"window.{name}",
                "defined-in-layout-template",
                definition_path,
                definition_path,
                definition_line,
                attrs(
                    resolution="exact-layout-source",
                    semanticRole="topology",
                ),
            ), *sorted(caller_paths))

    def template_events(
        self,
        themes: tuple[ThemeRecord, ...],
    ) -> None:
        """Link exact browser events only across co-active layout handles.

        A shared concrete layout source proves co-activation directly.
        Magento's ``default`` handle is active on every page in its area, so it
        can also prove co-activation with one page-specific handle. Theme-owned
        sources must belong to the same inheritance chain; sibling themes are
        never joined.
        """

        references_by_path = {
            path: self.index.optional_frontend_source(
                "template events",
                path,
                extract_template_event_references,
            )
            for path in sorted(self.layout_sources.templates)
            if path.casefold().endswith(".phtml")
            and path in self.index.artifacts
        }
        listeners_by_event: dict[
            tuple[str, str],
            list[tuple[str, TemplateEventReference]],
        ] = {}
        dispatchers: list[tuple[str, TemplateEventReference]] = []
        for path, references in references_by_path.items():
            for reference in references:
                key = (reference.owner, reference.name)
                if reference.relation == "listens":
                    listeners_by_event.setdefault(key, []).append(
                        (path, reference)
                    )
                elif reference.relation == "dispatches":
                    dispatchers.append((path, reference))

        def theme_compatible(
            first_layout: str,
            second_layout: str,
        ) -> bool:
            first = self.index.theme_for_path(first_layout, themes)
            second = self.index.theme_for_path(second_layout, themes)
            if first is None or second is None:
                return True
            first_chain = {
                candidate.name
                for candidate in self.index.theme_chain(first, themes)
            }
            second_chain = {
                candidate.name
                for candidate in self.index.theme_chain(second, themes)
            }
            return first.name in second_chain or second.name in first_chain

        def coactive_layouts(
            first_path: str,
            second_path: str,
        ) -> tuple[tuple[str, str, str, str, str, str], ...]:
            proofs = set()
            for (
                first_layout,
                first_area,
                first_handle,
            ) in self.layout_sources.templates[first_path]:
                for (
                    second_layout,
                    second_area,
                    second_handle,
                ) in self.layout_sources.templates[second_path]:
                    if (
                        first_area != second_area
                        or not theme_compatible(
                            first_layout,
                            second_layout,
                        )
                    ):
                        continue
                    if first_layout == second_layout:
                        resolution = "shared-layout-source"
                    elif "default" in {first_handle, second_handle}:
                        resolution = "default-handle-coactivation"
                    else:
                        continue
                    proofs.add((
                        first_layout,
                        second_layout,
                        first_area,
                        first_handle,
                        second_handle,
                        resolution,
                    ))
            return tuple(sorted(proofs))

        listener_dispatchers: dict[
            tuple[str, str, str, int],
            set[tuple[str, int, tuple[str, ...]]],
        ] = {}
        for dispatcher_path, dispatch in sorted(
            dispatchers,
            key=lambda item: (item[0], item[1]),
        ):
            candidates: dict[
                str,
                tuple[
                    TemplateEventReference,
                    set[tuple[str, str, str, str, str, str]],
                ],
            ] = {}
            for listener_path, listener in listeners_by_event.get(
                (dispatch.owner, dispatch.name),
                (),
            ):
                if listener_path == dispatcher_path:
                    continue
                proofs = coactive_layouts(
                    dispatcher_path,
                    listener_path,
                )
                if not proofs:
                    continue
                prior = candidates.get(listener_path)
                if prior is None or listener.line < prior[0].line:
                    candidates[listener_path] = (
                        listener,
                        set(proofs),
                    )
                else:
                    prior[1].update(proofs)
            if len(candidates) != 1:
                continue

            listener_path, (listener, proofs) = next(iter(candidates.items()))
            event_identity = f"{dispatch.owner}:{dispatch.name}"
            layout_paths = tuple(sorted({
                path
                for proof in proofs
                for path in proof[:2]
            }))
            resolutions = ",".join(sorted({
                proof[5] for proof in proofs
            }))
            handles = ",".join(sorted({
                f"{proof[3]}->{proof[4]}" for proof in proofs
            }))
            packet = self.graph.packet(
                "magento-template-event",
                f"{event_identity}:{listener_path}",
                eventName=dispatch.name,
                eventOwner=dispatch.owner,
            )
            packet.add(GraphFact(
                "magento-template-event-dispatch",
                event_identity,
                "dispatches-to-unique-layout-listener",
                event_identity,
                dispatcher_path,
                dispatch.line,
                attrs(
                    handles=handles,
                    listenerLine=listener.line,
                    listenerPath=listener_path,
                    resolution=resolutions,
                    semanticRole="topology",
                ),
            ), listener_path, *layout_paths)
            listener_dispatchers.setdefault(
                (
                    dispatch.owner,
                    dispatch.name,
                    listener_path,
                    listener.line,
                ),
                set(),
            ).add((
                dispatcher_path,
                dispatch.line,
                layout_paths,
            ))

        for (
            owner,
            event_name,
            listener_path,
            listener_line,
        ), matched_dispatchers in sorted(listener_dispatchers.items()):
            event_identity = f"{owner}:{event_name}"
            dispatcher_paths = tuple(sorted({
                path
                for path, _, _ in matched_dispatchers
            }))
            layout_paths = tuple(sorted({
                layout_path
                for _, _, paths in matched_dispatchers
                for layout_path in paths
            }))
            packet = self.graph.packet(
                "magento-template-event",
                f"{event_identity}:{listener_path}",
                eventName=event_name,
                eventOwner=owner,
            )
            packet.add(GraphFact(
                "magento-template-event-listener",
                event_identity,
                "listens-to-layout-dispatchers",
                event_identity,
                listener_path,
                listener_line,
                attrs(
                    dispatcherCount=len(dispatcher_paths),
                    semanticRole="topology",
                ),
            ), *dispatcher_paths, *layout_paths)

    def requirejs(
        self,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
    ) -> None:
        records: dict[str, dict[str, object]] = {}
        for path, content in sorted(self.index.artifacts.items()):
            if PurePosixPath(path).name != "requirejs-config.js":
                continue
            theme = self.index.theme_for_path(path, themes)
            if not self.index.is_deployed_view_source(
                path,
                modules,
                themes,
            ):
                continue
            module = self.index.module_for_path(path, modules)
            theme_module = self.index.theme_module(path, theme)
            if theme is not None:
                relative = path[len(theme.root):].lstrip("/")
                if relative not in {
                    "requirejs-config.js",
                    (
                        f"{theme_module}/requirejs-config.js"
                        if theme_module
                        else ""
                    ),
                }:
                    # A same-named JavaScript file below `web/` is a static
                    # asset, not an input to RequireJs\Config\File\Collector.
                    continue
                area = theme.area
            else:
                if module is None:
                    continue
                relative = (
                    path[len(module.root):].lstrip("/")
                    if module.root
                    else path
                )
                area_match = re.fullmatch(
                    r"view/([^/]+)/requirejs-config\.js",
                    relative,
                )
                if area_match is None:
                    continue
                area = area_match.group(1)

            try:
                relations = extract_requirejs_relations(content)
            except OptionalJavaScriptEnrichmentError as exception:
                if not exception.source_specific:
                    raise
                self.index.record_optional_enrichment_failure(
                    "RequireJS",
                    exception,
                    path,
                )
                continue
            records[path] = {
                "path": path,
                "area": area,
                "theme": theme,
                "theme_module": theme_module,
                "module": module,
                "relations": relations,
            }
            packet = self.graph.packet(
                "magento-requirejs",
                f"{area}:{path}",
                area=area,
            )
            for relation in relations:
                source_paths = self.index.ui_asset_paths(
                    relation.source,
                    area,
                    False,
                    modules,
                    themes,
                    theme,
                )
                target_paths = self.index.ui_asset_paths(
                    relation.target,
                    area,
                    False,
                    modules,
                    themes,
                    theme,
                )
                packet.add(GraphFact(
                    f"magento-requirejs-{relation.kind}",
                    relation.source,
                    relation.relation,
                    relation.target,
                    path,
                    relation.line,
                    attrs(
                        area=area,
                        theme=theme.name if theme else "",
                        mapScope=relation.scope,
                        fallbackPosition=relation.position,
                    ),
                ), *source_paths, *target_paths, theme.theme_xml if theme else "")

        if not records:
            return

        def relation_identity(relation) -> tuple[str, ...] | None:
            if relation.kind == "path":
                return ("path", relation.source)
            if relation.kind == "map":
                return ("map", relation.scope, relation.source)
            if relation.kind == "mixin":
                return ("mixin", relation.source, relation.target)
            if relation.kind == "shim":
                return ("shim", relation.source)
            return None

        def signature(relations) -> tuple[tuple[str, str, int], ...]:
            return tuple(sorted(
                (
                    relation.relation,
                    relation.target,
                    relation.position,
                )
                for relation in relations
            ))

        module_order = {
            module.name: module.order
            for module in modules
            if module.enabled
        }
        runtime_areas = {
            str(record["area"])
            for record in records.values()
            if record["area"] != "base"
        }
        runtime_areas.update(theme.area for theme in themes)
        # Retain a base-only effective selection even when another area is
        # present so consumers in an otherwise unconfigured area can still
        # inherit Magento's base RequireJS configuration exactly.
        runtime_areas.add("base")

        selections = [
            (area, theme)
            for area in sorted(runtime_areas)
            for theme in (
                tuple(
                    candidate
                    for candidate in themes
                    if candidate.area == area
                )
                or (None,)
            )
        ]
        emitted_precedence: set[GraphFact] = set()
        for area, selected_theme in selections:
            ordered_records = sorted(
                (
                    record
                    for record in records.values()
                    if record["theme"] is None
                    and record["area"] in {"base", area}
                ),
                key=lambda record: (
                    (
                        record["module"].order
                        if record["module"] is not None
                        else -1
                    ),
                    0 if record["area"] == "base" else 1,
                    str(record["path"]),
                ),
            )
            if selected_theme is not None:
                for current_theme in reversed(
                    self.index.theme_chain(selected_theme, themes)
                ):
                    theme_records = [
                        record
                        for record in records.values()
                        if record["theme"] == current_theme
                    ]
                    ordered_records.extend(sorted(
                        (
                            record
                            for record in theme_records
                            if record["theme_module"]
                        ),
                        key=lambda record: (
                            module_order.get(
                                str(record["theme_module"]),
                                -1,
                            ),
                            str(record["path"]),
                        ),
                    ))
                    ordered_records.extend(sorted(
                        (
                            record
                            for record in theme_records
                            if not record["theme_module"]
                        ),
                        key=lambda record: str(record["path"]),
                    ))

            effective_theme = (
                selected_theme.name if selected_theme is not None else ""
            )
            self._requirejs_configs[(area, effective_theme)] = (
                build_effective_requirejs_config(
                    area,
                    effective_theme,
                    tuple(
                        (
                            str(record["path"]),
                            tuple(record["relations"]),
                        )
                        for record in ordered_records
                    ),
                )
            )

            declarations: dict[
                tuple[str, ...],
                list[tuple[dict[str, object], tuple[object, ...]]],
            ] = {}
            for record in ordered_records:
                grouped: dict[tuple[str, ...], list[object]] = {}
                for relation in record["relations"]:
                    identity = relation_identity(relation)
                    if identity is not None:
                        grouped.setdefault(identity, []).append(relation)
                for identity, relations in sorted(grouped.items()):
                    declarations.setdefault(identity, []).append((
                        record,
                        tuple(sorted(relations)),
                    ))

            for identity, values in sorted(declarations.items()):
                for position, (
                    (previous, previous_relations),
                    (current, current_relations),
                ) in enumerate(zip(values, values[1:]), start=1):
                    previous_signature = signature(previous_relations)
                    current_signature = signature(current_relations)
                    if previous_signature == current_signature:
                        continue
                    theme_specific = (
                        previous["theme"] is not None
                        or current["theme"] is not None
                    )
                    selection_theme = (
                        selected_theme.name
                        if theme_specific and selected_theme is not None
                        else ""
                    )
                    current_path = str(current["path"])
                    previous_path = str(previous["path"])
                    fact = GraphFact(
                        "magento-requirejs-override",
                        previous_path,
                        "overridden-by-config",
                        current_path,
                        current_path,
                        min(
                            relation.line
                            for relation in current_relations
                        ),
                        attrs(
                            area=area,
                            theme=selection_theme,
                            requireJsKind=identity[0],
                            identity=":".join(identity[1:]),
                            precedencePosition=position,
                            previousValue="|".join(
                                f"{relation}:{target}:{offset}"
                                for relation, target, offset
                                in previous_signature
                            ),
                            effectiveValue="|".join(
                                f"{relation}:{target}:{offset}"
                                for relation, target, offset
                                in current_signature
                            ),
                        ),
                        related_paths=(previous_path,),
                    )
                    if fact in emitted_precedence:
                        continue
                    emitted_precedence.add(fact)
                    packet = self.graph.packet(
                        "magento-requirejs-precedence",
                        (
                            f"{area}:{selection_theme or 'module'}:"
                            f"{':'.join(identity)}"
                        ),
                        area=area,
                        theme=selection_theme,
                    )
                    packet.add(fact)

    def frontend_source_areas(
        self,
        path: str,
        themes: tuple[ThemeRecord, ...],
    ) -> tuple[str, ...]:
        activated_areas = {
            area
            for sources in (
                self.layout_sources.templates,
                self.layout_sources.conditional_templates,
            )
            for _, area, _ in sources.get(path, ())
        }
        if activated_areas:
            return tuple(sorted(activated_areas))
        theme = self.index.theme_for_path(path, themes)
        if theme is not None:
            return (theme.area,)
        match = re.search(
            r"/view/([^/]+)/(?:templates|web)/",
            f"/{path}",
        )
        return (match.group(1),) if match is not None else ()

    def requirejs_configs_for_source(
        self,
        area: str,
        source_theme: ThemeRecord | None,
    ) -> tuple[EffectiveRequireJsConfig, ...]:
        if source_theme is not None:
            selected = self._requirejs_configs.get((
                area,
                source_theme.name,
            ))
            if selected is not None:
                return (selected,)
        candidates = tuple(sorted(
            (
                config
                for (config_area_name, _), config
                in self._requirejs_configs.items()
                if config_area_name == area
            ),
            key=lambda config: config.theme,
        ))
        if candidates:
            return candidates
        return tuple(sorted(
            (
                config
                for (config_area_name, _), config
                in self._requirejs_configs.items()
                if config_area_name == "base"
            ),
            key=lambda config: config.theme,
        ))

    def effective_frontend_dependency(
        self,
        requested: str,
        consumer: str,
        area: str,
        source_theme: ThemeRecord | None,
    ) -> tuple[
        tuple[RequireJsResolution, ...],
        EffectiveRequireJsConfig | None,
        str,
    ]:
        normalized = normalize_amd_dependency(requested, consumer)
        if not normalized:
            return (), None, "dynamic-or-relative-unresolved"
        configs = self.requirejs_configs_for_source(area, source_theme)
        if not configs:
            return (
                (RequireJsResolution(normalized),),
                None,
                "literal-module-id",
            )
        candidate_resolutions = tuple(
            config.resolve(requested, consumer)
            for config in configs
        )
        if len(candidate_resolutions) == 1:
            return (
                candidate_resolutions[0],
                configs[0],
                "effective-requirejs",
            )
        first = candidate_resolutions[0]
        if all(candidate == first for candidate in candidate_resolutions[1:]):
            # The effective identifier and its provenance are invariant across
            # every repository-known theme selection. Mixin activation still
            # abstains because a sibling theme may change only its mixin set.
            return first, None, "stable-across-theme-configs"
        return (
            (RequireJsResolution(normalized),),
            None,
            "theme-dependent-requirejs-abstained",
        )

    def selected_frontend_asset_paths(
        self,
        identifier: str,
        area: str,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
        source_theme: ThemeRecord | None,
    ) -> tuple[str, ...]:
        loader, marker, resource = identifier.partition("!")
        if marker and loader != "text":
            return ()
        asset_identifier = resource if marker else loader
        candidates = self.index.ui_asset_paths(
            asset_identifier,
            area,
            marker and loader == "text",
            modules,
            themes,
            source_theme,
        )
        if not candidates:
            return ()
        if source_theme is not None:
            for theme in self.index.theme_chain(source_theme, themes):
                selected = tuple(
                    path
                    for path in candidates
                    if path == theme.root or path.startswith(theme.root + "/")
                )
                if selected:
                    return tuple(sorted(selected))
        elif any(
            self.index.theme_for_path(path, themes) is not None
            for path in candidates
        ):
            # A module-owned consumer can execute under any configured store
            # theme. Do not connect it to sibling theme overrides without an
            # active-theme selection.
            return ()

        module_candidates = tuple(
            path
            for path in candidates
            if self.index.theme_for_path(path, themes) is None
        )
        area_marker = f"/view/{area}/web/"
        selected = tuple(
            path for path in module_candidates
            if area_marker in f"/{path}"
        )
        if selected:
            return tuple(sorted(selected))
        base = tuple(
            path for path in module_candidates
            if "/view/base/web/" in f"/{path}"
        )
        return tuple(sorted(base or module_candidates))

    def emit_frontend_dependency(
        self,
        *,
        packet_kind: str,
        packet_key: str,
        fact_kind: str,
        source: str,
        relation: str,
        requested: str,
        consumer: str,
        path: str,
        source_line: int,
        position: int,
        area: str,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
        source_theme: ThemeRecord | None,
        activation_paths: tuple[str, ...] = (),
        extra_attributes: dict[str, object] | None = None,
    ) -> None:
        resolutions, config, resolution_kind = (
            self.effective_frontend_dependency(
                requested,
                consumer,
                area,
                source_theme,
            )
        )
        if not resolutions:
            return
        packet = self.graph.packet(
            packet_kind,
            packet_key,
            area=area,
            theme=source_theme.name if source_theme else "",
        )
        normalized_requested = normalize_amd_dependency(
            requested,
            consumer,
        )
        for resolution in resolutions:
            target_paths = self.selected_frontend_asset_paths(
                resolution.identifier,
                area,
                modules,
                themes,
                source_theme,
            )
            loader, marker, _ = resolution.identifier.partition("!")
            packet.add(GraphFact(
                fact_kind,
                source,
                relation,
                resolution.identifier,
                path,
                source_line,
                attrs(**{
                    "area": area,
                    "fallbackPosition": resolution.fallback_position,
                    "loaderPlugin": loader if marker else "",
                    "mappedIdentifier": resolution.mapped_identifier,
                    "position": position,
                    "requestedIdentifier": requested,
                    "requireJsConfigKinds": ",".join(
                        resolution.config_kinds
                    ),
                    "requireJsConfigPaths": ",".join(
                        resolution.config_paths
                    ),
                    "resolution": resolution_kind,
                    "theme": source_theme.name if source_theme else "",
                    **(extra_attributes or {}),
                }),
            ),
                *target_paths,
                *resolution.config_paths,
                *activation_paths,
                source_theme.theme_xml if source_theme else "",
            )

            if config is None or marker:
                continue
            activation_certainty = str(
                (extra_attributes or {}).get(
                    "activationCertainty",
                    "exact",
                )
            )
            for mixin in config.mixins_for(
                normalized_requested,
                resolution.mapped_identifier,
                resolution.identifier,
            ):
                mixin_paths = self.selected_frontend_asset_paths(
                    mixin.target,
                    area,
                    modules,
                    themes,
                    source_theme,
                )
                packet.add(GraphFact(
                    "magento-requirejs-consumer-mixin",
                    resolution.identifier,
                    (
                        "loads-effective-mixin"
                        if activation_certainty == "exact"
                        else "conditionally-loads-effective-mixin"
                    ),
                    mixin.target,
                    path,
                    source_line,
                    attrs(
                        activationCertainty=activation_certainty,
                        area=area,
                        configLine=mixin.line,
                        configPath=mixin.path,
                        position=position,
                        theme=source_theme.name if source_theme else "",
                    ),
                ),
                    mixin.path,
                    *target_paths,
                    *mixin_paths,
                    *activation_paths,
                )

    def frontend_initializers(
        self,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
    ) -> None:
        sources = {
            path
            for path in self.layout_sources.templates
            if path.casefold().endswith(".phtml")
            and path in self.index.artifacts
        }
        sources.update(
            path
            for path in self.layout_sources.conditional_templates
            if path.casefold().endswith(".phtml")
            and path in self.index.artifacts
        )
        sources.update(
            path
            for path in self.index.artifacts
            if path.casefold().endswith(".html")
            and "/web/" in f"/{path}"
            and self.index.is_deployed_view_source(path, modules, themes)
        )
        for path in sorted(sources):
            references = self.index.optional_frontend_source(
                "frontend initializers",
                path,
                extract_frontend_initializers,
            )
            source_theme = self.index.theme_for_path(path, themes)
            for area in self.frontend_source_areas(path, themes):
                exact_layout_entries = tuple(sorted(
                    entry
                    for entry in self.layout_sources.templates.get(path, ())
                    if entry[1] == area
                ))
                conditional_layout_entries = tuple(sorted(
                    entry
                    for entry in self.layout_sources.conditional_templates.get(
                        path,
                        (),
                    )
                    if entry[1] == area
                ))
                layout_entries = tuple(sorted({
                    *exact_layout_entries,
                    *conditional_layout_entries,
                }))
                activation_certainty = (
                    "exact" if exact_layout_entries else "conditional"
                )
                activation_paths = tuple(sorted({
                    layout_path for layout_path, _, _ in layout_entries
                }))
                handles = ",".join(sorted({
                    handle for _, _, handle in layout_entries
                }))
                for reference in references:
                    self.emit_frontend_dependency(
                        packet_kind="magento-frontend-init",
                        packet_key=f"{area}:{path}",
                        fact_kind="magento-frontend-init",
                        source=(
                            f"{reference.source_kind}:{reference.selector}"
                        ),
                        relation=(
                            "initializes-component"
                            if activation_certainty == "exact"
                            else "conditionally-initializes-component"
                        ),
                        requested=reference.component,
                        consumer="",
                        path=path,
                        source_line=reference.line,
                        position=reference.position,
                        area=area,
                        modules=modules,
                        themes=themes,
                        source_theme=source_theme,
                        activation_paths=activation_paths,
                        extra_attributes={
                            "activationCertainty": activation_certainty,
                            "handles": handles,
                            "initKind": reference.source_kind,
                            "selector": reference.selector,
                        },
                    )

    def amd_source_identifier(
        self,
        path: str,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
    ) -> str:
        theme = self.index.theme_for_path(path, themes)
        if theme is not None:
            relative = path[len(theme.root):].lstrip("/")
            parts = relative.split("/", 2)
            if len(parts) != 3 or "_" not in parts[0] or parts[1] != "web":
                return ""
            module_name = parts[0]
            asset = parts[2]
        else:
            module = self.index.module_for_path(path, modules)
            if module is None:
                return ""
            relative = (
                path[len(module.root):].lstrip("/")
                if module.root
                else path
            )
            match = re.fullmatch(r"view/[^/]+/web/(.+)", relative)
            if match is None:
                return ""
            module_name = module.name
            asset = match.group(1)
        for suffix in (".jsx", ".mjs", ".js"):
            if asset.casefold().endswith(suffix):
                asset = asset[:-len(suffix)]
                break
        return f"{module_name}/{asset}" if asset else ""

    def amd_consumers(
        self,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
    ) -> None:
        sources = {
            path
            for path in self.index.artifacts
            if path.casefold().endswith((".js", ".mjs", ".jsx"))
            and "/web/" in f"/{path}"
            and PurePosixPath(path).name != "requirejs-config.js"
            and self.index.is_deployed_view_source(path, modules, themes)
        }
        sources.update(
            path
            for path in self.layout_sources.templates
            if path.casefold().endswith(".phtml")
            and path in self.index.artifacts
        )
        sources.update(
            path
            for path in self.layout_sources.conditional_templates
            if path.casefold().endswith(".phtml")
            and path in self.index.artifacts
        )
        for path in sorted(sources):
            is_template = path.casefold().endswith(".phtml")
            dependencies = self.index.optional_frontend_source(
                "AMD consumers",
                path,
                (
                    extract_template_amd_dependencies
                    if is_template
                    else extract_amd_dependencies
                ),
            )
            source_theme = self.index.theme_for_path(path, themes)
            source_identifier = (
                ""
                if is_template
                else self.amd_source_identifier(path, modules, themes)
            )
            for area in self.frontend_source_areas(path, themes):
                exact_layout_entries = tuple(sorted(
                    entry
                    for entry in self.layout_sources.templates.get(path, ())
                    if entry[1] == area
                ))
                conditional_layout_entries = tuple(sorted(
                    entry
                    for entry in self.layout_sources.conditional_templates.get(
                        path,
                        (),
                    )
                    if entry[1] == area
                ))
                layout_entries = tuple(sorted({
                    *exact_layout_entries,
                    *conditional_layout_entries,
                }))
                activation_certainty = (
                    "exact" if exact_layout_entries else "conditional"
                )
                activation_paths = tuple(sorted({
                    layout_path for layout_path, _, _ in layout_entries
                }))
                for dependency in dependencies:
                    consumer = dependency.named_module or source_identifier
                    normalized = normalize_amd_dependency(
                        dependency.dependency,
                        consumer,
                    )
                    if not normalized:
                        continue
                    self.emit_frontend_dependency(
                        packet_kind="magento-amd-consumer",
                        packet_key=f"{area}:{path}",
                        fact_kind="magento-amd-dependency",
                        source=consumer or path,
                        relation=(
                            (
                                "declares-dependency"
                                if dependency.consumer_kind == "define"
                                else "loads-module"
                            )
                            if activation_certainty == "exact"
                            else (
                                "conditionally-declares-dependency"
                                if dependency.consumer_kind == "define"
                                else "conditionally-loads-module"
                            )
                        ),
                        requested=dependency.dependency,
                        consumer=consumer,
                        path=path,
                        source_line=dependency.line,
                        position=dependency.position,
                        area=area,
                        modules=modules,
                        themes=themes,
                        source_theme=source_theme,
                        activation_paths=activation_paths,
                        extra_attributes={
                            "activationCertainty": activation_certainty,
                            "callKind": dependency.consumer_kind,
                            "namedModule": dependency.named_module,
                        },
                    )
