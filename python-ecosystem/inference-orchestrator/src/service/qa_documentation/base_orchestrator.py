"""
Base Multi-Stage Orchestrator.

Provides shared infrastructure for multi-stage LLM pipelines:
- LLM instance management
- Smart dependency-aware batching via DependencyGraphBuilder
- Diff filtering for per-batch file subsets
- Event emission helpers

Subclass: QaDocOrchestrator
"""
import bisect
import json
import logging
import os
import re
from abc import ABC
from dataclasses import dataclass, replace
from typing import Dict, Any, List, Optional, Callable, Set, Sequence

from model.enrichment import PrEnrichmentDataDto

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %d", name, value, default)
        return default


# This is an input-packing target, independent from the finite output cap bound
# when the QA model is constructed.
QA_INPUT_TOKEN_TARGET = max(
    10_000,
    _env_int("REVIEW_STAGE1_BATCH_TOKEN_BUDGET", 60_000),
)
QA_INPUT_ESTIMATOR_SAFETY_TOKENS = 512
QA_OUTPUT_CONTEXT_RESERVE_TOKENS = 20_000


class QaPromptPackingError(RuntimeError):
    """Raised when the invariant QA prompt itself cannot fit the request."""


@dataclass(frozen=True)
class QaSemanticRecord:
    """One QA evidence record or bounded free-text continuation."""

    key: str
    section: str
    text: str
    paths: tuple[str, ...] = ()
    source_key: str = ""
    part_index: int = 1
    part_count: int = 1
    character_start: int = 0
    character_end: int = 0
    source_character_count: int = 0

    @property
    def identity(self) -> str:
        return self.source_key or self.key

    @property
    def display_key(self) -> str:
        if self.part_count <= 1:
            return self.key
        return (
            f"{self.identity}:part:{self.part_index:06d}-of-"
            f"{self.part_count:06d}"
        )

    def envelope(self) -> str:
        """Render provenance without changing or clipping the owned text."""
        metadata = {
            "recordKey": self.identity,
            "section": self.section,
            "paths": list(self.paths),
            "partIndex": self.part_index,
            "partCount": self.part_count,
            "characterStart": self.character_start,
            "characterEnd": self.character_end or len(self.text),
            "sourceCharacterCount": self.source_character_count or len(self.text),
        }
        return (
            "[QA_SEMANTIC_RECORD "
            + json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "]\n"
            + self.text
        )


_SEMANTIC_TEXT_BOUNDARY_RE = re.compile(r"(?:\r\n|\r|\n)+|[^\S\r\n]+")


# ── Event emission helpers ───────────────────────────────────────────

def emit_status(callback: Optional[Callable], stage: str, message: str) -> None:
    """Emit a status event to the caller (non-blocking)."""
    if callback:
        try:
            callback({"type": "status", "stage": stage, "message": message})
        except Exception:
            pass


def emit_progress(callback: Optional[Callable], percent: int, message: str) -> None:
    """Emit a progress event (0-100) to the caller."""
    if callback:
        try:
            callback({"type": "progress", "percent": percent, "message": message})
        except Exception:
            pass


def emit_error(callback: Optional[Callable], error: str) -> None:
    """Emit an error event to the caller."""
    if callback:
        try:
            callback({"type": "error", "message": error})
        except Exception:
            pass


