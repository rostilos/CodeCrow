"""Source context is selected by complete structural ownership, not length."""

import json

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from service.review.review_service import ReviewPart, ReviewService
from service.review.review_stages import anchor_ranges
from service.review.local_source import LocalReviewSource
from service.review.verification_cases import source_for_case


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

    source.read.assert_called_once_with("service.py", side="proposed", start_line=1, end_line=9010)
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


@pytest.mark.asyncio
async def test_adjacent_owners_remain_separate_when_reused_by_different_cases():
    first = ReviewPart("first", "service.py", "@@ -2 +2 @@\n-old\n+new", {2: "new"}, "proposed")
    second = ReviewPart("second", "service.py", "@@ -6 +6 @@\n-before\n+after", {6: "after"}, "proposed")
    content = "def first():\nnew\nfirst_tail\nend_first\ndef second():\nafter\nsecond_tail\nend_second\n"
    lines = content.splitlines(keepends=True)

    def read(path, *, side, start_line, end_line):
        return {"status": "ready", "path": path, "side": side, "startLine": start_line,
                "endLine": end_line, "content": "".join(lines[start_line - 1:end_line])}

    source = SimpleNamespace(read=Mock(side_effect=read))
    service = ReviewService(rag_client=SimpleNamespace(enabled=False))
    graph = {"first": {"units": [{"unitId": "a", "path": "service.py", "startLine": 1, "endLine": 4}]},
             "second": {"units": [{"unitId": "b", "path": "service.py", "startLine": 5, "endLine": 8}]}}

    records = await service._owner_source([first, second], graph, source, {})

    assert len(records) == 2
    assert source_for_case([first], records) == records[:1]
    assert source_for_case([second], records) == records[1:]
    assert "".join(record["content"] for record in records) == content


def local_source(tmp_path):
    target, overlay = tmp_path / "target", tmp_path / "overlay"
    target.mkdir()
    (overlay / "files").mkdir(parents=True)
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": ["service.py"], "deletedFiles": []}))
    (target / "service.py").write_text("target opening\nold\ntarget closing\n")
    (overlay / "files/service.py").write_text("proposed opening\nnew\nproposed closing\n")
    return LocalReviewSource({"target_repo_path": str(target), "review_overlay_path": str(overlay)})


@pytest.mark.asyncio
@pytest.mark.parametrize("graph", [{}, {"hunk": {"units": [], "structuralOwnershipComplete": False}},
                                   {"hunk": {"units": [{"unitId": "incomplete", "path": "service.py", "startLine": 2,
                                                         "endLine": 2}], "structuralOwnershipComplete": False}}])
async def test_graphless_or_partial_ownership_supplies_actual_complete_file(tmp_path, graph):
    source = local_source(tmp_path)
    service = ReviewService(rag_client=SimpleNamespace(enabled=False))

    records = await service._owner_source([part()], graph, source, {})

    assert len(records) == 1
    assert records[0]["content"] == "proposed opening\nnew\nproposed closing\n"
    assert records[0]["side"] == "proposed"
    assert source_for_case([part()], records) == records


@pytest.mark.asyncio
async def test_deletion_owner_uses_target_source_and_never_proposed_graph_coordinates(tmp_path):
    source = local_source(tmp_path)
    service = ReviewService(rag_client=SimpleNamespace(enabled=False))
    deleted = ReviewPart("hunk", "service.py", "@@ -2 +1,0 @@\n-old", {2: "old"}, "target")

    records = await service._owner_source([deleted], context((1, 3)), source, {})

    assert len(records) == 1
    assert records[0]["side"] == "target"
    assert records[0]["content"] == "target opening\nold\ntarget closing\n"
    assert source_for_case([deleted], records) == records


@pytest.mark.asyncio
async def test_target_source_failure_does_not_substitute_proposed_graph_source():
    rag = SimpleNamespace(get_review_file_content=AsyncMock())
    source = SimpleNamespace(read=Mock(return_value={"status": "unavailable", "path": "service.py", "side": "target"}))
    service = ReviewService(rag_client=rag)
    deleted = ReviewPart("hunk", "service.py", "@@ -2 +1,0 @@\n-old", {2: "old"}, "target")

    records = await service._owner_source([deleted], {}, source, {"review_collection_target": "tenant-receipt"})

    assert records[0]["status"] == "unavailable"
    rag.get_review_file_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_graphless_scope_can_read_complete_bound_graph_file_when_local_is_unavailable():
    rag = SimpleNamespace(get_review_file_content=AsyncMock(return_value={
        "status": "ready", "path": "service.py", "side": "proposed", "startLine": 1,
        "endLine": 3, "content": "opening\nnew\nclosing\n"}))
    source = SimpleNamespace(read=Mock(return_value={"status": "unavailable"}))
    service = ReviewService(rag_client=rag)

    records = await service._owner_source([part()], {}, source, {"review_collection_target": "tenant-receipt"})

    assert records[0]["content"] == "opening\nnew\nclosing\n"
    assert rag.get_review_file_content.await_args.kwargs == {
        "review_collection_target": "tenant-receipt", "focus_paths": ["service.py"],
        "path": "service.py", "side": "proposed", "start_line": 1, "end_line": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["proposed", "target"])
async def test_full_file_already_supplied_in_diff_is_not_repeated(tmp_path, side):
    source = local_source(tmp_path)
    content = source.read("service.py", side=side)["content"]
    marker = "+" if side == "proposed" else "-"
    header = "@@ -0,0 +1,3 @@" if side == "proposed" else "@@ -1,3 +0,0 @@"
    full_diff = header + "\n" + "".join(marker + line for line in content.splitlines(keepends=True))
    complete = ReviewPart("hunk", "service.py", full_diff,
                          dict(enumerate(content.splitlines(), 1)), side)
    service = ReviewService(rag_client=SimpleNamespace(enabled=False))

    assert await service._owner_source([complete], {}, source, {}) == []


@pytest.mark.asyncio
async def test_file_scope_reads_once_for_multiple_changed_hunks(tmp_path):
    source = local_source(tmp_path)
    source.read = Mock(wraps=source.read)
    service = ReviewService(rag_client=SimpleNamespace(enabled=False))
    other = ReviewPart("other", "service.py", "@@ -3 +3 @@\n-old tail\n+proposed closing", {3: "proposed closing"}, "proposed")

    records = await service._owner_source([part(), other], {}, source, {})

    source.read.assert_called_once_with("service.py", side="proposed", start_line=1, end_line=None)
    assert records[0]["content"].endswith("proposed closing\n")
