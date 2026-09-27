"""Exercise HTTPX/HTTPCore with fake sockets, real request and TLS routing."""
import socket
from unittest.mock import AsyncMock, MagicMock

import httpcore
import httpx
import pytest

from llm import ssrf_safe_transport as transport


def addresses(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443)) for ip in ips]


@pytest.fixture(autouse=True)
def enforce_public_endpoints(monkeypatch):
    monkeypatch.setattr(transport, "_ALLOW_PRIVATE", False)


class SocketStream(httpcore.NetworkStream):
    def __init__(self):
        self.response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
        self.tls_hosts = []
        self.written = []

    def read(self, max_bytes, timeout=None):
        response, self.response = self.response, b""
        return response

    def write(self, buffer, timeout=None):
        self.written.append(buffer)

    def close(self):
        pass

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls_hosts.append(server_hostname)
        return self


class AsyncSocketStream(httpcore.AsyncNetworkStream):
    def __init__(self):
        self.sync = SocketStream()

    async def read(self, max_bytes, timeout=None):
        return self.sync.read(max_bytes, timeout)

    async def write(self, buffer, timeout=None):
        self.sync.write(buffer, timeout)

    async def aclose(self):
        pass

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.sync.start_tls(ssl_context, server_hostname, timeout)
        return self


def test_sync_dns_rebinding_is_checked_again_at_socket_connection(monkeypatch):
    resolver = MagicMock(side_effect=[addresses("8.8.8.8"), addresses("127.0.0.1")])
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    backend = MagicMock()
    monkeypatch.setattr(httpcore, "SyncBackend", lambda: backend)
    with transport.create_ssrf_safe_http_client("https://provider.test/v1") as client:
        with pytest.raises(ValueError, match="private/reserved"):
            client.get("https://provider.test/v1/models")
    backend.connect_tcp.assert_not_called()


def test_sync_pins_socket_ip_preserving_tls_hostname_and_http_host(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: addresses("8.8.8.8"))
    stream = SocketStream()
    backend = MagicMock(connect_tcp=MagicMock(return_value=stream))
    monkeypatch.setattr(httpcore, "SyncBackend", lambda: backend)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    with transport.create_ssrf_safe_http_client("https://provider.test/v1") as client:
        response = client.get("https://provider.test/v1/models")
    assert response.text == "ok"
    assert backend.connect_tcp.call_args.args == ("8.8.8.8", 443)
    assert stream.tls_hosts == ["provider.test"]
    assert b"Host: provider.test" in b"".join(stream.written)


@pytest.mark.asyncio
async def test_async_rebinding_is_checked_at_socket_connection(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: addresses("8.8.8.8"))
    monkeypatch.setattr(transport.anyio, "getaddrinfo", AsyncMock(return_value=addresses("169.254.169.254")))
    backend = MagicMock(connect_tcp=AsyncMock())
    monkeypatch.setattr(httpcore, "AnyIOBackend", lambda: backend)
    async with transport.create_ssrf_safe_async_http_client("https://provider.test/v1") as client:
        with pytest.raises(ValueError, match="private/reserved"):
            await client.get("https://provider.test/v1/models")
    backend.connect_tcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_ip_pinning_preserves_tls_and_falls_back_between_public_ips(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: addresses("8.8.8.8"))
    monkeypatch.setattr(transport.anyio, "getaddrinfo", AsyncMock(return_value=addresses("8.8.8.8", "1.1.1.1")))
    stream = AsyncSocketStream()
    backend = MagicMock(connect_tcp=AsyncMock(side_effect=[httpcore.ConnectError("unreachable"), stream]))
    monkeypatch.setattr(httpcore, "AnyIOBackend", lambda: backend)
    async with transport.create_ssrf_safe_async_http_client("https://provider.test/v1") as client:
        response = await client.get("https://provider.test/v1/models")
    assert response.text == "ok"
    assert [call.args for call in backend.connect_tcp.await_args_list] == [("8.8.8.8", 443), ("1.1.1.1", 443)]
    assert stream.sync.tls_hosts == ["provider.test"]
    assert b"Host: provider.test" in b"".join(stream.sync.written)


@pytest.mark.asyncio
async def test_async_dns_resolution_obeys_connection_deadline(monkeypatch):
    async def slow_resolver(*args, **kwargs):
        await transport.anyio.sleep_forever()
    monkeypatch.setattr(transport.anyio, "getaddrinfo", slow_resolver)
    backend = transport.ValidatingAsyncNetworkBackend(MagicMock(connect_tcp=AsyncMock()))
    with pytest.raises(httpcore.ConnectTimeout):
        await backend.connect_tcp("provider.test", 443, timeout=0.01)
    backend._backend.connect_tcp.assert_not_awaited()


def test_self_hosted_opt_out_retains_standard_transport(monkeypatch):
    monkeypatch.setattr(transport, "_ALLOW_PRIVATE", True)
    with transport.create_ssrf_safe_http_client("http://127.0.0.1:1234") as client:
        assert type(client._transport) is httpx.HTTPTransport


def test_invalid_url_diagnostic_does_not_include_credentials():
    with pytest.raises(ValueError) as error:
        transport.validate_endpoint_url("https:///secret-token-sentinel")
    assert "secret-token-sentinel" not in str(error.value)
