from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping


SCENARIO_SCHEMA_VERSION = "1.0"
SUPPORTED_KINDS = frozenset(
    {
        "response",
        "structured",
        "stream",
        "rate_limit",
        "malformed",
        "timeout",
        "cancellation",
        "overage",
        "page",
        "duplicate",
        "retryable",
    }
)
_DOCUMENT_FIELDS = frozenset({"schema_version", "scenario_id", "steps"})
_STEP_FIELDS = frozenset(
    {
        "operation",
        "call",
        "kind",
        "payload",
        "usage",
        "chunks",
        "retry_after_seconds",
        "next_cursor",
        "duplicate_count",
    }
)
_SCENARIO_ID = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_. -]*[A-Za-z0-9])?$")
_SCENARIO_OPERATION = re.compile(r"^[a-z][a-z0-9_.-]*$")


class ScenarioContractError(ValueError):
    """The script and the adapter call sequence disagree."""


class SimulatedRateLimit(RuntimeError):
    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__(f"simulated rate limit; retry after {retry_after_seconds:g}s")
        self.retry_after_seconds = retry_after_seconds


class SimulatedRetryableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SimulatedResult:
    kind: str
    payload: Any = None
    usage: Mapping[str, int] = field(default_factory=dict)
    chunks: tuple[Any, ...] = ()
    next_cursor: str | None = None
    duplicate_count: int = 1
    overage: bool = False


@dataclass(frozen=True, slots=True)
class ScenarioStep:
    operation: str
    call: int
    kind: str
    payload: Any = None
    usage: Mapping[str, int] = field(default_factory=dict)
    chunks: tuple[Any, ...] = ()
    retry_after_seconds: float = 0.0
    next_cursor: str | None = None
    duplicate_count: int = 1

    def __post_init__(self) -> None:
        _require_unpadded_text(
            self.operation, "scenario operation", _SCENARIO_OPERATION, "ledger operation"
        )
        if isinstance(self.call, bool) or not isinstance(self.call, int) or self.call < 1:
            raise ScenarioContractError("scenario call ordinal must be positive")
        if not isinstance(self.kind, str) or self.kind not in SUPPORTED_KINDS:
            raise ScenarioContractError(f"unsupported scenario kind: {self.kind}")
        if (
            isinstance(self.retry_after_seconds, bool)
            or not isinstance(self.retry_after_seconds, (int, float))
            or self.retry_after_seconds < 0
            or (
                isinstance(self.retry_after_seconds, float)
                and not math.isfinite(self.retry_after_seconds)
            )
        ):
            raise ScenarioContractError("retry delay must not be negative")
        if (
            isinstance(self.duplicate_count, bool)
            or not isinstance(self.duplicate_count, int)
            or self.duplicate_count < 1
        ):
            raise ScenarioContractError("duplicate count must be positive")
        if not isinstance(self.usage, Mapping) or any(
            not isinstance(key, str)
            or isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount < 0
            for key, amount in self.usage.items()
        ):
            raise ScenarioContractError("scenario usage must contain non-negative integers")
        if not isinstance(self.chunks, tuple):
            raise ScenarioContractError("scenario chunks must be a tuple")
        if self.next_cursor is not None and not isinstance(self.next_cursor, str):
            raise ScenarioContractError("scenario cursor must be a string or null")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ScenarioStep:
        if not isinstance(value, Mapping):
            raise ScenarioContractError("scenario step must be an object")
        unknown = set(value) - _STEP_FIELDS
        if unknown:
            raise ScenarioContractError("scenario step contains unknown fields")
        raw_usage = value.get("usage", {})
        raw_chunks = value.get("chunks", [])
        if not isinstance(raw_usage, Mapping):
            raise ScenarioContractError("scenario usage must be an object")
        if not isinstance(raw_chunks, list):
            raise ScenarioContractError("scenario chunks must be a list")
        return cls(
            operation=value.get("operation", ""),
            call=value.get("call", 0),
            kind=value.get("kind", ""),
            payload=value.get("payload"),
            usage=dict(raw_usage),
            chunks=tuple(raw_chunks),
            retry_after_seconds=value.get("retry_after_seconds", 0.0),
            next_cursor=value.get("next_cursor"),
            duplicate_count=value.get("duplicate_count", 1),
        )

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "operation": self.operation,
            "call": self.call,
            "kind": self.kind,
        }
        optional = {
            "payload": self.payload,
            "usage": dict(self.usage),
            "chunks": list(self.chunks),
            "retry_after_seconds": self.retry_after_seconds,
            "next_cursor": self.next_cursor,
            "duplicate_count": self.duplicate_count,
        }
        defaults = {
            "payload": None,
            "usage": {},
            "chunks": [],
            "retry_after_seconds": 0.0,
            "next_cursor": None,
            "duplicate_count": 1,
        }
        document.update({key: value for key, value in optional.items() if value != defaults[key]})
        return document

    def resolve(self) -> SimulatedResult:
        if self.kind == "rate_limit":
            raise SimulatedRateLimit(self.retry_after_seconds)
        if self.kind == "timeout":
            raise TimeoutError("simulated dependency timeout")
        if self.kind == "cancellation":
            raise asyncio.CancelledError("simulated cancellation")
        if self.kind == "retryable":
            raise SimulatedRetryableError("simulated retryable dependency failure")
        return SimulatedResult(
            kind=self.kind,
            payload=self.payload,
            usage=dict(self.usage),
            chunks=self.chunks,
            next_cursor=self.next_cursor,
            duplicate_count=self.duplicate_count,
            overage=self.kind == "overage",
        )


