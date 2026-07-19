"""
Tests for pure helper functions in stage_1_file_review.py.

Covers: chunk_files, _deduplicate_pr_stale_chunks,
        _build_duplication_queries_from_diff,
        _scope_deterministic_to_diff, _extract_calibrated_issues,
        create_smart_batches_wrapper
"""
import pytest
import asyncio
import time
from unittest.mock import MagicMock, patch, AsyncMock

from service.review.orchestrator.stage_1_file_review import (
    chunk_files,
    Stage1PreparedContext,
    _build_stage_1_prepared_context,
    _find_diff_file_for_path,
    _chunk_diff_preserving_hunks,
    _expand_oversized_diff_batches,
    _format_batch_metadata_json,
    _extract_metadata_identifiers,
    _flatten_deterministic_context,
    _rag_context_has_chunks,
    Stage1RagState,
    fetch_batch_rag_context,
    execute_stage_1_file_reviews,
    review_file_batch,
    _resolve_fallback_rag_context,
    _scope_fallback_rag_context_to_batch,
    _supports_structured_output,
    _deduplicate_pr_stale_chunks,
    _build_duplication_queries_from_diff,
    _scope_deterministic_to_diff,
    _extract_calibrated_issues,
    create_smart_batches_wrapper,
)
from model.multi_stage import (
    FileGroup,
    ReviewFile,
    FileReviewBatchOutput,
    FileReviewOutput,
    ReviewPlan,
)
from model.output_schemas import CodeReviewIssue
from utils.diff_processor import DiffChangeType, DiffFile, ProcessedDiff


# ── chunk_files ──────────────────────────────────────────────────

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

    def test_cloudflare_structured_output_disabled_by_default(self):
        ChatCloudflareOpenAI = type("ChatCloudflareOpenAI", (), {})

        assert _supports_structured_output(ChatCloudflareOpenAI()) is False

    def test_oversized_processed_diff_defaults_to_summary_and_can_request_full_raw(self):
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

        assert diff_file.content == "[summary only]"
        assert prepared.full_diff_index_loaded is False

        full_diff_file = _find_diff_file_for_path(
            prepared,
            "src/big.py",
            use_full_diff=True,
        )

        assert "first_changed_line" in full_diff_file.content
        assert "[summary only]" not in full_diff_file.content
        assert prepared.full_diff_index_loaded is True

    def test_globally_compacted_diff_can_request_full_raw(self):
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

        assert diff_file.content == "[summary only]"
        assert prepared.full_diff_index_loaded is False

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
        assert any("@@ -1 +1,2 @@" in chunk for chunk in chunks)
        assert any("@@ -10 +11,2 @@" in chunk for chunk in chunks)

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

    def test_size_limited_diff_summary_not_expanded_without_full_diff_focus(self):
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

        assert len(expanded) == 1
        assert "_diff_override" not in expanded[0][0]
        assert prepared.full_diff_index_loaded is False

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

        assert '"path": "src/Foo.py"' in result
        assert "pkg24" in result
        assert "symbol34" in result
        assert "call19" in result

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
    async def test_semantic_timeout_disables_remaining_batches(self, monkeypatch):
        import service.review.orchestrator.stage_1_file_review as stage1

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
