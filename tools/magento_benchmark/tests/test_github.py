from __future__ import annotations

import io
import urllib.error
from email.message import Message

import pytest

from magento2_benchmark.curation import (
    REVIEW_THREADS_QUERY,
    _graphql_review_threads,
)
from magento2_benchmark.github import (
    GITHUB_REST_GET_CACHE_KIND,
    GITHUB_REST_GET_CACHE_SCHEMA,
    GitHubClient,
)
from magento2_benchmark.util import (
    canonical_json,
    read_json,
    sha256_text,
    write_json,
)


class _Response:
    def __init__(self, value: bytes, *, status: int = 200, etag: str = '"fresh"'):
        self._value = value
        self.status = status
        self.headers = Message()
        self.headers["ETag"] = etag
        self.headers["Last-Modified"] = "Wed, 29 Jul 2026 00:00:00 GMT"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._value


def _rest_cache_envelope(
    url,
    *,
    value=None,
    status=200,
    fetched_at="2026-07-29T12:00:00Z",
    etag='"fixture"',
):
    if value is None:
        value = {"full_name": "magento/magento2"}
    headers = {} if etag is None else {"ETag": etag}
    envelope = {
        "kind": GITHUB_REST_GET_CACHE_KIND,
        "schema": GITHUB_REST_GET_CACHE_SCHEMA,
        "method": "GET",
        "url": url,
        "status": status,
        "fetchedAt": fetched_at,
        "etag": etag,
        "headers": headers,
        "value": value,
        "responseSha256": sha256_text(canonical_json(value)),
    }
    envelope["envelopeSha256"] = sha256_text(canonical_json(envelope))
    return envelope


def _resign(envelope):
    envelope.pop("envelopeSha256", None)
    envelope["envelopeSha256"] = sha256_text(canonical_json(envelope))


def test_offline_client_returns_cached_get_without_network(tmp_path, monkeypatch):
    client = GitHubClient(cache_dir=tmp_path, offline=True)
    url = "https://api.github.com/repos/magento/magento2?state=all"
    cache_path = client._cache_path("GET", url)
    assert cache_path is not None
    write_json(cache_path, _rest_cache_envelope(url))
    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("offline mode attempted network"),
    )

    response = client.request(
        "GET",
        "/repos/magento/magento2",
        query={"state": "all"},
    )

    assert response.value == {"full_name": "magento/magento2"}
    assert response.status == 200
    assert response.url == url
    assert response.fetched_at == "2026-07-29T12:00:00Z"
    assert response.cache_envelope_digest == read_json(cache_path)[
        "envelopeSha256"
    ]
    assert response.cache_envelope == read_json(cache_path)


def test_offline_cache_miss_is_explicit_and_does_not_attempt_network(
    tmp_path,
    monkeypatch,
):
    client = GitHubClient(cache_dir=tmp_path, offline=True)
    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("offline mode attempted network"),
    )

    with pytest.raises(RuntimeError, match="offline GitHub cache miss"):
        client.get("/repos/magento/magento2/pulls/999999")


