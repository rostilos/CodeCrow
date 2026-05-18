"""Extended tests for qa_doc_orchestrator: helper methods and pipeline logic."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from service.qa_documentation.qa_doc_orchestrator import QaDocOrchestrator as QaDocumentationOrchestrator


# ── _extract_documented_prs ──────────────────────────────────


class TestExtractDocumentedPrs:
    def test_no_previous_doc(self):
        assert QaDocumentationOrchestrator._extract_documented_prs(None) == set()

    def test_empty_string(self):
        assert QaDocumentationOrchestrator._extract_documented_prs("") == set()

    def test_extracts_prs(self):
        doc = "some text\n<!-- codecrow-qa-autodoc:prs=1,2,3 -->\nmore text"
        result = QaDocumentationOrchestrator._extract_documented_prs(doc)
        assert result == {1, 2, 3}

    def test_no_marker(self):
        doc = "Just documentation without markers"
        result = QaDocumentationOrchestrator._extract_documented_prs(doc)
        assert result == set()

    def test_single_pr(self):
        doc = "<!-- codecrow-qa-autodoc:prs=42 -->"
        result = QaDocumentationOrchestrator._extract_documented_prs(doc)
        assert result == {42}


# ── _extract_text ─────────────────────────────────────────────


class TestExtractText:
    def test_string_content(self):
        resp = MagicMock()
        resp.content = "Hello"
        assert QaDocumentationOrchestrator._extract_text(resp) == "Hello"

    def test_list_content_strings(self):
        resp = MagicMock()
        resp.content = ["Part 1", "Part 2"]
        result = QaDocumentationOrchestrator._extract_text(resp)
        assert "Part 1" in result
        assert "Part 2" in result

    def test_list_content_dicts(self):
        resp = MagicMock()
        resp.content = [{"text": "Block 1"}, {"text": "Block 2"}]
        result = QaDocumentationOrchestrator._extract_text(resp)
        assert "Block 1" in result
        assert "Block 2" in result

    def test_plain_string_response(self):
        assert QaDocumentationOrchestrator._extract_text("direct") == "direct"

    def test_non_standard(self):
        resp = MagicMock(spec=[])
        result = QaDocumentationOrchestrator._extract_text(resp)
        assert isinstance(result, str)


# ── _parse_json_from_response ─────────────────────────────────


class TestParseJsonFromResponse:
    def test_plain_json(self):
        text = '{"key": "value"}'
        result = QaDocumentationOrchestrator._parse_json_from_response(text)
        assert result == {"key": "value"}

    def test_markdown_fence(self):
        text = '```json\n{"key": "value"}\n```'
        result = QaDocumentationOrchestrator._parse_json_from_response(text)
        assert result == {"key": "value"}

    def test_trailing_comma(self):
        text = '{"key": "value",}'
        result = QaDocumentationOrchestrator._parse_json_from_response(text)
        assert result == {"key": "value"}

    def test_embedded_json(self):
        text = 'Here is the result: {"a": 1} and more text'
        result = QaDocumentationOrchestrator._parse_json_from_response(text)
        assert result == {"a": 1}

    def test_empty_text(self):
        assert QaDocumentationOrchestrator._parse_json_from_response("") is None

    def test_none_text(self):
        assert QaDocumentationOrchestrator._parse_json_from_response(None) is None

    def test_no_json(self):
        assert QaDocumentationOrchestrator._parse_json_from_response("just text") is None

    def test_nested_json(self):
        text = '{"outer": {"inner": "val"}}'
        result = QaDocumentationOrchestrator._parse_json_from_response(text)
        assert result["outer"]["inner"] == "val"

    def test_generic_fence(self):
        text = '```\n{"key": 1}\n```'
        result = QaDocumentationOrchestrator._parse_json_from_response(text)
        assert result == {"key": 1}


# ── _truncate ─────────────────────────────────────────────────


class TestTruncate:
    def test_short_text(self):
        assert QaDocumentationOrchestrator._truncate("hi", 10) == "hi"

    def test_exact_length(self):
        assert QaDocumentationOrchestrator._truncate("hello", 5) == "hello"

    def test_long_text(self):
        result = QaDocumentationOrchestrator._truncate("hello world", 5)
        assert result == "hello…"

    def test_none_text(self):
        assert QaDocumentationOrchestrator._truncate(None, 10) == ""

    def test_empty_text(self):
        assert QaDocumentationOrchestrator._truncate("", 10) == ""


# ── _build_placeholders ──────────────────────────────────────


class TestBuildPlaceholders:
    def setup_method(self):
        self.orch = QaDocumentationOrchestrator.__new__(QaDocumentationOrchestrator)

    def test_basic(self):
        result = self.orch._build_placeholders(
            project_name="MyProject",
            pr_number=42,
            issues_found=5,
            files_analyzed=10,
            pr_metadata={"prTitle": "Fix bug", "prDescription": "desc"},
            task_context_dict={"task_key": "PROJ-123", "task_summary": "Do thing"},
            task_context_block="[task context]",
            diff="+ new line",
            source_branch="feature",
            target_branch="main",
            output_language="English",
        )
        assert result["project_name"] == "MyProject"
        assert result["pr_number"] == "42"
        assert result["task_key"] == "PROJ-123"
        assert result["output_language"] == "English"

    def test_defaults(self):
        result = self.orch._build_placeholders(
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

    def test_empty_language_defaults_english(self):
        result = self.orch._build_placeholders(
            project_name="P",
            pr_number=1,
            issues_found=0,
            files_analyzed=0,
            pr_metadata={},
            task_context_dict=None,
            task_context_block="",
            diff="",
            output_language="",
        )
        assert result["output_language"] == "English"


# ── _slim_stage_results ───────────────────────────────────────


class TestSlimStageResults:
    def setup_method(self):
        self.orch = QaDocumentationOrchestrator.__new__(QaDocumentationOrchestrator)

    def test_strips_raw_analysis(self):
        results = [{"file_analyses": [{"path": "a.py"}], "raw_analysis": "big text", "batch_id": 1}]
        text = self.orch._slim_stage_results(results)
        parsed = json.loads(text)
        assert isinstance(parsed, list)
        for item in parsed:
            assert "raw_analysis" not in item
            assert "batch_id" not in item

    def test_caps_output(self):
        results = [{"data": "x" * 1000}]
        text = self.orch._slim_stage_results(results, max_chars=100)
        assert len(text) <= 200  # cap + truncation notice

    def test_strips_empty_values(self):
        results = [{"key": "val", "empty": None, "blank": "", "void": []}]
        text = self.orch._slim_stage_results(results)
        parsed = json.loads(text)
        assert "empty" not in parsed[0]
        assert "blank" not in parsed[0]
        assert "void" not in parsed[0]


# ── _is_documentation_needed ──────────────────────────────────


class TestIsDocumentationNeeded:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_yes_response(self):
        orch = QaDocumentationOrchestrator.__new__(QaDocumentationOrchestrator)
        resp = MagicMock()
        resp.content = "YES - this PR changes API"
        orch.llm = MagicMock()
        orch.llm.ainvoke = AsyncMock(return_value=resp)
        result = await orch._is_documentation_needed({"diff": "some diff", "project_name": "X", "pr_number": "1", "task_key": "K", "task_summary": "S", "source_branch": "f", "target_branch": "m", "pr_title": "T", "pr_description": "D", "issues_found": "0", "files_analyzed": "1", "analysis_summary": "S", "task_context": "", "output_language": "English"})
        assert result is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_response(self):
        orch = QaDocumentationOrchestrator.__new__(QaDocumentationOrchestrator)
        resp = MagicMock()
        resp.content = "NO - just formatting changes"
        orch.llm = MagicMock()
        orch.llm.ainvoke = AsyncMock(return_value=resp)
        result = await orch._is_documentation_needed({"diff": "x", "project_name": "X", "pr_number": "1", "task_key": "K", "task_summary": "S", "source_branch": "f", "target_branch": "m", "pr_title": "T", "pr_description": "D", "issues_found": "0", "files_analyzed": "1", "analysis_summary": "S", "task_context": "", "output_language": "English"})
        assert result is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_failure_defaults_yes(self):
        orch = QaDocumentationOrchestrator.__new__(QaDocumentationOrchestrator)
        orch.llm = MagicMock()
        orch.llm.ainvoke = AsyncMock(side_effect=Exception("fail"))
        result = await orch._is_documentation_needed({"diff": "x", "project_name": "X", "pr_number": "1", "task_key": "K", "task_summary": "S", "source_branch": "f", "target_branch": "m", "pr_title": "T", "pr_description": "D", "issues_found": "0", "files_analyzed": "1", "analysis_summary": "S", "task_context": "", "output_language": "English"})
        assert result is True
