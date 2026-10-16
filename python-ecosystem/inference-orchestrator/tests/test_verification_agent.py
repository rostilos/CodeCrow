"""
Unit tests for service.review.orchestrator.verification_agent — 
search_file_content tool and VerificationResult model.
"""
import pytest
from service.review.orchestrator.verification_agent import (
    search_file_content,
    VerificationResult,
    _FILE_CONTENTS_CACHE,
)
import service.review.orchestrator.verification_agent as va_module


class TestSearchFileContent:
    def setup_method(self):
        va_module._FILE_CONTENTS_CACHE.clear()

    def test_file_not_in_cache(self):
        result = search_file_content(file_path="a.py", search_string="foo")
        assert "not available" in result

    def test_string_found(self):
        va_module._FILE_CONTENTS_CACHE["a.py"] = "def foo():\n    return 42\n"
        result = search_file_content(file_path="a.py", search_string="foo")
        assert "Found" in result
        assert "lines 1-2" in result
        assert "def foo():\n    return 42" in result

    def test_string_not_found(self):
        va_module._FILE_CONTENTS_CACHE["a.py"] = "def bar():\n    pass\n"
        result = search_file_content(file_path="a.py", search_string="foo")
        assert "Not Found" in result

    def test_source_window_preserves_original_line_endings(self):
        source = "def foo():\r\n    return 42\r\n"
        va_module._FILE_CONTENTS_CACHE["a.py"] = source
        assert source in search_file_content("a.py", "foo")

    def test_empty_request_cache_never_reads_shared_legacy_cache(self):
        va_module._FILE_CONTENTS_CACHE["a.py"] = "another tenant's source"
        token = va_module._ACTIVE_FILE_CONTENTS.set({})
        try:
            assert "not available" in search_file_content("a.py", "tenant")
        finally:
            va_module._ACTIVE_FILE_CONTENTS.reset(token)


class TestVerificationResult:
    def test_malformed_sibling_preserves_complete_assessment(self):
        result = va_module._parse_verification_result('''{"assessments": [
            {"issue_id": "issue_0", "verdict": "unknown", "reason": "Caller unavailable"},
            {"issue_id": "issue_1", "verdict": "invented"}]}''')
        assert [assessment.issue_id for assessment in result.assessments] == ["issue_0"]
        assert result.incomplete_reason == "invalid_assessment_response"

    def test_empty(self):
        r = VerificationResult(issue_ids_to_drop=[])
        assert r.issue_ids_to_drop == []

    def test_with_ids(self):
        r = VerificationResult(issue_ids_to_drop=["ISS-1", "ISS-2"])
        assert len(r.issue_ids_to_drop) == 2
