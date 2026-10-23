"""Optional MCP evidence verification for Stage 3 review aggregation.

The aggregation coordinator supplies prompt-budget and report-invocation
operations through :class:`Stage3McpRuntime`.  Keeping those operations behind
this narrow adapter lets the verification loop remain independent from Stage
3 prompt packing and final-report composition.
"""

from llm.provider_lifecycle import provider_execution_scope
from service.review.execution_scheduler import review_model_slot, optional_work_budget, optional_timeout_seconds
from service.review.inference_usage import record_model_response, record_compute_decision

import json
import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Protocol

from llm.reasoning_policy import ReasoningEffort, reasoning_request_kwargs, review_output_token_limit
from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from service.agent import AgentExecutionService
from service.review.orchestrator.mcp_tool_executor import McpToolExecutor
from utils.llm_response import extract_llm_response_text
from utils.path_identity import repository_source_location
from utils.diff_processor import DiffChangeType
from service.review.orchestrator.verification_agent import (
    PREDICATE_VERIFICATION_INSTRUCTION, VerificationAssessment,
    _validated_assessment, _decoded_verification_source_view,
    _current_hunk_source, _reviewable_hunks,
)


logger = logging.getLogger(__name__)


STAGE3_AGENT_MAX_OUTPUT_TOKENS = 16_384


_TERMINAL_FINALIZATION_INSTRUCTION = (
    "Repository verification is complete and tools are now disabled. Produce "
    "the final executive-summary report from the original review material and "
    "the complete tool results and admitted Request HEAD source blocks above. Do not request another tool call. "
    "Return one JSON object with report (the executive summary) and assessments "
    "(one assessment for every assigned Verification ID). Each assessment has "
    "issue_id, verdict (supported/disproven/unknown), reason, supporting_evidence, "
    "contradicting_evidence and unresolved_questions. Cite exact observed source "
    "with file_path and snippet; use unknown when evidence is insufficient. "
    "Do not infer verification success from completed discovery or agreement. "
    + PREDICATE_VERIFICATION_INSTRUCTION
)


class Stage3MessageTokenEstimator(Protocol):
    def __call__(
        self,
        messages: List[Any],
        *,
        use_mcp_tools: bool,
    ) -> int: ...


class Stage3ReportInvoker(Protocol):
    def __call__(
        self,
        llm: Any,
        prompt: str,
        fallback_llm: Any = None,
        allow_retry: bool = True,
        reasoning_effort: ReasoningEffort = ReasoningEffort.LOW,
    ) -> Awaitable[Dict[str, Any]]: ...


@dataclass(frozen=True)
class Stage3McpRuntime:
    """Coordinator operations needed by the optional verification loop."""

    input_token_target: Callable[[ReviewRequestDto], int]
    estimate_messages_tokens: Stage3MessageTokenEstimator
    continuation_messages: Callable[[str, List[Dict[str, Any]]], List[Any]]
    invoke_report: Stage3ReportInvoker
    response_finished_by_length: Callable[[Any], bool]


def safe_issue_field(issue: CodeReviewIssue, name: str) -> Any:
    value = getattr(issue, name, "")
    if value is None:
        return ""
    if value.__class__.__module__.startswith("unittest.mock"):
        return ""
    return value


def verification_issue_map(
    issues: List[CodeReviewIssue],
) -> Dict[str, CodeReviewIssue]:
    active = [
        issue
        for issue in issues
        if getattr(issue, "isResolved", False) is not True
    ]
    return {
        f"issue_{index}": issue
        for index, issue in enumerate(active)
    }


_RELATED_LOCATIONS_RE = re.compile(
    r"(?im)^\s*(?:[*_]{1,2})?also affects\s*:(?:[*_]{1,2})?\s*(.+)$"
)


def issue_reason_brief(issue: CodeReviewIssue) -> str:
    """Remove exact repetition while preserving every substantive paragraph."""
    reason = str(safe_issue_field(issue, "reason") or "").strip()
    if not reason:
        return ""
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", reason)
        if paragraph.strip()
    ]
    title = str(safe_issue_field(issue, "title") or "").strip().casefold()
    selected: List[str] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        normalized = " ".join(paragraph.strip("*_# ").casefold().split())
        if normalized == title:
            continue
        if normalized in seen:
            continue
        selected.append(paragraph)
        seen.add(normalized)
    return "\n\n".join(selected) if selected else reason


