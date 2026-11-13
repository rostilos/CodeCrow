from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from magento2_benchmark.corpus import attach_corpus_digest, validate_corpus
from magento2_benchmark.util import sha256_json, sha256_text


def redigest(corpus):
    return attach_corpus_digest(corpus)


def test_validates_exactly_fifty_cases_and_all_size_bands(corpus_factory):
    corpus = corpus_factory()

    summary = validate_corpus(corpus, paper_ready=True)

    assert summary == {
        "corpusId": "magento2-core-review-50-test",
        "corpusDigest": corpus["corpusDigest"],
        "cases": 50,
        "goldenComments": 50,
        "provisionalComments": 0,
        "paperReady": True,
        "sizeBands": {"large": 10, "medium": 20, "small": 20},
        "partitionCounts": {"development": 30, "sealed": 20},
        "partitionPolicyPreserved": True,
    }


def test_partition_contract_is_strict_but_legacy_policy_is_diagnostic(
    corpus_factory,
):
    corpus = corpus_factory()
    corpus["selectionPolicy"].pop("partitionPolicy")
    corpus = redigest(corpus)

    summary = validate_corpus(corpus)

    assert summary["partitionCounts"] == {
        "development": 30,
        "sealed": 20,
    }
    assert summary["partitionPolicyPreserved"] is False
    with pytest.raises(ValueError, match="partitionPolicy"):
        validate_corpus(corpus, paper_ready=True)


def test_rejects_partition_count_or_policy_drift(corpus_factory):
    corpus = corpus_factory()
    corpus["cases"][29]["partition"] = "sealed"
    corpus = redigest(corpus)
    with pytest.raises(ValueError, match="exactly 30 development"):
        validate_corpus(corpus)

    corpus = corpus_factory()
    corpus["selectionPolicy"]["partitionPolicy"]["method"] = "random"
    corpus = redigest(corpus)
    with pytest.raises(ValueError, match="partitionPolicy"):
        validate_corpus(corpus)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda corpus: corpus["cases"].pop(),
            "exactly 50 cases",
        ),
        (
            lambda corpus: corpus["cases"][1].__setitem__(
                "caseId", corpus["cases"][0]["caseId"]
            ),
            "duplicate caseId",
        ),
        (
            lambda corpus: corpus["cases"][1]["sourcePr"].__setitem__(
                "number", corpus["cases"][0]["sourcePr"]["number"]
            ),
            "appears more than once",
        ),
        (
            lambda corpus: corpus["cases"][0]["snapshot"].__setitem__(
                "headSha", corpus["cases"][0]["snapshot"]["baseSha"]
            ),
            "empty base/head",
        ),
        (
            lambda corpus: corpus["cases"][0]["snapshot"]["changedPaths"].reverse(),
            "must be sorted",
        ),
        (
            lambda corpus: corpus["cases"][0]["goldenComments"][0].__setitem__(
                "originalCommitId", "f" * 40
            ),
            "not frozen head",
        ),
        (
            lambda corpus: corpus["cases"][0]["goldenComments"][0].__setitem__(
                "path", "not/in/the/diff.php"
            ),
            "absent from the frozen snapshot diff",
        ),
        (
            lambda corpus: corpus["cases"][0]["goldenComments"][0].__setitem__(
                "reviewer", corpus["cases"][0]["sourcePr"]["author"]
            ),
            "pull-request author",
        ),
        (
            lambda corpus: corpus["cases"][0]["goldenComments"][0].__setitem__(
                "bodySha256", sha256_text("different")
            ),
            "does not match the source body",
        ),
        (
            lambda corpus: corpus["cases"][0]["goldenComments"][0]["validity"][
                "anchorValidation"
            ].__setitem__("diffSha256", "0" * 64),
            "different diff",
        ),
    ],
)
def test_rejects_snapshot_and_identity_invariant_violations(
    corpus_factory,
    mutate,
    message,
):
    corpus = corpus_factory()
    mutate(corpus)
    corpus = redigest(corpus)

    with pytest.raises(ValueError, match=message):
        validate_corpus(corpus)


def test_digest_detects_any_post_release_change(corpus_factory):
    corpus = corpus_factory()
    corpus["cases"][0]["sourcePr"]["title"] = "tampered"

    with pytest.raises(ValueError, match="corpusDigest mismatch"):
        validate_corpus(corpus)


