"""Stage 2: Cross-file and architectural analysis."""
import json
import hashlib
import logging
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.enrichment import PrEnrichmentDataDto
from model.multi_stage import ReviewPlan, CrossFileAnalysisResult
from utils.diff_processor import ProcessedDiff
from utils.task_context_builder import build_task_context

from utils.llm_response import extract_llm_response_text
from service.review.orchestrator.json_utils import (
    parse_llm_response,
    resolve_structured_output,
    supports_structured_output,
)
from service.review.orchestrator.structured_output import (
    format_response_diagnostics,
    invoke_structured_output,
    output_token_request_kwargs,
)
from service.review.pr_evidence import (
    PrEvidenceLedger,
    build_pr_evidence_ledger,
)
from service.review.orchestrator.inference_policy import (
    ReviewInferenceProfile,
    build_review_inference_profile,
)
from service.review.orchestrator.stage_2_semantic_packets import (
    STAGE2_INPUT_TOKEN_TARGET,
    STAGE_2_ARCHITECTURE_CONTEXT_CHAR_BUDGET,
    Stage2SemanticPacketInput,
    Stage2Prompt,
    _Stage2Prompt,
    _architecture_payload,
    _estimated_prompt_tokens,
    _format_complete_project_rules,
    _stage_2_input_token_budget,
    build_stage_2_prompts,
)
from llm.reasoning_policy import ReasoningEffort, reasoning_request_kwargs
from utils.llm_delegate import unwrap_llm_delegate

logger = logging.getLogger(__name__)


# Stage 2 returns a compact typed finding list. A finite stage-local completion
# budget prevents reasoning models from spending the provider's entire context
# window without emitting the schema. The reasoning-free recovery gets the same
# bound so a malformed direct response cannot run away either.
STAGE2_MAX_OUTPUT_TOKENS = 16_384


def _stage_2_output_token_limit(llm: Any) -> int:
    """Respect a provider/model cap that is lower than the Stage 2 ceiling."""
    limits = [STAGE2_MAX_OUTPUT_TOKENS]
    for candidate in (llm, unwrap_llm_delegate(llm)):
        for attribute in ("max_tokens", "max_output_tokens"):
            configured = getattr(candidate, attribute, None)
            if (
                isinstance(configured, int)
                and not isinstance(configured, bool)
                and configured > 0
            ):
                limits.append(configured)
    return min(limits)


class Stage2GenerationError(ValueError):
    """All core Stage 2 responses were exhausted without a valid result."""


def _build_stage_2_prompts(
    *,
    repo_slug: str,
    pr_title: str,
    commit_hash: str,
    stage_1_findings_json: str,
    architecture_context: str,
    migrations: str,
    cross_file_concerns: Sequence[str],
    project_rules: str,
    task_context: str,
    task_history_context: str,
    evidence_ledger: PrEvidenceLedger,
    token_budget: int = STAGE2_INPUT_TOKEN_TARGET,
    max_packets: int = 4,
) -> List[str]:
    """Compatibility facade over the typed semantic-packet boundary."""
    return build_stage_2_prompts(Stage2SemanticPacketInput(
        repo_slug=repo_slug,
        pr_title=pr_title,
        commit_hash=commit_hash,
        stage_1_findings_json=stage_1_findings_json,
        architecture_context=architecture_context,
        migrations=migrations,
        cross_file_concerns=cross_file_concerns,
        project_rules=project_rules,
        task_context=task_context,
        task_history_context=task_history_context,
        evidence_ledger=evidence_ledger,
        token_budget=token_budget,
        max_packets=max_packets,
    ))