def normalized_related_locations(issue: CodeReviewIssue) -> List[str]:
    values = safe_issue_field(issue, "relatedLocations") or []
    locations = list(values) if isinstance(values, (list, tuple, set)) else []
    reason = str(safe_issue_field(issue, "reason") or "")
    for match in _RELATED_LOCATIONS_RE.finditer(reason):
        locations.extend(match.group(1).split(","))
    return sorted({
        str(value).strip()
        for value in locations
        if str(value).strip()
    })


def verification_record(
    verification_id: str,
    issue: CodeReviewIssue,
) -> Dict[str, Any]:
    if isinstance(issue, CodeReviewIssue):
        payload = issue.model_dump(mode="json")
    else:
        # A small number of callers/tests use issue-like objects. Preserve the
        # same canonical contract instead of silently narrowing those records.
        payload = {
            field_name: safe_issue_field(issue, field_name)
            for field_name in CodeReviewIssue.model_fields
        }
    payload["reason"] = issue_reason_brief(issue)
    payload["relatedLocations"] = normalized_related_locations(issue)
    return {
        "verification_id": verification_id,
        "original_id": str(payload.get("id") or ""),
        **payload,
        # Stable verification aliases consumed by the MCP path. They augment,
        # rather than replace, the complete CodeReviewIssue payload above.
        "exact_source_anchor": str(payload.get("codeSnippet") or ""),
        "related_locations": list(payload.get("relatedLocations") or []),
    }


def extract_dismissed_issues(content: str) -> tuple[str, List[str]]:
    pattern = r'<!--\s*DISMISSED_ISSUES:\s*(\[.*?\])\s*-->'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return content, []

    try:
        dismissed = json.loads(match.group(1))
        if not isinstance(dismissed, list):
            logger.warning(
                "[Stage 3] DISMISSED_ISSUES was not a list: %s",
                match.group(1),
            )
            return content, []
        dismissed = [str(dismissed_id) for dismissed_id in dismissed if dismissed_id]
        logger.info(
            "[Stage 3] MCP verification requested dismissal of %d issues: %s",
            len(dismissed),
            dismissed,
        )
        clean_report = content[:match.start()].rstrip() + content[match.end():]
        return clean_report.strip(), dismissed
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("[Stage 3] Failed to parse DISMISSED_ISSUES: %s", exc)
        return content, []


def location_file_path(location: str) -> str:
    return repository_source_location(location)[0]


def _location_line(location: str) -> int:
    return repository_source_location(location)[1]


def required_verification_locations(
    issue: CodeReviewIssue,
) -> set[tuple[str, int]]:
    primary_path = location_file_path(
        str(safe_issue_field(issue, "file") or "")
    )
    try:
        primary_line = int(safe_issue_field(issue, "line") or 0)
    except (TypeError, ValueError):
        primary_line = 0
    locations = {(primary_path, max(0, primary_line))}
    for location in normalized_related_locations(issue):
        path, start, end = repository_source_location(location)
        locations.update({(path, start), (path, end)})
    anchored_paths = {path for path, line in locations if line > 0}
    return {(path, line) for path, line in locations
            if path and (line > 0 or path not in anchored_paths)}


def mcp_read_covers_location(
    entry: Dict[str, Any],
    verification_id: str,
    file_path: str,
    line: int,
    review_revision: str,
    *,
    allow_related_excerpt: bool = False,
) -> bool:
    args = entry.get("args", {})
    tool_name = entry.get("tool")
    revision_is_bound = (
        str(args.get("branch") or "") == review_revision
        if tool_name == "getBranchFileContent"
        else (
            tool_name == "getReviewFileContent"
            and entry.get("evidence_source_authority") == "proposed_tree"
            and str(entry.get("evidence_revision") or "") == review_revision
        )
    )
    if not (
        tool_name in {"getBranchFileContent", "getReviewFileContent"}
        and entry.get("success") is True
        and entry.get("evidence_valid") is True
        and verification_id in entry.get("verification_ids", [str(args.get("verificationId") or "")])
        and location_file_path(str(args.get("filePath") or "")) == file_path
        and revision_is_bound
    ):
        return False
    if entry.get("evidence_complete_file") is True:
        return True
    try:
        start_line = int(entry.get("evidence_start_line") or 0)
        end_line = int(entry.get("evidence_end_line") or 0)
    except (TypeError, ValueError):
        return False
    if line <= 0:
        return allow_related_excerpt and 0 < start_line <= end_line
    return start_line > 0 and start_line <= line <= end_line


