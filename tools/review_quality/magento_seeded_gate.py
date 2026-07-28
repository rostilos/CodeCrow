#!/usr/bin/env python3
"""Run the provider-free Magento candidate evidence/publication gate."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_CONTRACTS = PROJECT_ROOT / "analysis-plugins" / "contracts" / "python"
INFERENCE_SOURCE = (
    PROJECT_ROOT / "python-ecosystem" / "inference-orchestrator" / "src"
)
DEFAULT_CORPUS = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "magento_seeded_candidates.json"
)

for source_root in (str(PLUGIN_CONTRACTS), str(INFERENCE_SOURCE)):
    if source_root not in sys.path:
        sys.path.insert(0, source_root)

from codecrow_plugins import (  # noqa: E402
    FileArtifact,
    PluginCatalog,
    PluginRuntime,
    ProjectSelector,
    RepositoryFacts,
)
from model.dtos import ReviewRequestDto  # noqa: E402
from model.enrichment import FileContentDto, PrEnrichmentDataDto  # noqa: E402
from model.output_schemas import CodeReviewIssue  # noqa: E402
from model.plugins import ProjectCapabilitiesDto  # noqa: E402
from tools.review_quality.evaluation import evaluate_dataset  # noqa: E402


def _load_lightweight_host_module(name: str, relative_path: str):
    """Load a leaf host module without importing the eager service package."""
    source_path = INFERENCE_SOURCE / relative_path
    spec = importlib.util.spec_from_file_location(name, source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load host module {source_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_context_helpers = _load_lightweight_host_module(
    "_codecrow_quality_context_helpers",
    "service/review/orchestrator/context_helpers.py",
)
_plugin_context = _load_lightweight_host_module(
    "_codecrow_quality_plugin_context",
    "service/review/plugin_context.py",
)
rag_evidence_id = _context_helpers.rag_evidence_id
apply_plugin_validation_gate = _plugin_context.apply_plugin_validation_gate


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


def _load_corpus(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("corpus root must be an object")
    _non_empty_text(payload.get("corpusId"), "corpusId")
    artifacts = payload.get("artifacts")
    if (
        not isinstance(artifacts, dict)
        or not artifacts
        or any(
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or not isinstance(content, str)
            for path, content in artifacts.items()
        )
    ):
        raise ValueError("artifacts must map normalized relative paths to text")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a non-empty list")
    identities = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("each candidate must be an object")
        candidate_id = _non_empty_text(candidate.get("id"), "candidate.id")
        identities.append(candidate_id)
        if candidate.get("expected") not in {"publish", "withhold"}:
            raise ValueError(
                f"candidate {candidate_id!r} expected must be publish or withhold"
            )
        for field in ("file", "title", "reason", "fix"):
            _non_empty_text(candidate.get(field), f"candidate.{field}")
        _non_empty_text(candidate.get("claimKind"), "candidate.claimKind")
        selector = candidate.get("evidenceSelector")
        mode = candidate.get("evidenceMode")
        if (selector is None) == (mode is None):
            raise ValueError(
                f"candidate {candidate_id!r} needs exactly one evidence source"
            )
        if selector is not None:
            if not isinstance(selector, dict):
                raise ValueError("evidenceSelector must be an object")
            for field in ("kind", "source", "relation", "target"):
                _non_empty_text(
                    selector.get(field),
                    f"candidate.evidenceSelector.{field}",
                )
            attributes = selector.get("attributes", {})
            if (
                not isinstance(attributes, dict)
                or any(
                    not isinstance(key, str)
                    or not key
                    or not isinstance(value, str)
                    for key, value in attributes.items()
                )
            ):
                raise ValueError(
                    "candidate.evidenceSelector.attributes must map text to text"
                )
        elif mode != "invented":
            raise ValueError(
                f"candidate {candidate_id!r} has unsupported evidenceMode"
            )
    if len(identities) != len(set(identities)):
        raise ValueError("candidate IDs must be unique")
    targets = payload.get("qualityTargets")
    if not isinstance(targets, dict):
        raise ValueError("qualityTargets must be an object")
    for field in (
        "precision",
        "recall",
        "maxAddedModelCalls",
        "maxAddedCost",
        "maxPromptEstimatedInputTokens",
        "maxStage1EstimatedInputTokens",
    ):
        value = targets.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"qualityTargets.{field} must be numeric")
    return payload


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


def _select_fact(facts: tuple[Any, ...], selector: Mapping[str, Any]) -> Any:
    expected_attributes = selector.get("attributes", {})
    exact_fields = {
        field: value
        for field, value in selector.items()
        if field != "attributes"
    }
    matches = tuple(
        fact
        for fact in facts
        if all(
            getattr(fact, field) == value
            for field, value in exact_fields.items()
        )
        and all(
            dict(fact.attributes).get(key) == value
            for key, value in expected_attributes.items()
        )
    )
    if len(matches) != 1:
        raise ValueError(
            "evidence selector must resolve exactly once: "
            f"{dict(selector)!r} resolved {len(matches)} facts"
        )
    return matches[0]


def _evidence_record(fact: Any) -> tuple[str, dict[str, Any]]:
    payload = _fact_payload(fact)
    architecture_key = (
        f"{fact.kind}:{fact.source}:{fact.relation}:{fact.target}"
    )
    chunk = {
        "text": json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "_match_type": "plugin_graph",
        "metadata": {
            "path": fact.path,
            "architecture_key": architecture_key,
        },
    }
    return rag_evidence_id(chunk), payload


def _build_runtime(
    corpus: Mapping[str, Any],
    digest: str,
) -> tuple[Any, Any, tuple[Any, ...], tuple[Any, ...]]:
    artifacts = corpus["artifacts"]
    paths = tuple(sorted(artifacts))
    catalog = PluginCatalog.discover(PROJECT_ROOT / "analysis-plugins")
    capabilities = ProjectSelector(catalog.registry).select(
        RepositoryFacts(
            revision=digest[:40],
            paths=paths,
            marker_contents={
                "app/etc/config.php": artifacts["app/etc/config.php"],
                "composer.json": artifacts["composer.json"],
            },
        )
    )
    required = {"php", "magento", "hyva"}
    if not required <= set(capabilities.repository_plugins):
        raise RuntimeError(
            "fixture did not select PHP, Magento, and Hyva: "
            f"{capabilities.repository_plugins!r}"
        )
    runtime = PluginRuntime(catalog)
    handle = runtime.start_repository_analysis(
        capabilities,
        digest[:40],
    )
    if not handle.active:
        raise RuntimeError("fixture selected no repository analysis contributor")
    input_artifacts = tuple(
        FileArtifact(path, artifacts[path])
        for path in paths
    )
    handle.ingest(input_artifacts)
    analysis, diagnostics = handle.finish()
    if diagnostics:
        raise RuntimeError(
            "fixture repository analysis produced diagnostics: "
            + "; ".join(
                f"{item.plugin_id or 'plugin'}:{item.code}:{item.message}"
                for item in diagnostics
            )
        )
    facts = tuple(sorted({
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind.startswith("magento-")
    }))
    if not facts:
        raise RuntimeError("fixture produced no Magento graph facts")
    return catalog, capabilities, facts, analysis.snapshots


def _request(
    corpus: Mapping[str, Any],
    capabilities: Any,
    digest: str,
) -> ReviewRequestDto:
    artifacts = corpus["artifacts"]
    paths = tuple(sorted(artifacts))
    return ReviewRequestDto(
        projectId=0,
        projectVcsWorkspace="offline",
        projectVcsRepoSlug="magento-seeded-candidates",
        projectWorkspace="offline",
        projectNamespace="magento-seeded-candidates",
        aiProvider="PROVIDER_FREE",
        aiModel="no-model",
        aiApiKey="not-used",
        currentCommitHash=digest[:40],
        changedFiles=list(paths),
        projectCapabilities=ProjectCapabilitiesDto(
            repositoryPlugins=list(capabilities.repository_plugins),
            filePlugins={
                path: list(plugin_ids)
                for path, plugin_ids in capabilities.file_plugins.items()
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
        enrichmentData=PrEnrichmentDataDto(
            fileContents=[
                FileContentDto(
                    path=path,
                    content=artifacts[path],
                    sizeBytes=len(artifacts[path].encode("utf-8")),
                )
                for path in paths
            ]
        ),
    )


def run_gate(corpus_path: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    corpus = _load_corpus(corpus_path)
    digest = hashlib.sha256(_canonical_bytes(corpus)).hexdigest()
    _, capabilities, facts, snapshots = _build_runtime(corpus, digest)

    evidence_index: dict[str, list[dict[str, Any]]] = {}
    issues: list[CodeReviewIssue] = []
    for candidate in corpus["candidates"]:
        selector = candidate.get("evidenceSelector")
        if selector is not None:
            fact = _select_fact(facts, selector)
            evidence_ref, fact_payload = _evidence_record(fact)
            evidence_index.setdefault(evidence_ref, []).append(fact_payload)
            line = fact.line
        else:
            evidence_ref = "RAG-invented000000"
            line = 1
        issues.append(
            CodeReviewIssue(
                id=candidate["id"],
                severity="HIGH",
                category="BUG_RISK",
                file=candidate["file"],
                line=max(1, line),
                title=candidate["title"],
                reason=candidate["reason"],
                suggestedFixDescription=candidate["fix"],
                codeSnippet="<seeded-candidate>",
                evidenceRefs=[evidence_ref],
                claimKind=candidate["claimKind"],
            )
        )

    request = _request(corpus, capabilities, digest)
    gated = apply_plugin_validation_gate(
        issues,
        request,
        exact_evidence_by_id=evidence_index,
        deterministic_retrieval_states=["complete"],
    )
    fallback_published = [candidate["id"] for candidate in corpus["candidates"]]
    plugin_published = [issue.id for issue in gated if issue.id is not None]
    expected = [
        candidate["id"]
        for candidate in corpus["candidates"]
        if candidate["expected"] == "publish"
    ]
    plugin_abstained = sorted(set(fallback_published) - set(plugin_published))
    candidate_count = len(corpus["candidates"])
    common = {
        "caseId": corpus["corpusId"],
        "languages": ["php"],
        "frameworks": ["magento"],
        "expected": expected,
        "reviewableHunks": candidate_count,
        "terminalHunks": candidate_count,
        "changedLines": candidate_count,
        "modelCalls": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cost": 0,
    }
    evaluation = evaluate_dataset({
        "baseline": "fallback",
        "cases": [
            {
                **common,
                "mode": "fallback",
                "plugins": [],
                "published": fallback_published,
                "abstained": [],
            },
            {
                **common,
                "mode": "php-magento",
                "plugins": ["php", "magento"],
                "published": plugin_published,
                "abstained": plugin_abstained,
            },
        ],
    })
    metrics = {
        item.mode: item
        for item in evaluation.modes
    }
    plugin_metrics = metrics["php-magento"]
    delta = evaluation.paired_deltas[0]
    targets = corpus["qualityTargets"]
    checks = {
        "precision": plugin_metrics.precision >= float(targets["precision"]),
        "recall": plugin_metrics.recall >= float(targets["recall"]),
        "modelCalls": delta.model_calls <= int(targets["maxAddedModelCalls"]),
        "cost": delta.cost <= float(targets["maxAddedCost"]),
        "repositorySnapshots": {
            snapshot.plugin_id for snapshot in snapshots
        } >= {"php", "magento", "hyva"},
        "retrievalState": True,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "corpusId": corpus["corpusId"],
        "corpusSha256": digest,
        "scope": "seeded candidate evidence/publication; no LLM generation",
        "plugins": list(capabilities.repository_plugins),
        "architecture": {
            "magentoFacts": len(facts),
            "snapshotPlugins": sorted({
                snapshot.plugin_id for snapshot in snapshots
            }),
            "evidenceIds": len(evidence_index),
            "retrievalState": "complete",
        },
        "candidates": {
            "total": candidate_count,
            "typed": sum(bool(issue.claimKind) for issue in issues),
            "expectedPublished": len(expected),
            "pluginPublished": len(plugin_published),
            "pluginWithheld": len(plugin_abstained),
            "expectedPublishedIds": sorted(expected),
            "pluginPublishedIds": sorted(plugin_published),
            "pluginWithheldIds": plugin_abstained,
        },
        "targets": targets,
        "checks": checks,
        "evaluation": evaluation.as_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fixed, provider-free Magento candidate evidence gate."
        )
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        report = run_gate(arguments.corpus.resolve())
    except Exception as exception:
        report = {
            "status": "failed",
            "error": f"{type(exception).__name__}: {exception}",
        }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
