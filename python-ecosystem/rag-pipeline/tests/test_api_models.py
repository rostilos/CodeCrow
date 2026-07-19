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
    ContextSnapshotV1,
    EstimateRequest,
    EstimateResponse,
    DeleteBranchRequest,
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


class TestQueryRequest:

    def test_snapshot_requires_revision_inside_snapshot(self):
        snapshot = ContextSnapshotV1(
            base_sha="a" * 40,
            head_sha="b" * 40,
            merge_base_sha="c" * 40,
        )

        with pytest.raises(ValueError, match="requires revision"):
            QueryRequest(
                query="find dependency",
                workspace="ws",
                project="proj",
                branch="main",
                snapshot=snapshot,
            )
        with pytest.raises(ValueError, match="outside snapshot"):
            QueryRequest(
                query="find dependency",
                workspace="ws",
                project="proj",
                branch="main",
                revision="d" * 40,
                snapshot=snapshot,
            )

        request = QueryRequest(
            query="find dependency",
            workspace="ws",
            project="proj",
            branch="main",
            revision=snapshot.base_sha,
            snapshot=snapshot,
        )
        assert request.snapshot.parser_version == snapshot.parser_version


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

    def test_exact_overlay_requires_both_branches_and_execution(self):
        snapshot = ContextSnapshotV1(
            base_sha="a" * 40,
            head_sha="b" * 40,
            merge_base_sha="c" * 40,
        )
        with pytest.raises(ValueError, match="source and base branch"):
            DeterministicContextRequest(
                workspace="ws",
                project="proj",
                branches=["feature"],
                file_paths=["a.py"],
                snapshot=snapshot,
            )
        with pytest.raises(ValueError, match="require.*execution_id"):
            DeterministicContextRequest(
                workspace="ws",
                project="proj",
                branches=["feature", "main"],
                file_paths=["a.py"],
                snapshot=snapshot,
                pr_number=42,
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


class TestPRIndexRequest:

    def test_construction(self):
        req = PRIndexRequest(
            workspace="ws",
            project="proj",
            pr_number=42,
            branch="feature",
            files=[
                PRFileInfo(path="src/main.py", content="x = 1", change_type="MODIFIED"),
            ],
        )
        assert req.pr_number == 42
        assert len(req.files) == 1
        assert req.files[0].change_type == "MODIFIED"

    def test_exact_snapshot_identity_is_deterministic(self):
        snapshot = ContextSnapshotV1(
            base_sha="a" * 40,
            head_sha="b" * 40,
            merge_base_sha="c" * 40,
        )
        same = ContextSnapshotV1(**snapshot.model_dump())

        assert snapshot.identity == same.identity
        assert len(snapshot.identity) == 64

    def test_pr_index_accepts_exact_snapshot(self):
        snapshot = ContextSnapshotV1(
            base_sha="a" * 40,
            head_sha="b" * 40,
            merge_base_sha="c" * 40,
        )
        req = PRIndexRequest(
            workspace="ws",
            project="proj",
            pr_number=42,
            branch="feature",
            files=[],
            snapshot=snapshot,
            execution_id="review-42",
        )

        assert req.snapshot is snapshot
        assert req.snapshot.head_sha == "b" * 40


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

    def test_default_protected(self):
        req = CleanupStaleBranchesRequest(workspace="ws", project="proj")
        assert "main" in req.protected_branches
        assert "master" in req.protected_branches


class TestVectorStorageInspectionModels:

    def test_graph_request_defaults(self):
        req = VectorGraphRequest()
        assert req.limit == 160
        assert req.scan_limit == 2500
        assert req.filters.include_pr is True

    def test_graph_limits_are_bounded(self):
        assert VectorGraphRequest(limit=5000).limit == 5000

        with pytest.raises(ValueError):
            VectorGraphRequest(limit=5001)

        with pytest.raises(ValueError):
            VectorGraphRequest(scan_limit=10)

    def test_node_neighbor_limit_is_bounded(self):
        req = VectorNodeRequest(neighbor_limit=40)
        assert req.neighbor_limit == 40

        with pytest.raises(ValueError):
            VectorNodeRequest(neighbor_limit=500)