def validated_mcp_dismissals(
    requested_ids: List[str],
    issue_by_verification_id: Dict[str, CodeReviewIssue],
    executor: McpToolExecutor,
    review_revision: str,
    assessments: Dict[str, dict] | None = None,
    admitted_head_ranges: List[tuple[str, str, int, int]] | None = None,
) -> List[str]:
    """Accept dismissals only when every affected anchor has bound evidence."""
    validated: List[str] = []
    for verification_id in requested_ids:
        if (assessments or {}).get(verification_id, {}).get("verdict") != "disproven":
            logger.info("[Stage 3] Keeping %s: no source-backed falsification assessment", verification_id)
            continue
        issue = issue_by_verification_id.get(verification_id)
        if issue is None:
            logger.warning(
                "[Stage 3] Ignoring dismissal for unknown verification ID %s",
                verification_id,
            )
            continue
        required_locations = required_verification_locations(issue)
        is_head = str(safe_issue_field(issue, "anchorSide")).upper() == "HEAD"
        primary_path = location_file_path(str(safe_issue_field(issue, "file") or ""))
        # A file-only related reference requires observed source, not whole-file coverage.
        head_covered = {
            (path, line) for path, line in required_locations
            if is_head and any(
                revision == review_revision and source_path == path and 0 < start <= end
                and (start <= line <= end or (line == 0 and path != primary_path))
                for revision, source_path, start, end in admitted_head_ranges or []
            )
        }
        missing_locations = {
            location
            for location in required_locations
            if location not in head_covered and not any(
                mcp_read_covers_location(
                    entry,
                    verification_id,
                    location[0],
                    location[1],
                    review_revision,
                    allow_related_excerpt=is_head and location[0] != primary_path,
                )
                for entry in executor.call_log
            )
        }
        if not required_locations or missing_locations:
            missing = sorted(f"{path}:{line}" if line > 0 else path
                             for path, line in missing_locations)
            logger.warning(
                "[Stage 3] Keeping %s: dismissal lacks successful reviewed-revision "
                "evidence for %s",
                verification_id,
                missing,
            )
            assessments[verification_id].update(
                verdict="unknown", reasonCode="dismissal_evidence_incomplete",
                reason=("Falsification lacks reviewed-revision evidence for required locations: "
                        + ", ".join(missing or ["no valid source anchor"]) + "; candidate retained"),
            )
            continue
        validated.append(verification_id)
    return validated


def extract_verification_assessments(content: str, executor: McpToolExecutor,
                                    issues: Dict[str, CodeReviewIssue]) -> tuple[str, Dict[str, dict]]:
    pattern = r'<!--\s*VERIFICATION_ASSESSMENTS:\s*(\[.*?\])\s*-->'
    match = re.search(pattern, content, re.DOTALL)
    assessments = {}
    items = []
    missing_reason = "assessment_missing"
    try:
        payload = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip()))
        if isinstance(payload, dict):
            items = payload.get("assessments", [])
            content = str(payload.get("report") or "")
    except (ValueError, TypeError):
        # Keep usable prose from a malformed response; older model responses
        # may place the same assessment records in an HTML comment.
        if match:
            try:
                items = json.loads(match.group(1))
            except (ValueError, TypeError):
                missing_reason = "assessment_malformed"
            content = re.sub(pattern, "", content, flags=re.DOTALL).strip()
    if not isinstance(items, list):
        items = []
        missing_reason = "assessment_malformed"
    for item in items:
        try:
            assessment = VerificationAssessment.model_validate(item)
            if assessment.issue_id not in issues:
                continue
            evidence = {path: text for (key, path), text in executor.verification_source.items()
                        if key == assessment.issue_id}
            checked = _validated_assessment(assessment, evidence)
            checked.update(stage="stage_3_verification", attempted=True, completed=True,
                           reasonCode="assessed" if checked["verdict"] == assessment.verdict else "citation_unavailable")
            assessments[assessment.issue_id] = checked
        except (ValueError, TypeError, AttributeError):
            logger.warning("[Stage 3] Optional verification assessment unavailable; retaining unverified issues")
            missing_reason = "assessment_malformed"
    for key in issues:
        assessments.setdefault(key, {"verdict": "unknown", "reason": "no valid final verification assessment",
            "reasonCode": missing_reason, "stage": "stage_3_verification", "attempted": True, "completed": False})
    return content, assessments


