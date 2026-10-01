"""Deterministic local-evidence packing for Stage 1 review.

The module owns diff/source atomization, review-unit identity and ownership,
bounded joint-unit construction, and review-wide quota admission. Host-specific
prompt rendering and plugin-context resolution enter through one typed runtime.
"""
from collections import Counter
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from model.dtos import ReviewRequestDto
from utils.diff_processor import (
    DiffChangeType,
    DiffHunk,
    HunkDisposition,
    ProcessedDiff,
)
from utils.path_identity import (
    normalize_repository_path,
    repository_paths_match,
)


logger = logging.getLogger(__name__)

FULL_DIFF_REVIEW_FOCUS = "FULL_DIFF_REVIEW"
DEFAULT_STAGE1_DIFF_CHUNK_TOKEN_BUDGET = 60_000

__all__ = [
    "Stage1LocalPackingInput",
    "Stage1LocalPackingRuntime",
    "Stage1PreparedContext",
    "Stage1PromptMaterial",
    "Stage1ReviewUnitState",
    "pack_stage1_local_batches",
]


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


Stage1Batch = List[Dict[str, Any]]


@dataclass
class Stage1PromptMaterial:
    """Normalized local material used for packing and prompt invocation."""

    request: ReviewRequestDto
    batch_items: Stage1Batch
    batch_files_data: Stage1Batch
    batch_file_paths: List[str]
    complete_current_file_paths: set[str]
    current_source_per_file_budget: int
    batch_metadata: List[Any]
    enrichment_identifiers: Optional[List[str]]
    project_rules: str
    previous_issues_for_batch: str
    file_metadata_text: str
    task_context: str
    plugin_context_override: Optional[str]
    prepared_context: Stage1PreparedContext
    is_incremental: bool
    boundary_context: str


@dataclass(frozen=True)
class Stage1LocalPackingRuntime:
    """Host callbacks required to size prompts and resolve plugin context."""

    prepare_material: Callable[
        [ReviewRequestDto, Stage1Batch, Stage1PreparedContext, bool],
        Stage1PromptMaterial,
    ]
    material_prompt_tokens: Callable[[Stage1PromptMaterial], int]
    complete_plugin_context: Callable[[ReviewRequestDto, str], str]


@dataclass(frozen=True)
class Stage1LocalPackingInput:
    """Review-scoped inputs shared by deterministic local packing calls."""

    request: ReviewRequestDto
    prepared_context: Stage1PreparedContext
    is_incremental: bool
    token_budget: int


def _batch_prompt_tokens(
    runtime: Stage1LocalPackingRuntime,
    request: ReviewRequestDto,
    batch: Stage1Batch,
    prepared_context: Stage1PreparedContext,
    is_incremental: bool,
) -> int:
    material = runtime.prepare_material(
        request,
        batch,
        prepared_context,
        is_incremental,
    )
    return runtime.material_prompt_tokens(material)


@dataclass(frozen=True)
class _DiffReviewChunk:
    """One prompt-sized diff unit and the immutable hunks it contains."""

    content: str
    hunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Stage1EvidenceAtom:
    """One lossless semantic slice assigned to exactly one local review unit."""

    field: str
    text: str
    index: int
    total: int
    hunk_ids: tuple[str, ...] = ()


@dataclass
class Stage1ReviewUnitState:
    """Exact ownership and completion state for derived Stage 1 review units."""

    units_by_hunk: Dict[str, set[str]] = field(default_factory=dict)
    unit_owner: Dict[str, int] = field(default_factory=dict)
    completed_unit_ids: set[str] = field(default_factory=set)
    omitted_hunk_ids: set[str] = field(default_factory=set)
    omitted_unit_count: int = 0
    omitted_context_chars: int = 0
    omitted_paths: set[str] = field(default_factory=set)
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
                omitted_hunks = item.get("_omitted_hunk_ids", ()) or ()
                self.omitted_hunk_ids.update(
                    hunk_id
                    for hunk_id in omitted_hunks
                    if isinstance(hunk_id, str) and hunk_id
                )
                self.omitted_unit_count += max(
                    0,
                    int(item.get("_omitted_stage1_unit_count", 0) or 0),
                )
                self.omitted_context_chars += max(
                    0,
                    int(item.get("_omitted_context_chars", 0) or 0),
                )
                self.omitted_paths.update(
                    normalize_repository_path(path)
                    for path in (item.get("_omitted_stage1_paths", ()) or ())
                    if normalize_repository_path(path)
                )

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


def _is_compacted_stage_1_diff(diff_file: Optional[Any]) -> bool:
    if diff_file is None or not _diff_limit_reason_allows_full_review(
        getattr(diff_file, "skip_reason", None)
    ):
        return False
    content = str(getattr(diff_file, "content", "") or "")
    return "[CodeCrow Summary:" in content


