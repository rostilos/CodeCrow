"""Request-local retrieval reuse and recovery from unproductive tool loops."""
from __future__ import annotations

import hashlib
import json
from copy import copy
from typing import Any, Mapping

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from service.review.inference_usage import record_compute_decision


_RETRIEVAL_TOOLS = frozenset({
    "getReviewFileContent", "getBranchFileContent", "getMinimalReviewContext",
    "exploreReviewContext", "getImpactRadius", "traverseCodeGraph", "queryCodeGraph",
    "getStructuralUnit",
})


def evidence_payload(result: Any) -> Any:
    value = getattr(result, "artifact", None)
    if isinstance(value, Mapping):
        return value
    value = getattr(result, "content", result)
    if isinstance(value, list):
        value = "\n".join(str(part.get("text", "")) for part in value if isinstance(part, Mapping))
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def observation_status(result: Any) -> str:
    if getattr(result, "status", None) == "error" or getattr(result, "isError", False) is True:
        return "unavailable"
    payload = evidence_payload(result)
    if isinstance(payload, Mapping):
        if payload.get("isError") is True:
            return "unavailable"
        if isinstance(payload.get("context"), Mapping):
            payload = payload["context"]
        if (payload.get("unavailable") or payload.get("error")
                or str(payload.get("status", "")).casefold() in {"unavailable", "error", "failed", "disabled"}):
            return "unavailable"
        if payload.get("exists") is False:
            return "empty"
        evidence_fields = ("nodes", "edges", "relations", "results", "sourceWindows", "fileContent", "content", "unit")
        present = [payload[key] for key in evidence_fields if key in payload]
        evidence = payload.get("evidence")
        if isinstance(evidence, Mapping):
            present.extend(evidence[key] for key in (
                "nodes", "relations", "frontier", "callPaths", "blastRadius", "tests", "framework", "edges",
            ) if key in evidence)
        if present and not any(present):
            return "empty"
    elif isinstance(payload, str) and payload.startswith(("Error: ", "Error executing tool", "Tool execution failed:", "File not found:")):
        return "unavailable"
    elif not payload:
        return "empty"
    return "acquired"


class EvidenceAcquisitionMiddleware(AgentMiddleware):
    """Never re-execute identical reads; switch tools after repeated nonprogress.

    Each prompt agent owns one instance, bound to one immutable review snapshot.
    No results or disabled-tool state cross reviews or tenants.
    """

    def __init__(self):
        super().__init__()
        self._cache: dict[str, Any] = {}
        self._nonprogress: dict[str, int] = {}
        self._disabled: set[str] = set()

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        call = request.tool_call
        name, args = str(call.get("name") or ""), call.get("args") or {}
        if name not in _RETRIEVAL_TOOLS:
            return await handler(request)
        fingerprint = hashlib.sha256(json.dumps([name, args], sort_keys=True, default=str).encode()).hexdigest()
        gap = str(args.get("question") or args.get("target") or args.get("start") or args.get("filePath") or "focused source lookup")[:400]
        if name in self._disabled:
            record_compute_decision("evidence_acquisition", tool=name, outcome="unresolved", requestFingerprint=fingerprint, gap=gap)
            return ToolMessage(content=json.dumps({
                "status": "unavailable", "reason": "repeated_unproductive_queries",
                "unresolvedEvidenceGap": gap,
                "nextAction": "Use another focused graph operation or exact source tool, or retain the uncertainty; missing evidence is not proof of absence.",
            }), tool_call_id=call.get("id") or "evidence-recovery", name=name)
        cached = fingerprint in self._cache
        try:
            result = self._cache[fingerprint] if cached else await handler(request)
        except Exception as error:
            result = ToolMessage(content=json.dumps({"status": "unavailable", "reason": type(error).__name__, "unresolvedEvidenceGap": gap}),
                                 tool_call_id=call.get("id") or "evidence-recovery", name=name)
        status = observation_status(result)
        if not cached:
            self._cache[fingerprint] = result
        if status in {"empty", "unavailable"}:
            self._nonprogress[name] = self._nonprogress.get(name, 0) + 1
            if self._nonprogress[name] >= 2:
                self._disabled.add(name)
        else:
            self._nonprogress[name] = 0
        record_compute_decision("evidence_acquisition", tool=name,
                                outcome="cache_hit" if cached else status,
                                evidenceStatus=status, requestFingerprint=fingerprint,
                                gap=gap, unresolved=status != "acquired")
        if cached:
            if hasattr(result, "model_copy"):
                result = result.model_copy(update={"tool_call_id": call.get("id"), "id": None})
            else:
                result = copy(result)
                if hasattr(result, "tool_call_id"):
                    result.tool_call_id = call.get("id")
                if hasattr(result, "id"):
                    result.id = None
        return result

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        # Keep the native session's tool inventory stable. Disabled reads return
        # the explicit unresolved diagnostic above without touching the backend.
        return await handler(request)
