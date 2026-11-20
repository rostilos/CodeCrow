from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from magento2_benchmark.corpus import (
    attach_corpus_digest,
    decision_binding_digest,
)
from magento2_benchmark.curation import DECISIONS_KIND, DRAFT_STATUS
from magento2_benchmark.thread_provenance import (
    build_graphql_thread_archive,
    build_rest_review_thread_evidence,
    build_review_thread_binding,
)
from magento2_benchmark.util import sha256_json, sha256_text


TIMESTAMP = "2026-07-29T12:00:00Z"


def graphql_thread_fixture(
    *,
    comment_id: int,
    pull_request: int,
    path: str,
    line: int,
    body: str,
    reviewer: str,
    commit_sha: str,
    review_id: int | None = None,
    review_state: str = "APPROVED",
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_message = {
        "databaseId": comment_id,
        "url": (
            f"https://github.com/magento/magento2/pull/{pull_request}"
            f"#discussion_r{comment_id}"
        ),
        "body": body,
        "createdAt": TIMESTAMP,
        "updatedAt": TIMESTAMP,
        "author": {"login": reviewer, "__typename": "User"},
        "replyTo": None,
        "pullRequestReview": {
            "databaseId": (
                review_id if review_id is not None else 80_000 + comment_id
            ),
            "state": review_state,
            "commit": {"oid": commit_sha},
        },
    }
    raw_thread = {
        "id": f"PRRT_fixture_{comment_id}",
        "isResolved": True,
        "isOutdated": False,
        "path": path,
        "line": line,
        "originalLine": line,
        "startLine": None,
        "originalStartLine": None,
        "diffSide": "RIGHT",
        "startDiffSide": None,
        "comments": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [raw_message],
        },
    }
    normalized = {
        "rootCommentId": comment_id,
        "messages": [
            {
                "id": comment_id,
                "url": raw_message["url"],
                "author": reviewer,
                "authorType": "User",
                "authorAssociation": "MEMBER",
                "body": body,
                "createdAt": TIMESTAMP,
                "updatedAt": TIMESTAMP,
                "commitId": commit_sha,
                "originalCommitId": commit_sha,
                "inReplyToId": None,
            }
        ],
        "complete": True,
        "messageIdsReconciledWithRest": True,
        "resolutionMetadataAvailable": True,
        "isResolved": True,
        "isOutdated": False,
        "path": path,
        "line": line,
        "originalLine": line,
        "startLine": None,
        "originalStartLine": None,
        "diffSide": "RIGHT",
        "startDiffSide": None,
        "sourceSha256": sha256_json(raw_thread),
    }
    return raw_thread, normalized


def graphql_archive_fixture(
    pull_request: int,
    raw_threads: list[dict[str, Any]],
) -> dict[str, Any]:
    response = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {
                            "hasNextPage": False,
                            "endCursor": None,
                        },
                        "nodes": raw_threads,
                    }
                }
            }
        }
    }
    return build_graphql_thread_archive(
        pull_request=pull_request,
        pages=[
            (
                {
                    "owner": "magento",
                    "name": "magento2",
                    "number": pull_request,
                    "after": None,
                },
                response,
            )
        ],
    )


def full_sha(label: str) -> str:
    return hashlib.sha1(label.encode("utf-8")).hexdigest()


def _band_and_count(index: int) -> tuple[str, int]:
    if index <= 20:
        return "small", 3
    if index <= 40:
        return "medium", 11
    return "large", 31