async def execute_stage_2_cross_file(
    llm,
    request: ReviewRequestDto,
    stage_1_issues: List[CodeReviewIssue],
    plan: ReviewPlan,
    processed_diff: Optional[ProcessedDiff] = None,
    fallback_llm=None,
    visible_prompt_hunk_ids: Optional[set[str]] = None,
    prompt_provenance: Optional[Dict[str, str]] = None,
    pr_evidence_ledger: Optional[PrEvidenceLedger] = None,
    inference_profile: Optional[ReviewInferenceProfile] = None,
) -> CrossFileAnalysisResult:
    inference_profile = inference_profile or build_review_inference_profile(
        request,
        processed_diff,
    )
    issues_json = _slim_issues_for_stage_2(stage_1_issues)
    try:
        architecture_context = _build_architecture_context(
            enrichment=request.enrichmentData,
            changed_files=request.changedFiles,
        )
    except Exception as exc:
        logger.warning(
            "Optional Stage 2 architecture enrichment could not be rendered; "
            "continuing with core PR evidence: %s",
            exc,
        )
        architecture_context = (
            "No architecture context available (enrichment rendering failed)."
        )
    migrations = _detect_migration_paths(processed_diff)
    evidence_ledger = pr_evidence_ledger or build_pr_evidence_ledger(
        processed_diff,
        processed_diff,
        incremental=bool(
            request.analysisMode == "INCREMENTAL" and request.deltaDiff
        ),
        task_context=(
            request.taskContext
            if isinstance(request.taskContext, dict)
            else None
        ),
        pr_title=request.prTitle if isinstance(request.prTitle, str) else "",
        pr_description=(
            request.prDescription
            if isinstance(request.prDescription, str)
            else ""
        ),
    )
    input_token_budget = _stage_2_input_token_budget(request)
    prompts = _build_stage_2_prompts(
        repo_slug=request.projectVcsRepoSlug,
        pr_title=request.prTitle or "",
        commit_hash=request.currentCommitHash or request.commitHash or "",
        stage_1_findings_json=issues_json,
        architecture_context=architecture_context,
        migrations=migrations,
        cross_file_concerns=plan.cross_file_concerns,
        project_rules=_format_complete_project_rules(request.projectRules),
        task_context=(
            build_task_context(request.taskContext)
            or "No task context available."
        ),
        task_history_context=_build_task_history_context(request),
        evidence_ledger=evidence_ledger,
        token_budget=input_token_budget,
        max_packets=inference_profile.invocation_cap("stage_2_packets"),
    )
    if visible_prompt_hunk_ids is not None:
        visible_prompt_hunk_ids.clear()
        for prompt in prompts:
            visible_prompt_hunk_ids.update(
                getattr(prompt, "visible_hunk_ids", ())
            )

    prompt_digests = [_prompt_digest(prompt) for prompt in prompts]
    if prompt_provenance is not None:
        prompt_provenance.clear()
        prompt_provenance["generationPromptDigests"] = json.dumps(
            prompt_digests,
            separators=(",", ":"),
        )
        prompt_provenance["generationPromptCount"] = str(len(prompts))
        prompt_provenance["omittedPacketCount"] = str(max(
            (getattr(prompt, "omitted_packet_count", 0) for prompt in prompts),
            default=0,
        ))
        prompt_provenance["omittedUnitCount"] = str(max(
            (getattr(prompt, "omitted_unit_count", 0) for prompt in prompts),
            default=0,
        ))
        prompt_provenance["omittedHunkCount"] = str(max(
            (getattr(prompt, "omitted_hunk_count", 0) for prompt in prompts),
            default=0,
        ))
        prompt_provenance["completePrEvidenceVisible"] = (
            "true"
            if len(prompts) == 1
            and bool(getattr(prompts[0], "complete_pr_evidence_visible", False))
            else "false"
        )
        if len(prompts) == 1:
            prompt_provenance["generationPromptDigest"] = prompt_digests[0]

    successful_results: List[
        tuple[CrossFileAnalysisResult, _Stage2Prompt]
    ] = []
    optional_failures = 0
    core_failures = 0
    core_successes = 0
    for index, prompt in enumerate(prompts, start=1):
        estimated_tokens = _estimated_prompt_tokens(prompt)
        logger.info(
            "Stage 2 prompt assembled: shard=%d/%d chars=%d "
            "estimated_tokens=%d target_tokens=%d",
            index,
            len(prompts),
            len(prompt),
            estimated_tokens,
            input_token_budget,
        )
        result = await _invoke_stage_2_llm(
            llm,
            prompt,
            label=(
                "structured primary"
                if len(prompts) == 1
                else (
                    f"structured primary semantic-shard-{index}-of-"
                    f"{len(prompts)}"
                )
            ),
        )
        retry_llm = (
            fallback_llm
            if fallback_llm is not None and fallback_llm is not llm
            else llm
        )
        if result is None:
            logger.info(
                "Stage 2 structured response was unusable for semantic shard "
                "%d/%d; retrying once as a reasoning-free direct output "
                "request",
                index,
                len(prompts),
            )
            result = await _invoke_stage_2_llm(
                retry_llm,
                prompt,
                label=(
                    "direct-output recovery"
                    if len(prompts) == 1
                    else (
                        f"direct-output recovery semantic-shard-{index}-of-"
                        f"{len(prompts)}"
                    )
                ),
                force_unstructured=True,
            )
        if result is None:
            if bool(getattr(prompt, "optional_enrichment_only", False)):
                optional_failures += 1
                logger.warning(
                    "Optional Stage 2 enrichment shard failed open: "
                    "shard=%d/%d digest=%s",
                    index,
                    len(prompts),
                    prompt_digests[index - 1],
                )
                continue
            core_failures += 1
            logger.warning(
                "Core Stage 2 semantic shard exhausted its response attempts; "
                "continuing with remaining shards: shard=%d/%d digest=%s",
                index,
                len(prompts),
                prompt_digests[index - 1],
            )
            continue
        if not bool(getattr(prompt, "optional_enrichment_only", False)):
            core_successes += 1
        successful_results.append((
            result,
            prompt
            if isinstance(prompt, _Stage2Prompt)
            else _Stage2Prompt(
                prompt,
                visible_hunk_ids=evidence_ledger.delta_hunk_ids,
            ),
        ))

    if prompt_provenance is not None:
        prompt_provenance["optionalEnrichmentShardFailures"] = str(
            optional_failures
        )
        prompt_provenance["coreSemanticShardFailures"] = str(core_failures)
        if core_failures:
            prompt_provenance["completePrEvidenceVisible"] = "false"

    if core_successes == 0:
        raise Stage2GenerationError(
            "Stage 2 exhausted every core semantic shard response "
            f"({core_failures} failed core shard(s))"
        )

    merged, issue_provenance = _merge_stage_2_results_with_provenance(
        successful_results
    )
    if core_failures:
        merged.confidence = "LOW"
        logger.warning(
            "Stage 2 produced a partial cross-file result: successful_core=%d "
            "failed_core=%d; aggregate confidence forced to LOW",
            core_successes,
            core_failures,
        )
    if prompt_provenance is not None:
        prompt_provenance["issuePromptDigests"] = json.dumps(
            {
                issue_id: provenance.prompt_digest
                for issue_id, provenance in sorted(issue_provenance.items())
            },
            separators=(",", ":"),
        )
        prompt_provenance["issuePromptHunkIds"] = json.dumps(
            {
                issue_id: sorted(provenance.visible_hunk_ids)
                for issue_id, provenance in sorted(issue_provenance.items())
            },
            separators=(",", ":"),
        )
        prompt_provenance["issuePromptEvidenceIds"] = json.dumps(
            {
                issue_id: sorted(provenance.visible_evidence_ids)
                for issue_id, provenance in sorted(issue_provenance.items())
            },
            separators=(",", ":"),
        )

    return merged


