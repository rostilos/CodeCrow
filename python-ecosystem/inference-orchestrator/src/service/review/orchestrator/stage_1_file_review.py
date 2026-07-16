"""
Stage 1: Parallel file reviews — batching, RAG context, and per-batch LLM calls.
"""
import asyncio
import inspect
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.multi_stage import ReviewPlan, FileReviewBatchOutput
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import ProcessedDiff, DiffProcessor
from utils.task_context_builder import build_task_context
from utils.dependency_graph import create_smart_batches_async

from service.review.orchestrator.agents import extract_llm_response_text
from service.review.telemetry import observed_ainvoke
from service.review.orchestrator.json_utils import parse_llm_response
from service.review.orchestrator.reconciliation import (
    issue_matches_files,
    format_previous_issues_for_batch,
)
from service.review.orchestrator.context_helpers import (
    extract_diff_snippets,
    format_rag_context,
    format_duplication_context,
)
from service.review.orchestrator.stage_helpers import (
    emit_progress,
    format_project_rules,
)
from service.review.execution_context import is_manifest_bound_v1
from service.review.coverage import ExecutionCoverageTracker

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


SEMANTIC_RAG_FILLER_ENABLED = _env_bool("REVIEW_SEMANTIC_RAG_FILLER_ENABLED", True)
DUPLICATION_RAG_ENABLED = _env_bool("REVIEW_DUPLICATION_RAG_ENABLED", True)
STAGE1_MAX_FILES_PER_BATCH = max(1, _env_int("REVIEW_STAGE1_MAX_FILES_PER_BATCH", 7))
STAGE1_BATCH_TOKEN_BUDGET = max(10_000, _env_int("REVIEW_STAGE1_BATCH_TOKEN_BUDGET", 60_000))
STAGE1_DIFF_CHUNK_TOKEN_BUDGET = max(8_000, _env_int("REVIEW_STAGE1_DIFF_CHUNK_TOKEN_BUDGET", 35_000))
# Current source is primary evidence, not optional RAG context. Keep a bounded
# copy in each Stage 1 prompt so small/medium files are reviewed as a coherent
# post-change unit while the full source remains available to verification.
STAGE1_MAX_CURRENT_FILE_CHARS = max(
    2_000,
    _env_int("REVIEW_STAGE1_MAX_CURRENT_FILE_CHARS", 12_000),
)
STAGE1_MAX_TASK_HISTORY_CHARS = max(
    1_000,
    _env_int("REVIEW_STAGE1_MAX_TASK_HISTORY_CHARS", 4_000),
)
STRUCTURED_OUTPUT_ENABLED = _env_bool("REVIEW_STRUCTURED_OUTPUT_ENABLED", True)
CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED = _env_bool("REVIEW_CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED", False)
SEMANTIC_RAG_TIMEOUT_SECONDS = max(1, _env_int("REVIEW_SEMANTIC_RAG_TIMEOUT_SECONDS", 5))
GLOBAL_RAG_FALLBACK_TIMEOUT_SECONDS = max(1, _env_int("REVIEW_GLOBAL_RAG_FALLBACK_TIMEOUT_SECONDS", 5))
DETERMINISTIC_RAG_MAX_CHUNKS = max(1, _env_int("REVIEW_DETERMINISTIC_RAG_MAX_CHUNKS", 80))
FULL_DIFF_REVIEW_FOCUS = "FULL_DIFF_REVIEW"


@dataclass
class Stage1PreparedContext:
    """Precomputed per-review indexes shared by all Stage 1 batches."""
    diff_source: Optional[ProcessedDiff] = None
    diff_by_path: Dict[str, Optional[Any]] = field(default_factory=dict)
    full_diff_by_path: Dict[str, Optional[Any]] = field(default_factory=dict)
    full_diff_raw: Optional[str] = None
    full_diff_index_loaded: bool = False
    file_content_by_path: Dict[str, Optional[str]] = field(default_factory=dict)
    enrichment_metadata_by_path: Dict[str, Optional[Any]] = field(default_factory=dict)
    task_context: str = "No task context available."


@dataclass
class Stage1RagState:
    """Per-review RAG state shared across Stage 1 batches."""
    semantic_disabled: bool = False
    semantic_failures: int = 0
    semantic_disable_reason: str = ""


class Stage1BatchFailure(RuntimeError):
    """Typed fail-closed outcome for a candidate Stage 1 batch."""

    def __init__(self, message: str, *, reason_code: str):
        super().__init__(message)
        self.reason_code = reason_code


def _path_lookup_keys(path: Optional[str]) -> List[str]:
    if not path:
        return []
    normalized = path.lstrip("/")
    keys = [normalized]
    remainder = normalized
    while "/" in remainder:
        remainder = remainder.split("/", 1)[1]
        keys.append(remainder)
    return keys


def _add_path_lookup(mapping: Dict[str, Optional[Any]], path: Optional[str], value: Any) -> None:
    for key in _path_lookup_keys(path):
        existing = mapping.get(key)
        if existing is None and key in mapping:
            continue
        if existing is not None and existing is not value:
            mapping[key] = None
        else:
            mapping[key] = value


def _lookup_by_path(mapping: Dict[str, Optional[Any]], path: Optional[str]) -> Optional[Any]:
    for key in _path_lookup_keys(path):
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _stage_1_task_context(request: ReviewRequestDto) -> str:
    current = build_task_context(
        request.taskContext,
        max_description_length=4_000,
    )
    history = (request.taskHistoryContext or "").strip()
    if len(history) > STAGE1_MAX_TASK_HISTORY_CHARS:
        history = (
            history[:STAGE1_MAX_TASK_HISTORY_CHARS]
            + "\n[Prior task history truncated for this review batch.]"
        )
    sections = [section for section in (
        current,
        f"PRIOR TASK IMPLEMENTATION CONTEXT:\n{history}" if history else None,
    ) if section]
    return "\n\n".join(sections) or "No task context available."


def _build_stage_1_prepared_context(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
    is_incremental: bool,
) -> Stage1PreparedContext:
    diff_source = processed_diff
    if is_incremental and request.deltaDiff:
        diff_source = DiffProcessor().process(request.deltaDiff)

    diff_by_path: Dict[str, Optional[Any]] = {}
    if diff_source:
        for diff_file in diff_source.files:
            _add_path_lookup(diff_by_path, diff_file.path, diff_file)

    full_diff_raw = None
    if _needs_unbounded_stage_1_diff(diff_source):
        delta_diff = getattr(request, "deltaDiff", None)
        raw_diff = delta_diff if is_incremental and delta_diff else getattr(request, "rawDiff", None)
        if raw_diff:
            full_diff_raw = raw_diff
            logger.info(
                "Stage 1 deferred unbounded raw diff parsing until explicitly requested"
            )

    enrichment_metadata_by_path: Dict[str, Optional[Any]] = {}
    if request.enrichmentData and request.enrichmentData.fileMetadata:
        for meta in request.enrichmentData.fileMetadata:
            _add_path_lookup(enrichment_metadata_by_path, meta.path, meta)

    file_content_by_path: Dict[str, Optional[str]] = {}
    if request.enrichmentData and request.enrichmentData.fileContents:
        for file_content in request.enrichmentData.fileContents:
            if file_content.content and getattr(file_content, "skipped", False) is not True:
                _add_path_lookup(
                    file_content_by_path,
                    file_content.path,
                    file_content.content,
                )

    return Stage1PreparedContext(
        diff_source=diff_source,
        diff_by_path=diff_by_path,
        full_diff_raw=full_diff_raw,
        file_content_by_path=file_content_by_path,
        enrichment_metadata_by_path=enrichment_metadata_by_path,
        task_context=_stage_1_task_context(request),
    )


