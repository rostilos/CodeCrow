"""
Unit tests for model.enums — IssueCategory, AnalysisMode, RelationshipType.
"""
import pytest
from model.enums import IssueCategory, AnalysisMode, RelationshipType


class TestIssueCategory:

    def test_all_values(self):
        expected = {
            "SECURITY", "PERFORMANCE", "CODE_QUALITY", "BUG_RISK",
            "STYLE", "DOCUMENTATION", "BEST_PRACTICES",
            "ERROR_HANDLING", "TESTING", "ARCHITECTURE",
        }
        assert {e.value for e in IssueCategory} == expected

    def test_is_str_enum(self):
        assert isinstance(IssueCategory.SECURITY, str)
        assert IssueCategory.SECURITY == "SECURITY"

    @pytest.mark.parametrize("name", [
        "SECURITY", "PERFORMANCE", "CODE_QUALITY", "BUG_RISK",
        "STYLE", "DOCUMENTATION", "BEST_PRACTICES",
        "ERROR_HANDLING", "TESTING", "ARCHITECTURE",
    ])
    def test_access_by_name(self, name):
        assert IssueCategory[name].value == name

    def test_invalid_raises(self):
        with pytest.raises(KeyError):
            IssueCategory["INVALID"]


class TestAnalysisMode:

    def test_all_values(self):
        assert {e.value for e in AnalysisMode} == {"FULL", "INCREMENTAL"}

    def test_is_str_enum(self):
        assert isinstance(AnalysisMode.FULL, str)

    def test_full(self):
        assert AnalysisMode.FULL == "FULL"

    def test_incremental(self):
        assert AnalysisMode.INCREMENTAL == "INCREMENTAL"


class TestRelationshipType:

    def test_all_values(self):
        expected = {"IMPORTS", "EXTENDS", "IMPLEMENTS", "CALLS", "SAME_PACKAGE", "REFERENCES"}
        assert {e.value for e in RelationshipType} == expected

    def test_is_str_enum(self):
        assert isinstance(RelationshipType.IMPORTS, str)

    @pytest.mark.parametrize("name,value", [
        ("IMPORTS", "IMPORTS"),
        ("EXTENDS", "EXTENDS"),
        ("IMPLEMENTS", "IMPLEMENTS"),
        ("CALLS", "CALLS"),
        ("SAME_PACKAGE", "SAME_PACKAGE"),
        ("REFERENCES", "REFERENCES"),
    ])
    def test_each_member(self, name, value):
        assert RelationshipType[name].value == value
