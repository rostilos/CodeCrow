from __future__ import annotations

import json
from pathlib import PurePosixPath

from codecrow_plugins import GraphFact, PluginDiagnostic
from codecrow_plugins.graphql import (
    parse_operations,
    parse_schema,
    parse_schema_root_types,
)

from .architecture import (
    MAGENTO_GRAPHQL_CLIENT_SUFFIXES,
    ModuleRecord,
    PacketGraph,
    attrs,
    config_area,
    is_magento_config_xml,
    line,
    tag,
)
from .resolution_index import RepositorySourceIndex
from .resolution_models import _enabled


class DataTopology:
    """Build Magento data relationships from repository evidence."""

    def __init__(self, index: RepositorySourceIndex, graph: PacketGraph) -> None:
        self.index = index
        self.graph = graph

    def indexers_and_materialized_views(
        self,
        modules: tuple[ModuleRecord, ...],
    ) -> None:
        indexers: dict[str, dict[str, object]] = {}
        for path, module, order in self.index.ordered_configs("indexer.xml", modules, "global"):
            root = self.index.xml(path)
            if root is None:
                continue
            content = self.index.artifacts[path]
            for node in (
                item for item in root
                if tag(item) == "indexer" and item.get("id")
            ):
                identifier = node.get("id")
                prior = indexers.get(identifier, {"attributes": {}, "dependencies": set()})
                merged_attributes = dict(prior["attributes"])
                merged_attributes.update(node.attrib)
                dependencies = set(prior["dependencies"])
                dependencies.update(
                    child.get("id")
                    for container in node
                    if tag(container) == "dependencies"
                    for child in container
                    if tag(child) == "indexer" and child.get("id")
                )
                indexers[identifier] = {
                    "attributes": merged_attributes,
                    "dependencies": dependencies,
                    "path": path,
                    "line": line(content, identifier),
                    "module": module.name if module else "application",
                    "order": order,
                }

        views: dict[str, dict[str, object]] = {}
        for path, module, order in self.index.ordered_configs("mview.xml", modules, "global"):
            root = self.index.xml(path)
            if root is None:
                continue
            content = self.index.artifacts[path]
            for node in (
                item for item in root
                if tag(item) == "view" and item.get("id")
            ):
                identifier = node.get("id")
                prior = views.get(identifier, {"attributes": {}, "tables": {}})
                merged_attributes = dict(prior["attributes"])
                merged_attributes.update(node.attrib)
                tables = dict(prior["tables"])
                for table in (
                    child for container in node
                    if tag(container) == "subscriptions"
                    for child in container
                    if tag(child) == "table" and child.get("name")
                ):
                    table_key = (table.get("name"), table.get("entity_column", ""))
                    table_attributes = dict(tables.get(table_key, {}))
                    table_attributes.update(table.attrib)
                    tables[table_key] = table_attributes
                views[identifier] = {
                    "attributes": merged_attributes,
                    "tables": tables,
                    "path": path,
                    "line": line(content, identifier),
                    "module": module.name if module else "application",
                    "order": order,
                }

        schema_paths: dict[str, set[str]] = {}
        for path, _, _ in self.index.ordered_configs("db_schema.xml", modules, "global"):
            root = self.index.xml(path)
            if root is None:
                continue
            for table in (
                node for node in root
                if tag(node) == "table" and node.get("name")
            ):
                schema_paths.setdefault(table.get("name"), set()).add(path)

        for identifier, record in sorted(indexers.items()):
            attributes = record["attributes"]
            packet = self.graph.packet(
                "magento-indexer",
                identifier,
                module=str(record["module"]),
            )
            implementation = str(attributes.get("class", ""))
            if implementation:
                packet.add(GraphFact(
                    "magento-indexer-class", identifier, "executes", implementation,
                    str(record["path"]), int(record["line"]),
                    attrs(module=record["module"], order=record["order"]),
                ), self.index.symbol_path(implementation))
            view_id = str(attributes.get("view_id", ""))
            if view_id:
                packet.add(GraphFact(
                    "magento-indexer-view", identifier, "materializes-through", view_id,
                    str(record["path"]), int(record["line"]),
                ), str(views.get(view_id, {}).get("path", "")))
            shared_index = str(attributes.get("shared_index", ""))
            if shared_index:
                packet.add(GraphFact(
                    "magento-indexer-shared-index", identifier, "shares-index", shared_index,
                    str(record["path"]), int(record["line"]),
                ))
            for dependency in sorted(record["dependencies"]):
                packet.add(GraphFact(
                    "magento-indexer-dependency", identifier, "depends-on-indexer", dependency,
                    str(record["path"]), int(record["line"]),
                ), str(indexers.get(dependency, {}).get("path", "")))

        for identifier, record in sorted(views.items()):
            attributes = record["attributes"]
            packet = self.graph.packet(
                "magento-materialized-view",
                identifier,
                module=str(record["module"]),
                group=str(attributes.get("group", "")),
            )
            implementation = str(attributes.get("class", ""))
            if implementation:
                packet.add(GraphFact(
                    "magento-mview-class", identifier, "executes", implementation,
                    str(record["path"]), int(record["line"]),
                ), self.index.symbol_path(implementation))
            walker = str(attributes.get(
                "walker", "Magento\\Framework\\Mview\\View\\ChangeLogBatchWalker"
            ))
            packet.add(GraphFact(
                "magento-mview-walker", identifier, "uses-walker", walker,
                str(record["path"]), int(record["line"]),
            ), self.index.symbol_path(walker))
            for (table_name, entity_column), table_attributes in sorted(record["tables"].items()):
                packet.add(GraphFact(
                    "magento-mview-subscription",
                    identifier,
                    "subscribes-to-table",
                    table_name,
                    str(record["path"]),
                    int(record["line"]),
                    attrs(
                        entityColumn=entity_column,
                        processor=table_attributes.get("processor", ""),
                        subscriptionModel=table_attributes.get("subscription_model", ""),
                    ),
                ), *sorted(schema_paths.get(table_name, ())))

    def schema(self, modules: tuple[ModuleRecord, ...]) -> None:
        whitelist: dict[str, object] = {}

        def merge_mapping(
            destination: dict[str, object],
            source: dict[str, object],
        ) -> None:
            for key, value in source.items():
                current = destination.get(key)
                if isinstance(current, dict) and isinstance(value, dict):
                    merge_mapping(current, value)
                else:
                    destination[key] = value

        for path, content in sorted(self.index.artifacts.items()):
            if PurePosixPath(path).name != "db_schema_whitelist.json":
                continue
            module = self.index.module_for_path(path, modules)
            if module is None or not module.enabled:
                continue
            try:
                document = json.loads(content)
            except json.JSONDecodeError as exception:
                self.index.diagnostics.append(PluginDiagnostic(
                    "magento-invalid-schema-whitelist",
                    f"{path}: {exception}",
                    self.index.plugin_id,
                ))
                continue
            if not isinstance(document, dict):
                self.index.diagnostics.append(PluginDiagnostic(
                    "magento-invalid-schema-whitelist",
                    f"{path}: top-level value must be an object",
                    self.index.plugin_id,
                ))
                continue
            merge_mapping(whitelist, document)

        tables: dict[str, dict[str, object]] = {}
        for path, module, order in self.index.ordered_configs(
            "db_schema.xml",
            modules,
            "global",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            content = self.index.artifacts[path]
            for table in (
                node for node in root
                if tag(node) == "table" and node.get("name")
            ):
                table_name = table.get("name")
                record = tables.setdefault(table_name, {
                    "attributes": {},
                    "path": path,
                    "line": line(content, table_name),
                    "module": module.name if module else "application",
                    "order": order,
                    "paths": set(),
                    "children": {
                        "column": {},
                        "index": {},
                        "constraint": {},
                    },
                })
                record["attributes"].update({
                    key.rsplit("}", 1)[-1]: value
                    for key, value in table.attrib.items()
                    if key != "name"
                })
                record.update({
                    "path": path,
                    "line": line(content, table_name),
                    "module": module.name if module else "application",
                    "order": order,
                })
                record["paths"].add(path)
                for child in table:
                    child_kind = tag(child)
                    if child_kind == "column":
                        identity = child.get("name", "")
                    elif child_kind in {"index", "constraint"}:
                        identity = child.get("referenceId", "")
                    else:
                        continue
                    if not identity:
                        continue
                    child_record = record["children"][child_kind].setdefault(
                        identity,
                        {
                            "attributes": {},
                            "path": path,
                            "line": line(content, identity),
                            "module": (
                                module.name if module else "application"
                            ),
                            "order": order,
                            "paths": set(),
                            "columns": {},
                        },
                    )
                    child_record["attributes"].update({
                        key.rsplit("}", 1)[-1]: value
                        for key, value in child.attrib.items()
                        if key not in {"name", "referenceId"}
                    })
                    child_record.update({
                        "path": path,
                        "line": line(content, identity),
                        "module": (
                            module.name if module else "application"
                        ),
                        "order": order,
                    })
                    child_record["paths"].add(path)
                    for column_ref in (
                        node for node in child
                        if tag(node) == "column" and node.get("name")
                    ):
                        child_record["columns"][column_ref.get("name")] = path

        def whitelist_contains(
            table_name: str,
            element_kind: str = "",
            identity: str = "",
        ) -> bool:
            table_whitelist = whitelist.get(table_name)
            if not isinstance(table_whitelist, dict):
                return False
            if not element_kind:
                return True
            group = table_whitelist.get(element_kind)
            return isinstance(group, dict) and identity in group

        disabled_tables = {
            table_name
            for table_name, record in tables.items()
            if not _enabled(record["attributes"].get("disabled"))
        }
        for table_name, record in sorted(tables.items()):
            packet = self.graph.packet("magento-database", table_name)
            table_attributes = record["attributes"]
            if table_name in disabled_tables:
                packet.add(GraphFact(
                    "magento-db-removal",
                    str(record["module"]),
                    "disables-table",
                    table_name,
                    str(record["path"]),
                    int(record["line"]),
                    attrs(
                        elementType="table",
                        whitelisted=str(
                            whitelist_contains(table_name)
                        ).casefold(),
                        destructiveOperationAllowed=str(
                            whitelist_contains(table_name)
                        ).casefold(),
                    ),
                ), *sorted(record["paths"]))
                continue

            packet.add(GraphFact(
                "magento-db-table",
                str(record["module"]),
                "declares-table",
                table_name,
                str(record["path"]),
                int(record["line"]),
                attrs(
                    resource=table_attributes.get("resource", ""),
                    engine=table_attributes.get("engine", ""),
                    order=record["order"],
                ),
            ), *sorted(record["paths"]))

            for child_kind in ("column", "index", "constraint"):
                children = record["children"][child_kind]
                for identity, child_record in sorted(children.items()):
                    child_attributes = child_record["attributes"]
                    if not _enabled(child_attributes.get("disabled")):
                        allowed = whitelist_contains(
                            table_name,
                            child_kind,
                            identity,
                        )
                        packet.add(GraphFact(
                            "magento-db-removal",
                            table_name,
                            f"disables-{child_kind}",
                            identity,
                            str(child_record["path"]),
                            int(child_record["line"]),
                            attrs(
                                elementType=child_kind,
                                whitelisted=str(allowed).casefold(),
                                destructiveOperationAllowed=str(
                                    allowed
                                ).casefold(),
                                whitelistIdentity="reference-id",
                            ),
                        ), *sorted(child_record["paths"]))
                        continue

                    if child_kind == "column":
                        packet.add(GraphFact(
                            "magento-db-column",
                            table_name,
                            "has-column",
                            identity,
                            str(child_record["path"]),
                            int(child_record["line"]),
                            attrs(
                                dataType=child_attributes.get("type", ""),
                                nullable=child_attributes.get(
                                    "nullable",
                                    "",
                                ),
                                identity=child_attributes.get(
                                    "identity",
                                    "",
                                ),
                                module=child_record["module"],
                            ),
                        ), *sorted(child_record["paths"]))
                        continue

                    if child_kind == "index":
                        packet.add(GraphFact(
                            "magento-db-index",
                            table_name,
                            "indexes-columns",
                            identity,
                            str(child_record["path"]),
                            int(child_record["line"]),
                            attrs(
                                indexType=child_attributes.get(
                                    "indexType",
                                    "",
                                ),
                                columns=",".join(
                                    sorted(child_record["columns"])
                                ),
                                module=child_record["module"],
                            ),
                        ), *sorted(child_record["paths"]))
                        continue

                    reference_table = child_attributes.get(
                        "referenceTable",
                        "",
                    )
                    if reference_table:
                        invalid = reference_table in disabled_tables
                        packet.add(GraphFact(
                            (
                                "magento-db-foreign-key-invalid"
                                if invalid
                                else "magento-db-foreign-key"
                            ),
                            table_name,
                            (
                                "references-disabled-table"
                                if invalid
                                else "references-table"
                            ),
                            reference_table,
                            str(child_record["path"]),
                            int(child_record["line"]),
                            attrs(
                                referenceId=identity,
                                column=child_attributes.get("column", ""),
                                referenceColumn=child_attributes.get(
                                    "referenceColumn",
                                    "",
                                ),
                                module=child_record["module"],
                                semanticRole=(
                                    "diagnostic" if invalid else None
                                ),
                            ),
                        ), *sorted(child_record["paths"]),
                            *sorted(tables.get(
                                reference_table,
                                {},
                            ).get("paths", ())))
                    else:
                        packet.add(GraphFact(
                            "magento-db-constraint",
                            table_name,
                            "constrains-columns",
                            identity,
                            str(child_record["path"]),
                            int(child_record["line"]),
                            attrs(
                                constraintType=child_attributes.get(
                                    "type",
                                    "",
                                ),
                                columns=",".join(
                                    sorted(child_record["columns"])
                                ),
                                module=child_record["module"],
                            ),
                        ), *sorted(child_record["paths"]))

    def graphql(self, modules: tuple[ModuleRecord, ...]) -> None:
        for path, content in sorted(self.index.artifacts.items()):
            if not path.endswith(".graphqls"):
                continue
            module = self.index.module_for_path(path, modules)
            if module is None or not module.enabled:
                continue
            for declaration in parse_schema(content):
                type_name = declaration.name
                packet = self.graph.packet("magento-graphql", type_name, module=module.name if module else "")
                packet.add(GraphFact(
                    "magento-graphql-type",
                    type_name,
                    "declared-in",
                    path,
                    path,
                    declaration.line,
                    attrs(kind=declaration.kind),
                ))
                type_resolver = next((
                    directive for directive in declaration.directives
                    if directive.name in {"resolver", "typeResolver"}
                    and directive.argument("class")
                ), None)
                if type_resolver is not None:
                    resolver_class = type_resolver.argument("class") or ""
                    packet.add(GraphFact(
                        "magento-graphql-type-resolver",
                        type_name,
                        "resolved-by",
                        resolver_class,
                        path,
                        declaration.line,
                        attrs(directive=type_resolver.name),
                    ), self.index.symbol_path(resolver_class))
                for declared_field in declaration.fields:
                    field_key = f"{type_name}.{declared_field.name}"
                    packet.add(GraphFact(
                        "magento-graphql-field",
                        type_name,
                        "has-field",
                        declared_field.name,
                        path,
                        declared_field.line,
                        attrs(dataType=declared_field.target_type),
                    ))
                    resolver = next((
                        directive for directive in declared_field.directives
                        if directive.name in {"resolver", "typeResolver"}
                        and directive.argument("class")
                    ), None)
                    if resolver is not None:
                        resolver_class = resolver.argument("class") or ""
                        packet.add(GraphFact(
                            "magento-graphql-resolver",
                            field_key,
                            "resolved-by",
                            resolver_class,
                            path,
                            declared_field.line,
                            attrs(directive=resolver.name),
                        ), self.index.symbol_path(resolver_class))

    def graphql_clients(self, modules: tuple[ModuleRecord, ...]) -> None:
        """Link embedded operations to the unique schema fields they select.

        Magento's GraphQL boundary is a typed traversal, not a shared-word
        relationship. Each segment must resolve from its current GraphQL owner
        to exactly one enabled schema declaration; ambiguity or a missing field
        makes the plugin abstain from that segment and every deeper segment.
        """
        declarations: dict[
            tuple[str, str],
            list[tuple[str, str, int]],
        ] = {}
        root_types: dict[str, set[str]] = {}
        for schema_path, content in sorted(self.index.artifacts.items()):
            if not schema_path.casefold().endswith(".graphqls"):
                continue
            module = self.index.module_for_path(schema_path, modules)
            if module is None or not module.enabled:
                continue
            for operation, type_name in parse_schema_root_types(content):
                root_types.setdefault(operation, set()).add(type_name)
            for definition in parse_schema(content):
                for declared_field in definition.fields:
                    declarations.setdefault(
                        (definition.name, declared_field.name),
                        [],
                    ).append((
                        schema_path,
                        declared_field.target_type,
                        declared_field.line,
                    ))

        resolved_root_types = {
            operation: next(iter(type_names))
            for operation, type_names in root_types.items()
            if len(type_names) == 1
        }

        for client_path, content in sorted(self.index.artifacts.items()):
            if not client_path.casefold().endswith(
                MAGENTO_GRAPHQL_CLIENT_SUFFIXES
            ):
                continue
            for selection in parse_operations(
                content,
                embedded_only=not client_path.casefold().endswith(
                    (".gql", ".graphql")
                ),
                root_types=resolved_root_types,
            ):
                owner = selection.root
                resolved: tuple[str, str, int] | None = None
                resolved_owner = ""
                for segment in selection.segments:
                    candidates = declarations.get((owner, segment), ())
                    if len(candidates) != 1:
                        resolved = None
                        break
                    resolved_owner = owner
                    resolved = candidates[0]
                    owner = resolved[1]
                if resolved is None:
                    continue
                schema_path, target_type, declaration_line = resolved
                selection_key = ".".join((selection.root, *selection.segments))
                packet = self.graph.packet(
                    "magento-graphql-client",
                    f"{client_path}:{selection_key}",
                    operationRoot=selection.root,
                )
                packet.add(GraphFact(
                    "magento-graphql-operation-field",
                    f"{client_path}::{selection_key}",
                    "selects-schema-field",
                    f"{schema_path}::{resolved_owner}.{selection.segments[-1]}",
                    client_path,
                    selection.line,
                    attrs(
                        schemaPath=schema_path,
                        schemaLine=declaration_line,
                        targetType=target_type,
                        resolution="exact-typed-graphql-traversal",
                        semanticRole="topology",
                    ),
                ), schema_path)

    def extension_attributes(self, modules: tuple[ModuleRecord, ...]) -> None:
        schema_paths: dict[str, set[str]] = {}
        for schema_path, _, _ in self.index.ordered_configs(
            "db_schema.xml",
            modules,
            "global",
        ):
            schema_root = self.index.xml(schema_path)
            if schema_root is None:
                continue
            for table_node in (
                node for node in schema_root
                if tag(node) == "table" and node.get("name")
            ):
                schema_paths.setdefault(
                    table_node.get("name"),
                    set(),
                ).add(schema_path)

        effective: dict[
            tuple[str, str],
            dict[str, object],
        ] = {}
        for path, module, order in self.index.ordered_configs("extension_attributes.xml", modules, "global"):
            root = self.index.xml(path)
            if root is None:
                continue
            content = self.index.artifacts[path]
            for extension in (
                node for node in root.iter()
                if tag(node) == "extension_attributes" and node.get("for")
            ):
                interface = extension.get("for").lstrip("\\")
                packet = self.graph.packet("magento-extension-attributes", interface)
                for attribute in (
                    node for node in extension
                    if tag(node) == "attribute" and node.get("code") and node.get("type")
                ):
                    code = attribute.get("code")
                    record = effective.setdefault(
                        (interface, code),
                        {
                            "attributes": {},
                            "path": path,
                            "line": line(content, code),
                            "module": (
                                module.name
                                if module
                                else "application"
                            ),
                            "order": order,
                            "paths": set(),
                            "resources": set(),
                            "join": None,
                        },
                    )
                    record["attributes"].update({
                        name.rsplit("}", 1)[-1]: value
                        for name, value in attribute.attrib.items()
                    })
                    record["path"] = path
                    record["line"] = line(content, code)
                    record["module"] = (
                        module.name if module else "application"
                    )
                    record["order"] = order
                    record["paths"].add(path)
                    for resource in (
                        node for container in attribute
                        if tag(container) == "resources"
                        for node in container
                        if tag(node) == "resource" and node.get("ref")
                    ):
                        record["resources"].add(resource.get("ref"))
                    join_node = next(
                        (
                            node for node in attribute
                            if tag(node) == "join"
                        ),
                        None,
                    )
                    if join_node is not None:
                        fields = tuple(
                            (
                                (field_node.text or "").strip(),
                                (
                                    field_node.get("column")
                                    or (field_node.text or "").strip()
                                ),
                            )
                            for field_node in join_node
                            if tag(field_node) == "field"
                            and (field_node.text or "").strip()
                        )
                        record["join"] = {
                            "reference_table": join_node.get(
                                "reference_table",
                                "",
                            ),
                            "reference_field": join_node.get(
                                "reference_field",
                                "",
                            ),
                            "join_on_field": join_node.get(
                                "join_on_field",
                                "",
                            ),
                            "fields": fields,
                            "path": path,
                            "line": line(
                                content,
                                join_node.get("reference_table", ""),
                            ),
                        }

        for (interface, code), record in sorted(effective.items()):
            attribute_values = record["attributes"]
            data_type = str(attribute_values.get("type", ""))
            target = data_type.replace("[]", "").lstrip("\\")
            packet = self.graph.packet(
                "magento-extension-attributes",
                interface,
            )
            packet.add(GraphFact(
                "magento-extension-attribute",
                interface,
                "adds-attribute",
                code,
                str(record["path"]),
                int(record["line"]),
                attrs(
                    dataType=data_type,
                    module=record["module"],
                    order=record["order"],
                    resources=",".join(sorted(record["resources"])),
                ),
            ), self.index.symbol_path(interface), self.index.symbol_path(target),
                *sorted(record["paths"]))

            join_record = record.get("join")
            if not join_record:
                continue
            if data_type.endswith("[]"):
                # JoinProcessor cannot hydrate an array-typed extension
                # attribute from a joined row. Preserve the declaration, but
                # do not present the join as an effective data relationship.
                packet.add(GraphFact(
                    "magento-extension-attribute-join-inapplicable",
                    f"{interface}.{code}",
                    "cannot-hydrate-array-type",
                    data_type,
                    str(join_record["path"]),
                    int(join_record["line"]),
                    attrs(
                        module=record["module"],
                        semanticRole="diagnostic",
                    ),
                ), self.index.symbol_path(interface), self.index.symbol_path(target),
                    *sorted(record["paths"]))
                continue
            reference_table = str(
                join_record.get("reference_table", "")
            )
            reference_field = str(
                join_record.get("reference_field", "")
            )
            join_on_field = str(
                join_record.get("join_on_field", "")
            )
            packet.add(GraphFact(
                "magento-extension-attribute-join",
                f"{interface}.{code}",
                "joins-reference-table",
                reference_table,
                str(join_record["path"]),
                int(join_record["line"]),
                attrs(
                    dataType=data_type,
                    referenceField=reference_field,
                    joinOnField=join_on_field,
                    tableAlias=f"extension_attribute_{code}",
                ),
            ), self.index.symbol_path(interface), self.index.symbol_path(target),
                *sorted(record["paths"]),
                *sorted(schema_paths.get(reference_table, ())))
            for property_name, column_name in join_record["fields"]:
                packet.add(GraphFact(
                    "magento-extension-attribute-join-field",
                    f"{interface}.{code}",
                    "maps-table-column-to-property",
                    f"{reference_table}.{column_name}",
                    str(join_record["path"]),
                    int(join_record["line"]),
                    attrs(
                        property=property_name,
                        column=column_name,
                        referenceField=reference_field,
                        joinOnField=join_on_field,
                    ),
                ), self.index.symbol_path(interface), self.index.symbol_path(target),
                    *sorted(record["paths"]),
                    *sorted(schema_paths.get(reference_table, ())))

    def generic_config_references(self, modules: tuple[ModuleRecord, ...]) -> None:
        """Connect additional Magento XML schemas to exact PHP declarations.

        Magento modules define many schema-specific configuration files beyond
        the specialized flows above.  This fallback does not guess semantics:
        it emits an edge only when an XML attribute/text value resolves to a
        PHP symbol present in the indexed repository.
        """
        existing_references = {
            (fact.path, fact.target.split("::", 1)[0].lstrip("\\"))
            for packet in self.graph.build()
            for fact in packet.facts
        }
        for path in sorted(self.index.artifacts):
            if not is_magento_config_xml(path):
                continue
            module = self.index.module_for_path(path, modules)
            is_application_config = path.startswith("app/etc/")
            if not is_application_config and (
                module is None or not module.enabled
            ):
                # Composer libraries can contain unrelated XML under `etc/`
                # (including MFTF's intentionally entity-bearing DI fixture).
                # Only deployed Magento module roots and application config
                # participate in Magento's merged configuration.
                continue
            root = self.index.xml(path)
            if root is None:
                continue
            area = config_area(path, PurePosixPath(path).name) or "global"
            packet = None
            content = self.index.artifacts[path]
            for element in root.iter():
                candidates = [
                    (name.rsplit("}", 1)[-1], value)
                    for name, value in element.attrib.items()
                ]
                if element.text and element.text.strip():
                    candidates.append(("value", element.text.strip()))
                for attribute_name, raw_value in candidates:
                    class_name = raw_value.split("::", 1)[0].strip().lstrip("\\")
                    symbol = self.index.symbol(class_name)
                    if symbol is None:
                        continue
                    if (path, class_name) in existing_references:
                        continue
                    if packet is None:
                        packet = self.graph.packet(
                            "magento-config-reference",
                            path,
                            module=module.name if module else "",
                            area=area,
                            schema=PurePosixPath(path).name,
                        )
                    packet.add(GraphFact(
                        "magento-config-class-reference",
                        f"{PurePosixPath(path).name}:{tag(element)}",
                        f"references-via-{attribute_name}",
                        class_name,
                        path,
                        line(content, raw_value),
                        attrs(
                            element=tag(element),
                            attribute=attribute_name,
                            module=module.name if module else "",
                            area=area,
                        ),
                    ), symbol.path)