def _bounded_current_file_context(content: Optional[str]) -> str:
    """Return explicitly labelled, bounded current-source evidence for Stage 1."""
    if not content:
        return "(Current file content unavailable; use the diff evidence.)"
    if len(content) <= STAGE1_MAX_CURRENT_FILE_CHARS:
        return content

    # Preserve both ends without assigning language-specific meaning to either.
    # The deterministic verification stage still receives the complete content.
    half = max(1, (STAGE1_MAX_CURRENT_FILE_CHARS - 160) // 2)
    omitted = len(content) - (half * 2)
    return (
        content[:half]
        + f"\n\n[Current file context truncated: {omitted} characters omitted]\n\n"
        + content[-half:]
    )


def _needs_unbounded_stage_1_diff(diff_source: Optional[ProcessedDiff]) -> bool:
    if not diff_source:
        return False
    for diff_file in diff_source.files:
        if _diff_limit_reason_allows_full_review(diff_file.skip_reason):
            return True
    return False


def _diff_limit_reason_allows_full_review(reason: Optional[str]) -> bool:
    reason_lower = (reason or "").lower()
    return any(
        marker in reason_lower
        for marker in (
            "file too large",
            "too many lines",
            "would exceed total size limit",
            "exceeds max files limit",
        )
    )


def _find_diff_file_for_path(
    prepared_context: Optional[Stage1PreparedContext],
    file_path: str,
    use_full_diff: bool = False,
) -> Optional[Any]:
    if not prepared_context or not prepared_context.diff_source:
        return None

    if use_full_diff:
        _ensure_full_diff_index(prepared_context)
        matched_full_diff = _lookup_by_path(prepared_context.full_diff_by_path, file_path)
        if matched_full_diff is not None:
            return matched_full_diff

    matched = _lookup_by_path(prepared_context.diff_by_path, file_path)
    if matched is not None:
        return matched

    # Collision fallback for duplicate basenames/suffixes. This keeps legacy
    # suffix semantics without making every batch scan the entire diff.
    for diff_file in prepared_context.diff_source.files:
        if diff_file.path == file_path or diff_file.path.endswith("/" + file_path):
            return diff_file
    return None


def _ensure_full_diff_index(prepared_context: Stage1PreparedContext) -> None:
    if prepared_context.full_diff_index_loaded:
        return
    prepared_context.full_diff_index_loaded = True

    raw_diff = prepared_context.full_diff_raw
    if not raw_diff:
        return

    # Stage 1 can split very large diffs into multiple bounded prompts. Parse
    # the original hunks only when Stage 0 explicitly asks for full-diff review.
    raw_diff_size = max(len(raw_diff.encode("utf-8")) + 1, 1)
    raw_diff_source = DiffProcessor(
        max_file_size=raw_diff_size,
        max_files=10_000,
        max_total_size=raw_diff_size,
        max_lines_per_file=max(raw_diff.count("\n") + 1, 1),
    ).process(raw_diff)
    for diff_file in raw_diff_source.files:
        _add_path_lookup(prepared_context.full_diff_by_path, diff_file.path, diff_file)
    logger.info(
        "Stage 1 prepared unbounded raw diff index for %d file(s)",
        len(raw_diff_source.files),
    )


def _item_requests_full_diff(item: Dict[str, Any]) -> bool:
    file_info = item.get("file")
    focus_areas = getattr(file_info, "focus_areas", None) or []
    for focus_area in focus_areas:
        normalized = str(focus_area or "").strip().upper().replace("-", "_").replace(" ", "_")
        if normalized == FULL_DIFF_REVIEW_FOCUS:
            return True
    return False


def _iter_batch_enrichment_metadata(
    request: ReviewRequestDto,
    batch_file_paths: List[str],
    prepared_context: Optional[Stage1PreparedContext],
) -> List[Any]:
    if not request.enrichmentData or not request.enrichmentData.fileMetadata:
        return []

    result: List[Any] = []
    seen: set[int] = set()
    if prepared_context:
        for path in batch_file_paths:
            meta = _lookup_by_path(prepared_context.enrichment_metadata_by_path, path)
            if meta is not None and id(meta) not in seen:
                result.append(meta)
                seen.add(id(meta))

    if len(result) >= len(batch_file_paths):
        return result

    # Collision/path-format fallback.
    for meta in request.enrichmentData.fileMetadata:
        if id(meta) in seen:
            continue
        if meta.path in batch_file_paths or any(meta.path.endswith(bp) for bp in batch_file_paths):
            result.append(meta)
            seen.add(id(meta))

    return result


def _format_batch_metadata_json(batch_metadata: List[Any]) -> str:
    if not batch_metadata:
        return ""

    metadata_payload = [_metadata_to_payload(meta) for meta in batch_metadata]
    return json.dumps(metadata_payload, ensure_ascii=False, indent=2, default=str)


def _metadata_to_payload(meta: Any) -> Dict[str, Any]:
    if hasattr(meta, "model_dump"):
        return meta.model_dump(mode="json", by_alias=False, exclude_none=True)
    if isinstance(meta, dict):
        return {
            key: value
            for key, value in meta.items()
            if value is not None
        }
    return {
        key: value
        for key, value in vars(meta).items()
        if not key.startswith("_") and value is not None
    }


def _extract_metadata_identifiers(batch_metadata: List[Any], limit: int = 200) -> Optional[List[str]]:
    """
    Collect raw string identifiers from parser metadata without assigning
    meaning to specific metadata fields.
    """
    seen = set()
    identifiers: List[str] = []

    def visit(value: Any) -> None:
        if len(identifiers) >= limit or value is None:
            return
        if isinstance(value, str):
            text = value.strip()
            if text and text not in seen:
                seen.add(text)
                identifiers.append(text)
            return
        if isinstance(value, dict):
            for nested in value.values():
                visit(nested)
            return
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                visit(nested)
            return

    for meta in batch_metadata:
        visit(_metadata_to_payload(meta))

    return identifiers or None


def _unwrap_rag_context(response: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    context = response.get("context")
    if isinstance(context, dict):
        return context
    return response


def _flatten_deterministic_context(
    deterministic_response: Optional[Dict[str, Any]],
    max_chunks: int = DETERMINISTIC_RAG_MAX_CHUNKS,
) -> List[Dict[str, Any]]:
    """
    Flatten all deterministic RAG evidence into prompt chunks.

    The RAG API returns several grouped views. Stage 1 should not silently drop
    any of those groups; semantic interpretation remains with the LLM.
    """
    det_context = _unwrap_rag_context(deterministic_response)
    if not det_context:
        return []

    flattened: List[Dict[str, Any]] = []
    seen = set()

    def add_chunk(chunk: Any, source_group: str, group_key: str = "") -> None:
        if len(flattened) >= max_chunks or not isinstance(chunk, dict):
            return
        text = chunk.get("text") or chunk.get("content") or ""
        metadata = chunk.get("metadata") or {}
        path = metadata.get("path") or chunk.get("path") or chunk.get("file_path") or ""
        content_key = (path, hash(str(text)[:500]))
        if content_key in seen:
            return
        seen.add(content_key)

        merged = dict(chunk)
        merged.setdefault("text", text)
        merged.setdefault("content", text)
        merged.setdefault("metadata", metadata)
        merged.setdefault("file_path", path)
        merged.setdefault("path", path)
        merged.setdefault("score", _deterministic_score(source_group))
        merged["_source"] = "deterministic"
        merged["_match_type"] = source_group
        if group_key:
            merged["definition_name"] = group_key
        flattened.append(merged)

    grouped_sources = (
        ("changed_file", det_context.get("changed_files", {})),
        ("definition", det_context.get("related_definitions", {})),
        ("class_context", det_context.get("class_context", {})),
        ("namespace_context", det_context.get("namespace_context", {})),
    )
    for source_group, grouped in grouped_sources:
        if isinstance(grouped, dict):
            for group_key, chunks in grouped.items():
                for chunk in chunks or []:
                    add_chunk(chunk, source_group, str(group_key))

    for chunk in det_context.get("chunks", []) or []:
        match_type = (
            chunk.get("_match_type") or "deterministic"
            if isinstance(chunk, dict)
            else "deterministic"
        )
        add_chunk(chunk, match_type)

    return flattened


def _deterministic_score(source_group: str) -> float:
    if source_group in {"definition", "transitive_parent"}:
        return 0.95
    if source_group in {"changed_file", "class_context"}:
        return 0.92
    if source_group == "namespace_context":
        return 0.86
    return 0.84


def _supports_structured_output(llm) -> bool:
    if not STRUCTURED_OUTPUT_ENABLED:
        return False
    if CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED:
        return True

    cls = llm.__class__
    class_names = {getattr(c, "__name__", "") for c in cls.mro()}
    if "ChatCloudflareOpenAI" in class_names:
        return False
    return True


def _positive_int_or_default(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


# ── Batching ──────────────────────────────────────────────────


def chunk_files(file_groups: List[Any], max_files_per_batch: int = 5) -> List[List[Dict[str, Any]]]:
    all_files = []
    for group in file_groups:
        for f in group.files:
            all_files.append({"file": f, "priority": group.priority})
    return [all_files[i:i + max_files_per_batch] for i in range(0, len(all_files), max_files_per_batch)]


async def create_smart_batches_wrapper(
    file_groups: List[Any],
    processed_diff: Optional[ProcessedDiff],
    request: ReviewRequestDto,
    rag_client,
    max_files_per_batch: int = 15,
) -> List[List[Dict[str, Any]]]:
    manifest_bound = is_manifest_bound_v1(request)
    if manifest_bound:
        # These VCS coordinates are already checked against repositoryId by
        # the v1 DTO. The internal project aliases are not manifest inputs.
        workspace = request.projectVcsWorkspace
        project = request.projectVcsRepoSlug
        rag_client = None
    else:
        workspace = request.projectWorkspace
        project = request.projectNamespace

    branches = []
    rag_branch = request.get_rag_branch()
    base_branch = request.get_rag_base_branch()
    if rag_branch:
        branches.append(rag_branch)
    if base_branch and base_branch not in branches:
        branches.append(base_branch)
    if not branches:
        branches = ['main', 'master']

    enrichment_data = getattr(request, 'enrichmentData', None)

    try:
        # Keep Stage 1 prompts latency-sized instead of filling the model window.
        # This does not reduce coverage: every file is still reviewed, but large
        # PRs are split into more independently parallelizable batches.
        max_tokens = _positive_int_or_default(getattr(request, "maxAllowedTokens", None), 200000)
        model_safe_limit = max(10_000, max_tokens - 20_000)
        batch_token_limit = min(model_safe_limit, STAGE1_BATCH_TOKEN_BUDGET)
        if batch_token_limit < model_safe_limit:
            logger.info(
                "Stage 1 batch token budget capped at %d tokens "
                "(model-safe limit=%d, env REVIEW_STAGE1_BATCH_TOKEN_BUDGET)",
                batch_token_limit,
                model_safe_limit,
            )

        batches = await create_smart_batches_async(
            file_groups=file_groups,
            workspace=workspace,
            project=project,
            branches=branches,
            rag_client=rag_client,
            max_batch_size=max_files_per_batch,
            enrichment_data=enrichment_data,
            max_allowed_tokens=batch_token_limit,
            processed_diff=processed_diff,
        )
        total_files = sum(len(b) for b in batches)
        related_files = sum(1 for b in batches for f in b if f.get('has_relationships'))
        enrichment_source = "enrichment data" if enrichment_data else "RAG discovery"
        logger.info(
            f"Smart batching ({enrichment_source}): {total_files} files in "
            f"{len(batches)} batches, {related_files} files have cross-file relationships"
        )
        return batches
    except Exception as e:
        logger.warning(f"Smart batching failed, falling back to simple batching: {e}")
        return chunk_files(file_groups, max_files_per_batch)


def _split_hunk_by_lines(hunk: str, max_chars: int) -> List[str]:
    if len(hunk) <= max_chars:
        return [hunk]

    lines = hunk.splitlines(keepends=True)
    hunk_header = lines[0] if lines[0].startswith("@@ ") else ""
    body_lines = lines[1:] if hunk_header else lines
    chunks: List[str] = []
    current = hunk_header

    for line in body_lines:
        if current != hunk_header and len(current) + len(line) > max_chars:
            chunks.append(current)
            current = hunk_header + line
        else:
            current += line

    if current.strip():
        chunks.append(current)

    return chunks or [hunk]


def _chunk_diff_preserving_hunks(diff_content: str, max_tokens: int) -> List[str]:
    if not diff_content:
        return [diff_content]

    max_chars = max(1, max_tokens * 4)
    if len(diff_content) <= max_chars:
        return [diff_content]

    lines = diff_content.splitlines(keepends=True)
    header_lines: List[str] = []
    hunks: List[str] = []
    current_hunk: List[str] = []

    for line in lines:
        if line.startswith("@@ "):
            if current_hunk:
                hunks.append("".join(current_hunk))
            current_hunk = [line]
        elif current_hunk:
            current_hunk.append(line)
        else:
            header_lines.append(line)

    if current_hunk:
        hunks.append("".join(current_hunk))

    header = "".join(header_lines)
    body_budget = max(1, max_chars - len(header))
    if not hunks:
        chunks: List[str] = []
        current = ""
        for line in lines:
            if current and len(current) + len(line) > max_chars:
                chunks.append(current)
                current = line
            else:
                current += line
        chunks.append(current)
        return chunks

    normalized_hunks: List[str] = []
    for hunk in hunks:
        normalized_hunks.extend(_split_hunk_by_lines(hunk, body_budget))

    chunks: List[str] = []
    current = ""
    for hunk in normalized_hunks:
        if current and len(header) + len(current) + len(hunk) > max_chars:
            chunks.append(header + current)
            current = hunk
        else:
            current += hunk

    chunks.append(header + current)
    return chunks


def _expand_oversized_diff_batches(
    batches: List[List[Dict[str, Any]]],
    prepared_context: Stage1PreparedContext,
    diff_chunk_token_budget: int = STAGE1_DIFF_CHUNK_TOKEN_BUDGET,
) -> List[List[Dict[str, Any]]]:
    expanded_batches: List[List[Dict[str, Any]]] = []
    split_files = 0
    added_segments = 0

    for batch in batches:
        current_batch: List[Dict[str, Any]] = []

        for item in batch:
            file_info = item.get("file")
            file_path = getattr(file_info, "path", "")
            diff_file = _find_diff_file_for_path(
                prepared_context,
                file_path,
                use_full_diff=_item_requests_full_diff(item),
            )
            diff_content = diff_file.content if diff_file else ""
            chunks = _chunk_diff_preserving_hunks(diff_content, diff_chunk_token_budget)

            if len(chunks) <= 1:
                current_batch.append(item)
                continue

            if current_batch:
                expanded_batches.append(current_batch)
                current_batch = []

            split_files += 1
            added_segments += len(chunks)
            for idx, chunk in enumerate(chunks, start=1):
                segment_item = dict(item)
                segment_item["_diff_override"] = chunk
                segment_item["_diff_chunk_index"] = idx
                segment_item["_diff_chunk_total"] = len(chunks)
                expanded_batches.append([segment_item])

        if current_batch:
            expanded_batches.append(current_batch)

    if split_files:
        logger.info(
            "Stage 1 split %d oversized file diff(s) into %d hunk-preserving segment batch(es)",
            split_files,
            added_segments,
        )

    return expanded_batches


# ── RAG Context ───────────────────────────────────────────────

async def fetch_batch_rag_context(
    rag_client,
    request: ReviewRequestDto,
    batch_file_paths: List[str],
    batch_diff_snippets: List[str],
    pr_indexed: bool = False,
    llm_reranker=None,
    use_llm_rerank: bool = True,
    batch_priority: str = "MEDIUM",
    enrichment_identifiers: Optional[List[str]] = None,
    batch_raw_diffs: Optional[List[str]] = None,
    rag_state: Optional[Stage1RagState] = None,
) -> Optional[Dict[str, Any]]:
    if is_manifest_bound_v1(request) or not rag_client:
        return None

    try:
        rag_branch = request.get_rag_branch() or request.commitHash or "main"
        base_branch = request.get_rag_base_branch()

        # Scale top_k based on batch priority to ensure adequate context
        priority_upper = (batch_priority or "MEDIUM").upper()
        top_k = {"HIGH": 15, "MEDIUM": 10, "LOW": 8}.get(priority_upper, 10)

        logger.info(f"Fetching per-batch RAG context for {len(batch_file_paths)} files "
                     f"(priority={priority_upper}, top_k={top_k})")

        pr_number = request.pullRequestId if pr_indexed else None
        all_pr_files = request.changedFiles if pr_indexed else None

        context = None

        async def _fetch_deterministic_context() -> Optional[Dict[str, Any]]:
            try:
                return await rag_client.get_deterministic_context(
                    workspace=request.projectWorkspace,
                    project=request.projectNamespace,
                    branches=[branch for branch in [rag_branch, base_branch] if branch],
                    file_paths=batch_file_paths,
                    limit_per_file=5,
                    pr_number=pr_number,
                    pr_changed_files=all_pr_files,
                    additional_identifiers=enrichment_identifiers,
                )
            except Exception as det_err:
                logger.debug(f"Deterministic RAG lookup failed: {det_err}")
                return None

        async def _fetch_semantic_context() -> Optional[Dict[str, Any]]:
            if not SEMANTIC_RAG_FILLER_ENABLED:
                logger.info("Semantic RAG filler skipped by REVIEW_SEMANTIC_RAG_FILLER_ENABLED")
                return None

            semantic_top_k = min(top_k, 8)
            logger.info(
                f"Semantic RAG filler: prefetching up to {semantic_top_k} chunks "
                f"(target={top_k})"
            )
            # Let provider failures reach the bounded wait below so the shared
            # per-review state disables a failing semantic filler for subsequent
            # batches. Swallowing the error here caused every batch to retry a
            # provider that was already known to be unavailable.
            return await rag_client.get_pr_context(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                branch=rag_branch,
                changed_files=batch_file_paths,
                diff_snippets=batch_diff_snippets,
                pr_title=request.prTitle,
                pr_description=request.prDescription,
                top_k=semantic_top_k,
                base_branch=base_branch,
                pr_number=pr_number,
                all_pr_changed_files=all_pr_files,
                deleted_files=request.deletedFiles or None,
            )

        async def _fetch_duplication_context() -> Optional[List[Dict[str, Any]]]:
            if not DUPLICATION_RAG_ENABLED:
                logger.info("Duplication search skipped by REVIEW_DUPLICATION_RAG_ENABLED")
                return None

            try:
                # Build per-file enrichment metadata for duplication queries.
                # Pass the full parser payload through; do not select semantic
                # fields in Python.
                enrichment_metadata = None
                if request.enrichmentData and request.enrichmentData.fileMetadata:
                    enrichment_metadata = {}
                    for meta in request.enrichmentData.fileMetadata:
                        if meta.path in batch_file_paths or any(
                            meta.path.endswith(bp) or bp.endswith(meta.path) for bp in batch_file_paths
                        ):
                            enrichment_metadata[meta.path] = _metadata_to_payload(meta)

                duplication_queries = _build_duplication_queries_from_diff(
                    batch_diff_snippets, batch_file_paths,
                    enrichment_metadata=enrichment_metadata,
                )

                if not duplication_queries:
                    return None

                return await rag_client.search_for_duplicates(
                    workspace=request.projectWorkspace,
                    project=request.projectNamespace,
                    branch=rag_branch,
                    queries=duplication_queries,
                    top_k=8,
                    base_branch=base_branch,
                )
            except Exception as dup_err:
                logger.debug(f"Duplication search skipped: {dup_err}")
                return None

        deterministic_task = asyncio.create_task(_fetch_deterministic_context())
        duplication_task = asyncio.create_task(_fetch_duplication_context())

        # 1. Deterministic lookup FIRST — structural deps are highest-value context
        deterministic_response = await deterministic_task
        deterministic_chunks = _flatten_deterministic_context(deterministic_response)
        if deterministic_chunks:
            context = {"relevant_code": deterministic_chunks}
            logger.info(
                "Deterministic RAG: included %d chunk(s) from all deterministic context groups",
                len(deterministic_chunks),
            )

        # 2. Semantic search as FILLER — only fills remaining budget after deterministic
        det_count = len(context.get("relevant_code", [])) if context else 0
        semantic_fill = max(0, top_k - det_count)

        rag_response = None
        if semantic_fill > 0 and rag_state and rag_state.semantic_disabled:
            logger.info("Semantic RAG filler skipped: %s", rag_state.semantic_disable_reason)
        elif semantic_fill > 0:
            try:
                rag_response = await asyncio.wait_for(
                    _fetch_semantic_context(),
                    timeout=SEMANTIC_RAG_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                if rag_state:
                    rag_state.semantic_failures += 1
                    rag_state.semantic_disabled = True
                    rag_state.semantic_disable_reason = (
                        f"timed out after {SEMANTIC_RAG_TIMEOUT_SECONDS}s"
                    )
                logger.warning(
                    "Semantic RAG filler timed out after %ss; disabling for remaining Stage 1 batches",
                    SEMANTIC_RAG_TIMEOUT_SECONDS,
                )
            except Exception as sem_err:
                if rag_state:
                    rag_state.semantic_failures += 1
                    rag_state.semantic_disabled = True
                    rag_state.semantic_disable_reason = str(sem_err)
                logger.warning("Semantic RAG filler failed; disabling for remaining Stage 1 batches: %s", sem_err)

        if semantic_fill > 0 and rag_response:
            sem_context = _unwrap_rag_context(rag_response)
            sem_chunks = sem_context.get("relevant_code", [])
            if context is None:
                context = {"relevant_code": []}
            added = 0
            for chunk in sem_chunks:
                if added >= semantic_fill:
                    break
                context["relevant_code"].append(chunk)
                added += 1
            logger.info(f"Semantic RAG: added {added}/{len(sem_chunks)} chunks")
        elif semantic_fill > 0:
            logger.info("Semantic RAG filler produced no chunks")
        else:
            logger.info(f"Deterministic yielded {det_count} chunks — semantic search skipped")

        # 3. Duplication search
        dup_results = await duplication_task
        if dup_results:
            if context is None:
                context = {"relevant_code": []}

            dup_added = 0
            seen_paths = {
                existing.get("file_path", existing.get("path", ""))
                for existing in context.get("relevant_code", [])
                if existing.get("file_path", existing.get("path", ""))
            }

            for dup in dup_results:
                dup_path = dup.get("metadata", {}).get("path", "")
                dup_text = dup.get("text", "")

                if dup_path in batch_file_paths or not dup_text:
                    continue
                if dup_path in seen_paths:
                    continue
                seen_paths.add(dup_path)

                context["relevant_code"].append({
                    "file_path": dup_path,
                    "text": dup_text,
                    "content": dup_text,
                    "score": max(dup.get("score", 0.8), 0.80),
                    "_source": "duplication",
                    "metadata": dup.get("metadata", {}),
                    "_query": dup.get("_query", ""),
                })
                dup_added += 1
                if dup_added >= 5:
                    break

            if dup_added > 0:
                logger.info(f"Duplication search: added {dup_added} similar implementation chunks")

        if context:
            total_chunks = len(context.get("relevant_code", []))

            if pr_indexed and all_pr_files:
                context["relevant_code"] = _deduplicate_pr_stale_chunks(
                    context.get("relevant_code", []),
                    pr_changed_files=all_pr_files,
                    batch_file_paths=batch_file_paths,
                )
                deduped_count = total_chunks - len(context.get("relevant_code", []))
                if deduped_count > 0:
                    logger.info(f"Post-merge dedup: removed {deduped_count} stale branch chunks")
                total_chunks = len(context.get("relevant_code", []))

            logger.info(f"Total RAG context: {total_chunks} chunks for files {batch_file_paths}")

            if llm_reranker and total_chunks > 0:
                try:
                    chunks = context.get("relevant_code", [])
                    if not use_llm_rerank:
                        logger.info("Fast check: using structural per-batch RAG ordering")
                    reranked, rerank_result = await llm_reranker.rerank(
                        chunks,
                        pr_title=request.prTitle,
                        pr_description=request.prDescription,
                        changed_files=request.changedFiles,
                        use_llm=use_llm_rerank,
                    )
                    context["relevant_code"] = reranked
                    logger.info(
                        f"Per-batch reranking: {rerank_result.method} "
                        f"({rerank_result.processing_time_ms:.0f}ms, "
                        f"{rerank_result.original_count}→{rerank_result.reranked_count} chunks)"
                    )
                except Exception as rerank_err:
                    logger.warning(f"Per-batch reranking failed (non-critical): {rerank_err}")

            return context

        return None

    except Exception as e:
        logger.warning(f"Failed to fetch per-batch RAG context: {e}")
        return None


def _deduplicate_pr_stale_chunks(
    chunks: List[Dict[str, Any]],
    pr_changed_files: List[str],
    batch_file_paths: List[str],
) -> List[Dict[str, Any]]:
    if not chunks or not pr_changed_files:
        return chunks

    pr_changed_set = set()
    for f in pr_changed_files:
        normalized = f.lstrip("/")
        pr_changed_set.add(normalized)
        if "/" in normalized:
            pr_changed_set.add(normalized.rsplit("/", 1)[-1])

    batch_set = set()
    for f in batch_file_paths:
        normalized = f.lstrip("/")
        batch_set.add(normalized)
        if "/" in normalized:
            batch_set.add(normalized.rsplit("/", 1)[-1])

    by_path: Dict[str, List[Dict[str, Any]]] = {}
    for chunk in chunks:
        metadata = chunk.get("metadata", {})
        path = (metadata.get("path") or chunk.get("path") or
                chunk.get("file_path", "")).lstrip("/")
        if not path:
            path = "__unknown__"
        by_path.setdefault(path, []).append(chunk)

    result = []
    for path, path_chunks in by_path.items():
        path_basename = path.rsplit("/", 1)[-1] if "/" in path else path

        is_pr_file = (path in pr_changed_set or path_basename in pr_changed_set)
        is_batch_file = (path in batch_set or path_basename in batch_set)

        if not is_pr_file or is_batch_file:
            result.extend(path_chunks)
            continue

        pr_chunks = [c for c in path_chunks if c.get("_source") == "pr_indexed"]
        non_pr_chunks = [c for c in path_chunks if c.get("_source") != "pr_indexed"]

        if pr_chunks and non_pr_chunks:
            result.extend(pr_chunks)
            logger.info(
                f"Dedup: replaced {len(non_pr_chunks)} stale branch chunk(s) "
                f"with {len(pr_chunks)} PR-indexed chunk(s) for {path}"
            )
        elif pr_chunks:
            result.extend(pr_chunks)
        else:
            for c in non_pr_chunks:
                c["_potentially_stale"] = True
            result.extend(non_pr_chunks)

    return result


def _build_duplication_queries_from_diff(
    diff_snippets: List[str],
    file_paths: List[str],
    enrichment_metadata: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    Build duplication-oriented retrieval queries without semantic hardcoding.

    Metadata and diff snippets are passed through as structured/raw evidence.
    The retrieval layer and LLM decide whether the content indicates duplicate
    behavior; Python does not infer classes/functions/events/tables here.
    """
    queries = []
    seen = set()

    def _add(q: str):
        q = q.strip()
        if q and len(q) > 10 and q not in seen:
            seen.add(q)
            queries.append(q)

    if enrichment_metadata:
        for fp, meta in enrichment_metadata.items():
            payload = {"path": fp, "metadata": meta}
            _add("duplicate search structured metadata:\n" + json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ))

    for snippet in diff_snippets or []:
        _add("duplicate search diff evidence:\n" + snippet)

    return list(queries)[:10]


def _scope_deterministic_to_diff(
    related_defs: Dict[str, List[Dict]],
    batch_diff_snippets: List[str],
    batch_raw_diffs: Optional[List[str]] = None,
    max_per_def: int = 2,
    max_file_level: int = 2,
) -> List[Dict]:
    """
    Flatten deterministic definition chunks without semantic token filtering.

    Deterministic RAG has already selected related definitions. This function
    only normalizes/caps provider output; it does not decide relevance from
    hardcoded keywords, filename labels, or regex-derived token matches.

    Returns list of chunk dicts with added keys:
        _def_name: str — the definition name this chunk belongs to
        _diff_relevant: bool — compatibility flag, always True here
    """
    if not related_defs:
        return []

    scoped = []

    for def_name, def_chunks in related_defs.items():
        for chunk in def_chunks[:max_per_def]:
            annotated = dict(chunk)
            annotated["_def_name"] = def_name
            annotated["_diff_relevant"] = True
            scoped.append(annotated)

    logger.info(
        "Deterministic RAG scope: normalized %d chunk(s) from %d definition(s) without keyword filtering",
        len(scoped),
        len(related_defs),
    )

    return scoped


# ── Batch Review ──────────────────────────────────────────────


async def execute_stage_1_file_reviews(
    llm,
    request: ReviewRequestDto,
    plan: ReviewPlan,
    rag_client,
    rag_context: Optional[Dict[str, Any]] = None,
    processed_diff: Optional[ProcessedDiff] = None,
    is_incremental: bool = False,
    max_parallel: int = 5,
    event_callback: Optional[Callable[[Dict], None]] = None,
    pr_indexed: bool = False,
    llm_reranker=None,
    use_llm_rerank: bool = True,
    fallback_llm=None,
    coverage_tracker: Optional[ExecutionCoverageTracker] = None,
) -> List[CodeReviewIssue]:
    prepared_context = _build_stage_1_prepared_context(request, processed_diff, is_incremental)
    manifest_bound = is_manifest_bound_v1(request)
    rag_state = Stage1RagState()
    batches = await create_smart_batches_wrapper(
        file_groups=plan.file_groups,
        processed_diff=prepared_context.diff_source,
        request=request,
        rag_client=rag_client,
        max_files_per_batch=STAGE1_MAX_FILES_PER_BATCH,
    )
    batches = _expand_oversized_diff_batches(batches, prepared_context)
    if coverage_tracker is not None:
        coverage_tracker.bind_batches(batches)

    total_review_units = sum(len(batch) for batch in batches)
    unique_file_paths = {
        item["file"].path
        for batch in batches
        for item in batch
        if item.get("file") is not None
    }
    total_files = len(unique_file_paths)
    related_batches = sum(1 for b in batches if any(f.get('has_relationships') for f in b))
    logger.info(
        f"Stage 1: Processing {total_files} files as {total_review_units} review units "
        f"in {len(batches)} batches "
        f"({related_batches} batches with cross-file relationships)"
    )

    all_issues: List[CodeReviewIssue] = []
    if not batches:
        logger.info("Stage 1 Complete: no batches to review")
        return all_issues

    max_parallel = max(1, max_parallel)
    semaphore = asyncio.Semaphore(max_parallel)
    started_at = time.time()
    batch_results: Dict[int, List[CodeReviewIssue]] = {}
    completed_batches = 0

    logger.info(
        "Stage 1: scheduling %d batches with bounded concurrency=%d",
        len(batches),
        max_parallel,
    )

    async def _run_batch(batch_idx: int, batch: List[Dict[str, Any]]) -> tuple[int, List[CodeReviewIssue]]:
        async with semaphore:
            batch_paths = [item["file"].path for item in batch]
            coverage_anchor_ids = sorted({
                anchor_id
                for item in batch
                for anchor_id in item.get("_coverage_anchor_ids", [])
            })
            has_rels = any(item.get('has_relationships') for item in batch)
            logger.debug(f"Batch {batch_idx}: {batch_paths} (cross-file relationships: {has_rels})")
            try:
                result = await _review_batch_with_timing(
                    batch_idx, llm, request, batch, rag_client, prepared_context,
                    is_incremental, rag_context, pr_indexed,
                    llm_reranker=llm_reranker,
                    use_llm_rerank=use_llm_rerank,
                    fallback_llm=fallback_llm,
                    rag_state=rag_state,
                    fail_closed=manifest_bound or coverage_tracker is not None,
                )
            except Exception:
                if coverage_tracker is not None:
                    coverage_tracker.mark_batch_failed(
                        coverage_anchor_ids,
                        reason_code="stage1_batch_failed",
                    )
                raise
            if coverage_tracker is not None:
                # A valid empty model response is still affirmative evidence
                # that every anchor assigned to this batch was examined.
                coverage_tracker.mark_batch_examined(coverage_anchor_ids)
            return batch_idx, result

    tasks = [
        asyncio.create_task(_run_batch(batch_idx, batch))
        for batch_idx, batch in enumerate(batches, start=1)
    ]

    for completed_task in asyncio.as_completed(tasks):
        try:
            batch_num, res = await completed_task
            batch_results[batch_num] = res or []
            if res:
                logger.info(f"Batch {batch_num} completed: {len(res)} issues found")
            else:
                logger.info(f"Batch {batch_num} completed: no issues found")
        except Exception as exc:
            logger.error(f"Error reviewing Stage 1 batch: {exc}")
            if manifest_bound:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
        finally:
            completed_batches += 1
            progress = 10 + int((completed_batches / len(batches)) * 50)
            emit_progress(
                event_callback,
                progress,
                f"Stage 1: Reviewed {completed_batches}/{len(batches)} batches",
            )

    for batch_idx in range(1, len(batches) + 1):
        all_issues.extend(batch_results.get(batch_idx, []))

    elapsed = time.time() - started_at
    logger.info(
        f"Stage 1 Complete: {len(all_issues)} issues found across "
        f"{total_files} files in {elapsed:.2f}s"
    )
    return all_issues


async def _review_batch_with_timing(
    batch_idx: int,
    llm,
    request: ReviewRequestDto,
    batch: List[Dict[str, Any]],
    rag_client,
    prepared_context: Optional[Stage1PreparedContext],
    is_incremental: bool,
    fallback_rag_context: Optional[Any],
    pr_indexed: bool,
    llm_reranker=None,
    use_llm_rerank: bool = True,
    fallback_llm=None,
    rag_state: Optional[Stage1RagState] = None,
    fail_closed: bool = False,
) -> List[CodeReviewIssue]:
    start_time = time.time()
    batch_paths = [item["file"].path for item in batch]
    logger.info(f"[Batch {batch_idx}] STARTED - files: {batch_paths}")

    try:
        result = await review_file_batch(
            llm, request, batch, rag_client, prepared_context, is_incremental,
            fallback_rag_context=fallback_rag_context, pr_indexed=pr_indexed,
            llm_reranker=llm_reranker,
            use_llm_rerank=use_llm_rerank,
            fallback_llm=fallback_llm,
            rag_state=rag_state,
            fail_closed=fail_closed,
        )
        elapsed = time.time() - start_time
        logger.info(f"[Batch {batch_idx}] FINISHED in {elapsed:.2f}s - {len(result)} issues")
        return result
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"[Batch {batch_idx}] FAILED after {elapsed:.2f}s: {e}")
        raise


async def review_file_batch(
    llm,
    request: ReviewRequestDto,
    batch_items: List[Dict[str, Any]],
    rag_client,
    prepared_context: Optional[Stage1PreparedContext] = None,
    is_incremental: bool = False,
    fallback_rag_context: Optional[Any] = None,
    pr_indexed: bool = False,
    llm_reranker=None,
    use_llm_rerank: bool = True,
    fallback_llm=None,
    rag_state: Optional[Stage1RagState] = None,
    fail_closed: bool = False,
) -> List[CodeReviewIssue]:
    batch_files_data = []
    batch_file_paths = []
    batch_diff_snippets = []
    batch_raw_diffs = []

    if prepared_context is not None and not isinstance(prepared_context, Stage1PreparedContext):
        # Backwards compatibility for older direct callers/tests that pass
        # ProcessedDiff as the fifth positional argument.
        prepared_context = _build_stage_1_prepared_context(request, prepared_context, is_incremental)
    elif prepared_context is None:
        prepared_context = _build_stage_1_prepared_context(request, None, is_incremental)

    for item in batch_items:
        file_info = item["file"]
        batch_file_paths.append(file_info.path)
        current_file_content = _lookup_by_path(
            prepared_context.file_content_by_path,
            file_info.path,
        )

        file_diff = item.get("_diff_override") or ""
        if not file_diff:
            diff_file = _find_diff_file_for_path(
                prepared_context,
                file_info.path,
                use_full_diff=_item_requests_full_diff(item),
            )
            if diff_file:
                file_diff = diff_file.content
        if file_diff:
            chunk_total = int(item.get("_diff_chunk_total") or 0)
            if chunk_total > 1:
                chunk_index = int(item.get("_diff_chunk_index") or 1)
                file_diff = (
                    f"[Large diff segment {chunk_index}/{chunk_total} for {file_info.path}. "
                    "All segments are reviewed independently and merged after Stage 1.]\n"
                    f"{file_diff}"
            )
            batch_diff_snippets.extend(extract_diff_snippets(file_diff))
            batch_raw_diffs.append(file_diff)

        batch_files_data.append({
            "path": file_info.path,
            "type": "MODIFIED",
            "focus_areas": file_info.focus_areas,
            "current_code": _bounded_current_file_context(current_file_content),
            "diff": file_diff or "(Diff unavailable)",
            "is_incremental": is_incremental,
        })

    project_rules = format_project_rules(request.projectRules, batch_file_paths)

    # ── Extract neutral metadata identifiers for targeted RAG queries ──
    # The parser metadata is passed to the LLM in full below. For retrieval, use
    # raw string values from the same payload without field-specific semantics.
    enrichment_identifiers: Optional[List[str]] = None
    batch_metadata = _iter_batch_enrichment_metadata(request, batch_file_paths, prepared_context)
    if batch_metadata:
        enrichment_identifiers = _extract_metadata_identifiers(batch_metadata)
        if enrichment_identifiers:
            logger.info(
                f"Metadata identifiers for batch retrieval: {len(enrichment_identifiers)}"
            )

    rag_context_text = ""
    batch_rag_context = None

    if rag_client:
        batch_rag_context = await fetch_batch_rag_context(
            rag_client, request, batch_file_paths, batch_diff_snippets, pr_indexed,
            llm_reranker=llm_reranker,
            use_llm_rerank=use_llm_rerank,
            batch_priority=batch_items[0]["priority"] if batch_items else "MEDIUM",
            enrichment_identifiers=enrichment_identifiers,
            batch_raw_diffs=batch_raw_diffs,
            rag_state=rag_state,
        )

    if _rag_context_has_chunks(batch_rag_context):
        logger.info(f"Using per-batch RAG context for: {batch_file_paths}")
        rag_context_text = format_rag_context(
            batch_rag_context,
            set(batch_file_paths),
            pr_changed_files=request.changedFiles,
            deleted_files=request.deletedFiles,
        )
    else:
        resolved_fallback_rag_context = await _resolve_fallback_rag_context(
            fallback_rag_context
        )
        if resolved_fallback_rag_context:
            scoped_fallback_rag_context = _scope_fallback_rag_context_to_batch(
                resolved_fallback_rag_context,
                batch_file_paths,
            )
        else:
            scoped_fallback_rag_context = None

        if scoped_fallback_rag_context:
            scoped_context = _unwrap_rag_context(scoped_fallback_rag_context)
            scoped_chunks = scoped_context.get("relevant_code") or scoped_context.get("chunks") or []
            logger.info(
                f"Using batch-scoped fallback RAG context for batch: {batch_file_paths} "
                f"({len(scoped_chunks)} chunks)"
            )
            rag_context_text = format_rag_context(
                scoped_fallback_rag_context,
                set(batch_file_paths),
                pr_changed_files=request.changedFiles,
                deleted_files=request.deletedFiles,
            )

    logger.info(f"RAG context for batch: {len(rag_context_text)} chars")

    previous_issues_for_batch = ""
    has_previous_issues = request.previousCodeAnalysisIssues and len(request.previousCodeAnalysisIssues) > 0
    if has_previous_issues:
        relevant_prev_issues = [
            issue for issue in request.previousCodeAnalysisIssues
            if issue_matches_files(issue, batch_file_paths)
        ]
        if relevant_prev_issues:
            previous_issues_for_batch = format_previous_issues_for_batch(relevant_prev_issues)

    file_metadata_text = _format_batch_metadata_json(batch_metadata)
    if not file_metadata_text:
        logger.debug(f"No structured parser metadata for batch {batch_file_paths}")

    prompt = PromptBuilder.build_stage_1_batch_prompt(
        files=batch_files_data,
        priority=batch_items[0]["priority"] if batch_items else "MEDIUM",
        project_rules=project_rules,
        file_outlines=file_metadata_text,
        rag_context=rag_context_text,
        is_incremental=is_incremental,
        previous_issues=previous_issues_for_batch,
        all_pr_files=request.changedFiles,
        deleted_files=request.deletedFiles,
        task_context=prepared_context.task_context,
        pr_title=request.prTitle or "",
        pr_description=request.prDescription or "",
        pr_author=request.prAuthor or "",
    )

    issues = await _invoke_stage_1_batch_llm(llm, prompt, batch_file_paths, label="capped")
    if issues is not None:
        return issues

    if fallback_llm is not None and fallback_llm is not llm:
        logger.warning(
            "Stage 1 batch failed with capped LLM for %s; retrying without output cap",
            batch_file_paths,
        )
        issues = await _invoke_stage_1_batch_llm(
            fallback_llm,
            prompt,
            batch_file_paths,
            label="uncapped retry",
        )
        if issues is not None:
            return issues

    logger.error(
        "Batch review parse failure for %s after capped%s attempts. "
        "Zero issues will be reported for this batch.",
        batch_file_paths,
        " and uncapped" if fallback_llm is not None and fallback_llm is not llm else "",
    )
    if fail_closed:
        raise Stage1BatchFailure(
            f"Stage 1 response was invalid for {batch_file_paths}",
            reason_code="stage1_response_invalid",
        )
    return []


async def _resolve_fallback_rag_context(fallback_rag_context: Optional[Any]) -> Optional[Dict[str, Any]]:
    """
    Resolve a global RAG fallback only if a batch needs it.

    Per-batch RAG is the primary path. The fallback can be a normal context dict
    or an asyncio Task started earlier so its latency is hidden behind planning
    and PR indexing.
    """
    if not fallback_rag_context:
        return None
    if isinstance(fallback_rag_context, dict):
        return fallback_rag_context
    if inspect.isawaitable(fallback_rag_context):
        try:
            resolved = await asyncio.wait_for(
                asyncio.shield(fallback_rag_context),
                timeout=GLOBAL_RAG_FALLBACK_TIMEOUT_SECONDS,
            )
            return resolved if isinstance(resolved, dict) else None
        except asyncio.TimeoutError:
            logger.warning(
                "Fallback RAG context did not resolve within %ss; continuing without it",
                GLOBAL_RAG_FALLBACK_TIMEOUT_SECONDS,
            )
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Fallback RAG context failed: %s", exc)
            return None

    logger.debug(
        "Ignoring unsupported fallback RAG context type: %s",
        type(fallback_rag_context).__name__,
    )
    return None


def _scope_fallback_rag_context_to_batch(
    fallback_rag_context: Optional[Dict[str, Any]],
    batch_file_paths: List[str],
) -> Optional[Dict[str, Any]]:
    """
    Keep only fallback chunks that point at the current batch files.

    This is intentionally path-scoping, not semantic filtering. Per-batch RAG is
    still the primary source for related files and dependencies.
    """
    context = _unwrap_rag_context(fallback_rag_context)
    chunks = context.get("relevant_code") or context.get("chunks") or []
    if not chunks:
        return None

    scoped_chunks = [
        chunk
        for chunk in chunks
        if _chunk_matches_batch_path(chunk, batch_file_paths)
    ]
    if not scoped_chunks:
        return None

    scoped_context = dict(context)
    if "relevant_code" in scoped_context:
        scoped_context["relevant_code"] = scoped_chunks
    else:
        scoped_context["chunks"] = scoped_chunks
    return scoped_context


def _chunk_matches_batch_path(chunk: Dict[str, Any], batch_file_paths: List[str]) -> bool:
    if not isinstance(chunk, dict):
        return False

    metadata = chunk.get("metadata") or {}
    chunk_path = (
        metadata.get("path")
        or chunk.get("path")
        or chunk.get("file_path")
        or ""
    )
    if not chunk_path:
        return False

    normalized_chunk_path = str(chunk_path).lstrip("/")
    chunk_basename = normalized_chunk_path.rsplit("/", 1)[-1]

    for file_path in batch_file_paths:
        normalized_file_path = str(file_path or "").lstrip("/")
        if not normalized_file_path:
            continue
        file_basename = normalized_file_path.rsplit("/", 1)[-1]
        if (
            normalized_chunk_path == normalized_file_path
            or normalized_chunk_path.endswith("/" + normalized_file_path)
            or normalized_file_path.endswith("/" + normalized_chunk_path)
            or chunk_basename == file_basename
        ):
            return True
    return False


def _rag_context_has_chunks(rag_context: Optional[Dict[str, Any]]) -> bool:
    context = _unwrap_rag_context(rag_context)
    chunks = context.get("relevant_code") or context.get("chunks") or []
    return bool(chunks)


async def _invoke_stage_1_batch_llm(
    llm,
    prompt: str,
    batch_file_paths: List[str],
    label: str,
) -> Optional[List[CodeReviewIssue]]:
    structured_output_attempted = _supports_structured_output(llm)
    if structured_output_attempted:
        try:
            structured_llm = llm.with_structured_output(FileReviewBatchOutput)
            result = await observed_ainvoke(
                structured_llm, prompt, stage="generation", producer="stage_1"
            )
            if result:
                return _extract_calibrated_issues(result)
            logger.warning("Structured output returned empty Stage 1 result for %s (%s)", batch_file_paths, label)
        except Exception as e:
            logger.warning("Structured output failed for Stage 1 batch %s (%s): %s", batch_file_paths, label, e)
    else:
        logger.info(
            "Structured output skipped for Stage 1 batch %s (%s); using prompt JSON parsing",
            batch_file_paths,
            label,
        )

    try:
        response = await observed_ainvoke(
            llm,
            prompt,
            stage="generation",
            producer="stage_1",
            retry=structured_output_attempted,
        )
        content = extract_llm_response_text(response)
        data = await parse_llm_response(content, FileReviewBatchOutput, llm)
        return _extract_calibrated_issues(data)
    except Exception as parse_err:
        logger.warning("Stage 1 batch parse failed for %s (%s): %s", batch_file_paths, label, parse_err)
        return None


def _extract_calibrated_issues(batch_output: FileReviewBatchOutput) -> List[CodeReviewIssue]:
    all_batch_issues: List[CodeReviewIssue] = []
    for review in batch_output.reviews:
        review_confidence = (review.confidence or "MEDIUM").upper()
        for issue in review.issues:
            if review_confidence == "LOW" and issue.severity.upper() == "HIGH":
                logger.info(
                    f"Downgrading issue in {review.file} from HIGH to MEDIUM "
                    f"(batch confidence: LOW): {issue.reason[:80]}"
                )
                issue.severity = "MEDIUM"
        all_batch_issues.extend(review.issues)
    return all_batch_issues