class BaseOrchestrator(ABC):
    """
    Abstract base for multi-stage LLM pipelines.

    Provides:
    - ``self.llm`` — the LangChain LLM instance
    - ``self.event_callback`` — optional SSE/WS event emitter
    - ``build_dependency_batches()`` — smart batching from enrichment data
    - ``filter_diff_for_files()`` — per-batch diff slicing
    """

    def __init__(
        self,
        llm,
        event_callback: Optional[Callable[[Dict], None]] = None,
    ):
        self.llm = llm
        self.event_callback = event_callback
        self.qa_input_token_target = QA_INPUT_TOKEN_TARGET

    # ── Request-aware semantic prompt packing ────────────────────────

    @staticmethod
    def request_input_token_target(max_allowed_tokens: Any = None, *, persisted_context: bool = False) -> int:
        """Return an input target with room left for provider-native output."""
        if isinstance(max_allowed_tokens, bool):
            max_allowed_tokens = None
        try:
            model_context_tokens = (
                int(max_allowed_tokens)
                if max_allowed_tokens is not None
                else None
            )
        except (TypeError, ValueError):
            model_context_tokens = None
        if model_context_tokens is None or model_context_tokens <= 0:
            if not persisted_context:
                return QA_INPUT_TOKEN_TARGET
            model_context_tokens = 200_000
        if model_context_tokens > QA_OUTPUT_CONTEXT_RESERVE_TOKENS:
            model_safe_target = (
                model_context_tokens - QA_OUTPUT_CONTEXT_RESERVE_TOKENS
            )
        else:
            model_safe_target = max(1, model_context_tokens // 2)
        return model_safe_target if persisted_context else min(QA_INPUT_TOKEN_TARGET, model_safe_target)

    def set_request_input_token_target(self, max_allowed_tokens: Any = None, *, persisted_context: bool = False) -> int:
        self.qa_input_token_target = self.request_input_token_target(
            max_allowed_tokens, persisted_context=persisted_context,
        )
        return self.qa_input_token_target

    def input_token_target(self) -> int:
        return int(
            getattr(self, "qa_input_token_target", QA_INPUT_TOKEN_TARGET)
            or QA_INPUT_TOKEN_TARGET
        )

    @staticmethod
    def estimate_rendered_input_tokens(
        messages: Any,
        *,
        tool_definitions: Optional[Any] = None,
        response_schema: Optional[Any] = None,
    ) -> int:
        """Estimate complete rendered UTF-8 messages and bound declarations."""
        payload: Dict[str, Any] = {"messages": messages}
        if tool_definitions is not None:
            payload["tools"] = tool_definitions
        if response_schema is not None:
            payload["response_schema"] = response_schema
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return max(
            1,
            (len(encoded) + 2) // 3 + QA_INPUT_ESTIMATOR_SAFETY_TOKENS,
        )

    def prompt_fits(
        self,
        messages: Any,
        *,
        token_target: Optional[int] = None,
        tool_definitions: Optional[Any] = None,
        response_schema: Optional[Any] = None,
    ) -> bool:
        return self.estimate_rendered_input_tokens(
            messages,
            tool_definitions=tool_definitions,
            response_schema=response_schema,
        ) <= (token_target or self.input_token_target())

    @classmethod
    def _split_semantic_record(
        cls,
        record: QaSemanticRecord,
        render_messages: Callable[[Sequence[QaSemanticRecord]], Any],
        token_target: int,
    ) -> List[QaSemanticRecord]:
        """Split free text losslessly, preferring line/whitespace boundaries."""
        if cls.estimate_rendered_input_tokens(render_messages([record])) <= token_target:
            return [record]
        if not record.text:
            raise QaPromptPackingError(
                f"QA fixed prompt exceeds target for empty record {record.key!r}"
            )

        text = record.text
        boundaries = sorted({
            match.end() for match in _SEMANTIC_TEXT_BOUNDARY_RE.finditer(text)
        })
        spans: List[tuple[int, int]] = []
        start = 0
        probe_number = 999_999_999
        while start < len(text):
            low = start + 1
            high = len(text)
            maximum_end = start
            while low <= high:
                middle = (low + high) // 2
                candidate = replace(
                    record,
                    key=f"{record.identity}:part:{probe_number}",
                    source_key=record.identity,
                    text=text[start:middle],
                    part_index=probe_number,
                    part_count=probe_number,
                    character_start=start,
                    character_end=middle,
                    source_character_count=len(text),
                )
                if (
                    cls.estimate_rendered_input_tokens(
                        render_messages([candidate])
                    )
                    <= token_target
                ):
                    maximum_end = middle
                    low = middle + 1
                else:
                    high = middle - 1

            if maximum_end == start:
                raise QaPromptPackingError(
                    "QA fixed prompt/template exceeds the request-aware input "
                    f"target before record {record.key!r} can contribute one "
                    "Unicode code point"
                )
            boundary_index = bisect.bisect_right(
                boundaries,
                maximum_end,
            ) - 1
            semantic_end = (
                boundaries[boundary_index]
                if boundary_index >= 0 and boundaries[boundary_index] > start
                else maximum_end
            )
            spans.append((start, semantic_end))
            start = semantic_end

        total = len(spans)
        fragments = [
            replace(
                record,
                key=f"{record.identity}:part:{index:06d}",
                source_key=record.identity,
                text=text[start:end],
                part_index=index,
                part_count=total,
                character_start=start,
                character_end=end,
                source_character_count=len(text),
            )
            for index, (start, end) in enumerate(spans, start=1)
        ]
        if "".join(fragment.text for fragment in fragments) != text:
            raise QaPromptPackingError(
                f"QA semantic split failed exact reconstruction for {record.key!r}"
            )
        if any(
            cls.estimate_rendered_input_tokens(render_messages([fragment]))
            > token_target
            for fragment in fragments
        ):
            raise QaPromptPackingError(
                f"QA semantic split remained above target for {record.key!r}"
            )
        return fragments

    @classmethod
    def pack_semantic_records(
        cls,
        records: Sequence[QaSemanticRecord],
        render_messages: Callable[[Sequence[QaSemanticRecord]], Any],
        token_target: int,
        *,
        fragment_token_target: Optional[int] = None,
    ) -> List[List[QaSemanticRecord]]:
        """Pack every evidence record into request-safe calls without dropping bytes."""
        originals = list(records)
        identities = [record.identity for record in originals]
        if len(identities) != len(set(identities)):
            raise QaPromptPackingError(
                "QA semantic record identities must be unique before packing"
            )
        if not originals:
            empty_messages = render_messages([])
            if cls.estimate_rendered_input_tokens(empty_messages) > token_target:
                raise QaPromptPackingError(
                    "QA fixed prompt/template exceeds the request-aware target"
                )
            return [[]]

        packing_target = token_target

        expanded = [
            fragment
            for record in originals
            for fragment in cls._split_semantic_record(
                record,
                render_messages,
                min(packing_target, fragment_token_target or packing_target),
            )
        ]

        packets: List[List[QaSemanticRecord]] = []
        current: List[QaSemanticRecord] = []
        for record in expanded:
            candidate = [*current, record]
            if (
                current
                and cls.estimate_rendered_input_tokens(
                    render_messages(candidate)
                ) > packing_target
            ):
                packets.append(current)
                current = [record]
            else:
                current = candidate
        if current:
            packets.append(current)

        by_identity: Dict[str, List[QaSemanticRecord]] = {}
        for packet in packets:
            if cls.estimate_rendered_input_tokens(
                render_messages(packet)
            ) > token_target:
                raise QaPromptPackingError(
                    "QA semantic packet exceeds the request-aware target"
                )
            for record in packet:
                by_identity.setdefault(record.identity, []).append(record)

        if set(by_identity) != set(identities):
            raise QaPromptPackingError("QA semantic packing lost an input record")
        original_by_identity = {
            record.identity: record.text for record in originals
        }
        for identity, fragments in by_identity.items():
            ordered = sorted(fragments, key=lambda item: item.part_index)
            combined = "".join(item.text for item in ordered)
            if combined != original_by_identity[identity]:
                raise QaPromptPackingError(
                    f"QA semantic packing lost or repeated bytes for {identity!r}"
                )
        return packets

    def build_dependency_batches(
        self,
        changed_file_paths: List[str],
        enrichment_data: Optional[PrEnrichmentDataDto],
        diff: Optional[str] = None,
        max_batch_tokens: int = 60_000,
    ) -> List[List[Dict[str, Any]]]:
        """
        Build dependency-aware file batches using the DependencyGraphBuilder.

        Falls back to simple sequential batching if enrichment data is unavailable.
        Returns a list of batches, where each batch is a list of dicts with
        ``file_info``, ``priority``, etc.
        """
        from utils.dependency_graph import build_dependency_aware_batches

        try:
            batches = build_dependency_aware_batches(
                changed_files=changed_file_paths,
                enrichment_data=enrichment_data,
                max_batch_token_budget=max_batch_tokens,
                diff=diff,
            )
            if batches:
                logger.info(
                    "Dependency-aware batching: %d files → %d batches",
                    len(changed_file_paths), len(batches),
                )
                return self._bounded_dependency_batches(
                    batches,
                    enrichment_data,
                )
        except Exception as e:
            logger.warning("Dependency batching failed, falling back to sequential: %s", e)

        # Fallback: simple sequential batches of ~15 files each
        return self._bounded_dependency_batches(
            self._simple_batch(changed_file_paths, batch_size=15),
            enrichment_data,
        )

    def _bounded_dependency_batches(
        self,
        batches: Sequence[Sequence[Dict[str, Any]]],
        enrichment_data: Optional[PrEnrichmentDataDto],
    ) -> List[List[Dict[str, Any]]]:
        """Coalesce small groups without omitting changed paths."""
        materialized = [list(batch) for batch in batches]
        coalesced: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        for batch in materialized:
            if current and len(current) + len(batch) > 15:
                coalesced.append(current)
                current = []
            current.extend(batch)
        if current:
            coalesced.append(current)
        return coalesced

    @staticmethod
    def _simple_batch(
        paths: List[str], batch_size: int = 15
    ) -> List[List[Dict[str, Any]]]:
        """Fallback: chunk file paths into fixed-size batches."""
        batches = []
        for i in range(0, len(paths), batch_size):
            chunk = paths[i : i + batch_size]
            batches.append([{"file_info": type("FI", (), {"path": p})(), "priority": "MEDIUM"} for p in chunk])
        return batches

    # ── Diff filtering ───────────────────────────────────────────────

    @staticmethod
    def filter_diff_for_files(
        raw_diff: Optional[str], file_paths: Set[str]
    ) -> Optional[str]:
        """
        Filter a unified diff to include only hunks for the given file paths.
        Returns ``None`` if no relevant hunks are found.

        Uses suffix matching so that path format differences between the
        diff headers (e.g. ``src/main/Foo.java``) and ``file_paths``
        (e.g. ``repo/src/main/Foo.java`` or ``main/Foo.java``) don't
        silently cause the filter to drop every section.
        """
        if not raw_diff or not file_paths:
            return None

        sections = re.split(r'(?=^diff --git )', raw_diff, flags=re.MULTILINE)
        relevant = []

        def _matches(diff_path: str) -> bool:
            """Check if diff_path matches any entry in file_paths."""
            if diff_path in file_paths:
                return True
            for fp in file_paths:
                if fp.endswith(diff_path) or diff_path.endswith(fp):
                    return True
            return False

        for section in sections:
            if not section.strip():
                continue
            header_match = re.match(r'diff --git a/(.+?) b/(.+?)(?:\n|$)', section)
            if header_match:
                a_path = header_match.group(1)
                b_path = header_match.group(2)
                if _matches(a_path) or _matches(b_path):
                    relevant.append(section)

        return "\n".join(relevant) if relevant else None

    # ── Enrichment helpers ───────────────────────────────────────────

    @staticmethod
    def get_file_content_from_enrichment(
        path: str,
        enrichment_data: Optional[PrEnrichmentDataDto],
    ) -> Optional[str]:
        """Look up full file content from enrichment data by path."""
        if not enrichment_data or not enrichment_data.fileContents:
            return None
        for fc in enrichment_data.fileContents:
            if fc.path == path and fc.content and not fc.skipped:
                return fc.content
            # Suffix match
            if path.endswith(fc.path) or fc.path.endswith(path):
                if fc.content and not fc.skipped:
                    return fc.content
        return None

    @staticmethod
    def build_enrichment_lookup(
        enrichment_data: Optional[PrEnrichmentDataDto],
    ) -> Dict[str, str]:
        """Build a path → content dict from enrichment data."""
        lookup: Dict[str, str] = {}
        if not enrichment_data or not enrichment_data.fileContents:
            return lookup
        for fc in enrichment_data.fileContents:
            if fc.content and not fc.skipped:
                lookup[fc.path] = fc.content
                parts = fc.path.split("/", 1)
                if len(parts) > 1:
                    lookup[parts[1]] = fc.content
        return lookup
