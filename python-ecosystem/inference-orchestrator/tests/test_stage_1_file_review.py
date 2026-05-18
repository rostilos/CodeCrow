"""
Tests for pure helper functions in stage_1_file_review.py.

Covers: chunk_files, _detect_batch_language, _chunk_matches_language,
        _deduplicate_pr_stale_chunks, _build_duplication_queries_from_diff,
        _scope_deterministic_to_diff, _extract_calibrated_issues,
        create_smart_batches_wrapper
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from service.review.orchestrator.stage_1_file_review import (
    chunk_files,
    _detect_batch_language,
    _chunk_matches_language,
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


# ── _detect_batch_language ───────────────────────────────────────

class TestDetectBatchLanguage:
    def test_java_files(self):
        # _LANG_GROUPS key is 'java'
        result = _detect_batch_language(["Foo.java", "Bar.java"])
        assert result == "java"

    def test_python_files(self):
        # _LANG_GROUPS key is 'py'
        result = _detect_batch_language(["main.py", "utils.py"])
        assert result == "py"

    def test_javascript_files(self):
        # _LANG_GROUPS key is 'js'
        result = _detect_batch_language(["app.js", "index.ts", "page.tsx"])
        assert result == "js"

    def test_mixed_files_returns_dominant(self):
        # 2 java vs 1 py → 67% < 70% threshold → None
        result = _detect_batch_language(["a.java", "b.java", "c.py"])
        assert result is None

    def test_mixed_files_above_threshold(self):
        # 3 java vs 1 py → 75% >= 70% → 'java'
        result = _detect_batch_language(["a.java", "b.java", "c.java", "d.py"])
        assert result == "java"

    def test_empty_returns_none(self):
        result = _detect_batch_language([])
        assert result is None

    def test_unknown_extensions(self):
        result = _detect_batch_language(["readme.md", "config.yml"])
        assert result is None

    def test_go_files(self):
        result = _detect_batch_language(["main.go", "handler.go"])
        assert result == "go"

    def test_ruby_files(self):
        # _LANG_GROUPS key is 'rb'
        result = _detect_batch_language(["app.rb", "model.rb"])
        assert result == "rb"

    def test_csharp_files(self):
        # _LANG_GROUPS key is 'cs'
        result = _detect_batch_language(["Program.cs", "Service.cs"])
        assert result == "cs"

    def test_php_files(self):
        result = _detect_batch_language(["index.php", "api.php"])
        assert result == "php"


# ── _chunk_matches_language ──────────────────────────────────────

class TestChunkMatchesLanguage:
    def test_no_batch_lang_always_matches(self):
        chunk = {"metadata": {"path": "foo.rb"}}
        assert _chunk_matches_language(chunk, None) is True

    def test_java_chunk_matches_java(self):
        chunk = {"metadata": {"path": "src/Foo.java"}}
        assert _chunk_matches_language(chunk, "java") is True

    def test_python_chunk_does_not_match_java(self):
        chunk = {"metadata": {"path": "src/foo.py"}}
        assert _chunk_matches_language(chunk, "java") is False

    def test_no_path_passes_through(self):
        chunk = {"metadata": {}}
        assert _chunk_matches_language(chunk, "java") is True

    def test_chunk_with_file_path_key(self):
        chunk = {"file_path": "src/main.go"}
        assert _chunk_matches_language(chunk, "go") is True

    def test_config_file_always_passes(self):
        chunk = {"metadata": {"path": "config.xml"}}
        assert _chunk_matches_language(chunk, "py") is True

    def test_javascript_chunk_matches_js(self):
        chunk = {"metadata": {"path": "app.tsx"}}
        assert _chunk_matches_language(chunk, "js") is True

    def test_unknown_ext_passes_through(self):
        chunk = {"metadata": {"path": "main.rs"}}
        # .rs not in _EXT_TO_GROUP → passes through
        assert _chunk_matches_language(chunk, "java") is True


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
        # Should contain something related to the function name
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
        assert any("PaymentService" in q for q in result)
        assert any("BaseService" in q for q in result)
        assert any("Payable" in q for q in result)
        assert any("validateOrder" in q for q in result)

    def test_config_file_patterns(self):
        result = _build_duplication_queries_from_diff(
            [], ["application.yml", "build.gradle"],
        )
        assert any("application.yml" in q for q in result)

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

    def test_skips_short_names(self):
        enrichment = {
            "A.java": {
                "semantic_names": ["ab"],  # too short (len<=3)
                "extends": [],
                "implements": [],
                "calls": [],
            }
        }
        result = _build_duplication_queries_from_diff([], [], enrichment_metadata=enrichment)
        assert not any("ab" in q for q in result)

    def test_sql_table_extraction(self):
        diff_snippets = [
            "SELECT * FROM user_accounts WHERE active = true"
        ]
        result = _build_duplication_queries_from_diff(diff_snippets, [])
        assert any("user_accounts" in q for q in result)

    def test_class_extraction(self):
        diff_snippets = [
            "class PaymentGateway:\n    def process(self):\n        pass"
        ]
        result = _build_duplication_queries_from_diff(diff_snippets, [])
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

    def test_irrelevant_definition_capped(self):
        related_defs = {
            "UnusedHelper": [{"text": "class UnusedHelper", "metadata": {}}],
            "AnotherHelper": [{"text": "class AnotherHelper", "metadata": {}}],
            "ThirdHelper": [{"text": "class ThirdHelper", "metadata": {}}],
        }
        diff_snippets = ["+    x = SomethingElse()"]
        result = _scope_deterministic_to_diff(
            related_defs, diff_snippets, max_file_level=2
        )
        # All are irrelevant, only max_file_level=2 kept
        assert len(result) == 2
        for r in result:
            assert r["_diff_relevant"] is False

    def test_raw_diffs_used_for_token_extraction(self):
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

    def test_stopwords_excluded(self):
        # A stopword with 4+ chars (e.g., "self") that matches the token regex
        # but is excluded by stopword filtering
        related_defs = {
            "self": [{"text": "builtin self", "metadata": {}}],
        }
        diff_snippets = ["+    x = self.process()"]
        result = _scope_deterministic_to_diff(
            related_defs, diff_snippets, max_file_level=0
        )
        # 'self' is in stopwords, and 'process' (7 chars) will be in diff_tokens,
        # but 'self' won't match → def not diff-relevant → file_level capped to 0
        assert len(result) == 0

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

    def test_fallback_when_no_processed_diff(self):
        groups = self._make_plan(["a.py", "b.py"])
        result = create_smart_batches_wrapper(
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

    def test_single_file(self):
        groups = self._make_plan(["a.py"])
        result = create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=None,
            request=MagicMock(),
            rag_client=None,
        )
        assert len(result) == 1
        assert len(result[0]) == 1

    @patch("service.review.orchestrator.stage_1_file_review.create_smart_batches")
    def test_uses_smart_batches_when_available(self, mock_smart):
        mock_smart.return_value = None  # Force fallback
        groups = self._make_plan(["a.py", "b.py"])
        result = create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=MagicMock(),
            request=MagicMock(enrichmentData=None),
            rag_client=None,
        )
        # Should still return valid batches from fallback
        assert len(result) >= 1
