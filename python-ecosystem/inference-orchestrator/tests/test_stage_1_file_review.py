"""
Tests for pure helper functions in stage_1_file_review.py.

Covers: chunk_files, _deduplicate_pr_stale_chunks,
        _build_duplication_queries_from_diff,
        _scope_deterministic_to_diff, _extract_calibrated_issues,
        create_smart_batches_wrapper
"""
import pytest
import asyncio
import logging
import time
from unittest.mock import MagicMock, patch, AsyncMock

from service.review.orchestrator.stage_1_file_review import (
    chunk_files,
    Stage1PreparedContext,
    _build_stage_1_prepared_context,
    _bounded_current_file_context,
    _diff_contains_complete_added_source,
    _find_diff_file_for_path,
    _split_hunk_by_lines,
    _chunk_diff_preserving_hunks,
    _expand_oversized_diff_batches,
    _format_batch_metadata_json,
    _iter_batch_enrichment_metadata,
    _extract_metadata_identifiers,
    _flatten_deterministic_context,
    _rag_context_has_chunks,
    Stage1RagState,
    Stage1ContinuationAdmission,
    Stage1ReviewUnitState,
    fetch_batch_rag_context,
    execute_stage_1_file_reviews,
    review_file_batch,
    _resolve_fallback_rag_context,
    _scope_fallback_rag_context_to_batch,
    _chunk_matches_batch_path,
    _supports_structured_output,
    _deduplicate_pr_stale_chunks,
    _build_duplication_queries_from_diff,
    _scope_deterministic_to_diff,
    _extract_calibrated_issues,
    _invoke_stage_1_batch_llm,
    _bind_cross_file_request_origins,
    _drop_matching_request_dependent_issues,
    create_smart_batches_wrapper,
)
from service.review.orchestrator.exact_context import ReviewFollowupBudget
from model.multi_stage import (
    FileGroup,
    ReviewFile,
    FileReviewBatchOutput,
    FileReviewOutput,
    ReviewContextRequest,
    ReviewPlan,
)
from model.output_schemas import CodeReviewIssue
from utils.diff_processor import DiffChangeType, DiffFile, DiffProcessor, ProcessedDiff
from utils.prompts.prompt_builder import PromptBuilder


def _context_test_issue(path: str, title: str = "Candidate") -> CodeReviewIssue:
    return CodeReviewIssue(
        severity="MEDIUM",
        category="BUG_RISK",
        file=path,
        line=1,
        title=title,
        reason="A concrete runtime failure remains.",
        suggestedFixDescription="Correct the incompatible call.",
        codeSnippet="changed_call()",
    )


@pytest.mark.asyncio
async def test_continuation_admission_uses_batch_order_not_response_timing():
    budget = ReviewFollowupBudget(max_calls=1)
    admission = Stage1ContinuationAdmission(3, budget)

    third = asyncio.create_task(admission.acquire(3, "batch-3"))
    await asyncio.sleep(0)
    second = asyncio.create_task(admission.acquire(2, "batch-2"))
    await asyncio.sleep(0)
    assert not second.done()
    assert not third.done()

    await admission.skip(1)
    assert await asyncio.wait_for(second, timeout=1) is True
    assert not third.done()

    await admission.commit(2)
    assert await asyncio.wait_for(third, timeout=1) is False
    assert budget.summary()["entries"] == [{
        "kind": "stage_1_exact_continuation",
        "sourceKey": "batch-2",
        "state": "committed",
    }]


def test_cross_file_request_keeps_host_bound_origin_after_candidate_withheld():
    issue = _context_test_issue("src/api.py")
    request = ReviewContextRequest(
        requestId="ctx-1",
        kind="CROSS_FILE",
        question="Does src/client.py still call the changed API?",
        targetPath="src/client.py",
        relationship="src/api.py -> src/client.py call contract",
        requiredEvidence="The exact current caller invocation.",
        relatedIssueIndexes=[0],
        originatingPaths=["untrusted/model/path.py"],
    )

    bound = _bind_cross_file_request_origins(
        [request],
        [issue],
        ["src/api.py", "src/other.py"],
    )

    assert bound[0].originatingPaths == ["src/api.py"]
    assert _drop_matching_request_dependent_issues(
        [issue.model_copy()],
        [issue],
        bound,
    ) == []


# ── chunk_files ──────────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
async def test_batch_llm_attempt_details_do_not_duplicate_owner_warning(caplog):
    class FailingLlm:
        def with_structured_output(self, _schema):
            return self

        async def ainvoke(self, _prompt):
            raise RuntimeError("provider unavailable")

    with caplog.at_level(logging.DEBUG):
        result = await _invoke_stage_1_batch_llm(
            FailingLlm(), "prompt", ["src/a.py"], "capped"
        )

    assert result is None
    assert not [
        record for record in caplog.records
        if record.levelno >= logging.WARNING
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_batch_output_cannot_omit_a_mandatory_file():
    incomplete = FileReviewBatchOutput(reviews=[FileReviewOutput(
        file="src/a.py",
        analysis_summary="reviewed",
        issues=[],
        confidence="HIGH",
    )])
    llm = MagicMock()
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=incomplete)
    llm.with_structured_output.return_value = structured

    result = await _invoke_stage_1_batch_llm(
        llm,
        PromptBuilder.build_stage_1_batch_prompt(
            files=[
                {"path": "src/a.py", "diff": "+a"},
                {"path": "src/b.py", "diff": "+b"},
            ],
            priority="MEDIUM",
        ),
        ["src/a.py", "src/b.py"],
        "capped",
    )

    assert result is None


class TestChunkFiles:
    def _make_groups(self, paths_per_group):
        groups = []
        for gid, paths in enumerate(paths_per_group):
            files = [ReviewFile(path=p, focus_areas=[], risk_level="MEDIUM") for p in paths]
            groups.append(
                FileGroup(group_id=f"g{gid}", priority="MEDIUM", rationale="test", files=files)
            )
        return groups

    def test_single_small_group(self):
        groups = self._make_groups([["a.py", "b.py"]])
        batches = chunk_files(groups, max_files_per_batch=5)
        assert len(batches) == 1
        assert len(batches[0]) == 2

    def test_group_exceeds_batch_size(self):
        groups = self._make_groups([["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"]])
        batches = chunk_files(groups, max_files_per_batch=3)
        assert len(batches) == 2
        assert len(batches[0]) == 3
        assert len(batches[1]) == 3

    def test_multiple_groups_fit(self):
        groups = self._make_groups([["a.py"], ["b.py"]])
        batches = chunk_files(groups, max_files_per_batch=5)
        assert len(batches) == 1
        assert len(batches[0]) == 2

    def test_empty_groups(self):
        batches = chunk_files([], max_files_per_batch=5)
        assert batches == []

    def test_groups_split_across_batches(self):
        groups = self._make_groups([["a.py", "b.py", "c.py"], ["d.py", "e.py", "f.py"]])
        batches = chunk_files(groups, max_files_per_batch=3)
        assert len(batches) == 2

    def test_batch_size_one(self):
        groups = self._make_groups([["a.py", "b.py"]])
        batches = chunk_files(groups, max_files_per_batch=1)
        assert len(batches) == 2
        assert len(batches[0]) == 1
        assert len(batches[1]) == 1


# ── Stage 1 prepared context ────────────────────────────────────

