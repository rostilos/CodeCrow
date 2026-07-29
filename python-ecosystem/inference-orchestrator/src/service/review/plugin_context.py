from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence

from model.dtos import ReviewRequestDto
from model.plugins import ProjectCapabilitiesDto
from service.review.candidate_ledger import CandidateEvidenceLedger

logger = logging.getLogger(__name__)
PLUGIN_CONTEXT_CHAR_BUDGET = 6_000
_PLUGIN_DIAGNOSTIC_SINK: ContextVar[
    Optional[Callable[[dict[str, str]], None]]
] = ContextVar("codecrow_plugin_diagnostic_sink", default=None)


@contextmanager
def capture_plugin_diagnostics(
    sink: Callable[[dict[str, str]], None],
) -> Iterator[None]:
    """Capture diagnostics for one async review without affecting peers."""
    token = _PLUGIN_DIAGNOSTIC_SINK.set(sink)
    try:
        yield
    finally:
        _PLUGIN_DIAGNOSTIC_SINK.reset(token)


def _log_plugin_diagnostics(scope: str, diagnostics: tuple[Any, ...]) -> None:
    if not diagnostics:
        return
    records = [
        {
            "scope": scope,
            "pluginId": str(item.plugin_id or ""),
            "code": str(item.code or ""),
            "message": str(item.message or ""),
        }
        for item in diagnostics
    ]
    sink = _PLUGIN_DIAGNOSTIC_SINK.get()
    if sink is not None:
        for record in records:
            sink(record)
    logger.warning(
        "Plugin %s diagnostics: %s",
        scope,
        records,
    )


def _require_complete_plugin_contribution(
    scope: str,
    diagnostics: tuple[Any, ...],
) -> None:
    """Stop prompt construction when selected plugin context is incomplete."""
    if not diagnostics:
        return
    _log_plugin_diagnostics(scope, diagnostics)
    summary = "; ".join(
        f"{item.plugin_id or 'plugin'}:{item.code}: {item.message}"
        for item in diagnostics[:10]
    )
    raise RuntimeError(f"Plugin {scope} contribution is incomplete: {summary}")


def _bounded_lines(lines: list[str], max_chars: int) -> str:
    rendered: list[str] = []
    used = 0
    omitted = 0
    for index, line in enumerate(lines):
        added = len(line) + (1 if rendered else 0)
        if used + added > max_chars:
            omitted = len(lines) - index
            break
        rendered.append(line)
        used += added

    if omitted:
        summary = f"[{omitted} plugin evidence line(s) omitted by prompt budget]"
        while rendered and used + 1 + len(summary) > max_chars:
            removed = rendered.pop()
            used -= len(removed) + (1 if rendered else 0)
            omitted += 1
            summary = (
                f"[{omitted} plugin evidence line(s) omitted by prompt budget]"
            )
        if len(summary) <= max_chars:
            rendered.append(summary)
    return "\n".join(rendered)


@lru_cache(maxsize=1)
def _plugin_host():
    try:
        from codecrow_plugins import PluginRuntime, ProjectSelector
        from codecrow_plugins.bootstrap import discover_builtin_plugins
    except ModuleNotFoundError as exception:
        if exception.name != "codecrow_plugins":
            raise
        monorepo_package = (
            Path(__file__).resolve().parents[5]
            / "analysis-plugins"
            / "contracts"
            / "python"
        )
        if not monorepo_package.is_dir():
            return None
        sys.path.insert(0, str(monorepo_package))
        from codecrow_plugins import PluginRuntime, ProjectSelector
        from codecrow_plugins.bootstrap import discover_builtin_plugins

    catalog = discover_builtin_plugins()
    return catalog, PluginRuntime(catalog), ProjectSelector(catalog.registry)


