"""Observable, source-free Stage 1 repository-agent telemetry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import logging
import re
import time
from typing import Any, Callable, Optional

from service.review.orchestrator.stage_1_tool_inventory import (
    STAGE1_BRANCH_FILE_TOOL_NAME,
    STAGE1_REVIEW_FILE_TOOL_NAME,
    STAGE1_STRUCTURAL_OBSERVATION_TOOL_NAMES,
)


logger = logging.getLogger(__name__)
_EVIDENCE_ID = re.compile(r"^relation:[0-9a-f]{64}$")
_SOURCE_READ_TOOL_NAMES = frozenset({
    STAGE1_BRANCH_FILE_TOOL_NAME,
    STAGE1_REVIEW_FILE_TOOL_NAME,
})
_NON_DEGRADING_TOOL_REJECTIONS = frozenset({
    (STAGE1_REVIEW_FILE_TOOL_NAME, "current_source_already_supplied"),
})


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    if isinstance(value, (Mapping, list, tuple)):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except (TypeError, ValueError):
            return str(value)
    return value


def _json_bytes(value: Any) -> int:
    try:
        return len(json.dumps(
            _json_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8"))


def _action_value(action: Any, key: str) -> Any:
    if isinstance(action, Mapping):
        return action.get(key)
    return getattr(action, key, None)


def _tool_name(action: Any) -> str:
    return str(
        _action_value(action, "tool")
        or _action_value(action, "name")
        or "unknown"
    )


def _tool_input(action: Any) -> Mapping[str, Any]:
    value = (
        _action_value(action, "tool_input")
        or _action_value(action, "args")
        or {}
    )
    decoded = _json_value(value)
    return decoded if isinstance(decoded, Mapping) else {}


def _positive_line_number(value: Any) -> int | None:
    """Return a model-requested positive source line without bool coercion."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _source_read_request_summary(
    name: str,
    action: Any,
) -> dict[str, Any] | None:
    """Describe only the safe request shape of one Stage 1 file read.

    A valid positive ``startLine`` activates the bounded server path. Its
    optional ``endLine`` must also be a positive line at or after the start.
    Everything else is conservatively reported as a whole-file request. Raw
    tool arguments are deliberately excluded because they also carry tenant
    and repository identity.
    """
    if name not in _SOURCE_READ_TOOL_NAMES:
        return None

    arguments = _tool_input(action)
    raw_start = arguments.get("startLine")
    raw_end = arguments.get("endLine")
    start_provided = raw_start is not None
    end_provided = raw_end is not None
    start_line = _positive_line_number(raw_start)
    end_line = _positive_line_number(raw_end)

    if not start_provided and not end_provided:
        return {
            "mode": "whole_file",
            "boundsState": "absent",
        }
    if not start_provided:
        return {
            "mode": "whole_file",
            "boundsState": (
                "partial" if end_line is not None else "invalid"
            ),
        }
    if (
        start_line is None
        or (end_provided and end_line is None)
        or (end_line is not None and end_line < start_line)
    ):
        return {
            "mode": "whole_file",
            "boundsState": "invalid",
        }

    effective_end_line = end_line if end_line is not None else start_line
    return {
        "mode": "bounded_range",
        "boundsState": "valid",
        "startLine": start_line,
        "endLine": effective_end_line,
        "requestedLineCount": effective_end_line - start_line + 1,
    }