class TestStage1PreparedContext:
    def test_diff_lookup_uses_suffix_index(self):
        request = MagicMock(deltaDiff=None, taskContext=None, enrichmentData=None)
        processed = ProcessedDiff(files=[
            DiffFile(
                path="repo/services/api/src/Foo.py",
                change_type=DiffChangeType.MODIFIED,
                content="diff --git a/repo/services/api/src/Foo.py b/repo/services/api/src/Foo.py",
            )
        ])

        context = _build_stage_1_prepared_context(request, processed, is_incremental=False)

        assert _find_diff_file_for_path(context, "services/api/src/Foo.py").path == "repo/services/api/src/Foo.py"
        assert _find_diff_file_for_path(context, "src/Foo.py").path == "repo/services/api/src/Foo.py"

    def test_current_file_content_is_indexed_for_direct_stage_1_evidence(self):
        file_content = MagicMock(
            path="repo/templates/ratings.phtml",
            content="use SwatchHelper;\n$this->helper(SwatchHelper::class);",
            skipped=False,
        )
        enrichment = MagicMock(fileContents=[file_content], fileMetadata=[])
        request = MagicMock(
            deltaDiff=None,
            taskContext=None,
            enrichmentData=enrichment,
        )

        context = _build_stage_1_prepared_context(request, None, is_incremental=False)

        assert context.file_content_by_path["templates/ratings.phtml"] == file_content.content

    def test_large_current_source_uses_post_change_hunk_windows(self, monkeypatch):
        monkeypatch.setattr(
            "service.review.orchestrator.stage_1_file_review."
            "STAGE1_MAX_CURRENT_FILE_CHARS",
            1_200,
        )
        source = "\n".join(
            f"line_{line_number}" for line_number in range(1, 401)
        )
        diff = """\
diff --git a/src/large.py b/src/large.py
--- a/src/large.py
+++ b/src/large.py
@@ -198,3 +198,3 @@
-old
+new
"""

        rendered = _bounded_current_file_context(
            source,
            diff,
            context_lines=3,
        )

        assert len(rendered) <= 1_200
        assert "[Post-change lines 195-203]" in rendered
        assert "    198: line_198" in rendered
        assert "line_1\n" not in rendered
        assert "line_400" not in rendered

    def test_large_current_source_falls_back_when_diff_has_no_hunk(self, monkeypatch):
        monkeypatch.setattr(
            "service.review.orchestrator.stage_1_file_review."
            "STAGE1_MAX_CURRENT_FILE_CHARS",
            300,
        )
        source = "start\n" + ("middle\n" * 100) + "end\n"

        rendered = _bounded_current_file_context(source, "metadata only")

        assert len(rendered) <= 300
        assert rendered.startswith("start")
        assert rendered.endswith("end\n")
        assert "Current file context truncated" in rendered

    def test_complete_added_source_requires_contiguous_lossless_diff(self):
        source = "first()\nsecond()\n"
        complete_diff = """\
diff --git a/src/new.py b/src/new.py
new file mode 100644
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+first()
+second()
"""
        partial_diff = """\
diff --git a/src/new.py b/src/new.py
new file mode 100644
--- /dev/null
+++ b/src/new.py
@@ -0,0 +2,1 @@
+second()
"""
        modified_diff = """\
diff --git a/src/new.py b/src/new.py
--- a/src/new.py
+++ b/src/new.py
@@ -1 +1 @@
-first()
+second()
"""

        assert _diff_contains_complete_added_source(source, complete_diff)
        assert not _diff_contains_complete_added_source(source, partial_diff)
        assert not _diff_contains_complete_added_source(source, modified_diff)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_batch_prompt_receives_current_file_content_without_rag(self):
        path = "templates/ratings.phtml"
        source = "use SwatchHelper;\n$this->helper(SwatchHelper::class);"
        file_content = MagicMock(path=path, content=source, skipped=False)
        enrichment = MagicMock(fileContents=[file_content], fileMetadata=[])
        request = MagicMock(
            deltaDiff=None,
            rawDiff="",
            taskContext=None,
            enrichmentData=enrichment,
            projectRules=[],
            previousCodeAnalysisIssues=[],
            changedFiles=[path],
            deletedFiles=[],
            currentCommitHash="a" * 40,
        )
        prepared = _build_stage_1_prepared_context(request, None, is_incremental=False)
        batch = [{
            "file": ReviewFile(path=path, focus_areas=["general"], risk_level="LOW"),
            "priority": "LOW",
        }]

        with patch(
            "service.review.orchestrator.stage_1_file_review._invoke_stage_1_batch_llm",
            new_callable=AsyncMock,
            return_value=[],
        ) as invoke:
            result = await review_file_batch(
                MagicMock(),
                request,
                batch,
                rag_client=None,
                prepared_context=prepared,
            )

        assert result == []
        prompt = invoke.await_args.args[1]
        assert "Current File Content (post-change" in prompt
        assert source in prompt

    @pytest.mark.asyncio(loop_scope="function")
    async def test_added_file_source_is_not_duplicated_when_diff_is_complete(self):
        path = "src/new.py"
        source = "first()\nsecond()\n"
        raw_diff = """\
diff --git a/src/new.py b/src/new.py
new file mode 100644
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+first()
+second()
"""
        file_content = MagicMock(
            path=path,
            content=source,
            skipped=False,
        )
        enrichment = MagicMock(
            fileContents=[file_content],
            fileMetadata=[],
        )
        request = MagicMock(
            deltaDiff=None,
            rawDiff=raw_diff,
            taskContext=None,
            enrichmentData=enrichment,
            projectRules=[],
            previousCodeAnalysisIssues=[],
            changedFiles=[path],
            deletedFiles=[],
            currentCommitHash="a" * 40,
        )
        processed = DiffProcessor().process(raw_diff)
        prepared = _build_stage_1_prepared_context(
            request,
            processed,
            is_incremental=False,
        )
        batch = [{
            "file": ReviewFile(
                path=path,
                focus_areas=["general"],
                risk_level="LOW",
            ),
            "priority": "LOW",
        }]

        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            new_callable=AsyncMock,
            return_value=[],
        ) as invoke:
            await review_file_batch(
                MagicMock(),
                request,
                batch,
                rag_client=None,
                prepared_context=prepared,
            )

        prompt = invoke.await_args.args[1]
        assert "Type: ADDED" in prompt
        assert (
            "[Complete post-change source is present once as the added side "
            "of the diff below"
        ) in prompt
        assert "\nfirst()\nsecond()\n\nDiff:" not in prompt
        assert "+first()\n+second()" in prompt

    @pytest.mark.asyncio(loop_scope="function")
    async def test_batch_current_source_uses_one_fair_total_budget(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "service.review.orchestrator.stage_1_file_review."
            "STAGE1_CURRENT_SOURCE_BATCH_CHAR_BUDGET",
            8_000,
        )
        paths = ["src/first.py", "src/second.py"]
        source = "start\n" + ("middle\n" * 2_000) + "end\n"
        enrichment = MagicMock(
            fileContents=[
                MagicMock(path=path, content=source, skipped=False)
                for path in paths
            ],
            fileMetadata=[],
        )
        request = MagicMock(
            deltaDiff=None,
            rawDiff="",
            taskContext=None,
            enrichmentData=enrichment,
            projectRules=[],
            previousCodeAnalysisIssues=[],
            changedFiles=paths,
            deletedFiles=[],
            currentCommitHash="a" * 40,
        )
        prepared = _build_stage_1_prepared_context(
            request,
            None,
            is_incremental=False,
        )
        batch = [
            {
                "file": ReviewFile(
                    path=path,
                    focus_areas=["general"],
                    risk_level="LOW",
                ),
                "priority": "LOW",
            }
            for path in paths
        ]

        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            new_callable=AsyncMock,
            return_value=[],
        ) as invoke:
            await review_file_batch(
                MagicMock(),
                request,
                batch,
                rag_client=None,
                prepared_context=prepared,
            )

        prompt = invoke.await_args.args[1]
        source_marker = (
            "Current File Content (post-change; may be bounded when "
            "explicitly labelled):\n"
        )
        current_source_sections = prompt.split(source_marker)[1:]
        rendered_source_chars = sum(
            len(section.split("\n\nDiff:\n", 1)[0])
            for section in current_source_sections
        )
        assert rendered_source_chars <= 8_000
        assert len(current_source_sections) == 2
        assert all(
            "Current file context truncated" in section
            for section in current_source_sections
        )

    def test_cloudflare_structured_output_disabled_by_default(self):
        ChatCloudflareOpenAI = type("ChatCloudflareOpenAI", (), {})

        assert _supports_structured_output(ChatCloudflareOpenAI()) is False

    def test_oversized_processed_diff_automatically_restores_full_raw(self):
        raw_diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -1 +1,3 @@
+first_changed_line()
+second_changed_line()
"""
        summarized = DiffFile(
            path="src/big.py",
            change_type=DiffChangeType.MODIFIED,
            content="[summary only]",
            is_skipped=False,
            skip_reason="File too large: 999999 bytes > 1",
        )
        request = MagicMock(rawDiff=raw_diff, deltaDiff=None, enrichmentData=None, taskContext=None)

        prepared = _build_stage_1_prepared_context(
            request,
            ProcessedDiff(files=[summarized]),
            is_incremental=False,
        )
        diff_file = _find_diff_file_for_path(prepared, "src/big.py")

        assert "first_changed_line" in diff_file.content
        assert "[summary only]" not in diff_file.content
        assert prepared.full_diff_index_loaded is True

        full_diff_file = _find_diff_file_for_path(
            prepared,
            "src/big.py",
            use_full_diff=True,
        )

        assert "first_changed_line" in full_diff_file.content
        assert "[summary only]" not in full_diff_file.content
        assert prepared.full_diff_index_loaded is True

    def test_globally_compacted_diff_automatically_restores_full_raw(self):
        raw_diff = """\
diff --git a/src/after_limit.py b/src/after_limit.py
--- a/src/after_limit.py
+++ b/src/after_limit.py
@@ -1 +1,3 @@
+first_changed_line()
+second_changed_line()
"""
        summarized = DiffFile(
            path="src/after_limit.py",
            change_type=DiffChangeType.MODIFIED,
            content="[summary only]",
            is_skipped=False,
            skip_reason="Would exceed total size limit: 120000",
        )
        request = MagicMock(rawDiff=raw_diff, deltaDiff=None, enrichmentData=None, taskContext=None)

        prepared = _build_stage_1_prepared_context(
            request,
            ProcessedDiff(files=[summarized]),
            is_incremental=False,
        )
        diff_file = _find_diff_file_for_path(prepared, "src/after_limit.py")

        assert "first_changed_line" in diff_file.content
        assert "[summary only]" not in diff_file.content
        assert prepared.full_diff_index_loaded is True

        full_diff_file = _find_diff_file_for_path(
            prepared,
            "src/after_limit.py",
            use_full_diff=True,
        )

        assert "second_changed_line" in full_diff_file.content
        assert "[summary only]" not in full_diff_file.content
        assert prepared.full_diff_index_loaded is True


class TestLargeDiffSegmentation:
    def test_chunk_diff_preserves_file_header_and_hunk_headers(self):
        diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -1 +1,2 @@
+aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
@@ -10 +11,2 @@
+bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"""

        chunks = _chunk_diff_preserving_hunks(diff, max_tokens=20)

        assert len(chunks) > 1
        assert all("diff --git a/src/big.py b/src/big.py" in chunk for chunk in chunks)
        assert any("@@ -1,0 +1,1 @@" in chunk for chunk in chunks)
        assert any("@@ -10,0 +11,1 @@" in chunk for chunk in chunks)

    def test_split_hunk_recomputes_each_fragment_coordinates(self):
        hunk = (
            "@@ -100,3 +200,3 @@ def changed():\n"
            " context_one_xxxxxxxxx\n"
            "-removed_two_xxxxxxxxx\n"
            "+added_two_xxxxxxxxxxx\n"
            " context_three_xxxxxxx\n"
        )

        chunks = _split_hunk_by_lines(hunk, max_chars=55)

        assert len(chunks) == 4
        assert chunks[0].startswith("@@ -100,1 +200,1 @@ def changed():")
        assert chunks[1].startswith("@@ -101,1 +201,0 @@ def changed():")
        assert chunks[2].startswith("@@ -102,0 +201,1 @@ def changed():")
        assert chunks[3].startswith("@@ -102,1 +202,1 @@ def changed():")

    def test_owned_segments_require_every_fragment_before_hunk_is_reviewed(self):
        diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -10,4 +10,4 @@ def changed():
 context_one_xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