def _request_revision(request: ReviewRequestDto) -> str:
    revision = next(
        (
            value.strip()
            for value in (
                getattr(request, "currentCommitHash", None),
                getattr(request, "commitHash", None),
            )
            if isinstance(value, str) and value.strip()
        ),
        None,
    )
    if revision is None:
        raise ValueError(
            "plugin capability validation requires an immutable review revision"
        )
    return revision


def resolve_project_capabilities(request: ReviewRequestDto):
    host = _plugin_host()
    if host is None:
        return None
    catalog, _, selector = host
    payload = getattr(request, "projectCapabilities", None)
    if isinstance(payload, ProjectCapabilitiesDto):
        requested = tuple(payload.repositoryPlugins)
        resolved = tuple(descriptor.id for descriptor in catalog.registry.resolve(requested))
        if resolved != requested:
            raise ValueError("project capability plugins are not in dependency-stable order")
        return selector.project(
            revision=_request_revision(request),
            repository_plugins=requested,
            file_plugins={
                path: tuple(ids) for path, ids in payload.filePlugins.items()
            },
            detection_evidence={
                plugin_id: tuple(values)
                for plugin_id, values in payload.detectionEvidence.items()
            },
            unavailable_capabilities=tuple(payload.unavailableCapabilities),
        )

    # Transitional fallback: select only from immutable request evidence. It is
    # intentionally conservative when Java did not supply repository markers.
    from codecrow_plugins import RepositoryFacts

    paths = {path.lstrip("/") for path in request.changedFiles or []}
    marker_contents: dict[str, str] = {}
    enrichment = getattr(request, "enrichmentData", None)
    for file_content in getattr(enrichment, "fileContents", None) or []:
        normalized = file_content.path.lstrip("/")
        paths.add(normalized)
        if normalized == "composer.json" and file_content.content and not file_content.skipped:
            marker_contents[normalized] = file_content.content
    if not paths:
        return None
    revision = _request_revision(request)
    return selector.select(RepositoryFacts(
        revision=revision,
        paths=tuple(sorted(paths)),
        marker_contents=marker_contents,
    ))


