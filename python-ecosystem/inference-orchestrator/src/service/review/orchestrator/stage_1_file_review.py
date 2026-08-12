"""
Stage 1: Parallel file reviews — batching, RAG context, and per-batch LLM calls.
"""
import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.multi_stage import ReviewPlan, FileReviewBatchOutput
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import (
    DiffChangeType,
    DiffHunk,
    HunkDisposition,
    ProcessedDiff,
    DiffProcessor,
)
from utils.task_context_builder import build_task_context
from utils.dependency_graph import create_smart_batches_async

from utils.llm_response import extract_llm_response_text
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
from utils.path_identity import (
    normalize_repository_path,
    repository_paths_match,
)
from service.review.orchestrator.stage_helpers import (
    emit_progress,
    format_project_rules,
)
from service.review.plugin_context import (
    apply_plugin_file_policy,
    review_plugin_context,
)
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.prompt_diagnostics import record_prompt_diagnostic

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
# A per-file limit alone makes prompt cost grow by that full allowance for every
# file in a batch after smart batching has already applied its token estimate.
# Allocate one neutral batch-wide source budget fairly across files. The complete
# diff remains primary evidence, and verification retains full enriched source.
STAGE1_CURRENT_SOURCE_BATCH_CHAR_BUDGET = max(
    8_000,
    _env_int("REVIEW_STAGE1_CURRENT_SOURCE_BATCH_CHAR_BUDGET", 48_000),
)
# Parser metadata improves grouping and deterministic retrieval, but rendering
# every call/import/symbol into the LLM prompt is not an authoritative cost
# boundary. These neutral limits apply only to prompt serialization. The full
# parser payload remains available to batching and retrieval.
STAGE1_METADATA_CHAR_BUDGET = max(
    4_000,
    _env_int("REVIEW_STAGE1_METADATA_CHAR_BUDGET", 24_000),
)
STAGE1_METADATA_PER_FILE_CHAR_BUDGET = max(
    1_000,
    _env_int("REVIEW_STAGE1_METADATA_PER_FILE_CHAR_BUDGET", 6_000),
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
    context_disabled: bool = False
    context_disable_reason: str = ""
    semantic_disabled: bool = False
    semantic_failures: int = 0
    semantic_disable_reason: str = ""
    exact_evidence_by_id: Dict[str, tuple[Dict[str, Any], ...]] = field(
        default_factory=dict
    )
    deterministic_retrieval_states: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class _DiffReviewChunk:
    """One prompt-sized diff unit and the immutable hunks it contains."""

    content: str
    hunk_ids: tuple[str, ...] = ()


@dataclass
class Stage1ReviewUnitState:
    """Exact ownership and completion state for derived Stage 1 review units."""

    units_by_hunk: Dict[str, set[str]] = field(default_factory=dict)
    unit_owner: Dict[str, int] = field(default_factory=dict)
    completed_unit_ids: set[str] = field(default_factory=set)
    registered: bool = False

    def register_batches(self, batches: List[List[Dict[str, Any]]]) -> None:
        if self.registered:
            raise RuntimeError("Stage 1 review units were already registered")
        self.registered = True

        for batch_number, batch in enumerate(batches, start=1):
            for item in batch:
                unit_id = item.get("_review_unit_id")
                if not isinstance(unit_id, str) or not unit_id:
                    raise RuntimeError(
                        f"Stage 1 batch {batch_number} has a review unit without identity"
                    )
                previous_owner = self.unit_owner.get(unit_id)
                if previous_owner is not None:
                    raise RuntimeError(
                        "Stage 1 review unit was assigned more than once: "
                        f"{unit_id} belongs to batches {previous_owner} and "
                        f"{batch_number}"
                    )
                self.unit_owner[unit_id] = batch_number
                for hunk_id in item.get("_hunk_ids", ()) or ():
                    if not isinstance(hunk_id, str) or not hunk_id:
                        raise RuntimeError(
                            f"Stage 1 review unit {unit_id} has an invalid hunk identity"
                        )
                    self.units_by_hunk.setdefault(hunk_id, set()).add(unit_id)

    def unit_ids_for_batch(
        self,
        batch_number: int,
        batch: List[Dict[str, Any]],
    ) -> tuple[str, ...]:
        unit_ids = tuple(item["_review_unit_id"] for item in batch)
        if any(self.unit_owner.get(unit_id) != batch_number for unit_id in unit_ids):
            raise RuntimeError(
                f"Stage 1 batch {batch_number} does not own all of its review units"
            )
        return unit_ids

    def mark_completed(self, unit_ids: tuple[str, ...]) -> None:
        unknown = sorted(set(unit_ids) - set(self.unit_owner))
        if unknown:
            raise RuntimeError(
                "Stage 1 completed unknown review units: " + ", ".join(unknown)
            )
        repeated = sorted(set(unit_ids) & self.completed_unit_ids)
        if repeated:
            raise RuntimeError(
                "Stage 1 review units completed more than once: "
                + ", ".join(repeated)
            )
        self.completed_unit_ids.update(unit_ids)

    def assert_complete(self) -> None:
        missing = sorted(set(self.unit_owner) - self.completed_unit_ids)
        if missing:
            raise RuntimeError(
                "Stage 1 review-unit coverage is incomplete: " + ", ".join(missing)
            )

    @property
    def reviewed_hunk_ids(self) -> tuple[str, ...]:
        return tuple(sorted(
            hunk_id
            for hunk_id, unit_ids in self.units_by_hunk.items()
            if unit_ids and unit_ids.issubset(self.completed_unit_ids)
        ))


def _capture_deterministic_retrieval_state(
    deterministic_response: Optional[Dict[str, Any]],
    rag_state: Optional[Stage1RagState],
) -> None:
    """Retain the exact-retrieval state; prompt formatting captures visible facts."""
    if rag_state is None:
        return
    if _rag_response_error(deterministic_response):
        rag_state.deterministic_retrieval_states.append("failed")
        return
    rag_state.deterministic_retrieval_states.append(
        _deterministic_retrieval_state(deterministic_response)
    )


def _deterministic_retrieval_state(
    deterministic_response: Optional[Dict[str, Any]],
) -> str:
    context = _unwrap_rag_context(deterministic_response)
    metadata = context.get("_metadata") if isinstance(context, dict) else None
    if isinstance(metadata, dict) and metadata.get("retrieval_state"):
        return str(metadata["retrieval_state"]).strip().casefold()
    return "unknown"


def _rag_response_error(response: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the sanitized failure carried by a RAG client response, if any."""
    if not isinstance(response, dict):
        return None
    if str(response.get("status", "")).strip().casefold() != "error":
        return None
    detail = str(response.get("error") or "RAG request failed").strip()
    return detail or "RAG request failed"



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
        task_context=(
            build_task_context(request.taskContext, max_description_length=4000)
            or "No task context available."
        ),
    )


_DIFF_HUNK_HEADER = re.compile(
    r"^@@\s+-\d+(?:,\d+)?\s+"
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@"
)
_ADDED_FILE_HUNK_HEADER = re.compile(
    r"^@@\s+-0(?:,0)?\s+"
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@"
)
_COMPLETE_ADDED_SOURCE_MARKER = (
    "[Complete post-change source is present once as the added side of the diff "
    "below; the duplicate current-source copy was omitted.]"
)


def _diff_contains_complete_added_source(
    content: Optional[str],
    diff_content: str,
) -> bool:
    """Prove that an added-file diff contains the complete post-change source."""
    if content is None or not diff_content:
        return False

    added_lines: List[str] = []
    saw_hunk = False
    expected_hunk_lines: Optional[int] = None
    observed_hunk_lines = 0

    def finish_hunk() -> bool:
        return (
            expected_hunk_lines is None
            or observed_hunk_lines == expected_hunk_lines
        )

    for line in diff_content.splitlines():
        if line.startswith("@@"):
            if saw_hunk and not finish_hunk():
                return False
            match = _ADDED_FILE_HUNK_HEADER.match(line)
            if match is None:
                return False
            if int(match.group("new_start")) != len(added_lines) + 1:
                return False
            expected_hunk_lines = int(match.group("new_count") or "1")
            observed_hunk_lines = 0
            saw_hunk = True
            continue
        if not saw_hunk:
            continue
        if line.startswith("+"):
            added_lines.append(line[1:])
            observed_hunk_lines += 1
            continue
        if line == r"\ No newline at end of file":
            continue
        # Context or removed lines mean this is not a complete added-file image.
        if line.startswith((" ", "-")):
            return False

    return (
        saw_hunk
        and finish_hunk()
        and added_lines == content.splitlines()
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

    bounded_match = _lookup_by_path(prepared_context.diff_by_path, file_path)
    # Compaction is a prompt-size concern, never a review-scope decision. Once
    # the host has identified that a planned file contains compacted evidence,
    # restore its exact diff automatically and let the hunk-preserving splitter
    # create bounded review units. This must not depend on an LLM focus flag.
    restore_exact_diff = use_full_diff or (
        bounded_match is not None
        and _diff_limit_reason_allows_full_review(bounded_match.skip_reason)
    )
    if restore_exact_diff:
        _ensure_full_diff_index(prepared_context)
        matched_full_diff = _lookup_by_path(prepared_context.full_diff_by_path, file_path)
        if matched_full_diff is not None:
            return matched_full_diff

    if bounded_match is not None:
        return bounded_match

    # Accept an absolute checkout prefix, but never use a bare basename as file
    # identity; framework repositories contain many repeated configuration names.
    for diff_file in prepared_context.diff_source.files:
        if repository_paths_match(diff_file.path, file_path):
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

    This projection is deliberately schema-neutral so analysis-plugin
    fields do not require host-side dispatch. Omission markers distinguish a
    bounded prompt view from evidence that a metadata value is absent.
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

    # The per-entry cap normally makes this unnecessary, but enforce the total
    # boundary independently of JSON punctuation and unusual payload shapes.
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
        # At the absolute floor, retain one bounded identity per file. Default
        # production budgets never reach this path for the seven-file batch cap.
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

    # A single generic detail limit controls strings and sequences across
    # arbitrary plugin-defined fields. Binary search gives deterministic output
    # without knowing a concrete plugin schema.
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
            items.sort(key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ))
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
            bounded_text = text[:prefix_chars] + ("…" if prefix_chars < len(text) else "")
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
    return len(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ))


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

    def content_digest(value: Any) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    def add_chunk(chunk: Any, source_group: str, group_key: str = "") -> None:
        if not isinstance(chunk, dict):
            return
        text = chunk.get("text") or chunk.get("content") or ""
        metadata = chunk.get("metadata") or {}
        path = metadata.get("path") or chunk.get("path") or chunk.get("file_path") or ""
        content_key = (
            path,
            content_digest(text),
        )
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
        # Preserve the freshness authority from the exact retrieval payload.
        # Without this, PR-scoped architecture packets are mislabeled as branch
        # data and the stale-evidence guard removes them before prompt assembly.
        merged["_source"] = (
            "pr_indexed" if metadata.get("pr") is True else "deterministic"
        )
        merged["_match_type"] = source_group
        if group_key:
            merged["definition_name"] = group_key
        flattened.append(merged)

    grouped_sources = (
        ("architecture_relation", det_context.get("architecture_context", {})),
        ("architecture_related", det_context.get("architecture_related", {})),
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
        add_chunk(chunk, chunk.get("_match_type") or "deterministic")

    def stable_key(chunk: Dict[str, Any]) -> tuple:
        metadata = chunk.get("metadata") or {}
        fact_payloads = metadata.get("plugin_graph_facts")
        semantic_priority = 2
        if isinstance(fact_payloads, list):
            valid_facts = [
                fact for fact in fact_payloads
                if isinstance(fact, dict)
            ]
            if any(
                isinstance(fact.get("attributes"), dict)
                and fact["attributes"].get("semanticRole") == "diagnostic"
                for fact in valid_facts
            ):
                semantic_priority = 0
            elif any(
                fact.get("related_paths")
                or (
                    isinstance(fact.get("attributes"), dict)
                    and fact["attributes"]
                )
                for fact in valid_facts
            ):
                semantic_priority = 1
        return (
            semantic_priority,
            str(metadata.get("architecture_kind", "")),
            str(chunk.get("_matched_on", "")),
            str(metadata.get("path", chunk.get("path", ""))),
            str(metadata.get("architecture_key", "")),
            content_digest(chunk.get("text", "")),
        )

    def round_robin_architecture(
        chunks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        def matched_review_paths(chunk: Dict[str, Any]) -> tuple[str, ...]:
            raw = str(chunk.get("_matched_on") or "")
            normalized_paths = set()
            for path in raw.split(","):
                normalized = normalize_repository_path(path.strip())
                if normalized:
                    normalized_paths.add(normalized)
            return tuple(sorted(normalized_paths))

        ordered_chunks = sorted(chunks, key=stable_key)
        matched_paths_by_id = {
            id(chunk): matched_review_paths(chunk)
            for chunk in ordered_chunks
        }
        uncovered_paths = {
            path
            for chunk in ordered_chunks
            for path in matched_paths_by_id[id(chunk)]
        }
        coverage_first: List[Dict[str, Any]] = []
        coverage_ids = set()
        while uncovered_paths:
            candidates = [
                chunk for chunk in ordered_chunks
                if id(chunk) not in coverage_ids
                and uncovered_paths.intersection(
                    matched_paths_by_id[id(chunk)]
                )
            ]
            if not candidates:
                break
            selected = min(
                candidates,
                key=lambda chunk: (
                    stable_key(chunk)[0],
                    -len(
                        uncovered_paths.intersection(
                            matched_paths_by_id[id(chunk)]
                        )
                    ),
                    stable_key(chunk),
                ),
            )
            coverage_first.append(selected)
            coverage_ids.add(id(selected))
            uncovered_paths.difference_update(matched_paths_by_id[id(selected)])

        by_kind: Dict[str, List[Dict[str, Any]]] = {}
        for chunk in ordered_chunks:
            if id(chunk) in coverage_ids:
                continue
            metadata = chunk.get("metadata") or {}
            kind = str(metadata.get("architecture_kind") or "architecture")
            by_kind.setdefault(kind, []).append(chunk)
        ordered = list(coverage_first)
        while by_kind:
            # Every changed path with exact architecture evidence gets one
            # candidate before repeats. After that coverage pass, preserve kind
            # fairness and use the best remaining semantic fact in each kind.
            # This prevents both a relationship-heavy file and a large set of
            # coarse topology kinds from consuming the bounded prompt input.
            for kind in sorted(
                tuple(by_kind),
                key=lambda value: (
                    stable_key(by_kind[value][0])[0],
                    value,
                ),
            ):
                ordered.append(by_kind[kind].pop(0))
                if not by_kind[kind]:
                    del by_kind[kind]
        return ordered

    architecture_relations = round_robin_architecture([
        chunk for chunk in flattened
        if chunk.get("_match_type") == "architecture_relation"
    ])
    supporting_structural = sorted([
        chunk for chunk in flattened
        if chunk.get("_match_type") in {
            "architecture_related",
            "definition",
            "transitive_parent",
        }
    ], key=stable_key)
    direct = sorted([
        chunk for chunk in flattened
        if chunk.get("_match_type") in {"changed_file", "class_context"}
    ], key=stable_key)
    broader = sorted([
        chunk for chunk in flattened
        if chunk.get("_match_type") not in {
            "architecture_relation",
            "architecture_related",
            "definition",
            "transitive_parent",
            "changed_file",
            "class_context",
        }
    ], key=stable_key)

    relation_quota = max(1, (max_chunks * 3) // 4)
    support_quota = max(1, max_chunks // 5)
    direct_quota = max(0, max_chunks - relation_quota - support_quota)
    selected = (
        architecture_relations[:relation_quota]
        + supporting_structural[:support_quota]
        + direct[:direct_quota]
    )
    selected_ids = {id(chunk) for chunk in selected}
    fill = (
        architecture_relations[relation_quota:]
        + supporting_structural[support_quota:]
        + direct[direct_quota:]
        + broader
    )
    selected.extend(
        chunk for chunk in fill
        if id(chunk) not in selected_ids
    )
    return selected[:max_chunks]


def _deterministic_score(source_group: str) -> float:
    if source_group in {
        "architecture_relation",
        "architecture_related",
        "definition",
        "transitive_parent",
    }:
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

    from utils.llm_delegate import llm_class_names

    class_names = llm_class_names(llm)
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
    branches = []
    rag_branch = request.get_rag_branch()
    base_branch = request.get_rag_base_branch()
    if rag_branch:
        branches.append(rag_branch)
    if base_branch and base_branch not in branches:
        branches.append(base_branch)
    batching_rag_client = rag_client
    if not branches:
        logger.warning(
            "Stage 1 batching has no authoritative target branch; "
            "using local/enrichment grouping without a repository RAG lookup"
        )
        batching_rag_client = None
    exact_receipt_values = tuple(
        value
        for value in (
            getattr(request, "ragBaseGenerationManifestSha256", None),
            getattr(request, "ragPrGenerationFingerprint", None),
            getattr(
                request,
                "ragPrOverlayGenerationManifestSha256",
                None,
            ),
        )
        if isinstance(value, str) and value
    )
    if any(exact_receipt_values):
        logger.info(
            "Stage 1 smart-batching RAG discovery disabled while exact "
            "base/overlay generation receipts are active"
        )
        batching_rag_client = None

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
            workspace=request.projectWorkspace,
            project=request.projectNamespace,
            branches=branches,
            rag_client=batching_rag_client,
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


_UNIFIED_HUNK_HEADER = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_count>\d+))?\s+"
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@(?P<suffix>.*)$"
)


def _split_hunk_by_lines(hunk: str, max_chars: int) -> List[str]:
    if len(hunk) <= max_chars:
        return [hunk]

    lines = hunk.splitlines(keepends=True)
    if not lines:
        return [hunk]

    hunk_header = lines[0] if lines[0].startswith("@@ ") else ""
    body_lines = lines[1:] if hunk_header else lines
    header_text = hunk_header.rstrip("\r\n")
    header_newline = hunk_header[len(header_text):]
    match = _UNIFIED_HUNK_HEADER.match(header_text) if hunk_header else None
    if match is None:
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

    body_chunks: List[List[str]] = []
    current_lines: List[str] = []
    current_size = len(hunk_header)

    for line in body_lines:
        # Keep "\ No newline at end of file" attached to the line it describes.
        if (
            current_lines
            and not line.startswith("\\")
            and current_size + len(line) > max_chars
        ):
            body_chunks.append(current_lines)
            current_lines = [line]
            current_size = len(hunk_header) + len(line)
        else:
            current_lines.append(line)
            current_size += len(line)
    if current_lines:
        body_chunks.append(current_lines)

    old_cursor = int(match.group("old_start"))
    new_cursor = int(match.group("new_start"))
    suffix = match.group("suffix")
    chunks = []
    for fragment_lines in body_chunks:
        old_count = sum(
            1
            for line in fragment_lines
            if line.startswith((" ", "-"))
        )
        new_count = sum(
            1
            for line in fragment_lines
            if line.startswith((" ", "+"))
        )
        fragment_header = (
            f"@@ -{old_cursor},{old_count} +{new_cursor},{new_count} "
            f"@@{suffix}{header_newline}"
        )
        chunks.append(fragment_header + "".join(fragment_lines))
        old_cursor += old_count
        new_cursor += new_count

    return chunks or [hunk]


def _fallback_hunk_id(path: str, hunk: str) -> str:
    lines = hunk.splitlines()
    header = lines[0] if lines else ""
    content = "\n".join(lines)
    digest = hashlib.sha256(
        f"{path}\0{header}\0{content}".encode("utf-8")
    ).hexdigest()
    return "sha256:" + digest


def _chunk_diff_with_ownership(
    diff_content: str,
    max_tokens: int,
    *,
    known_hunks: tuple[DiffHunk, ...] = (),
    path: str = "",
) -> List[_DiffReviewChunk]:
    if not diff_content:
        return [_DiffReviewChunk(diff_content)]

    max_chars = max(1, max_tokens * 4)
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
        chunks: List[_DiffReviewChunk] = []
        current = ""
        for line in lines:
            if current and len(current) + len(line) > max_chars:
                chunks.append(_DiffReviewChunk(current))
                current = line
            else:
                current += line
        if current:
            chunks.append(_DiffReviewChunk(current))
        return chunks or [_DiffReviewChunk(diff_content)]

    if known_hunks and len(known_hunks) != len(hunks):
        raise RuntimeError(
            f"Diff hunk manifest mismatch for {path or '<unknown>'}: "
            f"parsed {len(hunks)}, manifest has {len(known_hunks)}"
        )

    hunk_ids: List[Optional[str]] = []
    for index, hunk in enumerate(hunks):
        if known_hunks:
            known = known_hunks[index]
            parsed_header = hunk.splitlines()[0] if hunk.splitlines() else ""
            if known.header != parsed_header:
                raise RuntimeError(
                    f"Diff hunk order mismatch for {path or '<unknown>'}: "
                    f"expected {known.header!r}, found {parsed_header!r}"
                )
            hunk_ids.append(
                known.id
                if known.disposition is HunkDisposition.REVIEWABLE
                else None
            )
        else:
            hunk_ids.append(_fallback_hunk_id(path, hunk))

    normalized_hunks: List[tuple[str, Optional[str]]] = []
    for hunk, hunk_id in zip(hunks, hunk_ids):
        normalized_hunks.extend(
            (fragment, hunk_id)
            for fragment in _split_hunk_by_lines(hunk, body_budget)
        )

    chunks: List[_DiffReviewChunk] = []
    current = ""
    current_hunk_ids: set[str] = set()
    for hunk, hunk_id in normalized_hunks:
        if current and len(header) + len(current) + len(hunk) > max_chars:
            chunks.append(_DiffReviewChunk(
                header + current,
                tuple(sorted(current_hunk_ids)),
            ))
            current = hunk
            current_hunk_ids = {hunk_id} if hunk_id else set()
        else:
            current += hunk
            if hunk_id:
                current_hunk_ids.add(hunk_id)

    if current:
        chunks.append(_DiffReviewChunk(
            header + current,
            tuple(sorted(current_hunk_ids)),
        ))

    return chunks or [_DiffReviewChunk(
        diff_content,
        tuple(sorted(hunk_id for hunk_id in hunk_ids if hunk_id)),
    )]


def _chunk_diff_preserving_hunks(diff_content: str, max_tokens: int) -> List[str]:
    return [
        chunk.content
        for chunk in _chunk_diff_with_ownership(diff_content, max_tokens)
    ]


def _review_unit_id(path: str, chunk: _DiffReviewChunk) -> str:
    digest = hashlib.sha256(
        (
            normalize_repository_path(path)
            + "\0"
            + "\0".join(chunk.hunk_ids)
            + "\0"
            + chunk.content
        ).encode("utf-8")
    ).hexdigest()
    return "sha256:" + digest


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
            chunks = _chunk_diff_with_ownership(
                diff_content,
                diff_chunk_token_budget,
                known_hunks=tuple(diff_file.hunks) if diff_file else (),
                path=file_path,
            )

            if len(chunks) <= 1:
                chunk = chunks[0]
                review_item = dict(item)
                review_item["_review_unit_id"] = _review_unit_id(file_path, chunk)
                review_item["_hunk_ids"] = chunk.hunk_ids
                current_batch.append(review_item)
                continue

            if current_batch:
                expanded_batches.append(current_batch)
                current_batch = []

            split_files += 1
            added_segments += len(chunks)
            for idx, chunk in enumerate(chunks, start=1):
                segment_item = dict(item)
                segment_item["_diff_override"] = chunk.content
                segment_item["_diff_chunk_index"] = idx
                segment_item["_diff_chunk_total"] = len(chunks)
                segment_item["_review_unit_id"] = _review_unit_id(
                    file_path,
                    chunk,
                )
                segment_item["_hunk_ids"] = chunk.hunk_ids
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

def _is_exact_revision_bound(
    request: ReviewRequestDto,
    pr_indexed: bool,
) -> bool:
    pr_number = getattr(request, "pullRequestId", None)
    return bool(
        pr_indexed
        and isinstance(pr_number, int)
        and pr_number > 0
        and all(
            isinstance(value, str) and bool(value.strip())
            for value in (
                getattr(request, "currentCommitHash", None)
                or getattr(request, "commitHash", None),
                getattr(request, "baseCommitHash", None),
                getattr(request, "ragCollectionTarget", None),
                getattr(request, "ragBaseGenerationManifestSha256", None),
                getattr(request, "ragPrGenerationFingerprint", None),
                getattr(
                    request,
                    "ragPrOverlayGenerationManifestSha256",
                    None,
                ),
            )
        )
    )


def _has_exact_base_binding(request: ReviewRequestDto) -> bool:
    return all(
        isinstance(value, str) and bool(value.strip())
        for value in (
            getattr(request, "baseCommitHash", None),
            getattr(request, "ragCollectionTarget", None),
            getattr(request, "ragBaseGenerationManifestSha256", None),
        )
    )


def _disable_rag_context(
    rag_state: Optional[Stage1RagState],
    reason: str,
) -> bool:
    """Open the optional-context circuit once and report whether it changed."""
    if rag_state is None:
        return True
    if rag_state.context_disabled:
        return False
    rag_state.context_disabled = True
    rag_state.context_disable_reason = reason
    rag_state.semantic_disabled = True
    rag_state.semantic_disable_reason = reason
    return True


def _disable_semantic_rag(
    rag_state: Optional[Stage1RagState],
    reason: str,
) -> bool:
    """Open the semantic-filler circuit once across concurrent batches."""
    if rag_state is None:
        return True
    if rag_state.semantic_disabled:
        return False
    rag_state.semantic_failures += 1
    rag_state.semantic_disabled = True
    rag_state.semantic_disable_reason = reason
    return True


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
    exact_revision_bound = _is_exact_revision_bound(request, pr_indexed)
    exact_base_bound = _has_exact_base_binding(request)
    exact_context_bound = exact_revision_bound or exact_base_bound
    if rag_state and rag_state.context_disabled:
        logger.debug(
            "Per-batch RAG context skipped after an earlier optional-context "
            "failure: %s",
            rag_state.context_disable_reason,
        )
        return None
    if not rag_client:
        if exact_context_bound and _disable_rag_context(
            rag_state,
            "revision-bound Stage 1 retrieval has no RAG client",
        ):
            logger.info(
                "Optional revision-bound RAG context is unavailable; "
                "continuing with local review evidence"
            )
        return None

    duplication_task: Optional[asyncio.Task] = None

    try:
        rag_branch = request.get_rag_branch()
        base_branch = request.get_rag_base_branch()
        if not rag_branch:
            message = "Missing authoritative target branch for Stage 1 RAG retrieval"
            _capture_deterministic_retrieval_state(
                {"status": "error", "error": message},
                rag_state,
            )
            if _disable_rag_context(rag_state, message):
                (logger.info if exact_context_bound else logger.warning)(
                    "%s; disabling optional RAG context for the remaining "
                    "Stage 1 batches",
                    message,
                )
            return None

        # Scale top_k based on batch priority to ensure adequate context
        priority_upper = (batch_priority or "MEDIUM").upper()
        top_k = {"HIGH": 15, "MEDIUM": 10, "LOW": 8}.get(priority_upper, 10)

        logger.info(f"Fetching per-batch RAG context for {len(batch_file_paths)} files "
                     f"(priority={priority_upper}, top_k={top_k})")

        pr_number = request.pullRequestId if exact_revision_bound else None
        all_pr_files = request.changedFiles if exact_revision_bound else None
        source_revision = (
            request.currentCommitHash or request.commitHash
            if exact_revision_bound
            else None
        )
        base_revision = (
            request.baseCommitHash if exact_context_bound else None
        )
        base_generation_receipt = (
            request.ragBaseGenerationManifestSha256
            if exact_context_bound
            else None
        )
        pr_generation_fingerprint = (
            request.ragPrGenerationFingerprint
            if exact_revision_bound
            else None
        )
        pr_overlay_generation_manifest_sha256 = (
            request.ragPrOverlayGenerationManifestSha256
            if exact_revision_bound
            else None
        )
        collection_target = (
            request.ragCollectionTarget if exact_context_bound else None
        )

        context = None

        async def _fetch_deterministic_context() -> Optional[Dict[str, Any]]:
            try:
                return await rag_client.get_deterministic_context(
                    workspace=request.projectWorkspace,
                    project=request.projectNamespace,
                    branches=(
                        [rag_branch]
                        if exact_context_bound
                        else list(dict.fromkeys(
                            branch
                            for branch in (rag_branch, base_branch)
                            if branch
                        ))
                    ),
                    file_paths=batch_file_paths,
                    limit_per_file=5,
                    pr_number=pr_number,
                    pr_changed_files=all_pr_files,
                    additional_identifiers=enrichment_identifiers,
                    source_revision=source_revision,
                    base_revision=base_revision,
                    base_generation_manifest_sha256=(
                        base_generation_receipt
                    ),
                    pr_generation_fingerprint=pr_generation_fingerprint,
                    pr_overlay_generation_manifest_sha256=(
                        pr_overlay_generation_manifest_sha256
                    ),
                    collection_target=collection_target,
                )
            except Exception as det_err:
                return {
                    "status": "error",
                    "error": f"{type(det_err).__name__}: {det_err}",
                }

        async def _fetch_semantic_context() -> Optional[Dict[str, Any]]:
            if not SEMANTIC_RAG_FILLER_ENABLED:
                logger.info("Semantic RAG filler skipped by REVIEW_SEMANTIC_RAG_FILLER_ENABLED")
                return None

            semantic_top_k = min(top_k, 8)
            logger.info(
                f"Semantic RAG filler: prefetching up to {semantic_top_k} chunks "
                f"(target={top_k})"
            )
            return await rag_client.get_pr_context(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                branch=rag_branch,
                changed_files=batch_file_paths,
                diff_snippets=batch_diff_snippets,
                pr_title=request.prTitle,
                pr_description=request.prDescription,
                top_k=semantic_top_k,
                base_branch=(base_branch if pr_number else None),
                pr_number=pr_number,
                all_pr_changed_files=all_pr_files,
                deleted_files=(request.deletedFiles or None) if pr_number else None,
                source_revision=source_revision,
                base_revision=base_revision,
                base_generation_manifest_sha256=base_generation_receipt,
                pr_generation_fingerprint=pr_generation_fingerprint,
                pr_overlay_generation_manifest_sha256=(
                    pr_overlay_generation_manifest_sha256
                ),
                collection_target=collection_target,
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
                        if any(
                            repository_paths_match(meta.path, batch_path)
                            for batch_path in batch_file_paths
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
                    repository_revision=base_revision,
                    repository_generation_manifest_sha256=(
                        base_generation_receipt
                    ),
                    collection_target=collection_target,
                )
            except Exception as dup_err:
                logger.debug("Duplication search skipped: %s", dup_err)
                return None

        deterministic_task = asyncio.create_task(_fetch_deterministic_context())
        duplication_task = asyncio.create_task(_fetch_duplication_context())

        # 1. Deterministic lookup FIRST — structural deps are highest-value context
        deterministic_response = await deterministic_task
        deterministic_error = _rag_response_error(deterministic_response)
        deterministic_chunks = _flatten_deterministic_context(deterministic_response)
        _capture_deterministic_retrieval_state(
            deterministic_response,
            rag_state,
        )
        deterministic_retrieval_state = _deterministic_retrieval_state(
            deterministic_response
        )
        context_error = (
            deterministic_error
            or (
                f"deterministic retrieval state is {deterministic_retrieval_state}"
                if exact_context_bound
                and deterministic_retrieval_state != "complete"
                else None
            )
        )
        if context_error:
            if duplication_task is not None and not duplication_task.done():
                duplication_task.cancel()
            if duplication_task is not None:
                await asyncio.gather(duplication_task, return_exceptions=True)
            if _disable_rag_context(rag_state, context_error):
                (logger.info if exact_context_bound else logger.warning)(
                    "Optional %sRAG context is unavailable; disabling it for "
                    "the remaining Stage 1 batches and continuing with local "
                    "review evidence: %s",
                    "revision-bound " if exact_context_bound else "",
                    context_error,
                )
            return None
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
        semantic_fill_enabled = (
            semantic_fill > 0 and SEMANTIC_RAG_FILLER_ENABLED
        )
        if semantic_fill > 0 and not SEMANTIC_RAG_FILLER_ENABLED:
            logger.info(
                "Semantic RAG filler skipped by "
                "REVIEW_SEMANTIC_RAG_FILLER_ENABLED"
            )
        elif semantic_fill_enabled and rag_state and rag_state.semantic_disabled:
            logger.info("Semantic RAG filler skipped: %s", rag_state.semantic_disable_reason)
        elif semantic_fill_enabled:
            try:
                rag_response = await asyncio.wait_for(
                    _fetch_semantic_context(),
                    timeout=SEMANTIC_RAG_TIMEOUT_SECONDS,
                )
                semantic_error = _rag_response_error(rag_response)
                if semantic_error:
                    raise RuntimeError(semantic_error)
            except asyncio.TimeoutError:
                rag_response = None
                reason = f"timed out after {SEMANTIC_RAG_TIMEOUT_SECONDS}s"
                if _disable_semantic_rag(rag_state, reason):
                    logger.warning(
                        "Semantic RAG filler timed out after %ss; disabling "
                        "for remaining Stage 1 batches",
                        SEMANTIC_RAG_TIMEOUT_SECONDS,
                    )
            except Exception as sem_err:
                rag_response = None
                if _disable_semantic_rag(rag_state, str(sem_err)):
                    logger.warning(
                        "Semantic RAG filler failed; disabling for remaining "
                        "Stage 1 batches: %s",
                        sem_err,
                    )

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
                    logger.info(f"Per-batch reranking skipped (non-critical): {rerank_err}")

            return context

        return None

    except Exception as e:
        if duplication_task is not None and not duplication_task.done():
            duplication_task.cancel()
        if duplication_task is not None:
            await asyncio.gather(duplication_task, return_exceptions=True)
        if _disable_rag_context(rag_state, str(e)):
            logger.warning(
                "Failed to fetch optional per-batch RAG context; disabling it "
                "for the remaining Stage 1 batches: %s",
                e,
            )
        return None


def _deduplicate_pr_stale_chunks(
    chunks: List[Dict[str, Any]],
    pr_changed_files: List[str],
    batch_file_paths: List[str],
) -> List[Dict[str, Any]]:
    if not chunks or not pr_changed_files:
        return chunks

    pr_changed_set = {
        normalize_repository_path(path)
        for path in pr_changed_files
        if normalize_repository_path(path)
    }
    batch_set = {
        normalize_repository_path(path)
        for path in batch_file_paths
        if normalize_repository_path(path)
    }

    by_path: Dict[str, List[Dict[str, Any]]] = {}
    for chunk in chunks:
        metadata = chunk.get("metadata", {})
        path = normalize_repository_path(
            metadata.get("path")
            or chunk.get("path")
            or chunk.get("file_path", "")
        )
        if not path:
            path = "__unknown__"
        by_path.setdefault(path, []).append(chunk)

    result = []
    for path, path_chunks in by_path.items():
        is_pr_file = any(
            repository_paths_match(path, changed_path)
            for changed_path in pr_changed_set
        )
        is_batch_file = any(
            repository_paths_match(path, batch_path)
            for batch_path in batch_set
        )

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
    rag_state: Optional[Stage1RagState] = None,
    review_unit_state: Optional[Stage1ReviewUnitState] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> List[CodeReviewIssue]:
    prepared_context = _build_stage_1_prepared_context(request, processed_diff, is_incremental)
    rag_state = rag_state or Stage1RagState()
    review_unit_state = review_unit_state or Stage1ReviewUnitState()
    batches = await create_smart_batches_wrapper(
        file_groups=plan.file_groups,
        processed_diff=prepared_context.diff_source,
        request=request,
        rag_client=rag_client,
        max_files_per_batch=STAGE1_MAX_FILES_PER_BATCH,
    )
    batches = _expand_oversized_diff_batches(batches, prepared_context)
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
                is_incremental, rag_context, pr_indexed,
                llm_reranker=llm_reranker,
                use_llm_rerank=use_llm_rerank,
                fallback_llm=fallback_llm,
                rag_state=rag_state,
                candidate_ledger=candidate_ledger,
            )
            return batch_idx, result, unit_ids

    tasks = [
        asyncio.create_task(_run_batch(batch_idx, batch))
        for batch_idx, batch in enumerate(batches, start=1)
    ]

    for completed_task in asyncio.as_completed(tasks):
        try:
            batch_num, res, unit_ids = await completed_task
            review_unit_state.mark_completed(unit_ids)
            batch_results[batch_num] = res or []
            if res:
                logger.info(f"Batch {batch_num} completed: {len(res)} issues found")
            else:
                logger.info(f"Batch {batch_num} completed: no issues found")
        except Exception as exc:
            logger.debug("Stage 1 batch failed; cancelling sibling batches: %s", exc)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise RuntimeError(
                "Stage 1 review is incomplete because at least one batch failed"
            ) from exc
        finally:
            completed_batches += 1
            progress = 10 + int((completed_batches / len(batches)) * 50)
            emit_progress(
                event_callback,
                progress,
                f"Stage 1: Reviewed {completed_batches}/{len(batches)} batches",
            )

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
    fallback_rag_context: Optional[Any],
    pr_indexed: bool,
    llm_reranker=None,
    use_llm_rerank: bool = True,
    fallback_llm=None,
    rag_state: Optional[Stage1RagState] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
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
            candidate_ledger=candidate_ledger,
        )
        elapsed = time.time() - start_time
        logger.info(f"[Batch {batch_idx}] FINISHED in {elapsed:.2f}s - {len(result)} issues")
        return result
    except Exception as e:
        elapsed = time.time() - start_time
        logger.debug(f"[Batch {batch_idx}] FAILED after {elapsed:.2f}s: {e}")
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
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> List[CodeReviewIssue]:
    batch_files_data = []
    batch_file_paths = []
    batch_diff_snippets = []
    batch_raw_diffs = []
    complete_current_file_paths = set()

    if prepared_context is not None and not isinstance(prepared_context, Stage1PreparedContext):
        # Backwards compatibility for older direct callers/tests that pass
        # ProcessedDiff as the fifth positional argument.
        prepared_context = _build_stage_1_prepared_context(request, prepared_context, is_incremental)
    elif prepared_context is None:
        prepared_context = _build_stage_1_prepared_context(request, None, is_incremental)

    available_current_source_count = sum(
        bool(_lookup_by_path(
            prepared_context.file_content_by_path,
            item["file"].path,
        ))
        for item in batch_items
    )
    current_source_per_file_budget = min(
        STAGE1_MAX_CURRENT_FILE_CHARS,
        max(
            1_000,
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
        file_diff = item.get("_diff_override") or ""
        if not file_diff and diff_file:
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

        change_type = (
            diff_file.change_type
            if diff_file is not None
            else DiffChangeType.MODIFIED
        )
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
    batch_visible_evidence_by_id: Dict[
        str, tuple[Dict[str, Any], ...]
    ] = {}

    exact_context_bound = (
        _is_exact_revision_bound(request, pr_indexed)
        or _has_exact_base_binding(request)
    )
    if rag_client or exact_context_bound:
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
            current_file_complete_paths=complete_current_file_paths,
            visible_evidence_by_id=batch_visible_evidence_by_id,
        )
    else:
        fallback_context_allowed = not (
            exact_context_bound
            or (rag_state is not None and rag_state.context_disabled)
        )
        if fallback_context_allowed:
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
        else:
            # Global fallback is alias/branch scoped and cannot satisfy an
            # immutable generation receipt. It must not be awaited after an
            # exact-context failure, because doing so could inject stale code.
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
                current_file_complete_paths=complete_current_file_paths,
                visible_evidence_by_id=batch_visible_evidence_by_id,
            )

    if rag_state and batch_visible_evidence_by_id:
        for evidence_id in sorted(batch_visible_evidence_by_id):
            combined = list(rag_state.exact_evidence_by_id.get(evidence_id, ()))
            for fact in batch_visible_evidence_by_id[evidence_id]:
                if fact not in combined:
                    combined.append(fact)
            rag_state.exact_evidence_by_id[evidence_id] = tuple(sorted(
                combined,
                key=lambda fact: json.dumps(
                    fact,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
            ))

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

    plugin_context_text = review_plugin_context(
        request,
        batch_file_paths,
        visible_evidence_by_id=batch_visible_evidence_by_id,
    )
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
        plugin_context=plugin_context_text,
    )
    logger.info(
        "Stage 1 prompt assembled: total=%d chars, metadata=%d, rag=%d, "
        "plugin=%d, files=%d, current_source_per_file_budget=%d",
        len(prompt),
        len(file_metadata_text),
        len(rag_context_text),
        len(plugin_context_text),
        len(batch_file_paths),
        current_source_per_file_budget,
    )
    record_prompt_diagnostic({
        "stage": "stage_1",
        "batchPaths": sorted(batch_file_paths),
        "fileCount": len(batch_file_paths),
        "totalPromptChars": len(prompt),
        "currentSourceChars": sum(
            len(str(item.get("current_code") or ""))
            for item in batch_files_data
        ),
        "diffChars": sum(
            len(str(item.get("diff") or ""))
            for item in batch_files_data
        ),
        "metadataChars": len(file_metadata_text),
        "ragChars": len(rag_context_text),
        "pluginChars": len(plugin_context_text),
        "projectRulesChars": len(project_rules),
        "taskContextChars": len(prepared_context.task_context),
        "previousIssuesChars": len(previous_issues_for_batch),
        "currentSourcePerFileBudget": current_source_per_file_budget,
    })

    issues = await _invoke_stage_1_batch_llm(llm, prompt, batch_file_paths, label="capped")
    if issues is not None:
        _register_stage_1_candidates(
            issues,
            batch_items,
            candidate_ledger,
            prompt,
            batch_visible_evidence_by_id,
        )
        return issues

    if fallback_llm is not None and fallback_llm is not llm:
        logger.info(
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
            _register_stage_1_candidates(
                issues,
                batch_items,
                candidate_ledger,
                prompt,
                batch_visible_evidence_by_id,
            )
            return issues

    logger.debug(
        "Batch review parse failure for %s after capped%s attempts. "
        "The batch will fail so missing results cannot be published as a clean review.",
        batch_file_paths,
        " and uncapped" if fallback_llm is not None and fallback_llm is not llm else "",
    )
    raise RuntimeError(
        "Stage 1 batch produced no valid result after all configured attempts: "
        + ", ".join(batch_file_paths)
    )


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

    normalized_chunk_path = normalize_repository_path(chunk_path)

    for file_path in batch_file_paths:
        normalized_file_path = normalize_repository_path(file_path)
        if not normalized_file_path:
            continue
        if repository_paths_match(normalized_chunk_path, normalized_file_path):
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
    if _supports_structured_output(llm):
        try:
            structured_llm = llm.with_structured_output(FileReviewBatchOutput)
            result = await structured_llm.ainvoke(prompt)
            if result:
                return _extract_calibrated_issues(result)
            logger.debug("Structured output returned empty Stage 1 result for %s (%s)", batch_file_paths, label)
        except Exception as e:
            logger.debug("Structured output failed for Stage 1 batch %s (%s): %s", batch_file_paths, label, e)
    else:
        logger.info(
            "Structured output skipped for Stage 1 batch %s (%s); using prompt JSON parsing",
            batch_file_paths,
            label,
        )

    try:
        response = await llm.ainvoke(prompt)
        content = extract_llm_response_text(response)
        data = await parse_llm_response(content, FileReviewBatchOutput, llm)
        return _extract_calibrated_issues(data)
    except Exception as parse_err:
        logger.debug("Stage 1 batch parse failed for %s (%s): %s", batch_file_paths, label, parse_err)
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
) -> None:
    if candidate_ledger is None:
        return
    batch_identity = ",".join(sorted(
        str(item.get("_review_unit_id") or "")
        for item in batch_items
    ))
    for index, issue in enumerate(issues):
        owner = _candidate_owner_item(issue, batch_items)
        candidate_ledger.register(
            issue,
            stage="stage_1",
            source_key=f"{batch_identity}:{index}",
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