@pytest.mark.parametrize(
    "invalidity",
    [
        "legacy",
        "malformed_json",
        "kind",
        "schema",
        "url",
        "method",
        "status_type",
        "status_range",
        "timestamp",
        "headers",
        "etag",
        "response_digest",
        "envelope_digest",
    ],
)
def test_offline_rejects_invalid_get_cache_without_network(
    tmp_path,
    monkeypatch,
    invalidity,
):
    client = GitHubClient(cache_dir=tmp_path, offline=True)
    url = "https://api.github.com/repos/magento/magento2"
    cache_path = client._cache_path("GET", url)
    assert cache_path is not None
    if invalidity == "legacy":
        write_json(
            cache_path,
            {
                "url": url,
                "status": 200,
                "etag": '"legacy"',
                "headers": {"ETag": '"legacy"'},
                "value": {"cached": True},
            },
        )
    elif invalidity == "malformed_json":
        cache_path.write_text("{", encoding="utf-8")
    else:
        envelope = _rest_cache_envelope(url)
        if invalidity == "kind":
            envelope["kind"] = "other-kind"
        elif invalidity == "schema":
            envelope["schema"] = "other-schema"
        elif invalidity == "url":
            envelope["url"] = f"{url}/different"
        elif invalidity == "method":
            envelope["method"] = "POST"
        elif invalidity == "status_type":
            envelope["status"] = "200"
        elif invalidity == "status_range":
            envelope["status"] = 304
        elif invalidity == "timestamp":
            envelope["fetchedAt"] = "2026-07-29T12:00:00+00:00"
        elif invalidity == "headers":
            envelope["headers"] = ["ETag", '"fixture"']
        elif invalidity == "etag":
            envelope["etag"] = '"different"'
        elif invalidity == "response_digest":
            envelope["responseSha256"] = "0" * 64
        elif invalidity == "envelope_digest":
            envelope["envelopeSha256"] = "0" * 64
        if invalidity != "envelope_digest":
            _resign(envelope)
        write_json(cache_path, envelope)
    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("offline mode attempted network"),
    )

    with pytest.raises(RuntimeError, match="invalid cached GitHub GET response"):
        client.get("/repos/magento/magento2")


def test_inspect_cached_get_only_returns_exact_valid_envelopes(
    tmp_path,
    monkeypatch,
):
    client = GitHubClient(cache_dir=tmp_path)
    url = "https://api.github.com/repos/magento/magento2?state=all"
    cache_path = client._cache_path("GET", url)
    assert cache_path is not None
    envelope = _rest_cache_envelope(
        url,
        value={"message": "Gone"},
        status=410,
    )
    write_json(cache_path, envelope)
    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("cache inspection attempted network"),
    )

    response = client.inspect_cached_get(
        "/repos/magento/magento2",
        query={"state": "all"},
    )

    assert response is not None
    assert response.status == 410
    assert response.url == url
    assert response.fetched_at == envelope["fetchedAt"]
    assert response.cache_envelope_digest == envelope["envelopeSha256"]
    assert response.cache_envelope == envelope
    assert (
        client.inspect_cached_get(
            "/repos/magento/magento2",
            query={"state": "open"},
        )
        is None
    )
    envelope["value"] = {"message": "tampered"}
    write_json(cache_path, envelope)
    assert (
        client.inspect_cached_get(
            "/repos/magento/magento2",
            query={"state": "all"},
        )
        is None
    )


@pytest.mark.parametrize("invalidity", ["legacy", "digest"])
def test_live_invalid_cache_fetches_unconditionally_and_replaces_it(
    tmp_path,
    monkeypatch,
    invalidity,
):
    client = GitHubClient(cache_dir=tmp_path)
    url = "https://api.github.com/repos/magento/magento2"
    cache_path = client._cache_path("GET", url)
    assert cache_path is not None
    if invalidity == "legacy":
        write_json(
            cache_path,
            {
                "url": url,
                "status": 200,
                "etag": '"legacy"',
                "headers": {"ETag": '"legacy"'},
                "value": {"cached": True},
            },
        )
    else:
        envelope = _rest_cache_envelope(url)
        envelope["envelopeSha256"] = "0" * 64
        write_json(cache_path, envelope)
    requests = []

    def fresh(request, **_kwargs):
        requests.append(request)
        assert request.get_header("If-none-match") is None
        return _Response(b'{"fresh":true}')

    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        fresh,
    )

    response = client.request("GET", "/repos/magento/magento2")

    assert len(requests) == 1
    assert response.value == {"fresh": True}
    assert response.url == url
    assert response.fetched_at is not None
    envelope = read_json(cache_path)
    assert envelope["kind"] == GITHUB_REST_GET_CACHE_KIND
    assert envelope["schema"] == GITHUB_REST_GET_CACHE_SCHEMA
    assert envelope["method"] == "GET"
    assert envelope["url"] == url
    assert envelope["status"] == 200
    assert envelope["etag"] == '"fresh"'
    assert envelope["responseSha256"] == sha256_text(
        canonical_json({"fresh": True})
    )
    envelope_digest = envelope.pop("envelopeSha256")
    assert envelope_digest == sha256_text(canonical_json(envelope))
    assert response.cache_envelope_digest == envelope_digest
    assert response.cache_envelope is not None
    assert response.cache_envelope["envelopeSha256"] == envelope_digest