-removed_two_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
+added_two_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
 context_three_xxxxxxxxxxxxxxxxxxxxxxxxxx
"""
        processed = DiffProcessor().process(diff)
        diff_file = processed.files[0]
        file_info = ReviewFile(
            path="src/big.py",
            focus_areas=[],
            risk_level="MEDIUM",
        )
        prepared = Stage1PreparedContext(
            diff_source=processed,
            diff_by_path={"src/big.py": diff_file},
        )

        expanded = _expand_oversized_diff_batches(
            [[{"file": file_info, "priority": "MEDIUM"}]],
            prepared,
            diff_chunk_token_budget=24,
        )
        unit_ids = tuple(
            batch[0]["_review_unit_id"]
            for batch in expanded
        )
        state = Stage1ReviewUnitState()
        state.register_batches(expanded)

        assert len(expanded) > 1
        assert len(set(unit_ids)) == len(unit_ids)
        assert {
            hunk_id
            for batch in expanded
            for hunk_id in batch[0]["_hunk_ids"]
        } == {diff_file.hunks[0].id}

        state.mark_completed(unit_ids[:-1])
        assert state.reviewed_hunk_ids == ()
        with pytest.raises(RuntimeError, match="coverage is incomplete"):
            state.assert_complete()

        state.mark_completed(unit_ids[-1:])
        state.assert_complete()
        assert state.reviewed_hunk_ids == (diff_file.hunks[0].id,)
        assert set(state.mandatory_unit_ids_by_hunk) == {
            diff_file.hunks[0].id
        }
        assert state.mandatory_unit_ids_by_hunk[diff_file.hunks[0].id].startswith(
            "sha256:"
        )

    def test_duplicate_review_unit_assignment_fails_closed(self):
        file_info = ReviewFile(
            path="src/a.py",
            focus_areas=[],
            risk_level="MEDIUM",
        )
        item = {
            "file": file_info,
            "_review_unit_id": "sha256:unit",
            "_hunk_ids": ("sha256:hunk",),
        }

        with pytest.raises(RuntimeError, match="assigned more than once"):
            Stage1ReviewUnitState().register_batches([[item], [dict(item)]])

    def test_expand_oversized_batches_creates_segment_batches(self):
        file_info = ReviewFile(path="src/big.py", focus_areas=[], risk_level="MEDIUM")
        diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -1 +1,2 @@
+aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
@@ -10 +11,2 @@
+bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"""
        diff_file = DiffFile(path="src/big.py", change_type=DiffChangeType.MODIFIED, content=diff)
        prepared = Stage1PreparedContext(
            diff_source=ProcessedDiff(files=[diff_file]),
            diff_by_path={"src/big.py": diff_file},
        )

        expanded = _expand_oversized_diff_batches(
            [[{"file": file_info, "priority": "MEDIUM"}]],
            prepared,
            diff_chunk_token_budget=20,
        )

        assert len(expanded) > 1
        assert all(len(batch) == 1 for batch in expanded)
        assert expanded[0][0]["_diff_chunk_total"] == len(expanded)

    def test_size_limited_diff_is_expanded_without_model_focus_flag(self):
        raw_diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -1 +1,5 @@
+aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
+bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
+cccccccccccccccccccccccccccccccccccccccccccccc
+dddddddddddddddddddddddddddddddddddddddddddddd
"""
        summarized = DiffFile(
            path="src/big.py",
            change_type=DiffChangeType.MODIFIED,
            content="[summary only]",
            is_skipped=False,
            skip_reason="File too large: 999999 bytes > 1",
        )
        request = MagicMock(rawDiff=raw_diff, deltaDiff=None, enrichmentData=None, taskContext=None)
        prepared = _build_stage_1_prepared_context(
            request,
            ProcessedDiff(files=[summarized]),
            is_incremental=False,
        )
        file_info = ReviewFile(path="src/big.py", focus_areas=[], risk_level="MEDIUM")

        expanded = _expand_oversized_diff_batches(
            [[{"file": file_info, "priority": "MEDIUM"}]],
            prepared,
            diff_chunk_token_budget=20,
        )

        assert len(expanded) > 1
        assert expanded[0][0]["_diff_chunk_total"] == len(expanded)
        assert prepared.full_diff_index_loaded is True

    def test_size_limited_diff_expanded_when_full_diff_focus_requested(self):
        raw_diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -1 +1,5 @@
+aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
+bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
+cccccccccccccccccccccccccccccccccccccccccccccc
+dddddddddddddddddddddddddddddddddddddddddddddd
"""
        summarized = DiffFile(
            path="src/big.py",
            change_type=DiffChangeType.MODIFIED,
            content="[summary only]",
            is_skipped=False,
            skip_reason="File too large: 999999 bytes > 1",
        )
        request = MagicMock(rawDiff=raw_diff, deltaDiff=None, enrichmentData=None, taskContext=None)
        prepared = _build_stage_1_prepared_context(
            request,
            ProcessedDiff(files=[summarized]),
            is_incremental=False,
        )
        file_info = ReviewFile(
            path="src/big.py",
            focus_areas=["FULL_DIFF_REVIEW"],
            risk_level="MEDIUM",
        )

        expanded = _expand_oversized_diff_batches(
            [[{"file": file_info, "priority": "MEDIUM"}]],
            prepared,
            diff_chunk_token_budget=20,
        )

        assert len(expanded) > 1
        assert expanded[0][0]["_diff_chunk_total"] == len(expanded)
        assert prepared.full_diff_index_loaded is True


# ── Structured metadata formatting ───────────────────────────────

class TestBatchEnrichmentMetadataScoping:
    def test_same_basename_in_another_module_is_not_selected(self):
        checkout = MagicMock(path="app/code/Acme/Checkout/etc/di.xml")
        cart = MagicMock(path="app/code/Acme/Cart/etc/di.xml")
        request = MagicMock()
        request.enrichmentData.fileMetadata = [cart, checkout]

        result = _iter_batch_enrichment_metadata(
            request,
            ["app/code/Acme/Checkout/etc/di.xml"],
            prepared_context=None,
        )

        assert result == [checkout]

    def test_absolute_prefix_metadata_matches_repository_path(self):
        checkout = MagicMock(
            path="/tmp/checkout/app/code/Acme/Checkout/etc/di.xml"
        )
        request = MagicMock()
        request.enrichmentData.fileMetadata = [checkout]

        result = _iter_batch_enrichment_metadata(
            request,
            ["app/code/Acme/Checkout/etc/di.xml"],
            prepared_context=None,
        )

        assert result == [checkout]


class TestStructuredMetadataFormatting:
    def test_metadata_is_serialized_as_json_without_outline_truncation(self):
        meta = MagicMock()
        meta.model_dump.return_value = {
            "path": "src/Foo.py",
            "imports": [f"pkg{i}" for i in range(25)],
            "semanticNames": [f"symbol{i}" for i in range(35)],
            "calls": [f"call{i}" for i in range(20)],
        }

        result = _format_batch_metadata_json([meta])

        assert '"path":"src/Foo.py"' in result
        assert "pkg24" in result
        assert "symbol34" in result
        assert "call19" in result

    def test_large_plugin_metadata_is_bounded_with_explicit_omissions(self):
        meta = {
            "path": "app/code/Acme/Checkout/Model/Cart.php",
            "language": "php",
            "pluginSpecificFacts": [
                {
                    "relation": f"relation-{index}",
                    "target": "x" * 200,
                }
                for index in range(500)
            ],
        }

        result = _format_batch_metadata_json(
            [meta],
            max_chars=2_000,
            max_chars_per_file=2_000,
        )

        assert len(result) <= 2_000
        assert "app/code/Acme/Checkout/Model/Cart.php" in result
        assert "_codecrowOmittedItems" in result

    def test_metadata_projection_is_deterministic_and_schema_neutral(self):
        first = {
            "path": "src/Foo.php",
            "frameworkExtension": {
                "zeta": ["z2", "z1"],
                "alpha": "value",
            },
        }
        second = {
            "frameworkExtension": {
                "alpha": "value",
                "zeta": ["z2", "z1"],
            },
            "path": "src/Foo.php",
        }

        assert _format_batch_metadata_json([first]) == _format_batch_metadata_json([second])

    def test_metadata_identifiers_are_collected_from_full_payload(self):
        meta = {
            "path": "src/Foo.py",
            "semanticNames": ["KnownSymbol"],
            "unknownParserField": {
                "frameworkSpecificName": "FrameworkThing",
                "nested": ["NestedValue"],
            },
        }

        result = _extract_metadata_identifiers([meta])

        assert "KnownSymbol" in result
        assert "FrameworkThing" in result
        assert "NestedValue" in result


# ── Deterministic RAG normalization ──────────────────────────────

