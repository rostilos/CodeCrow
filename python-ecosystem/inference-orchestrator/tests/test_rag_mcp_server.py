import pytest

from service.rag import rag_mcp_server


class _FakeRagClient:
    def __init__(self):
        self.calls = []
        self.closed = False

    async def get_structural_relations(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "anchors": [{"unitId": "unit:payment", "path": "src/example.py"}],
            "relations": [{
                "sourceUnitId": "unit:payment",
                "kind": "calls",
                "targetUnitId": "unit:policy",
            }],
        }

    async def explore_review_context(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": "ready",
            "snapshot": {
                "kind": "proposed_tree",
                "sourceRevision": kwargs["source_revision"],
            },
            "changed": {"paths": kwargs["focus_paths"]},
            "evidence": {"relations": []},
            "sourceWindows": [],
        }

    async def minimal_review_context(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "ready", "operation": "minimal_review_context"}

    async def review_impact_radius(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "ready", "operation": "impact_radius"}

    async def traverse_review_graph(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "ready", "operation": "traverse"}

    async def query_review_graph(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "ready", "operation": "graph_query"}

    async def get_review_structural_unit(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": "ready",
            "operation": "structural_unit",
            "unit": {
                "unitId": "unit:payment",
                "path": "src/example.py",
                "content": "exact proposed source",
                "contentSha256": "abc",
            },
            "sourceEvidence": True,
        }

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_fastmcp_schema_exposes_focused_graph_continuation_arguments():
    tools = {
        tool.name: tool
        for tool in await rag_mcp_server.server.list_tools()
    }

    assert {
        "getMinimalReviewContext",
        "getImpactRadius",
        "traverseCodeGraph",
        "queryCodeGraph",
        "getStructuralUnit",
        "exploreReviewContext",
    }.issubset(tools)
    token_budget = tools["traverseCodeGraph"].inputSchema["properties"][
        "tokenBudget"
    ]
    assert token_budget["default"] == 2_000
    assert token_budget["minimum"] == 512
    assert token_budget["maximum"] == 16_000
    relation_kinds = tools["traverseCodeGraph"].inputSchema["properties"][
        "relationKinds"
    ]
    relation_kind_items = next(
        branch["items"]
        for branch in relation_kinds["anyOf"]
        if branch.get("type") == "array"
    )
    assert relation_kind_items["maxLength"] == 128
    assert "cursor" in tools["queryCodeGraph"].inputSchema["properties"]
    assert tools["getMinimalReviewContext"].inputSchema["properties"][
        "includeSource"
    ]["default"] is True
    assert {
        "offset",
        "maxCharacters",
    }.issubset(tools["getStructuralUnit"].inputSchema["properties"])


@pytest.mark.asyncio
async def test_review_context_tool_uses_host_identity_and_bound_focus_paths(
    monkeypatch,
):
    fake = _FakeRagClient()
    monkeypatch.setattr(rag_mcp_server, "RagClient", lambda: fake)
    monkeypatch.setenv("CODECROW_RAG_MCP_WORKSPACE", "tenant")
    monkeypatch.setenv("CODECROW_RAG_MCP_PROJECT", "project")
    monkeypatch.setenv("CODECROW_RAG_MCP_BRANCH", "main")
    monkeypatch.setenv("CODECROW_RAG_MCP_REVISION", "target-head")
    monkeypatch.setenv("CODECROW_RAG_MCP_SOURCE_REVISION", "source-head")
    monkeypatch.setenv("CODECROW_RAG_MCP_MANIFEST", "base-manifest")
    monkeypatch.setenv(
        "CODECROW_RAG_MCP_COLLECTION_TARGET",
        "sealed-base-target",
    )
    monkeypatch.setenv(
        "CODECROW_RAG_MCP_REVIEW_COLLECTION_TARGET",
        "sealed-review-target",
    )
    monkeypatch.setenv(
        "CODECROW_RAG_MCP_REVIEW_GENERATION_MANIFEST_SHA256",
        "review-manifest",
    )
    monkeypatch.setenv(
        "CODECROW_RAG_MCP_TARGET_REPO_PATH",
        "/tmp/target-snapshot",
    )
    monkeypatch.setenv(
        "CODECROW_RAG_MCP_REVIEW_OVERLAY_PATH",
        "/tmp/review-overlay",
    )
    monkeypatch.setenv(
        "CODECROW_RAG_MCP_INCLUDE_PATTERNS_JSON",
        '["src/**"]',
    )
    monkeypatch.setenv(
        "CODECROW_RAG_MCP_EXCLUDE_PATTERNS_JSON",
        '["vendor/**"]',
    )
    monkeypatch.setenv("CODECROW_RAG_MCP_PROJECT_TYPE", "python")
    monkeypatch.setenv("CODECROW_RAG_MCP_SOURCE_ROOT", "src")

    result = await rag_mcp_server.explore_review_context(
        "Can this bypass authorization?",
        ["src/payment.py"],
        ["PaymentService.authorize"],
        37,
        6,
        12000,
    )

    assert result["snapshot"] == {
        "kind": "proposed_tree",
        "sourceRevision": "source-head",
    }
    assert fake.calls == [{
        "workspace": "tenant",
        "project": "project",
        "target_branch": "main",
        "base_revision": "target-head",
        "source_revision": "source-head",
        "target_repo_path": "/tmp/target-snapshot",
        "review_overlay_path": "/tmp/review-overlay",
        "base_collection_target": "sealed-base-target",
        "base_generation_manifest_sha256": "base-manifest",
        "review_collection_target": "sealed-review-target",
        "review_generation_manifest_sha256": "review-manifest",
        "focus_paths": ["src/payment.py"],
        "question": "Can this bypass authorization?",
        "focus_symbols": ["PaymentService.authorize"],
        "include_patterns": ["src/**"],
        "exclude_patterns": ["vendor/**"],
        "project_type": "python",
        "source_root": "src",
        "max_relations": 37,
        "max_source_windows": 6,
        "max_source_characters": 12000,
    }]
    assert fake.closed is True


