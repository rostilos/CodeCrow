"""Focused compatibility tests for Stage 1 and Stage 2 LLM output models."""

import pytest
from pydantic import ValidationError

from model.multi_stage import (
    CrossFileAnalysisResult,
    CrossFileIssue,
    FileReviewBatchOutput,
    FileReviewOutput,
)
from model.output_schemas import CodeReviewIssue


def _file_review(**overrides):
    values = {
        "file": "src/example.py",
        "analysis_summary": "No defect found",
        "confidence": "HIGH",
    }
    values.update(overrides)
    return FileReviewOutput.model_validate(values)


def _code_review_issue(**overrides):
    values = {
        "severity": "LOW",
        "category": "BUG_RISK",
        "file": "src/example.py",
        "line": 7,
        "reason": "A concrete defect remains.",
        "suggestedFixDescription": "Correct the defect.",
    }
    values.update(overrides)
    return CodeReviewIssue.model_validate(values)


def _cross_file_issue(**overrides):
    values = {
        "id": "CROSS_001",
        "severity": "MEDIUM",
        "category": "BUG_RISK",
        "title": "Mismatched shared contract",
        "affected_files": ["src/example.py", "src/consumer.py"],
        "description": "The changed files use incompatible values.",
        "evidence": "The producer and consumer visibly disagree.",
        "business_impact": "The request fails at runtime.",
        "suggestion": "Use the same value in both files.",
    }
    values.update(overrides)
    return CrossFileIssue.model_validate(values)


def test_stage_1_explicit_null_defaults_match_omitted_defaults():
    omitted = _file_review()
    explicit_null = _file_review(issues=None, note=None)

    assert explicit_null.model_dump() == omitted.model_dump()
    assert explicit_null.issues == []
    assert explicit_null.note == ""


def test_stage_1_missing_and_null_confidence_use_neutral_default():
    base = {
        "file": "src/example.py",
        "analysis_summary": "No defect found",
    }

    omitted = FileReviewOutput.model_validate(base)
    explicit_null = FileReviewOutput.model_validate({**base, "confidence": None})

    assert omitted.confidence == "MEDIUM"
    assert explicit_null.confidence == "MEDIUM"


def test_stage_1_nested_issue_explicit_null_defaults_match_omission():
    omitted = _code_review_issue()
    explicit_null = _code_review_issue(
        isResolved=None,
        evidenceRefs=None,
        claimKind=None,
        relatedLocations=None,
    )

    assert explicit_null.model_dump() == omitted.model_dump()
    assert explicit_null.isResolved is False
    assert explicit_null.evidenceRefs == []
    assert explicit_null.claimKind == ""
    assert explicit_null.relatedLocations == []


def test_stage_2_explicit_null_defaults_match_omitted_defaults():
    omitted = _cross_file_issue()
    explicit_null = _cross_file_issue(
        primary_file=None,
        evidenceRefs=None,
        claimKind=None,
        findingScope=None,
        coverageEvidenceRefs=None,
        coverageRegression=None,
    )

    assert explicit_null.model_dump() == omitted.model_dump()
    assert explicit_null.primary_file == ""
    assert explicit_null.evidenceRefs == []
    assert explicit_null.claimKind == ""
    assert explicit_null.findingScope == "CONCRETE_DEFECT"
    assert explicit_null.coverageEvidenceRefs == []
    assert explicit_null.coverageRegression is False


def test_null_normalization_does_not_make_required_result_lists_optional():
    with pytest.raises(ValidationError):
        FileReviewBatchOutput.model_validate({"reviews": None})

    with pytest.raises(ValidationError):
        CrossFileAnalysisResult.model_validate({
            "pr_risk_level": "LOW",
            "cross_file_issues": None,
            "pr_recommendation": "PASS",
            "confidence": "HIGH",
        })


def test_null_normalization_keeps_non_nullable_wire_schema_types():
    complete_file_review_schema = FileReviewOutput.model_json_schema()
    file_review_schema = complete_file_review_schema["properties"]
    code_issue_schema = CodeReviewIssue.model_json_schema()["properties"]
    cross_issue_schema = CrossFileIssue.model_json_schema()["properties"]

    assert file_review_schema["note"]["type"] == "string"
    assert file_review_schema["issues"]["type"] == "array"
    assert file_review_schema["confidence"]["type"] == "string"
    assert "confidence" not in complete_file_review_schema["required"]
    assert code_issue_schema["isResolved"]["type"] == "boolean"
    assert code_issue_schema["evidenceRefs"]["type"] == "array"
    assert code_issue_schema["claimKind"]["type"] == "string"
    assert cross_issue_schema["coverageRegression"]["type"] == "boolean"
    assert cross_issue_schema["coverageEvidenceRefs"]["type"] == "array"