async def _invoke_stage_2_llm(
    llm,
    prompt: str,
    label: str,
    force_unstructured: bool = False,
) -> Optional[CrossFileAnalysisResult]:
    output_token_limit = _stage_2_output_token_limit(llm)
    if supports_structured_output(llm) and not force_unstructured:
        try:
            invocation = await invoke_structured_output(
                llm,
                prompt,
                CrossFileAnalysisResult,
                effort=ReasoningEffort.LOW,
                label=f"stage-2-{label}",
                max_tokens=output_token_limit,
            )
            result = await resolve_structured_output(
                invocation,
                CrossFileAnalysisResult,
                llm,
            )
            if result:
                logger.info("Stage 2 cross-file analysis completed with structured output (%s)", label)
                return result
            logger.debug("Structured output returned empty Stage 2 result (%s)", label)
        except Exception as e:
            logger.warning(
                "Structured output failed for Stage 2 (%s): error_type=%s",
                label,
                type(e).__name__,
            )
        return None
    else:
        logger.info("Structured output skipped for Stage 2 (%s); using prompt JSON parsing", label)

    try:
        response = await llm.ainvoke(
            prompt,
            **output_token_request_kwargs(llm, output_token_limit),
            **reasoning_request_kwargs(
                llm,
                ReasoningEffort.NONE
                if force_unstructured
                else ReasoningEffort.LOW,
            ),
        )
        content = extract_llm_response_text(response)
        if not content.strip():
            logger.warning(
                "Stage 2 raw fallback returned no content (%s): %s",
                label,
                format_response_diagnostics(response),
            )
        return await parse_llm_response(
            content,
            CrossFileAnalysisResult,
            llm,
            max_provider_repairs=0,
        )
    except Exception as e:
        logger.debug("Stage 2 cross-file analysis failed (%s): %s", label, e)
        return None