def test_live_304_reuses_only_validated_cached_response(tmp_path, monkeypatch):
    client = GitHubClient(cache_dir=tmp_path)
    url = "https://api.github.com/repos/magento/magento2"
    cache_path = client._cache_path("GET", url)
    assert cache_path is not None
    envelope = _rest_cache_envelope(url, value={"cached": True})
    write_json(cache_path, envelope)

    def not_modified(request, **_kwargs):
        assert request.get_header("If-none-match") == '"fixture"'
        raise urllib.error.HTTPError(
            url,
            304,
            "Not Modified",
            Message(),
            io.BytesIO(b""),
        )

    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        not_modified,
    )

    response = client.request("GET", "/repos/magento/magento2")

    assert response.value == {"cached": True}
    assert response.status == 200
    assert response.url == url
    assert response.fetched_at == envelope["fetchedAt"]
    assert response.cache_envelope_digest == envelope["envelopeSha256"]
    assert response.cache_envelope == envelope


@pytest.mark.parametrize("cache_state", ["invalid", "without_etag"])
def test_live_304_requires_a_validated_cached_etag(
    tmp_path,
    monkeypatch,
    cache_state,
):
    client = GitHubClient(cache_dir=tmp_path)
    url = "https://api.github.com/repos/magento/magento2"
    cache_path = client._cache_path("GET", url)
    assert cache_path is not None
    envelope = _rest_cache_envelope(
        url,
        etag=None if cache_state == "without_etag" else '"fixture"',
    )
    if cache_state == "invalid":
        envelope["envelopeSha256"] = "0" * 64
    write_json(cache_path, envelope)

    def not_modified(request, **_kwargs):
        assert request.get_header("If-none-match") is None
        raise urllib.error.HTTPError(
            url,
            304,
            "Not Modified",
            Message(),
            io.BytesIO(b""),
        )

    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        not_modified,
    )

    with pytest.raises(RuntimeError, match="failed with HTTP 304"):
        client.get("/repos/magento/magento2")


def test_live_304_preserves_a_validated_cached_not_found(tmp_path, monkeypatch):
    client = GitHubClient(cache_dir=tmp_path)
    url = "https://api.github.com/repos/magento/magento2"
    cache_path = client._cache_path("GET", url)
    assert cache_path is not None
    write_json(
        cache_path,
        _rest_cache_envelope(
            url,
            value={"message": "Not Found"},
            status=404,
        ),
    )

    def not_modified(request, **_kwargs):
        assert request.get_header("If-none-match") == '"fixture"'
        raise urllib.error.HTTPError(
            url,
            304,
            "Not Modified",
            Message(),
            io.BytesIO(b""),
        )

    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        not_modified,
    )

    with pytest.raises(RuntimeError, match="failed with cached HTTP 404"):
        client.get("/repos/magento/magento2")


def test_network_failure_does_not_silently_substitute_existing_cache(
    tmp_path,
    monkeypatch,
):
    client = GitHubClient(cache_dir=tmp_path)
    url = "https://api.github.com/repos/magento/magento2"
    cache_path = client._cache_path("GET", url)
    assert cache_path is not None
    write_json(
        cache_path,
        _rest_cache_envelope(
            url,
            value={"cached": True},
            etag=None,
        ),
    )

    def unavailable(*args, **kwargs):
        raise urllib.error.URLError("network disabled in test")

    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        unavailable,
    )

    with pytest.raises(
        RuntimeError,
        match="never substituted.*explicit --offline",
    ):
        client.get("/repos/magento/magento2")


