from __future__ import annotations

from codecrow_plugins import GraphFact

from .architecture import ModuleRecord, PacketGraph, attrs, line, tag
from .resolution_index import RepositorySourceIndex
from .resolution_models import (
    _BROKER_DEFAULT_EXCHANGE,
    _BUILTIN_MESSAGE_CONSUMERS,
    _DEFAULT_MESSAGE_CONSUMER,
    _DEPLOYMENT_DEFAULT_CONNECTION,
    _MASS_MESSAGE_CONSUMER,
    _enabled,
    _message_topic_matches,
)


class MessageQueueTopology:
    """Build Magento queues relationships from repository evidence."""

    def __init__(self, index: RepositorySourceIndex, graph: PacketGraph) -> None:
        self.index = index
        self.graph = graph

    def add_message_consumer_resolution(
        self,
        packet,
        *,
        topic: str,
        topic_record: dict[str, object],
        consumer: str,
        consumer_record: dict[str, object],
        destination: str,
        exchange: str,
        publisher_connection: str,
        topology_connection: str,
        consumer_exact: bool,
        route_paths: tuple[str, ...],
    ) -> None:
        """Attach one queue consumer and its runtime-selected callbacks."""
        consumer_attributes = consumer_record["attributes"]
        consumer_connection = str(
            consumer_attributes.get(
                "connection",
                _DEPLOYMENT_DEFAULT_CONNECTION,
            )
        )
        handler = str(consumer_attributes.get("handler", ""))
        configured_consumer_instance = str(
            consumer_attributes.get("consumerInstance", "")
        ).strip()
        consumer_instance = (
            configured_consumer_instance
            or _DEFAULT_MESSAGE_CONSUMER
        )
        consumer_implementation_resolved = (
            not configured_consumer_instance
            or consumer_instance
            in {
                *_BUILTIN_MESSAGE_CONSUMERS,
                _MASS_MESSAGE_CONSUMER,
            }
        )
        packet.add(GraphFact(
            (
                "magento-message-effective-consumer"
                if consumer_exact
                else "magento-message-consumer-candidate"
            ),
            topic,
            (
                "handled-by-consumer"
                if consumer_exact
                else "may-be-handled-by-consumer"
            ),
            consumer,
            str(consumer_record["path"]),
            int(consumer_record["line"]),
            attrs(
                queue=destination,
                exchange=exchange,
                publisherConnection=publisher_connection,
                topologyConnection=topology_connection,
                consumerConnection=consumer_connection,
                connectionResolved=consumer_exact,
                handler=handler,
                consumerInstance=consumer_instance,
                consumerImplementationResolved=(
                    consumer_implementation_resolved
                ),
            ),
        ), self.index.symbol_path(consumer_instance),
            self.index.symbol_path(
                handler.split("::", 1)[0] if handler else ""
            ), *sorted(topic_record["paths"]),
            *route_paths,
            *sorted(consumer_record["paths"]))

        communication_handlers = []
        for handler_name, handler_record in sorted(
            topic_record.get("handlers", {}).items()
        ):
            handler_attributes = handler_record["attributes"]
            if not _enabled(handler_attributes.get("disabled")):
                continue
            target_type = str(
                handler_attributes.get("type", "")
            ).strip()
            target_method = str(
                handler_attributes.get("method", "")
            ).strip()
            communication_handlers.append({
                "target": (
                    f"{target_type}::{target_method}"
                    if target_type and target_method
                    else (target_type or str(handler_name))
                ),
                "type": target_type,
                "method": target_method,
                "source": "communication",
                "selection": (
                    "additive"
                    if consumer_instance == _MASS_MESSAGE_CONSUMER
                    else (
                        "fallback"
                        if consumer_implementation_resolved
                        else "implementation-defined"
                    )
                ),
                "record": handler_record,
            })

        queue_handlers = []
        if handler.strip():
            target_type, separator, target_method = (
                handler.strip().partition("::")
            )
            queue_handlers.append({
                "target": handler.strip(),
                "type": target_type.strip(),
                "method": (
                    target_method.strip()
                    if separator
                    else ""
                ),
                "source": "queue-consumer",
                "selection": (
                    "additive"
                    if consumer_instance == _MASS_MESSAGE_CONSUMER
                    else (
                        "override"
                        if consumer_implementation_resolved
                        else "implementation-defined"
                    )
                ),
                "record": consumer_record,
            })

        if consumer_instance == _MASS_MESSAGE_CONSUMER:
            selected_handlers = [
                *communication_handlers,
                *queue_handlers,
            ]
        elif consumer_instance in _BUILTIN_MESSAGE_CONSUMERS:
            selected_handlers = (
                queue_handlers
                if queue_handlers
                else communication_handlers
            )
        else:
            # Adobe's contract delegates handler semantics to an explicit
            # custom consumer implementation. Preserve every configured source
            # as a candidate without inventing which callback the class invokes.
            selected_handlers = [
                *communication_handlers,
                *queue_handlers,
            ]

        handler_exact = (
            consumer_exact
            and consumer_implementation_resolved
        )
        for selected_handler in selected_handlers:
            handler_record = selected_handler["record"]
            handler_valid = bool(
                selected_handler["type"]
                and selected_handler["method"]
            )
            effective = handler_exact and handler_valid
            packet.add(GraphFact(
                (
                    "magento-message-effective-handler"
                    if effective
                    else (
                        "magento-message-handler-unresolved"
                        if handler_exact
                        else "magento-message-handler-candidate"
                    )
                ),
                topic,
                (
                    "handled-by"
                    if effective
                    else (
                        "has-invalid-handler"
                        if handler_exact
                        else "may-be-handled-by"
                    )
                ),
                str(selected_handler["target"]),
                str(handler_record["path"]),
                int(handler_record["line"]),
                attrs(
                    consumer=consumer,
                    consumerInstance=consumer_instance,
                    consumerImplementationResolved=(
                        consumer_implementation_resolved
                    ),
                    connectionResolved=consumer_exact,
                    handlerSource=selected_handler["source"],
                    handlerSelection=selected_handler[
                        "selection"
                    ],
                    handlerValid=handler_valid,
                ),
            ), self.index.symbol_path(
                str(selected_handler["type"])
            ), self.index.symbol_path(consumer_instance),
                *sorted(topic_record["paths"]),
                *route_paths,
                *sorted(consumer_record["paths"]),
                *sorted(handler_record["paths"]))

        if handler_exact and not selected_handlers:
            packet.add(GraphFact(
                "magento-message-handler-unresolved",
                topic,
                "has-no-configured-handler",
                consumer,
                str(consumer_record["path"]),
                int(consumer_record["line"]),
                attrs(
                    consumer=consumer,
                    consumerInstance=consumer_instance,
                    consumerImplementationResolved=True,
                    connectionResolved=True,
                    reason="no-enabled-handler",
                ),
            ), self.index.symbol_path(consumer_instance),
                *sorted(topic_record["paths"]),
                *route_paths,
                *sorted(consumer_record["paths"]))

        if not consumer_implementation_resolved:
            packet.add(GraphFact(
                "magento-message-handler-resolution-dependent",
                topic,
                "handler-use-defined-by",
                consumer_instance,
                str(consumer_record["path"]),
                int(consumer_record["line"]),
                attrs(
                    consumer=consumer,
                    connectionResolved=consumer_exact,
                    configuredHandlerCount=len(selected_handlers),
                ),
            ), self.index.symbol_path(consumer_instance),
                *sorted(topic_record["paths"]),
                *route_paths,
                *sorted(consumer_record["paths"]))

    def message_queues(self, modules: tuple[ModuleRecord, ...]) -> None:
        topics: dict[str, dict[str, object]] = {}
        consumers: dict[str, dict[str, object]] = {}
        publishers: dict[str, dict[str, object]] = {}
        exchanges: dict[tuple[str, str], dict[str, object]] = {}

        def merge_record(
            values: dict,
            key,
            element,
            path: str,
            module: ModuleRecord | None,
            order: int,
        ) -> dict[str, object]:
            record = values.setdefault(
                key,
                {
                    "attributes": {},
                    "path": path,
                    "line": 1,
                    "module": module.name if module else "application",
                    "order": order,
                    "paths": set(),
                },
            )
            record["attributes"].update({
                name.rsplit("}", 1)[-1]: value
                for name, value in element.attrib.items()
            })
            record["path"] = path
            record["line"] = line(
                self.index.artifacts[path],
                str(
                    element.get("name")
                    or element.get("topic")
                    or element.get("id")
                    or key
                ),
            )
            record["module"] = module.name if module else "application"
            record["order"] = order
            record["paths"].add(path)
            return record

        for path, module, order in self.index.ordered_configs(
            "communication.xml",
            modules,
            "global",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            for topic_node in (
                node for node in root.iter()
                if tag(node) == "topic" and node.get("name")
            ):
                topic = topic_node.get("name")
                record = merge_record(
                    topics,
                    topic,
                    topic_node,
                    path,
                    module,
                    order,
                )
                handlers = record.setdefault("handlers", {})
                for handler_node in (
                    node for node in topic_node
                    if tag(node) == "handler"
                ):
                    target = handler_node.get("type", "")
                    method = handler_node.get("method", "")
                    handler_key = (
                        handler_node.get("name")
                        or f"{target}::{method}"
                    )
                    if not handler_key:
                        continue
                    merge_record(
                        handlers,
                        handler_key,
                        handler_node,
                        path,
                        module,
                        order,
                    )

        for path, module, order in self.index.ordered_configs(
            "queue_consumer.xml",
            modules,
            "global",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            for consumer_node in (
                node for node in root.iter()
                if tag(node) == "consumer" and node.get("name")
            ):
                merge_record(
                    consumers,
                    consumer_node.get("name"),
                    consumer_node,
                    path,
                    module,
                    order,
                )

        for path, module, order in self.index.ordered_configs(
            "queue_publisher.xml",
            modules,
            "global",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            for publisher_node in (
                node for node in root.iter()
                if tag(node) == "publisher" and node.get("topic")
            ):
                topic = publisher_node.get("topic")
                record = merge_record(
                    publishers,
                    topic,
                    publisher_node,
                    path,
                    module,
                    order,
                )
                connections = record.setdefault("connections", {})
                for connection_node in (
                    node for node in publisher_node
                    if tag(node) == "connection"
                ):
                    connection_name = (
                        connection_node.get("name")
                        or _DEPLOYMENT_DEFAULT_CONNECTION
                    )
                    merge_record(
                        connections,
                        connection_name,
                        connection_node,
                        path,
                        module,
                        order,
                    )

        for path, module, order in self.index.ordered_configs(
            "queue_topology.xml",
            modules,
            "global",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            for exchange_node in (
                node for node in root.iter()
                if tag(node) == "exchange"
                and node.get("name") is not None
            ):
                connection = (
                    exchange_node.get("connection")
                    or _DEPLOYMENT_DEFAULT_CONNECTION
                )
                exchange_key = (exchange_node.get("name"), connection)
                record = merge_record(
                    exchanges,
                    exchange_key,
                    exchange_node,
                    path,
                    module,
                    order,
                )
                bindings = record.setdefault("bindings", {})
                for binding_node in (
                    node for node in exchange_node
                    if tag(node) == "binding"
                    and node.get("topic")
                    and node.get("destination")
                ):
                    binding_key = (
                        binding_node.get("destinationType", "queue"),
                        binding_node.get("destination"),
                        binding_node.get("topic"),
                    )
                    merge_record(
                        bindings,
                        binding_key,
                        binding_node,
                        path,
                        module,
                        order,
                    )

        for topic, record in sorted(topics.items()):
            topic_attributes = record["attributes"]
            packet = self.graph.packet("magento-message-queue", topic)
            packet.add(GraphFact(
                "magento-message-topic",
                topic,
                "declared-in",
                str(record["path"]),
                str(record["path"]),
                int(record["line"]),
                attrs(
                    request=topic_attributes.get("request", ""),
                    response=topic_attributes.get("response", ""),
                    schema=topic_attributes.get("schema", ""),
                    module=record["module"],
                    order=record["order"],
                ),
            ), *sorted(record["paths"]))
            for handler_name, handler in sorted(
                record.get("handlers", {}).items()
            ):
                handler_attributes = handler["attributes"]
                target = str(handler_attributes.get("type", ""))
                method = str(handler_attributes.get("method", ""))
                disabled = not _enabled(
                    handler_attributes.get("disabled"),
                )
                packet.add(GraphFact(
                    "magento-message-handler",
                    topic,
                    (
                        "disables-handler"
                        if disabled
                        else "handled-by"
                    ),
                    (
                        f"{target}::{method}"
                        if target and method
                        else (target or handler_name)
                    ),
                    str(handler["path"]),
                    int(handler["line"]),
                    attrs(
                        name=handler_name,
                        module=handler["module"],
                        order=handler["order"],
                    ),
                ), self.index.symbol_path(target), *sorted(handler["paths"]))

        for consumer, record in sorted(consumers.items()):
            consumer_attributes = record["attributes"]
            queue = str(consumer_attributes.get("queue", consumer))
            handler = str(consumer_attributes.get("handler", ""))
            connection = str(
                consumer_attributes.get(
                    "connection",
                    _DEPLOYMENT_DEFAULT_CONNECTION,
                )
            )
            packet = self.graph.packet(
                "magento-message-consumer",
                consumer,
            )
            if not queue.strip():
                packet.add(GraphFact(
                    "magento-message-consumer-invalid",
                    consumer,
                    "has-empty-queue",
                    consumer,
                    str(record["path"]),
                    int(record["line"]),
                    attrs(
                        handler=handler,
                        connection=connection,
                        module=record["module"],
                        order=record["order"],
                        semanticRole="diagnostic",
                    ),
                ), self.index.symbol_path(
                    handler.split("::", 1)[0] if handler else ""
                ), *sorted(record["paths"]))
                continue
            packet.add(GraphFact(
                "magento-message-consumer",
                consumer,
                "consumes-queue",
                queue,
                str(record["path"]),
                int(record["line"]),
                attrs(
                    handler=handler,
                    connection=connection,
                    connectionResolved=(
                        connection != _DEPLOYMENT_DEFAULT_CONNECTION
                    ),
                    consumerInstance=consumer_attributes.get(
                        "consumerInstance",
                        "",
                    ),
                    module=record["module"],
                    order=record["order"],
                ),
            ), self.index.symbol_path(
                handler.split("::", 1)[0] if handler else ""
            ), *sorted(record["paths"]))

        for (exchange_name, connection), record in sorted(exchanges.items()):
            for binding_key, binding in sorted(
                record.get("bindings", {}).items()
            ):
                binding_attributes = binding["attributes"]
                topic_pattern = str(binding_attributes.get("topic", ""))
                destination = str(
                    binding_attributes.get("destination", "")
                )
                packet = self.graph.packet(
                    "magento-message-queue",
                    topic_pattern,
                )
                packet.add(GraphFact(
                    "magento-message-binding",
                    topic_pattern,
                    (
                        "disables-route-to"
                        if not _enabled(binding_attributes.get("disabled"))
                        else "routes-to"
                    ),
                    destination,
                    str(binding["path"]),
                    int(binding["line"]),
                    attrs(
                        exchange=(
                            exchange_name
                            or _BROKER_DEFAULT_EXCHANGE
                        ),
                        connection=connection,
                        connectionResolved=(
                            connection != _DEPLOYMENT_DEFAULT_CONNECTION
                        ),
                        destinationType=binding_key[0],
                        module=binding["module"],
                        order=binding["order"],
                    ),
                ), *sorted(record["paths"]), *sorted(binding["paths"]))

        publisher_resolved_consumers: set[tuple[str, str]] = set()
        for topic, topic_record in sorted(topics.items()):
            publisher = publishers.get(topic)
            packet = self.graph.packet("magento-message-queue", topic)
            if publisher is None:
                packet.add(GraphFact(
                    "magento-message-route-unresolved",
                    topic,
                    "has-no-publisher",
                    topic,
                    str(topic_record["path"]),
                    int(topic_record["line"]),
                    attrs(reason="publisher-configuration-absent"),
                ), *sorted(topic_record["paths"]))
                continue

            publisher_attributes = publisher["attributes"]
            if not _enabled(publisher_attributes.get("disabled")):
                packet.add(GraphFact(
                    "magento-message-publisher",
                    topic,
                    "publisher-disabled",
                    topic,
                    str(publisher["path"]),
                    int(publisher["line"]),
                    attrs(
                        module=publisher["module"],
                        order=publisher["order"],
                    ),
                ), *sorted(publisher["paths"]))
                continue

            connections = publisher.get("connections", {})
            if not connections:
                connections[_DEPLOYMENT_DEFAULT_CONNECTION] = {
                    "attributes": {
                        "name": _DEPLOYMENT_DEFAULT_CONNECTION,
                        "exchange": "magento",
                    },
                    "path": publisher["path"],
                    "line": publisher["line"],
                    "module": publisher["module"],
                    "order": publisher["order"],
                    "paths": set(publisher["paths"]),
                }
            active_connection = next(
                (
                    connection
                    for connection in connections.values()
                    if _enabled(connection["attributes"].get("disabled"))
                ),
                None,
            )
            # Magento adds its deployment default when every configured
            # connection is disabled.
            if active_connection is None:
                active_connection = {
                    "attributes": {
                        "name": _DEPLOYMENT_DEFAULT_CONNECTION,
                        "exchange": "magento",
                    },
                    "path": publisher["path"],
                    "line": publisher["line"],
                    "module": publisher["module"],
                    "order": publisher["order"],
                    "paths": set(publisher["paths"]),
                }

            connection_attributes = active_connection["attributes"]
            connection = str(
                connection_attributes.get(
                    "name",
                    _DEPLOYMENT_DEFAULT_CONNECTION,
                )
            )
            exchange_name = str(
                connection_attributes.get("exchange", "magento")
            )
            display_exchange = (
                exchange_name or _BROKER_DEFAULT_EXCHANGE
            )
            connection_resolved = (
                connection != _DEPLOYMENT_DEFAULT_CONNECTION
            )
            packet.add(GraphFact(
                "magento-message-publisher",
                topic,
                "publishes-through",
                display_exchange,
                str(active_connection["path"]),
                int(active_connection["line"]),
                attrs(
                    connection=connection,
                    connectionResolved=connection_resolved,
                    module=active_connection["module"],
                    order=active_connection["order"],
                ),
            ), *sorted(publisher["paths"]), *sorted(
                active_connection["paths"]
            ))

            exchange_candidates = [
                (key, record)
                for key, record in exchanges.items()
                if key[0] == exchange_name
                and (
                    key[1] == connection
                    or key[1] == _DEPLOYMENT_DEFAULT_CONNECTION
                    or connection == _DEPLOYMENT_DEFAULT_CONNECTION
                )
            ]
            routed = False
            for (candidate_exchange, candidate_connection), exchange in (
                exchange_candidates
            ):
                binding = next(
                    (
                        candidate
                        for candidate in exchange.get(
                            "bindings",
                            {},
                        ).values()
                        if _enabled(
                            candidate["attributes"].get("disabled")
                        )
                        and _message_topic_matches(
                            str(
                                candidate["attributes"].get(
                                    "topic",
                                    "",
                                )
                            ),
                            topic,
                        )
                    ),
                    None,
                )
                if binding is None:
                    continue
                routed = True
                destination = str(
                    binding["attributes"].get("destination", "")
                )
                exact_connection = (
                    connection_resolved
                    and candidate_connection == connection
                ) or (
                    connection == _DEPLOYMENT_DEFAULT_CONNECTION
                    and candidate_connection
                    == _DEPLOYMENT_DEFAULT_CONNECTION
                )
                route_kind = (
                    "magento-message-effective-route"
                    if exact_connection
                    else "magento-message-route-candidate"
                )
                route_relation = (
                    "routes-to-queue"
                    if exact_connection
                    else "may-route-to-queue"
                )
                packet.add(GraphFact(
                    route_kind,
                    topic,
                    route_relation,
                    destination,
                    str(binding["path"]),
                    int(binding["line"]),
                    attrs(
                        exchange=(
                            candidate_exchange
                            or _BROKER_DEFAULT_EXCHANGE
                        ),
                        publisherConnection=connection,
                        topologyConnection=candidate_connection,
                        connectionResolved=exact_connection,
                    ),
                ), *sorted(topic_record["paths"]),
                    *sorted(publisher["paths"]),
                    *sorted(exchange["paths"]),
                    *sorted(binding["paths"]))

                for consumer, consumer_record in sorted(consumers.items()):
                    consumer_attributes = consumer_record["attributes"]
                    consumer_queue = str(
                        consumer_attributes.get("queue", consumer)
                    )
                    if consumer_queue != destination:
                        continue
                    consumer_connection = str(
                        consumer_attributes.get(
                            "connection",
                            _DEPLOYMENT_DEFAULT_CONNECTION,
                        )
                    )
                    consumer_exact = exact_connection and (
                        consumer_connection == candidate_connection
                        or (
                            consumer_connection
                            == _DEPLOYMENT_DEFAULT_CONNECTION
                            and candidate_connection
                            == _DEPLOYMENT_DEFAULT_CONNECTION
                        )
                    )
                    if consumer_exact:
                        publisher_resolved_consumers.add((topic, consumer))
                    handler = str(
                        consumer_attributes.get("handler", "")
                    )
                    configured_consumer_instance = str(
                        consumer_attributes.get(
                            "consumerInstance",
                            "",
                        )
                    ).strip()
                    consumer_instance = (
                        configured_consumer_instance
                        or _DEFAULT_MESSAGE_CONSUMER
                    )
                    consumer_implementation_resolved = (
                        not configured_consumer_instance
                        or consumer_instance
                        in {
                            *_BUILTIN_MESSAGE_CONSUMERS,
                            _MASS_MESSAGE_CONSUMER,
                        }
                    )
                    packet.add(GraphFact(
                        (
                            "magento-message-effective-consumer"
                            if consumer_exact
                            else "magento-message-consumer-candidate"
                        ),
                        topic,
                        (
                            "handled-by-consumer"
                            if consumer_exact
                            else "may-be-handled-by-consumer"
                        ),
                        consumer,
                        str(consumer_record["path"]),
                        int(consumer_record["line"]),
                        attrs(
                            queue=destination,
                            exchange=candidate_exchange,
                            publisherConnection=connection,
                            topologyConnection=candidate_connection,
                            consumerConnection=consumer_connection,
                            connectionResolved=consumer_exact,
                            handler=handler,
                            consumerInstance=consumer_instance,
                            consumerImplementationResolved=(
                                consumer_implementation_resolved
                            ),
                        ),
                    ), self.index.symbol_path(consumer_instance),
                        self.index.symbol_path(
                            handler.split("::", 1)[0] if handler else ""
                        ), *sorted(topic_record["paths"]),
                        *sorted(publisher["paths"]),
                        *sorted(exchange["paths"]),
                        *sorted(binding["paths"]),
                        *sorted(consumer_record["paths"]))

                    communication_handlers = []
                    for handler_name, handler_record in sorted(
                        topic_record.get("handlers", {}).items()
                    ):
                        handler_attributes = handler_record["attributes"]
                        if not _enabled(
                            handler_attributes.get("disabled")
                        ):
                            continue
                        target_type = str(
                            handler_attributes.get("type", "")
                        ).strip()
                        target_method = str(
                            handler_attributes.get("method", "")
                        ).strip()
                        communication_handlers.append({
                            "target": (
                                f"{target_type}::{target_method}"
                                if target_type and target_method
                                else (
                                    target_type
                                    or str(handler_name)
                                )
                            ),
                            "type": target_type,
                            "method": target_method,
                            "source": "communication",
                            "selection": (
                                "additive"
                                if consumer_instance
                                == _MASS_MESSAGE_CONSUMER
                                else (
                                    "fallback"
                                    if consumer_implementation_resolved
                                    else "implementation-defined"
                                )
                            ),
                            "record": handler_record,
                        })

                    queue_handlers = []
                    if handler.strip():
                        target_type, separator, target_method = (
                            handler.strip().partition("::")
                        )
                        queue_handlers.append({
                            "target": handler.strip(),
                            "type": target_type.strip(),
                            "method": (
                                target_method.strip()
                                if separator
                                else ""
                            ),
                            "source": "queue-consumer",
                            "selection": (
                                "additive"
                                if consumer_instance
                                == _MASS_MESSAGE_CONSUMER
                                else (
                                    "override"
                                    if consumer_implementation_resolved
                                    else "implementation-defined"
                                )
                            ),
                            "record": consumer_record,
                        })

                    if consumer_instance == _MASS_MESSAGE_CONSUMER:
                        selected_handlers = [
                            *communication_handlers,
                            *queue_handlers,
                        ]
                    elif consumer_instance in _BUILTIN_MESSAGE_CONSUMERS:
                        selected_handlers = (
                            queue_handlers
                            if queue_handlers
                            else communication_handlers
                        )
                    else:
                        # Adobe's contract delegates handler semantics to an
                        # explicit custom consumer implementation. Preserve
                        # every configured source as a candidate without
                        # inventing which callback the class invokes.
                        selected_handlers = [
                            *communication_handlers,
                            *queue_handlers,
                        ]

                    handler_exact = (
                        consumer_exact
                        and consumer_implementation_resolved
                    )
                    for selected_handler in selected_handlers:
                        handler_record = selected_handler["record"]
                        handler_valid = bool(
                            selected_handler["type"]
                            and selected_handler["method"]
                        )
                        effective = handler_exact and handler_valid
                        packet.add(GraphFact(
                            (
                                "magento-message-effective-handler"
                                if effective
                                else (
                                    "magento-message-handler-unresolved"
                                    if handler_exact
                                    else "magento-message-handler-candidate"
                                )
                            ),
                            topic,
                            (
                                "handled-by"
                                if effective
                                else (
                                    "has-invalid-handler"
                                    if handler_exact
                                    else "may-be-handled-by"
                                )
                            ),
                            str(selected_handler["target"]),
                            str(handler_record["path"]),
                            int(handler_record["line"]),
                            attrs(
                                consumer=consumer,
                                consumerInstance=consumer_instance,
                                consumerImplementationResolved=(
                                    consumer_implementation_resolved
                                ),
                                connectionResolved=consumer_exact,
                                handlerSource=selected_handler["source"],
                                handlerSelection=selected_handler[
                                    "selection"
                                ],
                                handlerValid=handler_valid,
                            ),
                        ), self.index.symbol_path(
                            str(selected_handler["type"])
                        ), self.index.symbol_path(consumer_instance),
                            *sorted(topic_record["paths"]),
                            *sorted(publisher["paths"]),
                            *sorted(exchange["paths"]),
                            *sorted(binding["paths"]),
                            *sorted(consumer_record["paths"]),
                            *sorted(handler_record["paths"]))

                    if (
                        handler_exact
                        and not selected_handlers
                    ):
                        packet.add(GraphFact(
                            "magento-message-handler-unresolved",
                            topic,
                            "has-no-configured-handler",
                            consumer,
                            str(consumer_record["path"]),
                            int(consumer_record["line"]),
                            attrs(
                                consumer=consumer,
                                consumerInstance=consumer_instance,
                                consumerImplementationResolved=True,
                                connectionResolved=True,
                                reason="no-enabled-handler",
                            ),
                        ), self.index.symbol_path(consumer_instance),
                            *sorted(topic_record["paths"]),
                            *sorted(publisher["paths"]),
                            *sorted(exchange["paths"]),
                            *sorted(binding["paths"]),
                            *sorted(consumer_record["paths"]))

                    if not consumer_implementation_resolved:
                        packet.add(GraphFact(
                            "magento-message-handler-resolution-dependent",
                            topic,
                            "handler-use-defined-by",
                            consumer_instance,
                            str(consumer_record["path"]),
                            int(consumer_record["line"]),
                            attrs(
                                consumer=consumer,
                                connectionResolved=consumer_exact,
                                configuredHandlerCount=len(
                                    selected_handlers
                                ),
                            ),
                        ), self.index.symbol_path(consumer_instance),
                            *sorted(topic_record["paths"]),
                            *sorted(consumer_record["paths"]))
                # QueueResolver returns the first enabled matching binding.
                break

            if not routed:
                packet.add(GraphFact(
                    "magento-message-route-unresolved",
                    topic,
                    "has-no-matching-binding",
                    display_exchange,
                    str(active_connection["path"]),
                    int(active_connection["line"]),
                    attrs(
                        connection=connection,
                        connectionResolved=connection_resolved,
                        reason="topology-binding-absent-or-disabled",
                    ),
                ), *sorted(topic_record["paths"]),
                    *sorted(publisher["paths"]),
                    *sorted(active_connection["paths"]))

        # A consumer does not require a local publisher. Adobe explicitly
        # supports topology + consumer configuration for queues populated by a
        # third-party system. Resolve that inbound route directly from the
        # enabled binding, queue name and connection instead of treating the
        # absent local publisher as missing handler evidence.
        inbound_routes: dict[
            tuple[str, str],
            list[dict[str, object]],
        ] = {}
        for (
            (exchange_name, topology_connection),
            exchange_record,
        ) in sorted(exchanges.items()):
            for binding_key, binding in sorted(
                exchange_record.get("bindings", {}).items()
            ):
                binding_attributes = binding["attributes"]
                if (
                    binding_key[0] != "queue"
                    or not _enabled(
                        binding_attributes.get("disabled")
                    )
                ):
                    continue
                destination = str(
                    binding_attributes.get("destination", "")
                )
                topic_pattern = str(
                    binding_attributes.get("topic", "")
                )
                for consumer, consumer_record in sorted(
                    consumers.items()
                ):
                    consumer_attributes = consumer_record["attributes"]
                    consumer_queue = str(
                        consumer_attributes.get("queue", consumer)
                    )
                    if consumer_queue != destination:
                        continue
                    consumer_connection = str(
                        consumer_attributes.get(
                            "connection",
                            _DEPLOYMENT_DEFAULT_CONNECTION,
                        )
                    )
                    connection_exact = (
                        consumer_connection == topology_connection
                        and (
                            consumer_connection
                            != _DEPLOYMENT_DEFAULT_CONNECTION
                            or topology_connection
                            == _DEPLOYMENT_DEFAULT_CONNECTION
                        )
                    )
                    for topic, topic_record in sorted(topics.items()):
                        if not _message_topic_matches(
                            topic_pattern,
                            topic,
                        ):
                            continue
                        inbound_routes.setdefault(
                            (topic, consumer),
                            [],
                        ).append({
                            "connectionExact": connection_exact,
                            "destination": destination,
                            "exchange": exchange_name,
                            "topologyConnection": topology_connection,
                            "topicRecord": topic_record,
                            "consumerRecord": consumer_record,
                            "paths": tuple(sorted({
                                *exchange_record["paths"],
                                *binding["paths"],
                            })),
                        })

        for (topic, consumer), routes in sorted(
            inbound_routes.items()
        ):
            if (topic, consumer) in publisher_resolved_consumers:
                continue
            ordered_routes = sorted(
                routes,
                key=lambda route: (
                    not bool(route["connectionExact"]),
                    str(route["exchange"]),
                    str(route["topologyConnection"]),
                    tuple(route["paths"]),
                ),
            )
            route = ordered_routes[0]
            packet = self.graph.packet(
                "magento-message-queue",
                topic,
            )
            self.add_message_consumer_resolution(
                packet,
                topic=topic,
                topic_record=route["topicRecord"],
                consumer=consumer,
                consumer_record=route["consumerRecord"],
                destination=str(route["destination"]),
                        exchange=(
                            str(route["exchange"])
                            or _BROKER_DEFAULT_EXCHANGE
                        ),
                publisher_connection="",
                topology_connection=str(
                    route["topologyConnection"]
                ),
                consumer_exact=bool(route["connectionExact"]),
                route_paths=route["paths"],
            )
