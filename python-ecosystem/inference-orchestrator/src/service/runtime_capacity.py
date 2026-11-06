"""Shared inference runtime capacities, independent of review content size."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)
DEFAULT_REVIEW_CONCURRENCY = 16


def _capacity(name: str, default: int) -> int:
    configured = os.environ.get(name)
    if configured is None or not configured.strip():
        return default
    try:
        value = int(configured)
        if value > 0:
            return value
    except ValueError:
        pass
    logger.warning("Invalid positive capacity %s=%r; using %s", name, configured, default)
    return default


def review_concurrency() -> int:
    """Concurrent admitted review jobs and independent repository operations."""
    return _capacity("MAX_CONCURRENT_REVIEWS", DEFAULT_REVIEW_CONCURRENCY)


def review_call_concurrency() -> int:
    """Concurrent provider invocations; one review never owns a lifetime slot."""
    return _capacity("MAX_CONCURRENT_REVIEW_CALLS", review_concurrency())
