from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Mapping

from .api import DetectionAlternative, PluginDescriptor, PluginKind, ProjectCapabilities, RepositoryFacts
from .registry import PluginRegistry

MAX_DETECTION_EVIDENCE_PER_PLUGIN = 64


def _group_evidence(group: DetectionAlternative, facts: RepositoryFacts) -> tuple[str, ...] | None:
    paths = set(facts.paths)
    files_all_match = not group.files_all or all(path in paths for path in group.files_all)
    files_any_match = not group.files_any or any(path in paths for path in group.files_any)
    pattern_hits_all = {
        pattern: tuple(path for path in facts.paths if PurePosixPath(path).match(pattern))
        for pattern in group.path_patterns_all
    }
    pattern_hits_any = {
        pattern: tuple(path for path in facts.paths if PurePosixPath(path).match(pattern))
        for pattern in group.path_patterns_any
    }
    patterns_all_match = all(pattern_hits_all[pattern] for pattern in group.path_patterns_all)
    patterns_any_match = not group.path_patterns_any or any(pattern_hits_any.values())
    marker_hits = tuple(
        marker
        for marker in group.content_markers
        if marker.path in facts.marker_contents
        and marker.contains in facts.marker_contents[marker.path]
    )
    pattern_marker_hits = tuple(
        (marker, path)
        for marker in group.content_pattern_markers
        for path, content in facts.marker_contents.items()
        if PurePosixPath(path).match(marker.path_pattern) and marker.contains in content
    )
    markers_match = not group.content_markers or len(marker_hits) == len(group.content_markers)
    pattern_markers_match = all(
        any(hit_marker == marker for hit_marker, _ in pattern_marker_hits)
        for marker in group.content_pattern_markers
    )
    if not (files_all_match and files_any_match and patterns_all_match and patterns_any_match and markers_match and pattern_markers_match):
        return None

    evidence: set[str] = set()
    evidence.update(f"file:{path}" for path in group.files_all)
    evidence.update(f"file:{path}" for path in group.files_any if path in paths)
    for pattern, matched_paths in (*pattern_hits_all.items(), *pattern_hits_any.items()):
        evidence.update(f"pattern:{pattern}:{path}" for path in matched_paths)
    evidence.update(f"content:{marker.path}:{marker.contains}" for marker in marker_hits)
    evidence.update(
        f"content-pattern:{marker.path_pattern}:{path}:{marker.contains}"
        for marker, path in pattern_marker_hits
    )
    return tuple(sorted(evidence)[:MAX_DETECTION_EVIDENCE_PER_PLUGIN])


def _rule_evidence(descriptor: PluginDescriptor, facts: RepositoryFacts) -> tuple[str, ...] | None:
    rules = descriptor.detection
    extension_hits = tuple(
        path for path in facts.paths if PurePosixPath(path).suffix.lower() in rules.extensions
    )
    groups: list[DetectionAlternative] = []
    if rules.files_all or rules.files_any or rules.content_markers:
        groups.append(DetectionAlternative(
            files_all=rules.files_all,
            files_any=rules.files_any,
            content_markers=rules.content_markers,
        ))
    groups.extend(rules.alternatives)
    group_evidence = tuple(
        evidence
        for group in groups
        if (evidence := _group_evidence(group, facts)) is not None
    )

    if descriptor.kind is PluginKind.LANGUAGE:
        if not extension_hits and not group_evidence:
            return None
    elif descriptor.kind in {PluginKind.FRAMEWORK, PluginKind.DOMAIN} and not group_evidence:
        return None

    evidence = {f"extension:{path}" for path in extension_hits}
    for matched in group_evidence:
        evidence.update(matched)
    return tuple(sorted(evidence)[:MAX_DETECTION_EVIDENCE_PER_PLUGIN])


