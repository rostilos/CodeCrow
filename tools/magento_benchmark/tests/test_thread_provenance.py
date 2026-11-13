from __future__ import annotations

import copy

import pytest

from magento2_benchmark.collect import (
    _validated_review_thread_evidence,
)
from magento2_benchmark.corpus import (
    attach_corpus_digest,
    validate_corpus,
)
from magento2_benchmark.curation import _thread_evidence
from magento2_benchmark.thread_provenance import (
    REVIEW_THREADS_QUERY,
    build_graphql_thread_archive,
    build_rest_review_thread_evidence,
    build_review_thread_binding,
    validate_graphql_thread_archive,
    validate_review_thread_binding,
)
from magento2_benchmark.util import sha256_json

from conftest import (
    graphql_archive_fixture,
    graphql_thread_fixture,
    make_decisions,
    make_release_evidence,
    write_json,
)


def _response(nodes, *, has_next, end_cursor):
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {
                            "hasNextPage": has_next,
                            "endCursor": end_cursor,
                        },
                        "nodes": nodes,
                    }
                }
            }
        }
    }


def test_raw_graphql_archive_validates_full_cursor_chain():
    pull_request = 12_345
    first_raw, _ = graphql_thread_fixture(
        comment_id=700_001,
        pull_request=pull_request,
        path="A.php",
        line=4,
        body="Fix A.",
        reviewer="reviewer-a",
        commit_sha="a" * 40,
    )
    second_raw, _ = graphql_thread_fixture(
        comment_id=700_002,
        pull_request=pull_request,
        path="B.php",
        line=8,
        body="Fix B.",
        reviewer="reviewer-b",
        commit_sha="b" * 40,
    )
    archive = build_graphql_thread_archive(
        pull_request=pull_request,
        pages=[
            (
                {
                    "owner": "magento",
                    "name": "magento2",
                    "number": pull_request,
                    "after": None,
                },
                _response(
                    [first_raw],
                    has_next=True,
                    end_cursor="cursor-page-1",
                ),
            ),
            (
                {
                    "owner": "magento",
                    "name": "magento2",
                    "number": pull_request,
                    "after": "cursor-page-1",
                },
                _response(
                    [second_raw],
                    has_next=False,
                    end_cursor=None,
                ),
            ),
        ],
    )

    validated, roots = validate_graphql_thread_archive(
        archive,
        pull_request=pull_request,
    )

    assert validated["pageCount"] == 2
    assert set(roots) == {700_001, 700_002}
    assert (
        validated["pages"][1]["previousPageDigest"]
        == validated["pages"][0]["pageDigest"]
    )


