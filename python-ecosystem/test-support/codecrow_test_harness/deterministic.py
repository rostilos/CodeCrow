from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid5


DEFAULT_ID_NAMESPACE = UUID("4debb57f-a6e4-5f69-a42a-5f8f63bd7831")


@dataclass(slots=True)
class FrozenClock:
    _current: datetime

    def __post_init__(self) -> None:
        if self._current.tzinfo is None or self._current.utcoffset() is None:
            raise ValueError("frozen clock requires a timezone-aware instant")
        self._current = self._current.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._current

    def advance(self, amount: timedelta) -> datetime:
        if amount < timedelta(0):
            raise ValueError("frozen clock cannot move backwards")
        self._current += amount
        return self._current


class DeterministicIds:
    def __init__(self, *, namespace: UUID = DEFAULT_ID_NAMESPACE, prefix: str = "id") -> None:
        if not prefix:
            raise ValueError("ID prefix must not be empty")
        self._namespace = namespace
        self._prefix = prefix
        self._counter = 0

    def next_uuid(self) -> UUID:
        self._counter += 1
        return uuid5(self._namespace, f"{self._prefix}:{self._counter}")

    def reset(self) -> None:
        self._counter = 0
