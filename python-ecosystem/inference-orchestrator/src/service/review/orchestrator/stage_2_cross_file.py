"""
Stage 2: Cross-file & architectural analysis — duplication, conflicts, data flow.
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.enrichment import PrEnrichmentDataDto
from model.related_context import ContextGapV1
from model.multi_stage import ReviewPlan, CrossFileAnalysisResult
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import ProcessedDiff
from utils.task_context_builder import build_task_context

from service.review.orchestrator.agents import extract_llm_response_text
from service.review.telemetry import observed_ainvoke
from service.review.orchestrator.json_utils import parse_llm_response, supports_structured_output
from service.review.orchestrator.context_helpers import (
    format_duplication_context,
    format_related_context_pack,
)
from service.review.orchestrator.related_context import (
    build_related_context_pack,
    flatten_deterministic_context,
    manifest_anchor_digests,
)
from service.review.orchestrator.stage_helpers import format_project_rules_digest
from service.review.execution_context import (
    context_branch_labels,
    context_snapshot_v1,
)

logger = logging.getLogger(__name__)

_STAGE_2_STRIP_FIELDS = {
    'suggestedFixDiff', 'suggestedFixDescription',
    'resolutionExplanation', 'resolvedInCommit', 'visibility',
}

Stage2Context = Union[str, Dict[str, Any]]


async def execute_stage_2_cross_file(
    llm,
    request: ReviewRequestDto,
    stage_1_issues: List[CodeReviewIssue],
    plan: ReviewPlan,
    processed_diff: Optional[ProcessedDiff] = None,
    rag_client=None,
    fallback_llm=None,
    prefetched_cross_module_context: Optional[Stage2Context] = None,
) -> CrossFileAnalysisResult:
    issues_json = _slim_issues_for_stage_2(stage_1_issues)
    architecture_context = _build_architecture_context(
        enrichment=request.enrichmentData,
        changed_files=request.changedFiles,
    )
    migrations = _detect_migration_paths(processed_diff)
    pr_change_summary = _build_pr_change_summary(
        processed_diff=processed_diff,
        changed_files=request.changedFiles,
    )
    if prefetched_cross_module_context is not None:
        raw_cross_module_context = prefetched_cross_module_context
    else:
        raw_cross_module_context = await prefetch_stage_2_cross_module_context(
            rag_client=rag_client,
            request=request,
            processed_diff=processed_diff,
        )
    cross_module_context = _format_stage_2_context(raw_cross_module_context)

    prompt = PromptBuilder.build_stage_2_cross_file_prompt(
        repo_slug=request.projectVcsRepoSlug,
        pr_title=request.prTitle or "",
        commit_hash=request.commitHash or "HEAD",
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
        pr_change_summary=pr_change_summary,
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
) -> Stage2Context:
    return await _fetch_cross_module_context(
        rag_client=rag_client,
        request=request,
        processed_diff=processed_diff,
    )


async def _invoke_stage_2_llm(llm, prompt: str, label: str) -> Optional[CrossFileAnalysisResult]:
    structured_output_attempted = supports_structured_output(llm)
    if structured_output_attempted:
        try:
            structured_llm = llm.with_structured_output(CrossFileAnalysisResult)
            result = await observed_ainvoke(
                structured_llm, prompt, stage="generation", producer="stage_2"
            )
            if result:
                logger.info("Stage 2 cross-file analysis completed with structured output (%s)", label)
                return result
            logger.warning("Structured output returned empty Stage 2 result (%s)", label)
        except Exception as e:
            logger.warning("Structured output failed for Stage 2 (%s): %s", label, e)
    else:
        logger.info("Structured output skipped for Stage 2 (%s); using prompt JSON parsing", label)

    try:
        response = await observed_ainvoke(
            llm,
            prompt,
            stage="generation",
            producer="stage_2",
            retry=structured_output_attempted,
        )
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
) -> str:
    if not enrichment or not (
        getattr(enrichment, "relationships", None)
        or getattr(enrichment, "fileMetadata", None)
    ):
        return "No architecture context available (enrichment data not provided)."

    payload = {
        "changed_files": changed_files or [],
        "relationships": [_to_jsonable(rel) for rel in enrichment.relationships],
        "file_metadata": [_to_jsonable(meta) for meta in enrichment.fileMetadata],
    }
    return "Structured enrichment context (JSON):\n" + json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=False, exclude_none=True)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {key: _to_jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    enum_value = getattr(value, "value", None)
    if enum_value is not None and isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    if hasattr(value, "__dict__"):
        data = {
            key: _to_jsonable(val)
            for key, val in vars(value).items()
            if not key.startswith("_") and val is not None and not callable(val)
        }
        if data:
            return data
    return str(value)


def _detect_migration_paths(processed_diff: Optional[ProcessedDiff]) -> str:
    return (
        "Migration or schema-related files are not pre-classified by filename. "
        "Use the PR-wide change summary, structured enrichment context, task "
        "context, and diff evidence to decide whether migration or schema risks exist."
    )


def _build_pr_change_summary(
    processed_diff: Optional[ProcessedDiff],
    changed_files: Optional[List[str]],
    *,
    max_files: int = 80,
    max_changed_lines_per_file: int = 24,
    max_chars: int = 24000,
) -> str:
    if not processed_diff:
        files = changed_files or []
        if not files:
            return "No changed file summary available."
        listing = "\n".join(f"- {path}" for path in files[:max_files])
        if len(files) > max_files:
            listing += f"\n... and {len(files) - max_files} more files"
        return listing

    sections: List[str] = []
    included_files = processed_diff.get_included_files()

    for diff_file in included_files[:max_files]:
        change_type = getattr(diff_file.change_type, "value", str(diff_file.change_type))
        header = (
            f"- {diff_file.path} "
            f"({change_type}, +{diff_file.additions}/-{diff_file.deletions})"
        )
        evidence_notes = []
        if diff_file.skip_reason:
            evidence_notes.append(
                f"Diff evidence note: {diff_file.skip_reason}; compact summary evidence is shown."
            )
        changed_lines = []
        for line in (diff_file.content or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("[CodeCrow Summary") or stripped.startswith("Change statistics:"):
                evidence_notes.append(stripped)
            elif stripped.startswith("@@"):
                evidence_notes.append(f"Affected region: {stripped}")
            if line.startswith(("+++", "---", "@@")):
                continue
            if line.startswith(("+", "-")):
                changed_lines.append(line[:240])
            if len(changed_lines) >= max_changed_lines_per_file:
                break

        section_lines = [header]
        for note in list(dict.fromkeys(evidence_notes))[:12]:
            section_lines.append(f"  {note}")
        if changed_lines:
            section_lines.append("  Representative changed lines:")
            section_lines.extend(f"  {line}" for line in changed_lines)
        section = "\n".join(section_lines)
        sections.append(section)

        current = "\n\n".join(sections)
        if len(current) >= max_chars:
            return current[:max_chars] + "\n... PR-wide change summary truncated ..."

    if len(included_files) > max_files:
        sections.append(f"... and {len(included_files) - max_files} more changed files")

    return "\n\n".join(sections) if sections else "No changed file summary available."


def _slim_issues_for_stage_2(issues: List[CodeReviewIssue]) -> str:
    slim = []
    for issue in issues:
        d = issue.model_dump()
        for key in _STAGE_2_STRIP_FIELDS:
            d.pop(key, None)
        slim.append(d)
    return json.dumps(slim, indent=2)


def _format_stage_2_context(value: Optional[Stage2Context]) -> str:
    """Render only after an exact pack has survived the prefetch boundary."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    pack = value.get("related_context_pack_v1")
    return format_related_context_pack(pack) if isinstance(pack, dict) else ""