def make_corpus(*, paper_ready: bool = True) -> dict[str, Any]:
    cases = []
    mainline_cutoff_sha = full_sha("mainline-cutoff")
    source_archive_digest = "e" * 64
    thread_evidence_digest = "a" * 64
    for index in range(1, 51):
        case_id = f"m2b-{index:03d}"
        pr_number = 10_000 + index
        size_band, file_count = _band_and_count(index)
        base_sha = full_sha(f"base-{index}")
        head_sha = full_sha(f"head-{index}")
        final_head_sha = full_sha(f"final-{index}")
        merge_sha = full_sha(f"merge-{index}")
        merge_first_parent = full_sha(f"mainline-parent-{index}")
        diff_sha = sha256_text(f"diff-{index}")
        paths = sorted(
            f"app/code/Fixture{index:03d}/File{path_index:02d}.php"
            for path_index in range(1, file_count + 1)
        )
        body = f"Please fix benchmark defect {index}."
        source_api_response = {
            "id": 90_000 + index,
            "html_url": (
                "https://github.com/magento/magento2/pull/"
                f"{pr_number}#discussion_r{90_000 + index}"
            ),
            "pull_request_review_id": 80_000 + index,
            "user": {"login": f"reviewer-{index}", "type": "User"},
            "author_association": "MEMBER",
            "created_at": TIMESTAMP,
            "path": paths[0],
            "side": "RIGHT",
            "line": 4,
            "original_line": 4,
            "start_line": None,
            "original_start_line": None,
            "start_side": None,
            "subject_type": "line",
            "commit_id": head_sha,
            "original_commit_id": head_sha,
            "body": body,
            "updated_at": TIMESTAMP,
            "diff_hunk": "@@ -1,3 +1,4 @@\n+bad_call();",
            "in_reply_to_id": None,
            "pull_request_url": (
                "https://api.github.com/repos/magento/magento2/pulls/"
                f"{pr_number}"
            ),
        }
        source_review_response = {
            "id": 80_000 + index,
            "user": {"login": f"reviewer-{index}", "type": "User"},
            "state": "APPROVED",
            "submitted_at": TIMESTAMP,
            "commit_id": head_sha,
            "pull_request_url": (
                "https://api.github.com/repos/magento/magento2/pulls/"
                f"{pr_number}"
            ),
        }
        raw_graphql_thread, normalized_graphql_thread = (
            graphql_thread_fixture(
                comment_id=90_000 + index,
                pull_request=pr_number,
                path=paths[0],
                line=4,
                body=body,
                reviewer=f"reviewer-{index}",
                commit_sha=head_sha,
                review_id=80_000 + index,
            )
        )
        graphql_archive = graphql_archive_fixture(
            pr_number,
            [raw_graphql_thread],
        )
        source_archive_case_digest = sha256_text(
            f"source-archive-case-{index}"
        )
        rest_thread_evidence = build_rest_review_thread_evidence(
            pull_request=pr_number,
            root_comment_id=90_000 + index,
            all_comments=[source_api_response],
            all_submitted_reviews=[source_review_response],
            source_archive_digest=source_archive_digest,
            source_archive_case_evidence_digest=(
                source_archive_case_digest
            ),
        )
        adjudication_status = "accepted" if paper_ready else "provisional"
        fixed_later = True if paper_ready else None
        fix_sha = full_sha(f"fix-{index}") if paper_ready else None
        transition_diff_sha = sha256_text(f"path-transition-{index}")
        path_transition = (
            {
                "status": "modified",
                "sourcePath": paths[0],
                "finalPath": paths[0],
                "renameSimilarity": None,
                "checkpointBlobOid": full_sha(
                    f"checkpoint-blob-{index}"
                ),
                "finalBlobOid": full_sha(f"final-blob-{index}"),
                "diffSha256": transition_diff_sha,
            }
            if paper_ready
            else None
        )
        fix_evidence = (
            [
                {
                    "kind": "code_change",
                    "detail": "The faulty branch changed.",
                    "artifactDigest": transition_diff_sha,
                },
                {"kind": "review_resolution", "detail": "The thread was resolved."},
            ]
            if paper_ready
            else []
        )
        expected_issue = {
            "summary": f"Fixture defect {index}",
            "rootCause": "The new branch calls the wrong collaborator.",
            "failureMode": "The request returns an incorrect result.",
            "requiredChange": "Call the correct collaborator.",
            "category": "bug",
            "severity": "medium",
            "actionable": True,
            "atomic": True,
        }
        adjudication = {
            "status": adjudication_status,
            "adjudicator": "fixture-curator",
            "annotators": (
                ["fixture-curator-a", "fixture-curator-b"]
                if paper_ready
                else ["fixture-curator-a"]
            ),
            "threadComplete": paper_ready,
            "threadDisposition": "fixed" if paper_ready else "unresolved",
            "records": [],
            "threadEvidenceDigest": (
                thread_evidence_digest if paper_ready else None
            ),
            "threadDigest": (
                sha256_json(normalized_graphql_thread)
                if paper_ready
                else None
            ),
            "curationPacketDigest": "c" * 64 if paper_ready else None,
            "at": TIMESTAMP,
            "notes": "",
        }
        adjudication["decisionDigest"] = decision_binding_digest(
            expected_issue=expected_issue,
            fix_commit_sha=fix_sha,
            fix_evidence=fix_evidence,
            adjudication=adjudication,
        )
        if paper_ready:
            for annotator in ("fixture-curator-a", "fixture-curator-b"):
                record = {
                    "annotator": annotator,
                    "verdict": "accept",
                    "at": TIMESTAMP,
                    "caseId": case_id,
                    "sourceCommentId": 90_000 + index,
                    "sourceBodySha256": sha256_text(body),
                    "decisionDigest": adjudication["decisionDigest"],
                    "sourceArchiveDigest": source_archive_digest,
                    "threadEvidenceDigest": adjudication[
                        "threadEvidenceDigest"
                    ],
                    "threadDigest": adjudication["threadDigest"],
                    "curationPacketDigest": adjudication[
                        "curationPacketDigest"
                    ],
                }
                record["recordDigest"] = sha256_json(record)
                adjudication["records"].append(record)
        golden = {
            "id": f"{case_id}-comment-{90_000 + index}",
            "sourceCommentId": 90_000 + index,
            "sourceUrl": (
                "https://github.com/magento/magento2/pull/"
                f"{pr_number}#discussion_r{90_000 + index}"
            ),
            "reviewId": 80_000 + index,
            "reviewer": f"reviewer-{index}",
            "reviewerType": "User",
            "reviewerAssociation": "MEMBER",
            "createdAt": TIMESTAMP,
            "path": paths[0],
            "side": "RIGHT",
            "originalLine": 4,
            "sourceCurrentCommitId": head_sha,
            "sourceCurrentLine": 4,
            "sourceOriginalLine": 4,
            "sourceCurrentStartLine": None,
            "sourceOriginalStartLine": None,
            "sourceStartSide": None,
            "originalLineResolution": None,
            "originalCommitId": head_sha,
            "body": body,
            "bodySha256": sha256_text(body),
            "diffHunk": "@@ -1,3 +1,4 @@\n+bad_call();",
            "threadRoot": True,
            "sourceUpdatedAt": TIMESTAMP,
            "sourceApiResponse": source_api_response,
            "sourceApiResponseSha256": sha256_json(source_api_response),
            "sourceReviewResponse": source_review_response,
            "sourceReviewResponseSha256": sha256_json(
                source_review_response
            ),
            **(
                {
                    "reviewThreadEvidence": build_review_thread_binding(
                        thread=normalized_graphql_thread,
                        archive=graphql_archive,
                        thread_evidence_digest=thread_evidence_digest,
                        rest_thread_evidence=rest_thread_evidence,
                    )
                }
                if paper_ready
                else {}
            ),
            "expectedIssue": expected_issue,
            "validity": {
                "status": "present_at_snapshot",
                "snapshotSha": head_sha,
                "method": "exact_original_commit_and_diff_anchor",
                "anchorValidation": {
                    "status": "exact",
                    "path": paths[0],
                    "line": 4,
                    "diffSha256": diff_sha,
                },
                "fixedLater": fixed_later,
                "fixCommitSha": fix_sha,
                "disposition": "fixed" if paper_ready else "unresolved",
                "fixEvidence": fix_evidence,
                **(
                    {"pathTransition": path_transition}
                    if path_transition is not None
                    else {}
                ),
            },
            "adjudication": adjudication,
            "reviewState": "APPROVED",
            "reviewSubmittedAt": TIMESTAMP,
        }
        pull_url = f"https://github.com/magento/magento2/pull/{pr_number}"
        source_pull_response = {
            "number": pr_number,
            "html_url": pull_url,
            "title": f"Fixture PR {index}",
            "user": {"login": f"author-{index}", "type": "User"},
            "base": {"ref": "2.4-develop"},
            "merged_at": TIMESTAMP,
            "head": {"sha": final_head_sha},
            "merge_commit_sha": merge_sha,
            "changed_files": file_count,
            "state": "closed",
        }
        ancestry_evidence = {
            "schema": "codecrow.magento2-review-ancestry",
            "baseSha": base_sha,
            "reviewedHeadSha": head_sha,
            "finalHeadSha": final_head_sha,
            "mergeCommitSha": merge_sha,
            "mergeParents": [merge_first_parent, final_head_sha],
            "mergeFirstParentSha": merge_first_parent,
            "mergeSecondParentSha": final_head_sha,
            "mainlineCutoffSha": mainline_cutoff_sha,
            "reviewedMainlineMergeBaseSha": base_sha,
            "checks": {
                "baseAncestorReviewedHead": True,
                "reviewedHeadAncestorFinalHead": True,
                "finalHeadIsMergeSecondParent": True,
                "baseIsReviewedMainlineMergeBase": True,
                "mergeCommitAncestorMainlineCutoff": True,
            },
        }
        ancestry_evidence["evidenceDigest"] = sha256_json(
            ancestry_evidence
        )
        cases.append(
            {
                "caseId": case_id,
                "partition": "development" if index <= 30 else "sealed",
                "sizeBand": size_band,
                "sourcePr": {
                    "number": pr_number,
                    "url": pull_url,
                    "title": f"Fixture PR {index}",
                    "author": f"author-{index}",
                    "baseRef": "2.4-develop",
                    "mergedAt": TIMESTAMP,
                    "finalHeadSha": final_head_sha,
                    "mergeCommitSha": merge_sha,
                    "changedFiles": file_count,
                    "sourceApiResponse": source_pull_response,
                    "sourceApiResponseSha256": sha256_json(
                        source_pull_response
                    ),
                },
                "snapshot": {
                    "baseSha": base_sha,
                    "headSha": head_sha,
                    "reviewedAt": TIMESTAMP,
                    "fileCount": file_count,
                    "changedPaths": paths,
                    "diffSha256": diff_sha,
                    "derivation": "recorded_pull_base",
                },
                "sourceArchiveEvidence": {
                    "archiveDigest": source_archive_digest,
                    "caseEvidenceDigest": source_archive_case_digest,
                    "pullResponseSha256": sha256_json(
                        source_pull_response
                    ),
                    "selectedCommentResponseSha256": {
                        str(90_000 + index): sha256_json(
                            source_api_response
                        )
                    },
                    "submittedReviewResponseSha256": {
                        str(80_000 + index): sha256_json(
                            source_review_response
                        )
                    },
                },
                **(
                    {"graphqlThreadArchive": graphql_archive}
                    if paper_ready
                    else {}
                ),
                "goldenComments": [golden],
                "ancestryEvidence": ancestry_evidence,
                "replay": {
                    "baseRef": f"benchmark/magento2/{case_id}/base",
                    "headRef": f"benchmark/magento2/{case_id}/head",
                },
            }
        )
    return attach_corpus_digest(
        {
            "kind": "codecrow-magento2-review-corpus",
            "corpusId": "magento2-core-review-50-test",
            "generatedAt": TIMESTAMP,
            "repository": "magento/magento2",
            "defaultBranch": "2.4-develop",
            "selectionPolicy": {
                "mergedOnly": True,
                "reviewCommentsRequired": True,
                "snapshotRule": "exact_original_review_commit",
                "requiredCases": 50,
                "bands": {
                    "small": {"minFiles": 3, "maxFiles": 10},
                    "medium": {"minFiles": 11, "maxFiles": 30},
                    "large": {"minFiles": 31, "maxFiles": 80},
                },
                "requiredBands": {"small": 1, "medium": 1, "large": 1},
                "selectionSeed": "fixed-evidence-first-2026-07-29",
                "partitionPolicy": {
                    "method": (
                        "deterministic_label_blind_stratified_by_size_band"
                    ),
                    "developmentCases": 30,
                    "sealedCases": 20,
                },
                "mainlineCutoffSha": mainline_cutoff_sha,
            },
            "cases": cases,
            "provenance": {
                "collector": "test-fixture",
                "collectedAt": TIMESTAMP,
                "githubApiVersion": "2022-11-28",
                "gitVersion": "git version fixture",
                "diffPolicy": {
                    "algorithm": "myers",
                    "indentHeuristic": False,
                    "renameDetection": "50%",
                    "externalDiff": False,
                    "textconv": False,
                    "forceText": True,
                    "systemAttributes": False,
                    "globalAttributes": False,
                    "replaceObjects": False,
                    "inheritedGitOverrides": False,
                    "color": False,
                    "sourcePrefix": "a/",
                    "destinationPrefix": "b/",
                    "linePrefix": "",
                    "defaultContextLines": 3,
                    "interHunkContextLines": 0,
                    "submoduleFormat": "short",
                    "ignoreSubmodules": "none",
                    "outputIndicators": {
                        "new": "+",
                        "old": "-",
                        "context": " ",
                    },
                    "quotePath": True,
                    "renameLimit": 1000,
                    "hostInfoAttributes": "reject_nonempty",
                    "localDiffConfig": "reject",
                    "legacyGrafts": False,
                    "shallowHistory": False,
                },
                "reviewBodiesRetainedVerbatim": True,
                "sourceArchiveDigest": source_archive_digest,
                "threadEvidenceDigest": (
                    thread_evidence_digest if paper_ready else None
                ),
                "selectionDigest": "d" * 64,
                "selectionFileSha256": "f" * 64,
            },
        }
    )