def unknown_verification_assessments(issues: Dict[str, CodeReviewIssue], reason: str,
                                    *, attempted: bool = False) -> List[dict]:
    return [{"candidateObjectId": id(issue), "stage": "stage_3_verification",
             "verdict": "unknown", "reason": reason, "reasonCode": reason,
             "attempted": attempted, "completed": False} for issue in issues.values()]


def _retained_assessment_receipts(issues, state, reason):
    completed = state.get("assessments", {})
    return [{**receipt, **completed.get(key, {})}
            for key, receipt in zip(issues, unknown_verification_assessments(
                issues, reason, attempted=state.get("attempted", False)))]


def _assessed_verification_result(content, executor, issues, review_revision, state):
    report, dismissed = extract_dismissed_issues(content)
    report, assessments = extract_verification_assessments(report, executor, issues)
    state["report"] = report
    completed = state.setdefault("assessments", {})
    completed.update({key: value for key, value in assessments.items() if value["completed"]})
    assessments.update(completed)
    dismissed = sorted(set(dismissed) | {key for key, value in assessments.items() if value["verdict"] == "disproven"})
    validated = validated_mcp_dismissals(
        dismissed, issues, executor, review_revision, assessments, state.get("admitted_head_ranges"))
    for key, assessment in assessments.items():
        record_compute_decision("final_verification", verificationId=key, **assessment)
    return {
        "report": report,
        "verification_assessments": [
            {"candidateObjectId": id(issues[key]), **assessment}
            for key, assessment in assessments.items()
        ],
        "dismissed_issue_ids": [
            str(safe_issue_field(issues[key], "id") or "") for key in validated
            if str(safe_issue_field(issues[key], "id") or "")
        ],
        "dismissed_issue_keys": validated,
        "dismissed_issue_object_ids": [id(issues[key]) for key in validated],
    }


async def _verification_report_fallback(llm, prompt, runtime, state, issues, reason, fallback_llm=None,
                                        reasoning_effort=ReasoningEffort.LOW):
    """Preserve complete acquired observations when optional verification stops."""
    if state.get("report_fallback_consumed", False):
        record_compute_decision("final_verification_report", outcome="fallback_already_consumed")
        return {
            "report": state.get("report") or "Optional verification was unavailable; review findings have been retained.",
            "dismissed_issue_ids": [], "dismissed_issue_keys": [], "dismissed_issue_object_ids": [],
            "verification_assessments": _retained_assessment_receipts(issues, state, reason),
        }
    prompt = state.get("source_augmented_prompt", prompt)
    records = state.get("records", [])
    admitted = []
    try:
        for record in records:
            for result in record["toolResults"]:
                candidate = [*admitted, {"iteration": record["iteration"], "assistantContent": "", "toolResults": [result]}]
                messages = runtime.continuation_messages(prompt, candidate)
                if runtime.estimate_messages_tokens(messages, use_mcp_tools=False) <= state["token_target"]:
                    admitted = candidate
                else:
                    record_compute_decision("final_verification_evidence", outcome="context_limit", tool=result["name"])
        if admitted:
            prompt = "\n\n".join(str(message.get("content", "")) for message in runtime.continuation_messages(prompt, admitted))
    except Exception as exc:
        logger.warning("Optional verification evidence could not be rendered: %s", exc)
        record_compute_decision("final_verification_evidence", outcome=type(exc).__name__)
        admitted = []
    # The timeout recovery already pays for one completion. Assess its complete
    # observed source rather than throwing it away in a summary-only response.
    assess_observations = (
        reason == "verification_timeout" and issues and state.get("executor") is not None
        and admitted and len(admitted) == sum(len(record["toolResults"]) for record in records)
        and any(state["executor"].verification_source.values())
    )
    if assess_observations:
        assess_observations = runtime.estimate_messages_tokens([
            {"role": "user", "content": prompt + "\n\n" + _TERMINAL_FINALIZATION_INSTRUCTION}
        ], use_mcp_tools=False) <= state["token_target"]
    if assess_observations:
        prompt += "\n\n" + _TERMINAL_FINALIZATION_INSTRUCTION
    elif issues:
        prompt += ("\nOptional verification ended: " + reason + ". Produce the executive summary only. "
                   "Findings remain unverified; do not claim verified confidence or dismiss any candidate.")
    try:
        # The outer optional timeout can cancel this call and re-enter recovery.
        # Its single fallback slot stays consumed even when cancellation wins.
        state["report_fallback_consumed"] = True
        result = await runtime.invoke_report(llm, prompt, fallback_llm=fallback_llm,
                                             allow_retry=False, reasoning_effort=reasoning_effort)
        if assess_observations:
            return _assessed_verification_result(result.get("report", ""), state["executor"],
                issues, state["executor"].review_revision, state)
    except Exception as exc:
        logger.warning("Optional verification report fallback failed: %s", exc)
        record_compute_decision("final_verification_report", outcome=type(exc).__name__)
        result = {"report": state.get("report") or "Optional verification was unavailable; review findings have been retained."}
    # A fallback report cannot authorize removing findings, regardless of prose.
    result.update(dismissed_issue_ids=[], dismissed_issue_keys=[], dismissed_issue_object_ids=[])
    result["verification_assessments"] = _retained_assessment_receipts(issues, state, reason)
    return result


