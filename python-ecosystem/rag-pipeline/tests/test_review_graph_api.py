"""Request and router contracts for proposed-tree graph operations."""

from unittest.mock import patch

import pytest

from rag_pipeline.api.models import (
    ReviewFileRequest,
    ReviewSearchRequest,
    ReviewGraphQueryRequest,
    ReviewImpactRadiusRequest,
    ReviewMinimalContextRequest,
    ReviewTraverseRequest,
    ReviewUnitRequest,
)


def _binding():
    return {
        "workspace": "workspace",
        "project": "project",
        "target_branch": "main",
        "base_revision": "base",
        "source_revision": "source",
        "target_repo_path": "/tmp/target",
        "review_overlay_path": "/tmp/overlay",
        "base_collection_target": "sealed-base",
        "base_generation_manifest_sha256": "a" * 64,
        "review_collection_target": "sealed-review",
        "review_generation_manifest_sha256": "b" * 64,
        "focus_paths": [r"src\changed.py", "src/changed.py"],
    }


def test_operation_models_share_normalized_review_binding_and_bounded_defaults():
    minimal = ReviewMinimalContextRequest(
        **_binding(),
        question="Review the change",
        focus_symbols=[" Service.run ", "Service.run"],
    )
    impact = ReviewImpactRadiusRequest(**_binding(), targets=[" Service.run "])
    traverse = ReviewTraverseRequest(**_binding(), start="Service.run")
    graph = ReviewGraphQueryRequest(
        **_binding(),
        pattern="callers_of",
        target="Service.run",
    )
    unit = ReviewUnitRequest(**_binding(), unit_id="unit:service")
    source = ReviewFileRequest(**_binding(), path="src/changed.py")
    search = ReviewSearchRequest(**_binding(), query="createEvent")

    assert all(
        request.focus_paths == ["src/changed.py"]
        for request in (minimal, impact, traverse, graph, unit, source, search)
    )
    assert minimal.focus_symbols == ["Service.run"]
    assert minimal.max_relations == 25
    assert minimal.include_source is True
    assert impact.targets == ["Service.run"]
    assert impact.max_depth == 2
    assert traverse.strategy == "bfs"
    assert traverse.direction == "both"
    assert traverse.token_budget is None
    assert graph.detail_level == "standard"
    assert graph.cursor == 0
    assert unit.offset == 0
    assert unit.max_characters == 12000
    assert source.side == "proposed"
    assert source.start_line == 1
    assert source.end_line is None
    assert search.cursor == 0


def test_operation_models_preserve_explicit_cursor_budget_and_content_window():
    traverse = ReviewTraverseRequest(
        **_binding(),
        start="Service.run",
        token_budget=777,
    )
    graph = ReviewGraphQueryRequest(
        **_binding(),
        pattern="relations_of",
        target="unit:service",
        cursor=37,
    )
    unit = ReviewUnitRequest(
        **_binding(),
        unit_id="unit:service",
        offset=4096,
        max_characters=2048,
    )

    assert traverse.token_budget == 777
    assert graph.cursor == 37
    assert unit.offset == 4096
    assert unit.max_characters == 2048


def test_traversal_rejects_oversized_relation_kind_labels():
    with pytest.raises(ValueError, match="at most 128 characters"):
        ReviewTraverseRequest(
            **_binding(),
            start="Service.run",
            relation_kinds=["x" * 129],
        )


def test_traversal_model_rejects_unknown_strategy():
    with pytest.raises(ValueError):
        ReviewTraverseRequest(
            **_binding(),
            start="Service.run",
            strategy="sideways",
        )


def test_query_router_exposes_all_proposed_tree_operations():
    from rag_pipeline.api.routers.query import router

    paths = {route.path for route in router.routes}
    assert {
        "/query/review-minimal-context",
        "/query/review-impact-radius",
        "/query/review-traverse",
        "/query/review-graph",
        "/query/review-unit",
        "/query/review-file",
        "/query/review-search",
        "/query/review-generation",
    }.issubset(paths)


@patch("rag_pipeline.api.routers.query.ProposedTreeReviewContextService")
@patch("rag_pipeline.api.routers.query._manager")
def test_review_file_router_uses_the_sealed_review_binding(
    manager_factory, service_class,
):
    from rag_pipeline.api.routers.query import review_file

    request = ReviewFileRequest(
        **_binding(), path="src/changed.py", side="target",
        start_line=3, end_line=8,
    )
    service_class.return_value.get_review_file_content.return_value = {
        "status": "ready", "content": "source\n",
    }
    assert review_file(request)["content"] == "source\n"
    service_class.assert_called_once_with(manager_factory.return_value)
    arguments = service_class.return_value.get_review_file_content.call_args.kwargs
    assert arguments["review_collection_target"] == "sealed-review"
    assert arguments["path"] == "src/changed.py"
    assert (arguments["side"], arguments["start_line"], arguments["end_line"]) == (
        "target", 3, 8,
    )