def make_draft() -> dict[str, Any]:
    cases = []
    for index in range(1, 51):
        band, count = _band_and_count(index)
        head = full_sha(f"draft-head-{index}")
        comment_id = 200_000 + index
        cases.append(
            {
                "case_id": f"draft-{index:03d}",
                "repository": "magento/magento2",
                "pr_number": 20_000 + index,
                "pr_url": (
                    "https://github.com/magento/magento2/pull/"
                    f"{20_000 + index}"
                ),
                "title": f"Draft PR {index}",
                "pr_author": f"author-{index}",
                "merged_at": f"2026-07-{index % 28 + 1:02d}T12:00:00Z",
                "base_branch": "2.4-develop",
                "benchmark_base_sha": full_sha(f"draft-base-{index}"),
                "benchmark_head_sha": head,
                "final_head_sha": full_sha(f"draft-final-{index}"),
                "merge_commit_sha": full_sha(f"draft-merge-{index}"),
                "merge_first_parent_sha": full_sha(
                    f"draft-mainline-{index}"
                ),
                "checkpoint_changed_files": count,
                "size_band": band,
                "changed_files": sorted(
                    [
                        f"app/code/Draft{index}/File.php",
                        *[
                            f"app/code/Draft{index}/Extra{extra:02d}.php"
                            for extra in range(2, count + 1)
                        ],
                    ]
                ),
                "gold_comments": [
                    {
                        "id": comment_id,
                        "review_id": 300_000 + index,
                        "api_commit_id": head,
                        "original_commit_id": head,
                        "side": "RIGHT",
                        "thread_root": True,
                        "path": f"app/code/Draft{index}/File.php",
                        "path_status_at_checkpoint": "M",
                        "path_changed_before_merge": True,
                        "line": 8,
                        "raw_current_line": 8,
                        "raw_original_line": 8,
                        "raw_position": 1,
                        "raw_original_position": 1,
                        "start_line": None,
                        "raw_current_start_line": None,
                        "raw_original_start_line": None,
                        "raw_start_side": None,
                        "url": (
                            "https://github.com/magento/magento2/pull/"
                            f"{20_000 + index}#discussion_r{comment_id}"
                        ),
                        "reviewer": f"reviewer-{index}",
                        "author_association": "MEMBER",
                        "created_at": TIMESTAMP,
                        "updated_at": TIMESTAMP,
                        "body": f"Please correct draft defect {index}.",
                        "diff_hunk": "@@ -7,0 +8 @@\n+bad();",
                        "sampled_replies": [],
                        "gold_status": "provisional",
                        "anchor_validation": {
                            "side_is_right": True,
                            "path_in_checkpoint_diff": True,
                            "path_not_deleted": True,
                            "checkpoint_blob_resolved": True,
                            "diff_hunk_present": True,
                            "original_line_present": True,
                            "exact_line_content_match": True,
                            "validation_mode": (
                                "raw_original_line_exact_content_match"
                            ),
                        },
                        "adjudication": {"include_in_scoring": False},
                    }
                ],
            }
        )
    return {
        "kind": "codecrow-magento2-review-corpus-draft",
        "repository": "magento/magento2",
        "base_branch": "2.4-develop",
        "status": DRAFT_STATUS,
        "cases": cases,
    }


