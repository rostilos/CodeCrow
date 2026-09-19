"""Unit tests for structural and exact proposed-tree RAG queries."""

import asyncio
import json

import httpx
import pytest
import respx

import service.rag.rag_client as rag_client_module
from service.rag.rag_client import RagClient


def _review_binding():
    return {
        "workspace": "tenant",
        "project": "repository",
        "target_branch": "main",
        "base_revision": "target-head",
        "source_revision": "source-head",
        "target_repo_path": "/tmp/target-snapshot",
        "review_overlay_path": "/tmp/review-overlay",
        "focus_paths": ["src/payment.py"],
        "base_collection_target": "sealed-base-target",
        "base_generation_manifest_sha256": "a" * 64,
        "review_collection_target": "sealed-review-target",
        "review_generation_manifest_sha256": "b" * 64,
    }


async def _call_review_operation(client, operation):
    binding = _review_binding()
    if operation == "minimal":
        return await client.minimal_review_context(
            question="Review authorization",
            focus_symbols=["authorize"],
            **binding,
        )
    if operation == "impact":
        return await client.review_impact_radius(
            targets=["PaymentService.authorize"],
            max_depth=4,
            max_results=73,
            include_source=True,
            **binding,
        )
    if operation == "traverse":
        return await client.traverse_review_graph(
            start="PaymentService.authorize",
            direction="incoming",
            strategy="dfs",
            relation_kinds=["CALLS"],
            max_depth=5,
            max_results=61,
            **binding,
        )
    if operation == "query":
        return await client.query_review_graph(
            pattern="callers_of",
            target="PaymentService.authorize",
            max_results=19,
            include_source=True,
            **binding,
        )
    if operation == "unit":
        return await client.get_review_structural_unit(
            unit_id="unit:payment",
            **binding,
        )
    raise AssertionError(f"unknown operation: {operation}")


_REVIEW_ENDPOINTS = {
    "minimal": "/query/review-minimal-context",
    "impact": "/query/review-impact-radius",
    "traverse": "/query/review-traverse",
    "query": "/query/review-graph",
    "unit": "/query/review-unit",
}

