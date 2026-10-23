"""Adapted source seed/policy oracle for the restored multistage graph consumers."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from service.rag import rag_mcp_server
from service.rag.review_queries import ReviewQueries
from service.review.orchestrator.stage_1_rag_retrieval import fetch_stage1_relation_briefing
from service.review.review_service import ReviewService
from tests.test_structural_revision_binding import _request


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_seed,expected_seed", [
    ({"base_collection_target": "requested-base", "base_generation_manifest_sha256": "requested-manifest",
      "base_generation_revision": "older-seed"},
     ("requested-base", "requested-manifest", "older-seed")),
    ({"base_collection_target": "canonical-base", "base_generation_manifest_sha256": "canonical-manifest",
      "base_generation_revision": "canonical-seed"},
     ("canonical-base", "canonical-manifest", "canonical-seed")),
    ({"base_collection_target": None, "base_generation_manifest_sha256": None,
      "base_generation_revision": None}, None),
    ({}, ("requested-base", "requested-manifest", "older-seed")),
])
@pytest.mark.parametrize("includes,excludes", [(["src/**"], ["vendor/**"]), ([], [])])
async def test_accepted_seed_and_policy_reach_both_restored_graph_consumers(
    monkeypatch, receipt_seed, expected_seed, includes, excludes,
):
    policy = {"include_patterns": includes, "exclude_patterns": excludes,
              "project_type": "generic", "source_root": "src"}
    candidates = [{"collection_target": "active-seed", "generation_manifest_sha256": "c" * 64,
                   "revision": "active-revision"}]
    request = _request(ragCollectionTarget="requested-base", ragBaseGenerationManifestSha256="requested-manifest",
                       ragBaseGenerationRevision="older-seed", ragIndexPolicy=policy,
                       ragGenerationCandidates=candidates)
    transmitted = []

    async def respond(endpoint, payload, empty_result, **kwargs):
        transmitted.append((endpoint, dict(payload)))
        if endpoint == "/query/review-generation":
            return {"status": "ready", "source_revision": request.currentCommitHash,
                    "collection_target": "sealed-review", "generation_manifest_sha256": "b" * 64,
                    "index_policy": policy, **receipt_seed}
        assert endpoint == "/query/review-context"
        return {"status": "ready", "nodes": [], "edges": []}

    queries = ReviewQueries(SimpleNamespace(
        _post_review_query=respond, _review_preparation_timeout_seconds=lambda: 1800,
    ))
    client = MagicMock()
    client.prepare_review_generation = queries.prepare_review_generation
    client.explore_review_context = queries.explore_review_context
    service = ReviewService(rag_client=client)
    await service._prepare_rag_review_generation(request, client, None)
    assert request.ragReviewGenerationStatus == "ready"
    # Exercise actual endpoint payloads from host preloading and the child MCP
    # context, without invoking HTTP or a model.
    await fetch_stage1_relation_briefing(client, request, ["src/a.py"])
    context = service._build_rag_mcp_context(request, client)
    assert context is not None
    for key, value in context.items():
        monkeypatch.setenv(f"CODECROW_RAG_MCP_{key.upper()}", str(value))
    await queries.explore_review_context(
        question="Review changed", focus_paths=["src/a.py"], **rag_mcp_server._review_binding(),
    )
    preparation = transmitted[0][1]
    assert preparation["index_policy"] == policy
    assert preparation["base_generation_candidates"] == candidates
    assert preparation["base_generation_revision"] == "older-seed"
    assert len(transmitted) == 3
    seed_keys = ("base_collection_target", "base_generation_manifest_sha256", "base_generation_revision")
    for _, query in transmitted[1:]:
        assert "index_policy" not in query and "base_generation_candidates" not in query
        assert {key: query[key] for key in policy} == policy
        if expected_seed is None:
            assert all(key not in query for key in seed_keys)
        else:
            assert tuple(query[key] for key in seed_keys) == expected_seed
        assert query["review_collection_target"] == "sealed-review"
        assert query["review_generation_manifest_sha256"] == "b" * 64
    for _, payload in transmitted:
        assert payload["base_revision"] == request.localRepoRevision == request.targetHeadCommitHash == "target-b"
        assert payload["source_revision"] == "source-head"
        assert payload["target_branch"] == "main"
        assert payload["target_repo_path"] == "/tmp/structural-snapshot"
        assert payload["workspace"] == "tenant"
        assert payload["project"] == "repository"


@pytest.mark.asyncio
async def test_missing_seed_still_prepares_from_exact_source_and_preserves_policy():
    request = _request(ragCollectionTarget=None, ragBaseGenerationManifestSha256=None,
                       ragIndexPolicy={"include_patterns": ["app/**"], "exclude_patterns": ["vendor/**"]})
    prepared = []

    async def prepare(**binding):
        prepared.append(binding)
        return {"status": "ready", "source_revision": "source-head", "collection_target": "sealed-source",
                "generation_manifest_sha256": "d" * 64, "base_collection_target": None,
                "base_generation_manifest_sha256": None, "base_generation_revision": None,
                "index_policy": request.ragIndexPolicy.model_dump()}

    client = MagicMock(prepare_review_generation=prepare)
    service = ReviewService(rag_client=client)
    await service._prepare_rag_review_generation(request, client, None)
    assert request.ragReviewGenerationStatus == "ready"
    assert prepared[0]["index_policy"]["include_patterns"] == ["app/**"]
    assert "base_collection_target" not in prepared[0]
    assert service._build_rag_mcp_context(request, client)["review_collection_target"] == "sealed-source"


@pytest.mark.parametrize("policy,candidates", [
    ("invalid-policy", "invalid-candidates"),
    ({"include_patterns": "not-an-array"}, [None, {"revision": "partial"}]),
    ({"source_root": {"invalid": "object"}}, [False]),
])
def test_malformed_optional_metadata_preserves_review_request(caplog, policy, candidates):
    request = _request(ragIndexPolicy=policy, ragGenerationCandidates=candidates, useMcpTools=True,
                       taskHistoryContext="Prior task review", analysisMode="INCREMENTAL")
    assert request.ragIndexPolicy is None and request.ragGenerationCandidates == []
    assert request.taskHistoryContext == "Prior task review"
    assert request.analysisMode == "INCREMENTAL" and request.useMcpTools is True
    assert "malformed optional repository" in caplog.text