def test_pr_32187_thread_binds_current_and_original_multiline_coordinates():
    pull_request = 32187
    root_id = 577669406
    review_id = 592269309
    commit_id = "2bcdb1e8e2af1cb7681bc1fa5676487db5d70b23"
    original_commit_id = "dc5ecb86a22a20c0619f7cf04db0f27454358194"
    raw_thread, normalized = graphql_thread_fixture(
        comment_id=root_id,
        pull_request=pull_request,
        path="reviewed.xml",
        line=26,
        body="We don't need these on new tests.",
        reviewer="eduard13",
        commit_sha=original_commit_id,
        review_id=review_id,
        review_state="COMMENTED",
    )
    raw_thread.update(
        {
            "isOutdated": True,
            "line": None,
            "originalLine": 26,
            "startLine": None,
            "originalStartLine": 24,
            "diffSide": "RIGHT",
            "startDiffSide": "RIGHT",
        }
    )
    normalized.update(
        {
            "isOutdated": True,
            "line": None,
            "originalLine": 26,
            "startLine": None,
            "originalStartLine": 24,
            "diffSide": "RIGHT",
            "startDiffSide": "RIGHT",
            "sourceSha256": sha256_json(raw_thread),
        }
    )
    normalized["messages"][0]["authorAssociation"] = "CONTRIBUTOR"
    rest_comment = {
        "id": root_id,
        "html_url": raw_thread["comments"]["nodes"][0]["url"],
        "pull_request_review_id": review_id,
        "user": {"login": "eduard13", "type": "User"},
        "author_association": "CONTRIBUTOR",
        "body": "We don't need these on new tests.",
        "created_at": "2026-07-29T12:00:00Z",
        "updated_at": "2026-07-29T12:00:00Z",
        "path": "reviewed.xml",
        "commit_id": commit_id,
        "original_commit_id": original_commit_id,
        "line": None,
        "original_line": 26,
        "start_line": None,
        "original_start_line": 24,
        "side": "RIGHT",
        "start_side": "RIGHT",
        "subject_type": "line",
        "in_reply_to_id": None,
    }
    rest_review = {
        "id": review_id,
        "state": "COMMENTED",
        "commit_id": original_commit_id,
    }
    rest_evidence = build_rest_review_thread_evidence(
        pull_request=pull_request,
        root_comment_id=root_id,
        all_comments=[rest_comment],
        all_submitted_reviews=[rest_review],
        source_archive_digest="a" * 64,
        source_archive_case_evidence_digest="b" * 64,
    )
    archive = graphql_archive_fixture(pull_request, [raw_thread])

    binding = build_review_thread_binding(
        thread=normalized,
        archive=archive,
        thread_evidence_digest="c" * 64,
        rest_thread_evidence=rest_evidence,
    )
    assert binding["thread"]["originalStartLine"] == 24
    assert binding["thread"]["startLine"] is None

    drifted_rest = copy.deepcopy(rest_evidence)
    drifted_rest["comments"][0]["start_line"] = 24
    drifted_rest.pop("evidenceDigest")
    drifted_rest["evidenceDigest"] = sha256_json(drifted_rest)
    with pytest.raises(
        ValueError,
        match="start_line cannot exist|anchor drift",
    ):
        build_review_thread_binding(
            thread=normalized,
            archive=archive,
            thread_evidence_digest="c" * 64,
            rest_thread_evidence=drifted_rest,
        )

    for field in (
        "startLine",
        "originalStartLine",
        "startDiffSide",
    ):
        assert field in REVIEW_THREADS_QUERY


def test_raw_graphql_archive_rejects_resealed_missing_page():
    pull_request = 12_345
    raw_thread, _ = graphql_thread_fixture(
        comment_id=700_001,
        pull_request=pull_request,
        path="A.php",
        line=4,
        body="Fix A.",
        reviewer="reviewer-a",
        commit_sha="a" * 40,
    )
    archive = build_graphql_thread_archive(
        pull_request=pull_request,
        pages=[
            (
                {
                    "owner": "magento",
                    "name": "magento2",
                    "number": pull_request,
                    "after": None,
                },
                _response(
                    [raw_thread],
                    has_next=True,
                    end_cursor="cursor-page-1",
                ),
            ),
            (
                {
                    "owner": "magento",
                    "name": "magento2",
                    "number": pull_request,
                    "after": "cursor-page-1",
                },
                _response([], has_next=False, end_cursor=None),
            ),
        ],
    )
    archive["pages"] = archive["pages"][:1]
    archive["pageCount"] = 1
    archive.pop("archiveDigest")
    archive["archiveDigest"] = sha256_json(archive)

    with pytest.raises(ValueError, match="missing a declared next page"):
        validate_graphql_thread_archive(
            archive,
            pull_request=pull_request,
        )