def _stage_2_exact_context(
    request: ReviewRequestDto,
    *,
    chunks: List[Dict[str, Any]],
    source_branch: Optional[str],
    base_branch: Optional[str],
    retrieval_failure: Optional[str] = None,
) -> Dict[str, Any]:
    snapshot = context_snapshot_v1(request)
    if snapshot is None:
        raise ValueError("exact Stage 2 context requires an execution snapshot")
    execution_manifest = request.executionManifest
    affected_paths = list(dict.fromkeys(request.changedFiles or []))
    extra_gaps = []
    if retrieval_failure:
        extra_gaps.append(
            ContextGapV1(
                code="context_retrieval_failed",
                detail=(
                    "Cross-module retrieval failed; Stage 2 has no unchanged-code "
                    f"evidence from this source. Cause: {retrieval_failure[:500]}"
                ),
                affected_paths=affected_paths,
            )
        )
    result = build_related_context_pack(
        chunks=chunks,
        anchor_paths=affected_paths,
        snapshot=snapshot,
        execution_id=(
            execution_manifest.executionId
            if execution_manifest is not None
            else None
        ),
        source_branch=source_branch,
        base_branch=base_branch,
        anchor_digests=manifest_anchor_digests(request),
        base_index_available=(
            request.indexVersion == f"rag-commit-{snapshot['base_sha']}"
        ),
        additional_gaps=extra_gaps,
    )
    return {
        "related_context_pack_v1": result.pack.model_dump(mode="json"),
    }


