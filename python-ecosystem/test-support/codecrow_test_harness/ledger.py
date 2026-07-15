from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


LEDGER_SCHEMA_VERSION = "1.0"
_HOST = r"(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?|\[[0-9a-f:]+\])"
_SAFE_TARGET = re.compile(rf"^(?P<host>{_HOST}):(?P<port>[0-9]{{1,5}})$", re.IGNORECASE)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*$", re.IGNORECASE)
_BOUNDARY = re.compile(r"^[a-z][a-z0-9_-]*$")
_OPERATION = re.compile(r"^[a-z][a-z0-9_.-]*$")
_OUTCOME = re.compile(r"^[a-z][a-z0-9_-]*$")
_PHASES = frozenset({"PRE_DNS", "PRE_SOCKET", "PRE_EXEC", "SIMULATED"})


class LiveExternalCallError(AssertionError):
    """Raised when a supposedly offline run recorded a live call."""


class UnexpectedBlockedCallError(AssertionError):
    """Raised when an application swallowed a boundary denial."""


@dataclass(frozen=True, slots=True)
class ExternalCall:
    boundary: str
    live: bool
    operation: str
    outcome: str
    phase: str
    sequence: int
    simulated: bool
    target: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ExternalCallLedger:
    def __init__(self) -> None:
        self._entries: list[ExternalCall] = []
        self._acknowledged_blocked_sequences: set[int] = set()
        self._lock = threading.RLock()

    @property
    def entries(self) -> tuple[ExternalCall, ...]:
        with self._lock:
            return tuple(self._entries)

    @property
    def live_call_count(self) -> int:
        with self._lock:
            return sum(entry.live for entry in self._entries)

    @property
    def simulated_call_count(self) -> int:
        with self._lock:
            return sum(entry.simulated for entry in self._entries)

    def record(
        self,
        *,
        boundary: str,
        operation: str,
        outcome: str,
        phase: str,
        target: str,
        live: bool = False,
        simulated: bool = False,
    ) -> ExternalCall:
        required = (boundary, operation, outcome, phase, target)
        if any(not isinstance(value, str) or not value.strip() for value in required):
            raise ValueError("ledger text fields must be non-empty strings")
        if not isinstance(live, bool) or not isinstance(simulated, bool):
            raise ValueError("ledger live and simulated flags must be booleans")
        if live and simulated:
            raise ValueError("a call cannot be both live and simulated")
        with self._lock:
            entry = ExternalCall(
                boundary=_canonical_identifier(boundary, _BOUNDARY, "boundary"),
                live=live,
                operation=_canonical_identifier(operation, _OPERATION, "operation"),
                outcome=_canonical_identifier(outcome, _OUTCOME, "outcome"),
                phase=_canonical_phase(phase),
                sequence=len(self._entries) + 1,
                simulated=simulated,
                target=_redact_target(target.strip()),
            )
            self._entries.append(entry)
        return entry

    def to_document(self) -> dict[str, object]:
        with self._lock:
            entries = tuple(self._entries)
            return {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "live_call_count": sum(entry.live for entry in entries),
                "simulated_call_count": sum(entry.simulated for entry in entries),
                "calls": [entry.to_dict() for entry in entries],
            }

    def write(self, path: str | os.PathLike[str]) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(self.to_document(), indent=2, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def assert_zero_live_calls(self) -> None:
        if self.live_call_count:
            raise LiveExternalCallError(
                f"offline run recorded {self.live_call_count} live external call(s)"
            )

    def acknowledge_blocked(
        self,
        call: ExternalCall,
        *,
        boundary: str,
        operation: str,
        phase: str,
        target: str,
    ) -> None:
        expected = (
            _canonical_identifier(boundary, _BOUNDARY, "boundary"),
            _canonical_identifier(operation, _OPERATION, "operation"),
            _canonical_phase(phase),
            _redact_target(target.strip()),
        )
        with self._lock:
            if not any(entry is call for entry in self._entries) or call.outcome != "blocked":
                raise ValueError("only a recorded blocked call can be acknowledged")
            actual = (call.boundary, call.operation, call.phase, call.target)
            if actual != expected:
                raise ValueError("blocked-call acknowledgement does not match the expected call")
            self._acknowledged_blocked_sequences.add(call.sequence)

    def assert_no_unacknowledged_blocked_calls(self) -> None:
        with self._lock:
            unacknowledged = [
                call
                for call in self._entries
                if call.outcome == "blocked"
                and call.sequence not in self._acknowledged_blocked_sequences
            ]
        if unacknowledged:
            sequences = ", ".join(str(call.sequence) for call in unacknowledged)
            raise UnexpectedBlockedCallError(
                f"offline run contains unacknowledged blocked call sequence(s): {sequences}"
            )


def _redact_target(target: str) -> str:
    endpoint = _SAFE_TARGET.fullmatch(target)
    if endpoint:
        canonical = _canonical_host_port(endpoint.group("host"), endpoint.group("port"))
        return canonical or "<redacted-target>"
    if "://" in target:
        try:
            parsed = urlsplit(target)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            return "<redacted-target>"
        if parsed.scheme and host and _SCHEME.fullmatch(parsed.scheme):
            formatted_host = f"[{host}]" if ":" in host else host
            canonical = _canonical_host_port(formatted_host, port or 0)
            if canonical is None:
                return "<redacted-target>"
            canonical_host, _, canonical_port = canonical.rpartition(":")
            port_suffix = "" if port is None else f":{canonical_port}"
            return f"{parsed.scheme.lower()}://{canonical_host}{port_suffix}"
    return "<redacted-target>"


def _canonical_host_port(host: str, port: object) -> str | None:
    try:
        ascii_host = host.encode("ascii").decode("ascii").lower()
        canonical_port = int(port)
    except (UnicodeError, TypeError, ValueError):
        return None
    if not re.fullmatch(_HOST, ascii_host) or not 0 <= canonical_port <= 65535:
        return None
    return f"{ascii_host}:{canonical_port}"


def _canonical_identifier(value: str, pattern: re.Pattern[str], field: str) -> str:
    canonical = value.strip().lower()
    if not pattern.fullmatch(canonical):
        raise ValueError(f"invalid external-call {field}")
    return canonical


def _canonical_phase(value: str) -> str:
    canonical = value.strip().upper()
    if canonical not in _PHASES:
        raise ValueError(f"unsupported external-call phase: {value}")
    return canonical
