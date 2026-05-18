"""
Unit tests for utils.response_parser — ResponseParser.
"""
import json
import pytest
from utils.response_parser import ResponseParser


# ── _normalize_diff ──────────────────────────────────────────────

class TestNormalizeDiff:

    def test_none_returns_none(self):
        assert ResponseParser._normalize_diff(None) is None

    def test_null_string_returns_none(self):
        assert ResponseParser._normalize_diff("null") is None

    def test_empty_string_returns_none(self):
        assert ResponseParser._normalize_diff("") is None

    def test_non_string_returns_none(self):
        assert ResponseParser._normalize_diff(123) is None

    def test_no_diff_markers_returns_none(self):
        assert ResponseParser._normalize_diff("just some text") is None

    def test_valid_unified_diff(self):
        diff = "--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,3 @@\n-old\n+new"
        result = ResponseParser._normalize_diff(diff)
        assert result is not None
        assert "---" in result

    def test_code_block_stripped(self):
        diff = "```diff\n--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n```"
        result = ResponseParser._normalize_diff(diff)
        assert result is not None
        assert "```" not in result

    def test_diff_with_plus_minus_lines(self):
        diff = "some context\n-removed line\n+added line"
        result = ResponseParser._normalize_diff(diff)
        assert result is not None


# ── _clean_issue ─────────────────────────────────────────────────

class TestCleanIssue:

    def test_non_dict_passthrough(self):
        assert ResponseParser._clean_issue("not a dict") == "not a dict"

    def test_normalizes_issueId_to_id(self):
        issue = {"issueId": "42", "severity": "HIGH", "category": "BUG_RISK",
                 "file": "a.py", "line": 10, "reason": "Bug"}
        result = ResponseParser._clean_issue(issue)
        assert "id" in result
        assert result["id"] == "42"
        assert "issueId" not in result

    def test_invalid_severity_defaults_medium(self):
        issue = {"severity": "CRITICAL", "file": "a.py", "category": "BUG_RISK"}
        result = ResponseParser._clean_issue(issue)
        assert result["severity"] == "MEDIUM"

    def test_invalid_category_defaults_code_quality(self):
        issue = {"category": "UNKNOWN", "file": "a.py", "severity": "HIGH"}
        result = ResponseParser._clean_issue(issue)
        assert result["category"] == "CODE_QUALITY"

    def test_category_with_space_normalized(self):
        issue = {"category": "bug risk", "severity": "HIGH", "file": "a.py"}
        result = ResponseParser._clean_issue(issue)
        assert result["category"] == "BUG_RISK"

    def test_line_none_becomes_zero(self):
        issue = {"line": None, "severity": "HIGH", "file": "a.py", "category": "BUG_RISK"}
        result = ResponseParser._clean_issue(issue)
        assert result["line"] == 0

    def test_line_string_range(self):
        issue = {"line": "42-45", "severity": "HIGH", "file": "a.py", "category": "BUG_RISK"}
        result = ResponseParser._clean_issue(issue)
        assert result["line"] == 42

    def test_line_float(self):
        issue = {"line": 3.14, "severity": "HIGH", "file": "a.py", "category": "BUG_RISK"}
        result = ResponseParser._clean_issue(issue)
        assert result["line"] == 3

    def test_line_unparseable_string(self):
        issue = {"line": "abc", "severity": "HIGH", "file": "a.py", "category": "BUG_RISK"}
        result = ResponseParser._clean_issue(issue)
        assert result["line"] == 0

    def test_isResolved_string_true(self):
        issue = {"isResolved": "true", "severity": "HIGH", "file": "a.py",
                 "category": "BUG_RISK", "id": "1", "reason": "r"}
        result = ResponseParser._clean_issue(issue)
        assert result["isResolved"] is True

    def test_isResolved_non_bool(self):
        issue = {"isResolved": 0, "severity": "HIGH", "file": "a.py", "category": "BUG_RISK"}
        result = ResponseParser._clean_issue(issue)
        assert result["isResolved"] is False

    def test_id_converted_to_string(self):
        issue = {"id": 123, "severity": "HIGH", "file": "a.py", "category": "BUG_RISK"}
        result = ResponseParser._clean_issue(issue)
        assert result["id"] == "123"

    def test_unknown_fields_removed(self):
        issue = {"severity": "HIGH", "file": "a.py", "category": "BUG_RISK",
                 "extraField": "should be removed"}
        result = ResponseParser._clean_issue(issue)
        assert "extraField" not in result

    def test_diff_normalization_in_issue(self):
        issue = {"suggestedFixDiff": "no diff markers here",
                 "severity": "HIGH", "file": "a.py", "category": "BUG_RISK"}
        result = ResponseParser._clean_issue(issue)
        assert "suggestedFixDiff" not in result

    def test_diff_with_markers_kept(self):
        issue = {"suggestedFixDiff": "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new",
                 "severity": "HIGH", "file": "a.py", "category": "BUG_RISK"}
        result = ResponseParser._clean_issue(issue)
        assert "suggestedFixDiff" in result


