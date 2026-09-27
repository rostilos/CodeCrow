"""Source context is selected by complete structural ownership, not length."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from service.review.review_service import ReviewPart, ReviewService
from service.review.review_stages import anchor_ranges


def part():
    return ReviewPart("hunk", "service.py", "@@ -2 +2 @@\n-old\n+new", {2: "new"}, "proposed")


def context(*ranges):
    return {"hunk": {"units": [
        {"unitId": str(index), "path": "service.py", "startLine": start, "endLine": end}
        for index, (start, end) in enumerate(ranges)
    ]}}


@pytest.mark.asyncio
async def test_overlapping_owners_read_once_without_clipping():
    complete_source = "required source\n" * 9000
    source = SimpleNamespace(read=Mock(return_value={"status": "ready", "content": complete_source}))
    service = ReviewService(rag_client=SimpleNamespace(enabled=False))

    result = await service._owner_source([part()], context((1, 9000), (2, 7), (8990, 9010)), source, {})

    source.read.assert_called_once_with("service.py", start_line=1, end_line=9010)
    assert result[0]["content"] == complete_source


@pytest.mark.asyncio
async def test_owner_already_visible_in_diff_does_not_repeat_source():
    source = SimpleNamespace(read=Mock())
    service = ReviewService(rag_client=SimpleNamespace(enabled=False))

    assert await service._owner_source([part()], context((2, 2)), source, {}) == []
    source.read.assert_not_called()


@pytest.mark.asyncio
async def test_separate_owners_do_not_pull_unrelated_source_between_them():
    source = SimpleNamespace(read=Mock(return_value={"status": "ready", "content": "definition"}))
    service = ReviewService(rag_client=SimpleNamespace(enabled=False))

    await service._owner_source([part()], context((1, 8), (90, 98)), source, {})

    assert [(call.kwargs["start_line"], call.kwargs["end_line"]) for call in source.read.call_args_list] == [(1, 8), (90, 98)]


@pytest.mark.asyncio
async def test_local_unavailability_can_fall_back_to_bound_graph_source():
    rag = SimpleNamespace(get_review_file_content=AsyncMock(return_value={"status": "ready", "content": "complete source"}))
    source = SimpleNamespace(read=Mock(return_value={"status": "unavailable"}))
    service = ReviewService(rag_client=rag)
    binding = {"review_collection_target": "tenant-owned-receipt"}

    result = await service._owner_source([part()], context((1, 5)), source, binding)

    assert result[0]["content"] == "complete source"
    assert rag.get_review_file_content.await_args.kwargs["review_collection_target"] == "tenant-owned-receipt"


def test_anchor_ranges_are_lossless_even_across_deleted_gaps():
    lines = [1, 2, 3, 8, 10, 11]
    spans = anchor_ranges(lines)
    assert spans == [[1, 3], [8, 8], [10, 11]]
    assert [line for start, end in spans for line in range(start, end + 1)] == lines
