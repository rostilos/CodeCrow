"""Focused unit coverage for production API routers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


class TestSystemRouter:
    def test_root(self):
        from rag_pipeline.api.routers.system import root

        assert root()["message"] == "CodeCrow Repository Index API"

    def test_health(self):
        from rag_pipeline.api.routers.system import health

        assert asyncio.run(health())["status"] == "healthy"

    def test_current_representation_identity_uses_active_manager(self):
        from rag_pipeline.api import api as api_module
        from rag_pipeline.api.routers.system import current_representation_identity

        previous = api_module.index_manager
        manager = MagicMock()
        manager.current_representation_identity.return_value = {
            "representation_identity": "sha256:" + "0" * 64,
            "index_representation_fingerprint": "sha256:" + "1" * 64,
            "plugin_descriptor_fingerprint": "sha256:" + "2" * 64,
            "plugin_implementation_fingerprint": "sha256:" + "3" * 64,
            "plugin_ids": ["java"],
        }
        api_module.index_manager = manager
        try:
            result = current_representation_identity()
        finally:
            api_module.index_manager = previous

        assert result["plugin_ids"] == ["java"]
        manager.current_representation_identity.assert_called_once_with()


class TestParseRouter:
    @patch("rag_pipeline.core.splitter.ASTCodeSplitter")
    def test_parse_file_projects_ast_chunk_metadata(self, splitter_class):
        from rag_pipeline.api.models import ParseFileRequest
        from rag_pipeline.api.routers.parse import parse_file

        splitter_class.return_value.split_documents.return_value = [
            SimpleNamespace(metadata={
                "imports": ["os"],
                "symbol_names": ["hello"],
                "calls": ["print"],
            })
        ]

        result = parse_file(ParseFileRequest(
            path="test.py",
            content="import os\ndef hello():\n    pass\n",
            language="python",
        ))
        assert result.path == "test.py"
        assert result.success is True
        assert result.language == "python"
        assert result.imports == ["os"]
        assert result.symbol_names == ["hello"]
        assert result.calls == ["print"]
        parsed_documents = (
            splitter_class.return_value.split_documents.call_args.args[0]
        )
        assert parsed_documents[0].metadata == {"path": "test.py"}


class TestQueryRouter:
    @patch("rag_pipeline.api.routers.query._manager")
    def test_code_search_is_revision_bound(self, manager_factory):
        from rag_pipeline.api.models import CodeSearchRequest
        from rag_pipeline.api.routers.query import code_search

        manager = MagicMock()
        reader = manager.open_reader.return_value.__enter__.return_value
        reader.search_units.return_value = [{
            "unitId": "unit:main",
            "path": "src/main.py",
            "recordType": "source_unit",
            "startLine": 1,
            "endLine": 3,
            "language": "python",
            "kind": "function",
            "name": "main",
            "qualifiedName": "main",
        }]
        reader.get_unit.return_value = {
            "unit": {"content": "def main():\n    pass\n"},
            "sourceEvidence": True,
        }
        reader.snapshot.return_value = {"revision": "abc123"}
        manager_factory.return_value = manager

        result = code_search(CodeSearchRequest(
            query="main",
            workspace="ws",
            project="proj",
            branch="main",
            repository_revision="abc123",
            repository_generation_manifest_sha256="a" * 64,
            collection_target="generation-target",
        ))

        assert result["results"][0]["match_reasons"] == [
            "path",
            "symbol",
            "source",
        ]
        assert manager.open_reader.call_args.kwargs == {
            "workspace": "ws",
            "project": "proj",
            "branch": "main",
            "revision": "abc123",
            "generation_manifest_sha256": "a" * 64,
            "collection_target": "generation-target",
        }

    @patch("rag_pipeline.api.routers.query.ProposedTreeReviewContextService")
    @patch("rag_pipeline.api.routers.query._manager")
    def test_review_context_keeps_host_binding_outside_model_focus(
        self,
        manager_factory,
        service_class,
    ):
        from rag_pipeline.api.models import ReviewContextRequest
        from rag_pipeline.api.routers.query import review_context

        expected = {
            "status": "ready",
            "snapshot": {},
            "freshness": {},
            "changed": {},
            "evidence": {},
            "sourceWindows": [],
            "coverage": {},
            "provenance": {},
            "omittedFollowups": [],
        }
        service_class.return_value.review_context.return_value = expected
        request = ReviewContextRequest(
            workspace="ws",
            project="project",
            target_branch="main",
            base_revision="base",
            source_revision="source",
            target_repo_path="/tmp/target",
            review_overlay_path="/tmp/overlay",
            base_collection_target="sealed-base-target",
            base_generation_manifest_sha256="a" * 64,
            review_collection_target="sealed-review-target",
            review_generation_manifest_sha256="b" * 64,
            focus_paths=["src/service.py"],
            question="Who calls Service.run?",
            focus_symbols=["Service.run"],
        )

        assert review_context(request) == expected
        service_class.assert_called_once_with(manager_factory.return_value)
        assert service_class.return_value.review_context.call_args.kwargs[
            "target_repo_path"
        ] == "/tmp/target"
        assert service_class.return_value.review_context.call_args.kwargs[
            "focus_symbols"
        ] == ["Service.run"]
        assert service_class.return_value.review_context.call_args.kwargs[
            "base_collection_target"
        ] == "sealed-base-target"
        assert service_class.return_value.review_context.call_args.kwargs[
            "base_generation_manifest_sha256"
        ] == "a" * 64
        assert service_class.return_value.review_context.call_args.kwargs[
            "review_collection_target"
        ] == "sealed-review-target"
        assert service_class.return_value.review_context.call_args.kwargs[
            "review_generation_manifest_sha256"
        ] == "b" * 64

    @patch("rag_pipeline.api.routers.query.ProposedTreeReviewContextService")
    @patch("rag_pipeline.api.routers.query._manager")
    def test_review_context_reports_incomplete_overlay_as_conflict(
        self,
        manager_factory,
        service_class,
    ):
        from rag_pipeline.api.models import ReviewContextRequest
        from rag_pipeline.api.routers.query import review_context
        from rag_pipeline.core.review_context import ProposedTreeUnavailableError

        service_class.return_value.review_context.side_effect = (
            ProposedTreeUnavailableError("changed body unavailable")
        )
        request = ReviewContextRequest(
            workspace="ws",
            project="project",
            target_branch="main",
            base_revision="base",
            source_revision="source",
            target_repo_path="/tmp/target",
            review_overlay_path="/tmp/overlay",
            review_collection_target="sealed-review-target",
            review_generation_manifest_sha256="b" * 64,
            focus_paths=["src/service.py"],
            question="Review the change",
        )

        with pytest.raises(HTTPException) as raised:
            review_context(request)

        assert raised.value.status_code == 409
        assert raised.value.detail == "changed body unavailable"

    @patch("rag_pipeline.api.routers.query.logger.warning")
    @patch("rag_pipeline.api.routers.query.ProposedTreeReviewContextService")
    @patch("rag_pipeline.api.routers.query._manager")
    def test_review_context_reports_active_mutation_as_retryable_conflict(
        self,
        manager_factory,
        service_class,
        warning,
    ):
        from rag_pipeline.api.models import ReviewContextRequest
        from rag_pipeline.api.routers.query import review_context
        from rag_pipeline.core.coordination import MutationLeaseUnavailable

        detail = (
            "another RAG mutation is active for ws/project "
            "collection cc_review_g_abc"
        )
        service_class.return_value.review_context.side_effect = (
            MutationLeaseUnavailable(detail)
        )
        request = ReviewContextRequest(
            workspace="ws",
            project="project",
            target_branch="main",
            base_revision="base",
            source_revision="source",
            target_repo_path="/tmp/target",
            review_overlay_path="/tmp/overlay",
            review_collection_target="sealed-review-target",
            review_generation_manifest_sha256="b" * 64,
            focus_paths=["src/service.py"],
            question="Review the change",
        )

        with pytest.raises(HTTPException) as raised:
            review_context(request)

        assert raised.value.status_code == 409
        assert raised.value.detail == detail
        warning.assert_called_once_with(
            "Proposed-tree review context mutation conflict: "
            "workspace=%s project=%s detail=%s",
            "ws",
            "project",
            service_class.return_value.review_context.side_effect,
        )
