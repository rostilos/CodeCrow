"""
Stage 2: Cross-file & architectural analysis — duplication, conflicts, data flow.
"""
import json
import hashlib
import logging
import os
from collections import Counter
from typing import Any, Dict, List, Optional

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.enrichment import PrEnrichmentDataDto
from model.multi_stage import ReviewPlan, CrossFileAnalysisResult
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import ProcessedDiff
from utils.task_context_builder import build_task_context

from utils.llm_response import extract_llm_response_text
from service.review.orchestrator.json_utils import parse_llm_response, supports_structured_output
from service.review.orchestrator.context_helpers import format_duplication_context
from service.review.orchestrator.stage_helpers import format_project_rules_digest
from service.review.pr_evidence import (
    PrEvidenceLedger,
    build_pr_evidence_ledger,
)

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


STAGE_2_ARCHITECTURE_CONTEXT_CHAR_BUDGET = max(
    8_000,
    _env_int("REVIEW_STAGE_2_ARCHITECTURE_CONTEXT_CHAR_BUDGET", 64_000),
)
STAGE_2_FINDINGS_CHAR_BUDGET = max(
    16_000,
    _env_int("REVIEW_STAGE_2_FINDINGS_CHAR_BUDGET", 96_000),
)

_STAGE_2_STRIP_FIELDS = {
    'suggestedFixDiff', 'suggestedFixDescription',
    'resolutionReason', 'resolutionExplanation', 'resolvedInCommit', 'visibility',
}


