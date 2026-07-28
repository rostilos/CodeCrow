"""
Unit tests for rag_pipeline.api.models — Pydantic request/response models.
"""
import os
import pytest
from unittest.mock import patch

from rag_pipeline.api.models import (
    IndexRequest,
    QueryRequest,
    PRContextRequest,
    DeterministicContextRequest,
    ParseFileRequest,
    ParseBatchRequest,
    ParsedFileMetadata,
    PRFileInfo,
    PRIndexRequest,
    EstimateRequest,
    EstimateResponse,
    DeleteBranchRequest,
    DeleteFilesRequest,
    ApplyChangesRequest,
    UpdateFilesRequest,
    CleanupStaleBranchesRequest,
    VectorGraphRequest,
    VectorNodeRequest,
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
        )
        assert req.workspace == "ws"
        assert req.preserve_other_branches is False
        assert req.cleanup_repo_path is False

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_other_branch_preservation_requires_explicit_opt_in(self):
        req = IndexRequest(
            repo_path="/tmp/repo",
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
            preserve_other_branches=True,
        )
        assert req.preserve_other_branches is True

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_queue_consumer_cleanup_requires_explicit_opt_in(self):
        req = IndexRequest(
            repo_path="/tmp/codecrow-rag-owned",
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
            cleanup_repo_path=True,
        )
        assert req.cleanup_repo_path is True

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="Path must be under"):
            IndexRequest(
                repo_path="/etc/passwd",
                workspace="ws",
                project="proj",
                branch="main",
                commit="abc123",
            )


class TestIncrementalFileRequests:

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_update_rejects_repository_path_traversal(self):
        with pytest.raises(ValueError, match="repository-relative"):
            UpdateFilesRequest(
                file_paths=["../etc/passwd"],
                repo_base="/tmp/repo",
                workspace="ws",
                project="project",
                branch="main",
                commit="abc123",
            )

    def test_delete_carries_commit_and_rejects_absolute_paths(self):
        request = DeleteFilesRequest(
            file_paths=["app/code/Acme/Module/etc/di.xml"],
            workspace="ws",
            project="project",
            branch="main",
            commit="abc123",
        )
        assert request.commit == "abc123"

        with pytest.raises(ValueError, match="repository-relative"):
            DeleteFilesRequest(
                file_paths=["/etc/passwd"],
                workspace="ws",
                project="project",
                branch="main",
            )

    @patch.dict(os.environ, {"ALLOWED_REPO_ROOT": "/tmp"})
    def test_change_set_validates_both_path_sets(self):
        request = ApplyChangesRequest(
            updated_file_paths=["src/Updated.py"],
            deleted_file_paths=["src/Deleted.py"],
            repo_base="/tmp/repository",
            workspace="ws",
            project="project",
            branch="main",
            commit="abc123",
        )
        assert request.updated_file_paths == ["src/Updated.py"]
        assert request.deleted_file_paths == ["src/Deleted.py"]

        with pytest.raises(ValueError, match="repository-relative"):
            ApplyChangesRequest(
                updated_file_paths=["src/Updated.py"],
                deleted_file_paths=["../Deleted.py"],
                repo_base="/tmp/repository",
                workspace="ws",
                project="project",
                branch="main",
                commit="abc123",
            )


class TestPRContextRequest:

    def test_valid_request(self):
        req = PRContextRequest(
            workspace="ws",
            project="proj",
            changed_files=["src/main.py"],
        )
        assert req.top_k == 15  # default
        assert req.enable_priority_reranking is True

    def test_too_many_files_rejected(self):
        with patch.dict(os.environ, {"RAG_MAX_FILES_PER_REQUEST": "5"}):
            with pytest.raises(ValueError, match="Too many changed files"):
                PRContextRequest(
                    workspace="ws",
                    project="proj",
                    changed_files=[f"file{i}.py" for i in range(10)],
                )

    def test_too_many_snippets_rejected(self):
        with patch.dict(os.environ, {"RAG_MAX_SNIPPETS_PER_REQUEST": "2"}):
            with pytest.raises(ValueError, match="Too many diff snippets"):
                PRContextRequest(
                    workspace="ws",
                    project="proj",
                    changed_files=["a.py"],
                    diff_snippets=["s1", "s2", "s3"],
                )

    def test_defaults(self):
        req = PRContextRequest(
            workspace="ws",
            project="proj",
            changed_files=["a.py"],
        )
        assert req.diff_snippets == []
        assert req.deleted_files == []
        assert req.min_relevance_score == 0.7


