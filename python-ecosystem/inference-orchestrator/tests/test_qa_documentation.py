"""
Tests for QaDocOrchestrator and BaseOrchestrator helper methods.

Covers: _slim_stage_results, _build_placeholders, _extract_documented_prs,
        _extract_text, _parse_json_from_response, _truncate,
        emit_status, emit_progress, emit_error,
        BaseOrchestrator._simple_batch, filter_diff_for_files,
        get_file_content_from_enrichment, build_enrichment_lookup
"""
import pytest
import json
from unittest.mock import MagicMock

from service.qa_documentation.qa_doc_orchestrator import QaDocOrchestrator
from service.qa_documentation.base_orchestrator import (
    BaseOrchestrator,
    emit_status,
    emit_progress,
    emit_error,
)


# ── emit_status / emit_progress / emit_error ─────────────────────

class TestEmitFunctions:
    def test_emit_status_calls_callback(self):
        cb = MagicMock()
        emit_status(cb, "stage_1", "Starting stage 1")
        cb.assert_called_once()
        args = cb.call_args[0][0]
        assert args["type"] == "status"
        assert args["stage"] == "stage_1"

    def test_emit_progress_calls_callback(self):
        cb = MagicMock()
        emit_progress(cb, 50, "Halfway done")
        cb.assert_called_once()
        args = cb.call_args[0][0]
        assert args["type"] == "progress"
        assert args["percent"] == 50

    def test_emit_error_calls_callback(self):
        cb = MagicMock()
        emit_error(cb, "Something went wrong")
        cb.assert_called_once()
        args = cb.call_args[0][0]
        assert args["type"] == "error"

    def test_emit_with_none_callback(self):
        emit_status(None, "s", "m")
        emit_progress(None, 0, "m")
        emit_error(None, "e")  # Should not raise

    def test_emit_swallows_exceptions(self):
        cb = MagicMock(side_effect=RuntimeError("boom"))
        emit_status(cb, "s", "m")  # Should not raise
        emit_progress(cb, 0, "m")
        emit_error(cb, "e")


# ── BaseOrchestrator._simple_batch ───────────────────────────────

class TestSimpleBatch:
    def test_basic_batching(self):
        paths = ["a.py", "b.py", "c.py", "d.py", "e.py"]
        batches = BaseOrchestrator._simple_batch(paths, batch_size=2)
        assert len(batches) == 3
        assert len(batches[0]) == 2
        assert len(batches[1]) == 2
        assert len(batches[2]) == 1

    def test_empty(self):
        assert BaseOrchestrator._simple_batch([], batch_size=5) == []

    def test_single_batch(self):
        paths = ["a.py", "b.py"]
        batches = BaseOrchestrator._simple_batch(paths, batch_size=10)
        assert len(batches) == 1
        assert len(batches[0]) == 2

    def test_items_have_file_info_and_priority(self):
        batches = BaseOrchestrator._simple_batch(["a.py"], batch_size=5)
        item = batches[0][0]
        assert hasattr(item["file_info"], "path")
        assert item["file_info"].path == "a.py"
        assert item["priority"] == "MEDIUM"


# ── BaseOrchestrator.filter_diff_for_files ───────────────────────

class TestBaseFilterDiffForFiles:
    def test_filters_correctly(self):
        raw_diff = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n+new\n"
            "diff --git a/src/other.py b/src/other.py\n"
            "--- a/src/other.py\n+++ b/src/other.py\n@@ -1 +1 @@\n+new\n"
        )
        result = BaseOrchestrator.filter_diff_for_files(raw_diff, {"src/main.py"})
        assert "src/main.py" in result
        assert "src/other.py" not in result

    def test_suffix_matching(self):
        raw_diff = "diff --git a/src/main.py b/src/main.py\n---\n+++\n@@\n"
        result = BaseOrchestrator.filter_diff_for_files(
            raw_diff, {"repo/src/main.py"}
        )
        assert result is not None

    def test_none_input(self):
        assert BaseOrchestrator.filter_diff_for_files(None, {"a.py"}) is None

    def test_empty_files(self):
        assert BaseOrchestrator.filter_diff_for_files("diff...", set()) is None


# ── BaseOrchestrator.get_file_content_from_enrichment ────────────

