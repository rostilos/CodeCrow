"""Distinguish uncertain execution from a completed response needing repair."""
import httpx
import math
import time
from email.utils import parsedate_to_datetime


RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_MAX_WAIT_SECONDS = 60.0


def is_quota_exhausted(error: Exception) -> bool:
    details = (str(error) + " " + str(getattr(error, "body", ""))).lower()
    return any(marker in details for marker in (
        "insufficient_quota", "insufficient credits", "quota_exceeded",
        "billing_hard_limit_reached", "usage_limit_reached", "key limit exceeded",
    ))


def rate_limit_retry_delay(error: Exception, retry: int) -> float | None:
    """Retry only an explicit rejection, never a lost response or exhausted account."""
    status = getattr(error, "status_code", getattr(error, "code", None))
    if status != 429 or retry >= RATE_LIMIT_MAX_RETRIES or is_quota_exhausted(error):
        return None
    headers = getattr(getattr(error, "response", None), "headers", {}) or {}
    delay = 5.0 * 2 ** retry
    retry_after = headers.get("retry-after")
    if retry_after is not None:
        try:
            try:
                requested = float(retry_after)
            except ValueError:
                requested = parsedate_to_datetime(retry_after).timestamp() - time.time()
            if not math.isfinite(requested):
                return None
            delay = max(delay, requested)
        except (TypeError, ValueError, OverflowError):
            pass
    # A long provider cooldown must not be shortened into an early retry.
    return delay if delay <= RATE_LIMIT_MAX_WAIT_SECONDS else None


def raise_if_outcome_unknown(error: Exception) -> None:
    """A lost response must not enter another paid fallback or repair attempt."""
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "status_code", getattr(current, "code", None)) == 429:
            # The shared invocation boundary already exhausted its allowance.
            raise error
        if isinstance(current, (TimeoutError, ConnectionError, httpx.TransportError)):
            raise error
        module = type(current).__module__
        names = {base.__name__ for base in type(current).__mro__}
        if module.startswith("httpx2") and "TransportError" in names:
            raise error
        if module.startswith(("openai", "anthropic", "google.genai", "google.api_core")):
            status = getattr(current, "status_code", getattr(current, "code", None))
            if names & {"APIConnectionError", "APITimeoutError", "ServerError", "DeadlineExceeded", "ServiceUnavailable"} or (
                isinstance(status, int) and status >= 500
            ):
                raise error
        current = current.__cause__ or current.__context__
