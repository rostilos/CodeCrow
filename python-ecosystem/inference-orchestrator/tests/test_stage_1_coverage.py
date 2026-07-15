"""Full-file policy coverage for Stage 1's deterministic control paths."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import service.review.orchestrator.stage_1_file_review as stage1
from model.multi_stage import FileReviewBatchOutput, FileReviewOutput, ReviewFile, ReviewPlan
from model.output_schemas import CodeReviewIssue
from utils.diff_processor import DiffChangeType, DiffFile, ProcessedDiff


def _request(**overrides):
    values = {
        "deltaDiff": None,
        "rawDiff": "",
        "taskContext": None,
        "enrichmentData": None,
        "projectRules": [],
        "previousCodeAnalysisIssues": [],
        "changedFiles": ["src/a.py"],
        "deletedFiles": [],
        "projectWorkspace": "ws",
        "projectNamespace": "project",
        "pullRequestId": 7,
        "commitHash": "commit",
        "prTitle": "PR",
        "prDescription": "description",
        "maxAllowedTokens": 20_000,
    }
    values.update(overrides)
    request = MagicMock(**values)
    request.get_rag_branch.return_value = overrides.get("rag_branch", "feature")
    request.get_rag_base_branch.return_value = overrides.get("base_branch", "main")
    return request


def _issue(severity="MEDIUM"):
    return CodeReviewIssue(
        id="i1", severity=severity, category="BUG_RISK", file="src/a.py",
        line=1, title="Issue", reason="Reason", suggestedFixDescription="Fix",
    )


class TestStage1PureCoverage:
    def test_environment_lookup_path_and_current_context_boundaries(self, monkeypatch):
        monkeypatch.delenv("FLAG", raising=False)
        assert stage1._env_bool("FLAG", True) is True
        monkeypatch.setenv("FLAG", "yes")
        assert stage1._env_bool("FLAG", False) is True
        monkeypatch.delenv("COUNT", raising=False)
        assert stage1._env_int("COUNT", 3) == 3
        monkeypatch.setenv("COUNT", "bad")
        assert stage1._env_int("COUNT", 3) == 3
        monkeypatch.setenv("COUNT", "4")
        assert stage1._env_int("COUNT", 3) == 4

        assert stage1._path_lookup_keys(None) == []
        mapping = {}
        first, second = object(), object()
        stage1._add_path_lookup(mapping, "one/a.py", first)
        stage1._add_path_lookup(mapping, "two/a.py", second)
        stage1._add_path_lookup(mapping, "three/a.py", object())
        assert mapping["a.py"] is None
        assert stage1._lookup_by_path(mapping, "one/a.py") is first
        assert stage1._lookup_by_path(mapping, "missing.py") is None

        assert "unavailable" in stage1._bounded_current_file_context(None)
        with patch.object(stage1, "STAGE1_MAX_CURRENT_FILE_CHARS", 200):
            bounded = stage1._bounded_current_file_context("a" * 300)
        assert "characters omitted" in bounded

    def test_prepared_context_incremental_enrichment_and_diff_fallbacks(self):
        delta = (
            "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
        meta = SimpleNamespace(path="repo/src/a.py", symbol="A")
        complete = SimpleNamespace(path="repo/src/a.py", content="current", skipped=False)
        skipped = SimpleNamespace(path="src/b.py", content="ignored", skipped=True)
        request = _request(
            deltaDiff=delta,
            enrichmentData=SimpleNamespace(fileMetadata=[meta], fileContents=[complete, skipped]),
        )
        context = stage1._build_stage_1_prepared_context(request, None, True)
        assert stage1._find_diff_file_for_path(context, "src/a.py") is not None
        assert context.file_content_by_path["src/a.py"] == "current"
        assert context.enrichment_metadata_by_path["src/a.py"] is meta

        assert stage1._find_diff_file_for_path(None, "a.py") is None
        collision_file = DiffFile(
            path="root/src/a.py", change_type=DiffChangeType.MODIFIED, content="+x"
        )
        collision = stage1.Stage1PreparedContext(
            diff_source=ProcessedDiff(files=[collision_file]), diff_by_path={}
        )
        assert stage1._find_diff_file_for_path(collision, "src/a.py") is collision_file
        assert stage1._find_diff_file_for_path(collision, "missing.py") is None

        empty_full = stage1.Stage1PreparedContext(diff_source=ProcessedDiff(files=[]))
        stage1._ensure_full_diff_index(empty_full)
        stage1._ensure_full_diff_index(empty_full)
        assert empty_full.full_diff_index_loaded is True

        truncated = ProcessedDiff(files=[DiffFile(
            path="large.py", change_type=DiffChangeType.MODIFIED,
            content="", is_skipped=True, skip_reason="file too large",
        )])
        no_raw = stage1._build_stage_1_prepared_context(_request(rawDiff=""), truncated, False)
        assert no_raw.full_diff_raw is None
        assert stage1._find_diff_file_for_path(no_raw, "missing.py", use_full_diff=True) is None

    def test_metadata_focus_structured_and_numeric_helpers(self):
        meta = SimpleNamespace(path="repo/src/a.py", value="A", empty=None, _secret="x")
        request = _request(
            enrichmentData=SimpleNamespace(fileMetadata=[meta], fileContents=[])
        )
        prepared = stage1.Stage1PreparedContext(enrichment_metadata_by_path={})
        found = stage1._iter_batch_enrichment_metadata(request, ["src/a.py"], prepared)
        assert found == [meta]
        indexed = stage1.Stage1PreparedContext(enrichment_metadata_by_path={"src/a.py": meta})
        assert stage1._iter_batch_enrichment_metadata(request, ["src/a.py"], indexed) == [meta]
        partial = stage1.Stage1PreparedContext(enrichment_metadata_by_path={"src/a.py": meta})
        assert stage1._iter_batch_enrichment_metadata(
            request, ["src/a.py", "missing.py"], partial
        ) == [meta]
        assert stage1._iter_batch_enrichment_metadata(_request(), ["a.py"], None) == []

        unmatched = SimpleNamespace(path="other.py", value=1)
        trailing = SimpleNamespace(path="root/src/a.py", value=2)
        fallback_request = _request(enrichmentData=SimpleNamespace(
            fileMetadata=[unmatched, trailing], fileContents=[]
        ))
        assert stage1._iter_batch_enrichment_metadata(
            fallback_request, ["src/a.py"], None
        ) == [trailing]

        assert stage1._item_requests_full_diff({"file": ReviewFile(
            path="a.py", focus_areas=[" full-diff-review "], risk_level="HIGH"
        )})
        assert not stage1._item_requests_full_diff({})
        assert stage1._metadata_to_payload({"a": 1, "none": None}) == {"a": 1}
        assert stage1._metadata_to_payload(meta) == {"path": "repo/src/a.py", "value": "A"}
        assert stage1._extract_metadata_identifiers([], limit=1) is None
        assert stage1._extract_metadata_identifiers([{"a": "one", "b": "two"}], limit=1) == ["one"]
        assert stage1._extract_metadata_identifiers(
            [{"a": "same", "b": "same", "c": 3}]
        ) == ["same"]
        assert stage1._positive_int_or_default("bad", 5) == 5
        assert stage1._positive_int_or_default(0, 5) == 5
        assert stage1._positive_int_or_default(6, 5) == 6

        with patch.object(stage1, "STRUCTURED_OUTPUT_ENABLED", False):
            assert not stage1._supports_structured_output(MagicMock())
        with patch.object(stage1, "STRUCTURED_OUTPUT_ENABLED", True), patch.object(
            stage1, "CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED", True
        ):
            assert stage1._supports_structured_output(MagicMock())

    def test_flatten_caps_deduplicates_and_scores_all_groups(self):
        duplicate = {"text": "same", "metadata": {"path": "a.py"}}
        response = {"context": {
            "changed_files": {"a.py": [duplicate, duplicate]},
            "related_definitions": "invalid",
            "chunks": ["invalid", {"content": "other", "path": "b.py"}],
        }}
        result = stage1._flatten_deterministic_context(response, max_chunks=2)
        assert len(result) == 2
        assert stage1._flatten_deterministic_context(None) == []
        assert stage1._deterministic_score("definition") == .95
        assert stage1._deterministic_score("changed_file") == .92
        assert stage1._deterministic_score("namespace_context") == .86
        assert stage1._deterministic_score("other") == .84

    def test_plain_diff_chunking_and_stale_chunk_variants(self):
        plain = "header\n" + "x" * 20 + "\n" + "y" * 20
        chunks = stage1._chunk_diff_preserving_hunks(plain, max_tokens=3)
        assert len(chunks) > 1
        assert stage1._split_hunk_by_lines("", 1) == [""]
        assert stage1._split_hunk_by_lines("short", 20) == ["short"]
        assert stage1._split_hunk_by_lines(" " * 20, 2) == [" " * 20]

        only_pr = [{"path": "src/a.py", "text": "fresh", "_source": "pr_indexed"}]
        assert stage1._deduplicate_pr_stale_chunks(
            only_pr, ["src/a.py"], ["other.py"]
        ) == only_pr
        assert len(stage1._chunk_diff_preserving_hunks(
            "header\n@@ -1 +1 @@\n-old\n+new\n", max_tokens=3
        )) >= 1
        duplicate_queries = stage1._build_duplication_queries_from_diff(
            ["same evidence", "same evidence"], ["a.py"]
        )
        assert len(duplicate_queries) == 1


class TestStage1RagCoverage:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_client_returns_none(self):
        assert await stage1.fetch_batch_rag_context(
            None, _request(), ["a.py"], []
        ) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_semantic_duplication_pr_dedup_and_reranking(self):
        request = _request(
            changedFiles=["src/a.py", "src/stale.py"],
            enrichmentData=SimpleNamespace(
                fileMetadata=[SimpleNamespace(path="src/a.py", symbol="A")],
                fileContents=[],
            ),
        )

        class Rag:
            async def get_deterministic_context(self, **_kwargs):
                return {"context": {"chunks": []}}

            async def get_pr_context(self, **_kwargs):
                return {"context": {"relevant_code": [
                    {"text": "semantic", "file_path": "semantic.py"},
                    {"text": "stale", "file_path": "src/stale.py", "_source": "branch"},
                    {"text": "fresh", "file_path": "src/stale.py", "_source": "pr_indexed"},
                ]}}

            async def search_for_duplicates(self, **_kwargs):
                values = [
                    {"text": "same batch", "metadata": {"path": "src/a.py"}},
                    {"text": "", "metadata": {"path": "empty.py"}},
                    {"text": "seen", "metadata": {"path": "semantic.py"}},
                ]
                values.extend({
                    "text": f"duplicate {i}", "score": .1,
                    "metadata": {"path": f"dup{i}.py"}, "_query": "q",
                } for i in range(6))
                return values

        rerank_result = SimpleNamespace(
            method="structural", processing_time_ms=1,
            original_count=8, reranked_count=8,
        )
        reranker = MagicMock()
        reranker.rerank = AsyncMock(side_effect=lambda chunks, **_kwargs: (chunks, rerank_result))
        result = await stage1.fetch_batch_rag_context(
            Rag(), request, ["src/a.py"], ["+changed"], pr_indexed=True,
            llm_reranker=reranker, use_llm_rerank=False,
            enrichment_identifiers=["A"], batch_priority="UNKNOWN",
            rag_state=stage1.Stage1RagState(),
        )
        paths = [chunk.get("file_path") for chunk in result["relevant_code"]]
        assert "src/stale.py" in paths
        assert len([path for path in paths if path and path.startswith("dup")]) == 5
        reranker.rerank.assert_awaited_once()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_provider_failures_disable_semantic_and_outer_failure_is_safe(self):
        class Rag:
            async def get_deterministic_context(self, **_kwargs):
                raise RuntimeError("deterministic")

            async def get_pr_context(self, **_kwargs):
                raise RuntimeError("semantic")

            async def search_for_duplicates(self, **_kwargs):
                raise RuntimeError("duplication")

        state = stage1.Stage1RagState()
        assert await stage1.fetch_batch_rag_context(
            Rag(), _request(), ["a.py"], ["+changed"], rag_state=state
        ) is None
        assert state.semantic_disabled and state.semantic_failures == 1

        broken_request = _request()
        broken_request.get_rag_branch.side_effect = RuntimeError("request")
        assert await stage1.fetch_batch_rag_context(
            Rag(), broken_request, ["a.py"], []
        ) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_feature_flags_and_reranker_failure_are_nonfatal(self):
        class Rag:
            async def get_deterministic_context(self, **_kwargs):
                return {"context": {"chunks": [{"text": "one", "path": "x.py"}]}}

            async def get_pr_context(self, **_kwargs):
                raise AssertionError("disabled")

            async def search_for_duplicates(self, **_kwargs):
                raise AssertionError("disabled")

        reranker = MagicMock(rerank=AsyncMock(side_effect=RuntimeError("rerank")))
        with patch.object(stage1, "SEMANTIC_RAG_FILLER_ENABLED", False), patch.object(
            stage1, "DUPLICATION_RAG_ENABLED", False
        ):
            result = await stage1.fetch_batch_rag_context(
                Rag(), _request(), ["a.py"], [], llm_reranker=reranker
            )
        assert len(result["relevant_code"]) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_semantic_cap_duplication_without_prior_context_and_zero_additions(self):
        class SemanticRag:
            async def get_deterministic_context(self, **_kwargs):
                return None

            async def get_pr_context(self, **_kwargs):
                return {"relevant_code": [
                    {"text": str(i), "path": f"s{i}.py"} for i in range(12)
                ]}

            async def search_for_duplicates(self, **_kwargs):
                return []

        semantic = await stage1.fetch_batch_rag_context(
            SemanticRag(), _request(), ["a.py"], [], batch_priority="MEDIUM"
        )
        assert len(semantic["relevant_code"]) == 10

        class DupRag:
            async def get_deterministic_context(self, **_kwargs):
                return None

            async def get_pr_context(self, **_kwargs):
                return None

            async def search_for_duplicates(self, **_kwargs):
                return [{"text": "duplicate", "metadata": {"path": "other.py"}}]

        duplicate = await stage1.fetch_batch_rag_context(
            DupRag(), _request(), ["a.py"], ["+query"]
        )
        assert duplicate["relevant_code"][0]["file_path"] == "other.py"

        class SkippedDupRag(DupRag):
            async def get_deterministic_context(self, **_kwargs):
                return {"chunks": [{"text": "context", "path": "ctx.py"}]}

            async def search_for_duplicates(self, **_kwargs):
                return [{"text": "same", "metadata": {"path": "a.py"}}]

        skipped = await stage1.fetch_batch_rag_context(
            SkippedDupRag(), _request(), ["a.py"], ["+query"]
        )
        assert len(skipped["relevant_code"]) == 1

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize("semantic_error", [asyncio.TimeoutError(), RuntimeError("semantic")])
    async def test_semantic_fault_without_shared_state_is_contained(self, semantic_error):
        class Rag:
            async def get_deterministic_context(self, **_kwargs):
                return None

            async def get_pr_context(self, **_kwargs):
                raise semantic_error

            async def search_for_duplicates(self, **_kwargs):
                return []

        assert await stage1.fetch_batch_rag_context(
            Rag(), _request(), ["a.py"], ["+change"], rag_state=None
        ) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_semantic_merge_preserves_existing_context_and_zero_stale_removal(self):
        class Rag:
            async def get_deterministic_context(self, **_kwargs):
                return {"context": {"chunks": [{"text": "det", "path": "dep.py"}]}}

            async def get_pr_context(self, **_kwargs):
                return {"context": {"relevant_code": [{"text": "sem", "path": "sem.py"}]}}

            async def search_for_duplicates(self, **_kwargs):
                return []

        request = _request(
            changedFiles=["a.py"],
            enrichmentData=SimpleNamespace(fileMetadata=[
                SimpleNamespace(path="other.py", symbol="Other"),
                SimpleNamespace(path="a.py", symbol="A"),
            ], fileContents=[]),
        )
        result = await stage1.fetch_batch_rag_context(
            Rag(), request, ["a.py"], ["+change"],
            pr_indexed=True,
        )
        assert [item["text"] for item in result["relevant_code"]] == ["det", "sem"]


class TestStage1ExecutionCoverage:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_smart_batch_defaults_and_enrichment_success(self):
        file = ReviewFile(path="a.py", focus_areas=[], risk_level="LOW")
        group = SimpleNamespace(files=[file], priority="LOW")
        request = _request(
            rag_branch=None, base_branch=None,
            enrichmentData=SimpleNamespace(fileMetadata=[], fileContents=[]),
        )
        request.get_rag_branch.return_value = None
        request.get_rag_base_branch.return_value = None
        with patch.object(
            stage1, "create_smart_batches_async",
            new=AsyncMock(return_value=[[{"file": file, "priority": "LOW"}]]),
        ) as smart:
            result = await stage1.create_smart_batches_wrapper(
                [group], None, request, None
            )
        assert result and smart.await_args.kwargs["branches"] == ["main", "master"]

    def test_expand_flushes_current_batch_before_large_segments(self):
        small_file = ReviewFile(path="small.py", focus_areas=[], risk_level="LOW")
        large_file = ReviewFile(path="large.py", focus_areas=[], risk_level="LOW")
        small = DiffFile(
            path="small.py", change_type=DiffChangeType.MODIFIED, content="+small"
        )
        large = DiffFile(
            path="large.py", change_type=DiffChangeType.MODIFIED,
            content="@@ -1 +1 @@\n" + "+x\n" * 20,
        )
        context = stage1.Stage1PreparedContext(
            diff_source=ProcessedDiff(files=[small, large]),
            diff_by_path={"small.py": small, "large.py": large},
        )
        result = stage1._expand_oversized_diff_batches([[{
            "file": small_file, "priority": "LOW"
        }, {"file": large_file, "priority": "LOW"}]], context, 3)
        assert result[0][0]["file"].path == "small.py"
        assert len(result) > 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_batches_and_failed_batch_are_contained(self):
        with patch.object(stage1, "create_smart_batches_wrapper", new=AsyncMock(return_value=[])):
            assert await stage1.execute_stage_1_file_reviews(
                MagicMock(), _request(), ReviewPlan(analysis_summary="none", file_groups=[]), None
            ) == []

        file = ReviewFile(path="a.py", focus_areas=[], risk_level="LOW")
        batches = [[{"file": file, "priority": "LOW"}]]
        with patch.object(
            stage1, "create_smart_batches_wrapper", new=AsyncMock(return_value=batches)
        ), patch.object(
            stage1, "review_file_batch", new=AsyncMock(side_effect=RuntimeError("batch"))
        ):
            assert await stage1.execute_stage_1_file_reviews(
                MagicMock(), _request(changedFiles=["a.py"]),
                ReviewPlan(analysis_summary="one", file_groups=[]), None, max_parallel=0,
            ) == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_review_batch_compatibility_segment_context_and_fallback_llm(self):
        file = ReviewFile(path="src/a.py", focus_areas=[], risk_level="HIGH")
        processed = ProcessedDiff(files=[DiffFile(
            path="src/a.py", change_type=DiffChangeType.MODIFIED, content="+changed"
        )])
        meta = SimpleNamespace(path="src/a.py", symbol="A")
        previous = {"file": "src/a.py", "line": 1, "reason": "old"}
        request = _request(
            enrichmentData=SimpleNamespace(fileMetadata=[meta], fileContents=[]),
            previousCodeAnalysisIssues=[previous],
        )
        primary, fallback = MagicMock(), MagicMock()
        invoke = AsyncMock(side_effect=[None, [_issue()]])
        with patch.object(
            stage1, "fetch_batch_rag_context",
            new=AsyncMock(return_value={"relevant_code": [{"text": "ctx", "path": "src/a.py"}]}),
        ), patch.object(stage1, "_invoke_stage_1_batch_llm", invoke):
            result = await stage1.review_file_batch(
                primary, request,
                [{"file": file, "priority": "HIGH", "_diff_override": "+segment",
                  "_diff_chunk_total": 2, "_diff_chunk_index": 1}],
                MagicMock(), processed, fallback_llm=fallback,
            )
        assert len(result) == 1
        assert [call.args[0] for call in invoke.await_args_list] == [primary, fallback]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_review_batch_fallback_context_and_total_parse_failure(self):
        file = ReviewFile(path="src/a.py", focus_areas=[], risk_level="LOW")
        request = _request()
        with patch.object(stage1, "_invoke_stage_1_batch_llm", new=AsyncMock(return_value=None)):
            result = await stage1.review_file_batch(
                MagicMock(), request, [{"file": file, "priority": "LOW"}], None,
                fallback_rag_context={
                    "chunks": [{"text": "ctx", "metadata": {"path": "src/a.py"}}]
                },
            )
        assert result == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_review_batch_uses_indexed_diff_and_empty_batch_defaults(self):
        file = ReviewFile(path="src/a.py", focus_areas=[], risk_level="LOW")
        diff = DiffFile(
            path="src/a.py", change_type=DiffChangeType.MODIFIED, content="+from-index"
        )
        prepared = stage1.Stage1PreparedContext(
            diff_source=ProcessedDiff(files=[diff]), diff_by_path={"src/a.py": diff}
        )
        request = _request()
        with patch.object(
            stage1, "_invoke_stage_1_batch_llm", new=AsyncMock(return_value=[])
        ) as invoke:
            await stage1.review_file_batch(
                MagicMock(), request, [{"file": file, "priority": "LOW"}], None,
                prepared_context=prepared,
            )
            await stage1.review_file_batch(
                MagicMock(), request, [], None, prepared_context=prepared,
            )
        assert "+from-index" in invoke.await_args_list[0].args[1]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_review_batch_ignores_numeric_metadata_and_unrelated_previous_issue(self):
        file = ReviewFile(path="src/a.py", focus_areas=[], risk_level="LOW")
        request = _request(
            enrichmentData=SimpleNamespace(
                fileMetadata=[SimpleNamespace(path="src/a.py", count=3)], fileContents=[]
            ),
            previousCodeAnalysisIssues=[{"file": "other.py", "reason": "old"}],
        )
        with patch.object(stage1, "_invoke_stage_1_batch_llm", new=AsyncMock(return_value=[])):
            with patch.object(stage1, "_extract_metadata_identifiers", return_value=None):
                assert await stage1.review_file_batch(
                    MagicMock(), request, [{"file": file, "priority": "LOW"}], None
                ) == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_fallback_resolution_timeout_cancel_and_unsupported(self):
        async def slow():
            await asyncio.sleep(1)

        with patch.object(stage1, "GLOBAL_RAG_FALLBACK_TIMEOUT_SECONDS", .001):
            task = asyncio.create_task(slow())
            assert await stage1._resolve_fallback_rag_context(task) is None
            task.cancel()

        cancelled = asyncio.get_running_loop().create_future()
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stage1._resolve_fallback_rag_context(cancelled)
        assert await stage1._resolve_fallback_rag_context("bad") is None
        assert stage1._scope_fallback_rag_context_to_batch({"chunks": []}, ["a.py"]) is None
        assert not stage1._chunk_matches_batch_path("bad", ["a.py"])
        assert not stage1._chunk_matches_batch_path({}, ["a.py"])
        assert not stage1._chunk_matches_batch_path({"path": "a.py"}, [""])

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invoke_structured_empty_raw_success_unstructured_and_failure(self):
        output = FileReviewBatchOutput(reviews=[FileReviewOutput(
            file="src/a.py", analysis_summary="ok", issues=[_issue()], confidence="HIGH"
        )])
        llm = MagicMock()
        llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=None)
        llm.ainvoke = AsyncMock(return_value=MagicMock(content="json"))
        with patch.object(stage1, "parse_llm_response", new=AsyncMock(return_value=output)):
            result = await stage1._invoke_stage_1_batch_llm(llm, "prompt", ["a.py"], "test")
        assert len(result) == 1

        raw = MagicMock()
        raw.ainvoke = AsyncMock(side_effect=RuntimeError("parse"))
        with patch.object(stage1, "_supports_structured_output", return_value=False):
            assert await stage1._invoke_stage_1_batch_llm(
                raw, "prompt", ["a.py"], "test"
            ) is None

        unstructured = MagicMock()
        unstructured.ainvoke = AsyncMock(return_value=MagicMock(content="json"))
        with patch.object(stage1, "_supports_structured_output", return_value=False), patch.object(
            stage1, "parse_llm_response", new=AsyncMock(return_value=FileReviewBatchOutput(reviews=[]))
        ):
            assert await stage1._invoke_stage_1_batch_llm(
                unstructured, "prompt", ["a.py"], "test"
            ) == []

        structured_success = MagicMock()
        structured_success.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=output
        )
        assert len(await stage1._invoke_stage_1_batch_llm(
            structured_success, "prompt", ["a.py"], "test"
        )) == 1

        structured_failure = MagicMock()
        structured_failure.with_structured_output.return_value.ainvoke = AsyncMock(
            side_effect=RuntimeError("structured")
        )
        structured_failure.ainvoke = AsyncMock(return_value=MagicMock(content="json"))
        with patch.object(stage1, "parse_llm_response", new=AsyncMock(return_value=output)):
            assert len(await stage1._invoke_stage_1_batch_llm(
                structured_failure, "prompt", ["a.py"], "test"
            )) == 1