class TestGetFileContentFromEnrichment:
    def test_exact_match(self):
        fc = MagicMock(path="a.py", content="code", skipped=False)
        enrichment = MagicMock(fileContents=[fc])
        result = BaseOrchestrator.get_file_content_from_enrichment("a.py", enrichment)
        assert result == "code"

    def test_suffix_match(self):
        fc = MagicMock(path="src/a.py", content="code", skipped=False)
        enrichment = MagicMock(fileContents=[fc])
        result = BaseOrchestrator.get_file_content_from_enrichment("a.py", enrichment)
        assert result == "code"

    def test_skipped_returns_none(self):
        fc = MagicMock(path="a.py", content="code", skipped=True)
        enrichment = MagicMock(fileContents=[fc])
        result = BaseOrchestrator.get_file_content_from_enrichment("a.py", enrichment)
        assert result is None

    def test_no_enrichment(self):
        assert BaseOrchestrator.get_file_content_from_enrichment("a.py", None) is None

    def test_no_file_contents(self):
        enrichment = MagicMock(fileContents=None)
        assert BaseOrchestrator.get_file_content_from_enrichment("a.py", enrichment) is None


# ── BaseOrchestrator.build_enrichment_lookup ─────────────────────

class TestBuildEnrichmentLookup:
    def test_builds_lookup(self):
        fc1 = MagicMock(path="src/a.py", content="code_a", skipped=False)
        fc2 = MagicMock(path="src/b.py", content="code_b", skipped=False)
        enrichment = MagicMock(fileContents=[fc1, fc2])
        lookup = BaseOrchestrator.build_enrichment_lookup(enrichment)
        assert "src/a.py" in lookup
        assert "a.py" in lookup  # Short path also mapped
        assert lookup["src/a.py"] == "code_a"

    def test_skips_empty_content(self):
        fc = MagicMock(path="a.py", content="", skipped=False)
        enrichment = MagicMock(fileContents=[fc])
        lookup = BaseOrchestrator.build_enrichment_lookup(enrichment)
        assert "a.py" not in lookup

    def test_skips_skipped_files(self):
        fc = MagicMock(path="a.py", content="code", skipped=True)
        enrichment = MagicMock(fileContents=[fc])
        lookup = BaseOrchestrator.build_enrichment_lookup(enrichment)
        assert "a.py" not in lookup

    def test_none_enrichment(self):
        assert BaseOrchestrator.build_enrichment_lookup(None) == {}


# ── QaDocOrchestrator._slim_stage_results ────────────────────────

class TestSlimStageResults:
    def test_strips_verbose_fields(self):
        data = [
            {
                "batch_id": 1,
                "raw_analysis": "long text...",
                "error": "some error",
                "file_analyses": [{"path": "a.py", "issues": ["bug"]}],
            }
        ]
        result = QaDocOrchestrator._slim_stage_results(data)
        parsed = json.loads(result)
        assert "raw_analysis" not in parsed[0]
        assert "error" not in parsed[0]
        assert "batch_id" not in parsed[0]
        assert "file_analyses" in parsed[0]

    def test_truncates_when_max_chars(self):
        data = {"key": "x" * 10000}
        result = QaDocOrchestrator._slim_stage_results(data, max_chars=100)
        assert len(result) <= 150  # 100 + suffix

    def test_empty_values_stripped(self):
        data = {"key": "value", "empty_list": [], "none_val": None, "empty_str": ""}
        result = QaDocOrchestrator._slim_stage_results(data)
        parsed = json.loads(result)
        assert "empty_list" not in parsed
        assert "none_val" not in parsed
        assert "empty_str" not in parsed

    def test_compact_format(self):
        data = {"a": 1}
        result = QaDocOrchestrator._slim_stage_results(data)
        assert " " not in result  # Compact separators


# ── QaDocOrchestrator._build_placeholders ────────────────────────

class TestBuildPlaceholders:
    def test_basic_placeholders(self):
        orch = QaDocOrchestrator(llm=MagicMock())
        result = orch._build_placeholders(
            project_name="TestProject",
            pr_number=42,
            issues_found=5,
            files_analyzed=10,
            pr_metadata={"prTitle": "Fix bug", "prDescription": "desc", "analysisSummary": "summary"},
            task_context_dict={"task_key": "JIRA-123"},
            task_context_block="Task: JIRA-123",
            diff="diff here",
            source_branch="feature/x",
            target_branch="main",
        )
        assert result["project_name"] == "TestProject"
        assert result["pr_number"] == "42"
        assert result["issues_found"] == "5"
        assert result["files_analyzed"] == "10"
        assert result["pr_title"] == "Fix bug"
        assert result["task_key"] == "JIRA-123"
        assert result["diff"] == "diff here"
        assert result["source_branch"] == "feature/x"
        assert result["output_language"] == "English"

    def test_defaults_when_empty(self):
        orch = QaDocOrchestrator(llm=MagicMock())
        result = orch._build_placeholders(
            project_name=None,
            pr_number=None,
            issues_found=0,
            files_analyzed=0,
            pr_metadata={},
            task_context_dict=None,
            task_context_block="",
            diff=None,
        )
        assert result["project_name"] == "Unknown"
        assert result["pr_number"] == "N/A"
        assert result["diff"] == "No diff available."

    def test_custom_language(self):
        orch = QaDocOrchestrator(llm=MagicMock())
        result = orch._build_placeholders(
            project_name="P",
            pr_number=1,
            issues_found=0,
            files_analyzed=0,
            pr_metadata={},
            task_context_dict=None,
            task_context_block="",
            diff="d",
            output_language="Russian",
        )
        assert result["output_language"] == "Russian"


