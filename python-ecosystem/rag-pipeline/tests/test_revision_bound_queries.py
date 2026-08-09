from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from rag_pipeline.api.models import (
    DeterministicContextRequest,
    PRContextRequest,
    QueryRequest,
)
from rag_pipeline.api.routers.query import (
    _query_pr_indexed_data,
    get_deterministic_context,
    get_pr_context,
    semantic_search,
)
from rag_pipeline.core.repository_overlay import (
    IncrementalIndexPreconditionError,
)
from rag_pipeline.services.base import RAGQueryBase
from rag_pipeline.services.pr_context import PRContextMixin
from rag_pipeline.services.semantic_search import SemanticSearchMixin
from rag_pipeline.services.deterministic_context import DeterministicContextMixin


BASE_REVISION = "a" * 40
SOURCE_REVISION = "b" * 40
BASE_GENERATION = "c" * 64
PR_GENERATION = "sha256:" + "d" * 64
PR_OVERLAY_MANIFEST = "e" * 64
OVERLAY_REPRESENTATION = "sha256:" + "f" * 64


def _overlay_receipt(manifest=PR_OVERLAY_MANIFEST):
    return {
        "overlay_generation_member_count": 1,
        "overlay_generation_members_sha256": "1" * 64,
        "overlay_generation_manifest_sha256": manifest,
    }


def _manager():
    manager = MagicMock()
    manager._get_project_collection_name.return_value = "rag_ws__project"
    manager._collection_manager.collection_exists.return_value = True
    manager._collection_manager.resolve_collection_target.return_value = (
        "rag_ws__project_active"
    )
    manager.pr_overlay_representation_fingerprint = OVERLAY_REPRESENTATION
    return manager


def test_semantic_query_detects_generation_swap_during_request():
    manager = _manager()
    manager.get_revision_preflight.side_effect = [
        {"generation_manifest_sha256": BASE_GENERATION},
        {"generation_manifest_sha256": "e" * 64},
    ]
    service = MagicMock()
    service.semantic_search.return_value = []
    request = QueryRequest(
        query="dependency lookup",
        workspace="ws",
        project="project",
        branch="main",
        repository_revision=BASE_REVISION,
        repository_generation_manifest_sha256=BASE_GENERATION,
    )

    with patch(
        "rag_pipeline.api.routers.query._get_singletons",
        return_value=(manager, service),
    ):
        with pytest.raises(HTTPException) as exception:
            semantic_search(request)

    assert exception.value.status_code == 409
    assert "generation changed" in exception.value.detail
    service.semantic_search.assert_called_once_with(
        query="dependency lookup",
        workspace="ws",
        project="project",
        branch="main",
        top_k=10,
        filter_language=None,
        expected_revision=BASE_REVISION,
        collection_target="rag_ws__project_active",
    )


def test_pr_context_detects_base_generation_swap_after_retrieval():
    manager = _manager()
    manager.get_revision_preflight.side_effect = [
        {"generation_manifest_sha256": BASE_GENERATION},
        {"generation_manifest_sha256": "e" * 64},
    ]
    service = MagicMock()
    service._collection_or_alias_exists.return_value = True
    service.get_context_for_pr.return_value = {
        "relevant_code": [],
        "related_files": [],
        "changed_files": ["src/Foo.php"],
    }
    request = PRContextRequest(
        workspace="ws",
        project="project",
        branch="main",
        base_branch="main",
        changed_files=["src/Foo.php"],
        pr_number=42,
        source_revision=SOURCE_REVISION,
        base_revision=BASE_REVISION,
        base_generation_manifest_sha256=BASE_GENERATION,
        pr_generation_fingerprint=PR_GENERATION,
        pr_overlay_generation_manifest_sha256=PR_OVERLAY_MANIFEST,
    )

    with (
        patch(
            "rag_pipeline.api.routers.query._get_singletons",
            return_value=(manager, service),
        ),
        patch(
            "rag_pipeline.api.routers.query._query_pr_indexed_data",
            return_value=[],
        ) as query_overlay,
        patch(
            "rag_pipeline.api.routers.query.read_pr_overlay_generation",
            return_value=_overlay_receipt(),
        ),
    ):
        with pytest.raises(HTTPException) as exception:
            get_pr_context(request)

    assert exception.value.status_code == 409
    assert "generation changed" in exception.value.detail
    assert query_overlay.call_args.kwargs["collection_target"] == (
        "rag_ws__project_active"
    )
    assert service.get_context_for_pr.call_args.kwargs[
        "collection_target"
    ] == "rag_ws__project_active"