class TestDeterministicRagNormalization:
    def test_flattens_all_deterministic_groups(self):
        response = {
            "context": {
                "chunks": [
                    {"text": "all chunk", "metadata": {"path": "src/all.py"}},
                ],
                "changed_files": {
                    "src/a.py": [
                        {"text": "changed", "metadata": {"path": "src/a.py"}},
                    ],
                },
                "related_definitions": {
                    "Thing": [
                        {"text": "definition", "metadata": {"path": "src/thing.py"}},
                    ],
                },
                "class_context": {
                    "Calendar": [
                        {"text": "class ctx", "metadata": {"path": "src/calendar.py"}},
                    ],
                },
                "namespace_context": {
                    "booking": [
                        {"text": "namespace ctx", "metadata": {"path": "src/booking.py"}},
                    ],
                },
            }
        }

        chunks = _flatten_deterministic_context(response)
        texts = {chunk["text"] for chunk in chunks}

        assert {"all chunk", "changed", "definition", "class ctx", "namespace ctx"} <= texts
        assert all(chunk["_source"] == "deterministic" for chunk in chunks)

    def test_pr_architecture_packet_retains_pr_indexed_freshness(self):
        response = {
            "context": {
                "architecture_context": {
                    "plugin": [{
                        "text": "fresh effective plugin relation",
                        "metadata": {
                            "path": "__analysis_architecture__/magento/plugin.context",
                            "pr": True,
                            "pr_number": 42,
                            "architecture_kind": "magento-interception",
                        },
                    }],
                },
            },
        }

        chunks = _flatten_deterministic_context(response)

        assert len(chunks) == 1
        assert chunks[0]["_source"] == "pr_indexed"
        assert chunks[0]["_match_type"] == "architecture_relation"

    def test_distinct_chunks_with_equal_long_prefix_are_not_collapsed(self):
        shared_prefix = "Deterministic repository architecture context\n" + (
            "same-prefix " * 60
        )
        response = {
            "context": {
                "architecture_context": {
                    "first": [{
                        "text": shared_prefix + "first-late-fact",
                        "metadata": {
                            "path": "__analysis_architecture__/shared.context",
                            "architecture_kind": "magento-layout",
                        },
                    }],
                    "second": [{
                        "text": shared_prefix + "second-late-fact",
                        "metadata": {
                            "path": "__analysis_architecture__/shared.context",
                            "architecture_kind": "magento-layout",
                        },
                    }],
                },
            },
        }

        chunks = _flatten_deterministic_context(response)

        assert len(chunks) == 2
        assert {
            chunk["text"].rsplit(" ", 1)[-1]
            for chunk in chunks
        } == {
            "first-late-fact",
            "second-late-fact",
        }

    def test_exact_duplicate_chunk_across_grouped_views_is_collapsed(self):
        duplicate = {
            "text": "same exact deterministic fact",
            "metadata": {
                "path": "__analysis_architecture__/same.context",
                "architecture_kind": "magento-layout",
            },
        }
        response = {
            "context": {
                "architecture_context": {"packet": [duplicate]},
                "architecture_related": {"packet": [dict(duplicate)]},
                "chunks": [dict(duplicate)],
            },
        }

        chunks = _flatten_deterministic_context(response)

        assert len(chunks) == 1
        assert chunks[0]["text"] == "same exact deterministic fact"

    def test_architecture_relation_limit_round_robins_neutral_kinds(self):
        response = {
            "context": {
                "architecture_context": {
                    f"packet-{index}": [{
                        "text": f"relation {index}",
                        "metadata": {
                            "path": f"__analysis_architecture__/packet-{index}.context",
                            "architecture_kind": f"kind-{index % 3}",
                            "architecture_key": f"packet-{index}",
                        },
                    }]
                    for index in range(9)
                },
                "related_definitions": {
                    "RequiredType": [{
                        "text": "class RequiredType {}",
                        "metadata": {"path": "src/RequiredType.php"},
                    }],
                },
            },
        }

        chunks = _flatten_deterministic_context(response, max_chunks=4)

        relations = [
            chunk for chunk in chunks
            if chunk["_match_type"] == "architecture_relation"
        ]
        assert {
            chunk["metadata"]["architecture_kind"] for chunk in relations
        } == {"kind-0", "kind-1", "kind-2"}
        assert any(
            chunk["_match_type"] == "definition" for chunk in chunks
        )

    def test_architecture_relation_limit_covers_distinct_review_paths_first(self):
        architecture_context = {
            f"dominant-{index}": [{
                "text": f"dominant relation {index}",
                "metadata": {
                    "path": (
                        "__analysis_architecture__/"
                        f"dominant-{index}.context"
                    ),
                    "architecture_kind": f"kind-{index}",
                    "architecture_key": f"dominant-{index}",
                },
                "_matched_on": "src/dominant.py",
            }]
            for index in range(8)
        }
        architecture_context["quiet"] = [{
            "text": "quiet file exact relation",
            "metadata": {
                "path": "__analysis_architecture__/quiet.context",
                "architecture_kind": "kind-quiet",
                "architecture_key": "quiet",
            },
            "_matched_on": "src/quiet.py",
        }]
        response = {
            "context": {
                "architecture_context": architecture_context,
                "related_definitions": {
                    "RequiredType": [{
                        "text": "class RequiredType {}",
                        "metadata": {"path": "src/RequiredType.php"},
                    }],
                },
            },
        }

        chunks = _flatten_deterministic_context(response, max_chunks=4)

        assert "quiet file exact relation" in {
            chunk["text"] for chunk in chunks
        }
        assert {
            chunk["_matched_on"]
            for chunk in chunks
            if chunk["_match_type"] == "architecture_relation"
        } == {"src/dominant.py", "src/quiet.py"}

    def test_architecture_kind_prefers_diagnostic_then_resolved_fact(self):
        def relation(name, fact):
            return {
                "text": name,
                "metadata": {
                    "path": f"__analysis_architecture__/{name}.context",
                    "architecture_kind": "magento-di",
                    "architecture_key": name,
                    "plugin_graph_facts": [fact],
                },
            }

        response = {
            "context": {
                "architecture_context": {
                    "coarse": [relation("coarse", {
                        "kind": "php-inheritance",
                        "source": "Child",
                        "relation": "extends",
                        "target": "Parent",
                        "attributes": {},
                        "related_paths": [],
                    })],
                    "resolved": [relation("resolved", {
                        "kind": "php-inheritance",
                        "source": "Acme\\Child",
                        "relation": "extends",
                        "target": "Acme\\Parent",
                        "attributes": {"targetKind": "class"},
                        "related_paths": ["src/Parent.php"],
                    })],
                    "diagnostic": [relation("diagnostic", {
                        "kind": "magento-interceptor-inapplicable",
                        "source": "Acme\\Audit",
                        "relation": "cannot-intercept",
                        "target": "Acme\\FinalCart::save",
                        "attributes": {"semanticRole": "diagnostic"},
                        "related_paths": ["src/FinalCart.php"],
                    })],
                },
                "related_definitions": {
                    "RequiredType": [{
                        "text": "class RequiredType {}",
                        "metadata": {"path": "src/RequiredType.php"},
                    }],
                },
            },
        }

        first = _flatten_deterministic_context(response, max_chunks=2)
        second = _flatten_deterministic_context(response, max_chunks=3)

        assert [chunk["text"] for chunk in first] == [
            "diagnostic",
            "class RequiredType {}",
        ]
        assert [chunk["text"] for chunk in second] == [
            "diagnostic",
            "resolved",
            "class RequiredType {}",
        ]

    def test_empty_context_has_no_chunks(self):
        assert _rag_context_has_chunks({"relevant_code": []}) is False
        assert _rag_context_has_chunks({"context": {"relevant_code": []}}) is False
        assert _rag_context_has_chunks({"relevant_code": [{"text": "x"}]}) is True


# ── Lazy fallback RAG ────────────────────────────────────────────

class TestLazyFallbackRagContext:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_materialized_context_returned_directly(self):
        context = {"relevant_code": [{"text": "code"}]}

        result = await _resolve_fallback_rag_context(context)

        assert result is context

    @pytest.mark.asyncio(loop_scope="function")
    async def test_task_context_resolved_on_demand(self):
        async def build_context():
            await asyncio.sleep(0)
            return {"relevant_code": [{"text": "code"}]}

        task = asyncio.create_task(build_context())

        result = await _resolve_fallback_rag_context(task)

        assert result == {"relevant_code": [{"text": "code"}]}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_failed_task_returns_none(self):
        async def fail_context():
            await asyncio.sleep(0)
            raise RuntimeError("rag unavailable")

        task = asyncio.create_task(fail_context())

        result = await _resolve_fallback_rag_context(task)

        assert result is None

    def test_scopes_materialized_fallback_context_to_batch_paths(self):
        context = {
            "relevant_code": [
                {"text": "batch", "metadata": {"path": "src/a.py"}},
                {"text": "suffix", "metadata": {"path": "repo/service/src/b.py"}},
                {"text": "other", "metadata": {"path": "lib/unrelated.py"}},
                {"text": "unknown", "metadata": {}},
            ]
        }

        result = _scope_fallback_rag_context_to_batch(context, ["src/a.py", "src/b.py"])

        assert [chunk["text"] for chunk in result["relevant_code"]] == ["batch", "suffix"]

    def test_scoped_fallback_returns_none_when_no_batch_chunks_match(self):
        context = {"relevant_code": [{"text": "other", "metadata": {"path": "lib/other.py"}}]}

        result = _scope_fallback_rag_context_to_batch(context, ["src/a.py"])

        assert result is None


# ── Per-batch RAG fetching ───────────────────────────────────────