async def execute_stage_2_cross_file(
    llm,
    request: ReviewRequestDto,
    stage_1_issues: List[CodeReviewIssue],
    plan: ReviewPlan,
    processed_diff: Optional[ProcessedDiff] = None,
    rag_client=None,
    fallback_llm=None,
    prefetched_cross_module_context: Optional[str] = None,
    visible_evidence_by_id: Optional[
        Dict[str, tuple[Dict[str, Any], ...]]
    ] = None,
    visible_prompt_hunk_ids: Optional[set[str]] = None,
    prompt_provenance: Optional[Dict[str, str]] = None,
    pr_evidence_ledger: Optional[PrEvidenceLedger] = None,
) -> CrossFileAnalysisResult:
    issues_json = _slim_issues_for_stage_2(stage_1_issues)
    architecture_context = _build_architecture_context(
        enrichment=request.enrichmentData,
        changed_files=request.changedFiles,
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
    if visible_prompt_hunk_ids is not None:
        visible_prompt_hunk_ids.clear()
        visible_prompt_hunk_ids.update(evidence_ledger.delta_hunk_ids)
    if prefetched_cross_module_context is not None:
        cross_module_context = prefetched_cross_module_context
    else:
        cross_module_context = await prefetch_stage_2_cross_module_context(
            rag_client=rag_client,
            request=request,
            processed_diff=processed_diff,
            visible_evidence_by_id=visible_evidence_by_id,
        )

    prompt = PromptBuilder.build_stage_2_cross_file_prompt(
        repo_slug=request.projectVcsRepoSlug,
        pr_title=request.prTitle or "",
        commit_hash=request.currentCommitHash or request.commitHash or "",
        stage_1_findings_json=issues_json,
        architecture_context=architecture_context,
        migrations=migrations,
        cross_file_concerns=plan.cross_file_concerns,
        cross_module_context=cross_module_context,
        project_rules=format_project_rules_digest(request.projectRules),
        task_context=(
            build_task_context(request.taskContext, max_description_length=4000)
            or "No task context available."
        ),
        task_history_context=_build_task_history_context(request),
        pr_change_summary=evidence_ledger.full_pr_context,
        incremental_delta_summary=evidence_ledger.incremental_delta_context,
    )
    if prompt_provenance is not None:
        prompt_provenance.clear()
        prompt_provenance["generationPromptDigest"] = (
            "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        )

    result = await _invoke_stage_2_llm(llm, prompt, label="capped")
    if result is not None:
        return result

    if fallback_llm is not None and fallback_llm is not llm:
        logger.warning("Stage 2 failed with capped LLM; retrying without output cap")
        result = await _invoke_stage_2_llm(fallback_llm, prompt, label="uncapped retry")
        if result is not None:
            return result

    raise ValueError("Stage 2 cross-file analysis failed after capped and fallback attempts")


async def prefetch_stage_2_cross_module_context(
    rag_client,
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff] = None,
    visible_evidence_by_id: Optional[
        Dict[str, tuple[Dict[str, Any], ...]]
    ] = None,
) -> str:
    return await _fetch_cross_module_context(
        rag_client=rag_client,
        request=request,
        processed_diff=processed_diff,
        visible_evidence_by_id=visible_evidence_by_id,
    )


async def _invoke_stage_2_llm(llm, prompt: str, label: str) -> Optional[CrossFileAnalysisResult]:
    if supports_structured_output(llm):
        try:
            structured_llm = llm.with_structured_output(CrossFileAnalysisResult)
            result = await structured_llm.ainvoke(prompt)
            if result:
                logger.info("Stage 2 cross-file analysis completed with structured output (%s)", label)
                return result
            logger.warning("Structured output returned empty Stage 2 result (%s)", label)
        except Exception as e:
            logger.warning("Structured output failed for Stage 2 (%s): %s", label, e)
    else:
        logger.info("Structured output skipped for Stage 2 (%s); using prompt JSON parsing", label)

    try:
        response = await llm.ainvoke(prompt)
        content = extract_llm_response_text(response)
        return await parse_llm_response(content, CrossFileAnalysisResult, llm)
    except Exception as e:
        logger.warning("Stage 2 cross-file analysis failed (%s): %s", label, e)
        return None


# ── Helpers ───────────────────────────────────────────────────


def _build_task_history_context(request: ReviewRequestDto) -> str:
    value = getattr(request, "taskHistoryContext", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "No prior task history available."


def _build_architecture_context(
    enrichment: Optional[PrEnrichmentDataDto],
    changed_files: Optional[List[str]],
    *,
    max_chars: Optional[int] = None,
) -> str:
    if not enrichment or not (
        getattr(enrichment, "relationships", None)
        or getattr(enrichment, "fileMetadata", None)
    ):
        return "No architecture context available (enrichment data not provided)."

    context_budget = max(
        2_000,
        max_chars
        if max_chars is not None
        else STAGE_2_ARCHITECTURE_CONTEXT_CHAR_BUDGET,
    )
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
        key=lambda item: (
            0 if _has_structural_metadata(item) else 1,
            item["path"],
        ),
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

    # Relationship edges carry the direct cross-file proof, so reserve most of
    # the section for them. Metadata still gets an independent allocation so a
    # dense call graph cannot erase inheritance/parser state for every file.
    empty_payload = _architecture_payload(
        referenced_relationships,
        [],
        referenced_metadata,
        [],
        {},
    )
    fixed_chars = len(
        "Structured enrichment context (bounded JSON):\n"
        + json.dumps(
            empty_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    entry_budget = max(1_000, context_budget - fixed_chars)
    relationships_selected = _pack_json_entries(
        referenced_relationships,
        int(entry_budget * 0.72),
    )
    metadata_selected = _pack_json_entries(
        referenced_metadata,
        int(entry_budget * 0.28),
    )
    payload = _architecture_payload(
        referenced_relationships,
        relationships_selected,
        referenced_metadata,
        metadata_selected,
        _selected_path_table(
            path_table,
            relationships_selected,
            metadata_selected,
        ),
    )
    result = "Structured enrichment context (bounded JSON):\n" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    # Account for inventory/punctuation exactly. Remove low-priority complete
    # entries rather than slicing JSON or returning an unterminated object.
    while len(result) > context_budget and metadata_selected:
        metadata_selected.pop()
        payload = _architecture_payload(
            referenced_relationships,
            relationships_selected,
            referenced_metadata,
            metadata_selected,
            _selected_path_table(
                path_table,
                relationships_selected,
                metadata_selected,
            ),
        )
        result = "Structured enrichment context (bounded JSON):\n" + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    while len(result) > context_budget and relationships_selected:
        relationships_selected.pop()
        payload = _architecture_payload(
            referenced_relationships,
            relationships_selected,
            referenced_metadata,
            metadata_selected,
            _selected_path_table(
                path_table,
                relationships_selected,
                metadata_selected,
            ),
        )
        result = "Structured enrichment context (bounded JSON):\n" + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    logger.info(
        "Stage 2 architecture prompt budget: relationships=%d/%d, "
        "metadata=%d/%d, chars=%d/%d",
        len(relationships_selected),
        len(referenced_relationships),
        len(metadata_selected),
        len(referenced_metadata),
        len(result),
        context_budget,
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
    strength = getattr(value, "strength", None)
    if isinstance(strength, int):
        result["strength"] = strength
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
        normalized = sorted({
            item
            for item in field_value
            if isinstance(item, str) and item
        })
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
        "SAME_PACKAGE": 3,
    }
    relationship_type = str(item.get("type", "")).upper()
    strength = item.get("strength")
    return (
        type_priority.get(relationship_type, 2),
        -(strength if isinstance(strength, int) else 0),
        item.get("source", ""),
        item.get("target", ""),
        item.get("matched_on", ""),
    )


def _pack_json_entries(
    entries: List[Dict[str, Any]],
    char_budget: int,
) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    used = 0
    for entry in entries:
        entry_chars = len(json.dumps(
            entry,
            ensure_ascii=False,
            separators=(",", ":"),
        )) + (1 if selected else 0)
        if used + entry_chars > char_budget:
            continue
        selected.append(entry)
        used += entry_chars
    return selected


def _selected_path_table(
    path_table: Dict[str, str],
    relationships: List[Dict[str, Any]],
    metadata: List[Dict[str, Any]],
) -> Dict[str, str]:
    references = {
        item[key]
        for item in relationships
        for key in ("source", "target")
    } | {
        item["path"]
        for item in metadata
    }
    return {
        reference: path_table[reference]
        for reference in sorted(references)
    }


def _architecture_payload(
    relationships: List[Dict[str, Any]],
    relationships_selected: List[Dict[str, Any]],
    metadata: List[Dict[str, Any]],
    metadata_selected: List[Dict[str, Any]],
    path_table: Dict[str, str],
) -> Dict[str, Any]:
    relationship_types = Counter(
        str(item.get("type", "UNKNOWN"))
        for item in relationships
    )
    return {
        "inventory": {
            "relationship_count": len(relationships),
            "relationship_types": dict(sorted(relationship_types.items())),
            "included_relationship_count": len(relationships_selected),
            "omitted_relationship_count": (
                len(relationships) - len(relationships_selected)
            ),
            "metadata_file_count": len(metadata),
            "included_metadata_file_count": len(metadata_selected),
            "omitted_metadata_file_count": len(metadata) - len(metadata_selected),
            "omission_semantics": (
                "Omitted bounded entries are unknown, not evidence that a "
                "relationship is absent."
            ),
            "path_reference_semantics": (
                "source, target, and metadata path values reference path_table."
            ),
        },
        "path_table": path_table,
        "relationships": relationships_selected,
        "file_metadata": metadata_selected,
    }


def _detect_migration_paths(processed_diff: Optional[ProcessedDiff]) -> str:
    return (
        "Migration or schema-related files are not pre-classified by filename. "
        "Use the full PR state ledger, structured enrichment context, task "
        "context, and diff evidence to decide whether migration or schema risks exist."
    )


def _slim_issues_for_stage_2(
    issues: List[CodeReviewIssue],
    *,
    max_chars: Optional[int] = None,
) -> str:
    slim: List[Dict[str, Any]] = []
    for issue in issues:
        d = issue.model_dump()
        # Resolved lifecycle records are returned so Java can update historical
        # issues. They are not current findings and must not seed new Stage 2
        # architecture concerns.
        if d.get('isResolved') is True:
            continue
        for key in _STAGE_2_STRIP_FIELDS:
            d.pop(key, None)
        d = {
            key: value
            for key, value in d.items()
            if value is not None and value != "" and value is not False
        }
        for key, limit in (
            ("id", 200),
            ("file", 500),
            ("title", 300),
            ("reason", 1_200),
            ("codeSnippet", 600),
        ):
            value = d.get(key)
            if isinstance(value, str) and len(value) > limit:
                d[key] = (
                    value[:limit].rstrip()
                    + " [field truncated by deterministic Stage 2 budget]"
                )
        slim.append(d)

    if not slim:
        return "[]"

    context_budget = max(
        2_000,
        max_chars if max_chars is not None else STAGE_2_FINDINGS_CHAR_BUDGET,
    )
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for item in slim:
        file_path = item.get("file")
        group_key = file_path if isinstance(file_path, str) else "cross-file"
        by_file.setdefault(group_key, []).append(item)
    for values in by_file.values():
        values.sort(key=_stage_2_issue_priority)

    # First expose the highest-priority finding from every affected file, then
    # consume remaining findings by severity and per-file offset. This avoids a
    # noisy file erasing the PR-wide shape needed for cross-file reasoning.
    candidates: List[Dict[str, Any]] = [
        by_file[path][0]
        for path in sorted(by_file)
    ]
    remaining = [
        (offset, path, item)
        for path, values in by_file.items()
        for offset, item in enumerate(values[1:], start=1)
    ]
    candidates.extend(
        item
        for _, _, item in sorted(
            remaining,
            key=lambda value: (
                _stage_2_issue_priority(value[2]),
                value[0],
                value[1],
            ),
        )
    )

    selected: List[Dict[str, Any]] = []
    # Reserve enough space for a typed omission inventory if the cap is hit.
    payload_budget = max(1_000, context_budget - 512)
    used = 2
    for item in candidates:
        encoded = json.dumps(
            item,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        added = len(encoded) + (1 if selected else 0)
        if used + added > payload_budget:
            continue
        selected.append(item)
        used += added

    omitted = len(slim) - len(selected)
    if omitted:
        selected.append({
            "_codecrow_prompt_inventory": {
                "total_current_findings": len(slim),
                "included_findings": len(selected),
                "omitted_findings": omitted,
                "omission_semantics": (
                    "Omitted bounded findings remain valid Stage 1 findings; "
                    "their absence here is not proof that a cross-file relation "
                    "does not exist."
                ),
            }
        })
    result = json.dumps(
        selected,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    logger.info(
        "Stage 2 findings prompt budget: included=%d/%d, chars=%d/%d",
        len(slim) - omitted,
        len(slim),
        len(result),
        context_budget,
    )
    return result


def _stage_2_issue_priority(issue: Dict[str, Any]) -> tuple:
    severity_rank = {
        "CRITICAL": 0,
        "HIGH": 1,
        "MEDIUM": 2,
        "LOW": 3,
        "INFO": 4,
    }
    severity = str(issue.get("severity", "")).upper()
    line = issue.get("line")
    return (
        severity_rank.get(severity, 5),
        str(issue.get("category", "")),
        line if isinstance(line, int) else 0,
        str(issue.get("title", "")),
        str(issue.get("reason", "")),
    )


async def _fetch_cross_module_context(
    rag_client,
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff] = None,
    visible_evidence_by_id: Optional[
        Dict[str, tuple[Dict[str, Any], ...]]
    ] = None,
) -> str:
    if not rag_client:
        return ""

    base_revision_value = getattr(request, "baseCommitHash", None)
    base_generation_receipt_value = getattr(
        request,
        "ragBaseGenerationManifestSha256",
        None,
    )
    base_revision = (
        base_revision_value
        if isinstance(base_revision_value, str) and base_revision_value
        else None
    )
    base_generation_receipt = (
        base_generation_receipt_value
        if (
            isinstance(base_generation_receipt_value, str)
            and base_generation_receipt_value
        )
        else None
    )
    if not base_revision and not base_generation_receipt:
        logger.info(
            "Stage 2 cross-module RAG skipped: no exact target-generation lease"
        )
        return ""
    if not base_revision or not base_generation_receipt:
        raise RuntimeError(
            "Stage 2 cross-module RAG requires both immutable target revision "
            "and generation receipt"
        )

    try:
        rag_branch = request.get_rag_branch()
        base_branch = request.get_rag_base_branch()
        if not rag_branch:
            logger.warning(
                "Stage 2 cross-module RAG skipped: missing authoritative target branch"
            )
            return ""
        changed_files = request.changedFiles or []

        queries = []
        changed_files_json = json.dumps(changed_files, ensure_ascii=False)

        if request.prTitle:
            queries.append(
                "cross-module duplicate search PR title:\n"
                f"{request.prTitle}\nChanged files: {changed_files_json}"
            )

        if processed_diff:
            for f in processed_diff.get_included_files():
                queries.append(
                    "cross-module duplicate search diff evidence:\n"
                    f"File: {f.path}\n"
                    f"{f.content}"
                )

        if not queries:
            return ""

        seen = set()
        unique_queries = []
        for q in queries:
            if q not in seen and len(q) > 10:
                seen.add(q)
                unique_queries.append(q)
        unique_queries = unique_queries[:10]

        logger.info(f"Stage 2 cross-module RAG: {len(unique_queries)} queries")

        dup_results = await rag_client.search_for_duplicates(
            workspace=request.projectWorkspace,
            project=request.projectNamespace,
            branch=rag_branch,
            queries=unique_queries,
            top_k=6,
            base_branch=base_branch,
            repository_revision=base_revision,
            repository_generation_manifest_sha256=(
                base_generation_receipt
            ),
        )

        if not dup_results:
            return ""

        changed_set = set(changed_files)
        formatted = format_duplication_context(
            duplication_results=dup_results,
            batch_file_paths=list(changed_set),
            max_chunks=10,
            visible_evidence_by_id=visible_evidence_by_id,
        )

        if formatted:
            logger.info(f"Stage 2 cross-module context: {len(formatted)} chars")

        return formatted

    except Exception as e:
        raise RuntimeError(
            "Failed required revision-bound cross-module context for Stage 2: "
            f"{type(e).__name__}: {e}"
        ) from e