def _reviewable_manifest_hunk_ids(diff_file: Optional[Any]) -> tuple[str, ...]:
    if diff_file is None:
        return ()
    return tuple(sorted({
        hunk.id
        for hunk in (getattr(diff_file, "hunks", ()) or ())
        if getattr(hunk, "disposition", None) is HunkDisposition.REVIEWABLE
        and isinstance(getattr(hunk, "id", None), str)
        and hunk.id
    }))


def _compacted_stage_1_hunk_ids(diff_file: Optional[Any]) -> tuple[str, ...]:
    if not _is_compacted_stage_1_diff(diff_file):
        return ()
    return _reviewable_manifest_hunk_ids(diff_file)


def _reviewable_manifest_context_chars(diff_file: Optional[Any]) -> int:
    return sum(
        len(str(getattr(hunk, "content", "") or ""))
        for hunk in (getattr(diff_file, "hunks", ()) or ())
        if getattr(hunk, "disposition", None) is HunkDisposition.REVIEWABLE
    )


def _apply_compacted_stage_1_omission(
    item: Dict[str, Any],
    diff_file: Optional[Any],
    path: str,
) -> Dict[str, Any]:
    compacted_hunk_ids = _compacted_stage_1_hunk_ids(diff_file)
    if not compacted_hunk_ids:
        return item
    bounded = dict(item)
    bounded["_hunk_ids"] = ()
    bounded["_omitted_hunk_ids"] = tuple(sorted({
        *(bounded.get("_omitted_hunk_ids", ()) or ()),
        *compacted_hunk_ids,
    }))
    diagnostic = (
        "[CodeCrow bounded diff compaction: "
        f"path={normalize_repository_path(path)}, "
        f"omitted_hunks={len(compacted_hunk_ids)}, "
        f"reason={getattr(diff_file, 'skip_reason', None) or 'diff limit'}. "
        "Original hunk contents were not materialized; absence is not negative "
        "evidence.]"
    )
    existing = str(bounded.get("_stage1_budget_diagnostic") or "").strip()
    if diagnostic not in existing:
        bounded["_omitted_stage1_unit_count"] = (
            int(bounded.get("_omitted_stage1_unit_count", 0) or 0) + 1
        )
        bounded["_omitted_context_chars"] = (
            int(bounded.get("_omitted_context_chars", 0) or 0)
            + _reviewable_manifest_context_chars(diff_file)
        )
        normalized_path = normalize_repository_path(path)
        bounded["_omitted_stage1_paths"] = tuple(sorted({
            *(bounded.get("_omitted_stage1_paths", ()) or ()),
            *((normalized_path,) if normalized_path else ()),
        }))
        bounded["_stage1_budget_diagnostic"] = "\n".join(filter(None, (
            existing,
            diagnostic,
        )))
    return bounded


def _find_diff_file_for_path(
    prepared_context: Optional[Stage1PreparedContext],
    file_path: str,
    use_full_diff: bool = False,
) -> Optional[Any]:
    if not prepared_context or not prepared_context.diff_source:
        return None

    bounded_match = _lookup_by_path(prepared_context.diff_by_path, file_path)
    # The bounded ProcessedDiff is the review admission boundary. Never reload
    # the raw diff here: compacted manifest hunks are accounted as omitted.
    del use_full_diff

    if bounded_match is not None:
        return bounded_match

    # Accept an absolute checkout prefix, but never use a bare basename as file
    # identity; framework repositories contain many repeated configuration names.
    for diff_file in prepared_context.diff_source.files:
        if repository_paths_match(diff_file.path, file_path):
            return diff_file
    return None


def _item_requests_full_diff(item: Dict[str, Any]) -> bool:
    file_info = item.get("file")
    focus_areas = getattr(file_info, "focus_areas", None) or []
    for focus_area in focus_areas:
        normalized = str(focus_area or "").strip().upper().replace("-", "_").replace(" ", "_")
        if normalized == FULL_DIFF_REVIEW_FOCUS:
            return True
    return False


def _text_character_budget(text: str, token_budget: int) -> int:
    """Translate an input-token budget to chars using this text's UTF-8 width."""
    if not text:
        return 1
    byte_count = len(text.encode("utf-8"))
    if byte_count <= 0:
        return max(1, len(text))
    return max(1, (max(1, token_budget) * 4 * len(text)) // byte_count)


# ── Batching ──────────────────────────────────────────────────


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
    max_input_tokens: int,
    *,
    known_hunks: tuple[DiffHunk, ...] = (),
    path: str = "",
) -> List[_DiffReviewChunk]:
    if not diff_content:
        return [_DiffReviewChunk(diff_content)]

    max_chars = _text_character_budget(diff_content, max_input_tokens)
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


