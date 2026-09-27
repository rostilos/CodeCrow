"""Request-bound graph/local evidence tools; no external model traffic."""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.verification_tools import LocalReviewSource, VerificationTools


@pytest.fixture
def tree(tmp_path):
    target = tmp_path / "target"
    overlay = tmp_path / "overlay"
    (target / "app" / "models").mkdir(parents=True)
    (target / "app" / "models_extra").mkdir()
    (target / "app" / "empty").mkdir()
    (target / "app" / "models" / "base.py").write_text("symbol = 1\n")
    (target / "app" / "models" / "edited.py").write_text("old_symbol = 1\n")
    (target / "app" / "models" / "removed.py").write_text("symbol = 2\n")
    (target / "app" / "models_extra" / "outside.py").write_text("symbol = 3\n")
    (overlay / "files" / "app" / "models").mkdir(parents=True)
    (overlay / "files" / "app" / "models" / "edited.py").write_text("new_symbol = 4\n")
    (overlay / "files" / "app" / "models" / "added.py").write_text("symbol = 5\n")
    changed = ["app/models/edited.py", "app/models/added.py", "app/models/removed.py"]
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": changed, "deletedFiles": ["app/models/removed.py"]}))
    return {"target_repo_path": str(target), "review_overlay_path": str(overlay)}


def test_grep_directory_scopes_expand_overlay_and_target_without_prefix_leak(tree):
    source = LocalReviewSource(tree)
    proposed = source.grep("symbol", paths=["app/models"])
    assert proposed["status"] == "ready"
    assert proposed["complete"] is True
    assert {item["path"] for item in proposed["results"]} == {
        "app/models/base.py", "app/models/edited.py", "app/models/added.py",
    }
    target = source.grep("symbol", paths=["app/models/"], side="target")
    assert {item["path"] for item in target["results"]} == {
        "app/models/base.py", "app/models/edited.py", "app/models/removed.py",
    }
    assert source.grep("old_symbol", paths=["app/models"])["results"] == []
    assert source.grep("new_symbol", paths=["app/models"])["results"]
    assert source.grep("symbol", paths=["app/empty"])["complete"] is True
    assert source.grep("symbol", paths=["app/gone"])["unavailablePaths"] == ["app/gone"]
    assert source.grep("symbol", paths=["."])["complete"] is True


def test_directory_grep_does_not_open_unrelated_subtrees(tree, monkeypatch):
    import os
    from service.review import local_source
    original_open = os.open

    def restricted_open(path, flags, *args, **kwargs):
        assert path != "models_extra"
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(local_source.os, "open", restricted_open)
    assert LocalReviewSource(tree).grep("symbol", paths=["app/models"])["status"] == "ready"


def test_directory_grep_rejects_links_and_escaping_scopes(tree, tmp_path):
    external = tmp_path / "other-tenant"
    external.mkdir()
    (external / "private.py").write_text("private_marker = True\n")
    (tmp_path / "target" / "app" / "models" / "escape").symlink_to(external, target_is_directory=True)
    source = LocalReviewSource(tree)
    assert not source.grep("private_marker", paths=["app/models"])["results"]
    assert source.grep("private_marker", paths=["app/models/escape"])["status"] == "partial"
    assert source.grep("private_marker", paths=["../other-tenant"])["status"] == "unavailable"
    assert source.grep("private_marker", paths=[str(external)])["status"] == "unavailable"


@pytest.mark.asyncio
async def test_graph_tools_preserve_host_binding_and_never_inline_navigation_source(tree):
    methods = {name: AsyncMock(return_value={"status": "ready"}) for name in (
        "query_review_graph", "minimal_review_context", "review_impact_radius", "traverse_review_graph",
    )}
    rag = SimpleNamespace(**methods)
    binding = {**tree, "workspace": "tenant", "project": "project", "review_collection_target": "sealed"}
    tools = VerificationTools(rag_client=rag, binding=binding, parts=[])
    calls = [("queryCodeGraph", {"pattern": "callers_of", "target": "fn"}, "query_review_graph"),
             ("getMinimalReviewContext", {"question": "caller contract", "paths": ["app/models"]}, "minimal_review_context"),
             ("getImpactRadius", {"targets": ["fn"]}, "review_impact_radius"),
             ("traverseCodeGraph", {"start": "fn"}, "traverse_review_graph")]
    for name, arguments, method in calls:
        assert (await tools.call(name, arguments))["status"] == "ready"
        received = methods[method].call_args.kwargs
        assert received["workspace"] == "tenant"
        assert received["review_collection_target"] == "sealed"
        assert received["include_source"] is False
    assert (await tools.call("readReviewFile", {"path": "app/models/base.py"}))["contentSha256"] == hashlib.sha256(b"symbol = 1\n").hexdigest()
    assert all("workspace" not in item["inputSchema"]["properties"] for item in await tools.schemas())