async def _fetch_cross_module_context(
    rag_client,
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff] = None,
) -> Stage2Context:
    snapshot = context_snapshot_v1(request)
    rag_branch, base_branch = context_branch_labels(request)
    rag_branch = rag_branch or request.commitHash or "main"
    if not rag_client:
        if snapshot is not None:
            return _stage_2_exact_context(
                request,
                chunks=[],
                source_branch=rag_branch,
                base_branch=base_branch,
                retrieval_failure="RAG client unavailable",
            )
        return ""

    try:
        if snapshot is not None:
            workspace = request.projectVcsWorkspace
            project = request.projectVcsRepoSlug
        else:
            workspace = request.projectWorkspace
            project = request.projectNamespace
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
            if snapshot is not None:
                return _stage_2_exact_context(
                    request,
                    chunks=[],
                    source_branch=rag_branch,
                    base_branch=base_branch,
                    retrieval_failure="no cross-module retrieval query could be derived",
                )
            return ""

        seen = set()
        unique_queries = []
        for q in queries:
            if q not in seen and len(q) > 10:
                seen.add(q)
                unique_queries.append(q)
        unique_queries = unique_queries[:10]

        logger.info(f"Stage 2 cross-module RAG: {len(unique_queries)} queries")

        execution_id = (
            request.executionManifest.executionId
            if request.executionManifest is not None
            else None
        )
        duplicate_call = rag_client.search_for_duplicates(
            workspace=workspace,
            project=project,
            branch=rag_branch,
            queries=unique_queries,
            top_k=6,
            base_branch=base_branch,
            snapshot=snapshot,
            execution_id=execution_id,
        )
        if snapshot is not None:
            deterministic_call = rag_client.get_deterministic_context(
                workspace=workspace,
                project=project,
                branches=[
                    branch for branch in (rag_branch, base_branch) if branch
                ],
                file_paths=changed_files,
                limit_per_file=15,
                pr_number=request.pullRequestId,
                pr_changed_files=changed_files,
                snapshot=snapshot,
                execution_id=execution_id,
            )
            dup_results, deterministic_response = await asyncio.gather(
                duplicate_call,
                deterministic_call,
            )
        else:
            dup_results = await duplicate_call
            deterministic_response = None

        if snapshot is not None:
            changed_paths = {
                str(path or "").lstrip("/") for path in changed_files if path
            }
            exact_chunks = flatten_deterministic_context(deterministic_response)
            for result in dup_results or []:
                if not isinstance(result, dict):
                    continue
                metadata = result.get("metadata")
                metadata = metadata if isinstance(metadata, dict) else {}
                path = str(
                    metadata.get("path")
                    or metadata.get("file_path")
                    or result.get("path")
                    or ""
                ).lstrip("/")
                if path in changed_paths:
                    continue
                chunk = dict(result)
                chunk["metadata"] = metadata
                chunk.setdefault("_source", "duplication")
                exact_chunks.append(chunk)
            return _stage_2_exact_context(
                request,
                chunks=exact_chunks,
                source_branch=rag_branch,
                base_branch=base_branch,
            )

        if not dup_results:
            return ""

        changed_set = set(changed_files)
        formatted = format_duplication_context(
            duplication_results=dup_results,
            batch_file_paths=list(changed_set),
            max_chunks=10,
        )

        if formatted:
            logger.info(f"Stage 2 cross-module context: {len(formatted)} chars")

        return formatted

    except Exception as e:
        logger.warning(
            "Failed to fetch cross-module context for Stage 2: error_type=%s",
            type(e).__name__,
        )
        if snapshot is not None:
            return _stage_2_exact_context(
                request,
                chunks=[],
                source_branch=rag_branch,
                base_branch=base_branch,
                retrieval_failure=str(e),
            )
        return ""
