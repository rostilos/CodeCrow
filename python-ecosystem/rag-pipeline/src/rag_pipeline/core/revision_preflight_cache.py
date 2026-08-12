"""Bounded, single-flight cache for immutable revision verification receipts."""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Hashable, Optional


@dataclass(frozen=True)
class RevisionPreflightKey:
    """Identity of one tenant-bound immutable physical generation."""

    collection: str
    workspace: str
    project: str
    branch: str
    commit: str


@dataclass
class _CacheEntry:
    expires_at: float | None
    value: Optional[dict]


@dataclass
class _Flight:
    event: threading.Event
    value: Optional[dict] = None
    error: BaseException | None = None


class RevisionPreflightCache:
    """Cache expensive immutable-generation verification without stampedes.

    Only the compact verification receipt is retained. A bounded semaphore
    limits cold verification across different generations, while callers for
    the same generation share one in-flight load. Loader failures are shared
    with current waiters but are never cached, so a transient Qdrant failure
    remains retryable.
    """

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float,
        max_concurrent_loads: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must not be negative")
        if max_concurrent_loads < 1:
            raise ValueError("max_concurrent_loads must be positive")
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._values: OrderedDict[Hashable, _CacheEntry] = OrderedDict()
        self._flights: dict[Hashable, _Flight] = {}
        self._load_slots = threading.BoundedSemaphore(max_concurrent_loads)

    def get_or_load(
        self,
        key: Hashable,
        loader: Callable[[], Optional[dict]],
    ) -> Optional[dict]:
        """Return a cached receipt or execute one bounded single-flight load."""
        now = self._clock()
        with self._lock:
            entry = self._values.get(key)
            if entry is not None and (
                entry.expires_at is None or entry.expires_at > now
            ):
                self._values.move_to_end(key)
                return copy.deepcopy(entry.value)
            if entry is not None:
                self._values.pop(key, None)

            flight = self._flights.get(key)
            owner = flight is None
            if owner:
                flight = _Flight(event=threading.Event())
                self._flights[key] = flight

        if not owner:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            return copy.deepcopy(flight.value)

        try:
            with self._load_slots:
                value = loader()
        except BaseException as exception:
            with self._lock:
                self._flights.pop(key, None)
                flight.error = exception
                flight.event.set()
            raise

        stored_value = copy.deepcopy(value)
        with self._lock:
            # An absent revision can later appear in a legacy mutable target.
            # Positive receipts belong to sealed immutable generations; cache
            # those, but keep absence immediately observable and retryable.
            if stored_value is not None:
                self._values[key] = _CacheEntry(
                    expires_at=(
                        None
                        if self._ttl_seconds == 0
                        else self._clock() + self._ttl_seconds
                    ),
                    value=stored_value,
                )
                self._values.move_to_end(key)
                while len(self._values) > self._max_entries:
                    self._values.popitem(last=False)
            self._flights.pop(key, None)
            flight.value = stored_value
            flight.event.set()
        return copy.deepcopy(stored_value)

    def invalidate_collection(self, collection: str) -> None:
        """Discard cached receipts for one physical collection."""
        with self._lock:
            keys = [
                key
                for key in self._values
                if getattr(key, "collection", None) == collection
            ]
            for key in keys:
                self._values.pop(key, None)

    def clear(self) -> None:
        """Discard all completed cache entries without disturbing loaders."""
        with self._lock:
            self._values.clear()
