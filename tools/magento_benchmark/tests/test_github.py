from __future__ import annotations

import urllib.error

import pytest

from magento2_benchmark.curation import (
    REVIEW_THREADS_QUERY,
    _graphql_review_threads,
)
from magento2_benchmark.github import GitHubClient
from magento2_benchmark.util import (
    canonical_json,
    sha256_text,
    write_json,
)


def test_offline_client_returns_cached_get_without_network(tmp_path, monkeypatch):
    client = GitHubClient(cache_dir=tmp_path, offline=True)
    url = "https://api.github.com/repos/magento/magento2?state=all"
    cache_path = client._cache_path("GET", url)
    assert cache_path is not None
    write_json(
        cache_path,
        {
            "url": url,
            "status": 200,
            "etag": '"fixture"',
            "headers": {"ETag": '"fixture"'},
            "value": {"full_name": "magento/magento2"},
        },
    )
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
        {
            "url": url,
            "status": 200,
            "etag": '"fixture"',
            "headers": {},
            "value": {"cached": True},
        },
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