def test_paper_release_rejects_normalized_threads_without_raw_pages(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    threads, _ = make_release_evidence(draft, decisions)
    threads["cases"][0].pop("graphqlPageArchive")
    threads["cases"][0].pop("graphqlResponseDigests")
    threads.pop("threadEvidenceDigest")
    threads["threadEvidenceDigest"] = sha256_json(threads)

    with pytest.raises(ValueError, match="no raw GraphQL page archive"):
        _thread_evidence(
            write_json(tmp_path / "threads.json", threads),
            draft=draft,
            draft_digest=sha256_json(draft),
            require_raw_graphql=True,
        )


def test_materialization_and_corpus_reject_raw_thread_tampering(
    corpus_factory,
):
    corpus = corpus_factory()
    case = corpus["cases"][0]
    gold = case["goldenComments"][0]
    binding = gold["reviewThreadEvidence"]
    rest_thread = binding["restThreadEvidence"]
    annotation = {"adjudication": gold["adjudication"]}
    source_evidence = {"reviewThreadEvidence": binding}

    assert _validated_review_thread_evidence(
        pull_request=case["sourcePr"]["number"],
        comment_id=gold["sourceCommentId"],
        source_evidence=source_evidence,
        annotation=annotation,
        archive=case["graphqlThreadArchive"],
        thread_evidence_digest=corpus["provenance"][
            "threadEvidenceDigest"
        ],
        paper_ready=True,
        source_archive_evidence=case["sourceArchiveEvidence"],
        comments_by_id={
            int(comment["id"]): comment
            for comment in rest_thread["comments"]
        },
        reviews_by_id={
            int(review["id"]): review
            for review in rest_thread["submittedReviews"]
        },
    ) == binding

    tampered = copy.deepcopy(corpus)
    tampered_case = tampered["cases"][0]
    archive = tampered_case["graphqlThreadArchive"]
    page = archive["pages"][0]
    raw_thread = (
        page["response"]["data"]["repository"]["pullRequest"][
            "reviewThreads"
        ]["nodes"][0]
    )
    raw_thread["comments"]["nodes"][0]["body"] = "fabricated raw body"
    page["responseDigest"] = sha256_json(page["response"])
    page_without_digest = dict(page)
    page_without_digest.pop("pageDigest")
    page["pageDigest"] = sha256_json(page_without_digest)
    archive_without_digest = dict(archive)
    archive_without_digest.pop("archiveDigest")
    archive["archiveDigest"] = sha256_json(archive_without_digest)
    tampered_binding = tampered_case["goldenComments"][0][
        "reviewThreadEvidence"
    ]
    tampered_binding["graphqlPageDigest"] = page["pageDigest"]
    tampered_binding["graphqlArchiveDigest"] = archive["archiveDigest"]
    tampered = attach_corpus_digest(tampered)

    with pytest.raises(ValueError, match="not bound to its raw node"):
        validate_corpus(tampered, paper_ready=True)


def test_resealed_graphql_and_normalized_reply_cannot_override_raw_rest():
    pull_request = 12_345
    root_id = 700_001
    reply_id = 700_002
    root_review_id = 800_001
    reply_review_id = 800_002
    commit_sha = "a" * 40
    raw_thread, normalized = graphql_thread_fixture(
        comment_id=root_id,
        pull_request=pull_request,
        path="A.php",
        line=4,
        body="Root issue.",
        reviewer="reviewer-a",
        commit_sha=commit_sha,
        review_id=root_review_id,
        review_state="COMMENTED",
    )
    raw_reply = {
        "databaseId": reply_id,
        "url": (
            f"https://github.com/magento/magento2/pull/{pull_request}"
            f"#discussion_r{reply_id}"
        ),
        "body": "Applied in the next commit.",
        "createdAt": "2026-07-29T12:01:00Z",
        "updatedAt": "2026-07-29T12:01:00Z",
        "author": {"login": "author-a", "__typename": "User"},
        "replyTo": {"databaseId": root_id},
        "pullRequestReview": {
            "databaseId": reply_review_id,
            "state": "COMMENTED",
            "commit": {"oid": commit_sha},
        },
    }
    raw_thread["comments"]["nodes"].append(raw_reply)
    normalized["messages"].append(
        {
            "id": reply_id,
            "url": raw_reply["url"],
            "author": "author-a",
            "authorType": "User",
            "authorAssociation": "CONTRIBUTOR",
            "body": raw_reply["body"],
            "createdAt": raw_reply["createdAt"],
            "updatedAt": raw_reply["updatedAt"],
            "commitId": commit_sha,
            "originalCommitId": commit_sha,
            "inReplyToId": root_id,
        }
    )
    normalized["sourceSha256"] = sha256_json(raw_thread)
    rest_comments = [
        {
            "id": root_id,
            "html_url": raw_thread["comments"]["nodes"][0]["url"],
            "pull_request_review_id": root_review_id,
            "user": {"login": "reviewer-a", "type": "User"},
            "author_association": "MEMBER",
            "body": "Root issue.",
            "created_at": "2026-07-29T12:00:00Z",
            "updated_at": "2026-07-29T12:00:00Z",
            "commit_id": commit_sha,
            "original_commit_id": commit_sha,
            "path": "A.php",
            "line": 4,
            "original_line": 4,
            "start_line": None,
            "original_start_line": None,
            "side": "RIGHT",
            "start_side": None,
            "subject_type": "line",
            "in_reply_to_id": None,
        },
        {
            "id": reply_id,
            "html_url": raw_reply["url"],
            "pull_request_review_id": reply_review_id,
            "user": {"login": "author-a", "type": "User"},
            "author_association": "CONTRIBUTOR",
            "body": raw_reply["body"],
            "created_at": raw_reply["createdAt"],
            "updated_at": raw_reply["updatedAt"],
            "commit_id": commit_sha,
            "original_commit_id": commit_sha,
            "in_reply_to_id": root_id,
        },
    ]
    rest_reviews = [
        {
            "id": root_review_id,
            "state": "COMMENTED",
            "commit_id": commit_sha,
        },
        {
            "id": reply_review_id,
            "state": "COMMENTED",
            "commit_id": commit_sha,
        },
    ]
    source_archive_digest = "b" * 64
    source_case_digest = "c" * 64
    rest_evidence = build_rest_review_thread_evidence(
        pull_request=pull_request,
        root_comment_id=root_id,
        all_comments=rest_comments,
        all_submitted_reviews=rest_reviews,
        source_archive_digest=source_archive_digest,
        source_archive_case_evidence_digest=source_case_digest,
    )
    archive = graphql_archive_fixture(pull_request, [raw_thread])
    binding = build_review_thread_binding(
        thread=normalized,
        archive=archive,
        thread_evidence_digest="d" * 64,
        rest_thread_evidence=rest_evidence,
    )
    validate_review_thread_binding(
        binding,
        archive=archive,
        root_comment_id=root_id,
        thread_evidence_digest="d" * 64,
        require_complete=True,
        source_archive_digest=source_archive_digest,
        source_archive_case_evidence_digest=source_case_digest,
    )

    tampered_archive = copy.deepcopy(archive)
    tampered_binding = copy.deepcopy(binding)
    page = tampered_archive["pages"][0]
    tampered_thread = (
        page["response"]["data"]["repository"]["pullRequest"][
            "reviewThreads"
        ]["nodes"][0]
    )
    tampered_thread["comments"]["nodes"][1]["body"] = "Fabricated reply."
    tampered_normalized = tampered_binding["thread"]
    tampered_normalized["messages"][1]["body"] = "Fabricated reply."
    tampered_normalized["sourceSha256"] = sha256_json(tampered_thread)
    tampered_binding["threadDigest"] = sha256_json(tampered_normalized)
    page["responseDigest"] = sha256_json(page["response"])
    page_value = dict(page)
    page_value.pop("pageDigest")
    page["pageDigest"] = sha256_json(page_value)
    archive_value = dict(tampered_archive)
    archive_value.pop("archiveDigest")
    tampered_archive["archiveDigest"] = sha256_json(archive_value)
    tampered_binding["graphqlPageDigest"] = page["pageDigest"]
    tampered_binding["graphqlArchiveDigest"] = tampered_archive[
        "archiveDigest"
    ]

    with pytest.raises(
        ValueError,
        match="raw GraphQL and REST message .* body drift",
    ):
        validate_review_thread_binding(
            tampered_binding,
            archive=tampered_archive,
            root_comment_id=root_id,
            thread_evidence_digest="d" * 64,
            require_complete=True,
            source_archive_digest=source_archive_digest,
            source_archive_case_evidence_digest=source_case_digest,
        )


def test_materialization_rejects_rest_reply_added_after_release(
    corpus_factory,
):
    corpus = corpus_factory()
    case = corpus["cases"][0]
    gold = case["goldenComments"][0]
    binding = gold["reviewThreadEvidence"]
    rest_thread = binding["restThreadEvidence"]
    comments_by_id = {
        int(comment["id"]): comment
        for comment in rest_thread["comments"]
    }
    comments_by_id[999_999] = {
        "id": 999_999,
        "in_reply_to_id": gold["sourceCommentId"],
    }

    with pytest.raises(ValueError, match="REST thread coverage drifted"):
        _validated_review_thread_evidence(
            pull_request=case["sourcePr"]["number"],
            comment_id=gold["sourceCommentId"],
            source_evidence={"reviewThreadEvidence": binding},
            annotation={"adjudication": gold["adjudication"]},
            archive=case["graphqlThreadArchive"],
            thread_evidence_digest=corpus["provenance"][
                "threadEvidenceDigest"
            ],
            paper_ready=True,
            source_archive_evidence=case["sourceArchiveEvidence"],
            comments_by_id=comments_by_id,
            reviews_by_id={
                int(review["id"]): review
                for review in rest_thread["submittedReviews"]
            },
        )
