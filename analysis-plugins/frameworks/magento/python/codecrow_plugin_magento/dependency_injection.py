from __future__ import annotations

import json
from collections import deque

from codecrow_plugins import GraphFact, PluginDiagnostic, SymbolDefinition

from .architecture import ModuleRecord, PacketGraph, attrs, config_area, line, tag
from .resolution_index import RepositorySourceIndex
from .resolution_models import ConfigValue, DiState, _method_subject


class DependencyInjectionTopology:
    """Resolve effective dependency injection and generated-type relationships."""

    def __init__(self, index: RepositorySourceIndex, graph: PacketGraph) -> None:
        self.index = index
        self.graph = graph
        self._effective_di_arguments: dict[str, dict[str, dict]] = {}

    def di(self, modules: tuple[ModuleRecord, ...]) -> dict[str, DiState]:
        discovered_areas = {
            area
            for path in self.index.artifacts
            if (area := config_area(path, "di.xml")) not in {None, "initial", "global"}
        }
        states = {
            area: self.di_state(self.index.ordered_configs("di.xml", modules, area))
            for area in ("global", *sorted(discovered_areas))
        }
        global_state = states.get("global", DiState())
        descendants = self.descendants()

        for area, state in states.items():
            plugin_positions = self.plugin_priority_positions(state, area)
            effective_arguments = self.effective_argument_objects(
                state,
                area,
            )
            self._effective_di_arguments[area] = effective_arguments
            for interface, preference in sorted(state.preferences.items()):
                packet = self.graph.packet("magento-di", f"{area}:preference:{interface}", area=area)
                packet.add(GraphFact(
                    "magento-di-effective-preference",
                    interface,
                    "resolves-to",
                    preference.value,
                    preference.path,
                    preference.line,
                    attrs(area=area, module=preference.module, order=preference.order),
                ), self.index.symbol_path(interface), self.index.symbol_path(preference.value))

            for virtual_name, virtual_type in sorted(
                state.virtual_types.items()
            ):
                resolved = self.resolve_virtual_type(
                    virtual_name,
                    state,
                )
                inherited_objects = effective_arguments.get(
                    virtual_name,
                    {},
                )
                packet = self.graph.packet(
                    "magento-di-virtual-type",
                    f"{area}:{virtual_name}",
                    area=area,
                )
                packet.add(GraphFact(
                    "magento-di-virtual-type",
                    virtual_name,
                    "instantiates",
                    resolved,
                    virtual_type.path,
                    virtual_type.line,
                    attrs(
                        area=area,
                        configuredType=virtual_type.value,
                        module=virtual_type.module,
                        order=virtual_type.order,
                    ),
                ),
                    self.index.symbol_path(resolved),
                    *self.resolution_paths(virtual_name, state),
                    *(
                        configured.path
                        for configured, _ in inherited_objects.values()
                    ),
                )

            for (target, plugin_name), plugin in sorted(state.plugins.items()):
                plugin_attrs = dict(plugin.attributes)
                disabled = plugin_attrs.get("disabled", "false").casefold() in {"1", "true"}
                plugin_class = plugin.value
                relation = "disables-interceptor" if disabled else "intercepted-by"
                packet = self.graph.packet(
                    "magento-interception",
                    f"{area}:{target}:{plugin_name}",
                    area=area,
                    plugin=plugin_name,
                )
                packet.add(GraphFact(
                    "magento-di-effective-plugin",
                    target,
                    relation,
                    plugin_class,
                    plugin.path,
                    plugin.line,
                    attrs(**{
                        "area": area,
                        "module": plugin.module,
                        **plugin_attrs,
                        "name": plugin_name,
                        "effectivePriorityPosition": plugin_positions.get(
                            (target, plugin_name),
                            "",
                        ),
                    }),
                ), self.index.symbol_path(target), self.index.symbol_path(plugin_class))

                affected = {target, *descendants.get(target, set())}
                preferred = self.resolve_type(target, state)
                affected.add(preferred)
                affected.update(descendants.get(preferred, set()))
                for affected_type in sorted(affected)[:200]:
                    affected_path = self.index.symbol_path(affected_type)
                    if affected_type != target:
                        # Magento merges inherited plugin configuration by plugin
                        # name. A direct declaration on the concrete type is the
                        # effective override (including a disable) and must not
                        # coexist with a contradictory inherited fact.
                        if (affected_type, plugin_name) in state.plugins:
                            continue
                        packet.add(GraphFact(
                            "magento-di-inherited-plugin",
                            affected_type,
                            relation,
                            plugin_class,
                            plugin.path,
                            plugin.line,
                            attrs(area=area, declaredFor=target, name=plugin_name),
                        ), affected_path)
                plugin_symbol = self.index.symbol(plugin_class)
                if not disabled and plugin_symbol:
                    for method in plugin_symbol.methods:
                        subject = _method_subject(method)
                        if subject:
                            phase, intercepted_method = subject
                            applicability, reason, target_path = (
                                self.interception_applicability(
                                    target,
                                    intercepted_method,
                                    state,
                                    plugin_symbol,
                                    method,
                                )
                            )
                            if applicability is True:
                                packet.add(GraphFact(
                                    "magento-intercepted-method",
                                    plugin_class,
                                    phase,
                                    f"{target}::{intercepted_method}",
                                    plugin_symbol.path,
                                    plugin_symbol.line,
                                    attrs(area=area, plugin=plugin_name),
                                ), target_path)
                            elif applicability is False:
                                packet.add(GraphFact(
                                    "magento-interceptor-inapplicable",
                                    plugin_class,
                                    "cannot-intercept",
                                    f"{target}::{intercepted_method}",
                                    plugin.path,
                                    plugin.line,
                                    attrs(
                                        area=area,
                                        plugin=plugin_name,
                                        phase=phase,
                                        reason=reason,
                                        semanticRole="diagnostic",
                                    ),
                                ), plugin_symbol.path, target_path)

            by_target: dict[str, list[tuple[str, ConfigValue]]] = {}
            for (target, plugin_name), plugin in state.plugins.items():
                if (target, plugin_name) not in plugin_positions:
                    continue
                by_target.setdefault(target, []).append((plugin_name, plugin))
            for target, plugins in sorted(by_target.items()):
                ordered = sorted(
                    plugins,
                    key=lambda item: plugin_positions[(target, item[0])],
                )
                packet = self.graph.packet(
                    "magento-interception-order",
                    f"{area}:{target}",
                    area=area,
                    observedType=target,
                )
                for (
                    (plugin_name, plugin),
                    (next_name, next_plugin),
                ) in zip(ordered, ordered[1:]):
                    packet.add(GraphFact(
                        "magento-di-plugin-priority",
                        plugin.value,
                        "prioritized-before",
                        next_plugin.value,
                        plugin.path,
                        plugin.line,
                        attrs(
                            area=area,
                            observedType=target,
                            plugin=plugin_name,
                            nextPlugin=next_name,
                            position=plugin_positions[(target, plugin_name)],
                            nextPosition=plugin_positions[(target, next_name)],
                            sortOrder=dict(plugin.attributes).get("sortOrder", "0"),
                            nextSortOrder=dict(next_plugin.attributes).get(
                                "sortOrder",
                                "0",
                            ),
                        ),
                    ), next_plugin.path, self.index.symbol_path(plugin.value),
                       self.index.symbol_path(next_plugin.value))

            for owner, objects in sorted(effective_arguments.items()):
                inheritance_paths = self.argument_inheritance_paths(
                    owner,
                    state,
                )
                for (
                    (argument_name, item_name),
                    (argument, declared_for),
                ) in sorted(objects.items()):
                    resolved = self.resolve_type(argument.value, state)
                    resolution_paths = self.resolution_paths(
                        argument.value,
                        state,
                    )
                    packet = self.graph.packet(
                        "magento-di",
                        (
                            f"{area}:argument:{owner}:"
                            f"{argument_name}:{item_name}"
                        ),
                        area=area,
                    )
                    packet.add(GraphFact(
                        "magento-di-argument",
                        owner,
                        "injects",
                        resolved,
                        argument.path,
                        argument.line,
                        attrs(
                            area=area,
                            argument=argument_name,
                            item=item_name,
                            configured=argument.value,
                            declaredFor=declared_for,
                            inherited=(
                                "true"
                                if declared_for != owner
                                else ""
                            ),
                            virtualType=(
                                "true"
                                if owner in state.virtual_types
                                else ""
                            ),
                        ),
                    ),
                        self.index.symbol_path(owner),
                        self.index.symbol_path(resolved),
                        *inheritance_paths,
                        *resolution_paths,
                    )

            self.price_pool_packets(
                state,
                area,
                global_state if area != "global" else None,
            )

        # Area state that equals global does not need duplicate constructor packets.
        if "global" not in states:
            states["global"] = global_state
        return states

    def di_state(self, documents: tuple[tuple[str, ModuleRecord | None, int], ...]) -> DiState:
        state = DiState()
        plugin_position = 0
        argument_items: dict[tuple[str, str], set[str]] = {}

        def clear_argument(owner: str, argument_name: str) -> None:
            argument_key = (owner, argument_name)
            for item_name in argument_items.pop(argument_key, set()):
                key = (owner, argument_name, item_name)
                state.arguments.pop(key, None)
                state.item_types.pop(key, None)
                state.item_values.pop(key, None)

        def clear_item(owner: str, argument_name: str, item_name: str) -> None:
            prefix = item_name + "/"
            argument_key = (owner, argument_name)
            names = argument_items.setdefault(argument_key, set())
            removed = {
                candidate
                for candidate in names
                if candidate == item_name or candidate.startswith(prefix)
            }
            for candidate in removed:
                key = (owner, argument_name, candidate)
                state.arguments.pop(key, None)
                state.item_types.pop(key, None)
                state.item_values.pop(key, None)
            names.difference_update(removed)

        def xsi_type(element) -> str:
            return next(
                (
                    value
                    for key, value in element.attrib.items()
                    if key.endswith("}type") or key == "xsi:type"
                ),
                "",
            )

        def merge_array_items(
            element,
            owner: str,
            argument_name: str,
            item_path: str,
            source_path: str,
            content: str,
            module_name: str,
            order: int,
        ) -> None:
            for item in element:
                if tag(item) != "item" or not item.get("name"):
                    continue
                item_name = (
                    f"{item_path}/{item.get('name')}"
                    if item_path
                    else item.get("name")
                )
                key = (owner, argument_name, item_name)
                item_type = xsi_type(item)
                prior_type = state.item_types.get(key)
                if prior_type is None or prior_type.value != item_type:
                    clear_item(owner, argument_name, item_name)
                state.item_types[key] = ConfigValue(
                    item_type,
                    source_path,
                    line(content, item.get("name")),
                    module_name,
                    order,
                )
                argument_items.setdefault(
                    (owner, argument_name),
                    set(),
                ).add(item_name)
                if item_type == "array":
                    merge_array_items(
                        item,
                        owner,
                        argument_name,
                        item_name,
                        source_path,
                        content,
                        module_name,
                        order,
                    )
                    continue
                if item_type != "object":
                    clear_item(owner, argument_name, item_name)
                    state.item_types[key] = ConfigValue(
                        item_type,
                        source_path,
                        line(content, item.get("name")),
                        module_name,
                        order,
                    )
                    argument_items.setdefault(
                        (owner, argument_name),
                        set(),
                    ).add(item_name)
                    configured = (item.text or "").strip().lstrip("\\")
                    if configured:
                        state.item_values[key] = ConfigValue(
                            configured,
                            path=source_path,
                            line=line(content, configured),
                            module=module_name,
                            order=order,
                        )
                    continue
                configured = (item.text or "").strip().lstrip("\\")
                if configured:
                    state.arguments[key] = ConfigValue(
                        configured,
                        path=source_path,
                        line=line(content, configured),
                        module=module_name,
                        order=order,
                    )

        for path, module, order in documents:
            root = self.index.xml(path)
            if root is None:
                continue
            module_name = module.name if module else "application"
            content = self.index.artifacts[path]
            for element in root.iter():
                element_tag = tag(element)
                if element_tag == "preference" and element.get("for") and element.get("type"):
                    state.preferences[element.get("for").lstrip("\\")] = ConfigValue(
                        element.get("type").lstrip("\\"), path,
                        line(content, element.get("for")), module_name, order,
                    )
                elif element_tag == "virtualType" and element.get("name") and element.get("type"):
                    state.virtual_types[element.get("name").lstrip("\\")] = ConfigValue(
                        element.get("type").lstrip("\\"), path,
                        line(content, element.get("name")), module_name, order,
                    )
                elif element_tag == "plugin" and element.get("name"):
                    parent = next((candidate for candidate in root.iter() if element in list(candidate)), None)
                    target = parent.get("name") if parent is not None else None
                    prior = state.plugins.get((target or "", element.get("name")))
                    plugin_class = element.get("type") or (prior.value if prior else "")
                    if target and plugin_class:
                        merged_attrs = dict(prior.attributes) if prior else {}
                        merged_attrs.update({
                            key: value for key, value in element.attrib.items()
                            if key not in {"name", "type"}
                        })
                        state.plugins[(target.lstrip("\\"), element.get("name"))] = ConfigValue(
                            plugin_class.lstrip("\\"), path,
                            line(content, element.get("name")), module_name, order,
                            tuple(sorted(merged_attrs.items())),
                            plugin_position,
                        )
                        plugin_position += 1

            for owner in root.iter():
                if tag(owner) not in {"type", "virtualType"} or not owner.get("name"):
                    continue
                owner_name = owner.get("name").lstrip("\\")
                for arguments in owner:
                    if tag(arguments) != "arguments":
                        continue
                    for argument in arguments:
                        if tag(argument) != "argument" or not argument.get("name"):
                            continue
                        argument_name = argument.get("name")
                        argument_key = (owner_name, argument_name)
                        argument_type = xsi_type(argument)
                        prior_type = state.argument_types.get(argument_key)
                        if prior_type is None or prior_type.value != argument_type:
                            clear_argument(owner_name, argument_name)
                        state.argument_types[argument_key] = ConfigValue(
                            argument_type,
                            path,
                            line(content, argument_name),
                            module_name,
                            order,
                        )
                        if argument_type == "array":
                            merge_array_items(
                                argument,
                                owner_name,
                                argument_name,
                                "",
                                path,
                                content,
                                module_name,
                                order,
                            )
                            continue
                        clear_argument(owner_name, argument_name)
                        state.argument_types[argument_key] = ConfigValue(
                            argument_type,
                            path,
                            line(content, argument_name),
                            module_name,
                            order,
                        )
                        if argument_type != "object":
                            continue
                        configured = (argument.text or "").strip().lstrip("\\")
                        if configured:
                            argument_items.setdefault(
                                argument_key,
                                set(),
                            ).add("")
                            state.arguments[
                                (owner_name, argument_name, "")
                            ] = ConfigValue(
                                configured,
                                path,
                                line(content, configured),
                                module_name,
                                order,
                            )
        return state

    def price_pool_packets(
        self,
        state: DiState,
        area: str,
        global_state: DiState | None = None,
    ) -> None:
        """Join exact PHP price-code reads to Magento's effective price pool."""
        canonical_pool = r"Magento\Catalog\Pricing\Price\Pool"
        framework_pool = r"Magento\Framework\Pricing\Price\Pool"
        registrations: dict[
            tuple[str, str],
            tuple[str, ConfigValue, SymbolDefinition],
        ] = {}

        for (
            owner,
            argument_name,
            item_name,
        ), configured in sorted(state.item_values.items()):
            state_key = (owner, argument_name, item_name)
            item_type = state.item_types.get(state_key)
            if (
                global_state is not None
                and configured == global_state.item_values.get(state_key)
                and item_type == global_state.item_types.get(state_key)
            ):
                # A global registration applies to every area. Only publish an
                # area packet when scoped DI actually changes the effective
                # entry, otherwise prompt selection would repeat the same fact.
                continue
            if (
                argument_name != "prices"
                or not item_name
                or "/" in item_name
                or item_type is None
                or item_type.value != "string"
            ):
                continue
            resolved_owner = (
                self.resolve_virtual_type(owner, state)
                if owner in state.virtual_types
                else ""
            )
            if owner != canonical_pool and resolved_owner != framework_pool:
                continue
            provider = self.index.unique_symbol_casefold(configured.value)
            if provider is None:
                continue
            declared = None
            for key, value in provider.attributes:
                if not key.startswith("php-class-constant:"):
                    continue
                try:
                    candidate = json.loads(value)
                except (TypeError, ValueError):
                    continue
                if (
                    candidate.get("name") == "PRICE_CODE"
                    and candidate.get("value") == item_name
                ):
                    declared = candidate
                    break
            if declared is None:
                continue
            registrations[(provider.qualified_name, item_name)] = (
                owner,
                configured,
                provider,
            )
            packet = self.graph.packet(
                "magento-price-pool",
                f"{area}:registration:{owner}:{item_name}",
                area=area,
                pool=owner,
            )
            packet.add(GraphFact(
                "magento-price-pool-registration",
                owner,
                "registers-price-model",
                provider.qualified_name,
                configured.path,
                configured.line,
                attrs(
                    area=area,
                    argument="prices",
                    priceCode=item_name,
                ),
            ), provider.path)

        if not registrations:
            return
        registration_by_provider = {
            provider_name: (price_code, registration)
            for (
                provider_name,
                price_code,
            ), registration in registrations.items()
        }
        for consumer in self.index.symbols:
            for key, value in consumer.attributes:
                if not key.startswith("php-class-constant-reference:"):
                    continue
                try:
                    reference = json.loads(value)
                except (TypeError, ValueError):
                    continue
                if (
                    str(reference.get("argumentOf", "")).casefold()
                    != "getprice"
                    or reference.get("constant") != "PRICE_CODE"
                ):
                    continue
                provider_name = str(
                    reference.get("target", "")
                ).lstrip("\\")
                resolved_registration = registration_by_provider.get(
                    provider_name
                )
                if resolved_registration is None:
                    continue
                price_code, registration = resolved_registration
                identity = (provider_name, price_code)
                owner, configured, provider = registration
                try:
                    reference_line = max(1, int(reference.get("line", 1)))
                except (TypeError, ValueError):
                    reference_line = consumer.line
                packet = self.graph.packet(
                    "magento-price-pool-reference",
                    (
                        f"{area}:{consumer.qualified_name}:"
                        f"{provider.qualified_name}:{identity[1]}"
                    ),
                    area=area,
                    pool=owner,
                )
                packet.add(GraphFact(
                    "magento-price-pool-reference",
                    consumer.qualified_name,
                    "requests-registered-price",
                    provider.qualified_name,
                    consumer.path,
                    reference_line,
                    attrs(
                        area=area,
                        configPath=configured.path,
                        pool=owner,
                        priceCode=identity[1],
                    ),
                ), configured.path, provider.path)

    def plugin_priority_positions(
        self,
        state: DiState,
        area: str,
    ) -> dict[tuple[str, str], int]:
        by_target: dict[str, list[tuple[str, ConfigValue, int]]] = {}
        invalid_targets: set[str] = set()
        for (target, plugin_name), plugin in sorted(state.plugins.items()):
            plugin_attrs = dict(plugin.attributes)
            if plugin_attrs.get("disabled", "false").casefold() in {"1", "true"}:
                continue
            raw_sort_order = plugin_attrs.get("sortOrder", "0")
            try:
                sort_order = int(raw_sort_order)
            except ValueError:
                invalid_targets.add(target)
                self.index.diagnostics.append(PluginDiagnostic(
                    "magento-plugin-sort-order-invalid",
                    (
                        f"{area} plugin {plugin_name!r} for {target} has "
                        f"non-integer sortOrder {raw_sort_order!r}"
                    ),
                    self.index.plugin_id,
                ))
                continue
            by_target.setdefault(target, []).append(
                (plugin_name, plugin, sort_order)
            )

        positions: dict[tuple[str, str], int] = {}
        for target, plugins in sorted(by_target.items()):
            # A single invalid member makes the complete priority chain
            # unknowable; keep each raw plugin fact but emit no ordering claim.
            if target in invalid_targets:
                continue
            ordered = sorted(
                plugins,
                key=lambda item: (
                    item[2],
                    item[1].order,
                    item[1].position,
                    item[0],
                ),
            )
            positions.update({
                (target, plugin_name): position
                for position, (plugin_name, _, _) in enumerate(ordered)
            })
        return positions

    def php_argument_bases(self, owner: str) -> tuple[str, ...]:
        """Mirror ClassReader parent order recorded by the PHP repository plugin."""
        symbol = self.index.symbol(owner)
        if symbol is None or symbol.kind != "class":
            return ()
        parent_class = next(
            (
                value
                for key, value in symbol.attributes
                if key == "php-parent-class"
            ),
            "",
        )

        def declared_interfaces(
            candidate: SymbolDefinition | None,
        ) -> tuple[str, ...]:
            if candidate is None:
                return ()
            return tuple(
                value
                for key, value in sorted(candidate.attributes)
                if key.startswith("php-interface:")
            )

        def interface_closure(
            interface: str,
            seen: set[str],
        ) -> tuple[str, ...]:
            if interface in seen:
                return ()
            seen.add(interface)
            result = [interface]
            interface_symbol = self.index.symbol(interface)
            if interface_symbol is not None:
                for key, parent in sorted(interface_symbol.attributes):
                    if key.startswith("php-parent-interface:"):
                        result.extend(interface_closure(parent, seen))
            return tuple(result)

        inherited_interfaces: set[str] = set()
        current_parent = self.index.symbol(parent_class)
        while current_parent is not None and current_parent.kind == "class":
            for interface in declared_interfaces(current_parent):
                inherited_interfaces.update(
                    interface_closure(interface, set())
                )
            next_parent = next(
                (
                    value
                    for key, value in current_parent.attributes
                    if key == "php-parent-class"
                ),
                "",
            )
            current_parent = self.index.symbol(next_parent)

        interfaces: list[str] = []
        seen_interfaces: set[str] = set()
        for interface in declared_interfaces(symbol):
            for candidate in interface_closure(
                interface,
                seen_interfaces,
            ):
                if candidate not in inherited_interfaces:
                    interfaces.append(candidate)

        return tuple(
            value
            for value in (parent_class, *interfaces)
            if value
        )

    def argument_inheritance_paths(
        self,
        owner: str,
        state: DiState,
    ) -> tuple[str, ...]:
        """Return only sources that can contribute effective DI arguments."""
        paths: set[str] = set()
        visiting: set[str] = set()

        def collect(candidate: str) -> None:
            if candidate in visiting:
                return
            visiting.add(candidate)
            try:
                virtual_type = state.virtual_types.get(candidate)
                if virtual_type is not None:
                    paths.add(virtual_type.path)
                    instance_path = self.index.symbol_path(virtual_type.value)
                    if instance_path:
                        paths.add(instance_path)
                    collect(virtual_type.value)
                    return
                for base in self.php_argument_bases(candidate):
                    base_path = self.index.symbol_path(base)
                    if base_path:
                        paths.add(base_path)
                    collect(base)
            finally:
                visiting.remove(candidate)

        collect(owner)
        return tuple(sorted(paths))

    @staticmethod
    def remove_argument_subtree(
        values: dict[tuple[str, str], object],
        argument_name: str,
        item_name: str = "",
    ) -> None:
        prefix = item_name + "/" if item_name else ""
        for key in tuple(values):
            argument, item = key
            if argument != argument_name:
                continue
            if not item_name or item == item_name or item.startswith(prefix):
                values.pop(key, None)

    def effective_argument_objects(
        self,
        state: DiState,
        area: str,
    ) -> dict[
        str,
        dict[tuple[str, str], tuple[ConfigValue, str]],
    ]:
        """Reproduce Config::_collectConfiguration for object-valued leaves.

        Parent relations replace complete top-level arguments in their runtime
        order. A type's own arguments then use array_replace_recursive, so only
        same-named nested array items merge; scalar/object type changes remove
        inherited descendants.
        """
        memo: dict[
            str,
            tuple[
                dict[tuple[str, str], ConfigValue],
                dict[tuple[str, str], tuple[ConfigValue, str]],
            ],
        ] = {}
        visiting: list[str] = []

        def local(owner: str):
            types = {
                (argument, ""): configured
                for (configured_owner, argument), configured
                in state.argument_types.items()
                if configured_owner == owner
            }
            types.update({
                (argument, item): configured
                for (configured_owner, argument, item), configured
                in state.item_types.items()
                if configured_owner == owner
            })
            objects = {
                (argument, item): (configured, owner)
                for (configured_owner, argument, item), configured
                in state.arguments.items()
                if configured_owner == owner
            }
            return types, objects

        def replace_argument(
            destination_types,
            destination_objects,
            source_types,
            source_objects,
            argument_name: str,
        ) -> None:
            self.remove_argument_subtree(
                destination_types,
                argument_name,
            )
            self.remove_argument_subtree(
                destination_objects,
                argument_name,
            )
            destination_types.update({
                key: value
                for key, value in source_types.items()
                if key[0] == argument_name
            })
            destination_objects.update({
                key: value
                for key, value in source_objects.items()
                if key[0] == argument_name
            })

        def overlay_local(
            inherited_types,
            inherited_objects,
            local_types,
            local_objects,
        ) -> None:
            local_arguments = sorted({
                argument
                for argument, item in local_types
                if item == ""
            })
            for argument in local_arguments:
                local_root = local_types[(argument, "")]
                inherited_root = inherited_types.get((argument, ""))
                if (
                    inherited_root is None
                    or inherited_root.value != "array"
                    or local_root.value != "array"
                ):
                    replace_argument(
                        inherited_types,
                        inherited_objects,
                        local_types,
                        local_objects,
                        argument,
                    )
                    continue

                # array_replace_recursive keeps inherited named items unless a
                # local item with the same path replaces or recursively merges it.
                inherited_types[(argument, "")] = local_root
                local_items = sorted(
                    (
                        (item, configured)
                        for (candidate_argument, item), configured
                        in local_types.items()
                        if candidate_argument == argument and item
                    ),
                    key=lambda item: (
                        item[0].count("/"),
                        item[0],
                    ),
                )
                for item_name, configured in local_items:
                    key = (argument, item_name)
                    inherited = inherited_types.get(key)
                    if (
                        inherited is None
                        or inherited.value != "array"
                        or configured.value != "array"
                    ):
                        self.remove_argument_subtree(
                            inherited_types,
                            argument,
                            item_name,
                        )
                        self.remove_argument_subtree(
                            inherited_objects,
                            argument,
                            item_name,
                        )
                    inherited_types[key] = configured
                    if key in local_objects:
                        inherited_objects[key] = local_objects[key]

        def collect(owner: str):
            if owner in memo:
                types, objects = memo[owner]
                return dict(types), dict(objects)
            if owner in visiting:
                cycle = " -> ".join((*visiting, owner))
                raise ValueError(
                    f"DI argument inheritance cycle in {area}: {cycle}"
                )
            visiting.append(owner)
            try:
                if owner in state.virtual_types:
                    base = state.virtual_types[owner].value
                    inherited_types, inherited_objects = collect(base)
                else:
                    inherited_types = {}
                    inherited_objects = {}
                    for base in self.php_argument_bases(owner):
                        base_types, base_objects = collect(base)
                        # Config::_collectConfiguration uses array_replace
                        # between parent/interface relations.
                        for argument in sorted({
                            name
                            for name, item in base_types
                            if item == ""
                        }):
                            replace_argument(
                                inherited_types,
                                inherited_objects,
                                base_types,
                                base_objects,
                                argument,
                            )
                local_types, local_objects = local(owner)
                overlay_local(
                    inherited_types,
                    inherited_objects,
                    local_types,
                    local_objects,
                )
                memo[owner] = (
                    dict(inherited_types),
                    dict(inherited_objects),
                )
                return inherited_types, inherited_objects
            finally:
                visiting.pop()

        owners = {
            owner for owner, _ in state.argument_types
        } | set(state.virtual_types)
        result = {}
        try:
            for owner in sorted(owners):
                _, objects = collect(owner)
                result[owner] = objects
        except ValueError as exception:
            self.index.diagnostics.append(PluginDiagnostic(
                "magento-di-argument-inheritance-cycle",
                str(exception),
                self.index.plugin_id,
            ))
            return {}
        return result

    @staticmethod
    def resolve_virtual_type(
        requested: str,
        state: DiState,
    ) -> str:
        current = requested.lstrip("\\")
        seen: set[str] = set()
        while current not in seen and current in state.virtual_types:
            seen.add(current)
            current = state.virtual_types[current].value
        return current

    def resolve_type(self, requested: str, state: DiState) -> str:
        current = requested.lstrip("\\")
        # ObjectManager resolves the complete preference chain before the
        # factory resolves the requested virtual type. It does not re-apply a
        # preference to the concrete base reached inside getInstanceType().
        for mapping in (state.preferences, state.virtual_types):
            seen: set[str] = set()
            while current not in seen and current in mapping:
                seen.add(current)
                current = mapping[current].value
        return current

    def resolution_paths(self, requested: str, state: DiState) -> tuple[str, ...]:
        """Return every configuration source participating in type resolution."""
        current = requested.lstrip("\\")
        paths: set[str] = set()
        for mapping in (state.preferences, state.virtual_types):
            seen: set[str] = set()
            while current not in seen and current in mapping:
                seen.add(current)
                configured = mapping[current]
                paths.add(configured.path)
                current = configured.value
        return tuple(sorted(paths))

    def constructor_packets(
        self,
        modules: tuple[ModuleRecord, ...],
        states: dict[str, DiState],
    ) -> None:
        global_state = states.get("global", DiState())
        for symbol in self.index.symbols:
            if not symbol.constructor_types:
                continue
            module = self.index.module_for_path(symbol.path, modules)
            for area, state in states.items():
                resolutions = tuple(
                    (requested, self.resolve_type(requested, state))
                    for requested in symbol.constructor_types
                )
                global_resolutions = tuple(
                    (requested, self.resolve_type(requested, global_state))
                    for requested in symbol.constructor_types
                )
                if area != "global" and resolutions == global_resolutions:
                    continue
                packet = self.graph.packet(
                    "magento-object-graph",
                    f"{area}:{symbol.qualified_name}",
                    area=area,
                    module=module.name if module else "",
                )
                for requested, resolved in resolutions:
                    resolution_paths = self.resolution_paths(requested, state)
                    packet.add(GraphFact(
                        "php-constructor-dependency",
                        symbol.qualified_name,
                        "requests",
                        requested,
                        symbol.path,
                        symbol.line,
                        attrs(area=area),
                    ), self.index.symbol_path(requested), *resolution_paths)
                    # An identity result is not a Magento DI resolution.  In
                    # particular, an unconfigured interface cannot be created
                    # by the object manager, so claiming that it resolves to
                    # itself would leave false architecture context after a
                    # preference is removed.  Keep the language-level request
                    # edge above and emit this framework edge only when the
                    # effective Magento configuration actually maps the type.
                    if resolved != requested:
                        packet.add(GraphFact(
                            "magento-object-resolution",
                            requested,
                            "resolves-to",
                            resolved,
                            symbol.path,
                            symbol.line,
                            attrs(area=area, consumer=symbol.qualified_name),
                        ), self.index.symbol_path(resolved), *resolution_paths)

    def generated_factory_packets(
        self,
        modules: tuple[ModuleRecord, ...],
        states: dict[str, DiState],
    ) -> None:
        """Resolve only Magento's exact, absent ``<type>Factory`` convention.

        PHP cannot link a constructor dependency to a class that is intentionally
        absent from source. Magento owns the missing-class semantics: its object
        manager generates the factory, while a factory targeting an interface
        follows the effective DI preference. Keep that framework knowledge here
        and abstain whenever source identity or deployment state is ambiguous.
        """
        global_state = states.get("global", DiState())
        for consumer in self.index.symbols:
            if consumer.kind != "class" or not consumer.constructor_types:
                continue
            consumer_module = self.index.module_for_path(consumer.path, modules)
            if consumer_module is None or not consumer_module.enabled:
                continue
            for requested_factory in consumer.constructor_types:
                factory_type = requested_factory.lstrip("\\")
                if (
                    not factory_type.endswith("Factory")
                    or len(factory_type) <= len("Factory")
                ):
                    continue
                # A declared class is a custom factory. Its create() semantics
                # belong to PHP source analysis and must not be guessed from its
                # name. Ambiguous declarations are equally unsafe.
                if self.index.symbols_by_casefold.get(factory_type.casefold()):
                    continue

                requested_type = factory_type[:-len("Factory")]
                target_candidates = self.index.symbols_by_casefold.get(
                    requested_type.casefold(),
                    (),
                )
                if len(target_candidates) != 1:
                    continue
                target_symbol = target_candidates[0]
                if target_symbol.kind not in {"class", "interface"}:
                    continue
                target_module = self.index.module_for_path(
                    target_symbol.path,
                    modules,
                )
                if target_module is None or not target_module.enabled:
                    continue

                requested_type = target_symbol.qualified_name
                packet = self.graph.packet(
                    "magento-generated-factory",
                    f"{consumer.qualified_name}:{factory_type}",
                    consumerModule=consumer_module.name,
                    factoryType=factory_type,
                    targetModule=target_module.name,
                )
                packet.add(GraphFact(
                    "magento-generated-factory",
                    consumer.qualified_name,
                    "uses-generated-factory-for",
                    requested_type,
                    consumer.path,
                    consumer.line,
                    attrs(
                        consumerModule=consumer_module.name,
                        factoryType=factory_type,
                        generated="true",
                        requestedKind=target_symbol.kind,
                        targetModule=target_module.name,
                    ),
                ), target_symbol.path)

                global_resolution = self.resolve_type(
                    requested_type,
                    global_state,
                )
                for area, state in states.items():
                    resolved = self.resolve_type(requested_type, state)
                    if (
                        area != "global"
                        and resolved == global_resolution
                    ):
                        continue
                    # An interface without a preference is not constructible.
                    # Keep the exact factory-target edge above, but do not claim
                    # a runtime-created implementation that Magento cannot prove.
                    if (
                        target_symbol.kind == "interface"
                        and resolved == requested_type
                    ):
                        continue
                    resolved_symbol = self.index.unique_symbol_casefold(resolved)
                    canonical_resolved = (
                        resolved_symbol.qualified_name
                        if resolved_symbol is not None
                        else resolved.lstrip("\\")
                    )
                    resolution_paths = self.resolution_paths(
                        requested_type,
                        state,
                    )
                    packet.add(GraphFact(
                        "magento-generated-factory-resolution",
                        consumer.qualified_name,
                        "creates-via-generated-factory",
                        canonical_resolved,
                        consumer.path,
                        consumer.line,
                        attrs(
                            area=area,
                            factoryType=factory_type,
                            requestedType=requested_type,
                        ),
                    ),
                        target_symbol.path,
                        resolved_symbol.path if resolved_symbol else "",
                        *resolution_paths,
                    )

    def generated_proxy_packets(
        self,
        modules: tuple[ModuleRecord, ...],
        states: dict[str, DiState],
    ) -> None:
        """Resolve exact, absent ``<type>\\Proxy`` DI object arguments.

        Magento proxies are configured object values rather than constructor
        declarations. The generated class lazily delegates to the suffix-free
        original type, whose effective runtime implementation still follows the
        area's DI preference. Preserve the configured proxy edge and abstain
        whenever source identity or deployment state is ambiguous.
        """
        global_state = states.get("global", DiState())
        global_objects = self._effective_di_arguments.get("global", {})

        def proxy_target(configured: ConfigValue):
            proxy_type = configured.value.lstrip("\\")
            suffix = "\\Proxy"
            if (
                not proxy_type.endswith(suffix)
                or len(proxy_type) <= len(suffix)
                or self.index.symbols_by_casefold.get(proxy_type.casefold())
            ):
                return None
            requested_type = proxy_type[:-len(suffix)]
            candidates = self.index.symbols_by_casefold.get(
                requested_type.casefold(),
                (),
            )
            if len(candidates) != 1:
                return None
            target_symbol = candidates[0]
            if target_symbol.kind not in {"class", "interface"}:
                return None
            target_module = self.index.module_for_path(
                target_symbol.path,
                modules,
            )
            if target_module is None or not target_module.enabled:
                return None
            return (
                proxy_type,
                target_symbol.qualified_name,
                target_symbol,
                target_module,
            )

        for area, state in states.items():
            area_objects = self._effective_di_arguments.get(area, {})
            for owner, objects in sorted(area_objects.items()):
                global_owner_objects = global_objects.get(owner, {})
                for (
                    (argument_name, item_name),
                    (argument, declared_for),
                ) in sorted(objects.items()):
                    target = proxy_target(argument)
                    if target is None:
                        continue
                    (
                        proxy_type,
                        requested_type,
                        target_symbol,
                        target_module,
                    ) = target

                    global_argument_entry = global_owner_objects.get(
                        (argument_name, item_name)
                    )
                    global_target = (
                        proxy_target(global_argument_entry[0])
                        if global_argument_entry is not None
                        else None
                    )
                    dependency_changed = (
                        area == "global"
                        or global_argument_entry is None
                        or global_target is None
                        or (
                            proxy_type,
                            requested_type,
                            declared_for,
                        ) != (
                            global_target[0],
                            global_target[1],
                            global_argument_entry[1],
                        )
                    )

                    resolved = self.resolve_type(requested_type, state)
                    global_resolved = self.resolve_type(
                        requested_type,
                        global_state,
                    )
                    resolution_changed = (
                        area == "global"
                        or dependency_changed
                        or resolved != global_resolved
                    )
                    if not dependency_changed and not resolution_changed:
                        continue

                    packet = self.graph.packet(
                        "magento-generated-proxy",
                        (
                            f"{area}:{owner}:{argument_name}:"
                            f"{item_name}:{proxy_type}"
                        ),
                        area=area,
                        proxyType=proxy_type,
                    )
                    owner_path = self.index.symbol_path(owner)
                    inheritance_paths = self.argument_inheritance_paths(
                        owner,
                        state,
                    )
                    if dependency_changed:
                        packet.add(GraphFact(
                            "magento-generated-proxy",
                            owner,
                            "injects-generated-proxy-for",
                            requested_type,
                            argument.path,
                            argument.line,
                            attrs(
                                area=area,
                                argument=argument_name,
                                configured=argument.value,
                                declaredFor=declared_for,
                                generated="true",
                                inherited=(
                                    "true"
                                    if declared_for != owner
                                    else ""
                                ),
                                item=item_name,
                                module=argument.module,
                                proxyType=proxy_type,
                                requestedKind=target_symbol.kind,
                                targetModule=target_module.name,
                                virtualType=(
                                    "true"
                                    if owner in state.virtual_types
                                    else ""
                                ),
                            ),
                        ),
                            owner_path,
                            target_symbol.path,
                            *inheritance_paths,
                        )

                    if not resolution_changed:
                        continue
                    if (
                        target_symbol.kind == "interface"
                        and resolved == requested_type
                    ):
                        continue
                    resolved_symbol = self.index.unique_symbol_casefold(resolved)
                    canonical_resolved = (
                        resolved_symbol.qualified_name
                        if resolved_symbol is not None
                        else resolved.lstrip("\\")
                    )
                    packet.add(GraphFact(
                        "magento-generated-proxy-resolution",
                        owner,
                        "lazy-loads-via-generated-proxy",
                        canonical_resolved,
                        argument.path,
                        argument.line,
                        attrs(
                            area=area,
                            argument=argument_name,
                            item=item_name,
                            proxyType=proxy_type,
                            requestedType=requested_type,
                        ),
                    ),
                        owner_path,
                        target_symbol.path,
                        resolved_symbol.path if resolved_symbol else "",
                        *inheritance_paths,
                        *self.resolution_paths(requested_type, state),
                    )

    def descendants(self) -> dict[str, set[str]]:
        direct: dict[str, set[str]] = {}
        for symbol in self.index.symbols:
            for parent in symbol.parents:
                direct.setdefault(parent, set()).add(symbol.qualified_name)
        result: dict[str, set[str]] = {}
        for parent in direct:
            queue = list(direct[parent])
            descendants: set[str] = set()
            while queue:
                child = queue.pop()
                if child in descendants:
                    continue
                descendants.add(child)
                queue.extend(direct.get(child, ()))
            result[parent] = descendants
        return result

    def interception_applicability(
        self,
        target: str,
        method: str,
        state: DiState,
        plugin_symbol: SymbolDefinition,
        plugin_method: str,
    ) -> tuple[bool | None, str, str]:
        """Prove method interception only when PHP/Magento constraints are known."""
        target = target.lstrip("\\")
        if target in state.virtual_types:
            return False, "virtual-type", self.index.symbol_path(
                state.virtual_types[target].value
            )
        if method.casefold() in {"__construct", "__destruct"}:
            return False, "lifecycle-method", self.index.symbol_path(target)

        plugin_declaration = self.index.method_attributes(plugin_symbol, plugin_method)
        if plugin_declaration is not None:
            _, plugin_attributes = plugin_declaration
            if plugin_attributes.get("visibility", "public") != "public":
                return False, "plugin-method-not-public", plugin_symbol.path
            if plugin_attributes.get("static", "false") == "true":
                return False, "plugin-method-static", plugin_symbol.path

        target_symbol = self.index.symbol(target)
        if target_symbol is None:
            return None, "target-symbol-unavailable", ""
        if dict(target_symbol.attributes).get("type:final") == "true":
            return False, "final-class", target_symbol.path

        queue = deque([target_symbol])
        seen: set[str] = set()
        while queue:
            symbol = queue.popleft()
            if symbol.qualified_name in seen:
                continue
            seen.add(symbol.qualified_name)
            declaration = self.index.method_attributes(symbol, method)
            if declaration is not None:
                _, method_attributes = declaration
                if method_attributes.get("visibility", "public") != "public":
                    return False, "method-not-public", symbol.path
                if method_attributes.get("static", "false") == "true":
                    return False, "method-static", symbol.path
                if method_attributes.get("final", "false") == "true":
                    return False, "method-final", symbol.path
                return True, "", symbol.path
            parents = [
                self.index.symbol(parent)
                for parent in symbol.parents
            ]
            queue.extend(sorted(
                (parent for parent in parents if parent is not None),
                key=lambda parent: (
                    0 if parent.kind == "class" else 1,
                    parent.qualified_name,
                ),
            ))

        # Trait methods and unavailable external parents are not represented in
        # the current neutral symbol contract. Absence is therefore unknown,
        # never proof that the configured method cannot be intercepted.
        return None, "method-declaration-unavailable", target_symbol.path