# ── _normalize_issues ────────────────────────────────────────────

class TestNormalizeIssues:

    def test_none_returns_empty(self):
        assert ResponseParser._normalize_issues(None) == []

    def test_list_passthrough(self):
        issues = [{"severity": "HIGH", "file": "a.py", "category": "BUG_RISK"}]
        result = ResponseParser._normalize_issues(issues)
        assert len(result) == 1

    def test_dict_numeric_keys(self):
        issues = {"0": {"severity": "HIGH", "file": "a.py", "category": "BUG_RISK"},
                  "1": {"severity": "LOW", "file": "b.py", "category": "STYLE"}}
        result = ResponseParser._normalize_issues(issues)
        assert len(result) == 2

    def test_single_issue_dict(self):
        issue = {"severity": "HIGH", "file": "a.py", "reason": "Bug", "category": "BUG_RISK"}
        result = ResponseParser._normalize_issues(issue)
        assert len(result) == 1

    def test_non_dict_items_filtered(self):
        issues = [{"severity": "HIGH", "file": "a.py", "category": "BUG_RISK"}, "not a dict"]
        result = ResponseParser._normalize_issues(issues)
        assert len(result) == 1


# ── _find_analysis_in_object ─────────────────────────────────────

class TestFindAnalysisInObject:

    def test_direct_match(self):
        obj = {"comment": "Good", "issues": []}
        result = ResponseParser._find_analysis_in_object(obj)
        assert result == obj

    def test_nested_match(self):
        obj = {"tool_response": {"comment": "Good", "issues": []}}
        result = ResponseParser._find_analysis_in_object(obj)
        assert result is not None
        assert result["comment"] == "Good"

    def test_deep_nested(self):
        obj = {"a": {"b": {"comment": "Deep", "issues": []}}}
        result = ResponseParser._find_analysis_in_object(obj)
        assert result is not None

    def test_list_nested(self):
        obj = {"results": [{"comment": "List", "issues": []}]}
        result = ResponseParser._find_analysis_in_object(obj)
        assert result is not None

    def test_stringified_json(self):
        inner = json.dumps({"comment": "Str", "issues": []})
        obj = {"data": inner}
        result = ResponseParser._find_analysis_in_object(obj)
        assert result is not None

    def test_no_match(self):
        assert ResponseParser._find_analysis_in_object({"foo": "bar"}) is None

    def test_non_dict(self):
        assert ResponseParser._find_analysis_in_object("string") is None

    def test_max_depth(self):
        obj = {"comment": "deep", "issues": []}
        for _ in range(15):
            obj = {"nested": obj}
        assert ResponseParser._find_analysis_in_object(obj) is None


# ── _find_nested_json ────────────────────────────────────────────

class TestFindNestedJson:

    def test_simple_json(self):
        result = ResponseParser._find_nested_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_text(self):
        result = ResponseParser._find_nested_json('some text {"a": 1} more text')
        assert result == {"a": 1}

    def test_no_json(self):
        assert ResponseParser._find_nested_json("no json") is None

    def test_nested_braces(self):
        result = ResponseParser._find_nested_json('{"a": {"b": 1}}')
        assert result == {"a": {"b": 1}}


# ── _fix_unescaped_newlines_in_json ──────────────────────────────

class TestFixUnescapedNewlines:

    def test_empty(self):
        assert ResponseParser._fix_unescaped_newlines_in_json("") == ""

    def test_none(self):
        assert ResponseParser._fix_unescaped_newlines_in_json(None) is None

    def test_newline_in_string(self):
        text = '{"key": "line1\nline2"}'
        result = ResponseParser._fix_unescaped_newlines_in_json(text)
        assert "\\n" in result
        assert json.loads(result)["key"] == "line1\nline2"

    def test_tab_in_string(self):
        text = '{"key": "before\tafter"}'
        result = ResponseParser._fix_unescaped_newlines_in_json(text)
        assert "\\t" in result

    def test_cr_in_string(self):
        text = '{"key": "before\rafter"}'
        result = ResponseParser._fix_unescaped_newlines_in_json(text)
        assert "\\r" in result

    def test_already_escaped(self):
        text = '{"key": "line1\\nline2"}'
        result = ResponseParser._fix_unescaped_newlines_in_json(text)
        parsed = json.loads(result)
        assert parsed["key"] == "line1\nline2"

    def test_newline_outside_string(self):
        text = '{\n  "key": "value"\n}'
        result = ResponseParser._fix_unescaped_newlines_in_json(text)
        assert json.loads(result)["key"] == "value"


