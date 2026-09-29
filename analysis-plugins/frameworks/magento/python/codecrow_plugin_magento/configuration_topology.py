from __future__ import annotations

import json

from codecrow_plugins import GraphFact

from .architecture import ModuleRecord, PacketGraph, attrs, line, tag
from .dependency_injection import DependencyInjectionTopology
from .resolution_index import RepositorySourceIndex
from .resolution_models import ConfigurationSources, DiState


class ConfigurationTopology:
    """Build Magento configuration relationships from repository evidence."""

    def __init__(
        self,
        index: RepositorySourceIndex,
        graph: PacketGraph,
        configuration_sources: ConfigurationSources,
        di: DependencyInjectionTopology,
    ) -> None:
        self.index = index
        self.graph = graph
        self.configuration_sources = configuration_sources
        self.di = di

    def system_configuration(
        self,
        modules: tuple[ModuleRecord, ...],
    ) -> None:
        """Connect Admin configuration fields to their exact runtime inputs.

        Magento merges ``etc/adminhtml/system.xml`` declarations into the
        Stores > Configuration tree. A field can override its normal
        ``section/group/field`` storage path and can delegate rendering,
        option generation, and persistence to PHP models. The corresponding
        default is declared separately in ``etc/config.xml``. These are
        architecture relations, not defect rules: unresolved/external class
        names remain explicit targets and no validity claim is inferred.
        """
        acl_paths: dict[str, set[str]] = {}
        for path, _, _ in self.index.ordered_configs(
            "acl.xml",
            modules,
            "global",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            for resource in (
                node
                for node in root.iter()
                if tag(node) == "resource" and node.get("id")
            ):
                acl_paths.setdefault(resource.get("id"), set()).add(path)
        self.configuration_sources.acl = acl_paths

        default_paths: dict[
            str,
            list[tuple[str, str, int, str, int]],
        ] = {}

        def collect_defaults(
            node,
            *,
            path: str,
            module_name: str,
            order: int,
            scope: str,
            prefix: tuple[str, ...] = (),
        ) -> None:
            children = tuple(node)
            current = (*prefix, tag(node))
            if not children:
                value = (node.text or "").strip()
                if value:
                    config_path = "/".join(current)
                    default_paths.setdefault(config_path, []).append((
                        path,
                        scope,
                        line(self.index.artifacts[path], value),
                        module_name,
                        order,
                    ))
                return
            for child in children:
                collect_defaults(
                    child,
                    path=path,
                    module_name=module_name,
                    order=order,
                    scope=scope,
                    prefix=current,
                )

        for path, module, order in self.index.ordered_configs(
            "config.xml",
            modules,
            "global",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            module_name = module.name if module else "application"
            for scope_node in root:
                scope_kind = tag(scope_node)
                if scope_kind == "default":
                    for child in scope_node:
                        collect_defaults(
                            child,
                            path=path,
                            module_name=module_name,
                            order=order,
                            scope="default",
                        )
                    continue
                if scope_kind not in {"websites", "stores"}:
                    continue
                for scope_code in scope_node:
                    for child in scope_code:
                        collect_defaults(
                            child,
                            path=path,
                            module_name=module_name,
                            order=order,
                            scope=f"{scope_kind}:{tag(scope_code)}",
                        )

        def text_child(node, name: str) -> str:
            child = next(
                (
                    candidate
                    for candidate in node
                    if tag(candidate) == name and candidate.text
                ),
                None,
            )
            return child.text.strip() if child is not None else ""

        def model_relations(
            packet,
            node,
            *,
            config_path: str,
            source_path: str,
            content: str,
            element_kind: str,
        ) -> None:
            for child_name, relation in (
                ("frontend_model", "uses-frontend-model"),
                ("backend_model", "uses-backend-model"),
                ("source_model", "uses-source-model"),
            ):
                class_name = text_child(node, child_name).lstrip("\\")
                if not class_name:
                    continue
                packet.add(GraphFact(
                    "magento-system-config-model",
                    config_path,
                    relation,
                    class_name,
                    source_path,
                    line(content, class_name),
                    attrs(element=element_kind),
                ), self.index.symbol_path(class_name))

        def extension_relation(
            packet,
            node,
            *,
            identity: str,
            source_path: str,
            content: str,
            element_kind: str,
        ) -> None:
            target = node.get("extends", "").strip()
            if not target:
                return
            packet.add(GraphFact(
                "magento-system-config-extension",
                identity,
                "extends-config-node",
                target,
                source_path,
                line(content, target),
                attrs(element=element_kind),
            ))

        for path, module, order in self.index.ordered_configs(
            "system.xml",
            modules,
            "adminhtml",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            content = self.index.artifacts[path]
            module_name = module.name if module else "application"
            system_nodes = (
                (root,)
                if tag(root) == "system"
                else tuple(
                    node for node in root if tag(node) == "system"
                )
            )
            for system in system_nodes:
                for section in (
                    node
                    for node in system
                    if tag(node) == "section" and node.get("id")
                ):
                    section_id = section.get("id")
                    section_packet = self.graph.packet(
                        "magento-system-config",
                        f"section:{section_id}",
                        module=module_name,
                    )
                    section_packet.add(GraphFact(
                        "magento-system-config-section",
                        section_id,
                        "declared-in-admin-configuration",
                        path,
                        path,
                        line(content, section_id),
                        attrs(module=module_name, order=order),
                    ))
                    extension_relation(
                        section_packet,
                        section,
                        identity=section_id,
                        source_path=path,
                        content=content,
                        element_kind="section",
                    )
                    model_relations(
                        section_packet,
                        section,
                        config_path=section_id,
                        source_path=path,
                        content=content,
                        element_kind="section",
                    )
                    resource = text_child(section, "resource")
                    if resource:
                        section_packet.add(GraphFact(
                            "magento-system-config-acl",
                            section_id,
                            "requires-resource",
                            resource,
                            path,
                            line(content, resource),
                        ), *sorted(acl_paths.get(resource, ())))

                    pending_groups = [
                        (group, (section_id,))
                        for group in section
                        if tag(group) == "group" and group.get("id")
                    ]
                    while pending_groups:
                        group, parent_parts = pending_groups.pop(0)
                        group_parts = (*parent_parts, group.get("id"))
                        group_path = "/".join(group_parts)
                        group_packet = self.graph.packet(
                            "magento-system-config",
                            f"group:{group_path}",
                            module=module_name,
                        )
                        group_packet.add(GraphFact(
                            "magento-system-config-group",
                            group_path,
                            "declared-in-admin-configuration",
                            path,
                            path,
                            line(content, group.get("id")),
                            attrs(module=module_name, order=order),
                        ))
                        extension_relation(
                            group_packet,
                            group,
                            identity=group_path,
                            source_path=path,
                            content=content,
                            element_kind="group",
                        )
                        model_relations(
                            group_packet,
                            group,
                            config_path=group_path,
                            source_path=path,
                            content=content,
                            element_kind="group",
                        )

                        pending_groups.extend(
                            (child, group_parts)
                            for child in group
                            if tag(child) == "group" and child.get("id")
                        )
                        for field_node in (
                            child
                            for child in group
                            if tag(child) == "field" and child.get("id")
                        ):
                            declared_path = "/".join(
                                (*group_parts, field_node.get("id"))
                            )
                            effective_path = (
                                text_child(field_node, "config_path")
                                or declared_path
                            ).strip("/") or declared_path
                            self.configuration_sources.system.setdefault(
                                effective_path,
                                set(),
                            ).add(path)
                            field_packet = self.graph.packet(
                                "magento-system-config",
                                f"field:{declared_path}",
                                module=module_name,
                                configPath=effective_path,
                            )
                            field_packet.add(GraphFact(
                                "magento-system-config-field",
                                effective_path,
                                "declared-by-admin-field",
                                declared_path,
                                path,
                                line(content, field_node.get("id")),
                                attrs(
                                    module=module_name,
                                    order=order,
                                    fieldType=field_node.get("type", ""),
                                ),
                            ))
                            extension_relation(
                                field_packet,
                                field_node,
                                identity=declared_path,
                                source_path=path,
                                content=content,
                                element_kind="field",
                            )
                            model_relations(
                                field_packet,
                                field_node,
                                config_path=effective_path,
                                source_path=path,
                                content=content,
                                element_kind="field",
                            )
                            depends = next(
                                (
                                    child
                                    for child in field_node
                                    if tag(child) == "depends"
                                ),
                                None,
                            )
                            if depends is not None:
                                for dependency in (
                                    child
                                    for child in depends
                                    if tag(child) == "field"
                                    and child.get("id")
                                ):
                                    expected_value = (
                                        dependency.text or ""
                                    ).strip()
                                    field_packet.add(GraphFact(
                                        "magento-system-config-dependency",
                                        effective_path,
                                        "depends-on-config-field",
                                        dependency.get("id"),
                                        path,
                                        line(
                                            content,
                                            dependency.get("id"),
                                        ),
                                        attrs(
                                            expectedValue=expected_value,
                                            separator=dependency.get(
                                                "separator",
                                                "",
                                            ),
                                        ),
                                    ))
                            for (
                                default_path,
                                scope,
                                default_line,
                                default_module,
                                default_order,
                            ) in default_paths.get(effective_path, ()):
                                self.configuration_sources.system[
                                    effective_path
                                ].add(default_path)
                                field_packet.add(GraphFact(
                                    "magento-system-config-default",
                                    effective_path,
                                    "has-default-declaration",
                                    scope,
                                    default_path,
                                    default_line,
                                    attrs(
                                        module=default_module,
                                        order=default_order,
                                    ),
                                ), path)

        scope_config_types = {
            r"Magento\Framework\App\Config",
            r"Magento\Framework\App\Config\ScopeConfigInterface",
        }
        for symbol in sorted(self.index.symbols):
            for key, value in symbol.attributes:
                if not key.startswith(
                    "php-literal-instance-call-reference:"
                ):
                    continue
                try:
                    reference = json.loads(value)
                except (TypeError, json.JSONDecodeError) as exception:
                    raise ValueError(
                        "PHP literal-call metadata is invalid JSON"
                    ) from exception
                if not isinstance(reference, dict):
                    raise ValueError(
                        "PHP literal-call metadata must be an object"
                    )
                receiver_type = reference.get("target")
                method = reference.get("method")
                caller = reference.get("caller", "")
                call_line = reference.get("line")
                literal_arguments = reference.get(
                    "literalStringArguments"
                )
                receiver_resolution = reference.get(
                    "receiverResolution",
                    "",
                )
                if (
                    not isinstance(receiver_type, str)
                    or not isinstance(method, str)
                    or not isinstance(caller, str)
                    or not isinstance(call_line, int)
                    or call_line < 1
                    or not isinstance(literal_arguments, dict)
                    or not isinstance(receiver_resolution, str)
                    or any(
                        not isinstance(position, str)
                        or not isinstance(argument, str)
                        for position, argument
                        in literal_arguments.items()
                    )
                ):
                    raise ValueError(
                        "PHP literal-call metadata has invalid fields"
                    )
                if receiver_type.lstrip("\\") not in scope_config_types:
                    continue
                normalized_method = method.casefold()
                relation = {
                    "getvalue": "reads-config-value",
                    "issetflag": "checks-config-flag",
                }.get(normalized_method)
                if relation is None:
                    continue
                config_path = literal_arguments.get("0", "").strip("/")
                related_sources = self.configuration_sources.system.get(
                    config_path
                )
                if not config_path or not related_sources:
                    continue
                packet = self.graph.packet(
                    "magento-system-config",
                    (
                        f"consumer:{symbol.qualified_name}:"
                        f"{caller or '<class>'}:{method}:{config_path}"
                    ),
                    configPath=config_path,
                )
                packet.add(GraphFact(
                    "magento-system-config-consumer",
                    (
                        f"{symbol.qualified_name}::{caller}"
                        if caller
                        else symbol.qualified_name
                    ),
                    relation,
                    config_path,
                    symbol.path,
                    call_line,
                    attrs(
                        method=method,
                        receiverResolution=receiver_resolution,
                        receiverType=receiver_type.lstrip("\\"),
                    ),
                ), *sorted(related_sources))

    def admin_menu(
        self,
        modules: tuple[ModuleRecord, ...],
    ) -> None:
        """Resolve Magento Admin menu commands into effective topology.

        Magento chains ``add``, ``update``, and ``remove`` commands by item ID
        in module merge order. ``update`` replaces named attributes, ``add``
        fills only attributes not already supplied by an earlier command, and
        ``remove`` suppresses the item. Duplicate ``add`` commands and missing
        parents are exact configuration failures, so they are retained as
        diagnostics instead of being guessed into a navigable menu.
        """
        commands: dict[str, list[dict[str, object]]] = {}
        for path, module, order in self.index.ordered_configs(
            "menu.xml",
            modules,
            "adminhtml",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            content = self.index.artifacts[path]
            for menu in (
                node for node in root.iter() if tag(node) == "menu"
            ):
                for position, node in enumerate(menu):
                    operation = tag(node)
                    item_id = (node.get("id") or "").strip()
                    if operation not in {"add", "update", "remove"} or not item_id:
                        continue
                    values = {
                        key.rsplit("}", 1)[-1]: value.strip()
                        for key, value in node.attrib.items()
                        if value is not None and value.strip()
                    }
                    commands.setdefault(item_id, []).append({
                        "operation": operation,
                        "values": values,
                        "path": path,
                        "line": line(content, item_id),
                        "module": (
                            module.name if module else "application"
                        ),
                        "order": order,
                        "position": position,
                    })

        states: dict[str, dict[str, object]] = {}
        required = {"id", "title", "module", "resource"}
        for item_id, item_commands in sorted(commands.items()):
            item_commands.sort(key=lambda command: (
                int(command["order"]),
                int(command["position"]),
                str(command["path"]),
            ))
            values: dict[str, str] = {}
            value_sources: dict[str, tuple[str, int]] = {}
            all_paths: set[str] = set()
            add_count = 0
            removed = False
            removal_source: tuple[str, int] | None = None
            for command in item_commands:
                operation = str(command["operation"])
                path = str(command["path"])
                command_line = int(command["line"])
                all_paths.add(path)
                command_values = dict(command["values"])
                if operation == "add":
                    add_count += 1
                    for key, value in command_values.items():
                        if key not in values:
                            values[key] = str(value)
                            value_sources[key] = (path, command_line)
                elif operation == "update":
                    for key, value in command_values.items():
                        values[key] = str(value)
                        value_sources[key] = (path, command_line)
                else:
                    removed = True
                    removal_source = (path, command_line)
            states[item_id] = {
                "add_count": add_count,
                "all_paths": all_paths,
                "removed": removed,
                "removal_source": removal_source,
                "required_missing": required.difference(values),
                "values": values,
                "value_sources": value_sources,
            }

        visibility: dict[str, tuple[bool, str]] = {}

        def visible(
            item_id: str,
            stack: tuple[str, ...] = (),
        ) -> tuple[bool, str]:
            if item_id in visibility:
                return visibility[item_id]
            state = states.get(item_id)
            if state is None:
                return False, "missing"
            if item_id in stack:
                result = (False, "parent-cycle")
            elif int(state["add_count"]) != 1:
                result = (
                    False,
                    (
                        "duplicate-add"
                        if int(state["add_count"]) > 1
                        else "missing-add"
                    ),
                )
            elif state["required_missing"]:
                result = (False, "missing-required-attributes")
            elif bool(state["removed"]):
                result = (False, "removed")
            else:
                parent = str(dict(state["values"]).get("parent", ""))
                if not parent:
                    result = (True, "")
                elif parent not in states:
                    result = (False, "missing-parent")
                else:
                    parent_visible, parent_reason = visible(
                        parent,
                        (*stack, item_id),
                    )
                    result = (
                        (True, "")
                        if parent_visible
                        else (False, f"parent-{parent_reason}")
                    )
            visibility[item_id] = result
            return result

        modules_by_name = {module.name: module for module in modules}
        for item_id, state in sorted(states.items()):
            values = dict(state["values"])
            value_sources = dict(state["value_sources"])
            all_paths = set(state["all_paths"])
            is_visible, reason = visible(item_id)
            packet = self.graph.packet(
                "magento-admin-menu",
                item_id,
                itemId=item_id,
            )

            if not is_visible:
                source = (
                    state["removal_source"]
                    or value_sources.get("id")
                    or (sorted(all_paths)[0], 1)
                )
                kind = (
                    "magento-admin-menu-removed"
                    if reason == "removed"
                    else (
                        "magento-admin-menu-suppressed"
                        if reason.startswith("parent-")
                        and reason not in {
                            "parent-missing",
                            "parent-parent-cycle",
                        }
                        else "magento-admin-menu-invalid"
                    )
                )
                packet.add(GraphFact(
                    kind,
                    item_id,
                    (
                        "removed-by-config"
                        if reason == "removed"
                        else "not-in-effective-menu"
                    ),
                    reason,
                    str(source[0]),
                    int(source[1]),
                    attrs(
                        reason=reason,
                        semanticRole=(
                            "topology"
                            if kind == "magento-admin-menu-suppressed"
                            else "diagnostic"
                        ),
                    ),
                ), *sorted(all_paths))
                continue

            action = values.get("action", "").strip("/").casefold()
            resource = values.get("resource", "")
            config_path = values.get("dependsOnConfig", "")
            dependency_module = values.get("dependsOnModule", "")
            parent = values.get("parent", "")
            controller_paths = self.configuration_sources.controllers.get(
                action,
                set(),
            )
            resource_paths = self.configuration_sources.acl.get(resource, set())
            config_paths = self.configuration_sources.system.get(
                config_path,
                set(),
            )
            dependency = modules_by_name.get(dependency_module)
            parent_paths = (
                set(states[parent]["all_paths"])
                if parent in states
                else set()
            )
            related = set(all_paths)
            related.update(controller_paths)
            related.update(resource_paths)
            related.update(config_paths)
            related.update(parent_paths)
            if dependency is not None:
                related.add(dependency.module_xml)

            primary_key = "action" if action else "id"
            primary_source = value_sources.get(
                primary_key,
                value_sources.get("id", (sorted(all_paths)[0], 1)),
            )
            packet.add(GraphFact(
                "magento-admin-menu-item",
                item_id,
                "navigates-to" if action else "declares-container",
                action or parent or item_id,
                primary_source[0],
                primary_source[1],
                attrs(
                    module=values.get("module", ""),
                    parent=parent,
                    sortOrder=values.get("sortOrder", ""),
                    target=values.get("target", ""),
                    title=values.get("title", ""),
                ),
            ), *sorted(related))

            relation_specs = (
                (
                    "magento-admin-menu-parent",
                    "child-of-menu-item",
                    parent,
                    parent_paths,
                    "parent",
                ),
                (
                    "magento-admin-menu-acl",
                    "requires-resource",
                    resource,
                    resource_paths,
                    "resource",
                ),
                (
                    "magento-admin-menu-config-condition",
                    "visible-when-config-enabled",
                    config_path,
                    config_paths,
                    "dependsOnConfig",
                ),
                (
                    "magento-admin-menu-module-condition",
                    "visible-when-module-enabled",
                    dependency_module,
                    (
                        {dependency.module_xml}
                        if dependency is not None
                        else set()
                    ),
                    "dependsOnModule",
                ),
                (
                    "magento-admin-menu-action",
                    "dispatches-admin-action",
                    action,
                    controller_paths,
                    "action",
                ),
            )
            for (
                kind,
                relation,
                target,
                target_paths,
                attribute_name,
            ) in relation_specs:
                if not target:
                    continue
                source = value_sources.get(
                    attribute_name,
                    primary_source,
                )
                packet.add(GraphFact(
                    kind,
                    item_id,
                    relation,
                    target,
                    source[0],
                    source[1],
                    attrs(
                        exactTarget=bool(target_paths),
                        moduleEnabled=(
                            dependency.enabled
                            if (
                                attribute_name == "dependsOnModule"
                                and dependency is not None
                            )
                            else None
                        ),
                    ),
                ), *sorted(target_paths))

    def webapi_and_acl(self, modules: tuple[ModuleRecord, ...], states: dict[str, DiState]) -> None:
        acl_paths: dict[str, set[str]] = {}
        for path, module, order in self.index.ordered_configs("acl.xml", modules, "global"):
            root = self.index.xml(path)
            if root is None:
                continue
            content = self.index.artifacts[path]
            for resource in (node for node in root.iter() if tag(node) == "resource" and node.get("id")):
                acl_paths.setdefault(resource.get("id"), set()).add(path)
                parent = next(
                    (
                        candidate.get("id")
                        for candidate in root.iter()
                        if resource in list(candidate) and candidate.get("id")
                    ),
                    "",
                )
                packet = self.graph.packet("magento-acl", resource.get("id"))
                packet.add(GraphFact(
                    "magento-acl-resource",
                    resource.get("id"),
                    "child-of" if parent else "declared-in",
                    parent or path,
                    path,
                    line(content, resource.get("id")),
                    attrs(module=module.name if module else "application", title=resource.get("title", "")),
                ))

        rest_state = states.get("webapi_rest", states.get("global", DiState()))
        for path, module, order in self.index.ordered_configs("webapi.xml", modules, "global"):
            root = self.index.xml(path)
            if root is None:
                continue
            content = self.index.artifacts[path]
            for route in (node for node in root.iter() if tag(node) == "route" and node.get("url")):
                service = next((node for node in route if tag(node) == "service"), None)
                if service is None or not service.get("class") or not service.get("method"):
                    continue
                contract = service.get("class").lstrip("\\")
                implementation = self.di.resolve_type(contract, rest_state)
                key = f"{route.get('method', '')}:{route.get('url')}"
                packet = self.graph.packet("magento-webapi", key, method=route.get("method", ""), url=route.get("url"))
                packet.add(GraphFact(
                    "magento-webapi-route",
                    f"{route.get('method', '')} {route.get('url')}",
                    "invokes",
                    f"{contract}::{service.get('method')}",
                    path,
                    line(content, route.get("url")),
                    attrs(
                        implementation=implementation,
                        module=module.name if module else "application",
                        secure=route.get("secure", ""),
                    ),
                ), self.index.symbol_path(contract), self.index.symbol_path(implementation))
                for resource in (node for node in route.iter() if tag(node) == "resource" and node.get("ref")):
                    packet.add(GraphFact(
                        "magento-webapi-acl",
                        key,
                        "requires-resource",
                        resource.get("ref"),
                        path,
                        line(content, resource.get("ref")),
                    ), *acl_paths.get(resource.get("ref"), set()))

    def cron(self, modules: tuple[ModuleRecord, ...]) -> None:
        for path, module, order in self.index.ordered_configs("crontab.xml", modules, "global"):
            root = self.index.xml(path)
            if root is None:
                continue
            content = self.index.artifacts[path]
            for group in (node for node in root.iter() if tag(node) == "group" and node.get("id")):
                for job in (node for node in group if tag(node) == "job" and node.get("name")):
                    target = job.get("instance", "")
                    method = job.get("method", "execute")
                    schedule = next((node.text.strip() for node in job if tag(node) == "schedule" and node.text), "")
                    config_path = next((node.text.strip() for node in job if tag(node) == "config_path" and node.text), "")
                    packet = self.graph.packet("magento-cron", f"{group.get('id')}:{job.get('name')}")
                    packet.add(GraphFact(
                        "magento-cron-job",
                        job.get("name"),
                        "invokes",
                        f"{target}::{method}",
                        path,
                        line(content, job.get("name")),
                        attrs(group=group.get("id"), schedule=schedule, configPath=config_path),
                    ), self.index.symbol_path(target))
