import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from rag_pipeline.core.revision_preflight_cache import (
    RevisionPreflightCache,
    RevisionPreflightKey,
)


def _cache(**overrides):
    options = {
        "max_entries": 4,
        "ttl_seconds": 60,
        "max_concurrent_loads": 2,
    }
    options.update(overrides)
    return RevisionPreflightCache(**options)


def _key(collection="physical-a"):
    return RevisionPreflightKey(
        collection=collection,
        workspace="workspace",
        project="project",
        branch="main",
        commit="a" * 40,
    )


def test_preflight_cache_reuses_receipt_without_sharing_mutable_state():
    cache = _cache()
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        return {"plugin_ids": ["python"]}

    first = cache.get_or_load(_key(), load)
    first["plugin_ids"].append("tampered")
    second = cache.get_or_load(_key(), load)

    assert calls == 1
    assert second == {"plugin_ids": ["python"]}


def test_preflight_cache_single_flights_concurrent_generation_loads():
    cache = _cache()
    entered = threading.Event()
    release = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    def load():
        nonlocal calls
        with call_lock:
            calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return {"generation_manifest_sha256": "b" * 64}

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(cache.get_or_load, _key(), load) for _ in range(8)]
        assert entered.wait(timeout=2)
        time.sleep(0.05)
        release.set()
        results = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert results == [{"generation_manifest_sha256": "b" * 64}] * 8


def test_preflight_cache_bounds_cold_loads_across_generations():
    cache = _cache(max_concurrent_loads=2)
    release = threading.Event()
    call_lock = threading.Lock()
    active = 0
    peak = 0

    def load():
        nonlocal active, peak
        with call_lock:
            active += 1
            peak = max(peak, active)
        assert release.wait(timeout=2)
        with call_lock:
            active -= 1
        return {"ok": True}

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(cache.get_or_load, _key(f"physical-{index}"), load)
            for index in range(6)
        ]
        deadline = time.monotonic() + 2
        while peak < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        release.set()
        assert [future.result(timeout=2) for future in futures] == [
            {"ok": True}
        ] * 6

    assert peak == 2


def test_preflight_cache_does_not_cache_loader_failures():
    cache = _cache()
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("qdrant unavailable")
        return {"ok": True}

    with pytest.raises(RuntimeError, match="qdrant unavailable"):
        cache.get_or_load(_key(), load)

    assert cache.get_or_load(_key(), load) == {"ok": True}
    assert calls == 2


def test_preflight_cache_does_not_cache_absent_mutable_revision():
    cache = _cache()
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        return None if calls == 1 else {"ok": True}

    assert cache.get_or_load(_key(), load) is None
    assert cache.get_or_load(_key(), load) == {"ok": True}
    assert calls == 2


def test_preflight_cache_expires_and_invalidates_physical_collection():
    now = [10.0]
    cache = _cache(ttl_seconds=5, clock=lambda: now[0])
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        return {"call": calls}

    assert cache.get_or_load(_key(), load) == {"call": 1}
    now[0] = 16.0
    assert cache.get_or_load(_key(), load) == {"call": 2}
    cache.invalidate_collection("physical-a")
    assert cache.get_or_load(_key(), load) == {"call": 3}


def test_zero_ttl_retains_immutable_receipt_for_process_lifetime():
    now = [10.0]
    cache = _cache(ttl_seconds=0, clock=lambda: now[0])
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        return {"call": calls}

    assert cache.get_or_load(_key(), load) == {"call": 1}
    now[0] = 1_000_000.0
    assert cache.get_or_load(_key(), load) == {"call": 1}
    assert calls == 1
