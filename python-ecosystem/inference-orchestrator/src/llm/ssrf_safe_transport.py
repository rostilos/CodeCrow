"""HTTP clients whose actual socket destinations satisfy the endpoint policy.

Resolve and validate every new connection, then connect to the selected numeric
address. The request URL remains unchanged, preserving HTTP Host and TLS SNI.
ALLOW_PRIVATE_ENDPOINTS keeps the existing self-hosted opt-out behavior.
"""
import ipaddress
import logging
import os
import socket
import time
from typing import Optional
from urllib.parse import urlparse

import anyio
import httpcore
import httpx

logger = logging.getLogger(__name__)
_ALLOW_PRIVATE = os.environ.get("ALLOW_PRIVATE_ENDPOINTS", "false").lower() in ("true", "1", "yes")


def _is_safe_ip(ip_str: str) -> bool:
    try:
        address = ipaddress.ip_address(ip_str)
        return address.is_global and not address.is_reserved
    except ValueError:
        return False


def _endpoint_host(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(
            f"Only HTTPS endpoints are allowed (got '{parsed.scheme}'). "
            "Set ALLOW_PRIVATE_ENDPOINTS=true for local HTTP endpoints."
        )
    if not parsed.hostname:
        raise ValueError("Cannot extract hostname from endpoint URL")
    return parsed.hostname, parsed.port or 443


def _public_addresses(infos: list, hostname: str) -> list[str]:
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise ValueError(f"DNS resolution returned no results for '{hostname}'")
    for address in addresses:
        if not _is_safe_ip(address):
            raise ValueError(
                f"Endpoint '{hostname}' resolves to private/reserved IP {address}. "
                "Custom endpoints must resolve to public IP addresses. "
                "Set ALLOW_PRIVATE_ENDPOINTS=true for self-hosted deployments."
            )
    return addresses


def validate_endpoint_url(url: str) -> None:
    """Retain the eager configuration check in addition to socket validation."""
    if _ALLOW_PRIVATE:
        return
    hostname, port = _endpoint_host(url)
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
        raise ValueError(f"DNS resolution failed for '{hostname}': {error}") from error
    _public_addresses(infos, hostname)
    logger.debug("SSRF validation passed for endpoint host %s", hostname)


def _remaining_timeout(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise httpcore.ConnectTimeout("Endpoint resolution/connection deadline exceeded")
    return remaining


class ValidatingNetworkBackend(httpcore.NetworkBackend):
    def __init__(self, backend=None):
        self._backend = backend if backend is not None else httpcore.SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        except socket.gaierror as error:
            raise httpcore.ConnectError(f"DNS resolution failed for '{host}'") from error
        addresses = _public_addresses(infos, host)
        for index, address in enumerate(addresses):
            try:
                return self._backend.connect_tcp(
                    address, port, timeout=_remaining_timeout(deadline),
                    local_address=local_address, socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout):
                if index == len(addresses) - 1:
                    raise

    def sleep(self, seconds):
        self._backend.sleep(seconds)


class ValidatingAsyncNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, backend=None):
        self._backend = backend if backend is not None else httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            # DNS runs through AnyIO's resolver instead of blocking the event
            # loop; its time and all address attempts share one connect budget.
            with anyio.fail_after(timeout):
                try:
                    infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
                except socket.gaierror as error:
                    raise httpcore.ConnectError(f"DNS resolution failed for '{host}'") from error
                addresses = _public_addresses(infos, host)
                for index, address in enumerate(addresses):
                    try:
                        return await self._backend.connect_tcp(
                            address, port, timeout=timeout,
                            local_address=local_address, socket_options=socket_options,
                        )
                    except (httpcore.ConnectError, httpcore.ConnectTimeout):
                        if index == len(addresses) - 1:
                            raise
        except TimeoutError as error:
            raise httpcore.ConnectTimeout("Endpoint resolution/connection deadline exceeded") from error

    async def sleep(self, seconds):
        await self._backend.sleep(seconds)


class ValidatingHTTPTransport(httpx.HTTPTransport):
    def __init__(self):
        super().__init__()
        # httpx exposes transports; httpcore exposes a network_backend hook.
        # Keep this single adapter seam isolated and exercise real HTTP/TLS
        # requests in tests so a dependency change cannot silently bypass it.
        self._pool._network_backend = ValidatingNetworkBackend()

    def handle_request(self, request):
        _endpoint_host(str(request.url))
        return super().handle_request(request)


class ValidatingAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self):
        super().__init__()
        self._pool._network_backend = ValidatingAsyncNetworkBackend()

    async def handle_async_request(self, request):
        _endpoint_host(str(request.url))
        return await super().handle_async_request(request)


def create_ssrf_safe_http_client(
    base_url: str,
    api_key: Optional[str] = None,
    timeout: float = 120.0,
) -> httpx.Client:
    validate_endpoint_url(base_url)
    if _ALLOW_PRIVATE:
        return httpx.Client(timeout=timeout)
    # Explicit transports also prevent environment proxy mounts from bypassing
    # destination validation by asking a proxy to resolve the origin instead.
    return httpx.Client(timeout=timeout, transport=ValidatingHTTPTransport())


def create_ssrf_safe_async_http_client(
    base_url: str,
    api_key: Optional[str] = None,
    timeout: float = 120.0,
) -> httpx.AsyncClient:
    validate_endpoint_url(base_url)
    if _ALLOW_PRIVATE:
        return httpx.AsyncClient(timeout=timeout)
    return httpx.AsyncClient(timeout=timeout, transport=ValidatingAsyncHTTPTransport())