@pytest.mark.asyncio
async def test_structural_unit_reads_complete_local_definition(tree):
    rag = SimpleNamespace(get_review_structural_unit=AsyncMock(return_value={
        "status": "ready", "sourceEvidence": True,
        "unit": {"unitId": "unit", "path": "app/models/base.py", "startLine": 1, "endLine": 1,
                 "content": "clipped", "contentWindow": {"nextOffset": 7}},
    }))
    tools = VerificationTools(rag_client=rag, binding={**tree, "review_collection_target": "sealed"}, parts=[])
    result = await tools.call("getStructuralUnit", {"unitId": "unit"})
    assert result["content"] == "symbol = 1\n"
    assert result["path"] == "app/models/base.py"
    assert result["side"] == "proposed"
    assert "content" not in result["unit"]
    assert rag.get_review_structural_unit.await_count == 1


@pytest.mark.asyncio
async def test_structural_unit_assembles_complete_graph_source_when_local_unavailable():
    content = "full source spanning pages"
    digest = hashlib.sha256(content.encode()).hexdigest()
    unit = {"unitId": "unit", "path": "generated.context", "contentSha256": digest}
    rag = SimpleNamespace(get_review_structural_unit=AsyncMock(side_effect=[
        {"status": "ready", "sourceEvidence": True, "unit": {**unit, "content": content[:8], "contentWindow": {"offset": 0, "nextOffset": 8}}},
        {"status": "ready", "sourceEvidence": True, "unit": {**unit, "content": content[8:], "contentWindow": {"offset": 8, "nextOffset": None}}},
    ]))
    tools = VerificationTools(rag_client=rag, binding={"review_collection_target": "sealed"}, parts=[])
    result = await tools.call("getStructuralUnit", {"unitId": "unit"})
    assert result["content"] == content
    assert result["contentSha256"] == digest
    assert result["sourceEvidence"] is True


@pytest.mark.asyncio
async def test_partial_search_is_observable_and_decision_receipts_are_not_cached(tree):
    tools = VerificationTools(rag_client=None, binding=tree, parts=[])
    partial = await tools.call("grepReviewCode", {"query": "symbol", "paths": ["app/models", "missing"]})
    assert partial["status"] == "partial"
    assert partial["results"]
    assert tools.diagnostics
    handler = AsyncMock(return_value={"status": "ready", "accepted": ["candidate-1"]})
    tools.register_decisions(handler)
    arguments = {"decisions": [{"candidateId": "candidate-1", "verdict": "keep", "reason": "Exact source", "evidenceIds": ["read-1"]}], "findings": [{"partId": "part-1", "file": "app/models/base.py", "line": 1, "title": "Same bug", "reason": "Same trigger", "evidenceIds": ["read-1"], "duplicateOf": "candidate-1"}]}
    for _ in range(2):
        result = await tools.call("recordReviewDecisions", arguments)
        assert result["accepted"] == ["candidate-1"]
    assert handler.await_count == 2
    assert handler.call_args.kwargs["findings"][0]["duplicateOf"] == "candidate-1"


@pytest.mark.asyncio
async def test_generated_plugin_context_is_not_exact_source_evidence_when_local_missing():
    rag = SimpleNamespace(get_review_structural_unit=AsyncMock(return_value={
        "status": "ready", "sourceEvidence": True,
        "unit": {"unitId": "context", "path": "generated.context", "recordType": "plugin_context",
                 "content": "Summary claims every caller is safe", "startLine": 1, "endLine": 1},
    }))
    tools = VerificationTools(rag_client=rag, binding={"review_collection_target": "sealed"}, parts=[])
    result = await tools.call("getStructuralUnit", {"unitId": "context"})
    assert result["sourceEvidence"] is False
    assert result["context"] == "Summary claims every caller is safe"
    assert "content" not in result