# ── _remove_problematic_diffs ────────────────────────────────────

class TestRemoveProblematicDiffs:

    def test_no_diff_field(self):
        text = '{"severity": "HIGH"}'
        assert ResponseParser._remove_problematic_diffs(text) == text

    def test_empty(self):
        assert ResponseParser._remove_problematic_diffs("") == ""
        assert ResponseParser._remove_problematic_diffs(None) is None

    def test_nullifies_diff(self):
        text = '{"suggestedFixDiff": "some diff content"}'
        result = ResponseParser._remove_problematic_diffs(text)
        assert "null" in result


# ── extract_json_from_response ───────────────────────────────────

class TestExtractJsonFromResponse:

    def test_empty_response(self):
        result = ResponseParser.extract_json_from_response("")
        assert result["comment"] == "Empty response received"
        assert result["issues"] == []

    def test_valid_json(self):
        data = {"comment": "Good code", "issues": [
            {"severity": "LOW", "category": "STYLE", "file": "a.py",
             "line": 1, "reason": "Minor style issue"}
        ]}
        result = ResponseParser.extract_json_from_response(json.dumps(data))
        assert result["comment"] == "Good code"
        assert len(result["issues"]) == 1

    def test_json_in_code_block(self):
        text = '```json\n{"comment": "Block", "issues": []}\n```'
        result = ResponseParser.extract_json_from_response(text)
        assert result["comment"] == "Block"

    def test_nested_tool_output(self):
        data = {"tool_response": {"comment": "Nested", "issues": []}}
        result = ResponseParser.extract_json_from_response(json.dumps(data))
        assert result["comment"] == "Nested"

    def test_invalid_json_returns_error(self):
        result = ResponseParser.extract_json_from_response("not json at all {{{")
        assert "issues" in result
        assert "_needs_retry" in result or "Failed" in result.get("comment", "")

    def test_issues_as_object(self):
        data = {"comment": "Ok", "issues": {"0": {"severity": "HIGH",
                "file": "a.py", "category": "BUG_RISK", "line": 1, "reason": "R"}}}
        result = ResponseParser.extract_json_from_response(json.dumps(data))
        assert isinstance(result["issues"], list)
        assert len(result["issues"]) == 1

    def test_with_unescaped_newlines(self):
        text = '{"comment": "line1\nline2", "issues": []}'
        result = ResponseParser.extract_json_from_response(text)
        assert "line1" in result["comment"]

    def test_tool_output_without_analysis(self):
        data = {"tool_call_response": {"output": "some tool output"}}
        result = ResponseParser.extract_json_from_response(json.dumps(data))
        assert "_needs_retry" in result


# ── needs_retry / reset / get_last_raw ───────────────────────────

class TestRetryState:

    def test_reset_retry(self):
        ResponseParser._last_parse_needs_retry = True
        ResponseParser._last_raw_response = "test"
        ResponseParser.reset_retry_state()
        assert ResponseParser.needs_retry() is False
        assert ResponseParser.get_last_raw_response() is None


# ── get_fix_prompt ───────────────────────────────────────────────

class TestGetFixPrompt:

    def test_returns_string(self):
        prompt = ResponseParser.get_fix_prompt("raw text")
        assert isinstance(prompt, str)
        assert "raw text" in prompt
        assert "JSON" in prompt


# ── _extract_all_json_objects ────────────────────────────────────

class TestExtractAllJsonObjects:

    def test_multiple_objects(self):
        text = '{"a": 1} some text {"b": 2}'
        results = ResponseParser._extract_all_json_objects(text)
        assert len(results) == 2

    def test_no_objects(self):
        assert ResponseParser._extract_all_json_objects("no json") == []

    def test_nested_object(self):
        text = '{"a": {"b": 1}}'
        results = ResponseParser._extract_all_json_objects(text)
        assert len(results) == 1
        assert results[0]["a"]["b"] == 1


# ── create_error_response ────────────────────────────────────────

class TestCreateErrorResponse:

    def test_basic(self):
        result = ResponseParser.create_error_response("Something failed")
        assert result["status"] == "error"
        assert result["error"] is True
        assert result["issues"] == []
        assert "Something failed" in result["comment"]

    def test_with_exception(self):
        result = ResponseParser.create_error_response("Err", "ValueError: x")
        assert "ValueError" in result["error_message"]
