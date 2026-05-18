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

    def test_string_not_found(self):
        va_module._FILE_CONTENTS_CACHE["a.py"] = "def bar():\n    pass\n"
        result = search_file_content(file_path="a.py", search_string="foo")
        assert "Not Found" in result


class TestVerificationResult:
    def test_empty(self):
        r = VerificationResult(issue_ids_to_drop=[])
        assert r.issue_ids_to_drop == []

    def test_with_ids(self):
        r = VerificationResult(issue_ids_to_drop=["ISS-1", "ISS-2"])
        assert len(r.issue_ids_to_drop) == 2