def test_paper_ready_gate_requires_atomic_fixed_issue_and_two_fix_signals(
    corpus_factory,
):
    corpus = corpus_factory()
    gold = corpus["cases"][0]["goldenComments"][0]
    gold["expectedIssue"]["atomic"] = False
    corpus = redigest(corpus)
    with pytest.raises(ValueError, match="atomic"):
        validate_corpus(corpus, paper_ready=True)

    corpus = corpus_factory()
    gold = corpus["cases"][0]["goldenComments"][0]
    gold["validity"]["fixedLater"] = False
    gold["validity"]["fixCommitSha"] = None
    corpus = redigest(corpus)
    with pytest.raises(ValueError, match="no verified later fix"):
        validate_corpus(corpus, paper_ready=True)

    corpus = corpus_factory()
    corpus["cases"][0]["goldenComments"][0]["validity"]["fixEvidence"] = [
        {"kind": "code_change", "detail": "Changed"}
    ]
    corpus = redigest(corpus)
    with pytest.raises(ValueError, match="plus another fix signal"):
        validate_corpus(corpus, paper_ready=True)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda adjudication: adjudication.pop("annotators"),
            "two .*annotators",
        ),
        (
            lambda adjudication: adjudication.__setitem__(
                "threadComplete", False
            ),
            "complete review-thread",
        ),
        (
            lambda adjudication: adjudication.__setitem__(
                "threadDisposition", "unresolved"
            ),
            "disposed as fixed",
        ),
    ],
)
def test_paper_ready_gate_requires_retained_thread_adjudication_provenance(
    corpus_factory,
    mutate,
    message,
):
    corpus = corpus_factory()
    adjudication = corpus["cases"][0]["goldenComments"][0]["adjudication"]
    mutate(adjudication)
    corpus = redigest(corpus)

    with pytest.raises(ValueError, match=message):
        validate_corpus(corpus, paper_ready=True)


def test_provisional_corpus_is_valid_for_development_but_not_paper_ready(
    corpus_factory,
):
    corpus = corpus_factory(paper_ready=False)

    summary = validate_corpus(corpus)
    assert summary["paperReady"] is False
    assert summary["provisionalComments"] == 50

    with pytest.raises(ValueError, match="no verified later fix|provisional"):
        validate_corpus(corpus, paper_ready=True)


def test_summary_does_not_call_accepted_but_unfixed_data_paper_ready(
    corpus_factory,
):
    corpus = corpus_factory()
    gold = corpus["cases"][0]["goldenComments"][0]
    gold["validity"].update(
        {
            "fixedLater": False,
            "fixCommitSha": None,
            "disposition": "unresolved",
            "fixEvidence": [],
        }
    )
    corpus = redigest(corpus)

    summary = validate_corpus(corpus)

    assert summary["paperReady"] is False


@pytest.mark.parametrize(
    ("mutate_raw", "digest_field", "message"),
    [
        (
            lambda corpus: corpus["cases"][0]["sourcePr"][
                "sourceApiResponse"
            ].__setitem__("title", "A different API title"),
            ("sourcePr",),
            "pull-request API response",
        ),
        (
            lambda corpus: corpus["cases"][0]["goldenComments"][0][
                "sourceApiResponse"
            ].__setitem__("path", "app/code/Other.php"),
            ("goldenComments", "sourceApiResponse"),
            "review-comment API response",
        ),
        (
            lambda corpus: corpus["cases"][0]["goldenComments"][0][
                "sourceReviewResponse"
            ].__setitem__("state", "CHANGES_REQUESTED"),
            ("goldenComments", "sourceReviewResponse"),
            "submitted-review API response",
        ),
    ],
)
def test_strict_validation_cross_reconciles_archived_github_objects(
    corpus_factory,
    mutate_raw,
    digest_field,
    message,
):
    corpus = corpus_factory()
    mutate_raw(corpus)
    if digest_field == ("sourcePr",):
        source = corpus["cases"][0]["sourcePr"]
        source["sourceApiResponseSha256"] = sha256_json(
            source["sourceApiResponse"]
        )
    else:
        gold = corpus["cases"][0]["goldenComments"][0]
        response_field = digest_field[1]
        digest_name = (
            "sourceApiResponseSha256"
            if response_field == "sourceApiResponse"
            else "sourceReviewResponseSha256"
        )
        gold[digest_name] = sha256_json(gold[response_field])
    corpus = redigest(corpus)

    validate_corpus(corpus)
    with pytest.raises(ValueError, match=message):
        validate_corpus(corpus, paper_ready=True)


