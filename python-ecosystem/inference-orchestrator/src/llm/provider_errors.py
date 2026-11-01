"""Distinguish uncertain execution from a completed response needing repair."""
import httpx


def raise_if_outcome_unknown(error: Exception) -> None:
    """A lost response must not enter another paid fallback or repair attempt."""
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
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