# ── QaDocOrchestrator._extract_documented_prs ────────────────────

class TestExtractDocumentedPrs:
    def test_extracts_prs(self):
        doc = "some text\n<!-- codecrow-qa-autodoc:prs=1,2,3 -->\nmore text"
        result = QaDocOrchestrator._extract_documented_prs(doc)
        assert result == {1, 2, 3}

    def test_no_marker(self):
        assert QaDocOrchestrator._extract_documented_prs("no marker here") == set()

    def test_none(self):
        assert QaDocOrchestrator._extract_documented_prs(None) == set()

    def test_empty(self):
        assert QaDocOrchestrator._extract_documented_prs("") == set()

    def test_single_pr(self):
        doc = "<!-- codecrow-qa-autodoc:prs=42 -->"
        result = QaDocOrchestrator._extract_documented_prs(doc)
        assert result == {42}


# ── QaDocOrchestrator._extract_text ──────────────────────────────

class TestExtractText:
    def test_string_content(self):
        response = MagicMock(content="hello world")
        assert QaDocOrchestrator._extract_text(response) == "hello world"

    def test_list_content_strings(self):
        response = MagicMock(content=["part1", "part2"])
        result = QaDocOrchestrator._extract_text(response)
        assert "part1" in result
        assert "part2" in result

    def test_list_content_dicts(self):
        response = MagicMock(content=[{"text": "block1"}, {"text": "block2"}])
        result = QaDocOrchestrator._extract_text(response)
        assert "block1" in result
        assert "block2" in result

    def test_plain_string(self):
        assert QaDocOrchestrator._extract_text("direct string") == "direct string"

    def test_no_content_attr(self):
        # Object without .content
        result = QaDocOrchestrator._extract_text(42)
        assert result == "42"


# ── QaDocOrchestrator._parse_json_from_response ─────────────────

class TestParseJsonFromResponse:
    def test_direct_json(self):
        result = QaDocOrchestrator._parse_json_from_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_code_fence(self):
        text = '```json\n{"key": "value"}\n```'
        result = QaDocOrchestrator._parse_json_from_response(text)
        assert result == {"key": "value"}

    def test_trailing_comma(self):
        text = '{"a": 1, "b": 2, }'
        result = QaDocOrchestrator._parse_json_from_response(text)
        assert result == {"a": 1, "b": 2}

    def test_json_embedded_in_text(self):
        text = 'Here is the result: {"answer": "yes"} end.'
        result = QaDocOrchestrator._parse_json_from_response(text)
        assert result["answer"] == "yes"

    def test_empty(self):
        assert QaDocOrchestrator._parse_json_from_response("") is None
        assert QaDocOrchestrator._parse_json_from_response(None) is None

    def test_no_json(self):
        assert QaDocOrchestrator._parse_json_from_response("no json here") is None

    def test_trailing_comma_in_list(self):
        text = '{"items": [1, 2, 3, ]}'
        result = QaDocOrchestrator._parse_json_from_response(text)
        assert result is not None
        assert result["items"] == [1, 2, 3]


# ── QaDocOrchestrator._truncate ──────────────────────────────────

class TestTruncate:
    def test_short_text(self):
        assert QaDocOrchestrator._truncate("hello", 10) == "hello"

    def test_exact_length(self):
        assert QaDocOrchestrator._truncate("12345", 5) == "12345"

    def test_truncated(self):
        result = QaDocOrchestrator._truncate("1234567890", 5)
        assert len(result) <= 7  # 5 + ellipsis char
        assert result.endswith("…")

    def test_empty(self):
        assert QaDocOrchestrator._truncate("", 10) == ""

    def test_none(self):
        assert QaDocOrchestrator._truncate(None, 10) == ""
