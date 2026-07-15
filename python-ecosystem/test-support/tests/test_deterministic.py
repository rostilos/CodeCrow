from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


TEST_SUPPORT_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_SUPPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_SUPPORT_ROOT))

from codecrow_test_harness.deterministic import DeterministicIds, FrozenClock


def test_frozen_clock_is_utc_deterministic_and_monotonic() -> None:
    clock = FrozenClock(datetime(2026, 7, 14, 12, 0, tzinfo=timezone(timedelta(hours=3))))
    assert clock.now() == datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc)
    assert clock.advance(timedelta(seconds=5)) == datetime(
        2026, 7, 14, 9, 0, 5, tzinfo=timezone.utc
    )
    assert clock.advance(timedelta(0)) == clock.now()
    with pytest.raises(ValueError, match="backwards"):
        clock.advance(timedelta(microseconds=-1))
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 7, 14))


def test_deterministic_ids_replay_and_validate_prefix() -> None:
    first = DeterministicIds(prefix="execution")
    values = [first.next_uuid(), first.next_uuid()]
    assert values[0] != values[1]
    first.reset()
    assert first.next_uuid() == values[0]
    second = DeterministicIds(prefix="execution")
    assert second.next_uuid() == values[0]
    with pytest.raises(ValueError, match="prefix"):
        DeterministicIds(prefix="")