def apply_effective_project_capabilities(
    request: ReviewRequestDto,
    payload: object,
):
    """Accept the complete-repository plugin projection attested by RAG.

    Java contributes immutable changed-file/marker evidence. RAG contributes
    the selected plugin set from the complete indexed target repository.
    Fingerprints in the RAG response are provenance only. The local catalog
    validates structural plugin order and projection membership.
    """
    requested = tuple(
        request.projectCapabilities.repositoryPlugins
        if request.projectCapabilities is not None
        else ()
    )
    if payload is None:
        if requested:
            raise ValueError(
                "RAG omitted effective capabilities for a plugin-enabled review"
            )
        return None
    if not isinstance(payload, Mapping):
        raise ValueError("RAG effective capabilities must be an object")

    allowed = {
        "repositoryPlugins",
        "filePlugins",
        "detectionEvidence",
        "unavailableCapabilities",
        "fingerprint",
        "descriptorFingerprint",
        "implementationFingerprint",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(
            "RAG effective capabilities contain unknown fields: "
            + ", ".join(unknown)
        )
    dto = ProjectCapabilitiesDto.model_validate({
        key: value
        for key, value in payload.items()
        if key != "implementationFingerprint"
    })
    host = _plugin_host()
    if host is None:
        if dto.repositoryPlugins:
            raise ValueError(
                "RAG selected plugins but the inference plugin runtime is unavailable"
            )
        request.projectCapabilities = dto
        return None
    catalog, _, _ = host
    effective = tuple(dto.repositoryPlugins)
    resolved = tuple(
        descriptor.id
        for descriptor in catalog.registry.resolve(effective)
    )
    if resolved != effective:
        raise ValueError(
            "RAG effective plugins are not in dependency-stable order"
        )
    missing_requested = sorted(set(requested) - set(effective))
    if missing_requested:
        raise ValueError(
            "RAG effective capabilities removed request-selected plugins: "
            + ", ".join(missing_requested)
        )
    request.projectCapabilities = dto
    return resolve_project_capabilities(request)


def apply_plugin_file_policy(
    request: ReviewRequestDto,
    processed_diff: Any,
) -> Any:
    """Apply selected neutral file-policy contributions to the hunk manifest."""
    if processed_diff is None:
        return None
    host = _plugin_host()
    capabilities = resolve_project_capabilities(request)
    if host is None or capabilities is None:
        return processed_diff

    from codecrow_plugins import FileDisposition
    from utils.diff_processor import HunkDisposition

    _, runtime, _ = host
    newly_skipped = 0
    for diff_file in processed_diff.files:
        disposition = runtime.file_disposition(
            diff_file.path.lstrip("/"),
            capabilities,
        )
        diff_file.plugin_disposition = disposition.value
        if disposition not in {
            FileDisposition.EXCLUDED,
            FileDisposition.GENERATED,
        }:
            continue
        if diff_file.is_skipped:
            continue

        diff_file.is_skipped = True
        diff_file.skip_reason = f"Plugin file policy: {disposition.value}"
        hunk_disposition = (
            HunkDisposition.GENERATED
            if disposition is FileDisposition.GENERATED
            else HunkDisposition.EXCLUDED
        )
        diff_file.hunks = [
            replace(hunk, disposition=hunk_disposition)
            if hunk.disposition is HunkDisposition.REVIEWABLE
            else hunk
            for hunk in diff_file.hunks
        ]
        newly_skipped += 1

    if newly_skipped:
        included = processed_diff.get_included_files()
        processed_diff.total_additions = sum(
            item.additions for item in included
        )
        processed_diff.total_deletions = sum(
            item.deletions for item in included
        )
        processed_diff.total_files = len(included)
        processed_diff.skipped_files = len(processed_diff.get_skipped_files())
        processed_diff.processed_size_bytes = sum(
            item.size_bytes for item in included
        )
        logger.info(
            "Plugin file policy excluded %d changed file(s) from direct review",
            newly_skipped,
        )
    return processed_diff


def review_plugin_context(
    request: ReviewRequestDto,
    paths: list[str],
    *,
    include_evidence_targets: bool = True,
    visible_evidence_by_id: Optional[
        Mapping[str, Sequence[Mapping[str, Any]]]
    ] = None,
) -> str:
    host = _plugin_host()
    capabilities = resolve_project_capabilities(request)
    if host is None or capabilities is None:
        return ""
    _, runtime, _ = host
    contribution, diagnostics = runtime.review_contribution(
        tuple(sorted({path.lstrip("/") for path in paths})),
        capabilities,
    )
    _require_complete_plugin_contribution("review", diagnostics)
    evidence_requests = contribution.evidence_requests
    hidden_request_count = 0
    if visible_evidence_by_id is not None and evidence_requests:
        visible_identifiers = _visible_plugin_fact_identifiers(
            visible_evidence_by_id
        )
        evidence_requests = tuple(
            item
            for item in evidence_requests
            if _normalized_evidence_identifier(item.identifier)
            in visible_identifiers
        )
        hidden_request_count = (
            len(contribution.evidence_requests) - len(evidence_requests)
        )

    lines: list[str] = []
    if contribution.rules:
        lines.append("Deterministic analysis-plugin evidence rules:")
        lines.extend(f"- {rule}" for rule in contribution.rules)
    if evidence_requests:
        lines.append("Exact evidence required before matching claims:")
        grouped_requests: dict[tuple[str, str], list[str]] = {}
        for item in evidence_requests:
            grouped_requests.setdefault(
                (item.kind, item.reason),
                [],
            ).append(item.identifier)
        request_groups = tuple(sorted(grouped_requests.items()))
        for index, ((kind, reason), _) in enumerate(request_groups, start=1):
            lines.append(f"- E{index}: {kind} — {reason}")
        lines.append(
            "For a relationship claim governed by E#, set claimKind to its "
            "exact evidence class and cite matching RAG Evidence IDs; leave "
            "claimKind empty for generic defects proved by changed source."
        )
        lines.append(
            "For a structural fact, existence proves the relationship but not "
            "a defect; report it only when current source or an exact diagnostic "
            "fact proves concrete harmful behavior."
        )

        if include_evidence_targets:
            target_groups: dict[str, list[str]] = {}
            for index, (_, identifiers) in enumerate(request_groups, start=1):
                for identifier in sorted(set(identifiers)):
                    target_groups.setdefault(identifier, []).append(f"E{index}")
            lines.append("Evidence targets:")
            normalized_paths = {
                path.lstrip("/")
                for path in paths
            }
            unrequested_path_count = len(normalized_paths - set(target_groups))
            if unrequested_path_count:
                lines.append(
                    f"[{unrequested_path_count} review path(s) have no plugin "
                    "evidence request; do not treat this omission as proof that "
                    "no related framework evidence exists]"
                )
            lines.extend(
                f"  - {identifier} — {', '.join(group_ids)}"
                for identifier, group_ids in sorted(target_groups.items())
            )
    if hidden_request_count:
        lines.append(
            f"[{hidden_request_count} plugin evidence target(s) omitted because "
            "no matching exact fact is visible in this bounded prompt; do not "
            "create typed framework claims from plugin rules alone]"
        )
    return _bounded_lines(lines, PLUGIN_CONTEXT_CHAR_BUDGET)


def _normalized_evidence_identifier(value: Any) -> str:
    return str(value or "").strip().lstrip("/").replace("\\", "/")


def _visible_plugin_fact_identifiers(
    evidence_by_id: Mapping[str, Sequence[Mapping[str, Any]]],
) -> set[str]:
    """Collect exact identifiers from facts that actually survived prompt caps."""
    identifiers: set[str] = set()
    for evidence_id in sorted(evidence_by_id):
        for fact in evidence_by_id[evidence_id]:
            if not isinstance(fact, Mapping):
                continue
            for key in ("path", "source", "target"):
                normalized = _normalized_evidence_identifier(fact.get(key))
                if normalized:
                    identifiers.add(normalized)
            for path in fact.get("related_paths", ()) or ():
                normalized = _normalized_evidence_identifier(path)
                if normalized:
                    identifiers.add(normalized)
            attributes = fact.get("attributes")
            if isinstance(attributes, Mapping):
                for key, value in attributes.items():
                    for candidate in (key, value):
                        normalized = _normalized_evidence_identifier(candidate)
                        if normalized:
                            identifiers.add(normalized)
    return identifiers


def _canonical_review_group_components(
    groups: Sequence[Sequence[str]],
    allowed_paths: Sequence[str],
) -> list[tuple[str, ...]]:
    """Merge overlapping neutral constraints into stable disjoint components."""
    canonical_by_normalized = {
        path.strip().replace("\\", "/").lstrip("/"): path
        for path in allowed_paths
        if isinstance(path, str) and path.strip()
    }
    parent = {path: path for path in canonical_by_normalized}

    def find(path: str) -> str:
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    participating: set[str] = set()
    for group in groups:
        members = sorted({
            normalized
            for path in group
            if (
                isinstance(path, str)
                and (
                    normalized := path.strip().replace("\\", "/").lstrip("/")
                ) in canonical_by_normalized
            )
        })
        if len(members) < 2:
            continue
        participating.update(members)
        for member in members[1:]:
            union(members[0], member)

    components: dict[str, list[str]] = {}
    for normalized in sorted(participating):
        components.setdefault(find(normalized), []).append(
            canonical_by_normalized[normalized]
        )
    return sorted(
        (tuple(paths) for paths in components.values() if len(paths) > 1),
        key=lambda paths: tuple(
            path.strip().replace("\\", "/").lstrip("/") for path in paths
        ),
    )


def apply_plugin_plan_constraints(
    plan: Any,
    request: ReviewRequestDto,
    repository_group_paths: Sequence[Sequence[str]] = (),
) -> Any:
    host = _plugin_host()
    capabilities = resolve_project_capabilities(request)
    if host is None or capabilities is None:
        return plan
    _, runtime, _ = host
    contribution, diagnostics = runtime.review_contribution(
        tuple(sorted(set(request.changedFiles or []))),
        capabilities,
    )
    _require_complete_plugin_contribution("review planning", diagnostics)
    all_group_paths = (
        *contribution.group_paths,
        *tuple(tuple(paths) for paths in repository_group_paths),
    )
    if not all_group_paths:
        return plan

    from model.multi_stage import FileGroup

    file_by_path = {
        review_file.path: review_file
        for group in plan.file_groups
        for review_file in group.files
    }
    effective_groups = _canonical_review_group_components(
        all_group_paths,
        tuple(file_by_path),
    )
    constrained = {path for group in effective_groups for path in group}
    retained = []
    for group in plan.file_groups:
        remaining = [item for item in group.files if item.path not in constrained]
        if remaining:
            retained.append(group.model_copy(update={"files": remaining}))

    plugin_groups = []
    for index, paths in enumerate(effective_groups, start=1):
        files = [file_by_path[path] for path in paths if path in file_by_path]
        plugin_groups.append(FileGroup(
            group_id=f"PLUGIN_EVIDENCE_{index:03d}",
            priority="HIGH",
            rationale=(
                "Files are connected by deterministic plugin review constraints "
                "or repository graph facts."
            ),
            files=files,
        ))
    plan.file_groups = plugin_groups + retained
    return plan


def apply_plugin_validation_gate(
    issues: list[Any],
    request: ReviewRequestDto,
    *,
    exact_evidence_by_id: Optional[
        Mapping[str, Sequence[Mapping[str, Any]]]
    ] = None,
    deterministic_retrieval_states: Optional[Sequence[str]] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> list[Any]:
    host = _plugin_host()
    capabilities = resolve_project_capabilities(request)
    if host is None or capabilities is None or not issues:
        return issues
    _, runtime, _ = host
    from codecrow_plugins import (
        CandidateClaim,
        FileArtifact,
        GraphFact,
        ValidationDecision,
    )

    def graph_fact(payload: Mapping[str, Any]) -> Optional[GraphFact]:
        try:
            attributes = payload.get("attributes")
            return GraphFact(
                kind=str(payload["kind"]),
                source=str(payload["source"]),
                relation=str(payload["relation"]),
                target=str(payload["target"]),
                path=str(payload["path"]).lstrip("/"),
                line=max(1, int(payload.get("line", 1) or 1)),
                attributes=tuple(sorted(
                    (str(key), str(value))
                    for key, value in (
                        attributes.items()
                        if isinstance(attributes, Mapping)
                        else ()
                    )
                )),
                related_paths=tuple(sorted({
                    str(path).lstrip("/")
                    for path in payload.get("related_paths", ())
                    if isinstance(path, str) and path
                })),
            )
        except (KeyError, TypeError, ValueError):
            return None

    evidence_index = exact_evidence_by_id or {}

    all_facts = set()
    artifacts = []
    enrichment = getattr(request, "enrichmentData", None)
    for file_content in getattr(enrichment, "fileContents", None) or []:
        if not file_content.content or file_content.skipped:
            continue
        try:
            artifact = FileArtifact(file_content.path.lstrip("/"), file_content.content)
            artifacts.append(artifact)
            facts, diagnostics = runtime.graph_facts(artifact, capabilities)
            _log_plugin_diagnostics("validation graph facts", diagnostics)
            all_facts.update(facts)
        except ValueError:
            continue

    revision = next(
        (
            value for value in (
                getattr(request, "currentCommitHash", None),
                getattr(request, "commitHash", None),
            )
            if isinstance(value, str) and value.strip()
        ),
        "unresolved-revision",
    )
    handle = runtime.start_repository_analysis(capabilities, revision)
    if handle.active and artifacts:
        handle.ingest(tuple(sorted(artifacts, key=lambda artifact: artifact.path)))
        analysis, diagnostics = handle.finish()
        all_facts.update(
            fact for packet in analysis.packets for fact in packet.facts
        )
        _log_plugin_diagnostics("repository validation", diagnostics)

    previous_open_ids = set()
    for previous in getattr(request, "previousCodeAnalysisIssues", None) or []:
        payload = (
            previous.model_dump()
            if hasattr(previous, "model_dump")
            else previous
            if isinstance(previous, dict)
            else vars(previous)
        )
        status = str(payload.get("status") or "").strip().casefold()
        issue_id = str(payload.get("id") or "").strip()
        if issue_id and status in {"", "open"}:
            previous_open_ids.add(issue_id)

    kept = []

    def preserve_historical_lifecycle(
        issue: Any,
        resolution: Optional[str] = None,
    ) -> bool:
        issue_id = str(getattr(issue, "id", "") or "").strip()
        if not issue_id or issue_id not in previous_open_ids:
            return False
        if resolution:
            issue.isResolved = True
            issue.resolutionReason = resolution
            issue.resolutionExplanation = resolution
        kept.append(issue)
        return True

    for issue in issues:
        if getattr(issue, "isResolved", False):
            kept.append(issue)
            continue
        text = " ".join(
            str(getattr(issue, field, "") or "")
            for field in ("title", "reason")
        )
        raw_category = str(getattr(issue, "category", "") or "uncategorized")
        category = raw_category.strip().casefold().replace("_", "-").replace(" ", "-")
        raw_claim_kind = getattr(issue, "claimKind", "") or ""
        claim_kind = raw_claim_kind.strip() if isinstance(raw_claim_kind, str) else ""
        evidence_refs = tuple(dict.fromkeys(
            ref.strip()
            for ref in (getattr(issue, "evidenceRefs", None) or [])
            if isinstance(ref, str) and ref.strip()
        ))
        candidate_record = (
            candidate_ledger.record_for(issue)
            if candidate_ledger is not None
            else None
        )
        issue_evidence_index = (
            candidate_record.visible_evidence_by_id
            if candidate_record is not None
            else evidence_index
        )
        if claim_kind and not evidence_refs:
            logger.info(
                "Plugin evidence gate withheld typed claim in %s: "
                "claimKind=%s has no evidenceRefs",
                getattr(issue, "file", ""),
                claim_kind,
            )
            if preserve_historical_lifecycle(issue):
                continue
            if candidate_ledger is not None:
                candidate_ledger.reject(
                    issue,
                    gate="plugin_evidence",
                    code="missing_evidence_refs",
                )
            continue
        if evidence_refs and any(
            reference not in issue_evidence_index
            for reference in evidence_refs
        ):
            logger.info(
                "Plugin evidence gate withheld claim in %s: "
                "claimKind=%s cites unavailable evidence",
                getattr(issue, "file", ""),
                claim_kind,
            )
            if preserve_historical_lifecycle(issue):
                continue
            if candidate_ledger is not None:
                candidate_ledger.reject(
                    issue,
                    gate="plugin_evidence",
                    code="unavailable_evidence_ref",
                )
            continue
        cited_facts = {
            fact
            for reference in evidence_refs
            for payload in issue_evidence_index.get(reference, ())
            if (fact := graph_fact(payload)) is not None
        }
        # Explicit citations scope repository validation. Uncited local facts
        # must not accidentally approve a different same-kind relationship.
        claim_facts = cited_facts if evidence_refs else all_facts
        claim = CandidateClaim(
            category=category,
            path=str(getattr(issue, "file", "")).lstrip("/"),
            line=max(1, int(getattr(issue, "line", 1) or 1)),
            message=text,
            evidence=tuple(sorted(claim_facts)),
            claim_kind=claim_kind,
        )
        results, diagnostics = runtime.validate_with_diagnostics(claim, capabilities)
        _log_plugin_diagnostics(
            f"validation:{claim.path}",
            diagnostics,
        )
        if not claim_kind and evidence_refs and cited_facts:
            # A model must not bypass an exact contradiction by omitting
            # claimKind. Re-run plugin validation for each exact cited fact
            # class, but retain only deterministic REJECT decisions. PASS and
            # INSUFFICIENT_EVIDENCE cannot type or suppress an otherwise
            # generic finding.
            inferred_rejections = []
            for inferred_kind in sorted({fact.kind for fact in cited_facts}):
                inferred_claim = CandidateClaim(
                    category=category,
                    path=claim.path,
                    line=claim.line,
                    message=text,
                    evidence=tuple(sorted(
                        fact
                        for fact in cited_facts
                        if fact.kind == inferred_kind
                    )),
                    claim_kind=inferred_kind,
                )
                inferred_results, inferred_diagnostics = (
                    runtime.validate_with_diagnostics(
                        inferred_claim,
                        capabilities,
                    )
                )
                _log_plugin_diagnostics(
                    f"inferred validation:{claim.path}",
                    inferred_diagnostics,
                )
                inferred_rejections.extend(
                    result
                    for result in inferred_results
                    if result.decision is ValidationDecision.REJECT
                )
            results = (*results, *inferred_rejections)
        if claim_kind and not results:
            logger.info(
                "Plugin evidence gate withheld typed claim in %s: "
                "claimKind=%s is not handled by an active plugin",
                claim.path,
                claim_kind,
            )
            if preserve_historical_lifecycle(issue):
                continue
            if candidate_ledger is not None:
                candidate_ledger.reject(
                    issue,
                    gate="plugin_evidence",
                    code="unhandled_claim_kind",
                )
            continue
        rejection = next(
            (
                result
                for result in results
                if result.decision is ValidationDecision.REJECT
            ),
            None,
        )
        if rejection is not None:
            logger.info("Plugin evidence gate rejected issue in %s: %s", claim.path, rejection.code)
            if preserve_historical_lifecycle(
                issue,
                (
                    "Closed because deterministic plugin evidence contradicts "
                    f"the prior finding ({rejection.code})."
                ),
            ):
                continue
            if candidate_ledger is not None:
                candidate_ledger.reject(
                    issue,
                    gate="plugin_evidence",
                    code=str(rejection.code or "rejected"),
                )
            continue
        unsupported_citation = next(
            (
                result
                for result in results
                if result.decision
                is ValidationDecision.INSUFFICIENT_EVIDENCE
            ),
            None,
        )
        if unsupported_citation is not None:
            logger.info(
                "Plugin evidence gate withheld issue in %s: %s "
                "(refs=%s, retrieval_states=%s)",
                claim.path,
                unsupported_citation.code,
                evidence_refs,
                tuple(deterministic_retrieval_states or ()),
            )
            if preserve_historical_lifecycle(issue):
                continue
            if candidate_ledger is not None:
                candidate_ledger.reject(
                    issue,
                    gate="plugin_evidence",
                    code=str(unsupported_citation.code or "insufficient_evidence"),
                )
            continue
        if claim_kind and not any(
            result.decision is ValidationDecision.PASS for result in results
        ):
            logger.info(
                "Plugin evidence gate withheld typed claim in %s: "
                "claimKind=%s has no deterministic plugin approval",
                claim.path,
                claim_kind,
            )
            if preserve_historical_lifecycle(issue):
                continue
            if candidate_ledger is not None:
                candidate_ledger.reject(
                    issue,
                    gate="plugin_evidence",
                    code="no_deterministic_approval",
                )
            continue
        kept.append(issue)
    return kept
