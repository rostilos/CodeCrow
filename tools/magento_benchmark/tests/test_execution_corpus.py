from __future__ import annotations

import copy
import json

import pytest

from magento2_benchmark.execution_corpus import (
    CASE_FIELDS,
    EXECUTION_CORPUS_KIND,
    assert_label_free_execution_value,
    build_execution_corpus,
    known_label_values,
    validate_execution_corpus,
)
from magento2_benchmark.util import sha256_json


def test_strict_projection_is_exact_digest_bound_and_label_free(corpus_factory):
    corpus = corpus_factory()
    gold = corpus["cases"][0]["goldenComments"][0]

    execution = build_execution_corpus(corpus)
    summary = validate_execution_corpus(execution)
    serialized = json.dumps(execution, sort_keys=True)

    assert execution["kind"] == EXECUTION_CORPUS_KIND
    assert execution["corpusDigest"] == corpus["corpusDigest"]
    assert execution["executionCorpusDigest"] == sha256_json(
        {
            key: value
            for key, value in execution.items()
            if key != "executionCorpusDigest"
        }
    )
    assert summary["cases"] == 50
    assert summary["partitionCounts"] == {"development": 30, "sealed": 20}
    assert set(execution["cases"][0]) == CASE_FIELDS
    assert "goldenComments" not in serialized
    assert gold["reviewer"] not in serialized
    assert gold["body"] not in serialized
    assert str(gold["sourceCommentId"]) not in serialized


def test_projection_requires_paper_ready_release(corpus_factory):
    with pytest.raises(ValueError):
        build_execution_corpus(corpus_factory(paper_ready=False))


@pytest.mark.parametrize(
    "key",
    [
        "golden_comments",
        "Reviewer",
        "expected-issue",
        "adjudication",
        "validity",
        "disposition",
        "fixBindingsDigest",
        "decision_binding_digest",
    ],
)
def test_recursive_guard_rejects_label_keys_at_any_depth(key):
    with pytest.raises(ValueError, match="forbidden label key"):
        assert_label_free_execution_value(
            {"safe": [{"nested": {key: "renamed leak"}}]},
        )


def test_recursive_guard_rejects_renamed_known_label_values(corpus_factory):
    corpus = corpus_factory()
    reviewer = corpus["cases"][0]["goldenComments"][0]["reviewer"]
    forbidden = known_label_values(corpus)
    assert reviewer in forbidden

    with pytest.raises(ValueError, match="known label value"):
        assert_label_free_execution_value(
            {"apparentlyPublic": reviewer},
            forbidden_values=forbidden,
        )


def test_execution_validator_rejects_resealed_extra_or_changed_fields(
    corpus_factory,
):
    execution = build_execution_corpus(corpus_factory())
    hostile = copy.deepcopy(execution)
    hostile["cases"][0]["reviewEvidenceAlias"] = "hidden"
    hostile["executionCorpusDigest"] = sha256_json(
        {
            key: value
            for key, value in hostile.items()
            if key != "executionCorpusDigest"
        }
    )

    with pytest.raises(ValueError, match="fields are invalid"):
        validate_execution_corpus(hostile)
