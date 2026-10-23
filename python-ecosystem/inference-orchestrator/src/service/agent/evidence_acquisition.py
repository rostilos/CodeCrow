"""Request-local retrieval reuse and evidence diagnostics."""
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


def compact_evidence_messages(messages: list[Any]) -> tuple[list[Any], dict[str, int]]:
    """Reference exact source already visible, without changing stored evidence.

    References address earlier visible strings, never hidden artifacts or a
    process-wide cache. Rebuilding this view also restores source after history
    truncation, when its earlier occurrence is no longer available.
    """
    visible: list[tuple[int, str, str]] = []
    prepared, reused, removed, before, after = [], 0, 0, 0, 0

    def visit(value: Any, index: int, pointer: str = "") -> None:
        nonlocal reused, removed
        if isinstance(value, dict):
            for key, child in list(value.items()):
                path = pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")
                source_field = key == "fileContent" or (
                    key == "content" and ("/sourceWindows/" in path or pointer.endswith("/unit"))
                )
                if source_field and isinstance(child, str) and len(child) >= 512:
                    match = next(((i, p, text.find(child)) for i, p, text in visible if child in text), None)
                    if match is not None and key + "Reference" not in value:
                        i, p, offset = match
                        value.pop(key)
                        value[key + "Reference"] = {
                            "description": "Exact source already visible; reuse with this result's metadata. messageIndex is zero-based, excluding separate system instructions.",
                            "messageIndex": i,
                            "jsonPointer": p,
                            "characterOffset": offset,
                            "characterLength": len(child),
                            "sha256": hashlib.sha256(child.encode()).hexdigest(),
                        }
                        reused += 1
                        removed += len(child)
                        continue
                visit(child, index, path)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, index, pointer + "/" + str(i))
        elif isinstance(value, str) and len(value) >= 512:
            visible.append((index, pointer, value))

    for index, message in enumerate(messages):
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            prepared.append(message)
            continue
        is_retrieval = getattr(message, "type", None) == "tool" and getattr(message, "name", None) in _RETRIEVAL_TOOLS
        if is_retrieval:
            try:
                payload = json.loads(content)
            except ValueError:
                payload = None
            if isinstance(payload, (dict, list)):
                visit(payload, index)
                compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                before += len(content.encode())
                after += len(compact.encode())
                message = message.model_copy(update={"content": compact})
                prepared.append(message)
                continue
        if len(content) >= 512:
            visible.append((index, "", content))
        prepared.append(message)
    return prepared, {"sourceBodiesReused": reused, "sourceCharactersReused": removed,
                      "originalToolBytes": before, "visibleToolBytes": after}


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
    """Reuse identical reads without blocking different evidence requests.

    Each prompt agent owns one instance, bound to one immutable review snapshot.
    No cached results cross reviews or tenants.
    """

    def __init__(self):
        super().__init__()
        self._cache: dict[str, Any] = {}

    def _prepare_model_request(self, request: Any) -> Any:
        messages, diagnostics = compact_evidence_messages(request.messages)
        if diagnostics["originalToolBytes"]:
            record_compute_decision("evidence_context_reuse", **diagnostics)
        return request.override(messages=messages)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._prepare_model_request(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._prepare_model_request(request))

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        call = request.tool_call
        name, args = str(call.get("name") or ""), call.get("args") or {}
        if name not in _RETRIEVAL_TOOLS:
            return await handler(request)
        fingerprint = hashlib.sha256(json.dumps([name, args], sort_keys=True, default=str).encode()).hexdigest()
        gap = str(args.get("question") or args.get("target") or args.get("start") or args.get("filePath") or "focused source lookup")[:400]
        cached = fingerprint in self._cache
        try:
            result = self._cache[fingerprint] if cached else await handler(request)
        except Exception as error:
            result = ToolMessage(content=json.dumps({"status": "unavailable", "reason": type(error).__name__, "unresolvedEvidenceGap": gap}),
                                 tool_call_id=call.get("id") or "evidence-recovery", name=name)
        status = observation_status(result)
        if not cached:
            self._cache[fingerprint] = result
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