def make_decisions(
    draft: dict[str, Any],
    *,
    paper_ready: bool = True,
) -> dict[str, Any]:
    comments: dict[str, Any] = {}
    for index, case in enumerate(draft["cases"], start=1):
        comment_id = case["gold_comments"][0]["id"]
        comments[str(comment_id)] = {
            "include": True,
            "semanticActionable": True,
            "issuePresentAtSnapshot": True,
            "acceptedOrRequiredByReview": True,
            "fixedOrSupersededInFinalHead": True,
            "sameRootCauseFix": True,
            "threadDisposition": "fixed",
            "threadComplete": paper_ready,
            "summary": f"Draft issue {index}",
            "rootCause": "Incorrect dependency selection.",
            "failureMode": "The request produces the wrong result.",
            "requiredChange": "Select the correct dependency.",
            "category": "bug",
            "severity": "medium",
            "atomic": True,
            "fixCommitSha": full_sha(f"draft-fix-{index}"),
            "fixEvidence": [
                {"kind": "code_change", "detail": "The line was changed."},
                {"kind": "thread", "detail": "The reviewer resolved the thread."},
            ],
            "adjudication": {
                "status": "accepted" if paper_ready else "provisional",
                "annotators": (
                    ["curator-a", "curator-b"] if paper_ready else ["curator-a"]
                ),
                "adjudicator": "curator-a",
                "at": TIMESTAMP,
                "notes": "",
            },
        }
    return {
        "kind": DECISIONS_KIND,
        "draftSha256": sha256_json(draft),
        "comments": comments,
    }