def _prompt_digest(prompt: str) -> str:
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _IssueProvenance:
    prompt_digest: str
    visible_hunk_ids: frozenset[str]
    visible_evidence_ids: frozenset[str]


def _issue_identity(issue: Any) -> str:
    payload = issue.model_dump()
    payload.pop("id", None)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _issue_has_visible_evidence(
    issue: Any,
    provenance: _IssueProvenance,
) -> bool:
    evidence_refs = {
        str(evidence_id).strip()
        for evidence_id in (getattr(issue, "evidenceRefs", ()) or ())
        if str(evidence_id).strip()
    }
    return evidence_refs.issubset(provenance.visible_evidence_ids)


def _issue_provenance_rank(
    issue: Any,
    provenance: _IssueProvenance,
) -> tuple[Any, ...]:
    """Prefer a citation-valid generating shard, then the stable tie-break."""
    return (
        not _issue_has_visible_evidence(issue, provenance),
        provenance.prompt_digest,
        tuple(sorted(provenance.visible_hunk_ids)),
        tuple(sorted(provenance.visible_evidence_ids)),
    )


def _merge_stage_2_results_with_provenance(
    result_prompts: Sequence[tuple[CrossFileAnalysisResult, _Stage2Prompt]],
) -> tuple[CrossFileAnalysisResult, Dict[str, _IssueProvenance]]:
    if not result_prompts:
        raise ValueError("Stage 2 produced no semantic shard results")
    if len(result_prompts) == 1:
        result, prompt = result_prompts[0]
        provenance = _IssueProvenance(
            prompt_digest=_prompt_digest(prompt),
            visible_hunk_ids=prompt.visible_hunk_ids,
            visible_evidence_ids=prompt.visible_evidence_ids,
        )
        return result, {
            issue.id: provenance
            for issue in result.cross_file_issues
        }

    unique: Dict[str, tuple[Any, _IssueProvenance]] = {}
    for result, prompt in result_prompts:
        provenance = _IssueProvenance(
            prompt_digest=_prompt_digest(prompt),
            visible_hunk_ids=prompt.visible_hunk_ids,
            visible_evidence_ids=prompt.visible_evidence_ids,
        )
        for issue in result.cross_file_issues:
            key = _issue_identity(issue)
            existing = unique.get(key)
            if existing is None or _issue_provenance_rank(
                issue,
                provenance,
            ) < _issue_provenance_rank(existing[0], existing[1]):
                unique[key] = (issue, provenance)

    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    ordered_issues = sorted(
        unique.items(),
        key=lambda entry: (
            severity_order.get(str(entry[1][0].severity).upper(), 5),
            entry[1][0].primary_file,
            entry[1][0].line if entry[1][0].line is not None else -1,
            entry[1][0].title,
            entry[0],
        ),
    )
    renumbered = [
        entry[1][0].model_copy(update={"id": f"CROSS_{index:03d}"})
        for index, entry in enumerate(ordered_issues, start=1)
    ]
    issue_provenance = {
        f"CROSS_{index:03d}": entry[1][1]
        for index, entry in enumerate(ordered_issues, start=1)
    }

    def select(values: Iterable[str], ranking: Mapping[str, int], default: str) -> str:
        normalized = [str(value).upper() for value in values]
        if not normalized:
            return default
        return max(
            normalized,
            key=lambda value: (ranking.get(value, -1), value),
        )

    risk = select(
        (result.pr_risk_level for result, _ in result_prompts),
        {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3},
        "LOW",
    )
    recommendations = [
        "PASS"
        if str(result.pr_recommendation).upper() in {"PASS", "APPROVE"}
        else str(result.pr_recommendation).upper()
        for result, _ in result_prompts
    ]
    recommendation = select(
        recommendations,
        {"PASS": 0, "APPROVE": 0, "PASS_WITH_WARNINGS": 1, "FAIL": 2},
        "PASS",
    )
    # Confidence is conservative across shards: one low-confidence semantic
    # component lowers the confidence of the aggregate statement.
    confidence = select(
        (result.confidence for result, _ in result_prompts),
        {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3},
        "INFO",
    )
    return CrossFileAnalysisResult(
        pr_risk_level=risk,
        cross_file_issues=renumbered,
        pr_recommendation=recommendation,
        confidence=confidence,
    ), issue_provenance


