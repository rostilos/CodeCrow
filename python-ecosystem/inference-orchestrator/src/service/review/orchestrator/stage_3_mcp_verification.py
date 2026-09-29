"""Optional MCP evidence verification for Stage 3 review aggregation.

The aggregation coordinator supplies prompt-budget and report-invocation
operations through :class:`Stage3McpRuntime`.  Keeping those operations behind
this narrow adapter lets the verification loop remain independent from Stage
3 prompt packing and final-report composition.
"""

import json
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


logger = logging.getLogger(__name__)


STAGE3_AGENT_MAX_OUTPUT_TOKENS = 16_384


_TERMINAL_FINALIZATION_INSTRUCTION = (
    "Repository verification is complete and tools are now disabled. Produce "
    "the final executive-summary report from the original review material and "
    "the complete tool results above. Do not request another tool call. Apply "
    "the prompt's DISMISSED_ISSUES format only for false positives established "
    "by those successful repository reads."
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
    normalized = str(location or "").strip().replace("\\", "/").lstrip("/")
    if not normalized:
        return ""
    path, separator, possible_line = normalized.rpartition(":")
    if separator and possible_line.isdigit():
        return path
    return normalized


def _location_line(location: str) -> int:
    normalized = str(location or "").strip().replace("\\", "/")
    _, separator, possible_line = normalized.rpartition(":")
    if separator and possible_line.isdigit():
        return int(possible_line)
    return 0


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
    locations.update(
        (location_file_path(location), _location_line(location))
        for location in normalized_related_locations(issue)
    )
    return {(path, line) for path, line in locations if path}


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
) -> List[str]:
    """Accept dismissals only when every affected anchor has bound evidence."""
    validated: List[str] = []
    for verification_id in requested_ids:
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
    verification_records: List[Dict[str, Any]] = []

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
                    return await runtime.invoke_report(
                        llm,
                        prompt,
                        fallback_llm=fallback_llm,
                        allow_retry=(iteration == 0),
                    )
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
                    )
                response = await model_session.ainvoke(messages)
            else:
                # Binding an empty tool list still serializes a provider tool
                # parameter in some adapters. A direct call guarantees that
                # this terminal response has no callable repository tools.
                response = await llm.ainvoke(
                    messages,
                    **reasoning_request_kwargs(llm, ReasoningEffort.LOW),
                )
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
                    return await runtime.invoke_report(
                        fallback_llm,
                        prompt,
                        fallback_llm=None,
                        allow_retry=False,
                        reasoning_effort=ReasoningEffort.NONE,
                    )
                if runtime.response_finished_by_length(response):
                    return {
                        "report": extract_llm_response_text(response),
                        "dismissed_issue_ids": [],
                        "dismissed_issue_keys": [],
                        "dismissed_issue_object_ids": [],
                    }
                content = extract_llm_response_text(response)
                logger.info(
                    "[MCP Stage 3] Completed in %d iterations, %d "
                    "verification calls",
                    iteration + 1,
                    executor.call_count,
                )
                report, dismissed = extract_dismissed_issues(content)
                validated = validated_mcp_dismissals(
                    dismissed,
                    issue_by_verification_id,
                    executor,
                    review_revision,
                )
                return {
                    "report": report,
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
            for tool_call in tool_calls:
                tool_result = await executor.execute_tool(
                    tool_call["name"],
                    tool_call["args"],
                )
                tool_result_record = {
                    "toolCallId": tool_call["id"],
                    "name": tool_call["name"],
                    "arguments": tool_call["args"],
                    "result": str(tool_result),
                }
                iteration_record["toolResults"].append(tool_result_record)
                messages.append({
                    "role": "tool",
                    "content": tool_result_record["result"],
                    "tool_call_id": tool_call["id"],
                })
            verification_records.append(iteration_record)

        except Exception as exception:
            logger.info(
                "[MCP Stage 3] Iteration %d failed: %s",
                iteration + 1,
                exception,
            )
            break

    logger.warning(
        "[MCP Stage 3] Bounded verification loop ended after at most %d "
        "provider calls; retaining issues when no evidence-backed final "
        "dismissal was produced",
        max_iterations,
    )
    return {
        # A tool-call turn is not a report and may itself contain an
        # untrusted dismissal marker. Never surface it as final output when
        # the dedicated terminal call failed or violated the no-tools turn.
        "report": (
            "Optional MCP verification finalization was unavailable; "
            "no issue was dismissed."
        ),
        "dismissed_issue_ids": [],
        "dismissed_issue_keys": [],
        "dismissed_issue_object_ids": [],
    }
