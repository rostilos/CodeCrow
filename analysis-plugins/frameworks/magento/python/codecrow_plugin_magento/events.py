from __future__ import annotations

import json
from dataclasses import dataclass

from codecrow_plugins import SymbolDefinition


_LITERAL_INSTANCE_CALL_PREFIX = "php-literal-instance-call-reference:"
_EVENT_MANAGER_TYPES = frozenset({
    r"magento\framework\event\manager",
    r"magento\framework\event\managerinterface",
})
_EXACT_LITERAL_RESOLUTIONS = frozenset({
    "direct-literal",
    "local-exact-assignment",
})


@dataclass(frozen=True, order=True)
class EventDispatch:
    owner: str
    caller: str
    path: str
    line: int
    event_name: str
    receiver_type: str
    receiver_resolution: str
    literal_resolution: str

    @property
    def callable(self) -> str:
        return f"{self.owner}::{self.caller}"


@dataclass(frozen=True, order=True)
class EventEntrypoint:
    area: str
    target: str
    method: str
    path: str


@dataclass(frozen=True, order=True)
class EventAreaResolution:
    status: str
    area: str = ""
    paths: tuple[str, ...] = ()
    candidate_areas: tuple[str, ...] = ()


def _decoded_dispatch(
    symbol: SymbolDefinition,
    encoded: str,
) -> EventDispatch | None:
    try:
        payload = json.loads(encoded)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    method = payload.get("method")
    receiver_type = payload.get("target")
    caller = payload.get("caller")
    line = payload.get("line")
    arguments = payload.get("literalStringArguments")
    if (
        not isinstance(method, str)
        or method.casefold() != "dispatch"
        or not isinstance(receiver_type, str)
        or receiver_type.lstrip("\\").casefold() not in _EVENT_MANAGER_TYPES
        or not isinstance(caller, str)
        or not caller
        or not isinstance(line, int)
        or line < 1
        or not isinstance(arguments, dict)
    ):
        return None
    event_name = arguments.get("0")
    if not isinstance(event_name, str) or not event_name.strip():
        return None
    literal_resolutions = payload.get("literalArgumentResolution", {})
    if not isinstance(literal_resolutions, dict):
        return None
    literal_resolution = literal_resolutions.get("0", "direct-literal")
    if literal_resolution not in _EXACT_LITERAL_RESOLUTIONS:
        return None
    receiver_resolution = payload.get("receiverResolution", "")
    if not isinstance(receiver_resolution, str):
        return None
    return EventDispatch(
        owner=symbol.qualified_name,
        caller=caller,
        path=symbol.path,
        line=line,
        event_name=event_name.strip(),
        receiver_type=receiver_type.lstrip("\\"),
        receiver_resolution=receiver_resolution,
        literal_resolution=str(literal_resolution),
    )


def decode_event_dispatches(
    symbols: tuple[SymbolDefinition, ...],
) -> tuple[EventDispatch, ...]:
    """Decode only exact PHP Event Manager dispatch references.

    PHP owns syntax and receiver resolution. Magento consumes the neutral,
    typed snapshot attributes and never reparses PHP. If two different event
    names map to the same owner/caller/line, the available position is
    ambiguous and both are withheld.
    """
    decoded = {
        dispatch
        for symbol in symbols
        for key, encoded in symbol.attributes
        if key.startswith(_LITERAL_INSTANCE_CALL_PREFIX)
        if (dispatch := _decoded_dispatch(symbol, encoded)) is not None
    }
    names_by_location: dict[tuple[str, str, str, int], set[str]] = {}
    for dispatch in decoded:
        names_by_location.setdefault((
            dispatch.owner,
            dispatch.caller,
            dispatch.path,
            dispatch.line,
        ), set()).add(dispatch.event_name)
    return tuple(sorted(
        dispatch
        for dispatch in decoded
        if len(names_by_location[
            (
                dispatch.owner,
                dispatch.caller,
                dispatch.path,
                dispatch.line,
            )
        ]) == 1
    ))


def resolve_dispatch_area(
    dispatch: EventDispatch,
    entrypoints: tuple[EventEntrypoint, ...],
) -> EventAreaResolution:
    matches = tuple(
        entrypoint
        for entrypoint in entrypoints
        if entrypoint.target.lstrip("\\").casefold()
        == dispatch.owner.lstrip("\\").casefold()
        and entrypoint.method.casefold() == dispatch.caller.casefold()
    )
    areas = {entrypoint.area for entrypoint in matches}
    paths = tuple(sorted({
        entrypoint.path for entrypoint in matches if entrypoint.path
    }))
    if len(areas) == 1:
        area = next(iter(areas))
        return EventAreaResolution("proven", area, paths, (area,))
    if areas:
        return EventAreaResolution(
            "ambiguous",
            paths=paths,
            candidate_areas=tuple(sorted(areas)),
        )
    return EventAreaResolution("unresolved")