def test_live_not_found_is_cached_for_reproducible_offline_rejection(
    tmp_path,
    monkeypatch,
):
    client = GitHubClient(cache_dir=tmp_path)
    url = "https://api.github.com/repos/magento/magento2/pulls/comments/123"
    headers = Message()
    headers["X-RateLimit-Remaining"] = "42"

    def not_found(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            url,
            404,
            "Not Found",
            headers,
            io.BytesIO(b'{"message":"Not Found"}'),
        )

    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        not_found,
    )

    with pytest.raises(RuntimeError, match="failed with HTTP 404"):
        client.get("/repos/magento/magento2/pulls/comments/123")

    cache_path = client._cache_path("GET", url)
    assert cache_path is not None
    envelope = read_json(cache_path)
    assert envelope["status"] == 404
    assert envelope["method"] == "GET"
    assert envelope["url"] == url
    assert envelope["responseSha256"] == sha256_text(
        canonical_json({"message": "Not Found"})
    )
    envelope_digest = envelope.pop("envelopeSha256")
    assert envelope_digest == sha256_text(canonical_json(envelope))
    inspected = client.inspect_cached_get(url)
    assert inspected is not None
    assert inspected.status == 404
    assert inspected.cache_envelope_digest == envelope_digest
    offline = GitHubClient(cache_dir=tmp_path, offline=True)
    with pytest.raises(RuntimeError, match="failed with HTTP 404"):
        offline.get("/repos/magento/magento2/pulls/comments/123")


def test_mutations_require_token_before_request(monkeypatch):
    client = GitHubClient(token=None)
    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("mutation attempted network"),
    )

    with pytest.raises(RuntimeError, match="token is required"):
        client.create_ref("owner/repository", "benchmark/case/base", "a" * 40)


def test_tokenless_offline_graphql_uses_exact_cache_without_network(
    tmp_path,
    monkeypatch,
):
    client = GitHubClient(cache_dir=tmp_path, offline=True, token=None)
    variables = {
        "owner": "magento",
        "name": "magento2",
        "number": 12_345,
        "after": None,
    }
    payload = {
        "query": REVIEW_THREADS_QUERY,
        "variables": variables,
    }
    request_digest = sha256_text(canonical_json(payload))
    response = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {
                            "hasNextPage": False,
                            "endCursor": None,
                        },
                        "nodes": [],
                    }
                }
            }
        }
    }
    write_json(
        tmp_path / f"graphql-{request_digest}.json",
        {
            "requestDigest": request_digest,
            "request": payload,
            "fetchedAt": "2026-07-29T12:00:00Z",
            "value": response,
            "responseDigest": sha256_text(canonical_json(response)),
        },
    )
    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("offline mode attempted network"),
    )

    threads, archive = _graphql_review_threads(
        client,
        pull_request=12_345,
    )

    assert threads == {}
    assert archive is not None
    assert archive["pageCount"] == 1
    assert archive["pages"][0]["response"] == response


def test_tokenless_offline_graphql_cache_miss_is_explicit(
    tmp_path,
    monkeypatch,
):
    client = GitHubClient(cache_dir=tmp_path, offline=True, token=None)
    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("offline mode attempted network"),
    )

    with pytest.raises(
        RuntimeError,
        match="offline GitHub GraphQL cache miss",
    ):
        _graphql_review_threads(client, pull_request=12_345)


def test_live_graphql_still_requires_token_before_network(monkeypatch):
    client = GitHubClient(token=None)
    monkeypatch.setattr(
        "magento2_benchmark.github.urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("unauthenticated network request"),
    )

    with pytest.raises(RuntimeError, match="token is required"):
        client.graphql(REVIEW_THREADS_QUERY, {"number": 12_345})
