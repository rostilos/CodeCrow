"""Request-owned MCP conversations with incremental output and read reuse."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Callable

from llm.reasoning_policy import ReasoningEffort
from service.review.agent_calls import ReviewAgentSession, result_message
from service.review.async_work import gather_review_work
from service.review.verification_state import fingerprint


async def tool_results(tools: Any, calls: list[dict[str, Any]], allowed: set[str] | None = None):
    """Parallel read groups keep their wire order and decision-recording barriers."""
    async def execute(call):
        if call.get("error"):
            return {"status": "unavailable", "diagnostic": call["error"]}
        if allowed is not None and call["name"] not in allowed:
            return {"status": "unavailable", "diagnostic": "This tool is not available in this review stage."}
        return await tools.call(call["name"], call["arguments"])

    offset = 0
    while offset < len(calls):
        end = offset
        while end < len(calls) and calls[end]["name"] != "recordReviewDecisions":
            end += 1
        if end > offset:
            group = calls[offset:end]
            results = await gather_review_work(*(execute(call) for call in group))
            for call, result in zip(group, results):
                yield call, result
            offset = end
        else:
            call = calls[offset]
            yield call, await execute(call)
            offset += 1


@dataclass
class ToolConversation:
    outputs: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    tool_calls: int = 0
    graph_calls: int = 0
    complete: bool = False


async def converse(*, llm: Any, request: Any, tools: Any, system: str,
                   payload: dict[str, Any], stage: str, batch_ids: list[str],
                   allowed: set[str] | None = None,
                   effort: ReasoningEffort = ReasoningEffort.MEDIUM,
                   feedback: Callable[[ToolConversation], dict[str, Any] | None] | None = None,
                   checkpoint: bool = False) -> ToolConversation:
    result = ToolConversation()

    def record(**values):
        findings = values.get("findings")
        if findings:
            result.outputs.append({"findings": findings})
        return {"status": "ready", "instruction": "Candidate checkpoint saved; continue the owned change worklist."}

    if checkpoint:
        tools.register_decisions(record)
    schemas = await tools.schemas()
    if allowed is not None:
        schemas = [schema for schema in schemas if schema["name"] in allowed]
    available = {schema["name"] for schema in schemas}
    messages: list[Any] = [("system", system), ("human", json.dumps(payload, ensure_ascii=False))]
    seen: dict[str, str] = {}
    seen_outputs: set[str] = set()
    last_remaining: str | None = None
    stalled = False
    try:
        session = ReviewAgentSession(llm, request, schemas, effort=effort)
        result.diagnostics.extend(session.diagnostics)
        while True:
            turn = await session.invoke(messages, stage=stage, batch_ids=batch_ids)
            result.diagnostics.extend(turn.diagnostics)
            novel = False
            if turn.output is not None:
                signature = fingerprint(turn.output)
                if signature not in seen_outputs:
                    result.outputs.append(turn.output)
                    seen_outputs.add(signature)
            native_results, json_results = [], []
            async for call, value in tool_results(tools, turn.tool_calls, available):
                result.tool_calls += 1
                result.graph_calls += call["name"] in {
                    "queryCodeGraph", "getMinimalReviewContext", "getImpactRadius", "traverseCodeGraph",
                }
                if call["name"] != "recordReviewDecisions":
                    signature = fingerprint({"tool": call["name"], "result": value})
                    evidence_id = seen.get(signature)
                    if evidence_id is None:
                        evidence_id = f"read-{len(seen) + 1}"
                        seen[signature] = evidence_id
                        result.evidence[evidence_id] = {"kind": call["name"], "result": value}
                        novel = True
                        value = {"evidenceId": evidence_id, **value}
                    else:
                        value = {"status": value.get("status"), "evidenceId": evidence_id,
                                 "alreadyDelivered": True,
                                 "instruction": "The identical result is already in this conversation; use that source and evidence ID."}
                if call.get("protocol") == "json" or not session.native_tools:
                    json_results.append({"tool": call["name"], **value})
                else:
                    native_results.append(result_message(call, value))
            if session.native_tools:
                messages.extend([turn.response, *native_results])
            else:
                messages.append(("assistant", json.dumps(turn.output or {}, ensure_ascii=False)))
            if json_results:
                messages.append(("human", json.dumps({"toolObservations": json_results}, ensure_ascii=False)))
            if not turn.tool_calls:
                remaining = feedback(result) if feedback else None
                if remaining is None:
                    result.complete = True
                    break
                remaining_signature = fingerprint(remaining)
                novel |= last_remaining is not None and remaining_signature != last_remaining
                last_remaining = remaining_signature
                messages.append(("human", json.dumps(remaining, ensure_ascii=False)))
            if not novel:
                if stalled:
                    result.diagnostics.append(f"{stage}: remaining work unresolved after repeated results without new evidence.")
                    break
                messages.append(("human", "Use a new concrete evidence lead or finish with explicit unresolved work. Do not repeat identical reads."))
            stalled = not novel
    except Exception as error:
        result.diagnostics.append(f"{stage} interrupted; completed records retained: {error}")
    result.diagnostics.extend(tools.diagnostics)
    result.diagnostics = list(dict.fromkeys(result.diagnostics))
    return result