_REVIEW_RESULT_KEYS = {
    "minimal": {
        "status", "operation", "snapshot", "question", "focusPaths",
        "focusSymbols", "summary", "nodes", "edges", "sourceWindows",
        "coverage", "nextOperations",
    },
    "impact": {
        "status", "operation", "snapshot", "targets", "unresolvedTargets",
        "roots", "nodes", "edges", "connections", "impactScores",
        "impactedFiles", "frontier", "sourceWindows", "coverage",
        "scorePolicy",
    },
    "traverse": {
        "status", "operation", "snapshot", "targets", "unresolvedTargets",
        "roots", "nodes", "edges", "frontier", "sourceWindows",
        "coverage", "strategy", "direction", "relationKinds",
    },
    "query": {
        "status", "operation", "snapshot", "pattern", "target", "results",
        "cursor", "nextCursor", "sourceWindows", "coverage",
    },
    "unit": {
        "status", "operation", "snapshot", "unit", "sourceEvidence",
    },
}


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_review_generation_is_prepared_once_before_read_queries(monkeypatch):
    monkeypatch.setenv("RAG_REVIEW_PREPARATION_TIMEOUT_SECONDS", "321")
    route = respx.post("http://rag:8001/query/review-generation").mock(
        return_value=httpx.Response(200, json={
            "status": "ready",
            "collection_target": "sealed-review-target",
            "generation_manifest_sha256": "b" * 64,
            "source_revision": "source-head",
            "cache_hit": False,
        })
    )
    client = RagClient(base_url="http://rag:8001", enabled=True)

    result = await client.prepare_review_generation(
        workspace="tenant",
        project="repository",
        target_branch="main",
        base_revision="target-head",
        source_revision="source-head",
        target_repo_path="/tmp/target-snapshot",
        review_overlay_path="/tmp/review-overlay",
        base_collection_target="sealed-base-target",
        base_generation_manifest_sha256="a" * 64,
    )

    assert result["generation_manifest_sha256"] == "b" * 64
    payload = json.loads(route.calls.last.request.content)
    assert "focus_paths" not in payload
    assert "review_collection_target" not in payload
    assert payload["base_collection_target"] == "sealed-base-target"
    assert route.calls.last.request.extensions["timeout"]["read"] == 321.0
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_default_url_uses_deployment_owned_container(monkeypatch):
    monkeypatch.delenv("RAG_API_URL", raising=False)

    client = RagClient(enabled=False)

    assert client.base_url == "http://codecrow-rag-pipeline:8001"
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_disabled_client_returns_empty_search():
    client = RagClient(base_url="http://rag:8001", enabled=False)

    assert await client.search_code("q", "ws", "proj", "main") == {
        "results": []
    }
    assert await client.is_healthy() is False


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_code_search_forwards_exact_generation_binding():
    route = respx.post("http://rag:8001/query/code-search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client = RagClient(base_url="http://rag:8001", enabled=True)

    await client.search_code(
        "query",
        "ws",
        "proj",
        "main",
        top_k=17,
        repository_revision="abc123",
        repository_generation_manifest_sha256="receipt",
        collection_target="generation-collection",
    )

    payload = route.calls.last.request.content
    assert b'"repository_revision":"abc123"' in payload
    assert b'"repository_generation_manifest_sha256":"receipt"' in payload
    assert b'"collection_target":"generation-collection"' in payload
    assert b'"limit":17' in payload
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_structural_relations_forwards_exact_generation_binding():
    route = respx.post("http://rag:8001/query/relations").mock(
        return_value=httpx.Response(200, json={"anchors": [], "relations": []})
    )
    client = RagClient(base_url="http://rag:8001", enabled=True)

    await client.get_structural_relations(
        paths=["src/auth.py"],
        workspace="ws",
        project="proj",
        branch="main",
        max_relations=37,
        repository_revision="abc123",
        repository_generation_manifest_sha256="a" * 64,
        collection_target="generation-target",
    )

    payload = route.calls.last.request.content
    assert b'"paths":["src/auth.py"]' in payload
    assert b'"repository_revision":"abc123"' in payload
    assert b'"max_relations":37' in payload
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_review_context_forwards_exact_proposed_tree_binding(monkeypatch):
    monkeypatch.setenv("REVIEW_TIMEOUT_SECONDS", "7200")
    monkeypatch.setenv("RAG_REVIEW_CONTEXT_TIMEOUT_SECONDS", "123")
    route = respx.post("http://rag:8001/query/review-context").mock(
        return_value=httpx.Response(200, json={
            "status": "ready",
            "snapshot": {
                "kind": "proposed_tree",
                "sourceRevision": "source-head",
            },
            "evidence": {"relations": []},
        })
    )
    client = RagClient(base_url="http://rag:8001", enabled=True)

    result = await client.explore_review_context(
        workspace="tenant",
        project="repository",
        target_branch="main",
        base_revision="target-head",
        source_revision="source-head",
        target_repo_path="/tmp/target-snapshot",
        review_overlay_path="/tmp/review-overlay",
        focus_paths=["src/payment.py"],
        question="Can this bypass authorization?",
        base_collection_target="sealed-base-target",
        base_generation_manifest_sha256="a" * 64,
        review_collection_target="sealed-review-target",
        review_generation_manifest_sha256="b" * 64,
        focus_symbols=["PaymentService.authorize"],
        include_patterns=["src/**"],
        exclude_patterns=["vendor/**"],
        project_type="python",
        source_root="src",
        max_relations=37,
        max_source_windows=6,
        max_source_characters=12000,
    )

    assert result["snapshot"]["kind"] == "proposed_tree"
    payload = json.loads(route.calls.last.request.content)
    assert payload == {
        "workspace": "tenant",
        "project": "repository",
        "target_branch": "main",
        "base_revision": "target-head",
        "source_revision": "source-head",
        "target_repo_path": "/tmp/target-snapshot",
        "review_overlay_path": "/tmp/review-overlay",
        "focus_paths": ["src/payment.py"],
        "question": "Can this bypass authorization?",
        "base_collection_target": "sealed-base-target",
        "base_generation_manifest_sha256": "a" * 64,
        "review_collection_target": "sealed-review-target",
        "review_generation_manifest_sha256": "b" * 64,
        "focus_symbols": ["PaymentService.authorize"],
        "include_patterns": ["src/**"],
        "exclude_patterns": ["vendor/**"],
        "project_type": "python",
        "source_root": "src",
        "max_relations": 37,
        "max_source_windows": 6,
        "max_source_characters": 12000,
    }
    assert route.calls.last.request.extensions["timeout"] == {
        "connect": 123.0,
        "read": 123.0,
        "write": 123.0,
        "pool": 123.0,
    }
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_review_context_has_independent_bounded_default_timeout(monkeypatch):
    monkeypatch.delenv("RAG_REVIEW_CONTEXT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("REVIEW_TIMEOUT_SECONDS", "7200")
    route = respx.post("http://rag:8001/query/review-context").mock(
        return_value=httpx.Response(200, json={"status": "ready"})
    )
    client = RagClient(base_url="http://rag:8001", enabled=True)

    await client.explore_review_context(
        workspace="tenant",
        project="repository",
        target_branch="main",
        base_revision="target-head",
        source_revision="source-head",
        target_repo_path="/tmp/target-snapshot",
        review_overlay_path="/tmp/review-overlay",
        focus_paths=["src/payment.py"],
        question="Review the change",
    )

    assert route.calls.last.request.extensions["timeout"] == {
        "connect": 120.0,
        "read": 120.0,
        "write": 120.0,
        "pool": 120.0,
    }
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_review_context_failure_is_a_structured_optional_observation(
    monkeypatch,
):
    warning = []
    monkeypatch.setattr(
        rag_client_module.logger,
        "warning",
        lambda *arguments: warning.append(arguments),
    )
    route = respx.post("http://rag:8001/query/review-context").mock(
        return_value=httpx.Response(
            409,
            json={"detail": "changed source body is unavailable"},
        )
    )
    client = RagClient(base_url="http://rag:8001", enabled=True)

    result = await client.explore_review_context(
        workspace="tenant",
        project="repository",
        target_branch="main",
        base_revision="target-head",
        source_revision="source-head",
        target_repo_path="/tmp/target-snapshot",
        review_overlay_path="/tmp/review-overlay",
        focus_paths=["src/payment.py"],
        question="Review the change",
    )

    assert result["status"] == "error"
    assert result["status_code"] == 409
    assert result["error"] == "changed source body is unavailable"
    assert result["evidence"] == {"relations": []}
    assert result["coverage"]["treeState"] == "unavailable"
    assert route.call_count == 1
    assert warning == [(
        "Proposed-tree %s failed: status=%s detail=%s",
        "review context",
        409,
        "changed source body is unavailable",
    )]
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_review_context_retries_only_active_mutation_conflict(monkeypatch):
    warning = []
    monkeypatch.setattr(
        rag_client_module.logger,
        "warning",
        lambda *arguments: warning.append(arguments),
    )
    monkeypatch.setattr(
        rag_client_module,
        "_REVIEW_CONTEXT_MUTATION_RETRY_SECONDS",
        0,
    )
    route = respx.post("http://rag:8001/query/review-context").mock(
        side_effect=[
            httpx.Response(
                409,
                json={
                    "detail": (
                        "another RAG mutation is active for tenant/repository "
                        "collection cc_review_g_abc"
                    )
                },
            ),
            httpx.Response(
                409,
                json={
                    "detail": (
                        "another RAG mutation is active for tenant/repository "
                        "collection cc_review_g_abc"
                    )
                },
            ),
            httpx.Response(
                409,
                json={
                    "detail": (
                        "another RAG mutation is active for tenant/repository "
                        "collection cc_review_g_abc"
                    )
                },
            ),
            httpx.Response(
                200,
                json={
                    "status": "ready",
                    "snapshot": {"kind": "proposed_tree"},
                },
            ),
        ]
    )
    client = RagClient(base_url="http://rag:8001", enabled=True)

    result = await client.explore_review_context(
        workspace="tenant",
        project="repository",
        target_branch="main",
        base_revision="target-head",
        source_revision="source-head",
        target_repo_path="/tmp/target-snapshot",
        review_overlay_path="/tmp/review-overlay",
        focus_paths=["src/payment.py"],
        question="Review the change",
    )

    assert route.call_count == 4
    assert result == {
        "status": "ready",
        "snapshot": {"kind": "proposed_tree"},
    }
    assert warning == [(
        "Waiting for active RAG mutation before retrying proposed-tree "
        "%s: workspace=%s project=%s status=%s detail=%s",
        "review context",
        "tenant",
        "repository",
        409,
        (
            "another RAG mutation is active for tenant/repository "
            "collection cc_review_g_abc"
        ),
    )]
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_review_context_logs_and_preserves_transport_failure(monkeypatch):
    warning = []
    monkeypatch.setattr(
        rag_client_module.logger,
        "warning",
        lambda *arguments, **keywords: warning.append((arguments, keywords)),
    )
    respx.post("http://rag:8001/query/review-context").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    client = RagClient(base_url="http://rag:8001", enabled=True)

    result = await client.explore_review_context(
        workspace="tenant",
        project="repository",
        target_branch="main",
        base_revision="target-head",
        source_revision="source-head",
        target_repo_path="/tmp/target-snapshot",
        review_overlay_path="/tmp/review-overlay",
        focus_paths=["src/payment.py"],
        question="Review the change",
    )

    assert result["status"] == "error"
    assert result["status_code"] is None
    assert result["error"] == "connection refused"
    assert warning == [((
        "Proposed-tree %s failed: status=%s detail=%s",
        "review context",
        "transport-error",
        "connection refused",
    ), {})]
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_proposed_tree_query_methods_use_review_bound_endpoints():
    endpoints = (
        "/query/review-minimal-context",
        "/query/review-impact-radius",
        "/query/review-traverse",
        "/query/review-graph",
        "/query/review-unit",
    )
    routes = {
        endpoint: respx.post(f"http://rag:8001{endpoint}").mock(
            return_value=httpx.Response(200, json={"status": "ready"})
        )
        for endpoint in endpoints
    }
    client = RagClient(base_url="http://rag:8001", enabled=True)
    binding = {
        "workspace": "tenant",
        "project": "repository",
        "target_branch": "main",
        "base_revision": "target-head",
        "source_revision": "source-head",
        "target_repo_path": "/tmp/target-snapshot",
        "review_overlay_path": "/tmp/review-overlay",
        "focus_paths": ["src/payment.py"],
        "base_collection_target": "sealed-base-target",
        "base_generation_manifest_sha256": "a" * 64,
        "review_collection_target": "sealed-review-target",
        "review_generation_manifest_sha256": "b" * 64,
        "include_patterns": ["src/**"],
        "exclude_patterns": ["vendor/**"],
        "project_type": "python",
        "source_root": "src",
    }

    await client.minimal_review_context(
        question="Review authorization",
        focus_symbols=["authorize"],
        **binding,
    )
    await client.review_impact_radius(
        targets=["PaymentService.authorize"],
        max_depth=2,
        **binding,
    )
    await client.traverse_review_graph(
        start="PaymentService.authorize",
        direction="incoming",
        relation_kinds=["CALLS"],
        token_budget=777,
        **binding,
    )
    await client.query_review_graph(
        pattern="callers_of",
        target="PaymentService.authorize",
        cursor=37,
        **binding,
    )
    await client.get_review_structural_unit(
        unit_id="unit:payment",
        offset=4096,
        max_characters=2048,
        **binding,
    )

    for endpoint, route in routes.items():
        assert route.called, endpoint
        payload = json.loads(route.calls.last.request.content)
        assert payload["workspace"] == "tenant"
        assert payload["source_revision"] == "source-head"
        assert payload["focus_paths"] == ["src/payment.py"]
        assert payload["review_overlay_path"] == "/tmp/review-overlay"
    assert json.loads(
        routes["/query/review-minimal-context"].calls.last.request.content
    )["question"] == "Review authorization"
    assert json.loads(
        routes["/query/review-impact-radius"].calls.last.request.content
    )["targets"] == ["PaymentService.authorize"]
    traversal_payload = json.loads(
        routes["/query/review-traverse"].calls.last.request.content
    )
    assert traversal_payload["direction"] == "incoming"
    assert traversal_payload["token_budget"] == 777
    query_payload = json.loads(
        routes["/query/review-graph"].calls.last.request.content
    )
    assert query_payload["pattern"] == "callers_of"
    assert query_payload["cursor"] == 37
    unit_payload = json.loads(
        routes["/query/review-unit"].calls.last.request.content
    )
    assert unit_payload["unit_id"] == "unit:payment"
    assert unit_payload["offset"] == 4096
    assert unit_payload["max_characters"] == 2048
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("operation", tuple(_REVIEW_ENDPOINTS))
async def test_disabled_proposed_tree_methods_return_stable_unavailable_shapes(
    operation,
):
    client = RagClient(base_url="http://rag:8001", enabled=False)

    result = await _call_review_operation(client, operation)

    assert set(result) == _REVIEW_RESULT_KEYS[operation]
    assert result["status"] == "unavailable"
    assert result["snapshot"] == {}
    if operation != "unit":
        assert result["coverage"]["state"] == "unavailable"
        assert result["coverage"]["truncated"] is True
    else:
        assert result["unit"] is None
        assert result["sourceEvidence"] is False
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("operation", tuple(_REVIEW_ENDPOINTS))
@pytest.mark.parametrize("status", ("error", "unavailable"))
@respx.mock
async def test_http_200_degraded_observations_keep_operation_shape(
    operation,
    status,
):
    response = {
        "status": status,
        "coverage": {"state": "complete"},
    }
    if status == "error":
        response["error"] = "graph operation failed"
    respx.post(
        f"http://rag:8001{_REVIEW_ENDPOINTS[operation]}"
    ).mock(return_value=httpx.Response(200, json=response))
    client = RagClient(base_url="http://rag:8001", enabled=True)

    result = await _call_review_operation(client, operation)

    assert _REVIEW_RESULT_KEYS[operation].issubset(result)
    assert result["status"] == status
    if operation != "unit":
        assert result["coverage"]["state"] == "unavailable"
    if status == "error":
        assert result["error"] == "graph operation failed"
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("operation", "status_code"),
    (("impact", 409), ("unit", 404), ("query", 503)),
)
@respx.mock
async def test_http_failures_keep_operation_specific_empty_shape(
    operation,
    status_code,
):
    respx.post(
        f"http://rag:8001{_REVIEW_ENDPOINTS[operation]}"
    ).mock(return_value=httpx.Response(
        status_code,
        json={"detail": f"structural failure {status_code}"},
    ))
    client = RagClient(base_url="http://rag:8001", enabled=True)

    result = await _call_review_operation(client, operation)

    assert _REVIEW_RESULT_KEYS[operation].issubset(result)
    assert result["status"] == "error"
    assert result["status_code"] == status_code
    assert result["error"] == f"structural failure {status_code}"
    if operation != "unit":
        assert result["coverage"]["state"] == "unavailable"
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_review_query_outer_timeout_keeps_minimal_context_shape(
    monkeypatch,
):
    client = RagClient(base_url="http://rag:8001", enabled=True)

    async def stalled_query(*_args, **_kwargs):
        await asyncio.sleep(1)
        raise AssertionError("timeout should cancel the stalled query")

    monkeypatch.setattr(client, "_post_structural_query", stalled_query)
    monkeypatch.setattr(
        client,
        "_review_query_timeout_seconds",
        lambda: 0.001,
    )

    result = await _call_review_operation(client, "minimal")

    assert _REVIEW_RESULT_KEYS["minimal"].issubset(result)
    assert result["status"] == "error"
    assert result["status_code"] is None
    assert result["coverage"]["state"] == "unavailable"
    assert result["summary"] == ""
    assert result["error"] == (
        "proposed-tree minimal review context timed out after 0.001 seconds"
    )
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_query_client_is_reused_and_closed():
    respx.get("http://rag:8001/health").mock(
        return_value=httpx.Response(200)
    )
    client = RagClient(base_url="http://rag:8001", enabled=True)

    assert await client.is_healthy() is True
    query_client = client._client
    assert await client.is_healthy() is True
    assert client._client is query_client
    await client.close()
    assert query_client is not None and query_client.is_closed
