"""Stage 1: Parallel file reviews with exact repository context."""
import asyncio
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, TYPE_CHECKING

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.multi_stage import FileReviewBatchOutput, FileReviewOutput, ReviewPlan
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import (
    DiffChangeType,
    ProcessedDiff,
    DiffProcessor,
)
from utils.task_context_builder import build_task_context
from utils.dependency_graph import create_smart_batches_async

from utils.llm_response import extract_llm_response_text
from service.review.orchestrator.json_utils import (
    parse_llm_response,
    resolve_structured_output,
)
from service.review.orchestrator.structured_output import (
    format_response_diagnostics,
    invoke_structured_output,
)
from service.review.orchestrator.reconciliation import (
    issue_matches_files,
    format_previous_issues_for_batch,
)
from utils.path_identity import (
    normalize_repository_path,
    repository_paths_match,
)
from service.review.orchestrator.stage_helpers import (
    emit_progress,
    emit_status,
    format_project_rules,
)
from service.review.orchestrator.inference_policy import (
    ReviewInferenceProfile,
    build_review_inference_profile,
)
from service.review.orchestrator.stage_1_local_packing import (
    Stage1LocalPackingInput,
    Stage1LocalPackingRuntime,
    Stage1PreparedContext,
    Stage1PromptMaterial,
    Stage1ReviewUnitState,
    _COMPLETE_ADDED_SOURCE_MARKER,
    _DIFF_HUNK_HEADER,
    _DiffReviewChunk,
    _Stage1EvidenceAtom,
    _add_path_lookup,
    _allocate_stage1_invocation_quotas,
    _all_stage1_hunk_ids,
    _apply_compacted_stage_1_omission,
    _chunk_diff_preserving_hunks,
    _chunk_diff_with_ownership,
    _compacted_stage_1_hunk_ids,
    _diff_contains_complete_added_source,
    _diff_limit_reason_allows_full_review,
    _ensure_stage1_review_unit,
    _exact_stage1_diff,
    _expand_oversized_diff_batches as _pack_expand_oversized_diff_batches,
    _expand_oversized_stage1_evidence_batches as _pack_stage1_evidence_batches,
    _fallback_hunk_id,
    _find_diff_file_for_path,
    _is_compacted_stage_1_diff,
    _item_requests_full_diff,
    _joint_stage1_units_for_item as _pack_joint_stage1_units_for_item,
    _lookup_by_path,
    _partition_oversized_stage1_batch as _pack_partition_stage1_batch,
    _path_lookup_keys,
    _repack_stage1_batches_by_rendered_input as _pack_repack_stage1_batches,
    _reviewable_manifest_context_chars,
    _reviewable_manifest_hunk_ids,
    _split_hunk_by_lines,
    _stage1_atom_marker,
    _stage1_evidence_atoms,
    _stage1_item_with_overrides,
    _stage1_unit_from_atoms,
    pack_stage1_local_batches,
)
from service.review.orchestrator.stage_1_rag_retrieval import (
    Stage1RagState,
    STAGE1_RELATION_BRIEFING_MAX_CHARS,
    has_exact_proposed_tree_binding,
)
from service.review.orchestrator.stage_1_tool_inventory import (
    STAGE1_AGENT_TOOL_NAMES,
    STAGE1_BRANCH_FILE_TOOL_NAME,
    STAGE1_MINIMAL_REVIEW_CONTEXT_TOOL_NAME,
    STAGE1_QUERY_GRAPH_TOOL_NAME,
    STAGE1_REQUIRED_STRUCTURAL_TOOL_SEQUENCE,
    STAGE1_REVIEW_CONTEXT_TOOL_NAME,
    STAGE1_REVIEW_FILE_TOOL_NAME,
    STAGE1_STRUCTURAL_OBSERVATION_TOOL_NAMES,
    STAGE1_STRUCTURAL_TOOL_NAMES,
    STAGE1_VCS_TOOL_NAMES,
)
from service.review.orchestrator.stage_1_agent_telemetry import (
    Stage1AgentTelemetryRecorder,
    stage1_agent_tool_event_failures,
)

if TYPE_CHECKING:
    from service.agent import AgentExecutionService
from service.review.plugin_context import (
    apply_plugin_file_policy,
    review_plugin_context,
)
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.prompt_diagnostics import record_prompt_diagnostic
from service.review.snapshot_identity import (
    resolve_exact_structural_base_revision,
)
from llm.reasoning_policy import ReasoningEffort, reasoning_request_kwargs

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


STAGE1_MAX_FILES_PER_BATCH = max(1, _env_int("REVIEW_STAGE1_MAX_FILES_PER_BATCH", 15))
STAGE1_AGENT_MAX_STEPS = 6
STAGE1_AGENT_MAX_OUTPUT_TOKENS = 16_384
STAGE1_AGENT_TIMEOUT_SECONDS = max(
    1,
    _env_int("REVIEW_STAGE1_AGENT_TIMEOUT_SECONDS", 600),
)
STAGE1_BATCH_TOKEN_BUDGET = max(10_000, _env_int("REVIEW_STAGE1_BATCH_TOKEN_BUDGET", 60_000))
STAGE1_DIFF_CHUNK_TOKEN_BUDGET = max(
    8_000,
    _env_int(
        "REVIEW_STAGE1_DIFF_CHUNK_TOKEN_BUDGET",
        STAGE1_BATCH_TOKEN_BUDGET,
    ),
)
# Current source is primary evidence, not optional structural context. Keep a bounded
# copy in each Stage 1 prompt so small/medium files are reviewed as a coherent
# post-change unit while the full source remains available to verification.
STAGE1_MAX_CURRENT_FILE_CHARS = max(
    2_000,
    _env_int("REVIEW_STAGE1_MAX_CURRENT_FILE_CHARS", 12_000),
)
# Allocate one neutral batch-wide source budget fairly across files. The
# complete diff remains primary evidence, and verification retains full source.
STAGE1_CURRENT_SOURCE_BATCH_CHAR_BUDGET = max(
    8_000,
    _env_int("REVIEW_STAGE1_CURRENT_SOURCE_BATCH_CHAR_BUDGET", 48_000),
)
# These limits apply only to prompt serialization. Full parser metadata remains
# available to batching and deterministic retrieval.
STAGE1_METADATA_CHAR_BUDGET = max(
    4_000,
    _env_int("REVIEW_STAGE1_METADATA_CHAR_BUDGET", 24_000),
)
STAGE1_METADATA_PER_FILE_CHAR_BUDGET = max(
    1_000,
    _env_int("REVIEW_STAGE1_METADATA_PER_FILE_CHAR_BUDGET", 6_000),
)
STAGE1_RELATION_BRIEFING_RESERVED_TOKENS = (
    (STAGE1_RELATION_BRIEFING_MAX_CHARS + 3) // 4
) + 256
STRUCTURED_OUTPUT_ENABLED = _env_bool("REVIEW_STRUCTURED_OUTPUT_ENABLED", True)
CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED = _env_bool("REVIEW_CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED", False)
FULL_DIFF_REVIEW_FOCUS = "FULL_DIFF_REVIEW"
_STAGE1_ESTIMATOR_SAFETY_TOKENS = 256
_CANONICAL_STRUCTURAL_EVIDENCE_ID = re.compile(r"^relation:[0-9a-f]{64}$")


@dataclass(frozen=True)
class _Stage1DirectFallbackPrompt:
    prompt: str
    structural_context_loaded: bool


@dataclass(frozen=True)
class _Stage1ReviewOrigin:
    generation_prompt: str
    source_phase: Optional[str]


@dataclass
class _Stage1BatchReviewAccumulator:
    """Keep valid per-file results while incomplete attempts are recovered."""

    expected_paths: tuple[str, ...]
    reviews_by_path: Dict[str, FileReviewOutput] = field(default_factory=dict)
    origins_by_path: Dict[str, _Stage1ReviewOrigin] = field(default_factory=dict)

    @classmethod
    def for_paths(
        cls,
        paths: Sequence[str],
    ) -> "_Stage1BatchReviewAccumulator":
        return cls(expected_paths=tuple(paths))

    def merge(
        self,
        batch_output: FileReviewBatchOutput,
        requested_paths: Sequence[str],
        *,
        generation_prompt: str,
        source_phase: Optional[str],
    ) -> None:
        expected = {
            normalize_repository_path(path)
            for path in self.expected_paths
            if normalize_repository_path(path)
        }
        requested = {
            normalize_repository_path(path)
            for path in requested_paths
            if normalize_repository_path(path)
        }
        observed = [
            normalize_repository_path(review.file)
            for review in batch_output.reviews
        ]
        observed_counts = Counter(observed)
        origin = _Stage1ReviewOrigin(
            generation_prompt=generation_prompt,
            source_phase=source_phase,
        )
        for review, path in zip(batch_output.reviews, observed):
            # Unexpected, empty, and duplicate-path objects are ambiguous. Keep
            # every unique requested object; recovery owns only the remainder.
            if (
                not path
                or path not in expected
                or path not in requested
                or observed_counts[path] != 1
                or path in self.reviews_by_path
            ):
                continue
            self.reviews_by_path[path] = review
            self.origins_by_path[path] = origin

    def missing_paths(self) -> List[str]:
        return [
            path
            for path in self.expected_paths
            if normalize_repository_path(path) not in self.reviews_by_path
        ]

    def output(self) -> FileReviewBatchOutput:
        return FileReviewBatchOutput(reviews=[
            self.reviews_by_path[normalized]
            for path in self.expected_paths
            for normalized in (normalize_repository_path(path),)
            if normalized in self.reviews_by_path
        ])

    def outputs_by_origin(
        self,
    ) -> List[tuple[FileReviewBatchOutput, _Stage1ReviewOrigin]]:
        grouped: Dict[_Stage1ReviewOrigin, List[FileReviewOutput]] = {}
        for path in self.expected_paths:
            normalized = normalize_repository_path(path)
            review = self.reviews_by_path.get(normalized)
            origin = self.origins_by_path.get(normalized)
            if review is None or origin is None:
                continue
            grouped.setdefault(origin, []).append(review)
        return [
            (FileReviewBatchOutput(reviews=reviews), origin)
            for origin, reviews in grouped.items()
        ]


def _salvage_stage1_schema_tool_outputs(
    events: Sequence[Any],
    accumulator: Optional[_Stage1BatchReviewAccumulator],
    requested_paths: Sequence[str],
    *,
    generation_prompt: str,
    source_phase: Optional[str],
) -> int:
    """Keep valid Stage 1 schema calls exposed as agent tool events.

    LangChain's ``ToolStrategy`` can expose more than one otherwise-valid
    schema call as ordinary tool events before rejecting the combined final
    response. This is intentionally specific to Stage 1's output schema;
    repository tools and other structured response schemas are ignored.
    """
    if accumulator is None:
        return 0

    # A schema call is an intermediate generation at its transcript position.
    # Never credit it graph evidence returned only by a later tool event. The
    # missing-path recovery can safely regenerate those early partial objects.
    last_structural_event_index = max(
        (
            index
            for index, event in enumerate(events)
            if _tool_event_name(event)
            in STAGE1_STRUCTURAL_OBSERVATION_TOOL_NAMES
        ),
        default=-1,
    )
    salvaged = 0
    for event_index, event in enumerate(events):
        action = getattr(event, "action", None)
        if isinstance(action, Mapping):
            tool_name = action.get("tool")
            payload = action.get("tool_input")
        else:
            tool_name = getattr(action, "tool", None)
            payload = getattr(action, "tool_input", None)
        if tool_name != FileReviewBatchOutput.__name__:
            continue
        if event_index < last_structural_event_index:
            logger.debug(
                "Ignoring an intermediate Stage 1 schema output that precedes "
                "a later structural observation"
            )
            continue
        if not isinstance(payload, Mapping):
            continue

        try:
            output = FileReviewBatchOutput.model_validate(dict(payload))
        except (TypeError, ValueError) as batch_error:
            # ToolStrategy keeps the rejected schema call in the transcript
            # before asking the model for a correction. One malformed review
            # must not discard its valid siblings: the correction frequently
            # contains only the repaired object, not the whole original batch.
            raw_reviews = payload.get("reviews")
            if not isinstance(raw_reviews, (list, tuple)):
                logger.debug(
                    "Ignoring malformed Stage 1 schema tool output: %s",
                    batch_error,
                )
                continue

            raw_path_counts = Counter(
                normalize_repository_path(
                    raw_review.get("file")
                    if isinstance(raw_review, Mapping)
                    else getattr(raw_review, "file", "")
                )
                for raw_review in raw_reviews
            )
            valid_reviews: List[FileReviewOutput] = []
            invalid_review_count = 0
            ambiguous_review_count = 0
            for raw_review in raw_reviews:
                try:
                    valid_review = FileReviewOutput.model_validate(raw_review)
                except (TypeError, ValueError):
                    invalid_review_count += 1
                    continue
                if (
                    raw_path_counts[
                        normalize_repository_path(valid_review.file)
                    ]
                    != 1
                ):
                    # Preserve duplicate ambiguity from the rejected raw call.
                    # Filtering invalid siblings first must not make one of two
                    # same-path claims appear uniquely trustworthy.
                    ambiguous_review_count += 1
                    continue
                valid_reviews.append(valid_review)
            if not valid_reviews:
                logger.debug(
                    "Ignoring malformed Stage 1 schema tool output: %s",
                    batch_error,
                )
                continue

            output = FileReviewBatchOutput(reviews=valid_reviews)
            logger.debug(
                "Salvaging %d individually valid Stage 1 review object(s) "
                "from a rejected schema call; malformed=%d ambiguous=%d",
                len(valid_reviews),
                invalid_review_count,
                ambiguous_review_count,
            )

        before = len(accumulator.reviews_by_path)
        accumulator.merge(
            output,
            requested_paths,
            generation_prompt=generation_prompt,
            source_phase=source_phase,
        )
        salvaged += len(accumulator.reviews_by_path) - before

    return salvaged


def _available_stage1_agent_tools(
    agent_service: Optional["AgentExecutionService"],
) -> frozenset[str]:
    """Return the Stage 1 tools actually bound to this review session.

    The shared service exposes its initialized inventory. Lightweight test
    doubles and compatible callers without that property retain the historical
    complete tool set.
    """
    if agent_service is None:
        return frozenset()
    available = getattr(agent_service, "available_tool_names", None)
    if available is None:
        return STAGE1_AGENT_TOOL_NAMES
    try:
        return STAGE1_AGENT_TOOL_NAMES.intersection(available)
    except TypeError:
        return STAGE1_AGENT_TOOL_NAMES


def _stage1_schema_declaration_bytes() -> int:
    try:
        schema = FileReviewBatchOutput.model_json_schema()
    except (AttributeError, TypeError, ValueError):
        return 0
    return len(json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8"))


_STAGE1_SCHEMA_DECLARATION_BYTES = _stage1_schema_declaration_bytes()


def _build_stage_1_prepared_context(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
    is_incremental: bool,
) -> Stage1PreparedContext:
    diff_source = processed_diff
    if is_incremental and request.deltaDiff:
        diff_source = apply_plugin_file_policy(
            request,
            DiffProcessor().process(request.deltaDiff),
        )

    diff_by_path: Dict[str, Optional[Any]] = {}
    if diff_source:
        for diff_file in diff_source.files:
            _add_path_lookup(diff_by_path, diff_file.path, diff_file)

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
        full_diff_raw=None,
        file_content_by_path=file_content_by_path,
        enrichment_metadata_by_path=enrichment_metadata_by_path,
        task_context=(
            build_task_context(request.taskContext)
            or "No task context available."
        ),
    )


