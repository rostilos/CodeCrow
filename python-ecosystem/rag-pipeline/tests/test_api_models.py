"""
Unit tests for rag_pipeline.api.models — Pydantic request/response models.
"""
import os
import pytest
from unittest.mock import patch

from rag_pipeline.api.models import (
    IndexRequest,
    CodeSearchRequest,
    ParseFileRequest,
    ParseBatchRequest,
    ParsedFileMetadata,
    RepositoryIndexGraphRequest,
    RepositoryIndexNodeRequest,
    ReviewContextRequest,
)


class TestIndexRequest:

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_valid_path(self):
        req = IndexRequest(
            repo_path="/tmp/repo",
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
            source_tree_sha256="a" * 64,
            collection_target="generation-target",
        )
        assert req.workspace == "ws"
        assert req.transfer_repo_ownership is False

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_direct_api_may_delegate_exact_identity_to_server(self):
        req = IndexRequest(
            repo_path="/tmp/repo",
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
        )

        assert req.source_tree_sha256 is None
        assert req.collection_target is None

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_malformed_explicit_source_identity_is_rejected(self):
        with pytest.raises(ValueError, match="source_tree_sha256"):
            IndexRequest(
                repo_path="/tmp/repo",
                workspace="ws",
                project="proj",
                branch="main",
                commit="abc123",
                source_tree_sha256="not-a-sha256",
            )

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_stream_repository_ownership_requires_explicit_opt_in(self):
        req = IndexRequest(
            repo_path="/tmp/codecrow-rag-branch-generation-owned",
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
            source_tree_sha256="a" * 64,
            collection_target="generation-target",
            transfer_repo_ownership=True,
        )
        assert req.transfer_repo_ownership is True

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="Path must be under"):
            IndexRequest(
                repo_path="/etc/passwd",
                workspace="ws",
                project="proj",
                branch="main",
                commit="abc123",
                source_tree_sha256="a" * 64,
                collection_target="generation-target",
            )

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_allowed_root_name_prefix_is_not_treated_as_a_child_path(self):
        with pytest.raises(ValueError, match="Path must be under"):
            IndexRequest(
                repo_path="/tmp-outside/repo",
                workspace="ws",
                project="proj",
                branch="main",
                commit="abc123",
                source_tree_sha256="a" * 64,
                collection_target="generation-target",
            )

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_manual_project_profile_accepts_arbitrary_nested_source_root(self):
        req = IndexRequest(
            repo_path="/tmp/repo",
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
            source_tree_sha256="a" * 64,
            collection_target="generation-target",
            project_type="magento",
            source_root=r"magento\src\etc",
        )

        assert req.project_type == "magento"
        assert req.source_root == "magento/src/etc"

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_auto_project_profile_normalizes_to_marker_detection(self):
        req = IndexRequest(
            repo_path="/tmp/repo",
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
            source_tree_sha256="a" * 64,
            collection_target="generation-target",
            project_type=" AUTO ",
        )

        assert req.project_type is None

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    @pytest.mark.parametrize("source_root", ["/magento", "magento/", "../magento", "magento//src"])
    def test_source_root_must_be_a_repository_relative_directory(self, source_root):
        with pytest.raises(ValueError, match="source_root"):
            IndexRequest(
                repo_path="/tmp/repo",
                workspace="ws",
                project="proj",
                branch="main",
                commit="abc123",
                source_tree_sha256="a" * 64,
                collection_target="generation-target",
                project_type="magento",
                source_root=source_root,
            )

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_repository_delta_requires_complete_exact_base_binding(self):
        with pytest.raises(ValueError, match="repository delta requires"):
            IndexRequest(
                repo_path="/tmp/repo",
                workspace="ws",
                project="proj",
                branch="main",
                commit="next",
                base_revision="base",
                changed_paths=["src/changed.py"],
            )

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_repository_delta_normalizes_and_deduplicates_paths(self):
        request = IndexRequest(
            repo_path="/tmp/repo",
            workspace="ws",
            project="proj",
            branch="main",
            commit="next",
            base_revision="base",
            base_collection_target="base-target",
            base_generation_manifest_sha256="a" * 64,
            changed_paths=[r"src\changed.py", "src/changed.py"],
            deleted_paths=["src/deleted.py"],
        )

        assert request.changed_paths == ["src/changed.py"]
        assert request.deleted_paths == ["src/deleted.py"]

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_repository_delta_rejects_overlapping_changed_and_deleted_paths(self):
        with pytest.raises(ValueError, match="must be disjoint"):
            IndexRequest(
                repo_path="/tmp/repo",
                workspace="ws",
                project="proj",
                branch="main",
                commit="next",
                base_revision="base",
                base_collection_target="base-target",
                base_generation_manifest_sha256="a" * 64,
                changed_paths=["src/file.py"],
                deleted_paths=["src/file.py"],
            )