@pytest.mark.asyncio
async def test_plugin_symbol_declaration_line_reads_complete_original_file(tree, tmp_path):
    content = "def changed():\n    important_guard()\n    return dangerous_operation()\n"
    (tmp_path / "overlay" / "files" / "app" / "models" / "edited.py").write_text(content)
    rag = SimpleNamespace(get_review_structural_unit=AsyncMock(return_value={
        "status": "ready", "sourceEvidence": False,
        "unit": {"unitId": "symbol", "path": "app/models/edited.py", "recordType": "plugin_symbol",
                 "startLine": 1, "endLine": 1},
    }))
    tools = VerificationTools(rag_client=rag, binding={**tree, "review_collection_target": "sealed"}, parts=[])
    result = await tools.call("getStructuralUnit", {"unitId": "symbol"})
    assert result["content"] == content
    assert result["endLine"] == 3
    assert result["sourceEvidence"] is True


@pytest.mark.asyncio
async def test_transient_graph_failure_can_recover_on_identical_query(tree):
    rag = SimpleNamespace(query_review_graph=AsyncMock(side_effect=[
        {"status": "unavailable", "diagnostic": "temporary service outage"},
        {"status": "ready", "results": [{"unitId": "caller"}]},
    ]))
    tools = VerificationTools(rag_client=rag, binding={**tree, "review_collection_target": "sealed"}, parts=[])
    arguments = {"pattern": "callers_of", "target": "changed"}
    assert (await tools.call("queryCodeGraph", arguments))["status"] == "unavailable"
    ready = await tools.call("queryCodeGraph", arguments)
    assert ready["status"] == "ready"
    assert await tools.call("queryCodeGraph", arguments) == ready
    assert rag.query_review_graph.await_count == 2


@pytest.mark.asyncio
async def test_partial_local_search_can_recover_identical_query(tree, tmp_path):
    tools = VerificationTools(rag_client=None, binding=tree, parts=[])
    missing = tmp_path / "overlay" / "files" / "app" / "models" / "added.py"
    missing.unlink()
    arguments = {"query": "symbol", "paths": ["app/models"]}
    assert (await tools.call("grepReviewCode", arguments))["status"] == "partial"
    missing.write_text("symbol = 5\n")
    recovered = await tools.call("grepReviewCode", arguments)
    assert recovered["status"] == "ready"
    assert "app/models/added.py" in {entry["path"] for entry in recovered["results"]}


@pytest.mark.asyncio
async def test_graph_inventory_calls_actual_review_query_signatures_and_payload_bindings(tree):
    from service.rag.review_queries import ReviewQueries

    transport = SimpleNamespace(_post_review_query=AsyncMock(return_value={"status": "ready"}))
    queries = ReviewQueries(transport)
    binding = {**tree, "workspace": "tenant", "project": "project", "target_branch": "main",
               "base_revision": "base-sha", "source_revision": "source-sha", "review_collection_target": "sealed"}
    tools = VerificationTools(rag_client=queries, binding=binding, parts=[])
    operations = [
        ("queryCodeGraph", {"pattern": "callers_of", "target": "function"}, "/query/review-graph"),
        ("getMinimalReviewContext", {"question": "affected contract", "paths": ["app/models"]}, "/query/review-minimal-context"),
        ("getImpactRadius", {"targets": ["function"]}, "/query/review-impact-radius"),
        ("traverseCodeGraph", {"start": "function"}, "/query/review-traverse"),
        ("getStructuralUnit", {"unitId": "unit"}, "/query/review-unit"),
    ]
    for name, arguments, endpoint in operations:
        result = await tools.call(name, arguments)
        assert result["status"] == "ready"
        received_endpoint, payload, _fallback = transport._post_review_query.call_args.args
        assert received_endpoint == endpoint
        assert payload["workspace"] == "tenant"
        assert payload["project"] == "project"
        assert payload["review_collection_target"] == "sealed"
        assert payload["base_revision"] == "base-sha"
        assert payload["source_revision"] == "source-sha"
        if name != "getStructuralUnit":
            assert payload["include_source"] is False
        if name == "traverseCodeGraph":
            assert payload["token_budget"] is None
    assert transport._post_review_query.await_count == len(operations)