@pytest.mark.asyncio
async def test_structural_relations_tool_uses_request_scoped_repository_binding(
    monkeypatch,
):
    fake = _FakeRagClient()
    monkeypatch.setattr(rag_mcp_server, "RagClient", lambda: fake)
    monkeypatch.setenv("CODECROW_RAG_MCP_WORKSPACE", "tenant")
    monkeypatch.setenv("CODECROW_RAG_MCP_PROJECT", "project")
    monkeypatch.setenv("CODECROW_RAG_MCP_BRANCH", "main")
    monkeypatch.setenv("CODECROW_RAG_MCP_REVISION", "abc123")
    monkeypatch.setenv("CODECROW_RAG_MCP_MANIFEST", "manifest")
    monkeypatch.setenv("CODECROW_RAG_MCP_COLLECTION_TARGET", "collection")

    result = await rag_mcp_server.get_structural_relations(
        ["src/example.py"],
        100,
    )

    assert result["anchors"] == [{
        "unitId": "unit:payment",
        "path": "src/example.py",
    }]
    assert result["relations"][0]["targetUnitId"] == "unit:policy"
    assert fake.calls == [{
        "paths": ["src/example.py"],
        "max_relations": 100,
        "workspace": "tenant",
        "project": "project",
        "branch": "main",
        "repository_revision": "abc123",
        "repository_generation_manifest_sha256": "manifest",
        "collection_target": "collection",
    }]
    assert fake.closed is True


@pytest.mark.asyncio
async def test_proposed_tree_tools_forward_host_binding_and_query_arguments(
    monkeypatch,
):
    fake = _FakeRagClient()
    monkeypatch.setattr(rag_mcp_server, "RagClient", lambda: fake)
    monkeypatch.setenv("CODECROW_RAG_MCP_WORKSPACE", "tenant")
    monkeypatch.setenv("CODECROW_RAG_MCP_PROJECT", "project")
    monkeypatch.setenv("CODECROW_RAG_MCP_BRANCH", "main")
    monkeypatch.setenv("CODECROW_RAG_MCP_REVISION", "target-head")
    monkeypatch.setenv("CODECROW_RAG_MCP_SOURCE_REVISION", "source-head")
    monkeypatch.setenv("CODECROW_RAG_MCP_TARGET_REPO_PATH", "/tmp/target")
    monkeypatch.setenv("CODECROW_RAG_MCP_REVIEW_OVERLAY_PATH", "/tmp/overlay")
    monkeypatch.setenv("CODECROW_RAG_MCP_MANIFEST", "base-manifest")
    monkeypatch.setenv("CODECROW_RAG_MCP_COLLECTION_TARGET", "sealed-base")
    monkeypatch.setenv(
        "CODECROW_RAG_MCP_REVIEW_COLLECTION_TARGET",
        "sealed-review",
    )
    monkeypatch.setenv(
        "CODECROW_RAG_MCP_REVIEW_GENERATION_MANIFEST_SHA256",
        "review-manifest",
    )

    minimal = await rag_mcp_server.get_minimal_review_context(
        "Review authorization",
        ["src/example.py"],
        ["authorize"],
    )
    impact = await rag_mcp_server.get_impact_radius(
        ["src/example.py"],
        ["PaymentService.authorize"],
        2,
    )
    traversal = await rag_mcp_server.traverse_code_graph(
        "PaymentService.authorize",
        ["src/example.py"],
        "incoming",
        "bfs",
        ["CALLS"],
        tokenBudget=777,
    )
    query = await rag_mcp_server.query_code_graph(
        "callers_of",
        "PaymentService.authorize",
        ["src/example.py"],
        cursor=37,
    )
    unit = await rag_mcp_server.get_structural_unit(
        "unit:payment",
        ["src/example.py"],
        offset=4096,
        maxCharacters=2048,
    )

    assert minimal["operation"] == "minimal_review_context"
    assert impact["operation"] == "impact_radius"
    assert traversal["operation"] == "traverse"
    assert query["operation"] == "graph_query"
    assert unit["unit"]["content"] == "exact proposed source"
    assert unit["sourceEvidence"] is True
    assert len(fake.calls) == 5
    for call in fake.calls:
        assert call["workspace"] == "tenant"
        assert call["source_revision"] == "source-head"
        assert call["focus_paths"] == ["src/example.py"]
        assert call["review_collection_target"] == "sealed-review"
        assert call["review_generation_manifest_sha256"] == "review-manifest"
    assert fake.calls[0]["question"] == "Review authorization"
    assert fake.calls[1]["targets"] == ["PaymentService.authorize"]
    assert fake.calls[2]["direction"] == "incoming"
    assert fake.calls[2]["token_budget"] == 777
    assert fake.calls[3]["pattern"] == "callers_of"
    assert fake.calls[3]["cursor"] == 37
    assert fake.calls[4]["unit_id"] == "unit:payment"
    assert fake.calls[4]["offset"] == 4096
    assert fake.calls[4]["max_characters"] == 2048
    assert fake.closed is True