class TestDeterministicContextRequest:

    def test_basic_construction(self):
        req = DeterministicContextRequest(
            workspace="ws",
            project="proj",
            branches=["main"],
            file_paths=["src/main.py"],
        )
        assert req.limit_per_file == 10
        assert req.additional_identifiers is None

    def test_with_additional_identifiers(self):
        req = DeterministicContextRequest(
            workspace="ws",
            project="proj",
            branches=["main"],
            file_paths=["a.py"],
            additional_identifiers=["UserService", "OrderRepository"],
        )
        assert len(req.additional_identifiers) == 2


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


class TestPRIndexRequest:

    def test_construction(self):
        req = PRIndexRequest(
            workspace="ws",
            project="proj",
            pr_number=42,
            branch="feature",
            source_revision="head-commit",
            base_revision="base-commit",
            files=[
                PRFileInfo(path="src/main.py", content="x = 1", change_type="MODIFIED"),
            ],
        )
        assert req.pr_number == 42
        assert req.source_revision == "head-commit"
        assert req.base_revision == "base-commit"
        assert len(req.files) == 1
        assert req.files[0].change_type == "MODIFIED"
        assert req.files[0].content_state == "complete"

    def test_partial_diff_state_is_explicit_and_validated(self):
        partial = PRFileInfo(
            path="src/main.py",
            content="@@ -1 +1 @@\n-old\n+new",
            change_type="MODIFIED",
            content_state="partial_diff",
        )

        assert partial.content_state == "partial_diff"
        assert PRFileInfo(
            path="src/main.py",
            content="x = 1",
            change_type="modified",
        ).change_type == "MODIFIED"
        with pytest.raises(ValueError):
            PRFileInfo(
                path="src/main.py",
                content="x = 1",
                change_type="MODIFIED",
                content_state="unknown",
            )
        with pytest.raises(ValueError):
            PRFileInfo(
                path="src/main.py",
                content="x = 1",
                change_type="UNKNOWN",
            )


class TestEstimateResponse:

    def test_round_trip(self):
        resp = EstimateResponse(
            file_count=100,
            estimated_chunks=500,
            max_files_allowed=50000,
            max_chunks_allowed=1000000,
            within_limits=True,
            message="OK",
        )
        data = resp.model_dump()
        restored = EstimateResponse(**data)
        assert restored.within_limits is True


class TestDeleteBranchRequest:

    def test_construction(self):
        req = DeleteBranchRequest(workspace="ws", project="proj", branch="feature/old")
        assert req.branch == "feature/old"


class TestCleanupStaleBranches:

    def test_requires_authoritative_branches(self):
        with pytest.raises(ValueError):
            CleanupStaleBranchesRequest(workspace="ws", project="proj")
        with pytest.raises(ValueError):
            CleanupStaleBranchesRequest(
                workspace="ws",
                project="proj",
                protected_branches=[],
            )

    def test_preserves_explicit_branch_identity(self):
        req = CleanupStaleBranchesRequest(
            workspace="ws",
            project="proj",
            protected_branches=["synthetic-target"],
        )
        assert req.protected_branches == ["synthetic-target"]
        assert req.branches_to_keep is None

    @pytest.mark.parametrize("branches", [[""], [" main"], ["main", "main"]])
    def test_rejects_invalid_branch_identities(self, branches):
        with pytest.raises(ValueError):
            CleanupStaleBranchesRequest(
                workspace="ws",
                project="proj",
                protected_branches=branches,
            )


class TestVectorStorageInspectionModels:

    def test_graph_request_defaults(self):
        req = VectorGraphRequest()
        assert req.limit == 160
        assert req.scan_limit == 2500
        assert req.filters.include_pr is True

    def test_graph_limits_are_bounded(self):
        with pytest.raises(ValueError):
            VectorGraphRequest(limit=5001)

        with pytest.raises(ValueError):
            VectorGraphRequest(scan_limit=99)

    def test_node_neighbor_limit_is_bounded(self):
        req = VectorNodeRequest(neighbor_limit=40)
        assert req.neighbor_limit == 40

        with pytest.raises(ValueError):
            VectorNodeRequest(neighbor_limit=500)