class TestCodeSearchRequest:

    def test_exact_generation_binding_is_required(self):
        request = CodeSearchRequest(
            query="UserService",
            workspace="ws",
            project="proj",
            branch="main",
            repository_revision="abc123",
            repository_generation_manifest_sha256="a" * 64,
            collection_target="generation-target",
        )
        assert request.limit is None
        assert request.repository_revision == "abc123"

    def test_limit_is_bounded(self):
        with pytest.raises(ValueError):
            CodeSearchRequest(
                query="UserService",
                workspace="ws",
                project="proj",
                branch="main",
                repository_revision="abc123",
                repository_generation_manifest_sha256="a" * 64,
                collection_target="generation-target",
                limit=5001,
            )


class TestReviewContextRequest:

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_host_paths_and_bounded_focus_are_normalized(self):
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
            focus_paths=[r"src\service.py", "src/service.py"],
            question="Who calls Service.run?",
            focus_symbols=[" Service.run ", "Service.run"],
        )

        assert request.focus_paths == ["src/service.py"]
        assert request.focus_symbols == ["Service.run"]
        assert request.base_collection_target == "sealed-base-target"
        assert request.base_generation_manifest_sha256 == "a" * 64
        assert request.max_relations == 32
        assert request.max_source_windows == 6
        assert request.max_source_characters == 12000

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_base_generation_binding_remains_optional_for_compatibility(self):
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

        assert request.base_collection_target is None
        assert request.base_generation_manifest_sha256 is None

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_focus_path_traversal_is_rejected(self):
        with pytest.raises(ValueError, match="repository-relative"):
            ReviewContextRequest(
                workspace="ws",
                project="project",
                target_branch="main",
                base_revision="base",
                source_revision="source",
                target_repo_path="/tmp/target",
                review_overlay_path="/tmp/overlay",
                focus_paths=["../outside.py"],
                question="Review the change",
            )


class TestParseModels:

    def test_parse_file_request(self):
        req = ParseFileRequest(path="main.py", content="print('hello')")
        assert req.language is None

    def test_parsed_file_metadata_defaults(self):
        meta = ParsedFileMetadata(path="main.py")
        assert meta.imports == []
        assert meta.extends == []
        assert meta.success is True
        assert meta.error is None

    def test_parse_batch_request(self):
        req = ParseBatchRequest(files=[
            ParseFileRequest(path="a.py", content="x = 1"),
            ParseFileRequest(path="b.py", content="y = 2"),
        ])
        assert len(req.files) == 2


class TestRepositoryIndexInspectionModels:

    def test_graph_request_defaults(self):
        req = RepositoryIndexGraphRequest(collection_target="generation-target")
        assert req.limit == 160
        assert req.scan_limit == 2500
        assert req.filters.branches == []

    def test_graph_limits_are_bounded(self):
        with pytest.raises(ValueError):
            RepositoryIndexGraphRequest(collection_target="generation-target", limit=5001)

        with pytest.raises(ValueError):
            RepositoryIndexGraphRequest(collection_target="generation-target", scan_limit=99)

    def test_node_neighbor_limit_is_bounded(self):
        req = RepositoryIndexNodeRequest(collection_target="generation-target", neighbor_limit=40)
        assert req.neighbor_limit == 40

        with pytest.raises(ValueError):
            RepositoryIndexNodeRequest(collection_target="generation-target", neighbor_limit=500)