@pytest.mark.parametrize(
    "probe_error",
    [None, RuntimeError("qdrant unavailable")],
    ids=["missing-collection", "backend-error"],
)
def test_revision_bound_pr_context_probe_failure_returns_http_conflict(
    probe_error,
):
    manager = _manager()
    manager.get_revision_preflight.return_value = {
        "generation_manifest_sha256": BASE_GENERATION,
    }

    class ProbeFailingService(PRContextMixin, RAGQueryBase):
        pass

    service = object.__new__(ProbeFailingService)
    service.qdrant_client = MagicMock()
    if probe_error is None:
        service.qdrant_client.get_collections.return_value.collections = []
        service.qdrant_client.get_aliases.return_value.aliases = []
    else:
        service.qdrant_client.get_collections.side_effect = probe_error
    request = PRContextRequest(
        workspace="ws",
        project="project",
        branch="main",
        base_branch="main",
        changed_files=["src/Foo.php"],
        pr_number=42,
        source_revision=SOURCE_REVISION,
        base_revision=BASE_REVISION,
        base_generation_manifest_sha256=BASE_GENERATION,
        pr_generation_fingerprint=PR_GENERATION,
        pr_overlay_generation_manifest_sha256=PR_OVERLAY_MANIFEST,
    )

    with (
        patch(
            "rag_pipeline.api.routers.query._get_singletons",
            return_value=(manager, service),
        ),
        patch(
            "rag_pipeline.api.routers.query._query_pr_indexed_data",
            return_value=[],
        ),
        patch(
            "rag_pipeline.api.routers.query.read_pr_overlay_generation",
            return_value=_overlay_receipt(),
        ),
    ):
        with pytest.raises(HTTPException) as exception:
            get_pr_context(request)

    assert exception.value.status_code == 409
    assert (
        exception.value.detail
        == "revision-bound PR-context collection is unavailable"
    )
    manager.get_revision_preflight.assert_called_once()


def test_pr_overlay_query_filters_and_validates_exact_generation():
    manager = _manager()
    point = SimpleNamespace(
        payload={
            "path": "src/Foo.php",
            "text": "<?php final class Foo {}",
            "pr": True,
            "pr_number": 42,
            "pr_source_revision": SOURCE_REVISION,
            "pr_base_revision": BASE_REVISION,
            "pr_base_generation_manifest_sha256": BASE_GENERATION,
            "pr_generation_fingerprint": PR_GENERATION,
        },
        score=0.9,
    )
    manager.qdrant_client.scroll.return_value = ([point], None)
    service = MagicMock()
    service._filter_plugin_compatible_points.side_effect = lambda points: points

    results = _query_pr_indexed_data(
        index_manager=manager,
        query_service=service,
        workspace="ws",
        project="project",
        pr_number=42,
        changed_files=[],
        query_texts=[],
        pr_title=None,
        source_revision=SOURCE_REVISION,
        base_revision=BASE_REVISION,
        base_generation_manifest_sha256=BASE_GENERATION,
        pr_generation_fingerprint=PR_GENERATION,
    )

    assert [result["path"] for result in results] == ["src/Foo.php"]
    query_filter = manager.qdrant_client.scroll.call_args.kwargs[
        "scroll_filter"
    ]
    conditions = {
        condition.key: condition.match.value
        for condition in query_filter.must
    }
    assert conditions == {
        "pr": True,
        "pr_number": 42,
        "pr_source_revision": SOURCE_REVISION,
        "pr_base_revision": BASE_REVISION,
        "pr_base_generation_manifest_sha256": BASE_GENERATION,
        "pr_generation_fingerprint": PR_GENERATION,
    }
    assert {
        condition.key: condition.match.value
        for condition in query_filter.must_not
    } == {"pr_overlay_generation_manifest": True}

    point.payload["pr_generation_fingerprint"] = "sha256:" + "f" * 64
    with pytest.raises(Exception, match="outside the requested overlay"):
        _query_pr_indexed_data(
            index_manager=manager,
            query_service=service,
            workspace="ws",
            project="project",
            pr_number=42,
            changed_files=[],
            query_texts=[],
            pr_title=None,
            source_revision=SOURCE_REVISION,
            base_revision=BASE_REVISION,
            base_generation_manifest_sha256=BASE_GENERATION,
            pr_generation_fingerprint=PR_GENERATION,
        )