class ScriptedScenario:
    def __init__(self, scenario_id: str, steps: tuple[ScenarioStep, ...]) -> None:
        _require_unpadded_text(
            scenario_id, "scenario ID", _SCENARIO_ID, "ASCII-safe name"
        )
        if not isinstance(steps, tuple) or any(
            not isinstance(step, ScenarioStep) for step in steps
        ):
            raise ScenarioContractError("scenario steps must be a tuple of ScenarioStep values")
        keys = [(step.operation, step.call) for step in steps]
        if len(keys) != len(set(keys)):
            raise ScenarioContractError("scenario contains duplicate operation/call slots")
        operations = {step.operation for step in steps}
        for operation in operations:
            ordinals = sorted(step.call for step in steps if step.operation == operation)
            if ordinals != list(range(1, len(ordinals) + 1)):
                raise ScenarioContractError("scenario call ordinals must be contiguous")
        self.scenario_id = scenario_id
        self._steps = {key: step for key, step in zip(keys, steps)}
        self._ordered_steps = steps
        self._call_counts: dict[str, int] = {}
        self._consumed: set[tuple[str, int]] = set()

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> ScriptedScenario:
        if not isinstance(value, Mapping):
            raise ScenarioContractError("scenario document must be an object")
        unknown = set(value) - _DOCUMENT_FIELDS
        if unknown:
            raise ScenarioContractError("scenario document contains unknown fields")
        if value.get("schema_version") != SCENARIO_SCHEMA_VERSION:
            raise ScenarioContractError("unsupported or missing scenario schema version")
        raw_steps = value.get("steps")
        if not isinstance(raw_steps, list):
            raise ScenarioContractError("scenario steps must be a list")
        return cls(
            scenario_id=value.get("scenario_id", ""),
            steps=tuple(ScenarioStep.from_dict(step) for step in raw_steps),
        )

    def take(self, operation: str) -> ScenarioStep:
        _require_unpadded_text(
            operation, "scenario operation", _SCENARIO_OPERATION, "ledger operation"
        )
        ordinal = self._call_counts.get(operation, 0) + 1
        self._call_counts[operation] = ordinal
        key = (operation, ordinal)
        step = self._steps.get(key)
        if step is None:
            raise ScenarioContractError(
                f"scenario {self.scenario_id!r} has no step for {operation!r} call {ordinal}"
            )
        self._consumed.add(key)
        return step

    @property
    def remaining(self) -> tuple[ScenarioStep, ...]:
        return tuple(
            step
            for step in self._ordered_steps
            if (step.operation, step.call) not in self._consumed
        )

    def assert_consumed(self) -> None:
        if self.remaining:
            raise ScenarioContractError(
                f"scenario {self.scenario_id!r} has {len(self.remaining)} unconsumed step(s)"
            )

    def replay(self) -> ScriptedScenario:
        return ScriptedScenario(self.scenario_id, self._ordered_steps)

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": SCENARIO_SCHEMA_VERSION,
            "scenario_id": self.scenario_id,
            "steps": [step.to_dict() for step in self._ordered_steps],
        }


def _require_unpadded_text(
    value: object,
    field: str,
    pattern: re.Pattern[str],
    grammar: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioContractError(f"{field} must not be empty")
    if value != value.strip():
        raise ScenarioContractError(f"{field} must not have leading or trailing whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ScenarioContractError(f"{field} must not contain control characters")
    if not pattern.fullmatch(value):
        raise ScenarioContractError(f"{field} must use the {grammar} grammar")
    return value