def test_strict_validation_rejects_reusable_self_hashed_annotator_record(
    corpus_factory,
):
    corpus = corpus_factory()
    first_record = copy.deepcopy(
        corpus["cases"][0]["goldenComments"][0]["adjudication"]["records"][0]
    )
    corpus["cases"][1]["goldenComments"][0]["adjudication"]["records"][0] = (
        first_record
    )
    corpus = redigest(corpus)

    with pytest.raises(ValueError, match="evidence binding mismatch"):
        validate_corpus(corpus, paper_ready=True)


def test_strict_validation_rejects_rehashed_record_with_foreign_binding(
    corpus_factory,
):
    corpus = corpus_factory()
    record = corpus["cases"][0]["goldenComments"][0]["adjudication"][
        "records"
    ][0]
    record["caseId"] = "m2b-050"
    record.pop("recordDigest")
    record["recordDigest"] = sha256_json(record)
    corpus = redigest(corpus)

    with pytest.raises(ValueError, match="evidence binding mismatch"):
        validate_corpus(corpus, paper_ready=True)


def test_strict_validation_cross_checks_digest_bound_ancestry_identity(
    corpus_factory,
):
    corpus = corpus_factory()
    evidence = corpus["cases"][0]["ancestryEvidence"]
    evidence["reviewedHeadSha"] = corpus["cases"][1]["snapshot"]["headSha"]
    evidence.pop("evidenceDigest")
    evidence["evidenceDigest"] = sha256_json(evidence)
    corpus = redigest(corpus)

    validate_corpus(corpus)
    with pytest.raises(ValueError, match="ancestryEvidence identity drift"):
        validate_corpus(corpus, paper_ready=True)


def test_strict_validation_requires_source_archive_case_object_bindings(
    corpus_factory,
):
    corpus = corpus_factory()
    evidence = corpus["cases"][0]["sourceArchiveEvidence"]
    evidence["selectedCommentResponseSha256"]["90001"] = "0" * 64
    corpus = redigest(corpus)

    validate_corpus(corpus)
    with pytest.raises(ValueError, match="does not bind comment"):
        validate_corpus(corpus, paper_ready=True)


def test_strict_validation_rejects_pending_source_review(corpus_factory):
    corpus = corpus_factory()
    case = corpus["cases"][0]
    gold = case["goldenComments"][0]
    review = gold["sourceReviewResponse"]
    review["state"] = "PENDING"
    gold["reviewState"] = "PENDING"
    gold["sourceReviewResponseSha256"] = sha256_json(review)
    case["sourceArchiveEvidence"]["submittedReviewResponseSha256"][
        str(gold["reviewId"])
    ] = gold["sourceReviewResponseSha256"]
    corpus = redigest(corpus)

    validate_corpus(corpus)
    with pytest.raises(ValueError, match="not a submitted human review"):
        validate_corpus(corpus, paper_ready=True)


def test_strict_validation_requires_ancestry_evidence_but_provisional_does_not(
    corpus_factory,
):
    corpus = corpus_factory()
    corpus["cases"][0].pop("ancestryEvidence")
    corpus = redigest(corpus)

    validate_corpus(corpus)
    with pytest.raises(ValueError, match="no Git ancestry evidence"):
        validate_corpus(corpus, paper_ready=True)


def test_released_schema_exposes_source_record_and_ancestry_bindings():
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "schemas"
            / "released-corpus.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert "ancestryEvidence" in schema["$defs"]["case"]["properties"]
    assert "sourceArchiveEvidence" in schema["$defs"]["case"]["properties"]
    assert "graphqlThreadArchive" in schema["$defs"]["case"]["properties"]
    assert (
        "reviewThreadEvidence"
        in schema["$defs"]["goldenComment"]["properties"]
    )
    assert (
        "threadEvidenceDigest"
        in schema["$defs"]["provenance"]["properties"]
    )
    assert {
        "graphqlThreadArchive",
        "graphqlThreadPage",
        "graphqlThreadRequest",
        "reviewThreadEvidence",
        "restReviewThreadEvidence",
        "normalizedReviewThread",
    }.issubset(schema["$defs"])
    assert "pathTransition" in schema["$defs"]["validity"]["properties"]
    assert {
        "sourceApiResponse",
        "sourceApiResponseSha256",
    }.issubset(schema["$defs"]["sourcePr"]["properties"])
    assert {
        "caseId",
        "sourceCommentId",
        "sourceBodySha256",
        "decisionDigest",
        "sourceArchiveDigest",
        "threadEvidenceDigest",
        "threadDigest",
        "curationPacketDigest",
    }.issubset(schema["$defs"]["annotatorRecord"]["properties"])
