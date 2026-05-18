"""
Tests for rag_pipeline.core.index_manager.branch_manager — BranchManager.

Covers:
- delete_branch_points (success, failure)
- get_branch_point_count (success, error)
- get_indexed_branches (pagination, empty, error)
- preserve_other_branch_points (streaming generator, error)
- stream_copy_points_to_collection (success, dimension mismatch, copy)
- copy_points_to_collection (batching)
"""
import pytest
from unittest.mock import patch, MagicMock, call
from types import SimpleNamespace

from rag_pipeline.core.index_manager.branch_manager import BranchManager


def _make_point(branch, path="a.py", point_id="p1"):
    p = SimpleNamespace()
    p.id = point_id
    p.payload = {"branch": branch, "path": path}
    p.vector = [0.1, 0.2]
    return p


# ─────────────────────────────────────────────────────────────
# delete_branch_points
# ─────────────────────────────────────────────────────────────
class TestDeleteBranchPoints:

    def test_success(self):
        client = MagicMock()
        bm = BranchManager(client)
        result = bm.delete_branch_points("coll", "feat")
        assert result is True
        client.delete.assert_called_once()

    def test_failure(self):
        client = MagicMock()
        client.delete.side_effect = RuntimeError("err")
        bm = BranchManager(client)
        result = bm.delete_branch_points("coll", "feat")
        assert result is False


# ─────────────────────────────────────────────────────────────
# get_branch_point_count
# ─────────────────────────────────────────────────────────────
class TestGetBranchPointCount:

    def test_success(self):
        client = MagicMock()
        client.count.return_value = SimpleNamespace(count=42)
        bm = BranchManager(client)
        assert bm.get_branch_point_count("coll", "main") == 42

    def test_error_returns_zero(self):
        client = MagicMock()
        client.count.side_effect = RuntimeError("err")
        bm = BranchManager(client)
        assert bm.get_branch_point_count("coll", "main") == 0


# ─────────────────────────────────────────────────────────────
# get_indexed_branches
# ─────────────────────────────────────────────────────────────
class TestGetIndexedBranches:

    def test_single_page(self):
        client = MagicMock()
        pt1 = _make_point("main")
        pt2 = _make_point("dev")
        client.scroll.return_value = ([pt1, pt2], None)

        bm = BranchManager(client)
        branches = bm.get_indexed_branches("coll")
        assert set(branches) == {"main", "dev"}

    def test_multiple_pages(self):
        client = MagicMock()
        # First page must return exactly `limit` (100) points so the loop continues
        page1 = [_make_point("main", point_id=f"p{i}") for i in range(100)]
        page2 = [_make_point("dev", point_id="extra")]

        client.scroll.side_effect = [
            (page1, "offset_2"),
            (page2, None),
        ]

        bm = BranchManager(client)
        branches = bm.get_indexed_branches("coll")
        assert set(branches) == {"main", "dev"}

    def test_empty_collection(self):
        client = MagicMock()
        client.scroll.return_value = ([], None)
        bm = BranchManager(client)
        assert bm.get_indexed_branches("coll") == []

    def test_error_returns_empty(self):
        client = MagicMock()
        client.scroll.side_effect = RuntimeError("err")
        bm = BranchManager(client)
        assert bm.get_indexed_branches("coll") == []

    def test_points_without_branch_payload(self):
        client = MagicMock()
        pt = SimpleNamespace(id="p1", payload={}, vector=[0.1])
        client.scroll.return_value = ([pt], None)
        bm = BranchManager(client)
        assert bm.get_indexed_branches("coll") == []


# ─────────────────────────────────────────────────────────────
# preserve_other_branch_points
# ─────────────────────────────────────────────────────────────
class TestPreserveOtherBranchPoints:

    def test_yields_batches(self):
        client = MagicMock()
        pt1 = _make_point("dev", point_id="p1")
        pt2 = _make_point("staging", point_id="p2")

        client.scroll.side_effect = [
            ([pt1, pt2], None),
        ]

        bm = BranchManager(client)
        batches = list(bm.preserve_other_branch_points("coll", "main", batch_size=10))
        assert len(batches) == 1
        assert len(batches[0]) == 2

    def test_multiple_pages(self):
        client = MagicMock()
        pt1 = _make_point("dev", point_id="p1")
        pt2 = _make_point("dev", point_id="p2")

        client.scroll.side_effect = [
            ([pt1], "next"),
            ([pt2], None),
        ]

        bm = BranchManager(client)
        batches = list(bm.preserve_other_branch_points("coll", "main", batch_size=1))
        assert len(batches) == 2

    def test_empty_returns_nothing(self):
        client = MagicMock()
        client.scroll.return_value = ([], None)
        bm = BranchManager(client)
        batches = list(bm.preserve_other_branch_points("coll", "main"))
        assert batches == []

    def test_error_handled(self):
        client = MagicMock()
        client.scroll.side_effect = RuntimeError("err")
        bm = BranchManager(client)
        batches = list(bm.preserve_other_branch_points("coll", "main"))
        assert batches == []


# ─────────────────────────────────────────────────────────────
# stream_copy_points_to_collection
# ─────────────────────────────────────────────────────────────
class TestStreamCopyPointsToCollection:

    def test_success(self):
        client = MagicMock()

        # Simulate matching dimensions
        source_info = MagicMock()
        source_info.config.params.vectors.size = 768
        target_info = MagicMock()
        target_info.config.params.vectors.size = 768
        client.get_collection.side_effect = [source_info, target_info]

        pt = _make_point("dev", point_id="p1")
        client.scroll.return_value = ([pt], None)

        bm = BranchManager(client)
        total = bm.stream_copy_points_to_collection("src_coll", "tgt_coll", "main", 50)
        assert total == 1
        client.upsert.assert_called_once()

    def test_dimension_mismatch_skips(self):
        client = MagicMock()

        source_info = MagicMock()
        source_info.config.params.vectors.size = 768
        target_info = MagicMock()
        target_info.config.params.vectors.size = 1536
        client.get_collection.side_effect = [source_info, target_info]

        bm = BranchManager(client)
        total = bm.stream_copy_points_to_collection("src_coll", "tgt_coll", "main", 50)
        assert total == 0
        client.upsert.assert_not_called()

    def test_get_collection_error_continues(self):
        client = MagicMock()
        client.get_collection.side_effect = RuntimeError("err")
        pt = _make_point("dev", point_id="p1")
        client.scroll.return_value = ([pt], None)

        bm = BranchManager(client)
        total = bm.stream_copy_points_to_collection("src_coll", "tgt_coll", "main", 50)
        assert total == 1


# ─────────────────────────────────────────────────────────────
# copy_points_to_collection
# ─────────────────────────────────────────────────────────────
class TestCopyPointsToCollection:

    def test_batched_upsert(self):
        client = MagicMock()
        bm = BranchManager(client)

        points = [_make_point("dev", point_id=f"p{i}") for i in range(5)]
        bm.copy_points_to_collection(points, "tgt_coll", batch_size=2)

        assert client.upsert.call_count == 3  # ceil(5/2) = 3 batches

    def test_empty_points(self):
        client = MagicMock()
        bm = BranchManager(client)
        bm.copy_points_to_collection([], "tgt_coll")
        client.upsert.assert_not_called()