def _aggregate_source_read_requests(
    tool_sequence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate file-read request shapes without changing legacy path counts."""
    total_requests = 0
    bounded_range_requests = 0
    whole_file_requests = 0
    by_tool: dict[str, dict[str, int]] = {}
    for event in tool_sequence:
        request = event.get("sourceRead")
        if not isinstance(request, Mapping):
            continue
        name = str(event.get("name") or "unknown")
        mode = request.get("mode")
        if mode not in {"bounded_range", "whole_file"}:
            continue
        counts = by_tool.setdefault(name, {
            "totalRequests": 0,
            "boundedRangeRequests": 0,
            "wholeFileRequests": 0,
        })
        total_requests += 1
        counts["totalRequests"] += 1
        if mode == "bounded_range":
            bounded_range_requests += 1
            counts["boundedRangeRequests"] += 1
        else:
            whole_file_requests += 1
            counts["wholeFileRequests"] += 1
    return {
        "totalRequests": total_requests,
        "boundedRangeRequests": bounded_range_requests,
        "wholeFileRequests": whole_file_requests,
        "byTool": {
            name: by_tool[name]
            for name in sorted(by_tool)
        },
    }


def _tool_observation_error(
    observation: Any,
) -> tuple[str, str, Any] | None:
    """Return the failure carried by an otherwise completed tool event."""

    decoded = _json_value(observation)
    if not isinstance(decoded, Mapping):
        return None

    declared_status = str(decoded.get("status", "")).strip().casefold()
    explicit_error = decoded.get("error")
    if declared_status in {"error", "failed"}:
        failure_status = "error"
    elif (
        declared_status in {"unavailable", "disabled"}
        or decoded.get("unavailable") is True
    ):
        failure_status = "unavailable"
    elif (
        explicit_error is not None
        and explicit_error is not False
        and explicit_error != ""
    ):
        failure_status = "error"
    else:
        return None

    detail_value = explicit_error or decoded.get("message")
    default_detail = f"tool returned status:{failure_status}"
    if isinstance(detail_value, str):
        detail = detail_value.strip()
    elif detail_value is None:
        detail = default_detail
    else:
        try:
            detail = json.dumps(
                detail_value,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        except (TypeError, ValueError):
            detail = str(detail_value)
    if not detail:
        detail = default_detail

    status_code = decoded.get("status_code")
    if status_code is None:
        status_code = decoded.get("statusCode")
    return failure_status, detail, status_code


def stage1_agent_tool_event_failures(
    events: Sequence[Any],
) -> tuple[str, ...]:
    """Describe structured tool failures present in an agent transcript."""

    failures: list[str] = []
    for event in events:
        failure = _tool_observation_error(
            getattr(event, "observation", None),
        )
        if failure is None:
            continue
        failure_status, detail, status_code = failure
        status_suffix = (
            f" (status {status_code})"
            if status_code is not None
            else ""
        )
        failures.append(
            f"{_tool_name(getattr(event, 'action', None))} returned "
            f"status:{failure_status}{status_suffix}: {detail}"
        )
    return tuple(failures)


def _walk(value: Any):
    value = _json_value(value)
    yield value
    if isinstance(value, Mapping):
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk(nested)


def _evidence_ids(value: Any) -> list[str]:
    identifiers = {
        nested
        for nested in _walk(value)
        if isinstance(nested, str) and _EVIDENCE_ID.fullmatch(nested)
    }
    return sorted(identifiers)


def _snapshot(value: Any) -> dict[str, Any] | None:
    decoded = _json_value(value)
    if not isinstance(decoded, Mapping):
        return None
    candidate = decoded.get("snapshot")
    if not isinstance(candidate, Mapping):
        context = decoded.get("context")
        candidate = context.get("snapshot") if isinstance(context, Mapping) else None
    if not isinstance(candidate, Mapping):
        return None
    result = {
        key: candidate[key]
        for key in (
            "kind",
            "branch",
            "revision",
            "baseRevision",
            "baseCollectionTarget",
            "baseGenerationManifestSha256",
            "sourceRevision",
            "generationManifestSha256",
        )
        if candidate.get(key) is not None
    }
    return result or None


def _result_counts(value: Any) -> dict[str, int]:
    decoded = _json_value(value)
    if not isinstance(decoded, Mapping):
        return {}
    counts: dict[str, int] = {}
    explicit = decoded.get("resultCount")
    if isinstance(explicit, int) and not isinstance(explicit, bool):
        counts["results"] = max(0, explicit)
    for key, label in (
        ("results", "results"),
        ("relations", "relations"),
        ("nodes", "nodes"),
        ("edges", "edges"),
        ("roots", "roots"),
        ("frontier", "frontier"),
        ("impactedFiles", "impactedFiles"),
        ("nextOperations", "nextOperations"),
        ("sourceWindows", "sourceWindows"),
        ("omittedFollowups", "omittedFollowups"),
    ):
        candidate = decoded.get(key)
        if isinstance(candidate, list):
            counts[label] = len(candidate)
    evidence = decoded.get("evidence")
    if isinstance(evidence, Mapping):
        if isinstance(evidence.get("relations"), list):
            counts["relations"] = len(evidence["relations"])
        if isinstance(evidence.get("nodes"), list):
            counts["nodes"] = len(evidence["nodes"])
    changed = decoded.get("changed")
    if isinstance(changed, Mapping):
        for key, label in (
            ("units", "changedUnits"),
            ("symbols", "changedSymbols"),
            ("paths", "changedPaths"),
        ):
            candidate = changed.get(key)
            if isinstance(candidate, list):
                counts[label] = len(candidate)
    return dict(sorted(counts.items()))


def _read_paths(name: str, action: Any, observation: Any) -> list[str]:
    paths: set[str] = set()
    arguments = _tool_input(action)
    if name in _SOURCE_READ_TOOL_NAMES:
        for key in ("filePath", "path"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                paths.add(value.strip())
    if name in STAGE1_STRUCTURAL_OBSERVATION_TOOL_NAMES:
        decoded = _json_value(observation)
        if isinstance(decoded, Mapping):
            windows = decoded.get("sourceWindows")
            if isinstance(windows, list):
                for window in windows:
                    if isinstance(window, Mapping):
                        path = window.get("path")
                        if isinstance(path, str) and path.strip():
                            paths.add(path.strip())
            unit = decoded.get("unit")
            if isinstance(unit, Mapping) and any(
                unit.get(key) is not None
                for key in ("content", "source", "sourceText")
            ):
                path = unit.get("path")
                if isinstance(path, str) and path.strip():
                    paths.add(path.strip())
    return sorted(paths)


def _required_sequence_satisfied(
    invocation_items: Sequence[Mapping[str, Any]],
    required_tool_sequence: Sequence[str],
) -> bool:
    """Match required tools in order while allowing completed repeats."""
    next_index = 0
    for item in invocation_items:
        if next_index >= len(required_tool_sequence):
            return True
        name = item.get("name")
        if name == required_tool_sequence[next_index]:
            if item.get("status") != "completed":
                return False
            next_index += 1
            continue
        if name in required_tool_sequence[:next_index]:
            if item.get("status") != "completed":
                return False
            continue
        return False
    return next_index == len(required_tool_sequence)


@dataclass
class Stage1AgentTelemetryRecorder:
    """Collect facts already observable at the Stage 1 execution boundary."""

    batch_number: int
    batch_paths: tuple[str, ...]
    review_unit_ids: tuple[str, ...] = ()
    agent_requested: bool = False
    source_revision: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    agent_used: bool = False
    initial_required_tool: str | None = None
    required_tool_sequence: tuple[str, ...] = ()
    tool_sequence: list[dict[str, Any]] = field(default_factory=list)
    returned_evidence_ids: set[str] = field(default_factory=set)
    cited_evidence_ids: set[str] = field(default_factory=set)
    source_read_paths: set[str] = field(default_factory=set)
    agent_duration_ms: float = 0.0
    degraded: bool = False
    fallback_used: bool = False
    fallback_structural_context_loaded: bool = False
    partial_failure: str | None = None
    generation_preparation: dict[str, Any] = field(default_factory=lambda: {
        "eligible": False,
        "status": "not_eligible",
        "receiptPresent": False,
        "error": None,
    })
    relation_briefing: dict[str, Any] = field(default_factory=lambda: {
        "eligible": False,
        "attempted": False,
        "status": "not_eligible",
        "responseBytes": 0,
        "promptChars": 0,
        "backendResultCounts": {},
        "promptResultCounts": {},
        "visibleEvidenceIds": [],
        "sourceWindows": 0,
        "sourceCharacters": 0,
    })
    status: str = "running"
    _invocation: int = 0

    def record_relation_briefing(
        self,
        *,
        eligible: bool,
        attempted: bool,
        response: Any,
        visible_response: Any = None,
        prompt_context: str,
        visible_evidence_ids: Sequence[str],
    ) -> None:
        """Record the graph facts made visible before the first model turn."""
        decoded = _json_value(response)
        declared_status = (
            str(decoded.get("status") or "").strip().casefold()
            if isinstance(decoded, Mapping)
            else ""
        )
        coverage = (
            decoded.get("coverage")
            if isinstance(decoded, Mapping)
            else None
        )
        coverage_state = (
            str(
                coverage.get("graphState")
                or coverage.get("state")
                or ""
            ).strip().casefold()
            if isinstance(coverage, Mapping)
            else ""
        )
        if coverage_state == "complete_for_query":
            coverage_state = "complete"
        if not eligible:
            status = "not_eligible"
        elif not attempted:
            status = "not_attempted"
        elif declared_status in {"error", "failed", "unavailable", "disabled"}:
            status = "unavailable"
        elif response is None:
            status = "unavailable"
        elif prompt_context:
            status = coverage_state or declared_status or "ready"
        else:
            status = "empty"
        visible_ids = sorted({
            evidence_id
            for evidence_id in visible_evidence_ids
            if isinstance(evidence_id, str)
            and _EVIDENCE_ID.fullmatch(evidence_id)
        })
        self.returned_evidence_ids.update(visible_ids)
        backend_counts = _result_counts(decoded)
        visible_decoded = _json_value(visible_response)
        prompt_counts = _result_counts(visible_decoded)
        visible_windows = (
            visible_decoded.get("sourceWindows")
            if isinstance(visible_decoded, Mapping)
            else None
        )
        source_characters = sum(
            len(str(window.get("content") or ""))
            for window in visible_windows or ()
            if isinstance(window, Mapping)
        )
        self.relation_briefing = {
            "eligible": eligible,
            "attempted": attempted,
            "status": status,
            "responseBytes": _json_bytes(response) if attempted else 0,
            "promptChars": len(prompt_context),
            "backendResultCounts": backend_counts,
            "promptResultCounts": prompt_counts,
            "visibleEvidenceIds": visible_ids,
            "sourceWindows": int(
                prompt_counts.get("sourceWindows", 0) or 0
            ),
            "sourceCharacters": source_characters,
            "depthReached": (
                visible_decoded.get("coverage", {}).get("depthReached", 0)
                if isinstance(visible_decoded, Mapping)
                and isinstance(visible_decoded.get("coverage"), Mapping)
                else 0
            ),
        }

    def begin_agent(
        self,
        initial_required_tool: str | None,
        required_tool_sequence: Sequence[str] = (),
    ) -> float:
        self.agent_used = True
        self.initial_required_tool = (
            self.initial_required_tool or initial_required_tool
        )
        if not self.required_tool_sequence and required_tool_sequence:
            self.required_tool_sequence = tuple(required_tool_sequence)
        self._invocation += 1
        return time.monotonic()

    def record_agent_events(
        self,
        events: Sequence[Any],
        *,
        started_at: float,
        failed: bool = False,
        error: BaseException | None = None,
    ) -> tuple[str, ...]:
        self.agent_duration_ms += (time.monotonic() - started_at) * 1000
        observation_failures: list[str] = []
        for event in events:
            action = getattr(event, "action", None)
            observation = getattr(event, "observation", None)
            name = _tool_name(action)
            observation_error = _tool_observation_error(observation)
            evidence_ids = _evidence_ids(observation)
            self.returned_evidence_ids.update(evidence_ids)
            read_paths = _read_paths(name, action, observation)
            self.source_read_paths.update(read_paths)
            summary: dict[str, Any] = {
                "sequence": len(self.tool_sequence) + 1,
                "invocation": self._invocation,
                "name": name,
                "status": (
                    "failed" if observation_error is not None else "completed"
                ),
                "observationBytes": _json_bytes(observation),
                "resultCounts": _result_counts(observation),
                "evidenceIds": evidence_ids,
            }
            source_read = _source_read_request_summary(name, action)
            if source_read is not None:
                summary["sourceRead"] = source_read
            if observation_error is not None:
                failure_status, detail, status_code = observation_error
                summary["error"] = detail
                summary["failureStatus"] = failure_status
                decoded_observation = _json_value(observation)
                error_code = (
                    decoded_observation.get("errorCode")
                    if isinstance(decoded_observation, Mapping)
                    else None
                )
                if isinstance(error_code, str) and error_code.strip():
                    error_code = error_code.strip()
                    summary["errorCode"] = error_code
                if status_code is not None:
                    summary["statusCode"] = status_code
                status_suffix = (
                    f" (status {status_code})"
                    if status_code is not None
                    else ""
                )
                non_degrading_rejection = (
                    name,
                    error_code,
                ) in _NON_DEGRADING_TOOL_REJECTIONS
                if non_degrading_rejection:
                    summary["handledRejection"] = True
                else:
                    observation_failures.append(
                        f"{name} returned status:{failure_status}"
                        f"{status_suffix}: {detail}"
                    )
            if read_paths:
                summary["sourcePaths"] = read_paths
            snapshot = _snapshot(observation)
            if snapshot is not None:
                summary["snapshot"] = snapshot
                observed_revision = (
                    snapshot.get("sourceRevision")
                    or snapshot.get("revision")
                )
                if self.source_revision and observed_revision:
                    summary["sourceRevisionMatchesRequest"] = (
                        observed_revision == self.source_revision
                    )
            self.tool_sequence.append(summary)
        if failed:
            self.degraded = True
            self.partial_failure = (
                f"{type(error).__name__}: {error}"
                if error is not None
                else "agent execution failed"
            )
        elif observation_failures:
            self.degraded = True
            self.partial_failure = "; ".join(observation_failures)
        return tuple(observation_failures)

    def record_issues(self, issues: Sequence[Any]) -> None:
        for issue in issues:
            references = getattr(issue, "evidenceRefs", ()) or ()
            self.cited_evidence_ids.update(
                reference
                for reference in references
                if isinstance(reference, str) and _EVIDENCE_ID.fullmatch(reference)
            )

    def mark_agent_failure(self, error: BaseException) -> None:
        self.degraded = True
        self.partial_failure = f"{type(error).__name__}: {error}"

    def begin_repository_agent_recovery(self) -> None:
        """Replace a recoverable primary failure with the bounded agent retry.

        The next ``begin_agent`` call retains a distinct invocation number. If
        that recovery fails or any of its tools return an error, the normal
        recording methods mark the batch degraded again.
        """
        preparation_failed = (
            self.generation_preparation.get("eligible") is True
            and self.generation_preparation.get("status") != "ready"
        )
        self.degraded = preparation_failed
        self.partial_failure = (
            "proposed-tree generation unavailable: "
            + str(
                self.generation_preparation.get("error")
                or self.generation_preparation.get("status")
            )
            if preparation_failed
            else None
        )

    def record_fallback(self, *, structural_context_loaded: bool) -> None:
        self.fallback_used = True
        self.fallback_structural_context_loaded = structural_context_loaded

    def finish(self, *, status: str, error: BaseException | None = None) -> None:
        self.status = status
        if error is not None and self.partial_failure is None:
            self.partial_failure = f"{type(error).__name__}: {error}"

    def payload(self) -> dict[str, Any]:
        if self.tool_sequence:
            first_action = {
                "kind": "tool",
                "name": self.tool_sequence[0]["name"],
            }
        elif self.status == "completed" and self.agent_used:
            first_action = {"kind": "final_response_without_tool"}
        elif self.agent_used:
            first_action = {"kind": "failed_before_observable_tool"}
        else:
            first_action = {"kind": "direct_review"}
        returned = sorted(self.returned_evidence_ids)
        cited = sorted(self.cited_evidence_ids)
        required_sequence_satisfied = (
            not self.required_tool_sequence
            or any(
                _required_sequence_satisfied(
                    invocation_items,
                    self.required_tool_sequence,
                )
                for invocation in {
                    item.get("invocation") for item in self.tool_sequence
                }
                for invocation_items in [[
                    item
                    for item in self.tool_sequence
                    if item.get("invocation") == invocation
                ]]
                if len(invocation_items) >= len(self.required_tool_sequence)
            )
        )
        return {
            "batchNumber": self.batch_number,
            "batchPaths": list(self.batch_paths),
            "reviewUnitIds": list(self.review_unit_ids),
            "agentRequested": self.agent_requested,
            "agentUsed": self.agent_used,
            "initialRequiredTool": self.initial_required_tool,
            "requiredToolSequence": list(self.required_tool_sequence),
            "requiredToolSequenceSatisfied": required_sequence_satisfied,
            "status": self.status,
            "firstAction": first_action,
            "generationPreparation": dict(self.generation_preparation),
            "relationBriefing": dict(self.relation_briefing),
            "toolSequence": self.tool_sequence,
            "sourceReadCount": len(self.source_read_paths),
            "sourceReadPaths": sorted(self.source_read_paths),
            "sourceReadSummary": _aggregate_source_read_requests(
                self.tool_sequence
            ),
            "returnedEvidenceIds": returned,
            "citedEvidenceIds": cited,
            "uncitedReturnedEvidenceIds": sorted(set(returned).difference(cited)),
            "agentDurationMs": round(self.agent_duration_ms, 3),
            "batchDurationMs": round(
                (time.monotonic() - self.started_at) * 1000,
                3,
            ),
            "degraded": self.degraded,
            "fallback": {
                "used": self.fallback_used,
                "structuralContextLoaded": (
                    self.fallback_structural_context_loaded
                ),
            },
            "partialFailure": self.partial_failure,
        }

    def emit(self, callback: Optional[Callable[[dict[str, Any]], None]]) -> None:
        if callback is None:
            return
        event = {
            "type": "debug",
            "state": "stage_1_agent_telemetry",
            "message": "Stage 1 repository-agent batch completed",
            "agentTelemetry": self.payload(),
        }
        try:
            callback(event)
        except Exception:
            logger.warning(
                "Stage 1 agent telemetry callback failed",
                exc_info=True,
            )

    def record_generation_preparation(
        self,
        *,
        status: str | None,
        collection_target: str | None,
        generation_manifest_sha256: str | None,
        error: str | None,
    ) -> None:
        """Record the review-scoped graph preparation shared by this batch."""

        normalized_status = str(status or "not_eligible").strip().casefold()
        eligible = normalized_status != "not_eligible"
        receipt_present = bool(
            isinstance(collection_target, str)
            and collection_target.strip()
            and isinstance(generation_manifest_sha256, str)
            and len(generation_manifest_sha256) == 64
        )
        self.generation_preparation = {
            "eligible": eligible,
            "status": normalized_status,
            "receiptPresent": receipt_present,
            "error": str(error).strip() if error else None,
        }
        if eligible and normalized_status != "ready":
            self.degraded = True
            self.partial_failure = (
                "proposed-tree generation unavailable: "
                + (str(error).strip() if error else normalized_status)
            )