def _admit_request_head_hunks(processed_diff, prompt, messages, executor, issues,
                             review_revision, runtime, state):
    """Reuse admitted request HEAD source for citations and anchor coverage."""
    if processed_diff is None or not review_revision or not issues:
        return prompt
    terminal = {"role": "user", "content": _TERMINAL_FINALIZATION_INSTRUCTION}
    for diff_file in processed_diff.get_included_files():
        if diff_file.change_type is DiffChangeType.DELETED:
            continue
        for hunk in _reviewable_hunks(diff_file):
            if hunk.evidence_kind != "changed_source" or hunk.new_start <= 0 or hunk.new_count <= 0:
                continue
            source = _current_hunk_source(hunk)
            source_line_count = sum(line.startswith(("+", " ")) for line in hunk.content.splitlines()[1:])
            if not source or source_line_count != hunk.new_count:
                record_compute_decision("final_verification_evidence", outcome="request_hunk_unavailable", hunkId=hunk.id)
                continue
            path = location_file_path(diff_file.path)
            if not path:
                continue
            heading = (f"\n\nRequest HEAD source: {path}:{hunk.new_start}-{hunk.new_start + hunk.new_count - 1}"
                       f"; revision={review_revision}; hunk={hunk.id}\n")
            candidate_prompt = prompt + heading + source
            candidate_messages = [{"role": "user", "content": candidate_prompt}, *messages[1:], terminal]
            continuation = [*runtime.continuation_messages(candidate_prompt, state["records"]), terminal]
            if (runtime.estimate_messages_tokens(candidate_messages, use_mcp_tools=True) > state["token_target"]
                    or runtime.estimate_messages_tokens(continuation, use_mcp_tools=False) > state["token_target"]):
                record_compute_decision("final_verification_evidence", outcome="request_hunk_context_limit", hunkId=hunk.id)
                continue
            prompt = candidate_prompt
            messages[0] = {"role": "user", "content": prompt}
            state["source_augmented_prompt"] = prompt
            state.setdefault("admitted_head_ranges", []).append(
                (review_revision, path, hunk.new_start, hunk.new_start + hunk.new_count - 1))
            for verification_id in issues:
                executor.verification_source.setdefault((verification_id, path), []).append(source)
            record_compute_decision("final_verification_evidence", outcome="request_hunk_observed",
                                    hunkId=hunk.id, filePath=path, revision=review_revision)
    return prompt