class TestFetchBatchRagContext:
    def _request(self):
        request = MagicMock()
        request.get_rag_branch.return_value = "feature"
        request.get_rag_base_branch.return_value = "main"
        request.commitHash = "abc"
        request.projectWorkspace = "ws"
        request.projectNamespace = "proj"
        request.pullRequestId = 123
        request.changedFiles = ["src/a.py"]
        request.deletedFiles = []
        request.prTitle = "PR title"
        request.prDescription = "PR description"
        request.enrichmentData = None
        return request

    def _exact_request(self):
        request = self._request()
        request.currentCommitHash = "a" * 40
        request.commitHash = "a" * 40
        request.baseCommitHash = "b" * 40
        request.ragCollectionTarget = "cc_ws_proj_main_generation"
        request.ragBaseGenerationManifestSha256 = "c" * 64
        request.ragPrGenerationFingerprint = "d" * 64
        request.ragPrOverlayGenerationManifestSha256 = "e" * 64
        request.rawDiff = ""
        request.deltaDiff = None
        request.taskContext = None
        request.projectRules = []
        request.previousCodeAnalysisIssues = []
        return request

    @staticmethod
    def _batch(path="src/a.py"):
        return [{
            "file": ReviewFile(
                path=path,
                focus_areas=["general"],
                risk_level="MEDIUM",
            ),
            "priority": "MEDIUM",
        }]

    class _FallbackAwaitProbe:
        def __init__(self):
            self.awaited = False

        def __await__(self):
            self.awaited = True

            async def resolve():
                return {
                    "relevant_code": [{
                        "text": "STALE_FALLBACK_SENTINEL",
                        "metadata": {"path": "src/a.py"},
                        "score": 1.0,
                    }]
                }

            return resolve().__await__()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_target_branch_does_not_query_an_invented_branch(self):
        request = self._request()
        request.get_rag_branch.return_value = None
        request.get_rag_base_branch.return_value = None
        rag = MagicMock()
        rag.get_deterministic_context = AsyncMock()
        rag.get_pr_context = AsyncMock()
        rag.search_for_duplicates = AsyncMock()
        state = Stage1RagState()

        result = await fetch_batch_rag_context(
            rag,
            request,
            ["src/a.py"],
            ["diff"],
            rag_state=state,
        )

        assert result is None
        assert state.deterministic_retrieval_states == ["failed"]
        assert state.context_disabled is True
        rag.get_deterministic_context.assert_not_awaited()
        rag.get_pr_context.assert_not_awaited()
        rag.search_for_duplicates.assert_not_awaited()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_deterministic_chunks_skip_semantic_filler(self):
        class Rag:
            def __init__(self):
                self.semantic_calls = 0

            async def get_deterministic_context(self, **kwargs):
                return {
                    "context": {
                        "related_definitions": {
                            "Thing": [
                                {"text": f"definition {i}", "metadata": {"path": f"src/d{i}.py"}}
                                for i in range(12)
                            ]
                        }
                    }
                }

            async def get_pr_context(self, **kwargs):
                self.semantic_calls += 1
                raise AssertionError("semantic RAG should not be called")

            async def search_for_duplicates(self, **kwargs):
                return []

        rag = Rag()
        result = await fetch_batch_rag_context(
            rag,
            self._request(),
            ["src/a.py"],
            ["changed line"],
            batch_priority="MEDIUM",
            rag_state=Stage1RagState(),
        )

        assert len(result["relevant_code"]) >= 10
        assert rag.semantic_calls == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exact_graph_fact_capture_waits_for_prompt_formatting(self):
        fact = {
            "kind": "magento-di-effective-preference",
            "source": "Acme\\Api\\CartInterface",
            "relation": "resolves-to",
            "target": "Acme\\Model\\Cart",
            "path": "app/code/Acme/Checkout/etc/di.xml",
            "line": 3,
            "attributes": {"area": "global"},
            "related_paths": [
                "app/code/Acme/Checkout/Model/Cart.php",
            ],
        }

        class Rag:
            async def get_deterministic_context(self, **kwargs):
                chunk = {
                    "text": "CartInterface resolves-to Cart",
                    "metadata": {
                        "path": "__analysis_architecture__/preference.context",
                        "architecture_kind": "magento-di",
                        "architecture_key": "global:preference",
                        "plugin_graph_facts": [fact],
                    },
                    "_match_type": "architecture_relation",
                }
                return {
                    "context": {
                        "chunks": [chunk],
                        "architecture_context": {"preference": [chunk]},
                        "_metadata": {"retrieval_state": "complete"},
                    },
                }

            async def get_pr_context(self, **kwargs):
                return {"context": {"relevant_code": []}}

            async def search_for_duplicates(self, **kwargs):
                return []

        state = Stage1RagState()
        result = await fetch_batch_rag_context(
            Rag(),
            self._request(),
            ["src/a.py"],
            ["changed line"],
            batch_priority="MEDIUM",
            rag_state=state,
        )

        assert result["relevant_code"]
        assert state.deterministic_retrieval_states == ["complete"]
        assert state.exact_evidence_by_id == {}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_semantic_timeout_disables_remaining_batches(self, monkeypatch):
        import service.review.orchestrator.stage_1_file_review as stage1

        monkeypatch.setattr(stage1, "SEMANTIC_RAG_FILLER_ENABLED", True)
        monkeypatch.setattr(stage1, "SEMANTIC_RAG_TIMEOUT_SECONDS", 0.01)

        class Rag:
            def __init__(self):
                self.semantic_calls = 0

            async def get_deterministic_context(self, **kwargs):
                return {"context": {"chunks": [], "related_definitions": {}}}

            async def get_pr_context(self, **kwargs):
                self.semantic_calls += 1
                await asyncio.sleep(1)
                return {"context": {"relevant_code": [{"text": "late"}]}}

            async def search_for_duplicates(self, **kwargs):
                return []

        rag = Rag()
        state = Stage1RagState()

        first = await fetch_batch_rag_context(
            rag,
            self._request(),
            ["src/a.py"],
            ["changed line"],
            batch_priority="MEDIUM",
            rag_state=state,
        )
        second = await fetch_batch_rag_context(
            rag,
            self._request(),
            ["src/b.py"],
            ["changed line"],
            batch_priority="MEDIUM",
            rag_state=state,
        )

        assert first is None
        assert second is None
        assert state.semantic_disabled is True
        assert rag.semantic_calls == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_semantic_transport_failure_disables_remaining_batches(
        self,
        monkeypatch,
    ):
        import service.review.orchestrator.stage_1_file_review as stage1

        monkeypatch.setattr(stage1, "SEMANTIC_RAG_FILLER_ENABLED", True)

        class Rag:
            def __init__(self):
                self.semantic_calls = 0

            async def get_deterministic_context(self, **kwargs):
                return {"context": {"chunks": [], "related_definitions": {}}}

            async def get_pr_context(self, **kwargs):
                self.semantic_calls += 1
                return {
                    "status": "error",
                    "status_code": 503,
                    "error": "RAG service unavailable",
                }

            async def search_for_duplicates(self, **kwargs):
                return []

        rag = Rag()
        state = Stage1RagState()

        first = await fetch_batch_rag_context(
            rag,
            self._request(),
            ["src/a.py"],
            ["changed line"],
            batch_priority="MEDIUM",
            rag_state=state,
        )
        second = await fetch_batch_rag_context(
            rag,
            self._request(),
            ["src/b.py"],
            ["changed line"],
            batch_priority="MEDIUM",
            rag_state=state,
        )

        assert first is None
        assert second is None
        assert state.semantic_disabled is True
        assert state.semantic_failures == 1
        assert state.semantic_disable_reason == "RAG service unavailable"
        assert rag.semantic_calls == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_concurrent_semantic_failures_emit_one_owner_warning(
        self,
        caplog,
        monkeypatch,
    ):
        import service.review.orchestrator.stage_1_file_review as stage1

        monkeypatch.setattr(stage1, "SEMANTIC_RAG_FILLER_ENABLED", True)
        release = asyncio.Event()

        class Rag:
            def __init__(self):
                self.semantic_calls = 0

            async def get_deterministic_context(self, **kwargs):
                return {"context": {"chunks": [], "related_definitions": {}}}

            async def get_pr_context(self, **kwargs):
                self.semantic_calls += 1
                await release.wait()
                return {
                    "status": "error",
                    "status_code": 503,
                    "error": "RAG service unavailable",
                }

            async def search_for_duplicates(self, **kwargs):
                return []

        rag = Rag()
        state = Stage1RagState()
        with caplog.at_level(
            logging.WARNING,
            logger="service.review.orchestrator.stage_1_file_review",
        ):
            tasks = [
                asyncio.create_task(fetch_batch_rag_context(
                    rag,
                    self._request(),
                    [file_path],
                    ["changed line"],
                    batch_priority="MEDIUM",
                    rag_state=state,
                ))
                for file_path in ("src/a.py", "src/b.py")
            ]
            while rag.semantic_calls < 2:
                await asyncio.sleep(0)
            release.set()
            assert await asyncio.gather(*tasks) == [None, None]

        assert state.semantic_disabled is True
        assert state.semantic_failures == 1
        owner_warnings = [
            record for record in caplog.records
            if record.levelno == logging.WARNING
            and "Semantic RAG filler failed" in record.getMessage()
        ]
        assert len(owner_warnings) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_deterministic_failure_opens_one_review_context_circuit(
        self,
        caplog,
    ):
        class Rag:
            def __init__(self):
                self.deterministic_calls = 0
                self.semantic_calls = 0

            async def get_deterministic_context(self, **kwargs):
                self.deterministic_calls += 1
                return {
                    "status": "error",
                    "status_code": 500,
                    "error": "RAG service unavailable",
                }

            async def get_pr_context(self, **kwargs):
                self.semantic_calls += 1
                raise AssertionError("semantic retrieval must not run")

            async def search_for_duplicates(self, **kwargs):
                return []

        rag = Rag()
        state = Stage1RagState()
        with caplog.at_level(
            logging.WARNING,
            logger="service.review.orchestrator.stage_1_file_review",
        ):
            first = await fetch_batch_rag_context(
                rag,
                self._request(),
                ["src/a.py"],
                ["changed line"],
                batch_priority="MEDIUM",
                rag_state=state,
            )
            second = await fetch_batch_rag_context(
                rag,
                self._request(),
                ["src/b.py"],
                ["changed line"],
                batch_priority="MEDIUM",
                rag_state=state,
            )

        assert first is None
        assert second is None
        assert rag.deterministic_calls == 1
        assert rag.semantic_calls == 0
        assert state.deterministic_retrieval_states == ["failed"]
        assert state.context_disabled is True
        assert state.semantic_disabled is True
        owner_warnings = [
            record for record in caplog.records
            if record.levelno == logging.WARNING
            and "Optional RAG context is unavailable" in record.getMessage()
        ]
        assert len(owner_warnings) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exact_deterministic_failure_fails_open_to_review_model(self):
        class Rag:
            async def get_deterministic_context(self, **kwargs):
                return {
                    "status": "error",
                    "status_code": 500,
                    "error": "exact retrieval failed",
                }

            async def get_pr_context(self, **kwargs):
                raise AssertionError("semantic retrieval must not run")

            async def search_for_duplicates(self, **kwargs):
                return []

        fallback = self._FallbackAwaitProbe()

        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            new_callable=AsyncMock,
        ) as invoke:
            invoke.return_value = []
            state = Stage1RagState()
            result = await review_file_batch(
                MagicMock(),
                self._exact_request(),
                self._batch(),
                rag_client=Rag(),
                prepared_context=Stage1PreparedContext(),
                fallback_rag_context=fallback,
                pr_indexed=True,
                rag_state=state,
            )

        assert result == []
        assert state.context_disabled is True
        assert fallback.awaited is False
        assert "STALE_FALLBACK_SENTINEL" not in invoke.await_args.args[1]
        invoke.assert_awaited_once()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exact_missing_rag_client_fails_open_to_review_model(self):
        fallback = self._FallbackAwaitProbe()

        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            new_callable=AsyncMock,
        ) as invoke:
            invoke.return_value = []
            state = Stage1RagState()
            result = await review_file_batch(
                MagicMock(),
                self._exact_request(),
                self._batch(),
                rag_client=None,
                prepared_context=Stage1PreparedContext(),
                fallback_rag_context=fallback,
                pr_indexed=True,
                rag_state=state,
            )

        assert result == []
        assert state.context_disabled is True
        assert fallback.awaited is False
        assert "STALE_FALLBACK_SENTINEL" not in invoke.await_args.args[1]
        invoke.assert_awaited_once()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exact_base_binding_never_consumes_unbound_fallback(self):
        request = self._exact_request()
        request.ragPrGenerationFingerprint = None
        request.ragPrOverlayGenerationManifestSha256 = None
        fallback = self._FallbackAwaitProbe()

        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            new_callable=AsyncMock,
            return_value=[],
        ) as invoke:
            state = Stage1RagState()
            result = await review_file_batch(
                MagicMock(),
                request,
                self._batch(),
                rag_client=None,
                prepared_context=Stage1PreparedContext(),
                fallback_rag_context=fallback,
                pr_indexed=False,
                rag_state=state,
            )

        assert result == []
        assert state.context_disabled is True
        assert fallback.awaited is False
        assert "STALE_FALLBACK_SENTINEL" not in invoke.await_args.args[1]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_legacy_unbound_review_still_uses_scoped_fallback(self):
        request = self._request()
        request.rawDiff = ""
        request.deltaDiff = None
        request.taskContext = None
        request.projectRules = []
        request.previousCodeAnalysisIssues = []
        fallback = self._FallbackAwaitProbe()

        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            new_callable=AsyncMock,
            return_value=[],
        ) as invoke:
            result = await review_file_batch(
                MagicMock(),
                request,
                self._batch(),
                rag_client=None,
                prepared_context=Stage1PreparedContext(),
                fallback_rag_context=fallback,
                pr_indexed=False,
                rag_state=Stage1RagState(),
            )

        assert result == []
        assert fallback.awaited is True
        assert "STALE_FALLBACK_SENTINEL" in invoke.await_args.args[1]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exact_partial_deterministic_state_fails_open_once(self):
        class Rag:
            def __init__(self):
                self.deterministic_calls = 0

            async def get_deterministic_context(self, **kwargs):
                self.deterministic_calls += 1
                return {
                    "context": {
                        "chunks": [{"text": "partial context"}],
                        "_metadata": {"retrieval_state": "partial"},
                    }
                }

            async def search_for_duplicates(self, **kwargs):
                return []

        rag = Rag()
        state = Stage1RagState()
        first = await fetch_batch_rag_context(
            rag,
            self._exact_request(),
            ["src/a.py"],
            ["changed line"],
            pr_indexed=True,
            rag_state=state,
        )
        second = await fetch_batch_rag_context(
            rag,
            self._exact_request(),
            ["src/b.py"],
            ["changed line"],
            pr_indexed=True,
            rag_state=state,
        )

        assert first is None
        assert second is None
        assert rag.deterministic_calls == 1
        assert state.context_disabled is True
        assert state.deterministic_retrieval_states == ["partial"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exact_semantic_transport_failure_keeps_review_available(
        self,
        monkeypatch,
    ):
        import service.review.orchestrator.stage_1_file_review as stage1

        monkeypatch.setattr(stage1, "SEMANTIC_RAG_FILLER_ENABLED", True)

        class Rag:
            async def get_deterministic_context(self, **kwargs):
                return {
                    "context": {
                        "chunks": [],
                        "_metadata": {"retrieval_state": "complete"},
                    }
                }

            async def get_pr_context(self, **kwargs):
                return {
                    "status": "error",
                    "status_code": 503,
                    "error": "semantic backend unavailable",
                }

            async def search_for_duplicates(self, **kwargs):
                return []

        state = Stage1RagState()
        result = await fetch_batch_rag_context(
            Rag(),
            self._exact_request(),
            ["src/a.py"],
            ["changed line"],
            pr_indexed=True,
            rag_state=state,
        )

        assert result is None
        assert state.context_disabled is False
        assert state.semantic_disabled is True
        assert state.semantic_disable_reason == "semantic backend unavailable"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_degraded_overlay_uses_only_exact_base_binding(
        self,
        monkeypatch,
    ):
        import service.review.orchestrator.stage_1_file_review as stage1

        monkeypatch.setattr(stage1, "SEMANTIC_RAG_FILLER_ENABLED", True)

        class Rag:
            def __init__(self):
                self.deterministic_request = None
                self.semantic_request = None

            async def get_deterministic_context(self, **kwargs):
                self.deterministic_request = kwargs
                return {
                    "context": {
                        "chunks": [],
                        "_metadata": {"retrieval_state": "complete"},
                    }
                }

            async def get_pr_context(self, **kwargs):
                self.semantic_request = kwargs
                return {"context": {"relevant_code": []}}

            async def search_for_duplicates(self, **kwargs):
                return []

        request = self._exact_request()
        request.ragPrGenerationFingerprint = None
        request.ragPrOverlayGenerationManifestSha256 = None
        rag = Rag()

        result = await fetch_batch_rag_context(
            rag,
            request,
            ["src/a.py"],
            ["changed line"],
            pr_indexed=False,
            rag_state=Stage1RagState(),
        )

        assert result == {"relevant_code": []}
        assert rag.deterministic_request["branches"] == ["feature"]
        assert rag.deterministic_request["base_revision"] == "b" * 40
        assert (
            rag.deterministic_request["base_generation_manifest_sha256"]
            == "c" * 64
        )
        assert (
            rag.deterministic_request["collection_target"]
            == "cc_ws_proj_main_generation"
        )
        assert rag.deterministic_request["pr_number"] is None
        assert rag.deterministic_request["pr_changed_files"] is None
        assert rag.deterministic_request["source_revision"] is None
        assert rag.deterministic_request["pr_generation_fingerprint"] is None
        assert (
            rag.deterministic_request[
                "pr_overlay_generation_manifest_sha256"
            ]
            is None
        )
        assert rag.semantic_request["base_branch"] is None
        assert rag.semantic_request["pr_number"] is None
        assert rag.semantic_request["all_pr_changed_files"] is None
        assert rag.semantic_request["deleted_files"] is None
        assert rag.semantic_request["source_revision"] is None
        assert rag.semantic_request["base_revision"] == "b" * 40
        assert (
            rag.semantic_request["base_generation_manifest_sha256"]
            == "c" * 64
        )
        assert (
            rag.semantic_request["collection_target"]
            == "cc_ws_proj_main_generation"
        )
        assert rag.semantic_request["pr_generation_fingerprint"] is None
        assert (
            rag.semantic_request["pr_overlay_generation_manifest_sha256"]
            is None
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exact_bound_success_allows_intentional_semantic_disable(
        self,
        monkeypatch,
    ):
        import service.review.orchestrator.stage_1_file_review as stage1

        monkeypatch.setattr(stage1, "SEMANTIC_RAG_FILLER_ENABLED", False)

        class Rag:
            async def get_deterministic_context(self, **kwargs):
                return {
                    "context": {
                        "chunks": [{
                            "text": "exact context",
                            "metadata": {"path": "src/dependency.py"},
                        }],
                        "_metadata": {"retrieval_state": "complete"},
                    }
                }

            async def get_pr_context(self, **kwargs):
                raise AssertionError("semantic retrieval is intentionally disabled")

            async def search_for_duplicates(self, **kwargs):
                return []

        state = Stage1RagState()
        result = await fetch_batch_rag_context(
            Rag(),
            self._exact_request(),
            ["src/a.py"],
            ["changed line"],
            pr_indexed=True,
            rag_state=state,
        )

        assert [chunk["text"] for chunk in result["relevant_code"]] == [
            "exact context"
        ]
        assert state.deterministic_retrieval_states == ["complete"]
        assert state.semantic_disabled is False


# ── _deduplicate_pr_stale_chunks ─────────────────────────────────

class TestDeduplicatePrStaleChunks:
    def test_empty_chunks(self):
        assert _deduplicate_pr_stale_chunks([], ["a.py"], ["a.py"]) == []

    def test_empty_pr_files(self):
        chunks = [{"text": "code", "metadata": {"path": "a.py"}}]
        result = _deduplicate_pr_stale_chunks(chunks, [], ["a.py"])
        assert result == chunks

    def test_non_pr_file_kept(self):
        chunks = [{"text": "code", "metadata": {"path": "lib.py"}}]
        result = _deduplicate_pr_stale_chunks(chunks, ["a.py"], ["a.py"])
        assert len(result) == 1

    def test_pr_file_in_batch_kept(self):
        chunks = [
            {"text": "stale", "metadata": {"path": "a.py"}, "_source": "branch"},
            {"text": "fresh", "metadata": {"path": "a.py"}, "_source": "pr_indexed"},
        ]
        result = _deduplicate_pr_stale_chunks(chunks, ["a.py"], ["a.py"])
        assert len(result) == 2  # Both kept because file is in batch

    def test_pr_file_not_in_batch_prefers_pr_indexed(self):
        chunks = [
            {"text": "stale", "metadata": {"path": "a.py"}, "_source": "branch"},
            {"text": "fresh", "metadata": {"path": "a.py"}, "_source": "pr_indexed"},
        ]
        result = _deduplicate_pr_stale_chunks(chunks, ["a.py"], ["other.py"])
        assert len(result) == 1
        assert result[0]["_source"] == "pr_indexed"

    def test_no_pr_indexed_marks_stale(self):
        chunks = [
            {"text": "stale", "metadata": {"path": "a.py"}, "_source": "branch"},
        ]
        result = _deduplicate_pr_stale_chunks(chunks, ["a.py"], ["other.py"])
        assert len(result) == 1
        assert result[0].get("_potentially_stale") is True

    def test_no_metadata_path_uses_unknown(self):
        chunks = [{"text": "code"}]
        result = _deduplicate_pr_stale_chunks(chunks, ["a.py"], ["a.py"])
        assert len(result) == 1

    def test_basename_matching(self):
        chunks = [
            {"text": "code", "metadata": {"path": "src/a.py"}, "_source": "pr_indexed"},
            {"text": "stale", "metadata": {"path": "src/a.py"}, "_source": "branch"},
        ]
        result = _deduplicate_pr_stale_chunks(chunks, ["src/a.py"], ["other.py"])
        assert len(result) == 1
        assert result[0]["_source"] == "pr_indexed"

    def test_same_basename_in_different_module_is_not_a_pr_file(self):
        chunks = [{
            "text": "Cart module branch configuration",
            "metadata": {"path": "app/code/Acme/Cart/etc/di.xml"},
            "_source": "branch",
        }]

        result = _deduplicate_pr_stale_chunks(
            chunks,
            ["app/code/Acme/Checkout/etc/di.xml"],
            ["app/code/Acme/Checkout/etc/di.xml"],
        )

        assert result == chunks
        assert "_potentially_stale" not in result[0]


class TestChunkMatchesBatchPath:
    def test_absolute_checkout_prefix_matches_repository_path(self):
        chunk = {
            "metadata": {
                "path": "/tmp/checkout/app/code/Acme/Checkout/etc/di.xml",
            }
        }

        assert _chunk_matches_batch_path(
            chunk,
            ["app/code/Acme/Checkout/etc/di.xml"],
        )

    def test_same_basename_in_different_module_does_not_match(self):
        chunk = {
            "metadata": {"path": "app/code/Acme/Cart/etc/di.xml"},
        }

        assert not _chunk_matches_batch_path(
            chunk,
            ["app/code/Acme/Checkout/etc/di.xml"],
        )


# ── _build_duplication_queries_from_diff ─────────────────────────

class TestBuildDuplicationQueries:
    def test_empty_input(self):
        result = _build_duplication_queries_from_diff([], [])
        assert result == []

    def test_extracts_from_diff_snippets(self):
        diff_snippets = [
            "def calculate_total_price(items):\n    return sum(i.price for i in items)\n"
        ]
        result = _build_duplication_queries_from_diff(diff_snippets, [])
        assert len(result) > 0
        assert result[0].startswith("duplicate search diff evidence:")
        assert any("calculate_total_price" in q for q in result)

    def test_enrichment_metadata_semantic_names(self):
        enrichment = {
            "Foo.java": {
                "semantic_names": ["PaymentService", "OrderProcessor"],
                "extends": ["BaseService"],
                "implements": ["Payable"],
                "calls": ["validateOrder"],
            }
        }
        result = _build_duplication_queries_from_diff(
            [], [], enrichment_metadata=enrichment
        )
        assert result[0].startswith("duplicate search structured metadata:")
        assert any("PaymentService" in q for q in result)
        assert any("BaseService" in q for q in result)
        assert any("Payable" in q for q in result)
        assert any("validateOrder" in q for q in result)

    def test_file_names_alone_do_not_create_queries(self):
        result = _build_duplication_queries_from_diff(
            [], ["application.yml", "build.gradle"],
        )
        assert result == []

    def test_max_10_queries(self):
        enrichment = {
            "Big.java": {
                "semantic_names": [f"Symbol{i}" for i in range(20)],
                "extends": [],
                "implements": [],
                "calls": [],
            }
        }
        result = _build_duplication_queries_from_diff(
            ["class SomeClass:\n    pass"], [], enrichment_metadata=enrichment
        )
        assert len(result) <= 10

    def test_preserves_short_metadata_values(self):
        enrichment = {
            "A.java": {
                "semantic_names": ["ab"],  # too short (len<=3)
                "extends": [],
                "implements": [],
                "calls": [],
            }
        }
        result = _build_duplication_queries_from_diff([], [], enrichment_metadata=enrichment)
        assert any("ab" in q for q in result)

    def test_sql_text_passed_through_without_table_extraction(self):
        diff_snippets = [
            "SELECT * FROM user_accounts WHERE active = true"
        ]
        result = _build_duplication_queries_from_diff(diff_snippets, [])
        assert result[0].startswith("duplicate search diff evidence:")
        assert any("user_accounts" in q for q in result)

    def test_class_text_passed_through_without_class_extraction(self):
        diff_snippets = [
            "class PaymentGateway:\n    def process(self):\n        pass"
        ]
        result = _build_duplication_queries_from_diff(diff_snippets, [])
        assert result[0].startswith("duplicate search diff evidence:")
        assert any("PaymentGateway" in q for q in result)


# ── _scope_deterministic_to_diff ─────────────────────────────────

class TestScopeDeterministicToDiff:
    def test_empty_related_defs(self):
        result = _scope_deterministic_to_diff({}, ["some diff"])
        assert result == []

    def test_relevant_definition_kept(self):
        related_defs = {
            "MyService": [{"text": "class MyService", "metadata": {}}],
        }
        diff_snippets = ["+    service = MyService()"]
        result = _scope_deterministic_to_diff(related_defs, diff_snippets)
        assert len(result) == 1
        assert result[0]["_def_name"] == "MyService"
        assert result[0]["_diff_relevant"] is True

    def test_deterministic_chunks_are_not_token_filtered(self):
        related_defs = {
            "UnusedHelper": [{"text": "class UnusedHelper", "metadata": {}}],
            "AnotherHelper": [{"text": "class AnotherHelper", "metadata": {}}],
            "ThirdHelper": [{"text": "class ThirdHelper", "metadata": {}}],
        }
        diff_snippets = ["+    x = SomethingElse()"]
        result = _scope_deterministic_to_diff(
            related_defs, diff_snippets, max_file_level=2
        )
        assert len(result) == 3
        for r in result:
            assert r["_diff_relevant"] is True

    def test_raw_diffs_do_not_block_deterministic_context(self):
        related_defs = {
            "ConfigLoader": [{"text": "class ConfigLoader", "metadata": {}}],
        }
        result = _scope_deterministic_to_diff(
            related_defs, [], batch_raw_diffs=["+    loader = ConfigLoader()"]
        )
        assert len(result) == 1
        assert result[0]["_diff_relevant"] is True

    def test_max_per_def_caps_chunks(self):
        related_defs = {
            "BigClass": [
                {"text": "chunk1", "metadata": {}},
                {"text": "chunk2", "metadata": {}},
                {"text": "chunk3", "metadata": {}},
            ]
        }
        diff_snippets = ["+    x = BigClass()"]
        result = _scope_deterministic_to_diff(
            related_defs, diff_snippets, max_per_def=2
        )
        assert len(result) == 2

    def test_keyword_like_definitions_not_filtered(self):
        related_defs = {
            "self": [{"text": "builtin self", "metadata": {}}],
        }
        diff_snippets = ["+    x = self.process()"]
        result = _scope_deterministic_to_diff(
            related_defs, diff_snippets, max_file_level=0
        )
        assert len(result) == 1
        assert result[0]["_diff_relevant"] is True

    def test_semantic_names_in_metadata_checked(self):
        related_defs = {
            "mod_abc": [
                {
                    "text": "module abc",
                    "metadata": {
                        "primary_name": "AbcHandler",
                        "semantic_names": ["AbcHandler"],
                    },
                }
            ]
        }
        diff_snippets = ["+    h = AbcHandler()"]
        result = _scope_deterministic_to_diff(related_defs, diff_snippets)
        assert len(result) == 1
        assert result[0]["_diff_relevant"] is True

    def test_no_diff_tokens_keeps_everything(self):
        """When no diff text is available (e.g., binary), keep all defs."""
        related_defs = {
            "A": [{"text": "class A", "metadata": {}}],
        }
        result = _scope_deterministic_to_diff(related_defs, [])
        assert len(result) == 1
        assert result[0]["_diff_relevant"] is True


# ── _extract_calibrated_issues ───────────────────────────────────

class TestExtractCalibratedIssues:
    def _make_issue(self, severity="MEDIUM"):
        return CodeReviewIssue(
            id="i1",
            severity=severity,
            category="BUG",
            file="a.py",
            line=10,
            title="Test issue",
            reason="Test reason",
            suggestedFixDescription="Fix it",
        )

    def test_empty_batch(self):
        batch_output = FileReviewBatchOutput(reviews=[])
        result = _extract_calibrated_issues(batch_output)
        assert result == []

    def test_issues_returned(self):
        batch_output = FileReviewBatchOutput(reviews=[
            FileReviewOutput(
                file="a.py",
                analysis_summary="ok",
                issues=[self._make_issue()],
                confidence="HIGH",
            )
        ])
        result = _extract_calibrated_issues(batch_output)
        assert len(result) == 1

    def test_low_confidence_downgrades_high_to_medium(self):
        issue = self._make_issue(severity="HIGH")
        batch_output = FileReviewBatchOutput(reviews=[
            FileReviewOutput(
                file="a.py",
                analysis_summary="uncertain",
                issues=[issue],
                confidence="LOW",
            )
        ])
        result = _extract_calibrated_issues(batch_output)
        assert len(result) == 1
        assert result[0].severity == "MEDIUM"

    def test_low_confidence_does_not_downgrade_medium(self):
        issue = self._make_issue(severity="MEDIUM")
        batch_output = FileReviewBatchOutput(reviews=[
            FileReviewOutput(
                file="a.py",
                analysis_summary="ok",
                issues=[issue],
                confidence="LOW",
            )
        ])
        result = _extract_calibrated_issues(batch_output)
        assert result[0].severity == "MEDIUM"

    def test_high_confidence_keeps_high_severity(self):
        issue = self._make_issue(severity="HIGH")
        batch_output = FileReviewBatchOutput(reviews=[
            FileReviewOutput(
                file="a.py",
                analysis_summary="ok",
                issues=[issue],
                confidence="HIGH",
            )
        ])
        result = _extract_calibrated_issues(batch_output)
        assert result[0].severity == "HIGH"

    def test_multiple_reviews_aggregated(self):
        batch_output = FileReviewBatchOutput(reviews=[
            FileReviewOutput(
                file="a.py",
                analysis_summary="ok",
                issues=[self._make_issue(), self._make_issue()],
                confidence="HIGH",
            ),
            FileReviewOutput(
                file="b.py",
                analysis_summary="ok",
                issues=[self._make_issue()],
                confidence="MEDIUM",
            ),
        ])
        result = _extract_calibrated_issues(batch_output)
        assert len(result) == 3


# ── create_smart_batches_wrapper ─────────────────────────────────

class TestCreateSmartBatchesWrapper:
    def _make_plan(self, paths):
        files = [ReviewFile(path=p, focus_areas=[], risk_level="MEDIUM") for p in paths]
        return [FileGroup(group_id="g0", priority="HIGH", rationale="test", files=files)]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_fallback_when_no_processed_diff(self):
        groups = self._make_plan(["a.py", "b.py"])
        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=None,
            request=MagicMock(),
            rag_client=None,
        )
        assert len(result) >= 1
        # Each item is a dict with 'file' key
        for batch in result:
            for item in batch:
                assert "file" in item

    @pytest.mark.asyncio(loop_scope="function")
    async def test_single_file(self):
        groups = self._make_plan(["a.py"])
        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=None,
            request=MagicMock(),
            rag_client=None,
        )
        assert len(result) == 1
        assert len(result[0]) == 1

    @patch("service.review.orchestrator.stage_1_file_review.create_smart_batches_async")
    @pytest.mark.asyncio(loop_scope="function")
    async def test_uses_smart_batches_when_available(self, mock_smart):
        mock_smart.return_value = None  # Force fallback
        groups = self._make_plan(["a.py", "b.py"])
        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=MagicMock(),
            request=MagicMock(enrichmentData=None),
            rag_client=None,
        )
        # Should still return valid batches from fallback
        assert len(result) >= 1

    @patch("service.review.orchestrator.stage_1_file_review.create_smart_batches_async")
    @pytest.mark.asyncio(loop_scope="function")
    async def test_caps_stage_1_batch_token_budget_for_latency(self, mock_smart):
        groups = self._make_plan(["a.py", "b.py"])
        mock_smart.return_value = [[{"file": groups[0].files[0], "priority": "MEDIUM"}]]
        request = MagicMock(
            enrichmentData=None,
            maxAllowedTokens=200000,
            projectWorkspace="ws",
            projectNamespace="proj",
        )
        request.get_rag_branch.return_value = "feature"
        request.get_rag_base_branch.return_value = "main"

        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=MagicMock(),
            request=request,
            rag_client=None,
        )

        assert result == mock_smart.return_value
        assert mock_smart.call_args.kwargs["max_allowed_tokens"] == 60000

    @patch("service.review.orchestrator.stage_1_file_review.create_smart_batches_async")
    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_target_branch_uses_local_grouping_without_rag(self, mock_smart):
        groups = self._make_plan(["a.py"])
        mock_smart.return_value = [[{"file": groups[0].files[0], "priority": "MEDIUM"}]]
        request = MagicMock(
            enrichmentData=None,
            maxAllowedTokens=200000,
            projectWorkspace="ws",
            projectNamespace="proj",
        )
        request.get_rag_branch.return_value = None
        request.get_rag_base_branch.return_value = None

        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=MagicMock(),
            request=request,
            rag_client=MagicMock(),
        )

        assert result == mock_smart.return_value
        assert mock_smart.call_args.kwargs["branches"] == []
        assert mock_smart.call_args.kwargs["rag_client"] is None

    @patch("service.review.orchestrator.stage_1_file_review.create_smart_batches_async")
    @pytest.mark.asyncio(loop_scope="function")
    async def test_exact_receipts_disable_unbound_batching_rag(self, mock_smart):
        groups = self._make_plan(["a.py"])
        mock_smart.return_value = [[{
            "file": groups[0].files[0],
            "priority": "MEDIUM",
        }]]
        request = MagicMock(
            enrichmentData=None,
            maxAllowedTokens=200000,
            projectWorkspace="ws",
            projectNamespace="proj",
            ragBaseGenerationManifestSha256="a" * 64,
            ragPrGenerationFingerprint="sha256:" + "b" * 64,
            ragPrOverlayGenerationManifestSha256="c" * 64,
        )
        request.get_rag_branch.return_value = "main"
        request.get_rag_base_branch.return_value = "main"

        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=MagicMock(),
            request=request,
            rag_client=MagicMock(),
        )

        assert result == mock_smart.return_value
        assert mock_smart.call_args.kwargs["rag_client"] is None