class ProjectSelector:
    def __init__(self, registry: PluginRegistry):
        self._registry = registry

    def select(self, facts: RepositoryFacts) -> ProjectCapabilities:
        selected: list[str] = []
        evidence: dict[str, tuple[str, ...]] = {}
        file_plugins: dict[str, tuple[str, ...]] = {}

        for descriptor in self._registry.descriptors:
            matched = _rule_evidence(descriptor, facts)
            if matched is None:
                continue
            if any(required not in selected for required in descriptor.requires):
                continue
            selected.append(descriptor.id)
            evidence[descriptor.id] = matched

        active_languages = [
            self._registry.descriptor(plugin_id)
            for plugin_id in selected
            if self._registry.descriptor(plugin_id).kind is PluginKind.LANGUAGE
        ]
        for path in facts.paths:
            extension = PurePosixPath(path).suffix.lower()
            matches = tuple(
                descriptor.id
                for descriptor in active_languages
                if extension in descriptor.detection.extensions
            )
            if matches:
                file_plugins[path] = matches

        fingerprint = self._fingerprint(
            facts.revision,
            tuple(selected),
            file_plugins,
            evidence,
        )
        return ProjectCapabilities(
            repository_plugins=tuple(selected),
            file_plugins=file_plugins,
            detection_evidence=evidence,
            unavailable_capabilities=(),
            fingerprint=fingerprint,
            descriptor_fingerprint=self._registry.fingerprint_for(selected),
        )

    def project(
        self,
        *,
        revision: str,
        repository_plugins: tuple[str, ...],
        file_plugins: Mapping[str, tuple[str, ...]],
        detection_evidence: Mapping[str, tuple[str, ...]],
        unavailable_capabilities: tuple[str, ...] = (),
    ) -> ProjectCapabilities:
        """Build and validate a capability projection from trusted evidence.

        This is used when a host already has revision-bound selection evidence
        from more than one deterministic source, for example the complete
        target-branch RAG index plus changed-file evidence from the PR host.
        It does not perform detection and cannot introduce a plugin outside the
        dependency-stable registry resolution.
        """
        resolved = tuple(
            descriptor.id
            for descriptor in self._registry.resolve(repository_plugins)
        )
        if resolved != repository_plugins:
            raise ValueError(
                "repository plugins are not in dependency-stable order"
            )
        capabilities = ProjectCapabilities(
            repository_plugins=resolved,
            file_plugins=file_plugins,
            detection_evidence=detection_evidence,
            unavailable_capabilities=unavailable_capabilities,
            fingerprint=self._fingerprint(
                revision,
                resolved,
                file_plugins,
                detection_evidence,
            ),
            descriptor_fingerprint=self._registry.fingerprint_for(resolved),
        )
        return self.validate(capabilities, revision)

    def validate(
        self,
        capabilities: ProjectCapabilities,
        revision: str,
    ) -> ProjectCapabilities:
        """Validate a capability projection produced by another runtime."""
        resolved = tuple(
            descriptor.id
            for descriptor in self._registry.resolve(
                capabilities.repository_plugins
            )
        )
        if resolved != capabilities.repository_plugins:
            raise ValueError(
                "repository plugins are not in dependency-stable order"
            )
        expected_descriptor = self._registry.fingerprint_for(resolved)
        if capabilities.descriptor_fingerprint != expected_descriptor:
            raise ValueError(
                "plugin descriptor fingerprint does not match this runtime"
            )

        selected = set(resolved)
        for path, plugin_ids in capabilities.file_plugins.items():
            unknown = sorted(set(plugin_ids) - selected)
            if unknown:
                raise ValueError(
                    f"file plugins for {path} are not selected repository "
                    f"plugins: {', '.join(unknown)}"
                )
            non_languages = sorted(
                plugin_id
                for plugin_id in plugin_ids
                if self._registry.descriptor(plugin_id).kind
                is not PluginKind.LANGUAGE
            )
            if non_languages:
                raise ValueError(
                    f"file plugins for {path} are not language plugins: "
                    + ", ".join(non_languages)
                )
        unknown_evidence = sorted(
            set(capabilities.detection_evidence) - selected
        )
        if unknown_evidence:
            raise ValueError(
                "detection evidence references unselected plugins: "
                + ", ".join(unknown_evidence)
            )
        missing_evidence = sorted(
            selected - set(capabilities.detection_evidence)
        )
        if missing_evidence:
            raise ValueError(
                "selected plugins are missing detection evidence: "
                + ", ".join(missing_evidence)
            )

        expected_selection = self._fingerprint(
            revision,
            resolved,
            capabilities.file_plugins,
            capabilities.detection_evidence,
        )
        if capabilities.fingerprint != expected_selection:
            raise ValueError(
                "project capability fingerprint does not match the immutable "
                "revision and plugin projection"
            )
        return capabilities

    def _fingerprint(
        self,
        revision: str,
        selected: tuple[str, ...],
        file_plugins: Mapping[str, tuple[str, ...]],
        evidence: Mapping[str, tuple[str, ...]],
    ) -> str:
        projection = {
            "revision": revision,
            "registry": self._registry.fingerprint,
            "repositoryPlugins": selected,
            "filePlugins": {
                path: tuple(plugin_ids)
                for path, plugin_ids in sorted(file_plugins.items())
            },
            "detectionEvidence": {
                plugin_id: tuple(values)
                for plugin_id, values in sorted(evidence.items())
            },
        }
        encoded = json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