def test_revision_bound_pr_overlay_query_propagates_backend_failure():
    manager = _manager()
    manager.qdrant_client.scroll.side_effect = RuntimeError("qdrant unavailable")
    service = MagicMock()

    with pytest.raises(RuntimeError, match="qdrant unavailable"):
        _query_pr_indexed_data(
            index_manager=manager,
            query_service=service,
            workspace="ws",
            project="project",
            pr_number=42,
            changed_files=[],
            query_texts=[],
            pr_title=None,
            source_revision=SOURCE_REVISION,
            base_revision=BASE_REVISION,
            base_generation_manifest_sha256=BASE_GENERATION,
            pr_generation_fingerprint=PR_GENERATION,
        )


def test_revision_bound_pr_overlay_query_rejects_missing_collection():
    manager = _manager()
    manager._collection_manager.collection_exists.return_value = False

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="collection is unavailable",
    ):
        _query_pr_indexed_data(
            index_manager=manager,
            query_service=MagicMock(),
            workspace="ws",
            project="project",
            pr_number=42,
            changed_files=[],
            query_texts=[],
            pr_title=None,
            source_revision=SOURCE_REVISION,
            base_revision=BASE_REVISION,
            base_generation_manifest_sha256=BASE_GENERATION,
            pr_generation_fingerprint=PR_GENERATION,
        )


def _semantic_search_service(*, collection_exists=True):
    service = SemanticSearchMixin()
    service._get_project_collection_name = MagicMock(
        return_value="rag_ws__project"
    )
    service._collection_or_alias_exists = MagicMock(
        return_value=collection_exists
    )
    service._require_compatible_branches = MagicMock()
    service._supports_instructions = False
    return service


def test_revision_bound_semantic_search_propagates_backend_failure():
    service = _semantic_search_service()
    service._get_or_create_index = MagicMock(
        side_effect=RuntimeError("vector backend unavailable")
    )

    with pytest.raises(RuntimeError, match="vector backend unavailable"):
        service.semantic_search_multi_branch(
            query="dependency lookup",
            workspace="ws",
            project="project",
            branches=["main"],
            expected_revisions={"main": BASE_REVISION},
        )


def test_unbound_semantic_search_keeps_best_effort_backend_fallback():
    service = _semantic_search_service()
    service._get_or_create_index = MagicMock(
        side_effect=RuntimeError("vector backend unavailable")
    )

    assert service.semantic_search_multi_branch(
        query="dependency lookup",
        workspace="ws",
        project="project",
        branches=["main"],
    ) == []


def test_revision_bound_semantic_search_rejects_missing_collection():
    service = _semantic_search_service(collection_exists=False)

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="collection is unavailable",
    ):
        service.semantic_search_multi_branch(
            query="dependency lookup",
            workspace="ws",
            project="project",
            branches=["main"],
            expected_revisions={"main": BASE_REVISION},
        )


def test_pr_context_detects_overlay_generation_swap_after_retrieval():
    manager = _manager()
    manager.get_revision_preflight.return_value = {
        "generation_manifest_sha256": BASE_GENERATION,
    }
    service = MagicMock()
    service._collection_or_alias_exists.return_value = True
    service.get_context_for_pr.return_value = {
        "relevant_code": [],
        "related_files": [],
        "changed_files": ["src/Foo.php"],
    }
    request = PRContextRequest(
        workspace="ws",
        project="project",
        branch="main",
        base_branch="main",
        changed_files=["src/Foo.php"],
        pr_number=42,
        source_revision=SOURCE_REVISION,
        base_revision=BASE_REVISION,
        base_generation_manifest_sha256=BASE_GENERATION,
        pr_generation_fingerprint=PR_GENERATION,
        pr_overlay_generation_manifest_sha256=PR_OVERLAY_MANIFEST,
    )

    with (
        patch(
            "rag_pipeline.api.routers.query._get_singletons",
            return_value=(manager, service),
        ),
        patch(
            "rag_pipeline.api.routers.query._query_pr_indexed_data",
            return_value=[],
        ),
        patch(
            "rag_pipeline.api.routers.query.read_pr_overlay_generation",
            side_effect=[
                _overlay_receipt(),
                _overlay_receipt("2" * 64),
            ],
        ),
    ):
        with pytest.raises(HTTPException) as exception:
            get_pr_context(request)

    assert exception.value.status_code == 409
    assert "overlay generation changed" in exception.value.detail


