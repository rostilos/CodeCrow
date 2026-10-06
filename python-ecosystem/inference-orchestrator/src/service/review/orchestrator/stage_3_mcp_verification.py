"""Optional MCP evidence verification for Stage 3 review aggregation.

The aggregation coordinator supplies prompt-budget and report-invocation
operations through :class:`Stage3McpRuntime`.  Keeping those operations behind
this narrow adapter lets the verification loop remain independent from Stage
3 prompt packing and final-report composition.
"""
from service.review.execution_scheduler import review_model_slot, optional_work_budget, optional_timeout_seconds
from service.review.inference_usage import record_model_response, record_compute_decision

import json
import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Protocol

from llm.reasoning_policy import ReasoningEffort, reasoning_request_kwargs
from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from service.agent import AgentExecutionService
from service.review.orchestrator.mcp_tool_executor import McpToolExecutor
from utils.llm_response import extract_llm_response_text
from utils.path_identity import repository_source_location
from service.review.orchestrator.verification_agent import VerificationAssessment, _validated_assessment, _decoded_verification_source_view


logger = logging.getLogger(__name__)


STAGE3_AGENT_MAX_OUTPUT_TOKENS = 16_384


_TERMINAL_FINALIZATION_INSTRUCTION = (
    "Repository verification is complete and tools are now disabled. Produce "
    "the final executive-summary report from the original review material and "
    "the complete tool results above. Do not request another tool call. "
    "Return one JSON object with report (the executive summary) and assessments "
    "(one assessment for every assigned Verification ID). Each assessment has "
    "issue_id, verdict (supported/disproven/unknown), reason, supporting_evidence, "
    "contradicting_evidence and unresolved_questions. Cite exact observed source "
    "with file_path and snippet; use unknown when evidence is insufficient. "
    "Do not infer verification success from completed discovery or agreement."
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
        and str(args.get("verificationId") or "") == verification_id
        and location_file_path(str(args.get("filePath") or "")) == file_path
        and revision_is_bound
    ):
        return False
    if entry.get("evidence_complete_file") is True:
        return True
    if line <= 0:
        return False
    try:
        start_line = int(entry.get("evidence_start_line") or 0)
        end_line = int(entry.get("evidence_end_line") or 0)
    except (TypeError, ValueError):
        return False
    return start_line > 0 and start_line <= line <= end_line


def validated_mcp_dismissals(
    requested_ids: List[str],
    issue_by_verification_id: Dict[str, CodeReviewIssue],
    executor: McpToolExecutor,
    review_revision: str,
    assessments: Dict[str, dict] | None = None,
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
        missing_locations = {
            location
            for location in required_locations
            if not any(
                mcp_read_covers_location(
                    entry,
                    verification_id,
                    location[0],
                    location[1],
                    review_revision,
                )
                for entry in executor.call_log
            )
        }
        if not required_locations or missing_locations:
            logger.warning(
                "[Stage 3] Keeping %s: dismissal lacks successful reviewed-revision "
                "evidence for %s",
                verification_id,
                sorted(
                    f"{path}:{line}" if line > 0 else path
                    for path, line in missing_locations
                ),
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
        record_compute_decision("final_verification", verificationId=key, **assessments[key])
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


async def _verification_report_fallback(llm, prompt, runtime, state, issues, reason, fallback_llm=None,
                                        reasoning_effort=ReasoningEffort.LOW):
    """Preserve complete acquired observations when optional verification stops."""
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
    if issues:
        prompt += ("\nOptional verification ended: " + reason + ". Produce the executive summary only. "
                   "Findings remain unverified; do not claim verified confidence or dismiss any candidate.")
    try:
        result = await runtime.invoke_report(llm, prompt, fallback_llm=fallback_llm,
                                             allow_retry=False, reasoning_effort=reasoning_effort)
    except Exception as exc:
        logger.warning("Optional verification report fallback failed: %s", exc)
        record_compute_decision("final_verification_report", outcome=type(exc).__name__)
        result = {"report": state.get("report") or "Optional verification was unavailable; review findings have been retained."}
    # A fallback report cannot authorize removing findings, regardless of prose.
    result.update(dismissed_issue_ids=[], dismissed_issue_keys=[], dismissed_issue_object_ids=[])
    result["verification_assessments"] = _retained_assessment_receipts(issues, state, reason)
    return result


async def execute_stage_3_mcp_verification(
    llm,
    request: ReviewRequestDto,
    prompt: str,
    mcp_client,
    review_revision: str,
    issue_by_verification_id: Dict[str, CodeReviewIssue],
    runtime: Stage3McpRuntime,
    fallback_llm=None,
) -> Dict[str, Any]:
    """Bound optional verification, leaving time to report retained candidates."""
    seconds = optional_timeout_seconds("REVIEW_FINAL_VERIFICATION_TIMEOUT_SECONDS", 60)
    budget = optional_work_budget(seconds)
    state = {"records": [], "token_target": runtime.input_token_target(request), "attempted": False,
             "acquisition_deadline": asyncio.get_running_loop().time() + max(0, budget - min(20, budget / 2))}
    try:
        async with asyncio.timeout(budget):
            return await _execute_stage_3_mcp_verification(llm, request, prompt, mcp_client, review_revision,
                issue_by_verification_id, runtime, fallback_llm, state)
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
    state=None,
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

    messages = [{"role": "user", "content": prompt}]
    state = state if state is not None else {"records": [], "token_target": token_target, "attempted": False,
                                          "acquisition_deadline": float("inf")}
    verification_records: List[Dict[str, Any]] = state["records"]

    for iteration in range(max_iterations):
        try:
            use_mcp_tools = iteration == 0
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
                        max_output_tokens=STAGE3_AGENT_MAX_OUTPUT_TOKENS,
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
                report, dismissed = extract_dismissed_issues(content)
                report, assessments = extract_verification_assessments(report, executor, issue_by_verification_id)
                state["report"] = report
                completed = state.setdefault("assessments", {})
                completed.update({key: value for key, value in assessments.items() if value["completed"]})
                assessments.update(completed)
                if iteration == 0 and any(not assessment["completed"] for assessment in assessments.values()):
                    # Summary-only prose is not candidate verification. Reserve
                    # the second call for explicit, tools-disabled assessments.
                    continue
                logger.info(
                    "[MCP Stage 3] Completed in %d iterations, %d "
                    "verification calls",
                    iteration + 1,
                    executor.call_count,
                )
                dismissed = sorted(set(dismissed) | {key for key, value in assessments.items() if value["verdict"] == "disproven"})
                validated = validated_mcp_dismissals(
                    dismissed,
                    issue_by_verification_id,
                    executor,
                    review_revision,
                    assessments,
                )
                return {
                    "report": report,
                    "verification_assessments": [
                        {"candidateObjectId": id(issue_by_verification_id[key]), **assessment}
                        for key, assessment in assessments.items()
                    ],
                    "dismissed_issue_ids": [
                        str(safe_issue_field(issue_by_verification_id[key], "id") or "")
                        for key in validated
                        if str(safe_issue_field(issue_by_verification_id[key], "id") or "")
                    ],
                    "dismissed_issue_keys": validated,
                    "dismissed_issue_object_ids": [
                        id(issue_by_verification_id[key]) for key in validated
                    ],
                }

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