def _chunk_diff_preserving_hunks(
    diff_content: str,
    max_input_tokens: int,
) -> List[str]:
    return [
        chunk.content
        for chunk in _chunk_diff_with_ownership(
            diff_content,
            max_input_tokens,
        )
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
    diff_chunk_token_budget: int = DEFAULT_STAGE1_DIFF_CHUNK_TOKEN_BUDGET,
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
            compacted_hunk_ids = _compacted_stage_1_hunk_ids(diff_file)
            chunks = _chunk_diff_with_ownership(
                diff_content,
                diff_chunk_token_budget,
                known_hunks=(
                    ()
                    if compacted_hunk_ids
                    else tuple(diff_file.hunks) if diff_file else ()
                ),
                path=file_path,
            )

            if len(chunks) <= 1:
                chunk = chunks[0]
                review_item = dict(item)
                review_item["_review_unit_id"] = _review_unit_id(file_path, chunk)
                review_item["_hunk_ids"] = chunk.hunk_ids
                review_item = _apply_compacted_stage_1_omission(
                    review_item,
                    diff_file,
                    file_path,
                )
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
                segment_item = _apply_compacted_stage_1_omission(
                    segment_item,
                    diff_file,
                    file_path,
                )
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


def _split_source_at_semantic_line_boundaries(
    source: str,
    max_chars: int,
) -> List[str]:
    """Losslessly partition source at line/blank-block boundaries.

    A physical line is atomic. A pathological single line can therefore exceed
    the target, but it is never character-sliced or silently omitted.
    """
    if not source or len(source) <= max_chars:
        return [source]
    lines = source.splitlines(keepends=True)
    if not lines:
        return [source]

    chunks: List[str] = []
    current: List[str] = []
    current_chars = 0
    last_blank_index = 0

    def flush(count: Optional[int] = None) -> None:
        nonlocal current, current_chars, last_blank_index
        count = len(current) if count is None else count
        if count <= 0:
            return
        chunks.append("".join(current[:count]))
        current = current[count:]
        current_chars = sum(len(line) for line in current)
        last_blank_index = 0
        for index, line in enumerate(current, start=1):
            if not line.strip():
                last_blank_index = index

    for line in lines:
        if current and current_chars + len(line) > max_chars:
            flush(last_blank_index or len(current))
            while current and current_chars + len(line) > max_chars:
                flush(len(current))
        current.append(line)
        current_chars += len(line)
        if not line.strip():
            last_blank_index = len(current)

    flush()
    return chunks or [source]


_STAGE1_SHARED_OVERRIDE_FIELDS = (
    ("boundary", "_boundary_context_override", "boundary_context"),
    ("project rules", "_project_rules_override", "project_rules"),
    ("plugin context", "_plugin_context_override", "plugin_context"),
    ("task context", "_task_context_override", "task_context"),
    ("previous issues", "_previous_issues_override", "previous_issues"),
    ("parser metadata", "_metadata_override", "metadata"),
)


def _exact_stage1_diff(
    item: Dict[str, Any],
    prepared_context: Stage1PreparedContext,
) -> tuple[str, tuple[DiffHunk, ...], str]:
    file_info = item.get("file")
    path = getattr(file_info, "path", "")
    diff_file = _find_diff_file_for_path(
        prepared_context,
        path,
        use_full_diff=_item_requests_full_diff(item),
    )
    if "_diff_override" in item:
        return (
            str(item.get("_diff_override") or ""),
            (),
            path,
        )
    known_hunks = (
        ()
        if _is_compacted_stage_1_diff(diff_file)
        else tuple(getattr(diff_file, "hunks", ()) or ())
    )
    return (
        str(getattr(diff_file, "content", "") or ""),
        known_hunks,
        path,
    )


def _stage1_item_with_overrides(
    item: Dict[str, Any],
    overrides: Dict[str, str],
) -> Dict[str, Any]:
    packed = dict(item)
    packed.pop("_diff_chunk_index", None)
    packed.pop("_diff_chunk_total", None)
    packed["_current_source_override"] = overrides.get("source", "")
    packed["_diff_override"] = overrides.get("diff", "")
    for _, override_key, field in _STAGE1_SHARED_OVERRIDE_FIELDS:
        packed[override_key] = overrides.get(field, "")
    return packed


def _all_stage1_hunk_ids(
    item: Dict[str, Any],
    prepared_context: Stage1PreparedContext,
) -> tuple[str, ...]:
    existing = tuple(item.get("_hunk_ids", ()) or ())
    if existing:
        return tuple(sorted(set(existing)))
    file_info = item.get("file")
    path = getattr(file_info, "path", "")
    diff_file = _find_diff_file_for_path(prepared_context, path)
    compacted_hunk_ids = _compacted_stage_1_hunk_ids(diff_file)
    if compacted_hunk_ids:
        return compacted_hunk_ids
    diff_text, known_hunks, path = _exact_stage1_diff(item, prepared_context)
    if not diff_text:
        return ()
    chunk = _chunk_diff_with_ownership(
        diff_text,
        max(1, (len(diff_text.encode("utf-8")) + 3) // 4 + 1),
        known_hunks=known_hunks,
        path=path,
    )
    return tuple(sorted({hunk_id for part in chunk for hunk_id in part.hunk_ids}))


def _ensure_stage1_review_unit(
    item: Dict[str, Any],
    prepared_context: Stage1PreparedContext,
) -> Dict[str, Any]:
    owned = dict(item)
    file_info = item.get("file")
    path = getattr(file_info, "path", "")
    diff_file = _find_diff_file_for_path(prepared_context, path)
    compacted_hunk_ids = _compacted_stage_1_hunk_ids(diff_file)
    hunk_ids = () if compacted_hunk_ids else _all_stage1_hunk_ids(
        item,
        prepared_context,
    )
    owned["_hunk_ids"] = hunk_ids
    owned = _apply_compacted_stage_1_omission(owned, diff_file, path)
    if not isinstance(owned.get("_review_unit_id"), str) or not owned["_review_unit_id"]:
        diff_text, _, path = _exact_stage1_diff(owned, prepared_context)
        owned["_review_unit_id"] = _review_unit_id(
            path,
            _DiffReviewChunk(diff_text, hunk_ids),
        )
    return owned


def _stage1_atom_marker(atom: _Stage1EvidenceAtom, path: str) -> str:
    return (
        f"[Lossless Stage 1 {atom.field} slice {atom.index}/{atom.total} "
        f"for {path}. This review unit owns this complete semantic slice; "
        "all sibling slices must complete before Stage 1 completes.]\n"
    )


def _stage1_unit_from_atoms(
    base_item: Dict[str, Any],
    repeated: Dict[str, str],
    atoms: List[_Stage1EvidenceAtom],
    path: str,
    required_hunk_ids: tuple[str, ...] = (),
) -> Dict[str, Any]:
    payloads: Dict[str, List[str]] = {
        field: [value] if value else []
        for field, value in repeated.items()
    }
    hunk_ids: set[str] = set(required_hunk_ids)
    for atom in atoms:
        payloads.setdefault(atom.field, []).append(
            _stage1_atom_marker(atom, path) + atom.text
        )
        hunk_ids.update(atom.hunk_ids)
    overrides = {
        field: "\n".join(parts)
        for field, parts in payloads.items()
    }
    unit = _stage1_item_with_overrides(base_item, overrides)
    unit["_hunk_ids"] = tuple(sorted(hunk_ids))
    identity_payload = json.dumps(
        {
            "path": normalize_repository_path(path),
            "hunks": unit["_hunk_ids"],
            "overrides": {
                key: unit.get(key, "")
                for key in (
                    "_current_source_override",
                    "_diff_override",
                    *(entry[1] for entry in _STAGE1_SHARED_OVERRIDE_FIELDS),
                )
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    unit["_review_unit_id"] = "sha256:" + hashlib.sha256(
        identity_payload.encode("utf-8")
    ).hexdigest()
    return unit


def _stage1_evidence_atoms(
    field: str,
    text: str,
    max_tokens: int,
    *,
    diff_hunks: tuple[DiffHunk, ...] = (),
    path: str = "",
) -> List[_Stage1EvidenceAtom]:
    if not text:
        return []
    if field == "diff":
        chunks = _chunk_diff_with_ownership(
            text,
            max(1, max_tokens),
            known_hunks=diff_hunks,
            path=path,
        )
        return [
            _Stage1EvidenceAtom(
                field=field,
                text=chunk.content,
                index=index,
                total=len(chunks),
                hunk_ids=chunk.hunk_ids,
            )
            for index, chunk in enumerate(chunks, start=1)
        ]
    chunks = _split_source_at_semantic_line_boundaries(
        text,
        _text_character_budget(text, max_tokens),
    )
    return [
        _Stage1EvidenceAtom(
            field=field,
            text=chunk,
            index=index,
            total=len(chunks),
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


def _joint_stage1_units_for_item(
    item: Dict[str, Any],
    request: ReviewRequestDto,
    prepared_context: Stage1PreparedContext,
    is_incremental: bool,
    token_budget: int,
    max_units: int = 3,
    *,
    runtime: Stage1LocalPackingRuntime,
) -> List[Dict[str, Any]]:
    """Pack bounded primary evidence without optional-context call fan-out."""
    max_units = max(1, max_units)
    item_path = getattr(item.get("file"), "path", "")
    item_diff_file = _find_diff_file_for_path(prepared_context, item_path)
    item = _apply_compacted_stage_1_omission(
        item,
        item_diff_file,
        item_path,
    )
    material = runtime.prepare_material(
        request,
        [item],
        prepared_context,
        is_incremental,
    )
    full_prompt_tokens = runtime.material_prompt_tokens(material)
    if full_prompt_tokens <= token_budget:
        return [_ensure_stage1_review_unit(item, prepared_context)]

    path = material.batch_file_paths[0]
    diff_text, known_hunks, _ = _exact_stage1_diff(item, prepared_context)
    raw_source_text = str(
        _lookup_by_path(prepared_context.file_content_by_path, path) or ""
    )
    # Reuse the prompt-bounded source projection. Reloading the full file here
    # would silently defeat the Stage 1 source cap whenever semantic packing is
    # activated by another large field.
    source_text = str(material.batch_files_data[0].get("current_code") or "")
    if source_text == _COMPLETE_ADDED_SOURCE_MARKER:
        source_text = ""
    diff_file = _find_diff_file_for_path(
        prepared_context,
        path,
        use_full_diff=_item_requests_full_diff(item),
    )
    if (
        getattr(diff_file, "change_type", None) is DiffChangeType.ADDED
        and _diff_contains_complete_added_source(raw_source_text, diff_text)
    ):
        # The complete added-side source is already owned by the diff stream.
        # Do not serialize it a second time merely because both representations
        # are available.
        source_text = ""
    shared = {
        "boundary_context": material.boundary_context,
        "project_rules": material.project_rules,
        "plugin_context": runtime.complete_plugin_context(request, path),
        "task_context": material.task_context,
        "previous_issues": material.previous_issues_for_batch,
        "metadata": material.file_metadata_text,
    }
    if shared["metadata"]:
        try:
            shared["metadata"] = json.dumps(
                json.loads(shared["metadata"]),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        except (TypeError, ValueError):
            # Arbitrary plugin metadata may stringify non-JSON values. Its
            # deterministic serialized form is still preserved as one stream.
            pass
    empty_overrides = {
        "source": "",
        "diff": "",
        **{field: "" for field in shared},
    }
    base_item = _stage1_item_with_overrides(item, empty_overrides)
    base_material = runtime.prepare_material(
        request,
        [base_item],
        prepared_context,
        is_incremental,
    )
    base_prompt_tokens = runtime.material_prompt_tokens(base_material)
    if base_prompt_tokens >= token_budget:
        logger.warning(
            "Stage 1 fixed prompt scaffold is indivisible above the input "
            "packing target; preserving one complete prompt: path=%s "
            "scaffold_tokens=%d target_tokens=%d full_estimated_tokens=%d",
            path,
            base_prompt_tokens,
            token_budget,
            full_prompt_tokens,
        )
        return [_ensure_stage1_review_unit(item, prepared_context)]

    # Repeat small governing context with every unit while reserving most of the
    # flexible window for owned source/diff evidence. Larger context becomes its
    # own lossless stream instead of being clipped or repeated in every call.
    repeated: Dict[str, str] = {}
    repeat_limit = base_prompt_tokens + max(
        0,
        (token_budget - base_prompt_tokens) // 3,
    )
    for field in (
        "boundary_context",
        "project_rules",
        "plugin_context",
        "task_context",
        "previous_issues",
        "metadata",
    ):
        value = shared[field]
        if not value:
            continue
        candidate = dict(repeated)
        candidate[field] = value
        candidate_item = _stage1_item_with_overrides(
            item,
            {**empty_overrides, **candidate},
        )
        candidate_tokens = _batch_prompt_tokens(
            runtime,
            request,
            [candidate_item],
            prepared_context,
            is_incremental,
        )
        if candidate_tokens <= repeat_limit:
            repeated = candidate

    repeated_item = _stage1_item_with_overrides(
        item,
        {**empty_overrides, **repeated},
    )
    repeated_prompt_tokens = _batch_prompt_tokens(
        runtime,
        request,
        [repeated_item],
        prepared_context,
        is_incremental,
    )
    available_tokens = (
        token_budget
        - repeated_prompt_tokens
        - 256
    )
    if available_tokens < 256:
        logger.warning(
            "Stage 1 fixed/repeated scaffold leaves no useful semantic packing "
            "window; preserving one complete prompt: path=%s available_tokens=%d "
            "target_tokens=%d full_estimated_tokens=%d",
            path,
            max(0, available_tokens),
            token_budget,
            full_prompt_tokens,
        )
        return [_ensure_stage1_review_unit(item, prepared_context)]

    # Optional/shared scaffold is never allowed to manufacture review calls.
    # It is either repeated once when it fits or represented by a truthful
    # omission notice. Primary diff/current-source evidence owns the finite
    # derived-unit budget.
    omitted_shared = {
        field: len(value)
        for field, value in shared.items()
        if value and field not in repeated
    }
    if omitted_shared:
        omission_notice = (
            "[CodeCrow bounded optional Stage 1 context; omitted character "
            "counts: "
            + ", ".join(
                f"{field}={omitted_shared[field]}"
                for field in sorted(omitted_shared)
            )
            + ". Absence from this prompt is not negative evidence.]"
        )
        repeated["boundary_context"] = "\n".join(filter(None, (
            repeated.get("boundary_context", ""),
            omission_notice,
        )))
    stream_text = {
        "diff": diff_text,
        "source": source_text,
    }
    nonempty_streams = [field for field, text in stream_text.items() if text]
    if not nonempty_streams:
        logger.warning(
            "Stage 1 prompt is above the packing target but contains no "
            "shardable local evidence: path=%s estimated_tokens=%d target_tokens=%d",
            path,
            full_prompt_tokens,
            token_budget,
        )
        return [_ensure_stage1_review_unit(item, prepared_context)]

    # Half-window atoms normally pair one diff and one source slice. Other
    # streams fill remaining space greedily. Invocation growth is therefore
    # linear in total evidence, never diff-shards times source-shards.
    atom_budget = max(
        64,
        available_tokens - 96
        if len(nonempty_streams) == 1
        else available_tokens // 2 - 96,
    )
    streams: Dict[str, List[_Stage1EvidenceAtom]] = {}
    for field in ("diff", "source"):
        text = stream_text.get(field, "")
        streams[field] = _stage1_evidence_atoms(
            field,
            text,
            atom_budget,
            diff_hunks=known_hunks if field == "diff" else (),
            path=path,
        )

    ordered_atoms: List[_Stage1EvidenceAtom] = []
    max_stream_length = max((len(atoms) for atoms in streams.values()), default=0)
    for index in range(max_stream_length):
        for field in ("diff", "source"):
            if index < len(streams[field]):
                ordered_atoms.append(streams[field][index])

    packed_atom_groups: List[List[_Stage1EvidenceAtom]] = []
    current_atoms: List[_Stage1EvidenceAtom] = []
    for atom in ordered_atoms:
        candidate_atoms = current_atoms + [atom]
        candidate_item = _stage1_unit_from_atoms(
            item,
            repeated,
            candidate_atoms,
            path,
            (),
        )
        candidate_tokens = _batch_prompt_tokens(
            runtime,
            request,
            [candidate_item],
            prepared_context,
            is_incremental,
        )
        repeats_field = any(existing.field == atom.field for existing in current_atoms)
        if current_atoms and (
            repeats_field
            or candidate_tokens > token_budget
        ):
            packed_atom_groups.append(current_atoms)
            current_atoms = [atom]
        else:
            current_atoms = candidate_atoms
    if current_atoms:
        packed_atom_groups.append(current_atoms)

    omitted_groups = packed_atom_groups[max_units:]
    packed_atom_groups = packed_atom_groups[:max_units]
    omitted_hunk_ids = tuple(sorted({
        hunk_id
        for group in omitted_groups
        for atom in group
        for hunk_id in atom.hunk_ids
    }))
    omitted_context_chars = sum(
        len(atom.text)
        for group in omitted_groups
        for atom in group
        if atom.field != "diff"
    )
    if omitted_groups and packed_atom_groups:
        notice_atom = _Stage1EvidenceAtom(
            field="source",
            text=(
                "[CodeCrow Stage 1 invocation ceiling reached: "
                f"omitted_units={len(omitted_groups)}, "
                f"omitted_hunks={len(omitted_hunk_ids)}, "
                f"omitted_context_chars={omitted_context_chars}. "
                "Omitted evidence is not negative evidence.]"
            ),
            index=1,
            total=1,
        )
        packed_atom_groups[-1] = [*packed_atom_groups[-1], notice_atom]

    units = [
        _stage1_unit_from_atoms(
            item,
            repeated,
            atoms,
            path,
            (),
        )
        for atoms in packed_atom_groups
    ]
    inherited_omitted_units = int(
        item.get("_omitted_stage1_unit_count", 0) or 0
    )
    inherited_omitted_hunks = tuple(
        item.get("_omitted_hunk_ids", ()) or ()
    )
    inherited_omitted_paths = tuple(
        item.get("_omitted_stage1_paths", ()) or ()
    )
    inherited_omitted_context = int(
        item.get("_omitted_context_chars", 0) or 0
    )
    for unit in units:
        unit.pop("_omitted_stage1_unit_count", None)
        unit.pop("_omitted_hunk_ids", None)
        unit.pop("_omitted_stage1_paths", None)
        unit.pop("_omitted_context_chars", None)
    for unit_index, unit in enumerate(units, start=1):
        unit["_joint_unit_index"] = unit_index
        unit["_joint_unit_total"] = len(units)
        if unit_index == len(units) and (
            omitted_groups or inherited_omitted_units or inherited_omitted_hunks
            or inherited_omitted_paths or inherited_omitted_context
        ):
            unit["_omitted_stage1_unit_count"] = (
                inherited_omitted_units + len(omitted_groups)
            )
            unit["_omitted_hunk_ids"] = tuple(sorted({
                *inherited_omitted_hunks,
                *omitted_hunk_ids,
            }))
            unit["_omitted_stage1_paths"] = inherited_omitted_paths
            unit["_omitted_context_chars"] = (
                inherited_omitted_context + omitted_context_chars
            )
        if omitted_hunk_ids:
            unit["_hunk_ids"] = tuple(
                hunk_id
                for hunk_id in unit.get("_hunk_ids", ())
                if hunk_id not in omitted_hunk_ids
            )
        unit_tokens = _batch_prompt_tokens(
            runtime,
            request,
            [unit],
            prepared_context,
            is_incremental,
        )
        if unit_tokens > token_budget:
            fields = sorted({atom.field for atom in packed_atom_groups[unit_index - 1]})
            logger.warning(
                "Stage 1 retained an indivisible semantic local-evidence unit "
                "above the packing target: path=%s fields=%s "
                "estimated_tokens=%d target_tokens=%d",
                path,
                fields,
                unit_tokens,
                token_budget,
            )

    logger.info(
        "Stage 1 bounded oversized local evidence for %s into %d joint "
        "unit(s): diff_slices=%d source_slices=%d omitted_units=%d "
        "omitted_hunks=%d omitted_context_chars=%d",
        path,
        len(units),
        len(streams["diff"]),
        len(streams["source"]),
        len(omitted_groups),
        len(omitted_hunk_ids),
        omitted_context_chars,
    )
    return units


def _expand_oversized_stage1_evidence_batches(
    batches: List[List[Dict[str, Any]]],
    request: ReviewRequestDto,
    prepared_context: Stage1PreparedContext,
    is_incremental: bool,
    token_budget: int,
    max_units_per_item: int = 3,
    max_total_batches: Optional[int] = None,
    *,
    runtime: Stage1LocalPackingRuntime,
) -> List[List[Dict[str, Any]]]:
    """Assign bounded ownership under one review-wide semantic-call budget."""
    expanded: List[List[Dict[str, Any]]] = []
    total_budget = max(
        len(batches),
        max_total_batches if isinstance(max_total_batches, int) else len(batches),
    )
    remaining_extra = max(0, total_budget - len(batches))
    used_by_key: Counter[tuple[str, ...]] = Counter(
        tuple(sorted({
            normalize_repository_path(getattr(item.get("file"), "path", ""))
            for item in batch
            if getattr(item.get("file"), "path", "")
        })) or ("<unknown>",)
        for batch in batches
    )
    for batch in batches:
        prompt_tokens = _batch_prompt_tokens(
            runtime,
            request,
            batch,
            prepared_context,
            is_incremental,
        )
        if len(batch) > 1 or prompt_tokens <= token_budget:
            expanded.append([
                _ensure_stage1_review_unit(item, prepared_context)
                for item in batch
            ])
            continue
        key = tuple(sorted({
            normalize_repository_path(getattr(item.get("file"), "path", ""))
            for item in batch
            if getattr(item.get("file"), "path", "")
        })) or ("<unknown>",)
        extra_for_key = max(0, max_units_per_item - used_by_key[key])
        units = _joint_stage1_units_for_item(
            batch[0],
            request,
            prepared_context,
            is_incremental,
            token_budget,
            max_units=1 + min(remaining_extra, extra_for_key),
            runtime=runtime,
        )
        expanded.extend([unit] for unit in units)
        admitted_extra = max(0, len(units) - 1)
        used_by_key[key] += admitted_extra
        remaining_extra = max(0, remaining_extra - admitted_extra)
    return expanded


def _expand_oversized_current_source_batches(
    batches: List[List[Dict[str, Any]]],
    request: ReviewRequestDto,
    prepared_context: Stage1PreparedContext,
    is_incremental: bool,
    token_budget: int,
    *,
    runtime: Stage1LocalPackingRuntime,
) -> List[List[Dict[str, Any]]]:
    """Compatibility wrapper for the joint lossless local-evidence packer."""
    return _expand_oversized_stage1_evidence_batches(
        batches,
        request,
        prepared_context,
        is_incremental,
        token_budget,
        runtime=runtime,
    )


def pack_stage1_local_batches(
    batches: List[List[Dict[str, Any]]],
    packing_input: Stage1LocalPackingInput,
    runtime: Stage1LocalPackingRuntime,
    *,
    max_units_per_item: int = 3,
    max_total_batches: Optional[int] = None,
) -> List[List[Dict[str, Any]]]:
    """Expand oversized evidence through the neutral typed packing boundary."""
    return _expand_oversized_stage1_evidence_batches(
        batches,
        packing_input.request,
        packing_input.prepared_context,
        packing_input.is_incremental,
        packing_input.token_budget,
        max_units_per_item=max_units_per_item,
        max_total_batches=max_total_batches,
        runtime=runtime,
    )


def _allocate_stage1_invocation_quotas(
    batches: List[List[Dict[str, Any]]],
    total_cap: int,
    per_original_unit_cap: int,
) -> int:
    """Allocate one shared evidence/RAG call budget deterministically."""
    if not batches:
        return 0
    effective_cap = max(1, total_cap)
    if len(batches) > effective_cap:
        raise RuntimeError(
            "Stage 1 core batches were not admitted under the profile "
            f"invocation ceiling: batches={len(batches)} cap={effective_cap}"
        )

    keys: List[tuple[str, ...]] = []
    for batch in batches:
        key = tuple(sorted({
            normalize_repository_path(getattr(item.get("file"), "path", ""))
            for item in batch
            if getattr(item.get("file"), "path", "")
        })) or ("<unknown>",)
        keys.append(key)

    used_by_key = Counter(keys)
    quotas = [1 for _ in batches]
    remaining = max(0, effective_cap - len(batches))
    while remaining:
        admitted = False
        for index, key in enumerate(keys):
            if used_by_key[key] >= max(1, per_original_unit_cap):
                continue
            quotas[index] += 1
            used_by_key[key] += 1
            remaining -= 1
            admitted = True
            if not remaining:
                break
        if not admitted:
            break

    for batch, quota in zip(batches, quotas):
        for item in batch:
            item["_stage1_invocation_quota"] = quota
    admitted_total = sum(quotas)
    logger.info(
        "Stage 1 concrete invocation quotas: batches=%d admitted=%d "
        "profile_cap=%d per_original_unit_cap=%d",
        len(batches),
        admitted_total,
        total_cap,
        per_original_unit_cap,
    )
    return admitted_total


def _repack_stage1_batches_by_rendered_input(
    batches: List[List[Dict[str, Any]]],
    request: ReviewRequestDto,
    prepared_context: Stage1PreparedContext,
    is_incremental: bool,
    token_budget: int,
    *,
    runtime: Stage1LocalPackingRuntime,
) -> List[List[Dict[str, Any]]]:
    """Split existing graph-ordered batches using the rendered local prompt."""
    repacked: List[List[Dict[str, Any]]] = []
    for batch in batches:
        current: List[Dict[str, Any]] = []
        for item in batch:
            candidate = current + [item]
            prompt_tokens = _batch_prompt_tokens(
                runtime,
                request,
                candidate,
                prepared_context,
                is_incremental,
            )
            if current and prompt_tokens > token_budget:
                repacked.append(current)
                current = [item]
            else:
                current = candidate
        if current:
            repacked.append(current)
    return repacked


def _partition_oversized_stage1_batch(
    batch_items: List[Dict[str, Any]],
    request: ReviewRequestDto,
    prepared_context: Stage1PreparedContext,
    is_incremental: bool,
    token_budget: int,
    *,
    runtime: Stage1LocalPackingRuntime,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Choose a stable graph cut that favors fitting packs, then fewer cut edges."""
    if len(batch_items) < 2:
        return batch_items, []

    graph_edges: set[frozenset[str]] = set()
    enrichment = getattr(request, "enrichmentData", None)
    for relationship in getattr(enrichment, "relationships", None) or []:
        source = normalize_repository_path(
            getattr(relationship, "sourceFile", "")
        )
        target = normalize_repository_path(
            getattr(relationship, "targetFile", "")
        )
        if source and target and source != target:
            graph_edges.add(frozenset((source, target)))
    for item in batch_items:
        path = normalize_repository_path(getattr(item.get("file"), "path", ""))
        for related in item.get("related_files", ()) or ():
            related_path = normalize_repository_path(related)
            if path and related_path and path != related_path:
                graph_edges.add(frozenset((path, related_path)))

    candidates = []
    for split_at in range(1, len(batch_items)):
        left = batch_items[:split_at]
        right = batch_items[split_at:]
        left_paths = {
            normalize_repository_path(getattr(item.get("file"), "path", ""))
            for item in left
        }
        right_paths = {
            normalize_repository_path(getattr(item.get("file"), "path", ""))
            for item in right
        }
        crossing_edges = sum(
            1
            for edge in graph_edges
            if edge & left_paths and edge & right_paths
        )
        left_tokens = _batch_prompt_tokens(
            runtime,
            request,
            left,
            prepared_context,
            is_incremental,
        )
        right_tokens = _batch_prompt_tokens(
            runtime,
            request,
            right,
            prepared_context,
            is_incremental,
        )
        largest = max(left_tokens, right_tokens)
        fits = largest <= token_budget
        if fits:
            key = (0, crossing_edges, largest, abs(left_tokens - right_tokens))
        else:
            key = (1, largest, crossing_edges, abs(left_tokens - right_tokens))
        candidates.append((key, left, right))

    _, left, right = min(candidates, key=lambda candidate: candidate[0])
    return left, right