def _merge_stage_2_results(
    results: Sequence[CrossFileAnalysisResult],
) -> CrossFileAnalysisResult:
    result, _ = _merge_stage_2_results_with_provenance([
        (
            item,
            _Stage2Prompt(
                f"deterministic-direct-merge:{index}",
                visible_hunk_ids=(),
            ),
        )
        for index, item in enumerate(results)
    ])
    return result


def stage_2_coverage_ledger(
    ledger: PrEvidenceLedger,
    prompt_provenance: Mapping[str, str],
) -> PrEvidenceLedger:
    """Prevent partial Stage 2 shards from proving full-review omissions."""
    if prompt_provenance.get("completePrEvidenceVisible") == "true":
        return ledger
    return replace(ledger, full_evidence_complete=False)


# ── Helpers ───────────────────────────────────────────────────


def _build_task_history_context(request: ReviewRequestDto) -> str:
    value = getattr(request, "taskHistoryContext", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "No prior task history available."


def _build_architecture_context(
    enrichment: Optional[PrEnrichmentDataDto],
    changed_files: Optional[List[str]],
) -> str:
    if not enrichment or not (
        getattr(enrichment, "relationships", None)
        or getattr(enrichment, "fileMetadata", None)
    ):
        return "No architecture context available (enrichment data not provided)."

    relationships = sorted(
        (
            item
            for item in (
                _compact_relationship(relation)
                for relation in enrichment.relationships
            )
            if item
        ),
        key=_relationship_priority,
    )
    metadata = sorted(
        (
            item
            for item in (
                _compact_file_metadata(file_metadata)
                for file_metadata in enrichment.fileMetadata
            )
            if item
        ),
        # Stable sort retains parser/provider order within the structural
        # authority tier; lexical path order distorts numbered source order at
        # a finite admission boundary.
        key=lambda item: (0 if _has_structural_metadata(item) else 1,),
    )
    path_references = {
        path: f"P{index:03d}"
        for index, path in enumerate(
            sorted({
                item[key]
                for item in relationships
                for key in ("source", "target")
            } | {
                item["path"]
                for item in metadata
            }),
            start=1,
        )
    }
    path_table = {
        reference: path
        for path, reference in path_references.items()
    }
    referenced_relationships = [
        {
            **item,
            "source": path_references[item["source"]],
            "target": path_references[item["target"]],
        }
        for item in relationships
    ]
    referenced_metadata = [
        {
            **item,
            "path": path_references[item["path"]],
        }
        for item in metadata
    ]

    payload = _architecture_payload(
        referenced_relationships,
        referenced_metadata,
        path_table,
    )
    inventory = payload.get("inventory", {})
    detail_complete = bool(inventory.get("metadata_detail_complete", True))
    payload_label = (
        "complete JSON"
        if detail_complete
        else "bounded JSON; omitted metadata counts are explicit"
    )
    result = f"Structured enrichment context ({payload_label}):\n" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    logger.info(
        "Stage 2 architecture prompt payload: relationships=%d, "
        "metadata=%d, chars=%d",
        len(referenced_relationships),
        len(referenced_metadata),
        len(result),
    )
    return result


def _compact_relationship(value: Any) -> Dict[str, Any]:
    source = getattr(value, "sourceFile", None)
    target = getattr(value, "targetFile", None)
    relationship_type = getattr(value, "relationshipType", None)
    enum_value = getattr(relationship_type, "value", None)
    if isinstance(enum_value, str):
        relationship_type = enum_value
    if not all(isinstance(item, str) and item for item in (
        source,
        target,
        relationship_type,
    )):
        return {}
    result: Dict[str, Any] = {
        "source": source,
        "target": target,
        "type": relationship_type,
    }
    matched_on = getattr(value, "matchedOn", None)
    if isinstance(matched_on, str) and matched_on:
        result["matched_on"] = matched_on
    return result


def _compact_file_metadata(value: Any) -> Dict[str, Any]:
    path = getattr(value, "path", None)
    if not isinstance(path, str) or not path:
        return {}
    result: Dict[str, Any] = {"path": path}
    scalar_fields = (
        ("language", "language"),
        ("parentClass", "parent_class"),
        ("namespace", "namespace"),
        ("error", "parser_error"),
    )
    for source_field, output_field in scalar_fields:
        field_value = getattr(value, source_field, None)
        if isinstance(field_value, str) and field_value:
            result[output_field] = field_value
    sequence_fields = (
        ("extendsClasses", "extends"),
        ("implementsInterfaces", "implements"),
        ("imports", "imports"),
    )
    for source_field, output_field in sequence_fields:
        field_value = getattr(value, source_field, None)
        if not isinstance(field_value, (list, tuple)):
            continue
        # Parser order is deterministic and carries locality (declared/base
        # types first). Preserve that priority while removing exact repeats;
        # lexical sorting would retain Type10 before Type2 at the cap boundary.
        normalized = list(dict.fromkeys(
            item
            for item in field_value
            if isinstance(item, str) and item
        ))
        if normalized:
            result[output_field] = normalized[:8]
            if len(normalized) > 8:
                result[f"{output_field}_omitted"] = len(normalized) - 8
    return result


def _has_structural_metadata(item: Dict[str, Any]) -> bool:
    return any(
        key in item
        for key in ("extends", "implements", "parent_class", "parser_error")
    )


def _relationship_priority(item: Dict[str, Any]) -> tuple:
    type_priority = {
        "EXTENDS": 0,
        "IMPLEMENTS": 0,
        "IMPORTS": 1,
        "CALLS": 2,
    }
    relationship_type = str(item.get("type", "")).upper()
    # ``sorted`` is stable: retain provider/source order within the same
    # authority tier instead of lexically admitting component_10 before
    # component_2 at a finite packet boundary.
    return (type_priority.get(relationship_type, 2),)


def _detect_migration_paths(processed_diff: Optional[ProcessedDiff]) -> str:
    return (
        "Migration or schema-related files are not pre-classified by filename. "
        "Use the full PR state ledger, structured enrichment context, task "
        "context, and diff evidence to decide whether migration or schema risks exist."
    )


def _slim_issues_for_stage_2(issues: List[CodeReviewIssue]) -> str:
    """Serialize every current Stage 1 finding without clipping its fields."""
    current_findings: List[Dict[str, Any]] = []
    for issue in issues:
        d = issue.model_dump()
        # Resolved lifecycle records are returned so Java can update historical
        # issues. They are not current findings and must not seed new Stage 2
        # architecture concerns.
        if d.get('isResolved') is True:
            continue
        d = {
            key: value
            for key, value in d.items()
            if value is not None and value != "" and value is not False
        }
        current_findings.append(d)

    return json.dumps(
        current_findings,
        ensure_ascii=False,
        separators=(",", ":"),
    )