@patch("rag_pipeline.api.routers.query.ProposedTreeReviewContextService")
@patch("rag_pipeline.api.routers.query._manager")
def test_review_search_router_keeps_the_same_generation(
    manager_factory, service_class,
):
    from rag_pipeline.api.routers.query import review_search

    request = ReviewSearchRequest(
        **_binding(), query="createEvent", cursor=100,
    )
    service_class.return_value.search_review_code.return_value = {
        "status": "ready", "results": [],
    }
    assert review_search(request)["status"] == "ready"
    arguments = service_class.return_value.search_review_code.call_args.kwargs
    assert arguments["review_collection_target"] == "sealed-review"
    assert (arguments["query"], arguments["cursor"]) == (
        "createEvent", 100,
    )


@patch("rag_pipeline.api.routers.query.ProposedTreeReviewContextService")
@patch("rag_pipeline.api.routers.query._manager")
def test_minimal_context_router_passes_host_binding_and_operation_arguments(
    manager_factory,
    service_class,
):
    from rag_pipeline.api.routers.query import review_minimal_context

    service_class.return_value.minimal_review_context.return_value = {
        "status": "ready",
        "operation": "minimal_review_context",
    }
    request = ReviewMinimalContextRequest(
        **_binding(),
        question="Review Service.run",
        focus_symbols=["Service.run"],
        include_source=True,
    )

    result = review_minimal_context(request)

    assert result["operation"] == "minimal_review_context"
    service_class.assert_called_once_with(manager_factory.return_value)
    arguments = service_class.return_value.minimal_review_context.call_args.kwargs
    assert arguments["focus_paths"] == ["src/changed.py"]
    assert arguments["base_collection_target"] == "sealed-base"
    assert arguments["review_collection_target"] == "sealed-review"
    assert arguments["question"] == "Review Service.run"
    assert arguments["focus_symbols"] == ["Service.run"]
    assert arguments["include_source"] is True


@patch("rag_pipeline.api.routers.query.ProposedTreeReviewContextService")
@patch("rag_pipeline.api.routers.query._manager")
def test_traversal_router_preserves_absent_and_explicit_budget(manager_factory, service_class):
    from rag_pipeline.api.routers.query import review_traverse

    service_class.return_value.traverse_review_graph.return_value = {"status": "ready"}
    for token_budget in (None, 2000):
        request = ReviewTraverseRequest(**_binding(), start="unit:changed", token_budget=token_budget, include_source=False)
        assert review_traverse(request)["status"] == "ready"
        forwarded = service_class.return_value.traverse_review_graph.call_args.kwargs
        assert forwarded["token_budget"] == token_budget
        assert forwarded["review_collection_target"] == "sealed-review"
        assert forwarded["include_source"] is False


@pytest.mark.parametrize("base", [None, "sealed-base"])
@patch("rag_pipeline.api.routers.query.ProposedTreeReviewContextService")
@patch("rag_pipeline.api.routers.query._manager")
def test_prepare_returns_actual_base_provenance_after_full_fallback_or_delta(
    manager_factory, service_class, base,
):
    from types import SimpleNamespace
    from rag_pipeline.api.models import ProposedTreePrepareRequest
    from rag_pipeline.api.routers.query import prepare_review_generation

    policy = {"include_patterns": ["src/**"], "exclude_patterns": ["vendor/**"],
              "project_type": "generic", "source_root": "src"}
    candidates = [{"collection_target": "active-seed", "generation_manifest_sha256": "c" * 64,
                   "revision": "active-revision"}]
    request = ProposedTreePrepareRequest(**_binding(), index_policy=policy,
                                         base_generation_candidates=candidates)
    manifest = "a" * 64 if base else None
    service_class.return_value.prepare_generation_singleflight.return_value = SimpleNamespace(
        collection_target="sealed-review",
        receipt={
            "generation_manifest_sha256": "b" * 64,
            "index_include_patterns": policy["include_patterns"],
            "index_exclude_patterns": policy["exclude_patterns"],
            "snapshot_metadata": {
                "base_collection_target": base,
                "project_type": "generic", "source_root": "src",
                "base_generation_manifest_sha256": manifest,
            },
        },
        proposed_source_tree_sha256="c" * 64, overlay_sha256="d" * 64,
        representation_identity="representation", changed_paths=("src/changed.py",),
        deleted_paths=(), cache_hit=False,
    )

    result = prepare_review_generation(request)
    assert result["index_policy"] == policy
    assert result["base_collection_target"] == base
    assert result["base_generation_manifest_sha256"] == manifest
    assert result["collection_target"] == "sealed-review"
    assert result["source_revision"] == "source"
    forwarded = service_class.return_value.prepare_generation_singleflight.call_args.kwargs
    assert forwarded["base_collection_target"] == "sealed-base"
    assert forwarded["index_policy"] == policy
    assert forwarded["base_generation_candidates"] == candidates


@pytest.mark.parametrize("policy,candidates", [
    ("invalid-policy", "invalid-candidates"),
    ({"include_patterns": "not-an-array"}, [None, {"revision": "partial"}]),
    ({"source_root": "../outside"}, [False]),
])
def test_optional_prepare_metadata_is_normalized_without_rejecting_core_binding(caplog, policy, candidates):
    from rag_pipeline.api.models import ProposedTreePrepareRequest
    request = ProposedTreePrepareRequest(**_binding(), index_policy=policy,
                                         base_generation_candidates=candidates)
    assert request.index_policy is None and request.base_generation_candidates == []
    assert request.workspace == _binding()["workspace"]
    assert request.target_repo_path == _binding()["target_repo_path"]
    assert "malformed optional repository" in caplog.text
