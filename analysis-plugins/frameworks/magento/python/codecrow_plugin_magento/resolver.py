from __future__ import annotations

import logging
import time
from functools import partial
from typing import Callable

from codecrow_plugins import PluginDiagnostic, RepositoryAnalysis, SymbolDefinition

from .architecture import PacketGraph
from .component_discovery import ComponentDiscovery
from .configuration_topology import ConfigurationTopology
from .data_topology import DataTopology
from .dependency_injection import DependencyInjectionTopology
from .event_topology import EventTopology
from .frontend_topology import FrontendTopology
from .javascript import OptionalJavaScriptEnrichmentError
from .layout_topology import LayoutTopology
from .message_queue_topology import MessageQueueTopology
from .resolution_index import RepositorySourceIndex
from .resolution_models import ConfigurationSources, LayoutSources
from .route_topology import RouteTopology
from .view_components import ViewComponentTopology


logger = logging.getLogger(__name__)


class MagentoRepositoryResolver:
    """Compose ordered Magento topology stages for one repository scope."""

    def __init__(
        self,
        plugin_id: str,
        artifacts: dict[str, str],
        symbols: tuple[SymbolDefinition, ...],
        progress_callback: Callable[[dict[str, object]], None] | None = None,
        deadline: float | None = None,
    ) -> None:
        self.progress_callback = progress_callback
        self.deadline = deadline
        self.index = RepositorySourceIndex(plugin_id, artifacts, symbols)
        self.graph = PacketGraph(plugin_id)
        configuration_sources = ConfigurationSources()
        layout_sources = LayoutSources()
        self.discovery = ComponentDiscovery(self.index, self.graph)
        self.di = DependencyInjectionTopology(self.index, self.graph)
        self.components = ViewComponentTopology(
            self.index,
            self.graph,
            configuration_sources,
        )
        self.frontend = FrontendTopology(self.index, self.graph, layout_sources)
        self.queues = MessageQueueTopology(self.index, self.graph)
        self.data = DataTopology(self.index, self.graph)
        self.events = EventTopology(self.index, self.graph, self.di)
        self.layouts = LayoutTopology(self.index, self.graph, self.di, layout_sources)
        self.configuration = ConfigurationTopology(
            self.index,
            self.graph,
            configuration_sources,
            self.di,
        )
        self.routes = RouteTopology(
            self.index,
            self.graph,
            configuration_sources,
            self.layouts,
        )

    @property
    def invalid_paths(self) -> set[str]:
        return self.index.invalid_paths

    def report_progress(self, event: dict[str, object]) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(event)
        except Exception:
            # Repository progress is optional host observability.
            return

    def check_deadline(self, stage_name: str) -> None:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError(
                "Magento repository architecture exceeded its time budget "
                f"during {stage_name}"
            )

    def run_stage(self, stage_name: str, stage):
        self.check_deadline(stage_name)
        started = time.monotonic()
        self.report_progress({
            "pluginId": self.index.plugin_id,
            "substage": stage_name,
            "status": "started",
            "message": f"Building Magento architecture: {stage_name}",
        })
        try:
            result = stage()
            self.check_deadline(stage_name)
        except Exception as exception:
            duration_ms = round((time.monotonic() - started) * 1000)
            status = "timed_out" if isinstance(exception, TimeoutError) else "failed"
            logger.warning(
                "Magento architecture substage %s status=%s duration_ms=%s: %s",
                stage_name,
                status,
                duration_ms,
                exception,
            )
            self.report_progress({
                "pluginId": self.index.plugin_id,
                "substage": stage_name,
                "status": status,
                "durationMs": duration_ms,
                "message": (
                    f"Magento architecture {stage_name} {status.replace('_', ' ')} "
                    f"after {duration_ms} ms"
                ),
            })
            raise
        duration_ms = round((time.monotonic() - started) * 1000)
        logger.info(
            "Magento architecture substage %s status=completed duration_ms=%s",
            stage_name,
            duration_ms,
        )
        self.report_progress({
            "pluginId": self.index.plugin_id,
            "substage": stage_name,
            "status": "completed",
            "durationMs": duration_ms,
            "message": (
                f"Built Magento architecture: {stage_name} in {duration_ms} ms"
            ),
        })
        return result

    def _stages(self, modules, themes, di_states):
        """Keep dependencies ordered while sharing the standalone-theme path."""
        stages = []
        if modules:
            stages.extend((
                ("constructor/DI", partial(self.di.constructor_packets, modules, di_states)),
                ("generated factories", partial(self.di.generated_factory_packets, modules, di_states)),
                ("generated proxies", partial(self.di.generated_proxy_packets, modules, di_states)),
                ("events", partial(self.events.events, modules, di_states)),
                ("system configuration", partial(self.configuration.system_configuration, modules)),
            ))
        stages.append((
            "routes/layouts",
            partial(self.routes.routes_and_layouts, modules, themes, di_states),
        ))
        if modules:
            stages.append(("Admin menu", partial(self.configuration.admin_menu, modules)))
        stages.extend((
            ("template globals", self.frontend.template_globals),
            ("template events", partial(self.frontend.template_events, themes)),
            ("UI components", partial(self.components.ui_components, modules, themes)),
        ))
        if modules:
            stages.append((
                "email templates", partial(self.components.email_templates, modules, themes),
            ))
        stages.extend((
            ("RequireJS", partial(self.frontend.requirejs, modules, themes)),
            ("frontend initializers", partial(self.frontend.frontend_initializers, modules, themes)),
            ("AMD consumers", partial(self.frontend.amd_consumers, modules, themes)),
        ))
        if modules:
            stages.extend((
                ("Web API/ACL", partial(self.configuration.webapi_and_acl, modules, di_states)),
                ("cron", partial(self.configuration.cron, modules)),
                ("message queues", partial(self.queues.message_queues, modules)),
                ("indexers/mview", partial(self.data.indexers_and_materialized_views, modules)),
                ("declarative schema", partial(self.data.schema, modules)),
                ("GraphQL", partial(self.data.graphql, modules)),
                ("GraphQL clients", partial(self.data.graphql_clients, modules)),
                ("extension attributes", partial(self.data.extension_attributes, modules)),
                ("generic config references", partial(self.data.generic_config_references, modules)),
            ))
        return stages

    def resolve(self) -> tuple[RepositoryAnalysis, tuple[PluginDiagnostic, ...]]:
        started = time.monotonic()
        modules = self.run_stage("module discovery", self.discovery.modules)
        if modules:
            self.run_stage("module packets", partial(self.discovery.module_packets, modules))
        themes = self.run_stage("theme discovery", partial(self.discovery.themes, modules))
        if not modules and not themes:
            return RepositoryAnalysis(), tuple(self.index.diagnostics)
        di_states = (
            self.run_stage("dependency injection", partial(self.di.di, modules))
            if modules else None
        )
        for stage_name, stage in self._stages(modules, themes, di_states):
            try:
                self.run_stage(stage_name, stage)
            except TimeoutError:
                raise
            except OptionalJavaScriptEnrichmentError as exception:
                self.index.record_optional_enrichment_failure(stage_name, exception)
            except Exception as exception:
                raise RuntimeError(
                    f"Magento {stage_name} enrichment failed: "
                    f"{type(exception).__name__}: {exception}"
                ) from exception
        packets = self.run_stage("packet materialization", self.graph.build)
        logger.info(
            "Magento repository resolution: modules=%s themes=%s packets=%s elapsed=%.3fs",
            len(modules), len(themes), len(packets), time.monotonic() - started,
        )
        return RepositoryAnalysis(packets=packets), tuple(self.index.diagnostics)
