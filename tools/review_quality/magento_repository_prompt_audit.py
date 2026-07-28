#!/usr/bin/env python3
"""Run a provider-free prompt audit over an extracted Magento revision and diff."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_CONTRACTS = PROJECT_ROOT / "analysis-plugins" / "contracts" / "python"
INFERENCE_SOURCE = (
    PROJECT_ROOT / "python-ecosystem" / "inference-orchestrator" / "src"
)
for source_root in (
    str(INFERENCE_SOURCE),
    str(PLUGIN_CONTRACTS),
    str(PROJECT_ROOT),
):
    if source_root not in sys.path:
        sys.path.insert(0, source_root)

from tools.review_quality.prompt_gate_profile import (  # noqa: E402
    apply_fixed_prompt_gate_profile,
)

apply_fixed_prompt_gate_profile()

from codecrow_plugins import (  # noqa: E402
    FileArtifact,
    PluginCatalog,
    PluginRuntime,
    ProjectSelector,
    RepositoryFacts,
)
from model.dtos import ReviewRequestDto  # noqa: E402
from model.enrichment import FileContentDto, PrEnrichmentDataDto  # noqa: E402
from model.plugins import ProjectCapabilitiesDto  # noqa: E402
from service.review.prompt_dry_run import capture_review_prompts  # noqa: E402
from tools.review_quality.magento_prompt_gate import (  # noqa: E402
    FixtureGraphRagClient,
)
from tools.review_quality.validate_magento_architecture import (  # noqa: E402
    _candidate,
)
from utils.diff_processor import DiffChangeType, DiffProcessor  # noqa: E402


def _repository_paths(repository: Path) -> tuple[str, ...]:
    return tuple(sorted(
        path.relative_to(repository).as_posix()
        for path in repository.rglob("*")
        if path.is_file()
        and ".git" not in path.relative_to(repository).parts
    ))


def _fact_payload(fact: Any) -> dict[str, Any]:
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


def _fact_relationship(fact: Any) -> str:
    return f"[{fact.kind}] {fact.source} {fact.relation} {fact.target}"


def _touches(fact: Any, paths: set[str]) -> bool:
    return bool({fact.path, *fact.related_paths}.intersection(paths))


async def _capture(
    repository: Path,
    raw_diff: str,
    revision: str,
    max_file_bytes: int,
) -> dict[str, Any]:
    started = time.monotonic()
    all_paths = _repository_paths(repository)
    marker_contents = {}
    composer = repository / "composer.json"
    if composer.is_file():
        marker_contents["composer.json"] = composer.read_text(encoding="utf-8")

    catalog = PluginCatalog.discover(PROJECT_ROOT / "analysis-plugins")
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision=revision,
        paths=all_paths,
        marker_contents=marker_contents,
    ))
    if not {"php", "magento"} <= set(capabilities.repository_plugins):
        raise RuntimeError(
            "repository did not select PHP and Magento: "
            f"{capabilities.repository_plugins!r}"
        )

    processed = DiffProcessor().process(raw_diff)
    changed_paths = tuple(
        file.path for file in processed.files
        if file.change_type is not DiffChangeType.DELETED
        and not file.is_binary
    )
    deleted_paths = tuple(
        file.path for file in processed.files
        if file.change_type is DiffChangeType.DELETED
    )
    if not changed_paths:
        raise ValueError("diff contains no reviewable post-change files")
    missing_changed = sorted(
        path for path in changed_paths
        if not (repository / path).is_file()
    )
    if missing_changed:
        raise ValueError(
            "post-change files are absent from extracted revision: "
            + ", ".join(missing_changed)
        )

    runtime = PluginRuntime(catalog)
    handle = runtime.start_repository_analysis(capabilities, revision)
    ingested = 0
    skipped_large = []
    skipped_decode = []
    batch = []
    candidate_paths = [
        path for path in all_paths if _candidate(path)
    ]
    for relative in candidate_paths:
        if runtime.file_disposition(relative, capabilities).value == "excluded":
            continue
        source = repository / relative
        if source.stat().st_size > max_file_bytes:
            skipped_large.append(relative)
            continue
        try:
            content = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped_decode.append(relative)
            continue
        batch.append(FileArtifact(relative, content))
        ingested += 1
        if len(batch) == 100:
            handle.ingest(tuple(batch))
            batch.clear()
    if batch:
        handle.ingest(tuple(batch))
    analysis, diagnostics = handle.finish()
    if diagnostics:
        raise RuntimeError(
            "repository analysis produced diagnostics: "
            + "; ".join(
                f"{item.plugin_id or 'plugin'}:{item.code}:{item.message}"
                for item in diagnostics
            )
        )
    facts = {
        fact
        for packet in analysis.packets
        for fact in packet.facts
    }

    file_contents = []
    for path in changed_paths:
        content = (repository / path).read_text(encoding="utf-8")
        file_contents.append(FileContentDto(
            path=path,
            content=content,
            sizeBytes=len(content.encode("utf-8")),
        ))
        local_facts, local_diagnostics = runtime.graph_facts(
            FileArtifact(path, content),
            capabilities,
        )
        if local_diagnostics:
            raise RuntimeError(
                "changed-file graph produced diagnostics: "
                + "; ".join(
                    f"{item.plugin_id or 'plugin'}:{item.code}:{item.message}"
                    for item in local_diagnostics
                )
            )
        facts.update(local_facts)

    request = ReviewRequestDto(
        projectId=0,
        projectVcsWorkspace="offline",
        projectVcsRepoSlug=repository.name,
        projectWorkspace="offline",
        projectNamespace=repository.name,
        aiProvider="PROVIDER_FREE",
        aiModel="no-model",
        aiApiKey="not-used",
        analysisType="PULL_REQUEST",
        targetBranchName="base",
        sourceBranchName="head",
        pullRequestId=1,
        commitHash=revision,
        currentCommitHash=revision,
        baseCommitHash="0" * 40,
        changedFiles=list(changed_paths),
        deletedFiles=list(deleted_paths),
        rawDiff=raw_diff,
        enrichmentData=PrEnrichmentDataDto(fileContents=file_contents),
        projectCapabilities=ProjectCapabilitiesDto(
            repositoryPlugins=list(capabilities.repository_plugins),
            filePlugins={
                path: list(capabilities.file_plugins.get(path, ()))
                for path in changed_paths
            },
            detectionEvidence={
                plugin_id: list(evidence)
                for plugin_id, evidence
                in capabilities.detection_evidence.items()
            },
            unavailableCapabilities=list(
                capabilities.unavailable_capabilities
            ),
            fingerprint=capabilities.fingerprint,
            descriptorFingerprint=capabilities.descriptor_fingerprint,
        ),
    )
    touching_facts = tuple(sorted(
        fact for fact in facts
        if _touches(fact, set(changed_paths))
    ))
    repository_fact_kinds = Counter(fact.kind for fact in facts)
    generated_factory_facts = tuple(
        fact
        for fact in facts
        if fact.kind == "magento-generated-factory"
    )
    generated_factory_resolutions = tuple(
        fact
        for fact in facts
        if fact.kind == "magento-generated-factory-resolution"
    )
    generated_proxy_facts = tuple(
        fact
        for fact in facts
        if fact.kind == "magento-generated-proxy"
    )
    generated_proxy_resolutions = tuple(
        fact
        for fact in facts
        if fact.kind == "magento-generated-proxy-resolution"
    )
    rag = FixtureGraphRagClient(tuple(sorted(facts)))
    capture = await capture_review_prompts(
        request,
        rag,
        include_deterministic_rag=True,
        simulated_findings_per_file=0,
        full_pipeline_context=False,
    )
    capture["_audit"] = {
        "plugins": list(capabilities.repository_plugins),
        "changedPaths": list(changed_paths),
        "deletedPaths": list(deleted_paths),
        "repositoryPathCount": len(all_paths),
        "candidatePathCount": len(candidate_paths),
        "ingestedPathCount": ingested,
        "skippedLarge": skipped_large,
        "skippedDecode": skipped_decode,
        "packetCount": len(analysis.packets),
        "factCount": len(facts),
        "factKinds": dict(sorted(repository_fact_kinds.items())),
        "generatedFactories": {
            "dependencies": len(generated_factory_facts),
            "effectiveResolutions": len(
                generated_factory_resolutions
            ),
            "consumers": len({
                fact.source for fact in generated_factory_facts
            }),
            "requestedKinds": dict(sorted(Counter(
                dict(fact.attributes).get("requestedKind", "unknown")
                for fact in generated_factory_facts
            ).items())),
            "resolutionAreas": dict(sorted(Counter(
                dict(fact.attributes).get("area", "unknown")
                for fact in generated_factory_resolutions
            ).items())),
        },
        "generatedProxies": {
            "dependencies": len(generated_proxy_facts),
            "effectiveResolutions": len(
                generated_proxy_resolutions
            ),
            "owners": len({
                fact.source for fact in generated_proxy_facts
            }),
            "proxyTypes": len({
                dict(fact.attributes).get("proxyType", "")
                for fact in generated_proxy_facts
            }),
            "requestedKinds": dict(sorted(Counter(
                dict(fact.attributes).get("requestedKind", "unknown")
                for fact in generated_proxy_facts
            ).items())),
            "resolutionAreas": dict(sorted(Counter(
                dict(fact.attributes).get("area", "unknown")
                for fact in generated_proxy_resolutions
            ).items())),
        },
        "touchingFacts": touching_facts,
        "retrievalRequests": rag.requests,
        "returnedFactCount": rag.returned_fact_count,
        "elapsedSeconds": round(time.monotonic() - started, 3),
    }
    return capture


def run_audit(
    repository: Path,
    diff_path: Path,
    *,
    revision: str,
    max_file_bytes: int = 1_048_576,
) -> dict[str, Any]:
    repository = repository.resolve()
    if not repository.is_dir():
        raise ValueError(f"repository does not exist: {repository}")
    raw_diff = diff_path.read_text(encoding="utf-8")
    capture = asyncio.run(_capture(
        repository,
        raw_diff,
        revision,
        max_file_bytes,
    ))
    audit = capture.pop("_audit")
    stage_1_prompts = [
        prompt["renderedPrompt"]
        for prompt in capture["prompts"]
        if prompt["stage"] == "stage_1"
    ]
    rendered = "\n".join(stage_1_prompts)
    touching_facts = audit.pop("touchingFacts")
    visible_facts = [
        fact for fact in touching_facts
        if _fact_relationship(fact) in rendered
    ]
    missing_facts = [
        _fact_payload(fact) for fact in touching_facts
        if _fact_relationship(fact) not in rendered
    ]
    missing_changed_paths = [
        path for path in audit["changedPaths"]
        if path not in rendered
    ]
    quality = capture["qualitySignals"]["stage1"]
    fact_kind_counts = Counter(fact.kind for fact in touching_facts)
    visible_kind_counts = Counter(fact.kind for fact in visible_facts)
    coverage = (
        len(visible_facts) / len(touching_facts)
        if touching_facts else 1.0
    )
    checks = {
        "providerCalls": capture["providerCalls"] == 0,
        "phpMagentoSelected": (
            {"php", "magento"} <= set(audit["plugins"])
        ),
        "allChangedPathsReviewed": not missing_changed_paths,
        "deterministicFactsRetrieved": (
            not touching_facts or audit["returnedFactCount"] > 0
        ),
        "touchingFactCoverage": coverage >= 0.90,
        "noHiddenPluginEvidenceTargets": (
            quality["hiddenPluginEvidenceTargets"] == 0
        ),
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "repository": str(repository),
        "revision": revision,
        "diffSha256": hashlib.sha256(
            raw_diff.encode("utf-8")
        ).hexdigest(),
        "checks": checks,
        "plugins": audit.pop("plugins"),
        "repositoryAnalysis": audit,
        "contextCoverage": {
            "touchingFactCount": len(touching_facts),
            "visibleFactCount": len(visible_facts),
            "ratio": coverage,
            "factKinds": dict(sorted(fact_kind_counts.items())),
            "visibleFactKinds": dict(sorted(visible_kind_counts.items())),
            "missingFacts": missing_facts,
            "missingChangedPaths": missing_changed_paths,
        },
        "prompt": {
            "count": capture["promptCount"],
            "countsByStage": capture["promptCountsByStage"],
            "estimatedTotalInputTokens": capture["estimatedTotalInputTokens"],
            "qualitySignals": capture["qualitySignals"],
        },
        "providerCalls": capture["providerCalls"],
        "limitations": [
            (
                "The audit uses exact plugin-produced facts and the production "
                "prompt assembler, but does not execute semantic embedding search "
                "or review-model candidate generation."
            ),
            (
                "A passing context-delivery audit is not a precision, recall, "
                "latency, or production-cost claim."
            ),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", type=Path)
    parser.add_argument("--diff", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--max-file-bytes", type=int, default=1_048_576)
    arguments = parser.parse_args()
    try:
        report = run_audit(
            arguments.repository,
            arguments.diff,
            revision=arguments.revision,
            max_file_bytes=arguments.max_file_bytes,
        )
    except Exception as exception:
        report = {
            "status": "failed",
            "error": f"{type(exception).__name__}: {exception}",
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