def _bounded_current_file_context(
    content: Optional[str],
    diff_content: str = "",
    *,
    context_lines: int = 20,
    max_chars: Optional[int] = None,
) -> str:
    """Return explicitly labelled, bounded current-source evidence for Stage 1."""
    if not content:
        return "(Current file content unavailable; use the diff evidence.)"
    char_budget = max(
        1,
        min(
            STAGE1_MAX_CURRENT_FILE_CHARS,
            max_chars
            if max_chars is not None
            else STAGE1_MAX_CURRENT_FILE_CHARS,
        ),
    )
    if len(content) <= char_budget:
        return content

    source_lines = content.splitlines()
    windows: List[tuple[int, int]] = []
    for diff_line in diff_content.splitlines():
        match = _DIFF_HUNK_HEADER.match(diff_line)
        if match is None:
            continue
        new_start = max(1, int(match.group("new_start")))
        new_count = int(match.group("new_count") or "1")
        affected_count = max(1, new_count)
        start = max(1, new_start - max(0, context_lines))
        end = min(
            len(source_lines),
            new_start + affected_count - 1 + max(0, context_lines),
        )
        if end >= start:
            windows.append((start, end))

    if windows:
        merged_windows: List[tuple[int, int]] = []
        for start, end in sorted(windows):
            if merged_windows and start <= merged_windows[-1][1] + 1:
                prior_start, prior_end = merged_windows[-1]
                merged_windows[-1] = (prior_start, max(prior_end, end))
            else:
                merged_windows.append((start, end))

        prefix = (
            "[Post-change source windows around reviewed diff hunks; "
            "the complete file remains available to deterministic verification]"
        )
        rendered = [prefix]
        used = len(prefix)
        omitted_windows = 0
        for window_index, (start, end) in enumerate(merged_windows):
            heading = f"\n[Post-change lines {start}-{end}]"
            if used + len(heading) > char_budget:
                omitted_windows = len(merged_windows) - window_index
                break
            rendered.append(heading)
            used += len(heading)
            window_complete = True
            for line_number in range(start, end + 1):
                source_line = (
                    f"\n{line_number:>7}: "
                    f"{source_lines[line_number - 1]}"
                )
                if used + len(source_line) > char_budget:
                    window_complete = False
                    break
                rendered.append(source_line)
                used += len(source_line)
            if not window_complete:
                omitted_windows = len(merged_windows) - window_index
                break

        if omitted_windows:
            marker = (
                f"\n[{omitted_windows} additional post-change source "
                "window(s) omitted by prompt budget]"
            )
            while rendered and used + len(marker) > char_budget:
                removed = rendered.pop()
                used -= len(removed)
            if len(marker) <= char_budget:
                rendered.append(marker)
        return "".join(rendered)

    # A malformed or metadata-only diff has no usable new-side coordinates.
    # Preserve both ends without assigning language-specific meaning to either.
    half = max(1, (char_budget - 160) // 2)
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


def _ensure_full_diff_index(prepared_context: Stage1PreparedContext) -> None:
    if prepared_context.full_diff_index_loaded:
        return
    prepared_context.full_diff_index_loaded = True

    raw_diff = prepared_context.full_diff_raw
    if not raw_diff:
        return

    # Stage 1 can split very large diffs into multiple bounded prompts. Parse
    # the original hunks only when Stage 0 explicitly asks for full-diff review.
    raw_diff_source = DiffProcessor().process(raw_diff)
    for diff_file in raw_diff_source.files:
        _add_path_lookup(prepared_context.full_diff_by_path, diff_file.path, diff_file)
    logger.info(
        "Stage 1 prepared unbounded raw diff index for %d file(s)",
        len(raw_diff_source.files),
    )


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
        if any(
            repository_paths_match(meta.path, batch_path)
            for batch_path in batch_file_paths
        ):
            result.append(meta)
            seen.add(id(meta))

    return result


def _format_batch_metadata_json(
    batch_metadata: List[Any],
    *,
    max_chars: Optional[int] = None,
    max_chars_per_file: Optional[int] = None,
) -> str:
    """Serialize arbitrary parser metadata within a deterministic prompt budget.

    This projection is deliberately schema-neutral so analysis-plugin fields do
    not require host-side dispatch. Omission markers distinguish a bounded
    prompt view from evidence that a metadata value is absent.
    """
    if not batch_metadata:
        return ""

    metadata_payload = [_metadata_to_payload(meta) for meta in batch_metadata]
    total_budget = max(256, max_chars or STAGE1_METADATA_CHAR_BUDGET)
    configured_per_file = max(
        256,
        max_chars_per_file or STAGE1_METADATA_PER_FILE_CHAR_BUDGET,
    )
    # Reserve JSON list punctuation and distribute the hard total cap evenly.
    per_file_budget = min(
        configured_per_file,
        max(256, (total_budget - 2) // len(metadata_payload)),
    )
    projected = [
        _bounded_metadata_payload(payload, per_file_budget)
        for payload in metadata_payload
    ]
    rendered = json.dumps(
        projected,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    # Enforce the total boundary independently of JSON punctuation and unusual
    # payload shapes.
    while len(rendered) > total_budget and per_file_budget > 256:
        overflow_per_file = max(
            1,
            (len(rendered) - total_budget + len(projected) - 1)
            // len(projected),
        )
        per_file_budget = max(256, per_file_budget - overflow_per_file)
        projected = [
            _bounded_metadata_payload(payload, per_file_budget)
            for payload in metadata_payload
        ]
        rendered = json.dumps(
            projected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    if len(rendered) > total_budget:
        projected = [
            _metadata_identity_fallback(payload, per_file_budget)
            for payload in metadata_payload
        ]
        rendered = json.dumps(
            projected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    omitted_entries = sum(
        _count_metadata_omission_markers(value) for value in projected
    )
    if omitted_entries:
        logger.info(
            "Stage 1 parser metadata prompt view bounded to %d chars "
            "(rendered=%d, omission_markers=%d); full metadata retained for retrieval",
            total_budget,
            len(rendered),
            omitted_entries,
        )
    return rendered


def _bounded_metadata_payload(payload: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
    canonical = _project_metadata_detail(payload, detail_limit=None)
    if _json_char_length(canonical) <= max_chars:
        return canonical

    low = 1
    high = max(1, _metadata_detail_ceiling(payload))
    best: Optional[Dict[str, Any]] = None
    while low <= high:
        detail_limit = (low + high) // 2
        candidate = _project_metadata_detail(payload, detail_limit=detail_limit)
        if _json_char_length(candidate) <= max_chars:
            best = candidate
            low = detail_limit + 1
        else:
            high = detail_limit - 1

    if best is not None:
        return best
    return _metadata_identity_fallback(payload, max_chars)


def _project_metadata_detail(value: Any, detail_limit: Optional[int]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _project_metadata_detail(value[key], detail_limit)
            for key in sorted(value, key=lambda item: str(item))
            if value[key] is not None
        }
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if isinstance(value, set):
            items.sort(
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            )
        selected = items if detail_limit is None else items[:detail_limit]
        result = [
            _project_metadata_detail(item, detail_limit)
            for item in selected
        ]
        omitted = len(items) - len(selected)
        if omitted:
            result.append({"_codecrowOmittedItems": omitted})
        return result
    if isinstance(value, str) and detail_limit is not None:
        string_limit = max(64, detail_limit * 64)
        if len(value) > string_limit:
            omitted = len(value) - string_limit
            return value[:string_limit] + f"… [CodeCrow omitted {omitted} chars]"
    return value


def _metadata_detail_ceiling(value: Any) -> int:
    if isinstance(value, dict):
        return max(
            [1] + [_metadata_detail_ceiling(nested) for nested in value.values()]
        )
    if isinstance(value, (list, tuple, set)):
        return max(
            [len(value), 1]
            + [_metadata_detail_ceiling(nested) for nested in value]
        )
    if isinstance(value, str):
        return max(1, (len(value) + 63) // 64)
    return 1


def _metadata_identity_fallback(
    payload: Dict[str, Any],
    max_chars: int,
) -> Dict[str, Any]:
    identity: Dict[str, Any] = {
        "_codecrowMetadataOmitted": {
            "sourceFieldCount": len(payload),
            "reason": "prompt-character-budget",
        }
    }
    for key in ("path", "language", "namespace", "parentClass"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value)
        candidate = dict(identity)
        candidate[key] = text
        if _json_char_length(candidate) <= max_chars:
            identity = candidate
            continue

        low = 0
        high = len(text)
        best = ""
        while low <= high:
            prefix_chars = (low + high) // 2
            bounded_text = text[:prefix_chars] + (
                "…" if prefix_chars < len(text) else ""
            )
            candidate = dict(identity)
            candidate[key] = bounded_text
            if _json_char_length(candidate) <= max_chars:
                best = bounded_text
                low = prefix_chars + 1
            else:
                high = prefix_chars - 1
        if best:
            identity[key] = best
    return identity


def _json_char_length(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )


def _count_metadata_omission_markers(value: Any) -> int:
    if isinstance(value, dict):
        own = int(
            "_codecrowOmittedItems" in value
            or "_codecrowMetadataOmitted" in value
        )
        return own + sum(
            _count_metadata_omission_markers(nested)
            for nested in value.values()
        )
    if isinstance(value, list):
        return sum(_count_metadata_omission_markers(item) for item in value)
    if isinstance(value, str) and "[CodeCrow omitted " in value:
        return 1
    return 0


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


def _extract_metadata_identifiers(
    batch_metadata: List[Any],
    limit: int = 200,
) -> Optional[List[str]]:
    """Collect only parser fields that prove a symbol relationship.

    Paths, languages, namespaces, diagnostics, and arbitrary plugin strings are
    not definition lookup keys. Framework-specific relations reach the prompt
    through typed plugin graph facts instead of this generic symbol expansion.
    """
    identifier_fields = (
        "imports",
        "extends",
        "extendsClasses",
        "implements",
        "implementsInterfaces",
        "parent_class",
        "parentClass",
    )
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
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                visit(nested)
            return

    for meta in batch_metadata:
        payload = _metadata_to_payload(meta)
        for field_name in identifier_fields:
            if field_name in payload:
                visit(payload[field_name])

    return identifiers or None




def _supports_structured_output(llm) -> bool:
    if not STRUCTURED_OUTPUT_ENABLED:
        return False
    if CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED:
        return True

    from utils.llm_delegate import llm_class_names

    class_names = llm_class_names(llm)
    if "ChatCloudflareOpenAI" in class_names:
        return False
    return True


def _positive_int_or_default(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _relationship_type_text(relationship: Any) -> str:
    value = getattr(relationship, "relationshipType", "DEPENDENCY")
    return str(getattr(value, "value", value) or "DEPENDENCY")


def _stage1_boundary_context(
    request: ReviewRequestDto,
    batch_items: Sequence[Dict[str, Any]],
    batch_file_paths: Sequence[str],
) -> str:
    """Render exact graph edges cut by input packing, without source guessing."""
    batch_paths = {
        normalize_repository_path(path)
        for path in batch_file_paths
        if normalize_repository_path(path)
    }
    changed_paths = {
        normalize_repository_path(path)
        for path in (getattr(request, "changedFiles", None) or [])
        if normalize_repository_path(path)
    }
    records: Dict[tuple[str, str, str, str], Dict[str, str]] = {}
    enrichment = getattr(request, "enrichmentData", None)
    for relationship in getattr(enrichment, "relationships", None) or []:
        source = normalize_repository_path(
            getattr(relationship, "sourceFile", "")
        )
        target = normalize_repository_path(
            getattr(relationship, "targetFile", "")
        )
        if not source or not target:
            continue
        if (source in batch_paths) == (target in batch_paths):
            continue
        if changed_paths and not ({source, target} <= changed_paths):
            continue
        relation_type = _relationship_type_text(relationship)
        matched_on = str(getattr(relationship, "matchedOn", "") or "")
        key = (source, target, relation_type, matched_on)
        records[key] = {
            "source": source,
            "target": target,
            "type": relation_type,
            "matchedOn": matched_on,
        }

    # Structurally discovered graph edges may not exist in enrichment. Preserve their
    # endpoints as neutral dependency facts rather than silently losing them.
    for item in batch_items:
        file_info = item.get("file")
        source = normalize_repository_path(getattr(file_info, "path", ""))
        for related_path in item.get("related_files", ()) or ():
            target = normalize_repository_path(related_path)
            if not source or not target or target in batch_paths:
                continue
            ordered = tuple(sorted((source, target)))
            key = (ordered[0], ordered[1], "DEPENDENCY", "")
            records.setdefault(key, {
                "source": ordered[0],
                "target": ordered[1],
                "type": "DEPENDENCY",
                "matchedOn": "",
            })

    parts: List[str] = []
    if records:
        parts.append(
            "Exact cross-pack relationship records (deterministic; no source "
            "was inferred or truncated):\n"
            + json.dumps(
                [records[key] for key in sorted(records)],
                ensure_ascii=False,
                indent=2,
            )
        )
    diagnostics = sorted({
        str(item.get("_stage1_budget_diagnostic") or "")
        for item in batch_items
        if item.get("_stage1_budget_diagnostic")
    })
    parts.extend(diagnostics)
    return "\n\n".join(parts)


def _prepare_stage1_prompt_material(
    request: ReviewRequestDto,
    batch_items: List[Dict[str, Any]],
    prepared_context: Stage1PreparedContext,
    is_incremental: bool,
) -> Stage1PromptMaterial:
    """Build exact local prompt material once for packing and review."""
    batch_files_data: List[Dict[str, Any]] = []
    batch_file_paths: List[str] = []
    complete_current_file_paths: set[str] = set()
    available_current_source_count = sum(
        1
        for item in batch_items
        if _lookup_by_path(
            prepared_context.file_content_by_path,
            item["file"].path,
        )
    )
    current_source_per_file_budget = min(
        STAGE1_MAX_CURRENT_FILE_CHARS,
        max(
            1,
            STAGE1_CURRENT_SOURCE_BATCH_CHAR_BUDGET
            // max(1, available_current_source_count),
        ),
    )

    for item in batch_items:
        file_info = item["file"]
        batch_file_paths.append(file_info.path)
        current_file_content = _lookup_by_path(
            prepared_context.file_content_by_path,
            file_info.path,
        )
        diff_file = _find_diff_file_for_path(
            prepared_context,
            file_info.path,
            use_full_diff=_item_requests_full_diff(item),
        )
        if "_diff_override" in item:
            file_diff = str(item.get("_diff_override") or "")
        else:
            file_diff = ""
        if "_diff_override" not in item and diff_file:
            file_diff = diff_file.content
        if file_diff:
            chunk_total = int(item.get("_diff_chunk_total") or 0)
            if chunk_total > 1:
                chunk_index = int(item.get("_diff_chunk_index") or 1)
                file_diff = (
                    f"[Large diff segment {chunk_index}/{chunk_total} for "
                    f"{file_info.path}. All segments are reviewed independently "
                    "and merged after Stage 1.]\n"
                    f"{file_diff}"
                )
        change_type = (
            diff_file.change_type
            if diff_file is not None
            else DiffChangeType.MODIFIED
        )
        source_override = item.get("_current_source_override")
        if isinstance(source_override, str):
            current_code = source_override
        else:
            complete_added_source_in_diff = (
                change_type is DiffChangeType.ADDED
                and int(item.get("_diff_chunk_total") or 0) <= 1
                and _diff_contains_complete_added_source(
                    current_file_content,
                    file_diff,
                )
            )
            if complete_added_source_in_diff:
                current_code = _COMPLETE_ADDED_SOURCE_MARKER
                complete_current_file_paths.add(file_info.path)
            else:
                current_code = _bounded_current_file_context(
                    current_file_content,
                    file_diff,
                    max_chars=current_source_per_file_budget,
                )
                if (
                    current_file_content
                    and len(current_file_content) <= current_source_per_file_budget
                ):
                    complete_current_file_paths.add(file_info.path)

        batch_files_data.append({
            "path": file_info.path,
            "type": change_type.value.upper(),
            "focus_areas": file_info.focus_areas,
            "current_code": current_code,
            "diff": file_diff or "(Diff unavailable)",
            "is_incremental": is_incremental,
        })

    singleton_item = batch_items[0] if len(batch_items) == 1 else None
    if singleton_item is not None and "_project_rules_override" in singleton_item:
        project_rules = str(singleton_item.get("_project_rules_override") or "")
    else:
        project_rules = format_project_rules(request.projectRules, batch_file_paths)
    batch_metadata = _iter_batch_enrichment_metadata(
        request,
        batch_file_paths,
        prepared_context,
    )
    enrichment_identifiers = (
        _extract_metadata_identifiers(batch_metadata)
        if batch_metadata
        else None
    )
    previous_issues_for_batch = ""
    previous_issues = getattr(request, "previousCodeAnalysisIssues", None)
    if isinstance(previous_issues, (list, tuple)) and previous_issues:
        relevant_previous_issues = [
            issue
            for issue in previous_issues
            if issue_matches_files(issue, batch_file_paths)
        ]
        if relevant_previous_issues:
            previous_issues_for_batch = format_previous_issues_for_batch(
                relevant_previous_issues
            )

    if singleton_item is not None and "_previous_issues_override" in singleton_item:
        previous_issues_for_batch = str(
            singleton_item.get("_previous_issues_override") or ""
        )
    if singleton_item is not None and "_metadata_override" in singleton_item:
        file_metadata_text = str(singleton_item.get("_metadata_override") or "")
    else:
        file_metadata_text = _format_batch_metadata_json(batch_metadata)
    if singleton_item is not None and "_task_context_override" in singleton_item:
        task_context = str(singleton_item.get("_task_context_override") or "")
    else:
        task_context = prepared_context.task_context
    plugin_context_override = None
    if singleton_item is not None and "_plugin_context_override" in singleton_item:
        plugin_context_override = str(
            singleton_item.get("_plugin_context_override") or ""
        )
    if singleton_item is not None and "_boundary_context_override" in singleton_item:
        boundary_context = str(
            singleton_item.get("_boundary_context_override") or ""
        )
    else:
        boundary_context = _stage1_boundary_context(
            request,
            batch_items,
            batch_file_paths,
        )

    return Stage1PromptMaterial(
        request=request,
        batch_items=batch_items,
        batch_files_data=batch_files_data,
        batch_file_paths=batch_file_paths,
        complete_current_file_paths=complete_current_file_paths,
        current_source_per_file_budget=current_source_per_file_budget,
        batch_metadata=batch_metadata,
        enrichment_identifiers=enrichment_identifiers,
        project_rules=project_rules,
        previous_issues_for_batch=previous_issues_for_batch,
        file_metadata_text=file_metadata_text,
        task_context=task_context,
        plugin_context_override=plugin_context_override,
        prepared_context=prepared_context,
        is_incremental=is_incremental,
        boundary_context=boundary_context,
    )


def _render_stage1_prompt(
    material: Stage1PromptMaterial,
    structural_context_text: str,
    *,
    visible_evidence_by_id: Optional[
        Dict[str, tuple[Dict[str, Any], ...]]
    ] = None,
    use_mcp_tools: Optional[bool] = None,
    structural_tools_available: Optional[bool] = None,
    review_file_tool_available: bool = False,
) -> tuple[str, str]:
    if material.plugin_context_override is not None:
        plugin_context_text = material.plugin_context_override
    else:
        try:
            plugin_context_text = review_plugin_context(
                material.request,
                material.batch_file_paths,
                visible_evidence_by_id=visible_evidence_by_id or {},
            )
        except Exception as exception:
            logger.warning(
                "Optional Stage 1 plugin prompt context is unavailable; "
                "continuing with local and structural evidence: %s",
                exception,
            )
            plugin_context_text = ""
    mcp_tools_enabled = (
        bool(getattr(material.request, "useMcpTools", False))
        if use_mcp_tools is None
        else use_mcp_tools
    )
    prompt = PromptBuilder.build_stage_1_batch_prompt(
        files=material.batch_files_data,
        priority=(
            material.batch_items[0]["priority"]
            if material.batch_items
            else "MEDIUM"
        ),
        project_rules=material.project_rules,
        file_outlines=material.file_metadata_text,
        structural_context=structural_context_text,
        is_incremental=material.is_incremental,
        previous_issues=material.previous_issues_for_batch,
        all_pr_files=getattr(material.request, "changedFiles", None),
        deleted_files=getattr(material.request, "deletedFiles", None),
        task_context=material.task_context,
        use_mcp_tools=mcp_tools_enabled,
        structural_tools_available=(
            mcp_tools_enabled
            if structural_tools_available is None
            else structural_tools_available
        ),
        review_file_tool_available=review_file_tool_available,
        target_branch=str(
            getattr(material.request, "localRepoRevision", None)
            or material.request.get_target_head_commit_hash()
            or getattr(material.request, "targetBranchName", "")
            or ""
        ),
        vcs_workspace=str(
            getattr(material.request, "projectVcsWorkspace", "") or ""
        ),
        vcs_repo_slug=str(
            getattr(material.request, "projectVcsRepoSlug", "") or ""
        ),
        plugin_context=plugin_context_text,
        batch_boundary_context=material.boundary_context,
    )
    return prompt, plugin_context_text


def _estimated_prompt_tokens(prompt: str) -> int:
    """Estimate rendered UTF-8 input plus the structured-output declaration."""
    request_bytes = (
        len(prompt.encode("utf-8"))
        + _STAGE1_SCHEMA_DECLARATION_BYTES
    )
    return max(
        1,
        (request_bytes + 3) // 4 + _STAGE1_ESTIMATOR_SAFETY_TOKENS,
    )


# ── Batching ──────────────────────────────────────────────────


def chunk_files(
    file_groups: List[Any],
    max_files_per_batch: int = STAGE1_MAX_FILES_PER_BATCH,
    processed_diff: Optional[ProcessedDiff] = None,
    max_allowed_tokens: int = STAGE1_BATCH_TOKEN_BUDGET,
    token_cost_by_path: Optional[Dict[str, int]] = None,
) -> List[List[Dict[str, Any]]]:
    estimated_cost_by_path = {
        diff_file.path: (len(diff_file.content.encode("utf-8")) // 4) + 1000
        for diff_file in getattr(processed_diff, "files", [])
    }
    estimated_cost_by_path.update(token_cost_by_path or {})
    batches: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_tokens = 0
    for group in file_groups:
        for f in group.files:
            file_tokens = estimated_cost_by_path.get(f.path, 2000)
            if current and (
                len(current) >= max_files_per_batch
                or current_tokens + file_tokens > max_allowed_tokens
            ):
                batches.append(current)
                current = []
                current_tokens = 0
            current.append({"file": f, "priority": group.priority})
            current_tokens += file_tokens
    if current:
        batches.append(current)
    return batches


def _stage1_batch_token_limit(request: ReviewRequestDto) -> int:
    model_context_tokens = _positive_int_or_default(
        getattr(request, "maxAllowedTokens", None),
        200000,
    )
    return min(
        max(10_000, model_context_tokens - 20_000),
        STAGE1_BATCH_TOKEN_BUDGET,
    )


def _stage1_packing_token_limit(request: ReviewRequestDto) -> int:
    """Reserve prompt space for the deterministic relation briefing."""
    full_limit = _stage1_batch_token_limit(request)
    if not (
        getattr(request, "ragEnabled", True)
        and _has_exact_proposed_tree_binding(request)
    ):
        return full_limit
    return max(
        8_000,
        full_limit - STAGE1_RELATION_BRIEFING_RESERVED_TOKENS,
    )


async def create_smart_batches_wrapper(
    file_groups: List[Any],
    processed_diff: Optional[ProcessedDiff],
    request: ReviewRequestDto,
    rag_client,
    max_files_per_batch: int = 15,
    prepared_context: Optional[Stage1PreparedContext] = None,
    is_incremental: bool = False,
) -> List[List[Dict[str, Any]]]:
    branches = []
    rag_branch = request.get_rag_branch()
    base_branch = request.get_rag_base_branch()
    if rag_branch:
        branches.append(rag_branch)
    if base_branch and base_branch not in branches:
        branches.append(base_branch)
    structural_binding = {
        "repository_revision": resolve_exact_structural_base_revision(request),
        "repository_generation_manifest_sha256": getattr(
            request,
            "ragBaseGenerationManifestSha256",
            None,
        ),
        "collection_target": getattr(request, "ragCollectionTarget", None),
    }
    exact_structural_binding = all(
        isinstance(value, str) and bool(value.strip())
        for value in structural_binding.values()
    )
    structural_tools_enabled = bool(getattr(request, "useMcpTools", False))
    batching_rag_client = (
        rag_client
        if branches and exact_structural_binding and structural_tools_enabled
        else None
    )
    if not branches:
        logger.warning(
            "Stage 1 batching has no authoritative target branch; "
            "using local/enrichment grouping without a structural lookup"
        )
    elif (
        structural_tools_enabled
        and rag_client is not None
        and not exact_structural_binding
    ):
        logger.info(
            "Stage 1 structural smart-batching is unavailable because the "
            "request has no complete exact-generation binding"
        )

    enrichment_data = getattr(request, 'enrichmentData', None)
    token_cost_by_path: Optional[Dict[str, int]] = None
    shared_prompt_tokens = 0
    if prepared_context is not None:
        token_cost_by_path = {}
        shared_material = _prepare_stage1_prompt_material(
            request,
            [],
            prepared_context,
            is_incremental,
        )
        shared_prompt, _ = _render_stage1_prompt(shared_material, "")
        shared_prompt_tokens = _estimated_prompt_tokens(shared_prompt)
        for group in file_groups:
            for review_file in group.files:
                material = _prepare_stage1_prompt_material(
                    request,
                    [{"file": review_file, "priority": group.priority}],
                    prepared_context,
                    is_incremental,
                )
                local_prompt, _ = _render_stage1_prompt(material, "")
                token_cost_by_path[review_file.path] = (
                    max(
                        1,
                        _estimated_prompt_tokens(local_prompt)
                        - shared_prompt_tokens,
                    )
                )

    try:
        # Preserve coherent small/medium PRs while keeping every prompt inside
        # the configured model budget. Large components split only when their
        # actual diff cost or the explicit file ceiling requires it.
        model_context_tokens = _positive_int_or_default(
            getattr(request, "maxAllowedTokens", None),
            200000,
        )
        model_safe_limit = max(10_000, model_context_tokens - 20_000)
        batch_token_limit = _stage1_packing_token_limit(request)
        graph_content_limit = max(
            1_000,
            batch_token_limit - shared_prompt_tokens,
        )
        if batch_token_limit < model_safe_limit:
            logger.info(
                "Stage 1 batch token budget capped at %d tokens "
                "(model-safe limit=%d, env REVIEW_STAGE1_BATCH_TOKEN_BUDGET)",
                batch_token_limit,
                model_safe_limit,
            )

        batches = await create_smart_batches_async(
            file_groups=file_groups,
            workspace=request.projectWorkspace,
            project=request.projectNamespace,
            branches=branches,
            rag_client=batching_rag_client,
            max_batch_size=max_files_per_batch,
            enrichment_data=enrichment_data,
            max_allowed_tokens=graph_content_limit,
            processed_diff=processed_diff,
            token_cost_by_path=token_cost_by_path,
            structural_binding=(
                structural_binding if exact_structural_binding else None
            ),
        )
        total_files = sum(len(b) for b in batches)
        related_files = sum(1 for b in batches for f in b if f.get('has_relationships'))
        enrichment_source = (
            "enrichment data" if enrichment_data else "structural graph discovery"
        )
        logger.info(
            f"Smart batching ({enrichment_source}): {total_files} files in "
            f"{len(batches)} batches, {related_files} files have cross-file relationships"
        )
        return batches
    except Exception as e:
        logger.warning(
            "Smart batching failed, falling back to isolated-file batches: %s",
            e,
        )
        return chunk_files(
            file_groups,
            1,
            processed_diff=processed_diff,
            max_allowed_tokens=max(
                1_000,
                _stage1_packing_token_limit(request) - shared_prompt_tokens,
            ),
            token_cost_by_path=token_cost_by_path,
        )


def _complete_stage1_plugin_context(
    request: ReviewRequestDto,
    path: str,
) -> str:
    try:
        # Packing needs the complete deterministic contribution. Evidence visibility
        # may later reduce evidence targets, but it must never reveal a larger
        # plugin block than the packer measured.
        return review_plugin_context(request, [path])
    except Exception as exception:
        logger.warning(
            "Optional Stage 1 plugin prompt context is unavailable during "
            "lossless packing; continuing without it: %s",
            exception,
        )
        return ""


def _stage1_material_prompt_tokens(material: Stage1PromptMaterial) -> int:
    prompt, _ = _render_stage1_prompt(material, "")
    return _estimated_prompt_tokens(prompt)


def _stage1_local_packing_runtime() -> Stage1LocalPackingRuntime:
    return Stage1LocalPackingRuntime(
        prepare_material=_prepare_stage1_prompt_material,
        material_prompt_tokens=_stage1_material_prompt_tokens,
        complete_plugin_context=_complete_stage1_plugin_context,
    )


def _expand_oversized_diff_batches(
    batches: List[List[Dict[str, Any]]],
    prepared_context: Stage1PreparedContext,
    diff_chunk_token_budget: int = STAGE1_DIFF_CHUNK_TOKEN_BUDGET,
) -> List[List[Dict[str, Any]]]:
    return _pack_expand_oversized_diff_batches(
        batches,
        prepared_context,
        diff_chunk_token_budget=diff_chunk_token_budget,
    )


def _joint_stage1_units_for_item(
    item: Dict[str, Any],
    request: ReviewRequestDto,
    prepared_context: Stage1PreparedContext,
    is_incremental: bool,
    token_budget: int,
    max_units: int = 3,
) -> List[Dict[str, Any]]:
    """Backward-compatible facade over the local-packing runtime boundary."""
    return _pack_joint_stage1_units_for_item(
        item,
        request,
        prepared_context,
        is_incremental,
        token_budget,
        max_units=max_units,
        runtime=_stage1_local_packing_runtime(),
    )


def _expand_oversized_stage1_evidence_batches(
    batches: List[List[Dict[str, Any]]],
    request: ReviewRequestDto,
    prepared_context: Stage1PreparedContext,
    is_incremental: bool,
    token_budget: int,
    max_units_per_item: int = 3,
    max_total_batches: Optional[int] = None,
) -> List[List[Dict[str, Any]]]:
    """Preserve the historical adapter while using typed packing inputs."""
    return pack_stage1_local_batches(
        batches,
        Stage1LocalPackingInput(
            request=request,
            prepared_context=prepared_context,
            is_incremental=is_incremental,
            token_budget=token_budget,
        ),
        _stage1_local_packing_runtime(),
        max_units_per_item=max_units_per_item,
        max_total_batches=max_total_batches,
    )


def _expand_oversized_current_source_batches(
    batches: List[List[Dict[str, Any]]],
    request: ReviewRequestDto,
    prepared_context: Stage1PreparedContext,
    is_incremental: bool,
    token_budget: int,
) -> List[List[Dict[str, Any]]]:
    """Compatibility wrapper for the joint local-evidence packer."""
    return _expand_oversized_stage1_evidence_batches(
        batches,
        request,
        prepared_context,
        is_incremental,
        token_budget,
    )


def _repack_stage1_batches_by_rendered_input(
    batches: List[List[Dict[str, Any]]],
    request: ReviewRequestDto,
    prepared_context: Stage1PreparedContext,
    is_incremental: bool,
    token_budget: int,
) -> List[List[Dict[str, Any]]]:
    return _pack_repack_stage1_batches(
        batches,
        request,
        prepared_context,
        is_incremental,
        token_budget,
        runtime=_stage1_local_packing_runtime(),
    )


def _partition_oversized_stage1_batch(
    batch_items: List[Dict[str, Any]],
    request: ReviewRequestDto,
    prepared_context: Stage1PreparedContext,
    is_incremental: bool,
    token_budget: int,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    return _pack_partition_stage1_batch(
        batch_items,
        request,
        prepared_context,
        is_incremental,
        token_budget,
        runtime=_stage1_local_packing_runtime(),
    )


# ── Structural Context ────────────────────────────────────────

def _has_exact_proposed_tree_binding(request: ReviewRequestDto) -> bool:
    """Return whether the host supplied every proposed-tree graph input."""
    return has_exact_proposed_tree_binding(request)


def _bounded_nonnegative_int(value: Any, default: int = 0) -> int:
    """Parse optional graph counters without letting enrichment fail Stage 1."""
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, min(parsed, 1_000_000))


def _exact_json_projection(
    value: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Copy one server JSON object without truncating identity-bearing facts."""
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, OverflowError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _bounded_relation_briefing_value(value: Any) -> Any:
    """Bound non-evidence navigation hints before model admission."""
    if isinstance(value, Mapping):
        return {
            str(key)[:160]: _bounded_relation_briefing_value(nested)
            for key, nested in list(value.items())[:24]
        }
    if isinstance(value, (list, tuple)):
        return [
            _bounded_relation_briefing_value(nested)
            for nested in list(value)[:24]
        ]
    if isinstance(value, str):
        return value[:1_200]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1_200]

def _focused_relation_briefing_continuations(
    response: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Expose only model-callable, focused continuations in the capsule.

    The composite backend historically points its frontier back to
    ``exploreReviewContext``. Stage 1 now performs that broad orientation on
    the host, so repeating it would reintroduce the large observation that the
    capsule is intended to replace. Convert named frontier symbols to the
    source-compatible precise graph query and retain already-focused calls.
    """
    raw_continuations = (
        response.get("continuations")
        or evidence.get("frontier")
        or response.get("omittedFollowups")
        or ()
    )
    selected: List[Dict[str, Any]] = []
    for raw_item in raw_continuations:
        if not isinstance(raw_item, Mapping):
            continue
        nested_next = raw_item.get("next")
        nested_next = nested_next if isinstance(nested_next, Mapping) else {}
        tool = str(
            raw_item.get("tool") or nested_next.get("tool") or ""
        ).strip()
        arguments = raw_item.get("arguments")
        if not isinstance(arguments, Mapping):
            arguments = nested_next.get("arguments")
        arguments = arguments if isinstance(arguments, Mapping) else {}
        if tool == STAGE1_REVIEW_CONTEXT_TOOL_NAME:
            focus_symbols = arguments.get("focusSymbols")
            symbol = str(raw_item.get("symbol") or "").strip()
            if not symbol and isinstance(focus_symbols, (list, tuple)):
                symbol = str(next(iter(focus_symbols), "")).strip()
            if not symbol:
                continue
            selected.append({
                "tool": STAGE1_QUERY_GRAPH_TOOL_NAME,
                "reason": "Continue from the bounded relation frontier.",
                "arguments": {
                    "pattern": "relations_of",
                    "target": symbol,
                    "detailLevel": "standard",
                },
            })
        elif tool in STAGE1_STRUCTURAL_TOOL_NAMES:
            item = {
                "tool": tool,
                "arguments": _bounded_relation_briefing_value(arguments),
            }
            reason = str(raw_item.get("reason") or "").strip()
            if reason:
                item["reason"] = reason[:600]
            selected.append(item)
        if len(selected) >= 4:
            break
    return selected


def _stage1_relation_briefing_capsule(
    response: Optional[Dict[str, Any]],
    *,
    max_characters: int = STAGE1_RELATION_BRIEFING_MAX_CHARS,
) -> tuple[str, Dict[str, Any]]:
    """Select the exact relation facts that are guaranteed prompt-visible."""
    if not isinstance(response, dict):
        return "", {}
    status = str(response.get("status") or "").strip().casefold()
    snapshot = response.get("snapshot")
    if (
        status not in {"ready", "ok", "complete"}
        or not isinstance(snapshot, Mapping)
        or snapshot.get("kind") != "proposed_tree"
    ):
        return "", {}

    evidence = response.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    raw_edges = response.get("edges")
    if not isinstance(raw_edges, list):
        raw_edges = (
            evidence.get("relations")
            or response.get("relations")
            or response.get("results")
        )
    edges = []
    for raw_edge in raw_edges or ():
        if not isinstance(raw_edge, Mapping):
            continue
        evidence_id = str(
            raw_edge.get("evidenceId")
            or raw_edge.get("evidence_id")
            or ""
        ).strip()
        if (
            not _CANONICAL_STRUCTURAL_EVIDENCE_ID.fullmatch(evidence_id)
            or not isinstance(raw_edge.get("kind"), str)
            or not raw_edge.get("kind")
            or not isinstance(raw_edge.get("relation"), str)
            or not raw_edge.get("relation")
            or not isinstance(raw_edge.get("source"), str)
            or not raw_edge.get("source")
            or not isinstance(raw_edge.get("target"), str)
            or not raw_edge.get("target")
        ):
            continue
        edge = _exact_json_projection({
            key: raw_edge[key]
            for key in (
                "evidenceId",
                "kind",
                "relation",
                "source",
                "target",
                "sourceUnitId",
                "targetUnitId",
                "origin",
                "relatedPaths",
                "attributes",
                "hop",
                "depth",
            )
            if raw_edge.get(key) not in (None, "", [], {})
        })
        if edge is None:
            continue
        edge["evidenceId"] = evidence_id
        # Standard graph-query records expose endpoint units rather than the
        # compact endpoint IDs. Derive only these navigation hints; canonical
        # relation fact fields above remain byte-for-byte JSON projections.
        for unit_key, id_key in (
            ("sourceUnit", "sourceUnitId"),
            ("targetUnit", "targetUnitId"),
        ):
            unit = raw_edge.get(unit_key)
            if id_key not in edge and isinstance(unit, Mapping):
                unit_id = unit.get("unitId")
                if isinstance(unit_id, str) and unit_id:
                    edge[id_key] = unit_id
        if "hop" in edge:
            edge["hop"] = _bounded_nonnegative_int(edge["hop"])
        if "depth" in edge:
            edge["depth"] = _bounded_nonnegative_int(edge["depth"])
        edges.append(edge)

    if not edges:
        return "", {}

    node_by_id: Dict[str, Dict[str, Any]] = {}
    raw_nodes = response.get("nodes")
    if not isinstance(raw_nodes, list):
        raw_nodes = evidence.get("nodes")
    for raw_node in raw_nodes or ():
        if not isinstance(raw_node, Mapping):
            continue
        unit_id = raw_node.get("unitId")
        if not isinstance(unit_id, str) or not unit_id:
            continue
        node = _exact_json_projection({
            key: raw_node[key]
            for key in (
                "unitId",
                "path",
                "kind",
                "name",
                "qualifiedName",
                "startLine",
                "endLine",
                "language",
                "depth",
            )
            if raw_node.get(key) not in (None, "", [], {})
        })
        if node is not None:
            node_by_id[unit_id] = node

    source_candidates: List[Dict[str, Any]] = []
    for raw_window in response.get("sourceWindows") or ():
        if not isinstance(raw_window, Mapping):
            continue
        path = raw_window.get("path")
        content = raw_window.get("content")
        if (
            not isinstance(path, str)
            or not path
            or not isinstance(content, str)
            or not content
        ):
            continue
        source_window = _exact_json_projection({
            key: raw_window[key]
            for key in (
                "evidenceId",
                "unitId",
                "path",
                "startLine",
                "endLine",
                "content",
                "contentSha256",
                "changedFile",
                "truncated",
                "relationEvidenceIds",
            )
            if raw_window.get(key) not in (None, "", [], {})
        })
        if source_window is None:
            continue
        # Capsule admission skips a whole exact window when it does not fit; it
        # never slices or relabels source while retaining stale integrity data.
        source_candidates.append(source_window)

    coverage = response.get("coverage")
    coverage = coverage if isinstance(coverage, Mapping) else {}
    selected_edges: List[Dict[str, Any]] = []
    selected_windows: List[Dict[str, Any]] = []

    def build_payload(
        candidate_edges: Sequence[Dict[str, Any]],
        candidate_windows: Sequence[Dict[str, Any]] = (),
    ) -> Dict[str, Any]:
        referenced_ids = {
            str(edge.get(key) or "")
            for edge in candidate_edges
            for key in ("sourceUnitId", "targetUnitId")
            if edge.get(key)
        }
        candidate_nodes = [
            node_by_id[unit_id]
            for unit_id in sorted(referenced_ids)
            if unit_id in node_by_id
        ]
        changed = response.get("changed")
        changed = changed if isinstance(changed, Mapping) else {}
        return {
            "kind": "proposed_tree_relation_briefing",
            "focusPaths": list(
                response.get("focusPaths")
                or changed.get("focusPaths")
                or ()
            ),
            "relations": list(candidate_edges),
            "nodes": candidate_nodes,
            "sourceWindows": list(candidate_windows),
            "coverage": {
                "state": str(
                    coverage.get("graphState")
                    or coverage.get("state")
                    or "unknown"
                ),
                "serverTruncated": bool(
                    coverage.get("truncated")
                    or coverage.get("partialReasons")
                ),
                "depthReached": max(
                    (
                        _bounded_nonnegative_int(
                            edge.get("hop", edge.get("depth", 0))
                        )
                        for edge in candidate_edges
                    ),
                    default=0,
                ),
                "sourceIncluded": bool(candidate_windows),
                "availableRelations": len(edges),
                "shownRelations": len(candidate_edges),
                "omittedFromBriefing": max(0, len(edges) - len(candidate_edges)),
                "partialReasons": list(coverage.get("partialReasons") or ()),
            },
            "focusedFollowUps": list(
                response.get("nextOperations")
                or ("queryCodeGraph", "getImpactRadius", "traverseCodeGraph", "getStructuralUnit")
            )[:6],
            "continuations": list(
                _focused_relation_briefing_continuations(response, evidence)
            ),
        }

    prefix = (
        "RELATION-FIRST PROPOSED-TREE BRIEFING (bounded exact related source "
        "may be included):\n"
        "These exact typed relations are already visible; do not repeat the "
        "orientation call. Use their unit IDs/continuations for a focused graph "
        "follow-up. Use a complete source window directly and read another source "
        "range only for a concrete unresolved code question.\n"
    )
    relation_character_limit = max_characters
    if source_candidates:
        relation_character_limit -= min(4_000, max_characters // 3)
    # Preserve the backend ranking while guaranteeing that a returned deeper
    # hop is not hidden behind a relation-dense direct neighborhood.
    ordered_edges: List[Dict[str, Any]] = []
    promoted_indexes: set[int] = set()
    for hop in sorted({
        _bounded_nonnegative_int(
            edge.get("hop", edge.get("depth", 0))
        )
        for edge in edges
    }):
        for index, edge in enumerate(edges):
            if _bounded_nonnegative_int(
                edge.get("hop", edge.get("depth", 0))
            ) == hop:
                ordered_edges.append(edge)
                promoted_indexes.add(index)
                break
    ordered_edges.extend(
        edge for index, edge in enumerate(edges) if index not in promoted_indexes
    )
    for edge in ordered_edges:
        candidate = build_payload((*selected_edges, edge))
        serialized = json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if len(prefix) + len(serialized) > relation_character_limit:
            # A canonical evidence ID names the complete relation fact. Skip an
            # oversized edge as a whole instead of removing attributes or
            # truncating paths while retaining that ID.
            continue
        selected_edges.append(edge)

    if not selected_edges:
        return "", {}
    selected_evidence_ids = {
        str(edge.get("evidenceId") or "") for edge in selected_edges
    }
    ordered_source_candidates = sorted(
        source_candidates,
        key=lambda window: (
            not bool(selected_evidence_ids.intersection(
                str(value)
                for value in window.get("relationEvidenceIds") or ()
            )),
            str(window.get("path") or ""),
            _bounded_nonnegative_int(window.get("startLine")),
        ),
    )
    for window in ordered_source_candidates:
        candidate = build_payload(
            selected_edges,
            (*selected_windows, window),
        )
        serialized = json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if len(prefix) + len(serialized) > max_characters:
            continue
        selected_windows.append(window)
    payload = build_payload(selected_edges, selected_windows)
    selected_nodes = list(payload["nodes"])
    text = prefix + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    visible_response = {
        "status": "ready",
        "snapshot": dict(snapshot),
        "focusPaths": list(payload["focusPaths"]),
        "nodes": selected_nodes,
        "edges": list(selected_edges),
        "sourceWindows": list(selected_windows),
        "coverage": dict(payload["coverage"]),
        "nextOperations": list(payload["focusedFollowUps"]),
        "continuations": list(payload["continuations"]),
    }
    return text, visible_response


def _safe_stage1_relation_briefing_capsule(
    response: Optional[Dict[str, Any]],
    *,
    max_characters: int = STAGE1_RELATION_BRIEFING_MAX_CHARS,
) -> tuple[str, Dict[str, Any]]:
    """Keep malformed optional graph output from aborting the core review."""
    try:
        return _stage1_relation_briefing_capsule(
            response,
            max_characters=max_characters,
        )
    except Exception as exception:
        logger.warning(
            "Optional Stage 1 relation briefing was malformed; continuing "
            "without it: %s",
            exception,
        )
        return "", {}


def _bounded_proposed_review_context(
    response: Optional[Dict[str, Any]],
    *,
    max_characters: int,
) -> tuple[str, Dict[str, Any]]:
    """Return one whole bounded recovery observation and its visible facts."""
    if max_characters <= 0 or not isinstance(response, dict):
        return "", {}
    if response.get("status") not in {None, "ok", "complete", "ready"}:
        return "", {}
    snapshot = response.get("snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("kind") != "proposed_tree":
        return "", {}

    capsule, visible_response = _safe_stage1_relation_briefing_capsule(
        response,
        max_characters=max_characters,
    )
    if capsule:
        return capsule, visible_response

    # A structural-unit response may contain exact source but no relation. It
    # is useful in recovery only when the entire JSON observation fits; slicing
    # it could detach content from its integrity and offset metadata.
    if not response.get("unit"):
        return "", {}
    prefix = (
        "EXACT PROPOSED-TREE STRUCTURAL UNIT RETRIEVED BEFORE AGENT "
        "DEGRADATION:\n"
    )
    try:
        serialized = json.dumps(
            response,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError):
        return "", {}
    text = prefix + serialized
    return (text, {}) if len(text) <= max_characters else ("", {})


def format_proposed_review_context(
    response: Optional[Dict[str, Any]],
    *,
    max_characters: int = STAGE1_RELATION_BRIEFING_MAX_CHARS,
) -> str:
    """Serialize one bounded, exact proposed-tree recovery observation."""
    text, _visible_response = _bounded_proposed_review_context(
        response,
        max_characters=max_characters,
    )
    return text


def structural_relation_evidence(
    response: Optional[Dict[str, Any]],
) -> Dict[str, tuple[Dict[str, Any], ...]]:
    """Expose only canonical relation facts under their stable evidence IDs."""
    if not isinstance(response, dict):
        return {}
    context = response.get("context")
    data = context if isinstance(context, dict) else response
    evidence = data.get("evidence")
    relations = (
        evidence.get("relations")
        if isinstance(evidence, dict)
        else None
    ) or data.get("relations") or data.get("edges") or data.get("results")
    if not isinstance(relations, list):
        return {}

    visible: Dict[str, tuple[Dict[str, Any], ...]] = {}
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        evidence_id = str(
            relation.get("evidenceId")
            or relation.get("evidence_id")
            or ""
        ).strip()
        if not _CANONICAL_STRUCTURAL_EVIDENCE_ID.fullmatch(evidence_id):
            continue
        if not all(
            isinstance(relation.get(key), str) and relation.get(key)
            for key in ("kind", "source", "relation", "target")
        ):
            continue
        origin = relation.get("origin")
        if not isinstance(origin, dict):
            continue
        path = origin.get("path") or relation.get("path")
        line = origin.get("line") or relation.get("line")
        if (
            not isinstance(path, str)
            or not path
            or isinstance(line, bool)
            or not isinstance(line, int)
            or line < 1
        ):
            continue
        attributes = relation.get("attributes")
        if attributes is not None and not isinstance(attributes, dict):
            continue
        attributes = attributes or {}
        related_paths = (
            relation.get("relatedPaths")
            or relation.get("related_paths")
            or ()
        )
        if (
            not isinstance(related_paths, (list, tuple))
            or any(not isinstance(path, str) for path in related_paths)
        ):
            continue
        fact = {
            "kind": relation["kind"],
            "source": relation["source"],
            "relation": relation["relation"],
            "target": relation["target"],
            "path": path,
            "line": line,
            "attributes": dict(attributes),
            "related_paths": tuple(
                path
                for path in related_paths
                if path
            ),
        }
        _merge_structural_evidence(visible, {evidence_id: (fact,)})
    return visible


def _decoded_tool_observation(observation: Any) -> Any:
    """Decode structured MCP observations without extracting IDs from prose."""
    if isinstance(observation, str):
        try:
            return json.loads(observation)
        except (TypeError, ValueError):
            return None
    if isinstance(observation, (Mapping, list, tuple)):
        return observation
    model_dump = getattr(observation, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except (TypeError, ValueError):
            return None
    return None


def _proposed_tree_tool_observation(observation: Any) -> Optional[Dict[str, Any]]:
    """Unwrap only a successful request-bound proposed-tree tool envelope."""
    decoded = _decoded_tool_observation(observation)
    if not isinstance(decoded, Mapping):
        return None
    payload: Mapping[str, Any] = decoded
    for wrapper_key in (
        "structuredContent",
        "structured_content",
        "context",
        "data",
        "result",
    ):
        nested = payload.get(wrapper_key)
        if isinstance(nested, Mapping):
            payload = nested
    status = str(payload.get("status") or "").strip().casefold()
    snapshot = payload.get("snapshot")
    if (
        status not in {"ready", "ok", "complete"}
        or not isinstance(snapshot, Mapping)
        or snapshot.get("kind") != "proposed_tree"
    ):
        return None
    return dict(payload)


def _structural_relations_in_observation(observation: Any) -> List[Dict[str, Any]]:
    """Read relation records only from trusted structural response envelopes.

    Source windows and structural units contain repository-controlled text. We
    deliberately never recurse through arbitrary values or decode nested
    strings, so JSON source cannot impersonate a graph-store relation record.
    """
    decoded = _decoded_tool_observation(observation)
    relations: List[Dict[str, Any]] = []

    def add_relation(value: Any) -> None:
        if not isinstance(value, Mapping):
            return
        evidence_id = str(
            value.get("evidenceId") or value.get("evidence_id") or ""
        ).strip()
        if (
            _CANONICAL_STRUCTURAL_EVIDENCE_ID.fullmatch(evidence_id)
            and all(key in value for key in ("kind", "source", "relation", "target"))
        ):
            relations.append(dict(value))

    def visit_envelope(value: Any) -> None:
        if not isinstance(value, Mapping):
            if isinstance(value, (list, tuple)):
                for candidate in value:
                    add_relation(candidate)
            return
        add_relation(value)
        for key in ("relations", "edges", "results"):
            collection = value.get(key)
            if isinstance(collection, (list, tuple)):
                for candidate in collection:
                    add_relation(candidate)
        for key in (
            "structuredContent",
            "structured_content",
            "context",
            "data",
            "result",
            "evidence",
        ):
            nested = value.get(key)
            if isinstance(nested, Mapping):
                visit_envelope(nested)

    visit_envelope(decoded)
    return relations


def structural_tool_observation_evidence(
    tool_name: str,
    observation: Any,
) -> Dict[str, tuple[Dict[str, Any], ...]]:
    """Return canonical relation evidence exposed by a structural MCP call.

    VCS reads and arbitrary identifier-shaped fields are deliberately excluded:
    only observations from the request-bound structural tools are inspected, and
    only the graph store's ``relation:<sha256>`` identity is accepted.
    """
    if tool_name not in STAGE1_STRUCTURAL_OBSERVATION_TOOL_NAMES:
        return {}
    payload = _proposed_tree_tool_observation(observation)
    if payload is None:
        return {}
    relations = _structural_relations_in_observation(payload)
    if not relations:
        return {}
    return structural_relation_evidence({"relations": relations})


def _merge_structural_evidence(
    target: Dict[str, tuple[Dict[str, Any], ...]],
    incoming: Dict[str, tuple[Dict[str, Any], ...]],
) -> None:
    """Merge bounded relation facts without depending on batch completion order."""
    for evidence_id, facts in incoming.items():
        canonical: Dict[str, Dict[str, Any]] = {}
        for fact in (*target.get(evidence_id, ()), *facts):
            key = json.dumps(
                fact,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            canonical[key] = fact
        target[evidence_id] = tuple(canonical[key] for key in sorted(canonical))


def _record_structural_retrieval_state(
    rag_state: Optional[Stage1RagState],
    response: Optional[Dict[str, Any]],
) -> None:
    if rag_state is None:
        return
    state = _structural_retrieval_state(response)
    if state not in rag_state.deterministic_retrieval_states:
        rag_state.deterministic_retrieval_states.append(state)


def _structural_retrieval_state(
    response: Optional[Dict[str, Any]],
) -> str:
    """Return truthful graph coverage for one structural tool observation."""
    if not isinstance(response, dict):
        return "unavailable"

    top_status = str(response.get("status") or "").strip().casefold()
    if top_status in {"error", "failed"} or response.get("error"):
        return "unavailable"
    if (
        top_status in {"unavailable", "disabled"}
        or response.get("unavailable") is True
    ):
        return "unavailable"

    context = response.get("context")
    data = context if isinstance(context, dict) else response
    nested_status = str(data.get("status") or "").strip().casefold()
    if nested_status in {"error", "failed"} or data.get("error"):
        return "unavailable"
    if (
        nested_status in {"unavailable", "disabled"}
        or data.get("unavailable") is True
    ):
        return "unavailable"

    coverage = data.get("coverage")
    if isinstance(coverage, dict):
        coverage_state = str(
            coverage.get("graphState") or coverage.get("state") or ""
        ).strip().casefold()
        if coverage_state == "complete_for_query":
            return "complete"
        if coverage_state:
            return coverage_state

    successful_status = nested_status or top_status
    if successful_status in {"ok", "complete", "ready"}:
        return "complete"
    if successful_status:
        return successful_status
    return "unavailable"


def _tool_event_name(event: Any) -> str:
    action = getattr(event, "action", None)
    value = (
        action.get("tool", "")
        if isinstance(action, Mapping)
        else getattr(action, "tool", "")
    )
    return str(value or "unknown")


def _validate_required_agent_tool_sequence(
    events: Sequence[Any],
    required_tool_names: Sequence[str],
) -> None:
    """Prove that each required graph operation completed in order.

    Providers may emit more than one call for the currently available graph
    operation in a single model turn. Repeating an operation that has already
    completed does not skip the workflow; it only gathers another focused
    result. Keep rejecting unavailable operations, forward skips, and missing
    required steps.
    """
    if not required_tool_names:
        return

    next_index = 0
    for event in events:
        if next_index >= len(required_tool_names):
            break
        observed_name = _tool_event_name(event)
        required_name = required_tool_names[next_index]
        if observed_name == required_name:
            next_index += 1
        elif observed_name not in required_tool_names[:next_index]:
            raise RuntimeError(
                "Required Stage 1 graph workflow was violated: expected "
                f"{required_name} at step {next_index + 1}, observed "
                f"{observed_name}"
            )
        failures = stage1_agent_tool_event_failures((event,))
        if failures:
            raise RuntimeError(
                "Required Stage 1 graph operation failed: " + failures[0]
            )

    if next_index != len(required_tool_names):
        missing = required_tool_names[next_index:]
        raise RuntimeError(
            "Required Stage 1 graph workflow was incomplete; missing: "
            + ", ".join(missing)
        )


def _consume_stage1_agent_tool_events(
    events: Sequence[Any],
    *,
    visible_evidence_by_id: Optional[
        Dict[str, tuple[Dict[str, Any], ...]]
    ],
    rag_state: Optional[Stage1RagState],
    context_holder: Optional[Dict[str, Any]],
) -> Counter:
    """Account completed tool results, including a transcript before failure."""
    tool_counts: Counter = Counter()
    observed_evidence: Dict[str, tuple[Dict[str, Any], ...]] = {}
    structural_observations: List[Dict[str, Any]] = []
    for event in events:
        normalized_tool_name = _tool_event_name(event)
        tool_counts[normalized_tool_name] += 1
        observation = getattr(event, "observation", None)
        _merge_structural_evidence(
            observed_evidence,
            structural_tool_observation_evidence(
                normalized_tool_name,
                observation,
            ),
        )
        if normalized_tool_name in STAGE1_STRUCTURAL_OBSERVATION_TOOL_NAMES:
            decoded = _decoded_tool_observation(observation)
            if isinstance(decoded, Mapping):
                payload: Mapping[str, Any] = decoded
                for wrapper_key in (
                    "structuredContent",
                    "structured_content",
                    "context",
                    "data",
                    "result",
                ):
                    nested = payload.get(wrapper_key)
                    if isinstance(nested, Mapping):
                        payload = nested
                structural_observations.append(dict(payload))
                snapshot = payload.get("snapshot")
                if (
                    context_holder is not None
                    and isinstance(snapshot, Mapping)
                    and snapshot.get("kind") == "proposed_tree"
                ):
                    context_holder["response"] = dict(payload)
                    responses = context_holder.setdefault("responses", [])
                    if isinstance(responses, list) and len(responses) < 8:
                        responses.append(dict(payload))

    if visible_evidence_by_id is not None:
        _merge_structural_evidence(
            visible_evidence_by_id,
            observed_evidence,
        )
    if rag_state is not None:
        _merge_structural_evidence(
            rag_state.exact_evidence_by_id,
            observed_evidence,
        )
        for observation in structural_observations:
            _record_structural_retrieval_state(rag_state, observation)
    return tool_counts


# ── Batch Review ──────────────────────────────────────────────


async def execute_stage_1_file_reviews(
    llm,
    request: ReviewRequestDto,
    plan: ReviewPlan,
    rag_client,
    processed_diff: Optional[ProcessedDiff] = None,
    is_incremental: bool = False,
    max_parallel: int = 5,
    event_callback: Optional[Callable[[Dict], None]] = None,
    fallback_llm=None,
    rag_state: Optional[Stage1RagState] = None,
    review_unit_state: Optional[Stage1ReviewUnitState] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
    inference_profile: Optional[ReviewInferenceProfile] = None,
    agent_service: Optional["AgentExecutionService"] = None,
) -> List[CodeReviewIssue]:
    prepared_context = _build_stage_1_prepared_context(request, processed_diff, is_incremental)
    inference_profile = inference_profile or build_review_inference_profile(
        request,
        processed_diff,
    )
    rag_state = rag_state or Stage1RagState()
    review_unit_state = review_unit_state or Stage1ReviewUnitState()
    batches = await create_smart_batches_wrapper(
        file_groups=plan.file_groups,
        processed_diff=prepared_context.diff_source,
        request=request,
        rag_client=rag_client,
        max_files_per_batch=STAGE1_MAX_FILES_PER_BATCH,
        prepared_context=prepared_context,
        is_incremental=is_incremental,
    )
    stage1_token_budget = _stage1_packing_token_limit(request)
    batches = _repack_stage1_batches_by_rendered_input(
        batches,
        request,
        prepared_context,
        is_incremental,
        stage1_token_budget,
    )
    configured_stage1_invocation_cap = inference_profile.invocation_cap(
        "stage_1_total"
    )
    # Every graph component batch owns changed-file/hunk coverage and must run
    # at least once. The profile cap limits supplemental oversized-evidence
    # passes; it must never discard a core file-review prompt.
    stage1_invocation_cap = max(
        configured_stage1_invocation_cap,
        len(batches),
    )
    if stage1_invocation_cap > configured_stage1_invocation_cap:
        logger.info(
            "Stage 1 raised the invocation allowance from %d to %d so every "
            "core dependency batch is reviewed",
            configured_stage1_invocation_cap,
            stage1_invocation_cap,
        )
    batches = _expand_oversized_stage1_evidence_batches(
        batches,
        request,
        prepared_context,
        is_incremental,
        stage1_token_budget,
        max_units_per_item=inference_profile.invocation_cap(
            "stage_1_per_unit"
        ),
        max_total_batches=stage1_invocation_cap,
    )
    admitted_stage1_invocations = _allocate_stage1_invocation_quotas(
        batches,
        stage1_invocation_cap,
        inference_profile.invocation_cap("stage_1_per_unit"),
    )
    if (
        agent_service is not None
        and getattr(request, "mcpLocalOnly", False) is True
    ):
        stage1_agent_call_limit = STAGE1_AGENT_MAX_STEPS + 2
        logger.info(
            "Stage 1 provider-call ceiling: semantic_invocations=%d, "
            "repository_agent_invocations<=%d, agent_model_calls<=%d, "
            "direct_recovery_calls=0, total_calls<=%d",
            admitted_stage1_invocations,
            admitted_stage1_invocations * 2,
            admitted_stage1_invocations * stage1_agent_call_limit * 2,
            admitted_stage1_invocations * stage1_agent_call_limit * 2,
        )
    elif agent_service is not None:
        stage1_agent_call_limit = STAGE1_AGENT_MAX_STEPS + 2
        logger.info(
            "Stage 1 provider-call ceiling: semantic_invocations=%d, "
            "agent_model_calls<=%d, direct_recovery_calls<=%d, "
            "total_calls<=%d",
            admitted_stage1_invocations,
            admitted_stage1_invocations * stage1_agent_call_limit,
            admitted_stage1_invocations * 2,
            admitted_stage1_invocations * (
                stage1_agent_call_limit + 2
            ),
        )
    else:
        logger.info(
            "Stage 1 provider-call ceiling: semantic_invocations=%d, "
            "primary_calls=%d, direct_output_recovery_calls<=%d, "
            "total_calls<=%d",
            admitted_stage1_invocations,
            admitted_stage1_invocations,
            admitted_stage1_invocations,
            admitted_stage1_invocations * 2,
        )
    review_unit_state.register_batches(batches)

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

    async def _run_batch(
        batch_idx: int,
        batch: List[Dict[str, Any]],
    ) -> tuple[int, List[CodeReviewIssue], tuple[str, ...]]:
        unit_ids = review_unit_state.unit_ids_for_batch(batch_idx, batch)
        async with semaphore:
            batch_paths = [item["file"].path for item in batch]
            has_rels = any(item.get('has_relationships') for item in batch)
            logger.debug(f"Batch {batch_idx}: {batch_paths} (cross-file relationships: {has_rels})")
            result = await _review_batch_with_timing(
                batch_idx, llm, request, batch, rag_client, prepared_context,
                is_incremental,
                fallback_llm=fallback_llm,
                rag_state=rag_state,
                candidate_ledger=candidate_ledger,
                agent_service=agent_service,
                event_callback=event_callback,
            )
            return batch_idx, result, unit_ids

    tasks = [
        asyncio.create_task(_run_batch(batch_idx, batch))
        for batch_idx, batch in enumerate(batches, start=1)
    ]

    try:
        for completed_task in asyncio.as_completed(tasks):
            try:
                batch_num, res, unit_ids = await completed_task
                review_unit_state.mark_completed(unit_ids)
                batch_results[batch_num] = res or []
                if res:
                    logger.info(
                        f"Batch {batch_num} completed: {len(res)} issues found"
                    )
                else:
                    logger.info(f"Batch {batch_num} completed: no issues found")
            except Exception as exc:
                logger.debug(
                    "Stage 1 batch failed; cancelling sibling batches: %s",
                    exc,
                )
                raise RuntimeError(
                    "Stage 1 review is incomplete because at least one batch "
                    "failed"
                ) from exc
            finally:
                completed_batches += 1
                progress = 10 + int((completed_batches / len(batches)) * 50)
                emit_progress(
                    event_callback,
                    progress,
                    f"Stage 1: Reviewed {completed_batches}/{len(batches)} "
                    "batches",
                )
    finally:
        # A streaming disconnect cancels the owning review coroutine. Join all
        # prompt agents before ReviewService closes their request-owned MCP
        # sessions, otherwise an orphaned batch can write to a closed stdio
        # transport while it is still unwinding.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    review_unit_state.assert_complete()
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
    fallback_llm=None,
    rag_state: Optional[Stage1RagState] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
    agent_service: Optional["AgentExecutionService"] = None,
    event_callback: Optional[Callable[[Dict], None]] = None,
) -> List[CodeReviewIssue]:
    start_time = time.monotonic()
    batch_paths = [item["file"].path for item in batch]
    telemetry = Stage1AgentTelemetryRecorder(
        batch_number=batch_idx,
        batch_paths=tuple(batch_paths),
        review_unit_ids=tuple(
            unit_id
            for item in batch
            if isinstance(
                unit_id := item.get("_review_unit_id"),
                str,
            ) and unit_id
        ),
        agent_requested=bool(agent_service is not None and request.useMcpTools),
        source_revision=request.currentCommitHash or request.commitHash,
    )
    logger.info(f"[Batch {batch_idx}] STARTED - files: {batch_paths}")

    try:
        result = await review_file_batch(
            llm, request, batch, rag_client, prepared_context, is_incremental,
            fallback_llm=fallback_llm,
            rag_state=rag_state,
            candidate_ledger=candidate_ledger,
            agent_service=agent_service,
            event_callback=event_callback,
            agent_telemetry=telemetry,
        )
        telemetry.record_issues(result)
        telemetry.finish(status="completed")
        elapsed = time.monotonic() - start_time
        logger.info(f"[Batch {batch_idx}] FINISHED in {elapsed:.2f}s - {len(result)} issues")
        return result
    except BaseException as e:
        telemetry.finish(status="failed", error=e)
        elapsed = time.monotonic() - start_time
        logger.debug(f"[Batch {batch_idx}] FAILED after {elapsed:.2f}s: {e}")
        raise
    finally:
        telemetry.emit(event_callback)


async def review_file_batch(
    llm,
    request: ReviewRequestDto,
    batch_items: List[Dict[str, Any]],
    rag_client,
    prepared_context: Optional[Stage1PreparedContext] = None,
    is_incremental: bool = False,
    fallback_llm=None,
    rag_state: Optional[Stage1RagState] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
    agent_service: Optional["AgentExecutionService"] = None,
    event_callback: Optional[Callable[[Dict], None]] = None,
    agent_telemetry: Optional[Stage1AgentTelemetryRecorder] = None,
) -> List[CodeReviewIssue]:
    if prepared_context is not None and not isinstance(prepared_context, Stage1PreparedContext):
        # Backwards compatibility for older direct callers/tests that pass
        # ProcessedDiff as the fifth positional argument.
        prepared_context = _build_stage_1_prepared_context(request, prepared_context, is_incremental)
    elif prepared_context is None:
        prepared_context = _build_stage_1_prepared_context(request, None, is_incremental)
    material = _prepare_stage1_prompt_material(
        request,
        batch_items,
        prepared_context,
        is_incremental,
    )
    batch_file_paths = material.batch_file_paths
    requested_agentic_mode = (
        agent_service is not None and bool(request.useMcpTools)
    )
    available_agent_tools = (
        _available_stage1_agent_tools(agent_service)
        if requested_agentic_mode
        else frozenset()
    )
    repository_agent_tools = STAGE1_VCS_TOOL_NAMES.intersection(
        available_agent_tools
    )
    review_overlay_path = getattr(request, "localReviewOverlayPath", None)
    review_file_tool_available = bool(
        STAGE1_REVIEW_FILE_TOOL_NAME in repository_agent_tools
        and isinstance(review_overlay_path, str)
        and review_overlay_path.strip()
    )
    if not review_file_tool_available:
        repository_agent_tools = repository_agent_tools.difference({
            STAGE1_REVIEW_FILE_TOOL_NAME,
        })
    else:
        # One request-bound read tool covers both proposed and unchanged paths.
        # Removing the target-only alternative prevents a model from silently
        # selecting stale source for a changed path discovered through the graph.
        repository_agent_tools = repository_agent_tools.difference({
            STAGE1_BRANCH_FILE_TOOL_NAME,
        })
    agentic_mode = bool(requested_agentic_mode and repository_agent_tools)
    available_structural_tools = STAGE1_STRUCTURAL_TOOL_NAMES.intersection(
        available_agent_tools
    )
    structural_inventory_complete = (
        available_structural_tools == STAGE1_STRUCTURAL_TOOL_NAMES
    )
    structural_agent_mode = bool(
        agentic_mode
        and structural_inventory_complete
        and _has_exact_proposed_tree_binding(request)
    )
    local_only_agent_required = getattr(request, "mcpLocalOnly", False) is True
    structural_agent_required = (
        getattr(request, "requireStructuralMcp", False) is True
    )
    agent_execution_required = (
        local_only_agent_required or structural_agent_required
    )
    if structural_agent_required:
        missing_structural_tools = sorted(
            STAGE1_STRUCTURAL_TOOL_NAMES.difference(
                available_structural_tools
            )
        )
        if missing_structural_tools:
            raise RuntimeError(
                "Required Stage 1 structural MCP inventory is incomplete; "
                "missing: " + ", ".join(missing_structural_tools)
            )
        if not structural_agent_mode:
            raise RuntimeError(
                "Required Stage 1 structural MCP has no exact sealed "
                "proposed-tree binding; source-only fallback is disabled"
            )
    if agent_execution_required and (
        not requested_agentic_mode
        or not review_file_tool_available
        or not agentic_mode
    ):
        raise RuntimeError(
            "Required Stage 1 MCP execution needs getReviewFileContent with "
            "its exact request binding; "
            "direct review fallback is disabled"
        )
    required_structural_tool_sequence = (
        STAGE1_REQUIRED_STRUCTURAL_TOOL_SEQUENCE
        if structural_agent_mode
        else ()
    )
    agent_tool_names = frozenset(
        repository_agent_tools
        | (available_structural_tools if structural_agent_mode else frozenset())
    )
    token_budget = _stage1_batch_token_limit(request)
    # Proposed-tree mutation is completed once before MCP startup. Stage 1 does
    # not issue a hidden per-batch graph request: eligible batches make one
    # observable required model tool call against the sealed read-only receipt.
    preloaded_structural_context = ""
    preloaded_structural_evidence: Dict[
        str, tuple[Dict[str, Any], ...]
    ] = {}
    relation_briefing_response: Optional[Dict[str, Any]] = None
    visible_briefing_response: Dict[str, Any] = {}
    relation_briefing_eligible = False
    relation_briefing_attempted = False

    if not material.file_metadata_text:
        logger.debug(f"No structured parser metadata for batch {batch_file_paths}")
    invocations = _build_stage1_invocations(
        material,
        use_mcp_tools=agentic_mode,
        structural_tools_available=structural_agent_mode,
        review_file_tool_available=review_file_tool_available,
        preloaded_structural_context=preloaded_structural_context,
        preloaded_structural_evidence=preloaded_structural_evidence,
    )
    prompt = invocations[0][0]
    estimated_tokens = _estimated_prompt_tokens(prompt)

    if rag_state is not None:
        _merge_structural_evidence(
            rag_state.exact_evidence_by_id,
            preloaded_structural_evidence,
        )
    if agent_telemetry is not None:
        agent_telemetry.record_generation_preparation(
            status=getattr(request, "ragReviewGenerationStatus", None),
            collection_target=getattr(
                request,
                "ragReviewCollectionTarget",
                None,
            ),
            generation_manifest_sha256=getattr(
                request,
                "ragReviewGenerationManifestSha256",
                None,
            ),
            error=getattr(request, "ragReviewGenerationError", None),
        )
        agent_telemetry.record_relation_briefing(
            eligible=relation_briefing_eligible,
            attempted=relation_briefing_attempted,
            response=relation_briefing_response,
            visible_response=visible_briefing_response,
            prompt_context=preloaded_structural_context,
            visible_evidence_ids=tuple(preloaded_structural_evidence),
        )
    if estimated_tokens > token_budget and len(batch_items) > 1:
        logger.warning(
            "Stage 1 final multi-file prompt exceeded the packing target; "
            "retaining the admitted core batch: paths=%s "
            "estimated_tokens=%d target_tokens=%d",
            batch_file_paths,
            estimated_tokens,
            token_budget,
        )
    all_issues: List[CodeReviewIssue] = []
    for invocation_index, (
        invocation_prompt,
        invocation_structural_context,
        invocation_plugin_context,
        invocation_visible_evidence,
    ) in enumerate(invocations, start=1):
        invocation_tokens = _estimated_prompt_tokens(invocation_prompt)
        logger.info(
            "Stage 1 prompt assembled: total=%d chars, estimated_tokens=%d, "
            "target_tokens=%d, metadata=%d, structural=%d, plugin=%d, "
            "files=%d",
            len(invocation_prompt),
            invocation_tokens,
            token_budget,
            len(material.file_metadata_text),
            len(invocation_structural_context),
            len(invocation_plugin_context),
            len(batch_file_paths),
        )
        record_prompt_diagnostic({
            "stage": "stage_1",
            "agentPhase": (
                "agent" if agentic_mode else "direct"
            ),
            "batchPaths": sorted(batch_file_paths),
            "fileCount": len(batch_file_paths),
            "totalPromptChars": len(invocation_prompt),
            "currentSourceChars": sum(
                len(str(item.get("current_code") or ""))
                for item in material.batch_files_data
            ),
            "currentSourcePerFileBudget": material.current_source_per_file_budget,
            "diffChars": sum(
                len(str(item.get("diff") or ""))
                for item in material.batch_files_data
            ),
            "metadataChars": len(material.file_metadata_text),
            "structuralContextChars": len(invocation_structural_context),
            "pluginChars": len(invocation_plugin_context),
            "projectRulesChars": len(material.project_rules),
            "taskContextChars": len(material.task_context),
            "previousIssuesChars": len(material.previous_issues_for_batch),
            "boundaryContextChars": len(material.boundary_context),
            "estimatedInputTokens": invocation_tokens,
            "inputPackingTargetTokens": token_budget,
            "omittedStage1Units": sum(
                int(item.get("_omitted_stage1_unit_count", 0) or 0)
                for item in batch_items
            ),
            "omittedStage1Hunks": sum(
                len(item.get("_omitted_hunk_ids", ()) or ())
                for item in batch_items
            ),
        })

        fallback_cache: Dict[
            tuple[str, ...],
            _Stage1DirectFallbackPrompt,
        ] = {}
        agent_context_holder: Dict[str, Any] = {
            "response": None,
            "responses": [],
        }
        base_visible_evidence = dict(invocation_visible_evidence)
        primary_visible_evidence = dict(base_visible_evidence)
        visible_evidence_by_generation_prompt: Dict[
            str,
            Dict[str, tuple[Dict[str, Any], ...]],
        ] = {
            invocation_prompt: primary_visible_evidence,
        }

        def prepare_recovery_material(
            recovery_paths: Sequence[str],
        ) -> Stage1PromptMaterial:
            recovery_path_set = {
                normalize_repository_path(path)
                for path in recovery_paths
            }
            recovery_batch_items = [
                item
                for item in batch_items
                if normalize_repository_path(
                    getattr(item.get("file"), "path", "")
                ) in recovery_path_set
            ]
            return (
                material
                if len(recovery_batch_items) == len(batch_items)
                else _prepare_stage1_prompt_material(
                    request,
                    recovery_batch_items,
                    prepared_context,
                    is_incremental,
                )
            )

        async def prepare_direct_fallback_prompt(
            recovery_paths: Sequence[str],
        ) -> _Stage1DirectFallbackPrompt:
            cache_key = tuple(
                normalize_repository_path(path)
                for path in recovery_paths
            )
            cached = fallback_cache.get(cache_key)
            if cached is not None:
                return cached

            structural_context_parts = (
                [invocation_structural_context]
                if invocation_structural_context
                else []
            )
            fallback_visible_evidence = (
                dict(base_visible_evidence)
                if invocation_structural_context
                else {}
            )
            raw_responses = agent_context_holder.get("responses")
            responses = (
                list(raw_responses)
                if isinstance(raw_responses, list)
                else []
            )
            latest_response = agent_context_holder.get("response")
            if isinstance(latest_response, dict) and not responses:
                responses.append(latest_response)
            for response in responses:
                if not isinstance(response, dict):
                    continue
                separator_characters = 2 if structural_context_parts else 0
                remaining_characters = max(
                    0,
                    STAGE1_RELATION_BRIEFING_MAX_CHARS
                    - sum(len(part) for part in structural_context_parts)
                    - separator_characters * len(structural_context_parts),
                )
                observed_context, visible_response = (
                    _bounded_proposed_review_context(
                        response,
                        max_characters=remaining_characters,
                    )
                )
                if (
                    not observed_context
                    or observed_context in structural_context_parts
                ):
                    continue
                structural_context_parts.append(observed_context)
                _merge_structural_evidence(
                    fallback_visible_evidence,
                    structural_relation_evidence(visible_response),
                )
            structural_context = "\n\n".join(structural_context_parts)
            recovery_material = prepare_recovery_material(recovery_paths)
            def render_fallback() -> str:
                return _render_stage1_prompt(
                    recovery_material,
                    structural_context,
                    visible_evidence_by_id=fallback_visible_evidence,
                    use_mcp_tools=False,
                    structural_tools_available=False,
                    review_file_tool_available=False,
                )[0]

            fallback_prompt = render_fallback()
            if (
                _estimated_prompt_tokens(fallback_prompt) > token_budget
                and len(structural_context_parts) > 1
            ):
                structural_context_parts = (
                    [invocation_structural_context]
                    if invocation_structural_context
                    else []
                )
                structural_context = "\n\n".join(structural_context_parts)
                fallback_visible_evidence = (
                    dict(base_visible_evidence)
                    if invocation_structural_context
                    else {}
                )
                fallback_prompt = render_fallback()
            if (
                _estimated_prompt_tokens(fallback_prompt) > token_budget
                and structural_context
            ):
                structural_context = ""
                fallback_visible_evidence = {}
                fallback_prompt = render_fallback()
            visible_evidence_by_generation_prompt[fallback_prompt] = dict(
                fallback_visible_evidence
            )
            prepared_fallback = _Stage1DirectFallbackPrompt(
                prompt=fallback_prompt,
                structural_context_loaded=bool(structural_context),
            )
            fallback_cache[cache_key] = prepared_fallback
            logger.info(
                "Stage 1 degraded direct proposed-tree context: paths=%s chars=%d "
                "available=%s",
                list(recovery_paths),
                len(structural_context),
                bool(structural_context),
            )
            return prepared_fallback

        direct_invocation_prompt = (
            None
            if agentic_mode
            else _render_stage1_prompt(
                material,
                invocation_structural_context,
                visible_evidence_by_id=base_visible_evidence,
                use_mcp_tools=False,
                structural_tools_available=False,
                review_file_tool_available=False,
            )[0]
        )
        if direct_invocation_prompt is not None:
            visible_evidence_by_generation_prompt[
                direct_invocation_prompt
            ] = dict(base_visible_evidence)
        invocation_trace = {"generation_prompt": invocation_prompt}
        retry_llm = (
            fallback_llm
            if fallback_llm is not None and fallback_llm is not llm
            else llm
        )
        review_accumulator = _Stage1BatchReviewAccumulator.for_paths(
            batch_file_paths
        )

        primary_agent_error: Optional[Exception] = None
        try:
            issues = await _invoke_stage_1_batch_llm(
                llm,
                invocation_prompt,
                batch_file_paths,
                label=(
                    "agentic primary" if agentic_mode else "structured primary"
                ),
                agent_service=agent_service if agentic_mode else None,
                event_callback=event_callback,
                direct_fallback_prompt=direct_invocation_prompt,
                direct_fallback_prompt_factory=prepare_direct_fallback_prompt,
                agent_allowed_tool_names=agent_tool_names,
                agent_max_steps=STAGE1_AGENT_MAX_STEPS,
                agent_phase="primary",
                agent_complete_current_source_paths=tuple(sorted(
                    material.complete_current_file_paths
                )),
                agent_visible_evidence_by_id=primary_visible_evidence,
                rag_state=rag_state,
                invocation_trace=invocation_trace,
                agent_context_holder=agent_context_holder,
                agent_telemetry=agent_telemetry,
                review_accumulator=review_accumulator,
                **({
                    "required_agent_tool_names": (
                        required_structural_tool_sequence
                    ),
                } if required_structural_tool_sequence else {}),
                fail_closed_agent=agent_execution_required,
            )
        except Exception as error:
            if not agent_execution_required:
                raise
            primary_agent_error = error
            issues = None
        if issues is None:
            if agent_execution_required:
                recovery_paths = review_accumulator.missing_paths()
                if not recovery_paths:
                    raise RuntimeError(
                        "Required Stage 1 primary agent failed after complete "
                        "schema coverage; no missing-path recovery was eligible"
                    ) from primary_agent_error

                recovery_material = prepare_recovery_material(recovery_paths)
                recovery_visible_evidence = dict(base_visible_evidence)
                recovery_prompt, recovery_plugin_context = _render_stage1_prompt(
                    recovery_material,
                    invocation_structural_context,
                    visible_evidence_by_id=recovery_visible_evidence,
                    use_mcp_tools=True,
                    structural_tools_available=structural_agent_mode,
                    review_file_tool_available=review_file_tool_available,
                )
                visible_evidence_by_generation_prompt[
                    recovery_prompt
                ] = recovery_visible_evidence
                recovery_context_holder: Dict[str, Any] = {
                    "response": None,
                    "responses": [],
                }
                if agent_telemetry is not None:
                    agent_telemetry.begin_repository_agent_recovery()
                logger.warning(
                    "Required Stage 1 primary agent did not complete batch "
                    "coverage; retrying exactly once through the repository "
                    "agent for missing paths=%s (preserved=%d, prompt_chars=%d, "
                    "plugin_chars=%d): %s",
                    recovery_paths,
                    len(review_accumulator.reviews_by_path),
                    len(recovery_prompt),
                    len(recovery_plugin_context),
                    primary_agent_error or "empty primary result",
                )
                try:
                    issues = await _invoke_stage_1_batch_llm(
                        llm,
                        recovery_prompt,
                        recovery_paths,
                        label="agentic missing-path recovery",
                        agent_service=agent_service,
                        event_callback=event_callback,
                        agent_allowed_tool_names=agent_tool_names,
                        agent_max_steps=STAGE1_AGENT_MAX_STEPS,
                        agent_phase="missing_path_recovery",
                        agent_complete_current_source_paths=tuple(sorted(
                            recovery_material.complete_current_file_paths
                        )),
                        agent_visible_evidence_by_id=recovery_visible_evidence,
                        rag_state=rag_state,
                        invocation_trace=invocation_trace,
                        agent_context_holder=recovery_context_holder,
                        agent_telemetry=agent_telemetry,
                        review_accumulator=review_accumulator,
                        **({
                            "required_agent_tool_names": (
                                required_structural_tool_sequence
                            ),
                        } if required_structural_tool_sequence else {}),
                        fail_closed_agent=True,
                    )
                except Exception as recovery_error:
                    raise RuntimeError(
                        "Required Stage 1 MCP failed after one bounded "
                        "repository-agent recovery for missing paths: "
                        + ", ".join(recovery_paths)
                    ) from recovery_error
                if issues is None or review_accumulator.missing_paths():
                    raise RuntimeError(
                        "Required Stage 1 repository-agent recovery returned "
                        "incomplete coverage; direct output recovery is disabled"
                    )
            else:
                recovery_paths = review_accumulator.missing_paths()
                logger.info(
                    "Stage 1 structured response was unusable for %s evidence "
                    "shard %d/%d; retrying once as a reasoning-free direct "
                    "output request",
                    recovery_paths,
                    invocation_index,
                    len(invocations),
                )
                recovery_prompt = (
                    await prepare_direct_fallback_prompt(recovery_paths)
                ).prompt
                issues = await _invoke_stage_1_batch_llm(
                    retry_llm,
                    invocation_prompt,
                    recovery_paths,
                    label="direct-output recovery",
                    force_unstructured=True,
                    event_callback=event_callback,
                    direct_fallback_prompt=recovery_prompt,
                    invocation_trace=invocation_trace,
                    agent_telemetry=agent_telemetry,
                    review_accumulator=review_accumulator,
                )
        if issues is None:
            logger.debug(
                "Batch review parse failure for %s evidence shard %d/%d. "
                "The batch will fail so missing results cannot be published "
                "as a clean review.",
                batch_file_paths,
                invocation_index,
                len(invocations),
            )
            raise RuntimeError(
                "Stage 1 batch produced no valid result after all configured "
                "attempts: " + ", ".join(batch_file_paths)
            )
        if review_accumulator.reviews_by_path:
            merged_issues: List[CodeReviewIssue] = []
            for review_output, origin in review_accumulator.outputs_by_origin():
                origin_issues = _extract_calibrated_issues(review_output)
                _register_stage_1_candidates(
                    origin_issues,
                    batch_items,
                    candidate_ledger,
                    origin.generation_prompt,
                    visible_evidence_by_generation_prompt.get(
                        origin.generation_prompt,
                        {},
                    ),
                    source_phase=origin.source_phase,
                )
                merged_issues.extend(origin_issues)
            all_issues.extend(merged_issues)
        else:
            # Preserve compatibility with injected invocation doubles that
            # return issues directly rather than filling the accumulator.
            _register_stage_1_candidates(
                issues,
                batch_items,
                candidate_ledger,
                invocation_trace["generation_prompt"],
                visible_evidence_by_generation_prompt.get(
                    invocation_trace["generation_prompt"],
                    {},
                ),
                source_phase="agent" if agentic_mode else None,
            )
            all_issues.extend(issues)

    return all_issues


def _build_stage1_invocations(
    material: Stage1PromptMaterial,
    *,
    use_mcp_tools: bool,
    structural_tools_available: bool = True,
    review_file_tool_available: bool = False,
    preloaded_structural_context: str = "",
    preloaded_structural_evidence: Optional[
        Dict[str, tuple[Dict[str, Any], ...]]
    ] = None,
) -> List[
    tuple[
        str,
        str,
        str,
        Dict[str, tuple[Dict[str, Any], ...]],
    ]
]:
    """Build one prompt with the host-selected relation briefing, if any."""
    trusted_preloaded_context = bool(
        preloaded_structural_context.startswith(
            "RELATION-FIRST PROPOSED-TREE BRIEFING"
        )
        or not use_mcp_tools
    )
    structural_context = (
        preloaded_structural_context if trusted_preloaded_context else ""
    )
    visible_evidence = (
        dict(preloaded_structural_evidence or {})
        if trusted_preloaded_context
        else {}
    )
    prompt, plugin_context = _render_stage1_prompt(
        material,
        structural_context,
        visible_evidence_by_id=visible_evidence,
        use_mcp_tools=use_mcp_tools,
        structural_tools_available=structural_tools_available,
        review_file_tool_available=review_file_tool_available,
    )
    return [(
        prompt,
        structural_context,
        plugin_context,
        visible_evidence,
    )]


async def _invoke_stage_1_batch_llm(
    llm,
    prompt: str,
    batch_file_paths: List[str],
    label: str = "primary",
    force_unstructured: bool = False,
    agent_service: Optional["AgentExecutionService"] = None,
    event_callback: Optional[Callable[[Dict], None]] = None,
    direct_fallback_prompt: Optional[str] = None,
    direct_fallback_prompt_factory: Optional[
        Callable[[Sequence[str]], Awaitable[_Stage1DirectFallbackPrompt]]
    ] = None,
    agent_allowed_tool_names: Optional[frozenset[str]] = None,
    agent_max_steps: Optional[int] = None,
    agent_phase: str = "primary",
    agent_complete_current_source_paths: Sequence[str] = (),
    agent_visible_evidence_by_id: Optional[
        Dict[str, tuple[Dict[str, Any], ...]]
    ] = None,
    rag_state: Optional[Stage1RagState] = None,
    invocation_trace: Optional[Dict[str, str]] = None,
    agent_context_holder: Optional[
        Dict[str, Any]
    ] = None,
    agent_telemetry: Optional[Stage1AgentTelemetryRecorder] = None,
    review_accumulator: Optional[_Stage1BatchReviewAccumulator] = None,
    required_agent_tool_names: Sequence[str] = (),
    fail_closed_agent: bool = False,
) -> Optional[List[CodeReviewIssue]]:
    if invocation_trace is not None:
        invocation_trace["generation_prompt"] = prompt
    recovery_paths = list(batch_file_paths)

    def accept_review_output(
        data: FileReviewBatchOutput,
        requested_paths: Sequence[str],
        *,
        generation_prompt: str,
        source_phase: Optional[str],
    ) -> List[CodeReviewIssue]:
        if review_accumulator is None:
            _validate_batch_review_coverage(data, list(requested_paths))
            return _extract_calibrated_issues(data)

        review_accumulator.merge(
            data,
            requested_paths,
            generation_prompt=generation_prompt,
            source_phase=source_phase,
        )
        missing_paths = review_accumulator.missing_paths()
        if missing_paths:
            try:
                _validate_batch_review_coverage(data, list(requested_paths))
            except ValueError as coverage_error:
                raise coverage_error
            raise ValueError(
                "Stage 1 merged review coverage remains incomplete "
                f"(missing={','.join(missing_paths)})"
            )
        return _extract_calibrated_issues(review_accumulator.output())

    agent_degraded = False
    agent_degradation_emitted = False
    if agent_service is not None:
        from service.agent import AgentExecutionRequest

        resolved_agent_tools = (
            STAGE1_AGENT_TOOL_NAMES
            if agent_allowed_tool_names is None
            else agent_allowed_tool_names
        )
        structural_tools = STAGE1_STRUCTURAL_TOOL_NAMES.intersection(
            resolved_agent_tools
        )
        initial_required_tool_name = (
            required_agent_tool_names[0]
            if required_agent_tool_names
            else
            STAGE1_MINIMAL_REVIEW_CONTEXT_TOOL_NAME
            if (
                agent_phase == "primary"
                and STAGE1_MINIMAL_REVIEW_CONTEXT_TOOL_NAME
                in structural_tools
            )
            else (
                STAGE1_REVIEW_CONTEXT_TOOL_NAME
                if (
                    agent_phase == "primary"
                    and STAGE1_REVIEW_CONTEXT_TOOL_NAME in structural_tools
                )
                else None
            )
        )
        agent_started_at = (
            agent_telemetry.begin_agent(
                initial_required_tool_name,
                required_agent_tool_names,
            )
            if agent_telemetry is not None
            else time.monotonic()
        )
        events_recorded = False
        try:
            relation_briefing_loaded = (
                "RELATION-FIRST PROPOSED-TREE BRIEFING" in prompt
            )
            exploration_instruction = (
                "Complete the required proposed-tree workflow in order: compact "
                "review context, impact radius, one focused named relation query, "
                "and exact structural-unit inspection. Use each result to select "
                "the next call's precise target. Afterward, use exploreReviewContext "
                "or traverseCodeGraph only when a concrete unresolved multi-hop "
                "question remains. "
                if required_agent_tool_names
                else
                "Use the host-loaded relation-first briefing as the structural "
                "orientation baseline. If it leaves a concrete relationship "
                "question, deepen from a named unit, path, or frontier with the "
                "smallest focused proposed-tree tool. Do not repeat the broad "
                "orientation. Prefer a precise graph query or exact structural "
                "unit when its target is known, and use broad traversal only "
                "when a fixed relationship query cannot answer the question. "
                if structural_tools and relation_briefing_loaded
                else (
                    "The host has sealed one exact proposed-tree generation. "
                    "Begin with the required getMinimalReviewContext call, use "
                    "its bounded exact source directly, and follow only the "
                    "smallest graph continuation needed for this batch. "
                    if structural_tools
                    else "Use the supplied repository file tool when it resolves "
                    "a concrete context gap. "
                )
            )
            structural_source_instruction = (
                "Exact source returned by getStructuralUnit or in any focused "
                "graph operation's sourceWindows comes from the request-bound "
                "proposed tree; use it directly without rereading that range. "
                if structural_tools
                else ""
            )
            source_authority_instruction = structural_source_instruction + (
                "Use getReviewFileContent for code unrepresented by the graph, "
                "or a concrete required range that was omitted or truncated. "
                "Request the smallest useful line range by default. It selects "
                "proposed source for PR-modified "
                "paths (including paths in another batch) and pinned target-head "
                "source for unchanged paths. "
                if STAGE1_REVIEW_FILE_TOOL_NAME in resolved_agent_tools
                else "Treat getBranchFileContent as target-head-only source; "
                "the prompt's diff/current content remains authoritative for "
                "every PR-modified path. "
            )
            tool_argument_bindings: Dict[str, Dict[str, Any]] = {
                tool_name: {
                    "focusPaths": tuple(batch_file_paths),
                }
                for tool_name in sorted(structural_tools)
            }
            if STAGE1_REVIEW_FILE_TOOL_NAME in resolved_agent_tools:
                tool_argument_bindings[STAGE1_REVIEW_FILE_TOOL_NAME] = {
                    "contextSuppliedPaths": tuple(sorted({
                        normalize_repository_path(path)
                        for path in agent_complete_current_source_paths
                        if normalize_repository_path(path)
                    })),
                }
            execution = await agent_service.execute(AgentExecutionRequest(
                prompt=prompt,
                allowed_tool_names=resolved_agent_tools,
                max_steps=agent_max_steps or STAGE1_AGENT_MAX_STEPS,
                reasoning_effort=ReasoningEffort.MEDIUM,
                max_output_tokens=STAGE1_AGENT_MAX_OUTPUT_TOKENS,
                timeout_seconds=STAGE1_AGENT_TIMEOUT_SECONDS,
                # Bind the complete batch schema inside the agent graph so the
                # reserved final call returns validated JSON without mcp-use's
                # separate post-formatting model call.
                output_schema=FileReviewBatchOutput,
                additional_instructions=(
                    "Return the complete structured file-review response "
                    "required by the prompt, with one review object for every "
                    "requested file. "
                    + exploration_instruction
                    + source_authority_instruction
                ),
                metadata={
                    "stage": "stage_1",
                    "label": label,
                    "phase": agent_phase,
                    "batchPaths": tuple(batch_file_paths),
                },
                initial_required_tool_name=initial_required_tool_name,
                required_tool_names=tuple(required_agent_tool_names),
                tool_argument_bindings=tool_argument_bindings,
            ))
            tool_events = tuple(getattr(execution, "tool_events", ()) or ())
            tool_counts = _consume_stage1_agent_tool_events(
                tool_events,
                visible_evidence_by_id=agent_visible_evidence_by_id,
                rag_state=rag_state,
                context_holder=agent_context_holder,
            )
            if agent_telemetry is not None:
                tool_event_failures = agent_telemetry.record_agent_events(
                    tool_events,
                    started_at=agent_started_at,
                )
            else:
                tool_event_failures = stage1_agent_tool_event_failures(
                    tool_events,
                )
            events_recorded = True
            _validate_required_agent_tool_sequence(
                tool_events,
                required_agent_tool_names,
            )
            salvaged_review_count = _salvage_stage1_schema_tool_outputs(
                tool_events,
                review_accumulator,
                batch_file_paths,
                generation_prompt=prompt,
                source_phase="agent",
            )
            if tool_event_failures:
                failed_tool_names = sorted({
                    _tool_event_name(event)
                    for event in tool_events
                    if stage1_agent_tool_event_failures((event,))
                })
                emit_status(
                    event_callback,
                    "stage_1_agent_degraded",
                    "Repository-agent tool enrichment returned an error for "
                    "one file-review batch; the diff, current source, and "
                    "other available context remained available "
                    f"(tools: {', '.join(failed_tool_names)})",
                )
                agent_degradation_emitted = True
            logger.info(
                "Stage 1 agent phase completed: phase=%s paths=%s "
                "tool_calls=%d tools=%s",
                agent_phase,
                batch_file_paths,
                sum(tool_counts.values()),
                dict(sorted(tool_counts.items())),
            )
            if (
                review_accumulator is not None
                and not review_accumulator.missing_paths()
            ):
                data = review_accumulator.output()
                calibrated_issues = _extract_calibrated_issues(data)
                if agent_telemetry is not None:
                    agent_telemetry.record_issues(calibrated_issues)
                logger.info(
                    "Stage 1 agent phase result salvaged from schema tool "
                    "events: phase=%s paths=%s salvaged_review_objects=%d "
                    "issue_count=%d",
                    agent_phase,
                    batch_file_paths,
                    salvaged_review_count,
                    len(calibrated_issues),
                )
                return calibrated_issues
            output = execution.output
            if isinstance(output, FileReviewBatchOutput):
                data = output
            elif isinstance(output, dict):
                data = FileReviewBatchOutput.model_validate(output)
            else:
                content = extract_llm_response_text(output)
                if not content.strip():
                    raise ValueError("Stage 1 agent returned no review content")
                data = await parse_llm_response(
                    content,
                    FileReviewBatchOutput,
                    llm,
                    max_provider_repairs=0,
                )
            calibrated_issues = accept_review_output(
                data,
                batch_file_paths,
                generation_prompt=prompt,
                source_phase="agent",
            )
            if agent_telemetry is not None:
                agent_telemetry.record_issues(calibrated_issues)
            logger.info(
                "Stage 1 agent phase result: phase=%s paths=%s "
                "structured_review_objects=%d issue_count=%d",
                agent_phase,
                batch_file_paths,
                len(data.reviews),
                len(calibrated_issues),
            )
            return calibrated_issues
        except Exception as agent_error:
            if not events_recorded:
                partial_events = tuple(
                    getattr(agent_error, "tool_events", ()) or ()
                )
                _salvage_stage1_schema_tool_outputs(
                    partial_events,
                    review_accumulator,
                    batch_file_paths,
                    generation_prompt=prompt,
                    source_phase="agent",
                )
                _consume_stage1_agent_tool_events(
                    partial_events,
                    visible_evidence_by_id=agent_visible_evidence_by_id,
                    rag_state=rag_state,
                    context_holder=agent_context_holder,
                )
                if agent_telemetry is not None:
                    agent_telemetry.record_agent_events(
                        partial_events,
                        started_at=agent_started_at,
                        failed=True,
                        error=agent_error,
                    )
            elif agent_telemetry is not None:
                agent_telemetry.mark_agent_failure(agent_error)
            if fail_closed_agent:
                raise RuntimeError(
                    "Required Stage 1 repository-agent execution failed; "
                    "direct review fallback is disabled"
                ) from agent_error
            # Repository exploration is optional. Preserve complete file-review
            # coverage by retrying the same diff/current-source evidence through
            # the direct structured path when the agent or its tools fail.
            logger.warning(
                "Stage 1 agent did not produce a complete review for batch %s "
                "(%s); preserved=%d missing=%s; continuing through the direct "
                "recovery path: %s",
                batch_file_paths,
                label,
                len(review_accumulator.reviews_by_path)
                if review_accumulator is not None
                else 0,
                review_accumulator.missing_paths()
                if review_accumulator is not None
                else batch_file_paths,
                agent_error,
            )
            agent_degraded = True
            if review_accumulator is not None:
                recovery_paths = review_accumulator.missing_paths()

    if review_accumulator is not None and not recovery_paths:
        return _extract_calibrated_issues(review_accumulator.output())

    direct_prompt = direct_fallback_prompt
    structural_context_loaded = False
    if direct_prompt is None and direct_fallback_prompt_factory is not None:
        try:
            prepared_fallback = await direct_fallback_prompt_factory(
                recovery_paths
            )
            direct_prompt = prepared_fallback.prompt
            structural_context_loaded = (
                prepared_fallback.structural_context_loaded
            )
        except Exception as fallback_error:
            logger.info(
                "Optional Stage 1 direct fallback context could not be "
                "prepared for %s: %s",
                recovery_paths,
                fallback_error,
            )
    direct_prompt = direct_prompt or prompt
    if agent_degraded:
        if agent_telemetry is not None:
            agent_telemetry.record_fallback(
                structural_context_loaded=structural_context_loaded,
            )
        fallback_detail = (
            "diff, current source, and the already-retrieved exact "
            "proposed-tree context"
            if structural_context_loaded
            else (
                "diff and current source; no exact proposed-tree context was "
                "available"
            )
        )
        if not agent_degradation_emitted:
            emit_status(
                event_callback,
                "stage_1_agent_degraded",
                "Repository-agent analysis did not produce a complete result "
                "for one file-review batch; valid file results were preserved "
                "and analysis continued for only the missing files with its "
                + fallback_detail,
            )
    if invocation_trace is not None:
        invocation_trace["generation_prompt"] = direct_prompt

    if _supports_structured_output(llm) and not force_unstructured:
        try:
            invocation = await invoke_structured_output(
                llm,
                direct_prompt,
                FileReviewBatchOutput,
                effort=ReasoningEffort.LOW,
                label=f"stage-1-{label}",
            )
            result = await resolve_structured_output(
                invocation,
                FileReviewBatchOutput,
                llm,
            )
            if result:
                return accept_review_output(
                    result,
                    recovery_paths,
                    generation_prompt=direct_prompt,
                    source_phase=(
                        "direct_recovery" if agent_degraded else None
                    ),
                )
            logger.debug(
                "Structured output returned empty Stage 1 result for %s (%s)",
                recovery_paths,
                label,
            )
        except Exception as e:
            # The batch owner emits the single exhausted-attempt warning. Keep
            # attempt detail at DEBUG to avoid duplicating one failure for every
            # nested batch call; raw-response shape failures are already logged
            # by the structured-output adapter.
            logger.debug(
                "Structured output failed for Stage 1 batch %s (%s): "
                "error_type=%s",
                recovery_paths,
                label,
                type(e).__name__,
            )
        # A direct-output parse is the one configured recovery call, not an
        # implicit second primary call. The caller owns that recovery so total
        # provider attempts remain primary + one finite recovery attempt.
        return None
    else:
        logger.info(
            "Structured output skipped for Stage 1 batch %s (%s); using prompt JSON parsing",
            recovery_paths,
            label,
        )

    try:
        response = await llm.ainvoke(
            direct_prompt,
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
                "Stage 1 raw fallback returned no content for %s (%s): %s",
                recovery_paths,
                label,
                format_response_diagnostics(response),
            )
        data = await parse_llm_response(
            content,
            FileReviewBatchOutput,
            llm,
            max_provider_repairs=0,
        )
        return accept_review_output(
            data,
            recovery_paths,
            generation_prompt=direct_prompt,
            source_phase=(
                "direct_recovery"
                if agent_degraded or force_unstructured
                else None
            ),
        )
    except Exception as parse_err:
        logger.debug(
            "Stage 1 batch parse failed for %s (%s): %s",
            recovery_paths,
            label,
            parse_err,
        )
        return None


def _validate_batch_review_coverage(
    batch_output: FileReviewBatchOutput,
    batch_file_paths: List[str],
) -> None:
    """Validate that the agent returned exactly one result for every file."""
    expected = [normalize_repository_path(path) for path in batch_file_paths]
    observed = [
        normalize_repository_path(review.file)
        for review in batch_output.reviews
    ]
    expected_counts = Counter(expected)
    observed_counts = Counter(observed)
    missing = sorted(
        path
        for path in expected_counts
        if not observed_counts[path]
    )
    unexpected = sorted(
        path for path in observed_counts if path not in expected_counts
    )
    duplicates = sorted(
        path for path, count in observed_counts.items() if path and count > 1
    )
    empty_count = observed_counts.get("", 0)

    if (
        not expected
        or "" in expected_counts
        or len(observed) != len(expected)
        or missing
        or unexpected
        or duplicates
        or empty_count
    ):
        details = [f"expected={len(expected)}", f"received={len(observed)}"]
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        if duplicates:
            details.append("duplicates=" + ",".join(duplicates))
        if empty_count:
            details.append(f"empty_paths={empty_count}")
        raise ValueError(
            "Stage 1 batch review coverage mismatch (" + "; ".join(details) + ")"
        )


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


def _candidate_owner_item(
    issue: CodeReviewIssue,
    batch_items: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    issue_path = normalize_repository_path(getattr(issue, "file", "") or "")
    if not issue_path:
        return None
    exact = [
        item
        for item in batch_items
        if normalize_repository_path(getattr(item.get("file"), "path", ""))
        == issue_path
    ]
    if len(exact) == 1:
        return exact[0]
    matches = [
        item
        for item in batch_items
        if repository_paths_match(
            issue_path,
            getattr(item.get("file"), "path", ""),
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _register_stage_1_candidates(
    issues: List[CodeReviewIssue],
    batch_items: List[Dict[str, Any]],
    candidate_ledger: Optional[CandidateEvidenceLedger],
    generation_prompt: str,
    visible_evidence_by_id: Optional[
        Dict[str, tuple[Dict[str, Any], ...]]
    ] = None,
    source_phase: Optional[str] = None,
) -> None:
    if candidate_ledger is None:
        return
    batch_identity = ",".join(sorted(
        str(item.get("_review_unit_id") or "")
        for item in batch_items
    ))
    phase = str(source_phase or "").strip()
    for index, issue in enumerate(issues):
        owner = _candidate_owner_item(issue, batch_items)
        candidate_ledger.register(
            issue,
            stage="stage_1",
            source_key=(
                f"{batch_identity}:{phase}:{index}"
                if phase
                else f"{batch_identity}:{index}"
            ),
            review_unit_ids=(
                (str(owner.get("_review_unit_id")),)
                if owner is not None and owner.get("_review_unit_id")
                else ()
            ),
            prompt_hunk_ids=(
                tuple(owner.get("_hunk_ids", ()) or ())
                if owner is not None
                else ()
            ),
            generation_prompt=generation_prompt,
            visible_evidence_by_id=visible_evidence_by_id,
        )