class TestStage1Scheduling:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_batches_run_with_bounded_concurrency(self):
        files = [ReviewFile(path=f"src/f{i}.py", focus_areas=[], risk_level="MEDIUM") for i in range(3)]
        batches = [[{"file": f, "priority": "MEDIUM"}] for f in files]
        request = MagicMock()
        request.deltaDiff = None
        request.rawDiff = ""
        request.taskContext = None
        request.enrichmentData = None
        request.changedFiles = [f.path for f in files]

        async def fake_batches(**kwargs):
            return batches

        async def fake_review(*args, **kwargs):
            await asyncio.sleep(0.05)
            return []

        with patch(
            "service.review.orchestrator.stage_1_file_review.create_smart_batches_wrapper",
            side_effect=fake_batches,
        ), patch(
            "service.review.orchestrator.stage_1_file_review.review_file_batch",
            side_effect=fake_review,
        ):
            started = time.perf_counter()
            issues = await execute_stage_1_file_reviews(
                llm=MagicMock(),
                request=request,
                plan=ReviewPlan(analysis_summary="x", file_groups=[], cross_file_concerns=[]),
                rag_client=None,
                max_parallel=3,
            )
            elapsed = time.perf_counter() - started

        assert issues == []
        assert elapsed < 0.12

    @pytest.mark.asyncio(loop_scope="function")
    async def test_reverse_completion_keeps_batch_order_and_completes_units(self):
        files = [
            ReviewFile(
                path=f"src/f{i}.py",
                focus_areas=[],
                risk_level="MEDIUM",
            )
            for i in range(3)
        ]
        batches = [[{"file": file, "priority": "MEDIUM"}] for file in files]
        request = MagicMock(
            deltaDiff=None,
            rawDiff="",
            taskContext=None,
            enrichmentData=None,
            changedFiles=[file.path for file in files],
        )
        state = Stage1ReviewUnitState()

        async def fake_batches(**kwargs):
            return batches

        async def fake_review(_llm, _request, batch, *_args, **_kwargs):
            index = int(batch[0]["file"].path.removesuffix(".py")[-1])
            await asyncio.sleep((2 - index) * 0.02)
            return [batch[0]["file"].path]

        with patch(
            "service.review.orchestrator.stage_1_file_review.create_smart_batches_wrapper",
            side_effect=fake_batches,
        ), patch(
            "service.review.orchestrator.stage_1_file_review.review_file_batch",
            side_effect=fake_review,
        ):
            issues = await execute_stage_1_file_reviews(
                llm=MagicMock(),
                request=request,
                plan=ReviewPlan(
                    analysis_summary="x",
                    file_groups=[],
                    cross_file_concerns=[],
                ),
                rag_client=None,
                max_parallel=3,
                review_unit_state=state,
            )

        assert issues == [file.path for file in files]
        state.assert_complete()
        assert len(state.completed_unit_ids) == 3

    @pytest.mark.asyncio(loop_scope="function")
    async def test_any_failed_batch_fails_the_whole_stage(self):
        files = [ReviewFile(path=f"src/f{i}.py", focus_areas=[], risk_level="MEDIUM") for i in range(2)]
        batches = [[{"file": file, "priority": "MEDIUM"}] for file in files]
        request = MagicMock(
            deltaDiff=None,
            rawDiff="",
            taskContext=None,
            enrichmentData=None,
            changedFiles=[file.path for file in files],
        )

        async def fake_batches(**kwargs):
            return batches

        async def fake_review(_llm, _request, batch, *_args, **_kwargs):
            if batch[0]["file"].path.endswith("f0.py"):
                raise RuntimeError("provider timeout")
            await asyncio.sleep(0.1)
            return []

        with patch(
            "service.review.orchestrator.stage_1_file_review.create_smart_batches_wrapper",
            side_effect=fake_batches,
        ), patch(
            "service.review.orchestrator.stage_1_file_review.review_file_batch",
            side_effect=fake_review,
        ):
            with pytest.raises(RuntimeError, match="Stage 1 review is incomplete"):
                await execute_stage_1_file_reviews(
                    llm=MagicMock(),
                    request=request,
                    plan=ReviewPlan(analysis_summary="x", file_groups=[], cross_file_concerns=[]),
                    rag_client=None,
                    max_parallel=2,
                )
