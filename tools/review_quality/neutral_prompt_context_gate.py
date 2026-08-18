#!/usr/bin/env python3
"""Provider-free gate for neutral plugin graph-to-prompt context delivery."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = PROJECT_ROOT / "analysis-plugins"
PLUGIN_CONTRACTS = PLUGIN_ROOT / "contracts" / "python"
INFERENCE_SOURCE = (
    PROJECT_ROOT / "python-ecosystem" / "inference-orchestrator" / "src"
)

# Prompt assembly and repository analysis must load the same implementation
# tree. This gate never discovers an image-baked plugin bundle.
os.environ["CODECROW_PLUGINS_ROOT"] = str(PLUGIN_ROOT)

for source_root in (
    str(INFERENCE_SOURCE),
    str(PLUGIN_CONTRACTS),
    str(PROJECT_ROOT),
):
    if source_root not in sys.path:
        sys.path.insert(0, source_root)

from tools.review_quality.prompt_gate_profile import (  # noqa: E402
    apply_fixed_prompt_gate_profile,
    stable_prompt_digest,
    stable_prompt_record_digests,
)

apply_fixed_prompt_gate_profile()

from codecrow_plugins import (  # noqa: E402
    FileArtifact,
    PluginCatalog,
    PluginRuntime,
    ProjectSelector,
    RepositoryAnalysisMode,
    RepositoryFacts,
)
from model.dtos import ReviewRequestDto  # noqa: E402
from model.enrichment import FileContentDto, PrEnrichmentDataDto  # noqa: E402
from model.plugins import ProjectCapabilitiesDto  # noqa: E402
from service.review.plugin_context import _plugin_host  # noqa: E402
from service.review.prompt_dry_run import capture_review_prompts  # noqa: E402
from tools.review_quality.neutral_corpus import (  # noqa: E402
    CASE_DEFINITIONS,
    NeutralCaseDefinition,
    definition_digest,
)


MAX_CASE_INPUT_TOKENS = 60_000
MAX_STAGE1_INPUT_TOKENS = 25_000
MAX_PLUGIN_CONTEXT_SHARE = 0.15


def _revision(prefix: str, digest: str) -> str:
    return hashlib.sha256(f"{prefix}\0{digest}".encode("utf-8")).hexdigest()[:40]


def _head_files(definition: NeutralCaseDefinition) -> dict[str, str]:
    files = dict(definition.base_files)
    files.update(definition.head_replacements)
    return dict(sorted(files.items()))


def _raw_diff(definition: NeutralCaseDefinition) -> str:
    sections: list[str] = []
    for path, head in sorted(definition.head_replacements.items()):
        base = definition.base_files[path]
        unified = "".join(difflib.unified_diff(
            base.splitlines(keepends=True),
            head.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=80,
        ))
        sections.append(
            f"diff --git a/{path} b/{path}\n"
            "index 1111111..2222222 100644\n"
            f"{unified.rstrip()}"
        )
    return "\n".join(sections) + "\n"


def _capabilities_dto(capabilities) -> ProjectCapabilitiesDto:
    return ProjectCapabilitiesDto(
        repositoryPlugins=list(capabilities.repository_plugins),
        filePlugins={
            path: list(plugin_ids)
            for path, plugin_ids in capabilities.file_plugins.items()
        },
        detectionEvidence={
            plugin_id: list(values)
            for plugin_id, values in capabilities.detection_evidence.items()
        },
        unavailableCapabilities=list(
            capabilities.unavailable_capabilities
        ),
        fingerprint=capabilities.fingerprint,
        descriptorFingerprint=capabilities.descriptor_fingerprint,
    )


def _request(
    definition: NeutralCaseDefinition,
    capabilities,
    digest: str,
) -> ReviewRequestDto:
    head = _head_files(definition)
    changed = tuple(sorted(definition.head_replacements))
    return ReviewRequestDto(
        projectId=99001,
        projectVcsWorkspace="disconnected-quality-gate",
        projectVcsRepoSlug=definition.case_id,
        projectWorkspace="disconnected-quality-gate",
        projectNamespace=definition.case_id,
        aiProvider="OPENAI",
        aiModel="provider-disabled-neutral-gate",
        aiApiKey="provider-disabled-neutral-gate-key",
        analysisType="PULL_REQUEST",
        targetBranchName="main",
        sourceBranchName=f"fixture/{definition.case_id}",
        pullRequestId=1,
        currentCommitHash=_revision("head", digest),
        commitHash=_revision("head", digest),
        baseCommitHash=_revision("base", digest),
        # The deterministic adapter models a PR overlay on an immutable base
        # generation. Supply the same complete base binding production sends;
        # the dry-run facade then returns the matching PR-generation receipts.
        # Without these coordinates Stage 1 correctly treats the request as an
        # unbound legacy review and must not query PR-scoped vectors.
        ragCollectionTarget=f"neutral_{digest}_main_generation",
        ragBaseGenerationManifestSha256=hashlib.sha256(
            f"base-generation\0{digest}".encode("utf-8")
        ).hexdigest(),
        changedFiles=list(changed),
        rawDiff=_raw_diff(definition),
        prTitle=f"Provider-free neutral prompt gate: {definition.case_id}",
        enrichmentData=PrEnrichmentDataDto(fileContents=[
            FileContentDto(
                path=path,
                content=head[path],
                sizeBytes=len(head[path].encode("utf-8")),
            )
            for path in changed
        ]),
        projectCapabilities=_capabilities_dto(capabilities),
    )


def _fact_payload(fact) -> dict[str, Any]:
    return {
        "kind": fact.kind,
        "source": fact.source,
        "relation": fact.relation,
        "target": fact.target,
        "path": fact.path,
        "line": fact.line,
        "attributes": dict(fact.attributes),
        "related_paths": list(fact.related_paths),
    }


def _architecture_text(packet) -> str:
    lines = [
        "Deterministic repository architecture context",
        f"Plugin: {packet.plugin_id}",
        f"Kind: {packet.kind}",
        f"Source: {packet.key}",
        "Facts:",
    ]
    for fact in packet.facts:
        attributes = dict(fact.attributes)
        attribute_text = (
            " {" + ", ".join(
                f"{key}={attributes[key]}" for key in sorted(attributes)
            ) + "}"
            if attributes
            else ""
        )
        lines.append(
            f"- [{fact.kind}] {fact.source} {fact.relation} {fact.target} "
            f"({fact.path}:{fact.line}){attribute_text}"
        )
    return "\n".join(lines)


class ExactFixtureRag:
    """Production-shaped exact adapter over real base and PR plugin packets."""

    def __init__(
        self,
        *,
        base_packets: Sequence[Any],
        overlay_packets: Sequence[Any],
        head_files: Mapping[str, str],
        changed_paths: Sequence[str],
    ):
        self._packets = (
            *((packet, False) for packet in base_packets),
            *((packet, True) for packet in overlay_packets),
        )
        self._head_files = dict(head_files)
        self._changed_paths = frozenset(changed_paths)
        self.requests: list[tuple[str, ...]] = []
        self.returned_facts = 0
        self.returned_pr_facts = 0

    async def get_deterministic_context(
        self,
        *,
        file_paths: Sequence[str],
        pr_number: int | None = None,
        pr_changed_files: Sequence[str] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        requested = {
            str(path).lstrip("/").replace("\\", "/")
            for path in file_paths
            if path
        }
        if pr_number != 1:
            raise RuntimeError("neutral prompt gate lost the PR overlay identity")
        if set(pr_changed_files or ()) != self._changed_paths:
            raise RuntimeError(
                "neutral prompt gate lost the complete changed-file identity"
            )
        self.requests.append(tuple(sorted(requested)))

        selected_packets: list[tuple[Any, bool]] = []
        selected_indexes: set[int] = set()
        reachable_paths = set(requested)
        changed = True
        while changed:
            changed = False
            for index, (packet, is_pr) in enumerate(self._packets):
                if index in selected_indexes:
                    continue
                packet_paths = set(packet.paths)
                if not packet_paths.intersection(reachable_paths):
                    continue
                selected_indexes.add(index)
                selected_packets.append((packet, is_pr))
                reachable_paths.update(packet_paths)
                changed = True

        chunks: list[dict[str, Any]] = []
        related_paths: set[str] = set()
        for packet, is_pr in selected_packets:
            packet_paths = set(packet.paths)
            matched = sorted(packet_paths.intersection(requested))
            if not matched:
                matched = sorted(packet_paths.intersection(reachable_paths))
            related_paths.update(packet_paths - requested)
            identity = (
                f"{packet.plugin_id}\0{packet.kind}\0{packet.key}\0"
                f"{'pr' if is_pr else 'base'}"
            )
            payloads = [_fact_payload(fact) for fact in packet.facts]
            chunks.append({
                "text": _architecture_text(packet),
                "score": 1.0,
                "_source": "pr_indexed" if is_pr else "deterministic",
                "_match_type": "architecture_relation",
                "_matched_on": ",".join(matched),
                "metadata": {
                    "path": (
                        "__analysis_architecture__/neutral/"
                        + hashlib.sha256(identity.encode("utf-8")).hexdigest()
                        + ".context"
                    ),
                    "pr": is_pr,
                    "architecture_plugin": packet.plugin_id,
                    "architecture_kind": packet.kind,
                    "architecture_key": packet.key,
                    "architecture_paths": list(packet.paths),
                    "plugin_graph_facts": payloads,
                },
            })
            self.returned_facts += len(payloads)
            if is_pr:
                self.returned_pr_facts += len(payloads)

        for path in sorted(requested | related_paths):
            content = self._head_files.get(path)
            if content is None:
                continue
            is_pr = path in self._changed_paths
            chunks.append({
                "text": content,
                "score": 1.0,
                "_source": "pr_indexed" if is_pr else "deterministic",
                "_match_type": (
                    "changed_file" if path in requested
                    else "architecture_related"
                ),
                "_matched_on": path,
                "metadata": {
                    "path": path,
                    "pr": is_pr,
                    "content_state": "complete",
                },
            })

        return {
            "context": {
                "chunks": chunks,
                "changed_files": {},
                "related_definitions": {},
                "_metadata": {
                    "retrieval_state": "complete",
                    "failures": [],
                },
            }
        }


def _repository_evidence(definition: NeutralCaseDefinition):
    digest = definition_digest(definition)
    head_files = _head_files(definition)
    catalog = PluginCatalog.discover(PLUGIN_ROOT)
    selector = ProjectSelector(catalog.registry)
    capabilities = selector.select(RepositoryFacts(
        revision=_revision("head", digest),
        paths=tuple(sorted(head_files)),
    ))
    runtime = PluginRuntime(catalog)

    base_handle = runtime.start_repository_analysis(
        capabilities,
        _revision("base", digest),
    )
    base_handle.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(definition.base_files.items())
    ))
    base_analysis, base_diagnostics = base_handle.finish()

    overlay_handle = runtime.start_repository_analysis(
        capabilities,
        _revision("head", digest),
        snapshots=base_analysis.snapshots,
        mode=RepositoryAnalysisMode.PR_OVERLAY,
    )
    overlay_handle.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(definition.head_replacements.items())
    ))
    overlay_analysis, overlay_diagnostics = overlay_handle.finish()
    changed = set(definition.head_replacements)
    overlay_packets = tuple(
        packet
        for packet in overlay_analysis.packets
        if changed.intersection(packet.paths)
    )
    diagnostics = (*base_diagnostics, *overlay_diagnostics)
    return (
        digest,
        capabilities,
        base_analysis.packets,
        overlay_packets,
        diagnostics,
        head_files,
    )


def _evidence_sentinel(content: str) -> str:
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip()
    ]
    if not lines:
        return ""
    return max(lines, key=lambda line: (len(line), line))


def _owner_prompt(prompts: Sequence[Mapping[str, Any]], path: str) -> list[str]:
    marker = re.compile(
        rf"^FILE #\d+:\s*{re.escape(path)}(?:\s|\(|$)",
        re.MULTILINE,
    )
    return [
        str(prompt["renderedPrompt"])
        for prompt in prompts
        if prompt.get("stage") == "stage_1"
        and marker.search(str(prompt.get("renderedPrompt") or ""))
    ]


async def _capture_case(definition: NeutralCaseDefinition) -> dict[str, Any]:
    (
        digest,
        capabilities,
        base_packets,
        overlay_packets,
        repository_diagnostics,
        head_files,
    ) = _repository_evidence(definition)
    if repository_diagnostics:
        raise RuntimeError(
            "neutral repository analysis produced diagnostics: "
            + "; ".join(
                f"{item.plugin_id or 'plugin'}:{item.code}:{item.message}"
                for item in repository_diagnostics
            )
        )

    rag = ExactFixtureRag(
        base_packets=base_packets,
        overlay_packets=overlay_packets,
        head_files=head_files,
        changed_paths=tuple(definition.head_replacements),
    )
    capture = await capture_review_prompts(
        _request(definition, capabilities, digest),
        rag,
        include_deterministic_rag=True,
        simulated_findings_per_file=0,
        full_pipeline_context=False,
    )
    stage1 = [
        prompt
        for prompt in capture["prompts"]
        if prompt["stage"] == "stage_1"
    ]
    missing_evidence: dict[str, list[str]] = {}
    owner_counts: dict[str, int] = {}
    removed_relation_missing: list[str] = []
    for defect in definition.expected_defects:
        owners = _owner_prompt(stage1, defect.file)
        owner_counts[defect.id] = len(owners)
        if len(owners) != 1:
            missing_evidence[defect.id] = list(defect.evidence_files)
            continue
        rendered = owners[0]
        missing = [
            path
            for path in defect.evidence_files
            if path not in rendered
            or _evidence_sentinel(head_files[path]) not in rendered
        ]
        if missing:
            missing_evidence[defect.id] = missing
        language = defect.file.rsplit(".", 1)[-1].casefold()
        plugin = {
            "py": "python",
            "java": "java",
            "ts": "typescript",
            "mts": "typescript",
            "cts": "typescript",
        }.get(language)
        if (
            plugin
            and f"[{plugin}-pr-removed-relation]" not in rendered
            and "[data-contract-pr-removed-reference]" not in rendered
        ):
            removed_relation_missing.append(defect.id)

    quality = capture["qualitySignals"]["stage1"]
    selected = tuple(capabilities.repository_plugins)
    expected = tuple(definition.candidate_plugins)
    checks = {
        "selectedPluginsExact": selected == expected,
        "providerCallsZero": capture["providerCalls"] == 0,
        "pipelineCompleted": capture["pipeline"]["completed"] is True,
        "pluginDiagnosticsZero": capture["pluginDiagnostics"]["count"] == 0,
        "deterministicRetrievalUsed": bool(rag.requests),
        "prFactsReturned": rag.returned_pr_facts > 0,
        "singleStage1OwnerPerDefect": all(
            count == 1 for count in owner_counts.values()
        ),
        "expectedEvidenceVisible": not missing_evidence,
        "removedRelationVisible": not removed_relation_missing,
        "noRagTruncation": quality["ragContextTruncationMarkers"] == 0,
        "noCurrentSourceTruncation": (
            quality["currentSourceTruncationMarkers"] == 0
        ),
        "caseInputTokenCeiling": (
            capture["estimatedTotalInputTokens"] <= MAX_CASE_INPUT_TOKENS
        ),
        "stage1InputTokenCeiling": (
            quality["maxEstimatedInputTokens"] <= MAX_STAGE1_INPUT_TOKENS
        ),
        "pluginContextShareCeiling": (
            quality["pluginContextShare"] <= MAX_PLUGIN_CONTEXT_SHARE
        ),
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "caseId": definition.case_id,
        "definitionSha256": digest,
        "checks": checks,
        "plugins": list(selected),
        "repository": {
            "basePacketCount": len(base_packets),
            "overlayPacketCount": len(overlay_packets),
            "overlayFactKinds": dict(sorted(Counter(
                fact.kind
                for packet in overlay_packets
                for fact in packet.facts
            ).items())),
            "returnedFactCount": rag.returned_facts,
            "returnedPrFactCount": rag.returned_pr_facts,
            "retrievalRequestCount": len(rag.requests),
        },
        "prompt": {
            "digest": stable_prompt_digest(capture["prompts"]),
            "recordDigests": stable_prompt_record_digests(
                capture["prompts"]
            ),
            "count": capture["promptCount"],
            "countsByStage": capture["promptCountsByStage"],
            "estimatedTotalInputTokens": capture[
                "estimatedTotalInputTokens"
            ],
            "stage1QualitySignals": quality,
        },
        "ownerCounts": owner_counts,
        "missingEvidence": missing_evidence,
        "missingRemovedRelation": removed_relation_missing,
        "providerCalls": capture["providerCalls"],
    }


async def _run(case_ids: Sequence[str]) -> dict[str, Any]:
    _plugin_host.cache_clear()
    cases = [
        await _capture_case(CASE_DEFINITIONS[case_id])
        for case_id in case_ids
    ]
    checks = {
        "allCasesPassed": all(case["status"] == "passed" for case in cases),
        "allProviderCallsZero": all(
            case["providerCalls"] == 0 for case in cases
        ),
        "caseSetExact": tuple(case_ids) == tuple(sorted(CASE_DEFINITIONS)),
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "qualityTargets": {
            "maxCaseEstimatedInputTokens": MAX_CASE_INPUT_TOKENS,
            "maxStage1EstimatedInputTokens": MAX_STAGE1_INPUT_TOKENS,
            "maxPluginContextShare": MAX_PLUGIN_CONTEXT_SHARE,
        },
        "cases": cases,
    }


def run_gate(case_ids: Sequence[str] | None = None) -> dict[str, Any]:
    selected = (
        tuple(sorted(CASE_DEFINITIONS))
        if case_ids is None
        else tuple(case_ids)
    )
    unknown = sorted(set(selected) - set(CASE_DEFINITIONS))
    if unknown:
        raise ValueError("unknown neutral case(s): " + ", ".join(unknown))
    return asyncio.run(_run(selected))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        action="append",
        choices=tuple(sorted(CASE_DEFINITIONS)),
    )
    arguments = parser.parse_args()
    try:
        report = run_gate(arguments.case)
    except Exception as exception:
        report = {
            "status": "failed",
            "error": f"{type(exception).__name__}: {exception}",
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
