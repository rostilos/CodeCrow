from __future__ import annotations

import base64
import gzip
import json
from dataclasses import asdict

from codecrow_plugins import (
    FileArtifact,
    PluginOutcome,
    RepositoryAnalysis,
    RepositorySnapshot,
)

from .template_runtime import (
    AlpineEventDispatch,
    AlpineEventListener,
    AlpineProviderDefinition,
    AlpineProviderUse,
    TemplateRuntime,
    TemplateRuntimeVariable,
    ViewModelRequirement,
    WebApiReference,
    extract_template_runtime,
)
from .topology import HyvaTopologyResolver


class HyvaRepositorySession:
    SNAPSHOT_KIND = "hyva-template-runtime"

    def __init__(
        self,
        plugin_id: str,
        revision: str,
        templates: dict[str, TemplateRuntime] | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.revision = revision
        self.templates = dict(templates or {})

    @classmethod
    def restore(
        cls,
        plugin_id: str,
        revision: str,
        snapshots,
    ) -> "HyvaRepositorySession":
        snapshot = next(
            (
                item for item in snapshots
                if item.kind == cls.SNAPSHOT_KIND
            ),
            None,
        )
        if snapshot is None:
            raise ValueError(
                "Hyva repository snapshot is missing hyva-template-runtime"
            )
        raw = gzip.decompress(base64.b64decode(snapshot.content.encode("ascii")))
        records = json.loads(raw.decode("utf-8"))
        if not isinstance(records, dict):
            raise ValueError("Hyva template snapshot must contain an object")
        templates = {}
        for path, record in records.items():
            if not isinstance(path, str) or not isinstance(record, dict):
                raise ValueError("Hyva template snapshot contains invalid data")
            templates[path] = TemplateRuntime(
                requirements=tuple(sorted(
                    ViewModelRequirement(**value)
                    for value in record.get("requirements", ())
                )),
                webapi_references=tuple(sorted(
                    WebApiReference(
                        view_model_variable=value["view_model_variable"],
                        http_method=value["http_method"],
                        route=value["route"],
                        line=value["line"],
                        state_identifiers=tuple(
                            value.get("state_identifiers", ())
                        ),
                    )
                    for value in record.get("webapiReferences", ())
                )),
                alpine_identifiers=tuple(sorted(
                    record.get("alpineIdentifiers", ())
                )),
                alpine_provider_definitions=tuple(sorted(
                    AlpineProviderDefinition(**value)
                    for value in record.get(
                        "alpineProviderDefinitions",
                        (),
                    )
                )),
                alpine_provider_uses=tuple(sorted(
                    AlpineProviderUse(**value)
                    for value in record.get("alpineProviderUses", ())
                )),
                alpine_event_dispatches=tuple(sorted(
                    AlpineEventDispatch(**value)
                    for value in record.get("alpineEventDispatches", ())
                )),
                alpine_event_listeners=tuple(sorted(
                    AlpineEventListener(**value)
                    for value in record.get("alpineEventListeners", ())
                )),
                runtime_variables=tuple(sorted(
                    TemplateRuntimeVariable(**value)
                    for value in record.get("runtimeVariables", ())
                )),
            )
        return cls(plugin_id, revision, templates)

    def ingest(self, artifacts: tuple[FileArtifact, ...]) -> None:
        for artifact in artifacts:
            if not artifact.path.casefold().endswith(".phtml"):
                continue
            if artifact.deleted:
                self.templates.pop(artifact.path, None)
                continue
            # A changed source that fails extraction cannot keep facts from a
            # restored snapshot of the previous revision.
            self.templates.pop(artifact.path, None)
            runtime = extract_template_runtime(artifact.content)
            if (
                runtime.requirements
                or runtime.webapi_references
                or runtime.alpine_identifiers
                or runtime.alpine_provider_definitions
                or runtime.alpine_provider_uses
                or runtime.alpine_event_dispatches
                or runtime.alpine_event_listeners
                or runtime.runtime_variables
            ):
                self.templates[artifact.path] = runtime
            else:
                self.templates.pop(artifact.path, None)

    def _snapshot(self) -> RepositorySnapshot:
        records = {
            path: {
                "requirements": [
                    asdict(value) for value in runtime.requirements
                ],
                "webapiReferences": [
                    asdict(value) for value in runtime.webapi_references
                ],
                "alpineIdentifiers": list(runtime.alpine_identifiers),
                "alpineProviderDefinitions": [
                    asdict(value)
                    for value in runtime.alpine_provider_definitions
                ],
                "alpineProviderUses": [
                    asdict(value) for value in runtime.alpine_provider_uses
                ],
                "alpineEventDispatches": [
                    asdict(value) for value in runtime.alpine_event_dispatches
                ],
                "alpineEventListeners": [
                    asdict(value) for value in runtime.alpine_event_listeners
                ],
                "runtimeVariables": [
                    asdict(value) for value in runtime.runtime_variables
                ],
            }
            for path, runtime in sorted(self.templates.items())
        }
        raw = json.dumps(
            records,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        content = base64.b64encode(
            gzip.compress(raw, compresslevel=6, mtime=0)
        ).decode("ascii")
        return RepositorySnapshot(
            self.plugin_id,
            self.SNAPSHOT_KIND,
            content,
        )

    def finish(self, dependencies: RepositoryAnalysis):
        resolver = HyvaTopologyResolver(self.plugin_id, self.templates)
        packets = resolver.packets(dependencies)
        return PluginOutcome.handled(RepositoryAnalysis(
            packets=packets,
            snapshots=(self._snapshot(),),
            diagnostics=tuple(resolver.diagnostics),
        ))