async def execute_stage_3_mcp_verification(
    llm,
    request: ReviewRequestDto,
    prompt: str,
    mcp_client,
    review_revision: str,
    issue_by_verification_id: Dict[str, CodeReviewIssue],
    runtime: Stage3McpRuntime,
    fallback_llm=None,
    processed_diff=None,
) -> Dict[str, Any]:
    """Bound optional verification, leaving time to report retained candidates."""
    async with provider_execution_scope(llm):
        seconds = optional_timeout_seconds("REVIEW_FINAL_VERIFICATION_TIMEOUT_SECONDS", 60)
        budget = optional_work_budget(seconds)
        state = {"records": [], "token_target": runtime.input_token_target(request), "attempted": False,
                 "acquisition_deadline": asyncio.get_running_loop().time() + max(0, budget - min(20, budget / 2))}
        try:
            async with asyncio.timeout(budget):
                return await _execute_stage_3_mcp_verification(llm, request, prompt, mcp_client, review_revision,
                    issue_by_verification_id, runtime, fallback_llm, state, processed_diff)
        except TimeoutError:
            record_compute_decision("final_verification", outcome="timeout", verdict="unknown")
            logger.warning("Optional final verification timed out; retaining candidates for report synthesis")
            return await _verification_report_fallback(llm, prompt, runtime, state,
                issue_by_verification_id, "verification_timeout", fallback_llm)
        except Exception as exc:
            logger.warning("Optional final verification failed; retaining candidates: %s", exc)
            record_compute_decision("final_verification", outcome=type(exc).__name__, verdict="unknown")
            return await _verification_report_fallback(llm, prompt, runtime, state,
                issue_by_verification_id, "verification_unavailable", fallback_llm)


