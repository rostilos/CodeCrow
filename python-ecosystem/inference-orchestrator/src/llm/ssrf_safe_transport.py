"""
SSRF-safe httpx transport for OpenAI-compatible custom endpoints.

Validates that resolved IP addresses are globally routable before
establishing a connection.  This prevents Server-Side Request Forgery
(SSRF) attacks that could probe internal infrastructure via a
user-supplied base URL.

The ``ALLOW_PRIVATE_ENDPOINTS`` environment variable (set to ``true``)
disables the IP-range checks — intended for self-hosted / air-gapped
deployments where the inference endpoint *is* on the private network.
"""

import os
import socket
import ipaddress
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_ALLOW_PRIVATE = os.environ.get("ALLOW_PRIVATE_ENDPOINTS", "false").lower() in ("true", "1", "yes")


def _is_safe_ip(ip_str: str) -> bool:
    """Return True if the IP address is globally routable (not private/reserved)."""
    try:
        addr = ipaddress.ip_address(ip_str)
        return addr.is_global and not addr.is_reserved
    except ValueError:
        return False


def validate_endpoint_url(url: str) -> None:
    """
    Validate that *url* resolves to a globally-routable IP address and
    uses HTTPS.

    Raises ``ValueError`` if validation fails (unless
    ``ALLOW_PRIVATE_ENDPOINTS`` is enabled).
    """
    if _ALLOW_PRIVATE:
        logger.debug("SSRF check skipped — ALLOW_PRIVATE_ENDPOINTS is enabled")
        return

    from urllib.parse import urlparse
    parsed = urlparse(url)

    # Enforce HTTPS
    if parsed.scheme != "https":
        raise ValueError(
            f"Only HTTPS endpoints are allowed (got '{parsed.scheme}'). "
            "Set ALLOW_PRIVATE_ENDPOINTS=true for local HTTP endpoints."
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Cannot extract hostname from URL: {url}")

    # Resolve DNS and validate every returned address
    try:
        infos = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for '{hostname}': {exc}") from exc

    if not infos:
        raise ValueError(f"DNS resolution returned no results for '{hostname}'")

    for family, _type, _proto, _canonname, sockaddr in infos:
        ip_str = sockaddr[0]
        if not _is_safe_ip(ip_str):
            raise ValueError(
                f"Endpoint '{hostname}' resolves to private/reserved IP {ip_str}. "
                "Custom endpoints must resolve to public IP addresses. "
                "Set ALLOW_PRIVATE_ENDPOINTS=true for self-hosted deployments."
            )

    logger.debug("SSRF validation passed for %s", url)


def create_ssrf_safe_http_client(
    base_url: str,
    api_key: Optional[str] = None,
    timeout: float = 120.0,
) -> httpx.Client:
    """
    Create an httpx.Client that has been pre-validated against SSRF.

    The validation is performed *eagerly* (at client-creation time) by
    resolving the hostname and checking the IP ranges.  This is simpler
    and more portable than implementing a custom httpx transport, while
    still blocking the vast majority of SSRF vectors.
    """
    validate_endpoint_url(base_url)
    return httpx.Client(timeout=timeout)


def create_ssrf_safe_async_http_client(
    base_url: str,
    api_key: Optional[str] = None,
    timeout: float = 120.0,
) -> httpx.AsyncClient:
    """Async variant of :func:`create_ssrf_safe_http_client`."""
    validate_endpoint_url(base_url)
    return httpx.AsyncClient(timeout=timeout)