def make_release_evidence(
    draft: dict[str, Any],
    decisions: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    thread_cases = []
    packet_cases = []
    for case in draft["cases"]:
        threads = []
        raw_graphql_threads = []
        packet_comments = []
        for comment in case["gold_comments"]:
            comment_id = int(comment["id"])
            raw_graphql_thread, thread = graphql_thread_fixture(
                comment_id=comment_id,
                pull_request=int(case["pr_number"]),
                path=str(comment["path"]),
                line=int(comment["line"]),
                body=str(comment["body"]),
                reviewer=str(comment["reviewer"]),
                commit_sha=str(comment["original_commit_id"]),
                review_id=int(comment["review_id"]),
                review_state="COMMENTED",
            )
            raw_graphql_threads.append(raw_graphql_thread)
            threads.append(thread)
            path_diff = f"fix-diff-{comment_id}"
            path_diff_digest = sha256_text(path_diff)
            path = comment["path"]
            checkpoint_blob = full_sha(
                f"packet-checkpoint-blob-{comment_id}"
            )
            final_blob = full_sha(f"packet-final-blob-{comment_id}")
            path_transition = {
                "status": "modified",
                "sourcePath": path,
                "finalPath": path,
                "renameSimilarity": None,
                "checkpointBlobOid": checkpoint_blob,
                "finalBlobOid": final_blob,
                "diffSha256": path_diff_digest,
            }
            packet_comments.append(
                {
                    "commentId": comment_id,
                    "sourceUrl": comment["url"],
                    "reviewer": comment["reviewer"],
                    "reviewerAssociation": comment["author_association"],
                    "createdAt": comment["created_at"],
                    "body": comment["body"],
                    "path": path,
                    "line": comment["line"],
                    "startLine": comment.get("start_line"),
                    "diffHunk": comment["diff_hunk"],
                    "pathTransition": path_transition,
                    "checkpointSource": {
                        "available": True,
                        "path": path,
                        "blobOid": checkpoint_blob,
                        "startLine": 1,
                        "endLine": 1,
                        "content": "bad();",
                    },
                    "finalSource": {
                        "available": True,
                        "path": path,
                        "blobOid": final_blob,
                        "startLine": 1,
                        "endLine": 1,
                        "content": "fixed();",
                    },
                    "checkpointToFinalPathDiff": path_diff,
                    "checkpointToFinalPathDiffSha256": path_diff_digest,
                }
            )
            decision = decisions["comments"].get(str(comment_id))
            if isinstance(decision, dict) and decision.get("include") is True:
                decision["fixEvidence"] = [
                    {
                        "kind": "code_change",
                        "detail": "The line was changed.",
                        "artifactDigest": path_diff_digest,
                    },
                    {
                        "kind": "thread",
                        "detail": "The complete thread was reviewed.",
                        "artifactDigest": sha256_json(thread),
                    },
                ]
                records = []
                for annotator in ("curator-a", "curator-b"):
                    record = {
                        "annotator": annotator,
                        "verdict": "accept",
                        "at": TIMESTAMP,
                    }
                    record["recordDigest"] = sha256_json(record)
                    records.append(record)
                decision["adjudication"]["records"] = records
        graphql_archive = graphql_archive_fixture(
            int(case["pr_number"]),
            raw_graphql_threads,
        )
        thread_cases.append(
            {
                "pullRequest": case["pr_number"],
                "threads": threads,
                "reviews": [],
                "graphqlPageArchive": graphql_archive,
                "graphqlResponseDigests": [
                    page["responseDigest"]
                    for page in graphql_archive["pages"]
                ],
            }
        )
        packet_cases.append(
            {
                "draftCaseId": case["case_id"],
                "pullRequest": case["pr_number"],
                "sourceUrl": case["pr_url"],
                "baseSha": case["benchmark_base_sha"],
                "headSha": case["benchmark_head_sha"],
                "finalHeadSha": case["final_head_sha"],
                "sizeBand": case["size_band"],
                "fileCount": case["checkpoint_changed_files"],
                "comments": packet_comments,
            }
        )
    thread_evidence = {
        "kind": "codecrow-magento2-review-thread-evidence",
        "generatedAt": TIMESTAMP,
        "draftSha256": sha256_json(draft),
        "source": "fixture",
        "graphqlResolutionMetadataAvailable": True,
        "cases": thread_cases,
    }
    thread_evidence["threadEvidenceDigest"] = sha256_json(thread_evidence)
    packet = {
        "kind": "codecrow-magento2-curation-packet",
        "generatedAt": TIMESTAMP,
        "draftSha256": sha256_json(draft),
        "warning": "fixture",
        "cases": packet_cases,
    }
    packet["packetDigest"] = sha256_json(packet)
    thread_by_comment = {
        int(thread["rootCommentId"]): thread
        for case in thread_cases
        for thread in case["threads"]
    }
    for index, case in enumerate(draft["cases"], start=1):
        for comment in case["gold_comments"]:
            comment_id = int(comment["id"])
            decision = decisions["comments"].get(str(comment_id))
            if not isinstance(decision, dict) or decision.get("include") is not True:
                continue
            adjudication = decision["adjudication"]
            thread_digest = sha256_json(thread_by_comment[comment_id])
            normalized_adjudication = {
                "status": adjudication["status"],
                "annotators": adjudication["annotators"],
                "adjudicator": adjudication["adjudicator"],
                "at": adjudication["at"],
                "notes": adjudication.get("notes", ""),
                "threadComplete": decision["threadComplete"],
                "threadDisposition": decision["threadDisposition"],
                "threadEvidenceDigest": thread_evidence[
                    "threadEvidenceDigest"
                ],
                "threadDigest": thread_digest,
                "curationPacketDigest": packet["packetDigest"],
            }
            expected_issue = {
                "summary": decision["summary"],
                "rootCause": decision["rootCause"],
                "failureMode": decision["failureMode"],
                "requiredChange": decision["requiredChange"],
                "category": decision["category"],
                "severity": decision["severity"],
                "actionable": True,
                "atomic": decision["atomic"] is True,
            }
            decision_digest = decision_binding_digest(
                expected_issue=expected_issue,
                fix_commit_sha=decision["fixCommitSha"],
                fix_evidence=decision["fixEvidence"],
                adjudication=normalized_adjudication,
            )
            for record in adjudication["records"]:
                record.pop("recordDigest", None)
                record.update(
                    {
                        "caseId": f"m2b-{index:03d}",
                        "sourceCommentId": comment_id,
                        "sourceBodySha256": sha256_text(comment["body"]),
                        "decisionDigest": decision_digest,
                        "sourceArchiveDigest": None,
                        "threadEvidenceDigest": thread_evidence[
                            "threadEvidenceDigest"
                        ],
                        "threadDigest": thread_digest,
                        "curationPacketDigest": packet["packetDigest"],
                    }
                )
                record["recordDigest"] = sha256_json(record)
    return thread_evidence, packet


def write_json(path: Path, value: Any) -> Path:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def make_judgment(
    corpus: dict[str, Any],
    *,
    suffix: str,
    analysis_model: str,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    corpus_cases = {case["caseId"]: case for case in corpus["cases"]}
    for case in cases:
        source_case = corpus_cases.get(case.get("caseId"))
        if source_case is None or case.get("status") != "scored":
            continue
        case["goldIssues"] = [
            {
                "goldId": f"G{index:03d}",
                "sourceId": gold["id"],
                "sourceUrl": gold["sourceUrl"],
                "path": gold["path"],
                "line": gold["originalLine"],
                "reviewComment": gold["body"],
                "summary": gold["expectedIssue"]["summary"],
                "category": gold["expectedIssue"]["category"],
                "severity": gold["expectedIssue"]["severity"],
            }
            for index, gold in enumerate(
                source_case["goldenComments"], start=1
            )
        ]
    value = {
        "kind": "codecrow-magento2-judgment-run",
        "judgmentId": f"judgment-{suffix}",
        "analysisRunId": f"analysis-{suffix}",
        "analysisModel": analysis_model,
        "judgeModel": "judge-fixture",
        "promptVersion": "fixture-v1",
        "corpusDigest": corpus["corpusDigest"],
        "cases": cases,
    }
    value["judgmentDigest"] = sha256_json(value)
    return value


def scored_case(
    case_id: str,
    *,
    gold_count: int,
    candidate_count: int,
    assignments: int,
    novel_verdicts: list[str] | None = None,
) -> dict[str, Any]:
    matched = min(assignments, gold_count, candidate_count)
    assignment_values = [
        {
            "goldId": f"G{index + 1:03d}",
            "candidateId": f"C{index + 1:03d}",
            "weight": 1.0,
        }
        for index in range(assignments)
    ]
    unmatched_gold = [
        f"G{index:03d}" for index in range(matched + 1, gold_count + 1)
    ]
    unmatched_candidates = [
        f"C{index:03d}" for index in range(matched + 1, candidate_count + 1)
    ]
    verdicts = list(novel_verdicts or [])
    novel = [
        {
            "candidateId": candidate_id,
            "verdict": verdict,
            "grounded_at_snapshot": (
                "yes" if verdict == "valid_in_scope_novel" else "no"
            ),
            "actionable": (
                "yes" if verdict == "valid_in_scope_novel" else "no"
            ),
            "confidence": 0.9,
        }
        for candidate_id, verdict in zip(unmatched_candidates, verdicts)
    ]
    matched_edges = {
        (
            f"G{index + 1:03d}",
            f"C{index + 1:03d}",
        )
        for index in range(assignments)
    }
    pair_judgments = []
    for gold_index in range(1, gold_count + 1):
        for candidate_index in range(1, candidate_count + 1):
            gold_id = f"G{gold_index:03d}"
            candidate_id = f"C{candidate_index:03d}"
            matched_edge = (gold_id, candidate_id) in matched_edges
            pair_judgments.append(
                {
                    "goldId": gold_id,
                    "candidateId": candidate_id,
                    "specific_issue": "yes",
                    "grounded_at_snapshot": "yes",
                    "same_root_cause": "yes" if matched_edge else "no",
                    "same_failure_or_consequence": (
                        "yes" if matched_edge else "no"
                    ),
                    "compatible_required_change": (
                        "yes" if matched_edge else "no"
                    ),
                    "location_relation": (
                        "same_symbol" if matched_edge else "unrelated"
                    ),
                    "verdict": (
                        "substantive_match" if matched_edge else "no_match"
                    ),
                    "confidence": 0.9,
                }
            )
    return {
        "caseId": case_id,
        "status": "scored",
        "goldCount": gold_count,
        "candidateCount": candidate_count,
        "goldIssues": [
            {
                "goldId": f"G{index:03d}",
                "category": "bug",
                "severity": "medium",
                "summary": f"Gold {index}",
            }
            for index in range(1, gold_count + 1)
        ],
        "candidateFindings": [
            {
                "candidateId": f"C{index:03d}",
                "title": f"Candidate {index}",
            }
            for index in range(1, candidate_count + 1)
        ],
        "assignments": assignment_values,
        "pairJudgments": pair_judgments,
        "unmatchedGold": unmatched_gold,
        "unmatchedCandidates": unmatched_candidates,
        "novelFindingJudgments": novel,
    }


def make_git_pair(repository: Path) -> tuple[str, str]:
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Benchmark Test"],
        check=True,
    )
    for name in ("A.php", "B.php", "C.php"):
        (repository / name).write_text("<?php\nreturn 1;\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-q", "-m", "base"],
        check=True,
    )
    base = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    for name in ("A.php", "B.php", "C.php"):
        (repository / name).write_text("<?php\nreturn 2;\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-q", "-m", "head"],
        check=True,
    )
    head = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    return base, head


@pytest.fixture
def corpus_factory():
    def factory(*, paper_ready: bool = True) -> dict[str, Any]:
        return copy.deepcopy(make_corpus(paper_ready=paper_ready))

    return factory


@pytest.fixture
def draft_factory():
    def factory() -> dict[str, Any]:
        return copy.deepcopy(make_draft())

    return factory