async def _execute_stage_3_mcp_verification(
    llm, request: ReviewRequestDto, prompt: str, mcp_client, review_revision: str,
    issue_by_verification_id: Dict[str, CodeReviewIssue], runtime: Stage3McpRuntime, fallback_llm=None,
    state=None, processed_diff=None,
) -> Dict[str, Any]:
    """Run the bounded optional tool loop and return a report plus dismissals."""
    executor = McpToolExecutor(
        mcp_client,
        request,
        stage="stage_3",
        review_revision=review_revision,
        verification_issues=issue_by_verification_id,
    )
    tool_defs = executor.get_tool_definitions()
    agent_service = AgentExecutionService(llm=llm, client=mcp_client)
    model_session = None
    # Exactly one tool-selection turn plus one tools-disabled, evidence-aware
    # completion turn. The old 15-turn loop (and identical recursive replay)
    # multiplied every semantic child into a provider-call tree. Keeping the
    # second call unbound also prevents a final tool-call response from being
    # mistaken for a completed report.
    max_iterations = 2
    token_target = runtime.input_token_target(request)

    if issue_by_verification_id:
        prompt += "\n\n" + PREDICATE_VERIFICATION_INSTRUCTION
    messages = [{"role": "user", "content": prompt}]
    state = state if state is not None else {"records": [], "token_target": token_target, "attempted": False,
                                          "acquisition_deadline": float("inf")}
    state["executor"] = executor
    verification_records: List[Dict[str, Any]] = state["records"]

    # Prefetch candidate anchors within the existing call/time/context budgets. Otherwise one
    # tool-selection turn commonly reads only issue_0 and leaves the remaining
    # findings unknown. The executor binds overlapping windows to all covered
    # candidates, so nearby findings need only one repository read.
    anchor_record = {"iteration": 0, "assistantContent": "Host-acquired candidate anchors", "toolResults": []}
    for verification_id, issue in issue_by_verification_id.items():
        path = location_file_path(str(safe_issue_field(issue, "file") or ""))
        try:
            line = int(safe_issue_field(issue, "line") or 0)
        except (ValueError, TypeError):
            continue
        if (not path or line <= 0
                or str(safe_issue_field(issue, "anchorSide")).upper() == "BASE"):
            continue
        if any(mcp_read_covers_location(entry, verification_id, path, line, review_revision)
               for entry in executor.call_log):
            continue
        if executor.call_count >= executor.max_calls:
            record_compute_decision("final_verification_evidence", outcome="anchor_call_limit", verificationId=verification_id)
            break
        tool = "getReviewFileContent" if executor.local_only else "getBranchFileContent"
        arguments = {"verificationId": verification_id, "filePath": f"{path}:{line}"}
        source_lengths = {key: len(windows) for key, windows in executor.verification_source.items()}
        log_start = len(executor.call_log)
        try:
            async with asyncio.timeout(max(0, state["acquisition_deadline"] - asyncio.get_running_loop().time())):
                result = await executor.execute_tool(tool, arguments)
        except TimeoutError:
            record_compute_decision("final_verification_evidence", outcome="anchor_timeout", verificationId=verification_id)
            break
        rendered = str(result) + _decoded_verification_source_view(str(result))
        bound_ids = sorted({key for log in executor.call_log[log_start:]
                            for key in log.get("verification_ids", [])}) or [verification_id]
        entry = {"role": "user", "content": f"Observed source for {', '.join(bound_ids)}, {path}:{line}:\n{rendered}"}
        if runtime.estimate_messages_tokens([*messages, entry], use_mcp_tools=True) > token_target:
            # Source not shown to the model cannot authorize its verdict.
            for key, windows in executor.verification_source.items():
                del windows[source_lengths.get(key, 0):]
            for log in executor.call_log[log_start:]:
                log["evidence_valid"] = False
                log["evidence_not_admitted"] = True
            record_compute_decision("final_verification_evidence", outcome="anchor_context_limit", verificationId=verification_id)
            break
        messages.append(entry)
        valid = any(mcp_read_covers_location(log, verification_id, path, line, review_revision)
                    for log in executor.call_log[log_start:])
        record_compute_decision("final_verification_evidence", outcome="anchor_observed" if valid else "anchor_unavailable",
                                verificationId=verification_id, boundVerificationIds=bound_ids)
        anchor_record["toolResults"].append({"toolCallId": f"anchor_{len(anchor_record['toolResults'])}",
            "name": tool, "arguments": arguments, "result": rendered})
    if anchor_record["toolResults"]:
        verification_records.append(anchor_record)

    source_started = time.perf_counter()
    try:
        prompt = _admit_request_head_hunks(
            processed_diff, prompt, messages, executor, issue_by_verification_id,
            review_revision, runtime, state,
        )
    except Exception as exc:
        logger.warning("Optional request HEAD evidence unavailable; keeping acquired source: %s", exc)
        record_compute_decision("final_verification_evidence", outcome="request_hunk_unavailable", errorType=type(exc).__name__)
        prompt = state.get("source_augmented_prompt", prompt)
    finally:
        record_compute_decision("final_verification_evidence", outcome="request_hunk_preparation",
                                elapsedMs=round((time.perf_counter() - source_started) * 1000, 3))

    for iteration in range(max_iterations):
        try:
            # Prefetch may consume every repository call. Spend the remaining
            # time assessing acquired source instead of asking for unusable tools.
            use_mcp_tools = (
                iteration == 0
                and not executor.budget_exhausted
                and asyncio.get_running_loop().time() < state["acquisition_deadline"]
            )
            if not use_mcp_tools:
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": _TERMINAL_FINALIZATION_INSTRUCTION,
                    },
                ]
            estimated_tokens = runtime.estimate_messages_tokens(
                messages,
                use_mcp_tools=use_mcp_tools,
            )
            if estimated_tokens > token_target:
                continuation_messages = runtime.continuation_messages(
                    prompt,
                    verification_records,
                )
                if not use_mcp_tools:
                    continuation_messages = [
                        *continuation_messages,
                        {
                            "role": "user",
                            "content": _TERMINAL_FINALIZATION_INSTRUCTION,
                        },
                    ]
                continuation_tokens = runtime.estimate_messages_tokens(
                    continuation_messages,
                    use_mcp_tools=use_mcp_tools,
                )
                if continuation_tokens > token_target:
                    logger.warning(
                        "[MCP Stage 3] Optional verification stopped before "
                        "iteration %d: complete messages/tool results require "
                        "%d input tokens (continuation=%d, target=%d). "
                        "Falling back without clipping any record.",
                        iteration + 1,
                        estimated_tokens,
                        continuation_tokens,
                        token_target,
                    )
                    return await _verification_report_fallback(llm, prompt, runtime, state,
                        issue_by_verification_id, "verification_context_limit", fallback_llm)
                logger.info(
                    "[MCP Stage 3] Reframed %d complete verification "
                    "iteration records into a fresh continuation (%d -> %d "
                    "estimated input tokens, target=%d)",
                    len(verification_records),
                    estimated_tokens,
                    continuation_tokens,
                    token_target,
                )
                messages = continuation_messages

            if use_mcp_tools:
                if model_session is None:
                    model_session = agent_service.create_model_session(
                        tool_definitions=tool_defs,
                        reasoning_effort=ReasoningEffort.LOW,
                        max_output_tokens=review_output_token_limit(llm, STAGE3_AGENT_MAX_OUTPUT_TOKENS),
                        usage_stage="stage_3_verification",
                    )
                state["attempted"] = True
                async with asyncio.timeout(max(0, state["acquisition_deadline"] - asyncio.get_running_loop().time())):
                    response = await model_session.ainvoke(messages)
            else:
                # Binding an empty tool list still serializes a provider tool
                # parameter in some adapters. A direct call guarantees that
                # this terminal response has no callable repository tools.
                state["attempted"] = True
                async with review_model_slot("stage_3_mcp_verification"):
                    response = await llm.ainvoke(
                        messages,
                        **reasoning_request_kwargs(llm, ReasoningEffort.LOW),
                    )
                    record_model_response(response)
            messages.append(response)

            tool_calls = getattr(response, "tool_calls", None)
            if tool_calls and not use_mcp_tools:
                logger.warning(
                    "[MCP Stage 3] Terminal tools-disabled response contained "
                    "tool calls; retaining every issue"
                )
                break
            if not tool_calls:
                if (
                    runtime.response_finished_by_length(response)
                    and fallback_llm is not None
                    and iteration == 0
                ):
                    logger.info(
                        "MCP Stage 3 report exhausted its output; retrying once "
                        "as a reasoning-free direct output request"
                    )
                    return await _verification_report_fallback(
                        fallback_llm,
                        prompt,
                        runtime,
                        state,
                        issue_by_verification_id,
                        "verification_output_limit",
                        fallback_llm=None,
                        reasoning_effort=ReasoningEffort.NONE,
                    )
                if runtime.response_finished_by_length(response):
                    return await _verification_report_fallback(llm, prompt, runtime, state,
                        issue_by_verification_id, "verification_output_limit", fallback_llm)
                content = extract_llm_response_text(response)
                result = _assessed_verification_result(content, executor, issue_by_verification_id, review_revision, state)
                if iteration == 0 and any(not assessment["completed"] for assessment in result["verification_assessments"]):
                    # Summary-only prose is not candidate verification. Reserve
                    # the second call for explicit, tools-disabled assessments.
                    continue
                logger.info(
                    "[MCP Stage 3] Completed in %d iterations, %d "
                    "verification calls",
                    iteration + 1,
                    executor.call_count,
                )
                return result

            iteration_record: Dict[str, Any] = {
                "iteration": iteration + 1,
                "assistantContent": extract_llm_response_text(response),
                "toolResults": [],
            }
            verification_records.append(iteration_record)
            for tool_call in tool_calls:
                async with asyncio.timeout(max(0, state["acquisition_deadline"] - asyncio.get_running_loop().time())):
                    tool_result = await executor.execute_tool(tool_call["name"], tool_call["args"])
                tool_result_record = {
                    "toolCallId": tool_call["id"],
                    "name": tool_call["name"],
                    "arguments": tool_call["args"],
                    "result": str(tool_result),
                }
                iteration_record["toolResults"].append(tool_result_record)
                messages.append({
                    "role": "tool",
                    "content": tool_result_record["result"] + _decoded_verification_source_view(tool_result_record["result"]),
                    "tool_call_id": tool_call["id"],
                })
        except Exception as exception:
            logger.info(
                "[MCP Stage 3] Iteration %d failed: %s",
                iteration + 1,
                exception,
            )
            if iteration == 0:
                # Reframe completed observations: a partially executed tool
                # batch cannot be sent back as an incomplete native tool reply.
                messages = runtime.continuation_messages(prompt, verification_records)
                record_compute_decision("final_verification", outcome=type(exception).__name__, verdict="unknown")
                continue
            break

    logger.warning(
        "[MCP Stage 3] Bounded verification loop ended after at most %d "
        "provider calls; retaining issues when no evidence-backed final "
        "dismissal was produced",
        max_iterations,
    )
    return await _verification_report_fallback(llm, prompt, runtime, state,
        issue_by_verification_id, "verification_finalization_unavailable", fallback_llm)