def test_deterministic_context_detects_overlay_generation_swap():
    manager = _manager()
    manager.get_revision_preflight.return_value = {
        "generation_manifest_sha256": BASE_GENERATION,
    }
    service = MagicMock()
    events = []

    def retrieve_context(**_kwargs):
        events.append("retrieve")
        return {
            "chunks": [],
            "changed_files": {},
            "related_definitions": {},
        }

    def probe_overlay(*_args, **_kwargs):
        events.append("probe")
        return (
            _overlay_receipt()
            if events.count("probe") == 1
            else _overlay_receipt("2" * 64)
        )

    service.get_deterministic_context.side_effect = retrieve_context
    request = DeterministicContextRequest(
        workspace="ws",
        project="project",
        branches=["main"],
        file_paths=["src/Foo.php"],
        pr_number=42,
        source_revision=SOURCE_REVISION,
        base_revision=BASE_REVISION,
        base_generation_manifest_sha256=BASE_GENERATION,
        pr_generation_fingerprint=PR_GENERATION,
        pr_overlay_generation_manifest_sha256=PR_OVERLAY_MANIFEST,
    )

    with (
        patch(
            "rag_pipeline.api.routers.query._get_singletons",
            return_value=(manager, service),
        ),
        patch(
            "rag_pipeline.api.routers.query.read_pr_overlay_generation",
            side_effect=probe_overlay,
        ),
    ):
        with pytest.raises(HTTPException) as exception:
            get_deterministic_context(request)

    assert exception.value.status_code == 409
    assert "overlay generation changed" in exception.value.detail
    assert events == ["probe", "retrieve", "probe"]
    assert service.get_deterministic_context.call_args.kwargs[
        "collection_target"
    ] == "rag_ws__project_active"


def test_revision_bound_deterministic_context_rejects_extra_branch():
    manager = _manager()
    manager.get_revision_preflight.return_value = {
        "generation_manifest_sha256": BASE_GENERATION,
    }
    service = MagicMock()
    request = DeterministicContextRequest(
        workspace="ws",
        project="project",
        branches=["main", "unbound-feature"],
        file_paths=["src/Foo.php"],
        pr_number=42,
        source_revision=SOURCE_REVISION,
        base_revision=BASE_REVISION,
        base_generation_manifest_sha256=BASE_GENERATION,
        pr_generation_fingerprint=PR_GENERATION,
        pr_overlay_generation_manifest_sha256=PR_OVERLAY_MANIFEST,
    )

    with (
        patch(
            "rag_pipeline.api.routers.query._get_singletons",
            return_value=(manager, service),
        ),
        patch(
            "rag_pipeline.api.routers.query.read_pr_overlay_generation",
            return_value=_overlay_receipt(),
        ),
    ):
        with pytest.raises(HTTPException) as exception:
            get_deterministic_context(request)

    assert exception.value.status_code == 409
    assert "exactly one authoritative" in exception.value.detail
    service.get_deterministic_context.assert_not_called()


def test_deterministic_context_rejects_partial_overlay_identity():
    manager = _manager()
    service = MagicMock()
    request = DeterministicContextRequest(
        workspace="ws",
        project="project",
        branches=["main"],
        file_paths=["src/Foo.php"],
        pr_number=42,
        pr_generation_fingerprint=PR_GENERATION,
    )

    with patch(
        "rag_pipeline.api.routers.query._get_singletons",
        return_value=(manager, service),
    ):
        with pytest.raises(HTTPException) as exception:
            get_deterministic_context(request)

    assert exception.value.status_code == 409
    assert "requires PR number" in exception.value.detail
    service.get_deterministic_context.assert_not_called()


def test_exact_overlay_rejects_empty_authoritative_branch_list():
    manager = _manager()
    service = MagicMock()
    request = DeterministicContextRequest(
        workspace="ws",
        project="project",
        branches=[],
        file_paths=["src/Foo.php"],
        pr_number=42,
        source_revision=SOURCE_REVISION,
        base_revision=BASE_REVISION,
        base_generation_manifest_sha256=BASE_GENERATION,
        pr_generation_fingerprint=PR_GENERATION,
        pr_overlay_generation_manifest_sha256=PR_OVERLAY_MANIFEST,
    )

    with patch(
        "rag_pipeline.api.routers.query._get_singletons",
        return_value=(manager, service),
    ):
        with pytest.raises(HTTPException) as exception:
            get_deterministic_context(request)

    assert exception.value.status_code == 409
    assert "one authoritative branch" in exception.value.detail
    service.get_deterministic_context.assert_not_called()


def test_deterministic_service_rejects_fingerprint_without_revisions():
    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="complete source/base generation identity",
    ):
        DeterministicContextMixin.get_deterministic_context(
            MagicMock(),
            workspace="ws",
            project="project",
            branches=["main"],
            file_paths=["src/Foo.php"],
            pr_number=42,
            pr_generation_fingerprint=PR_GENERATION,
        )
