"""Request-local accounting of real host invocations; no estimated provider prices."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import time
from typing import Any, Mapping


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def provider_usage(response: Any) -> dict[str, int] | None:
    """Read reported usage from the raw provider message or structured envelope."""
    getter = response.get if isinstance(response, Mapping) else lambda key, default=None: getattr(response, key, default)
    usage = _mapping(getter("usage_metadata"))
    metadata = _mapping(getter("response_metadata"))
    native = _mapping(metadata.get("token_usage") or metadata.get("usage") or _mapping(getter("llm_output")).get("token_usage"))
    values = {}
    for name, native_name in (("inputTokens", "prompt_tokens"), ("outputTokens", "completion_tokens"), ("totalTokens", "total_tokens")):
        key = {"inputTokens": "input_tokens", "outputTokens": "output_tokens", "totalTokens": "total_tokens"}[name]
        count = _count(usage.get(key))
        if count is None:
            count = _count(native.get(native_name))
        if count is not None:
            values[name] = count
    for name, detail, key, native_detail, native_key in (
        ("cachedInputTokens", "input_token_details", "cache_read", "prompt_tokens_details", "cached_tokens"),
        ("reasoningOutputTokens", "output_token_details", "reasoning", "completion_tokens_details", "reasoning_tokens"),
    ):
        count = _count(_mapping(usage.get(detail)).get(key))
        if count is None:
            count = _count(_mapping(native.get(native_detail)).get(native_key))
        if count is not None:
            values[name] = count
    if values:
        return values
    raw = _mapping(response).get("raw")
    if raw is not None and raw is not response:
        return provider_usage(raw)
    results = getter("result")
    if isinstance(results, (list, tuple)):
        reported = [provider_usage(message) for message in results
                    if getattr(message, "type", None) != "tool"]
        if reported and all(value is not None for value in reported):
            complete_keys = set.intersection(*(set(value) for value in reported))
            return {key: sum(value[key] for value in reported) for key in complete_keys} or None
    return None


@dataclass
class InferenceUsage:
    started_at: float = field(default_factory=time.monotonic)
    calls: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        stages = {}
        for call in self.calls:
            stage = stages.setdefault(call["stage"], {"attempts": 0, "completed": 0, "failed": 0, "callsWithUsage": 0, "durationMs": 0.0, "queueWaitMs": 0.0})
            stage["attempts"] += 1
            stage["completed"] += int(call["outcome"] == "completed")
            stage["failed"] += int(call["outcome"] not in {"completed", "running"})
            stage["durationMs"] += call.get("durationMs", 0)
            stage["queueWaitMs"] += call.get("queueWaitMs", 0)
            if call.get("usage") is not None:
                stage["callsWithUsage"] += 1
                for key, value in call["usage"].items():
                    observed_key = "observed" + key[0].upper() + key[1:]
                    known_key = key + "KnownCalls"
                    stage[observed_key] = stage.get(observed_key, 0) + value
                    stage[known_key] = stage.get(known_key, 0) + 1
        for stage in stages.values():
            for key in ("inputTokens", "outputTokens", "totalTokens", "cachedInputTokens", "reasoningOutputTokens"):
                known_key = key + "KnownCalls"
                observed_key = "observed" + key[0].upper() + key[1:]
                stage.setdefault(known_key, 0)
                stage.setdefault(observed_key, 0)
                stage[key] = stage[observed_key] if stage[known_key] == stage["attempts"] else None
        return {
            "attempts": len(self.calls), "elapsedMs": round((time.monotonic() - self.started_at) * 1000, 3),
            "stages": stages, "decisions": list(self.decisions),
            "usageAccounting": "provider-reported observed subtotals with per-token known-call counts; totals are null when any call omits that field",
            "attemptAccounting": "host-visible invocations and recovery attempts; SDK-internal retries are not observable",
        }


_ACTIVE_USAGE: ContextVar[InferenceUsage | None] = ContextVar("review_inference_usage", default=None)
_ACTIVE_CALL: ContextVar[dict | None] = ContextVar("review_inference_call", default=None)


def record_model_response(response: Any) -> None:
    call = _ACTIVE_CALL.get()
    if call is not None:
        call["usage"] = provider_usage(response)


def record_compute_decision(kind: str, **details: Any) -> None:
    usage = _ACTIVE_USAGE.get()
    if usage is not None:
        usage.decisions.append({"kind": kind, **details})


def current_inference_usage() -> dict[str, Any]:
    usage = _ACTIVE_USAGE.get()
    return usage.summary() if usage is not None else {}
