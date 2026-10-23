from __future__ import annotations

from collections import Counter
from datetime import datetime
import re
from typing import Any, Mapping

from .path_transition import validate_path_transition_evidence
from .thread_provenance import (
    rest_review_comment_anchor,
    validate_graphql_thread_archive,
    validate_review_thread_binding,
)
from .util import require_full_sha, require_text, sha256_json, sha256_text


CORPUS_KIND = "codecrow-magento2-review-corpus"
VALID_CATEGORIES = {
    "architecture",
    "backward_compatibility",
    "bug",
    "code_quality",
    "documentation",
    "performance",
    "security",
    "style",
    "testing",
}
VALID_SEVERITIES = {"critical", "high", "medium", "low"}
VALID_BANDS = {"small", "medium", "large"}
SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EXPECTED_PARTITION_POLICY = {
    "method": "deterministic_label_blind_stratified_by_size_band",
    "developmentCases": 30,
    "sealedCases": 20,
}


def _integer(value: Any, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _timestamp(value: Any, field: str) -> str:
    text = require_text(value, field)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    return text


def _url(value: Any, field: str) -> str:
    text = require_text(value, field)
    if not text.startswith("https://github.com/"):
        raise ValueError(f"{field} must be a public GitHub URL")
    return text


def _sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{field} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must not contain duplicates")
    return list(value)


def _size_band(file_count: int, policy: Mapping[str, Any]) -> str:
    bands = policy.get("bands")
    if not isinstance(bands, Mapping):
        raise ValueError("selectionPolicy.bands must be an object")
    matches = []
    for name in ("small", "medium", "large"):
        bounds = bands.get(name)
        if not isinstance(bounds, Mapping):
            raise ValueError(f"selectionPolicy.bands.{name} must be an object")
        minimum = _integer(bounds.get("minFiles"), f"{name}.minFiles", 1)
        maximum = _integer(bounds.get("maxFiles"), f"{name}.maxFiles", minimum)
        if maximum < minimum:
            raise ValueError(f"{name}.maxFiles must be >= minFiles")
        if minimum <= file_count <= maximum:
            matches.append(name)
    if len(matches) != 1:
        raise ValueError(
            f"file count {file_count} must belong to exactly one configured size band"
        )
    return matches[0]


def decision_binding_digest(
    *,
    expected_issue: Mapping[str, Any],
    fix_commit_sha: Any,
    fix_evidence: Any,
    adjudication: Mapping[str, Any],
) -> str:
    """Digest the released decision fields shared by curation and validation."""

    return sha256_json(
        {
            "expectedIssue": dict(expected_issue),
            "fixCommitSha": fix_commit_sha,
            "fixEvidence": list(fix_evidence) if isinstance(fix_evidence, list) else [],
            "adjudication": {
                field: adjudication.get(field)
                for field in (
                    "status",
                    "annotators",
                    "adjudicator",
                    "at",
                    "notes",
                    "threadComplete",
                    "threadDisposition",
                    "threadEvidenceDigest",
                    "threadDigest",
                    "curationPacketDigest",
                )
            },
        }
    )


def _validate_source_pr_evidence(
    source: Mapping[str, Any],
    field: str,
) -> None:
    raw = source.get("sourceApiResponse")
    if not isinstance(raw, Mapping):
        raise ValueError(f"{field} has no archived pull-request API response")
    _sha256(
        source.get("sourceApiResponseSha256"),
        f"{field}.sourceApiResponseSha256",
    )
    if source["sourceApiResponseSha256"] != sha256_json(raw):
        raise ValueError(f"{field}.sourceApiResponse digest mismatch")
    user = raw.get("user")
    base = raw.get("base")
    head = raw.get("head")
    expected = {
        "number": raw.get("number"),
        "url": raw.get("html_url"),
        "title": raw.get("title"),
        "author": user.get("login") if isinstance(user, Mapping) else None,
        "baseRef": base.get("ref") if isinstance(base, Mapping) else None,
        "mergedAt": raw.get("merged_at"),
        "finalHeadSha": head.get("sha") if isinstance(head, Mapping) else None,
        "mergeCommitSha": raw.get("merge_commit_sha"),
        "changedFiles": raw.get("changed_files"),
    }
    if any(source.get(key) != value for key, value in expected.items()):
        raise ValueError(
            f"{field} drifted from its archived pull-request API response"
        )
    if raw.get("state") != "closed" or not raw.get("merged_at"):
        raise ValueError(f"{field} archived pull request is not merged")


def _validate_golden_source_evidence(
    value: Mapping[str, Any],
    field: str,
    *,
    source_pr: Mapping[str, Any],
) -> None:
    comment = value["sourceApiResponse"]
    review = value["sourceReviewResponse"]
    anchor = _validate_golden_anchor_projection(value, field)
    user = comment.get("user")
    expected = {
        "sourceCommentId": comment.get("id"),
        "sourceUrl": comment.get("html_url"),
        "reviewId": comment.get("pull_request_review_id"),
        "reviewer": user.get("login") if isinstance(user, Mapping) else None,
        "reviewerType": user.get("type") if isinstance(user, Mapping) else None,
        "reviewerAssociation": comment.get("author_association") or "UNKNOWN",
        "createdAt": comment.get("created_at"),
        "path": comment.get("path"),
        "side": anchor["side"],
        "sourceOriginalLine": anchor["originalLine"],
        "originalCommitId": anchor["originalCommitId"],
        "body": comment.get("body"),
        "diffHunk": comment.get("diff_hunk"),
        "sourceUpdatedAt": comment.get("updated_at"),
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise ValueError(
            f"{field} drifted from its archived review-comment API response"
        )
    if comment.get("in_reply_to_id") is not None:
        raise ValueError(f"{field} archived comment is not a thread root")
    pr_number = source_pr["number"]
    pull_api_suffix = f"/repos/magento/magento2/pulls/{pr_number}"
    expected_pull_api_url = f"https://api.github.com{pull_api_suffix}"
    if not str(comment.get("pull_request_url") or "").endswith(pull_api_suffix):
        raise ValueError(f"{field} archived comment belongs to another pull request")

    review_user = review.get("user")
    review_expected = {
        "reviewId": review.get("id"),
        "reviewer": (
            review_user.get("login")
            if isinstance(review_user, Mapping)
            else None
        ),
        "reviewState": review.get("state"),
        "reviewSubmittedAt": review.get("submitted_at"),
    }
    if any(
        value.get(key) != expected_value
        for key, expected_value in review_expected.items()
    ):
        raise ValueError(
            f"{field} drifted from its archived submitted-review API response"
        )
    if (
        not isinstance(review_user, Mapping)
        or review_user.get("type") != "User"
        or review.get("state")
        not in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"}
    ):
        raise ValueError(f"{field} archived review is not a submitted human review")
    _timestamp(
        review.get("submitted_at"),
        f"{field}.sourceReviewResponse.submitted_at",
    )
    require_full_sha(
        review.get("commit_id"),
        f"{field}.sourceReviewResponse.commit_id",
    )
    if review.get("pull_request_url") != expected_pull_api_url:
        raise ValueError(f"{field} archived review belongs to another pull request")


def _validate_golden_anchor_projection(
    value: Mapping[str, Any],
    field: str,
) -> dict[str, Any]:
    """Bind released H/current coordinate fields to the exact REST payload."""

    anchor = rest_review_comment_anchor(
        value.get("sourceApiResponse"),
        field=f"{field}.sourceApiResponse",
    )
    expected = {
        "sourceCurrentCommitId": anchor["currentCommitId"],
        "originalCommitId": anchor["originalCommitId"],
        "path": anchor["path"],
        "side": anchor["side"],
        "sourceCurrentLine": anchor["currentLine"],
        "sourceOriginalLine": anchor["originalLine"],
        "sourceCurrentStartLine": anchor["currentStartLine"],
        "sourceOriginalStartLine": anchor["originalStartLine"],
        "sourceStartSide": anchor["startSide"],
    }
    if any(
        value.get(key) != expected_value
        for key, expected_value in expected.items()
    ):
        raise ValueError(
            f"{field} current/original anchor projection drifted from its "
            "archived review-comment API response"
        )
    return anchor


def _validate_ancestry_evidence(
    case: Mapping[str, Any],
    field: str,
    *,
    mainline_cutoff_sha: str,
) -> None:
    evidence = case.get("ancestryEvidence")
    if not isinstance(evidence, Mapping):
        raise ValueError(f"{field} has no Git ancestry evidence")
    digest_value = dict(evidence)
    declared = digest_value.pop("evidenceDigest", None)
    if declared != sha256_json(digest_value):
        raise ValueError(f"{field}.ancestryEvidence digest mismatch")
    snapshot = case["snapshot"]
    source = case["sourcePr"]
    parents = evidence.get("mergeParents")
    if not isinstance(parents, list) or len(parents) != 2:
        raise ValueError(f"{field}.ancestryEvidence merge parents are invalid")
    for index, parent in enumerate(parents):
        require_full_sha(
            parent,
            f"{field}.ancestryEvidence.mergeParents[{index}]",
        )
    expected = {
        "baseSha": snapshot["baseSha"],
        "reviewedHeadSha": snapshot["headSha"],
        "finalHeadSha": source["finalHeadSha"],
        "mergeCommitSha": source["mergeCommitSha"],
        "mergeFirstParentSha": parents[0],
        "mergeSecondParentSha": source["finalHeadSha"],
        "mainlineCutoffSha": mainline_cutoff_sha,
        "reviewedMainlineMergeBaseSha": snapshot["baseSha"],
    }
    if evidence.get("schema") != "codecrow.magento2-review-ancestry":
        raise ValueError(f"{field}.ancestryEvidence schema is invalid")
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise ValueError(f"{field}.ancestryEvidence identity drift")
    if parents[1] != source["finalHeadSha"]:
        raise ValueError(f"{field}.ancestryEvidence final head is not merge parent")
    checks = evidence.get("checks")
    required_checks = {
        "baseAncestorReviewedHead": True,
        "reviewedHeadAncestorFinalHead": True,
        "finalHeadIsMergeSecondParent": True,
        "baseIsReviewedMainlineMergeBase": True,
        "mergeCommitAncestorMainlineCutoff": True,
    }
    if checks != required_checks:
        raise ValueError(f"{field}.ancestryEvidence proof checks are incomplete")


def _source_archive_digest_map(
    value: Any,
    *,
    field: str,
) -> dict[int, str]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{field} must be a non-empty digest map")
    result: dict[int, str] = {}
    for key, digest in value.items():
        if (
            not isinstance(key, str)
            or not key.isdigit()
            or key.startswith("0")
        ):
            raise ValueError(f"{field} keys must be canonical positive IDs")
        identifier = int(key)
        if identifier < 1 or identifier in result:
            raise ValueError(f"{field} keys must be unique positive IDs")
        _sha256(digest, f"{field}.{key}")
        result[identifier] = str(digest)
    return result


def _validate_source_archive_case_evidence(
    case: Mapping[str, Any],
    field: str,
    *,
    source_archive_digest: Any,
) -> None:
    evidence = case.get("sourceArchiveEvidence")
    if not isinstance(evidence, Mapping):
        raise ValueError(f"{field} has no source archive case evidence")
    expected_fields = {
        "archiveDigest",
        "caseEvidenceDigest",
        "pullResponseSha256",
        "selectedCommentResponseSha256",
        "submittedReviewResponseSha256",
    }
    if set(evidence) != expected_fields:
        raise ValueError(f"{field}.sourceArchiveEvidence fields are invalid")
    _sha256(
        evidence.get("archiveDigest"),
        f"{field}.sourceArchiveEvidence.archiveDigest",
    )
    if evidence.get("archiveDigest") != source_archive_digest:
        raise ValueError(
            f"{field}.sourceArchiveEvidence belongs to another source archive"
        )
    _sha256(
        evidence.get("caseEvidenceDigest"),
        f"{field}.sourceArchiveEvidence.caseEvidenceDigest",
    )
    _sha256(
        evidence.get("pullResponseSha256"),
        f"{field}.sourceArchiveEvidence.pullResponseSha256",
    )
    if (
        evidence.get("pullResponseSha256")
        != case["sourcePr"].get("sourceApiResponseSha256")
    ):
        raise ValueError(
            f"{field}.sourceArchiveEvidence does not bind the pull response"
        )
    comment_digests = _source_archive_digest_map(
        evidence.get("selectedCommentResponseSha256"),
        field=(
            f"{field}.sourceArchiveEvidence."
            "selectedCommentResponseSha256"
        ),
    )
    review_digests = _source_archive_digest_map(
        evidence.get("submittedReviewResponseSha256"),
        field=(
            f"{field}.sourceArchiveEvidence."
            "submittedReviewResponseSha256"
        ),
    )
    for comment in case["goldenComments"]:
        comment_id = int(comment["sourceCommentId"])
        review_id = int(comment["reviewId"])
        if (
            comment_digests.get(comment_id)
            != comment.get("sourceApiResponseSha256")
        ):
            raise ValueError(
                f"{field}.sourceArchiveEvidence does not bind comment "
                f"{comment_id}"
            )
        if (
            review_digests.get(review_id)
            != comment.get("sourceReviewResponseSha256")
        ):
            raise ValueError(
                f"{field}.sourceArchiveEvidence does not bind review "
                f"{review_id}"
            )


def _validate_expected_issue(
    value: Any,
    field: str,
    *,
    paper_ready: bool,
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    require_text(value.get("summary"), f"{field}.summary")
    category = value.get("category")
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"{field}.category must be one of {sorted(VALID_CATEGORIES)}"
        )
    severity = value.get("severity")
    if severity not in VALID_SEVERITIES:
        raise ValueError(
            f"{field}.severity must be one of {sorted(VALID_SEVERITIES)}"
        )
    if value.get("actionable") is not True:
        raise ValueError(f"{field}.actionable must be true")
    if paper_ready and value.get("atomic") is not True:
        raise ValueError(f"{field}.atomic must be true for paper-ready data")
    if paper_ready:
        require_text(value.get("rootCause"), f"{field}.rootCause")
        require_text(value.get("failureMode"), f"{field}.failureMode")
        require_text(value.get("requiredChange"), f"{field}.requiredChange")


def _validate_golden(
    value: Any,
    field: str,
    *,
    case: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    paper_ready: bool,
    source_archive_digest: Any,
    thread_evidence_digest: Any,
    graphql_thread_archive: Mapping[str, Any] | None,
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    require_text(value.get("id"), f"{field}.id")
    _integer(value.get("sourceCommentId"), f"{field}.sourceCommentId", 1)
    _integer(value.get("reviewId"), f"{field}.reviewId", 1)
    _url(value.get("sourceUrl"), f"{field}.sourceUrl")
    reviewer = require_text(value.get("reviewer"), f"{field}.reviewer")
    if reviewer.casefold() == str(case["sourcePr"]["author"]).casefold():
        raise ValueError(f"{field} is authored by the pull-request author")
    if value.get("reviewerType") != "User":
        raise ValueError(f"{field}.reviewerType must be User")
    require_text(value.get("reviewerAssociation"), f"{field}.reviewerAssociation")
    _timestamp(value.get("createdAt"), f"{field}.createdAt")
    path = require_text(value.get("path"), f"{field}.path")
    if path not in snapshot["changedPaths"]:
        raise ValueError(f"{field}.path is absent from the frozen snapshot diff")
    if value.get("side") != "RIGHT":
        raise ValueError(f"{field}.side must be RIGHT")
    _integer(value.get("originalLine"), f"{field}.originalLine", 1)
    original_commit = require_full_sha(
        value.get("originalCommitId"),
        f"{field}.originalCommitId",
    )
    if original_commit != snapshot["headSha"]:
        raise ValueError(
            f"{field} targets {original_commit}, not frozen head "
            f"{snapshot['headSha']}"
        )
    body = require_text(value.get("body"), f"{field}.body")
    if value.get("bodySha256") != sha256_text(body):
        raise ValueError(f"{field}.bodySha256 does not match the source body")
    require_text(value.get("diffHunk"), f"{field}.diffHunk")
    if value.get("threadRoot") is not True:
        raise ValueError(f"{field} must be a root review-thread comment")
    source_api_response = value.get("sourceApiResponse")
    source_review_response = value.get("sourceReviewResponse")
    if source_api_response is not None:
        if not isinstance(source_api_response, Mapping):
            raise ValueError(f"{field}.sourceApiResponse must be an object")
        _sha256(
            value.get("sourceApiResponseSha256"),
            f"{field}.sourceApiResponseSha256",
        )
        if value["sourceApiResponseSha256"] != sha256_json(
            source_api_response
        ):
            raise ValueError(f"{field}.sourceApiResponse digest mismatch")
    if source_review_response is not None:
        if not isinstance(source_review_response, Mapping):
            raise ValueError(f"{field}.sourceReviewResponse must be an object")
        _sha256(
            value.get("sourceReviewResponseSha256"),
            f"{field}.sourceReviewResponseSha256",
        )
        if value["sourceReviewResponseSha256"] != sha256_json(
            source_review_response
        ):
            raise ValueError(f"{field}.sourceReviewResponse digest mismatch")
    if paper_ready:
        _validate_golden_source_evidence(
            value,
            field,
            source_pr=case["sourcePr"],
        )
    _validate_expected_issue(
        value.get("expectedIssue"),
        f"{field}.expectedIssue",
        paper_ready=paper_ready,
    )

    validity = value.get("validity")
    if not isinstance(validity, Mapping):
        raise ValueError(f"{field}.validity must be an object")
    if validity.get("status") != "present_at_snapshot":
        raise ValueError(f"{field} is not certified present at the snapshot")
    if validity.get("snapshotSha") != snapshot["headSha"]:
        raise ValueError(f"{field}.validity.snapshotSha does not match the case")
    if validity.get("method") not in {
        "exact_original_commit_and_diff_anchor",
        "exact_original_commit_and_diff_context_anchor",
        "exact_original_commit_position_resolved_anchor",
        "human_code_inspection",
    }:
        raise ValueError(f"{field}.validity.method is unsupported")
    if validity.get("method") == "exact_original_commit_position_resolved_anchor":
        source_line = _integer(
            value.get("sourceOriginalLine"),
            f"{field}.sourceOriginalLine",
            1,
        )
        resolution = value.get("originalLineResolution")
        if (
            not isinstance(resolution, Mapping)
            or resolution.get("method")
            != "raw_original_position_hunk_terminal_with_adjacent_checkpoint_line"
            or abs(source_line - int(value["originalLine"])) > 1
        ):
            raise ValueError(f"{field} has invalid position-based line resolution")
    anchor = validity.get("anchorValidation")
    if not isinstance(anchor, Mapping) or anchor.get("status") != "exact":
        raise ValueError(f"{field} has no exact frozen-diff anchor")
    _sha256(anchor.get("diffSha256"), f"{field}.anchorValidation.diffSha256")
    line_kind = anchor.get("lineKind")
    if line_kind not in {None, "added", "context"}:
        raise ValueError(f"{field}.anchorValidation.lineKind is invalid")
    if (
        validity.get("method")
        == "exact_original_commit_and_diff_context_anchor"
        and line_kind != "context"
    ):
        raise ValueError(f"{field} context anchor must declare lineKind=context")
    if anchor["diffSha256"] != snapshot["diffSha256"]:
        raise ValueError(f"{field} was validated against a different diff")
    fixed_later = validity.get("fixedLater")
    if fixed_later not in (True, False, None):
        raise ValueError(f"{field}.validity.fixedLater must be boolean or null")
    if fixed_later is True:
        require_full_sha(
            validity.get("fixCommitSha"),
            f"{field}.validity.fixCommitSha",
        )
    path_transition = validity.get("pathTransition")
    if path_transition is not None:
        transition_digest = (
            path_transition.get("diffSha256")
            if isinstance(path_transition, Mapping)
            else ""
        )
        try:
            validate_path_transition_evidence(
                path_transition,
                source_path=path,
                diff_sha256=str(transition_digest),
            )
        except ValueError as exc:
            raise ValueError(
                f"{field}.validity.pathTransition is invalid: {exc}"
            ) from exc
    if paper_ready:
        if fixed_later is not True:
            raise ValueError(f"{field} has no verified later fix")
        if validity.get("disposition") != "fixed":
            raise ValueError(f"{field}.validity.disposition must be fixed")
        if path_transition is None:
            raise ValueError(
                f"{field} has no deterministic checkpoint-to-final "
                "path transition evidence"
            )
        evidence = validity.get("fixEvidence")
        if not isinstance(evidence, list) or len(evidence) < 2:
            raise ValueError(
                f"{field} needs code-change evidence plus another fix signal"
            )
        evidence_kinds = {
            str(item.get("kind") or "")
            for item in evidence
            if isinstance(item, Mapping)
        }
        if "code_change" not in evidence_kinds or len(evidence_kinds) < 2:
            raise ValueError(
                f"{field} needs code_change plus another fix signal"
            )
        for evidence_index, item in enumerate(evidence):
            if not isinstance(item, Mapping):
                raise ValueError(
                    f"{field}.validity.fixEvidence[{evidence_index}] "
                    "must be an object"
                )
            require_text(
                item.get("kind"),
                f"{field}.validity.fixEvidence[{evidence_index}].kind",
            )
            require_text(
                item.get("detail"),
                f"{field}.validity.fixEvidence[{evidence_index}].detail",
            )
        transition_digest = str(path_transition["diffSha256"])
        matching_code_change = [
            item
            for item in evidence
            if isinstance(item, Mapping)
            and item.get("kind") == "code_change"
            and item.get("artifactDigest") == transition_digest
        ]
        if not matching_code_change:
            raise ValueError(
                f"{field} code_change evidence is not bound to the "
                "path-transition diff"
            )
        for item in matching_code_change:
            _sha256(
                item.get("artifactDigest"),
                f"{field}.validity.fixEvidence.artifactDigest",
            )

    adjudication = value.get("adjudication")
    if not isinstance(adjudication, Mapping):
        raise ValueError(f"{field}.adjudication must be an object")
    if adjudication.get("status") not in {"accepted", "provisional"}:
        raise ValueError(f"{field}.adjudication.status is invalid")
    require_text(adjudication.get("adjudicator"), f"{field}.adjudicator")
    _timestamp(adjudication.get("at"), f"{field}.adjudication.at")
    if paper_ready and adjudication.get("status") != "accepted":
        raise ValueError(f"{field} is provisional, not paper-ready")
    if paper_ready:
        annotators = adjudication.get("annotators")
        if (
            not isinstance(annotators, list)
            or len({str(item) for item in annotators if str(item)}) < 2
        ):
            raise ValueError(f"{field} needs two annotators with independent review")
        if adjudication.get("threadComplete") is not True:
            raise ValueError(f"{field} has no complete review-thread audit")
        if adjudication.get("threadDisposition") != "fixed":
            raise ValueError(f"{field} review thread is not disposed as fixed")
        records = adjudication.get("records")
        if not isinstance(records, list) or len(records) < 2:
            raise ValueError(f"{field} needs independent annotator records")
        decision_digest = decision_binding_digest(
            expected_issue=value["expectedIssue"],
            fix_commit_sha=validity.get("fixCommitSha"),
            fix_evidence=validity.get("fixEvidence"),
            adjudication=adjudication,
        )
        if adjudication.get("decisionDigest") != decision_digest:
            raise ValueError(f"{field} adjudication decision digest mismatch")
        _sha256(source_archive_digest, "provenance.sourceArchiveDigest")
        expected_record_bindings = {
            "caseId": case["caseId"],
            "sourceCommentId": value["sourceCommentId"],
            "sourceBodySha256": value["bodySha256"],
            "decisionDigest": decision_digest,
            "sourceArchiveDigest": source_archive_digest,
            "threadEvidenceDigest": adjudication.get(
                "threadEvidenceDigest"
            ),
            "threadDigest": adjudication.get("threadDigest"),
            "curationPacketDigest": adjudication.get(
                "curationPacketDigest"
            ),
        }
        record_annotators = set()
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise ValueError(f"{field}.records[{index}] must be an object")
            record_value = dict(record)
            declared = record_value.pop("recordDigest", None)
            if declared != sha256_json(record_value):
                raise ValueError(
                    f"{field}.records[{index}] digest mismatch"
                )
            annotator = require_text(
                record.get("annotator"),
                f"{field}.records[{index}].annotator",
            ).strip()
            if record.get("verdict") != "accept":
                raise ValueError(f"{field}.records[{index}] is not accepted")
            if any(
                record.get(binding) != expected
                for binding, expected in expected_record_bindings.items()
            ):
                raise ValueError(
                    f"{field}.records[{index}] evidence binding mismatch"
                )
            record_annotators.add(annotator)
        normalized_annotators = {
            str(item).strip()
            for item in annotators
            if isinstance(item, str) and item.strip()
        }
        if record_annotators != normalized_annotators:
            raise ValueError(f"{field} annotator record identities drift")
        for digest_field in (
            "threadEvidenceDigest",
            "threadDigest",
            "curationPacketDigest",
        ):
            _sha256(
                adjudication.get(digest_field),
                f"{field}.adjudication.{digest_field}",
            )
        if not isinstance(source_api_response, Mapping) or not isinstance(
            source_review_response, Mapping
        ):
            raise ValueError(f"{field} has no archived source API evidence")

    thread_binding = value.get("reviewThreadEvidence")
    if thread_binding is not None and not isinstance(thread_binding, Mapping):
        raise ValueError(f"{field}.reviewThreadEvidence must be an object")
    if paper_ready:
        if not isinstance(graphql_thread_archive, Mapping):
            raise ValueError(
                f"{field} has no raw GraphQL thread page archive"
            )
        _sha256(
            thread_evidence_digest,
            "provenance.threadEvidenceDigest",
        )
        source_archive_case = case.get("sourceArchiveEvidence")
        if not isinstance(source_archive_case, Mapping):
            raise ValueError(
                f"{field} has no source archive case evidence"
            )
        try:
            validated_thread = validate_review_thread_binding(
                thread_binding,
                archive=graphql_thread_archive,
                root_comment_id=int(value["sourceCommentId"]),
                thread_evidence_digest=str(thread_evidence_digest),
                require_complete=paper_ready,
                source_archive_digest=str(source_archive_digest),
                source_archive_case_evidence_digest=str(
                    source_archive_case.get("caseEvidenceDigest")
                ),
            )
        except ValueError as exc:
            raise ValueError(
                f"{field}.reviewThreadEvidence is invalid: {exc}"
            ) from exc
        if (
            adjudication.get("threadEvidenceDigest")
            != thread_evidence_digest
            or adjudication.get("threadDigest")
            != validated_thread["threadDigest"]
        ):
            raise ValueError(
                f"{field} adjudication is not bound to its raw GraphQL "
                "thread evidence"
            )
        rest_thread = validated_thread.get("restThreadEvidence")
        rest_comments = (
            rest_thread.get("comments")
            if isinstance(rest_thread, Mapping)
            else None
        )
        rest_reviews = (
            rest_thread.get("submittedReviews")
            if isinstance(rest_thread, Mapping)
            else None
        )
        if (
            not isinstance(rest_comments, list)
            or not rest_comments
            or rest_comments[0] != source_api_response
        ):
            raise ValueError(
                f"{field}.reviewThreadEvidence REST root does not match "
                "the archived source review comment"
            )
        matching_reviews = [
            review
            for review in rest_reviews
            if isinstance(review, Mapping)
            and review.get("id") == value.get("reviewId")
        ] if isinstance(rest_reviews, list) else []
        if matching_reviews != [source_review_response]:
            raise ValueError(
                f"{field}.reviewThreadEvidence REST review does not match "
                "the archived submitted review"
            )


def _paper_ready_golden(value: Mapping[str, Any]) -> bool:
    expected = value.get("expectedIssue")
    validity = value.get("validity")
    adjudication = value.get("adjudication")
    if not all(
        isinstance(item, Mapping)
        for item in (expected, validity, adjudication)
    ):
        return False
    annotators = adjudication.get("annotators")
    records = adjudication.get("records")
    evidence = validity.get("fixEvidence")
    path_transition = validity.get("pathTransition")
    review_thread_evidence = value.get("reviewThreadEvidence")
    transition_digest = (
        path_transition.get("diffSha256")
        if isinstance(path_transition, Mapping)
        else None
    )
    return bool(
        expected.get("atomic") is True
        and all(
            isinstance(expected.get(field), str) and expected[field].strip()
            for field in ("rootCause", "failureMode", "requiredChange")
        )
        and validity.get("fixedLater") is True
        and validity.get("disposition") == "fixed"
        and isinstance(evidence, list)
        and len(evidence) >= 2
        and isinstance(path_transition, Mapping)
        and isinstance(review_thread_evidence, Mapping)
        and path_transition.get("sourcePath") == value.get("path")
        and isinstance(transition_digest, str)
        and len(transition_digest) == 64
        and any(
            isinstance(item, Mapping)
            and item.get("kind") == "code_change"
            and item.get("artifactDigest") == transition_digest
            for item in evidence
        )
        and adjudication.get("status") == "accepted"
        and isinstance(annotators, list)
        and len({str(item) for item in annotators if str(item)}) >= 2
        and isinstance(records, list)
        and len(records) >= 2
        and all(
            isinstance(adjudication.get(field), str)
            and len(adjudication[field]) == 64
            for field in (
                "threadEvidenceDigest",
                "threadDigest",
                "curationPacketDigest",
            )
        )
        and adjudication.get("threadComplete") is True
        and adjudication.get("threadDisposition") == "fixed"
    )


def validate_corpus(
    payload: Any,
    *,
    paper_ready: bool = False,
    required_cases: int | None = None,
) -> dict[str, Any]:
    """Validate and summarize a frozen Magento review corpus.

    The strict snapshot invariant is deliberate: every golden comment in a case
    must have been created against that exact case head. This avoids scoring a
    finding as a false negative after the developer already fixed the issue.
    """

    if not isinstance(payload, Mapping):
        raise ValueError("corpus must be a JSON object")
    if payload.get("kind") != CORPUS_KIND:
        raise ValueError(f"corpus.kind must be {CORPUS_KIND!r}")
    corpus_id = require_text(payload.get("corpusId"), "corpusId")
    _timestamp(payload.get("generatedAt"), "generatedAt")
    if payload.get("repository") != "magento/magento2":
        raise ValueError("repository must be magento/magento2")
    default_branch = require_text(payload.get("defaultBranch"), "defaultBranch")
    if default_branch != "2.4-develop":
        raise ValueError("defaultBranch must be 2.4-develop")
    policy = payload.get("selectionPolicy")
    if not isinstance(policy, Mapping):
        raise ValueError("selectionPolicy must be an object")
    if policy.get("mergedOnly") is not True:
        raise ValueError("selectionPolicy.mergedOnly must be true")
    if policy.get("reviewCommentsRequired") is not True:
        raise ValueError("selectionPolicy.reviewCommentsRequired must be true")
    if policy.get("snapshotRule") != "exact_original_review_commit":
        raise ValueError(
            "selectionPolicy.snapshotRule must be exact_original_review_commit"
        )
    mainline_cutoff = policy.get("mainlineCutoffSha")
    if paper_ready:
        mainline_cutoff = require_full_sha(
            mainline_cutoff,
            "selectionPolicy.mainlineCutoffSha",
        )

    provenance_value = payload.get("provenance")
    source_archive_digest = (
        provenance_value.get("sourceArchiveDigest")
        if isinstance(provenance_value, Mapping)
        else None
    )
    thread_evidence_digest = (
        provenance_value.get("threadEvidenceDigest")
        if isinstance(provenance_value, Mapping)
        else None
    )

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty array")
    if required_cases is not None and required_cases != 50:
        raise ValueError("the Magento benchmark requires exactly 50 cases")
    expected_count = 50
    if (
        _integer(
            policy.get("requiredCases"),
            "selectionPolicy.requiredCases",
            1,
        )
        != expected_count
    ):
        raise ValueError("selectionPolicy.requiredCases must be 50")
    if len(cases) != expected_count:
        raise ValueError(
            f"corpus must contain exactly {expected_count} cases, found {len(cases)}"
        )
    expected_bands = {
        "small": {"minFiles": 3, "maxFiles": 10},
        "medium": {"minFiles": 11, "maxFiles": 30},
        "large": {"minFiles": 31, "maxFiles": 80},
    }
    if policy.get("bands") != expected_bands:
        raise ValueError(
            "selectionPolicy.bands must be small 3-10, medium 11-30, "
            "and large 31-80"
        )
    partition_policy = policy.get("partitionPolicy")
    partition_policy_preserved = (
        partition_policy == EXPECTED_PARTITION_POLICY
    )
    if partition_policy is not None and not partition_policy_preserved:
        raise ValueError(
            "selectionPolicy.partitionPolicy must preserve the deterministic "
            "label-blind 30-development/20-sealed split"
        )
    if paper_ready and not partition_policy_preserved:
        raise ValueError(
            "paper-ready corpus must preserve selectionPolicy.partitionPolicy"
        )

    case_ids: set[str] = set()
    pr_numbers: set[int] = set()
    comment_ids: set[int] = set()
    gold_ids: set[str] = set()
    band_counts: Counter[str] = Counter()
    partition_counts: Counter[str] = Counter()
    golden_count = 0
    provisional_count = 0
    paper_ready_count = 0

    for index, case in enumerate(cases):
        field = f"cases[{index}]"
        if not isinstance(case, Mapping):
            raise ValueError(f"{field} must be an object")
        case_id = require_text(case.get("caseId"), f"{field}.caseId")
        if not SAFE_CASE_ID.fullmatch(case_id):
            raise ValueError(f"{field}.caseId is not a safe artifact identifier")
        if case_id in case_ids:
            raise ValueError(f"duplicate caseId: {case_id}")
        case_ids.add(case_id)
        if case.get("partition") not in {"development", "sealed"}:
            raise ValueError(f"{field}.partition must be development or sealed")
        partition_counts[str(case["partition"])] += 1

        source = case.get("sourcePr")
        if not isinstance(source, Mapping):
            raise ValueError(f"{field}.sourcePr must be an object")
        number = _integer(source.get("number"), f"{field}.sourcePr.number", 1)
        if number in pr_numbers:
            raise ValueError(f"pull request {number} appears more than once")
        pr_numbers.add(number)
        if source.get("url") != f"https://github.com/magento/magento2/pull/{number}":
            raise ValueError(f"{field}.sourcePr.url is not canonical")
        require_text(source.get("title"), f"{field}.sourcePr.title")
        require_text(source.get("author"), f"{field}.sourcePr.author")
        if source.get("baseRef") != default_branch:
            raise ValueError(f"{field} was not merged to {default_branch}")
        _timestamp(source.get("mergedAt"), f"{field}.sourcePr.mergedAt")
        require_full_sha(
            source.get("finalHeadSha"),
            f"{field}.sourcePr.finalHeadSha",
        )
        require_full_sha(
            source.get("mergeCommitSha"),
            f"{field}.sourcePr.mergeCommitSha",
        )
        _integer(
            source.get("changedFiles"),
            f"{field}.sourcePr.changedFiles",
            1,
        )
        if paper_ready:
            _validate_source_pr_evidence(source, f"{field}.sourcePr")
        # The measured unit is the frozen review round B..H. A PR may change
        # size after H while reviewers address comments, so its final GitHub
        # file count is provenance rather than a selection-band input.

        snapshot = case.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise ValueError(f"{field}.snapshot must be an object")
        base_sha = require_full_sha(
            snapshot.get("baseSha"), f"{field}.snapshot.baseSha"
        )
        head_sha = require_full_sha(
            snapshot.get("headSha"), f"{field}.snapshot.headSha"
        )
        if base_sha == head_sha:
            raise ValueError(f"{field} has an empty base/head snapshot")
        _timestamp(snapshot.get("reviewedAt"), f"{field}.snapshot.reviewedAt")
        file_count = _integer(
            snapshot.get("fileCount"),
            f"{field}.snapshot.fileCount",
            1,
        )
        changed_paths = _string_list(
            snapshot.get("changedPaths"),
            f"{field}.snapshot.changedPaths",
        )
        if len(changed_paths) != file_count:
            raise ValueError(f"{field}.snapshot.fileCount does not match changedPaths")
        if changed_paths != sorted(changed_paths):
            raise ValueError(f"{field}.snapshot.changedPaths must be sorted")
        observed_band = _size_band(file_count, policy)
        if case.get("sizeBand") != observed_band:
            raise ValueError(f"{field}.sizeBand must be {observed_band}")
        band_counts[observed_band] += 1
        _sha256(snapshot.get("diffSha256"), f"{field}.snapshot.diffSha256")
        if snapshot.get("derivation") not in {
            "merge_base_with_merge_first_parent",
            "recorded_pull_base",
        }:
            raise ValueError(f"{field}.snapshot.derivation is unsupported")

        golden = case.get("goldenComments")
        if not isinstance(golden, list) or not golden:
            raise ValueError(f"{field}.goldenComments must be non-empty")
        graphql_thread_archive = case.get("graphqlThreadArchive")
        if graphql_thread_archive is not None:
            try:
                validate_graphql_thread_archive(
                    graphql_thread_archive,
                    pull_request=number,
                )
            except ValueError as exc:
                raise ValueError(
                    f"{field}.graphqlThreadArchive is invalid: {exc}"
                ) from exc
        for gold_index, comment in enumerate(golden):
            gold_field = f"{field}.goldenComments[{gold_index}]"
            _validate_golden(
                comment,
                gold_field,
                case=case,
                snapshot=snapshot,
                paper_ready=paper_ready,
                source_archive_digest=source_archive_digest,
                thread_evidence_digest=thread_evidence_digest,
                graphql_thread_archive=(
                    graphql_thread_archive
                    if isinstance(graphql_thread_archive, Mapping)
                    else None
                ),
            )
            comment_id = comment["sourceCommentId"]
            gold_id = comment["id"]
            if comment_id in comment_ids:
                raise ValueError(f"duplicate source review comment: {comment_id}")
            if gold_id in gold_ids:
                raise ValueError(f"duplicate golden issue id: {gold_id}")
            comment_ids.add(comment_id)
            gold_ids.add(gold_id)
            golden_count += 1
            if comment["adjudication"]["status"] != "accepted":
                provisional_count += 1
            if _paper_ready_golden(comment):
                paper_ready_count += 1

        if paper_ready:
            _validate_source_archive_case_evidence(
                case,
                field,
                source_archive_digest=source_archive_digest,
            )

        replay = case.get("replay")
        if not isinstance(replay, Mapping):
            raise ValueError(f"{field}.replay must be an object")
        require_text(replay.get("baseRef"), f"{field}.replay.baseRef")
        require_text(replay.get("headRef"), f"{field}.replay.headRef")
        if replay["baseRef"] == replay["headRef"]:
            raise ValueError(f"{field}.replay refs must be distinct")
        if paper_ready:
            _validate_ancestry_evidence(
                case,
                field,
                mainline_cutoff_sha=str(mainline_cutoff),
            )

    required_bands = policy.get("requiredBands")
    if not isinstance(required_bands, Mapping):
        raise ValueError("selectionPolicy.requiredBands must be an object")
    for band in VALID_BANDS:
        minimum = _integer(
            required_bands.get(band, 1),
            f"selectionPolicy.requiredBands.{band}",
            0,
        )
        if band_counts[band] < minimum:
            raise ValueError(
                f"corpus has {band_counts[band]} {band} cases; requires {minimum}"
            )
    expected_partition_counts = {
        "development": EXPECTED_PARTITION_POLICY["developmentCases"],
        "sealed": EXPECTED_PARTITION_POLICY["sealedCases"],
    }
    observed_partition_counts = {
        name: partition_counts[name]
        for name in ("development", "sealed")
    }
    if observed_partition_counts != expected_partition_counts:
        raise ValueError(
            "corpus partition must contain exactly 30 development and "
            "20 sealed cases"
        )

    provenance = provenance_value
    if not isinstance(provenance, Mapping):
        raise ValueError("provenance must be an object")
    require_text(provenance.get("collector"), "provenance.collector")
    _timestamp(provenance.get("collectedAt"), "provenance.collectedAt")
    require_text(provenance.get("githubApiVersion"), "provenance.githubApiVersion")
    require_text(provenance.get("gitVersion"), "provenance.gitVersion")
    if provenance.get("diffPolicy") != {
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
    }:
        raise ValueError("provenance.diffPolicy is not the deterministic policy")
    _sha256(provenance.get("selectionDigest"), "provenance.selectionDigest")
    selection_file_sha256 = provenance.get("selectionFileSha256")
    if selection_file_sha256 is not None:
        _sha256(
            selection_file_sha256,
            "provenance.selectionFileSha256",
        )
    if paper_ready and selection_file_sha256 is None:
        raise ValueError(
            "paper-ready corpus has no released selection file digest"
        )
    if provenance.get("reviewBodiesRetainedVerbatim") is not True:
        raise ValueError("review comment bodies must be retained verbatim")
    source_archive_digest = provenance.get("sourceArchiveDigest")
    if source_archive_digest is not None:
        _sha256(
            source_archive_digest,
            "provenance.sourceArchiveDigest",
        )
    if paper_ready and source_archive_digest is None:
        raise ValueError(
            "paper-ready corpus has no bound REST source archive"
        )
    thread_evidence_digest = provenance.get("threadEvidenceDigest")
    if thread_evidence_digest is not None:
        _sha256(
            thread_evidence_digest,
            "provenance.threadEvidenceDigest",
        )
    if paper_ready and thread_evidence_digest is None:
        raise ValueError(
            "paper-ready corpus has no bound raw GraphQL thread archive"
        )

    digest_payload = dict(payload)
    declared_digest = digest_payload.pop("corpusDigest", None)
    computed_digest = sha256_json(digest_payload)
    if declared_digest != computed_digest:
        raise ValueError(
            "corpusDigest mismatch; regenerate after modifying corpus data"
        )

    return {
        "corpusId": corpus_id,
        "corpusDigest": computed_digest,
        "cases": len(cases),
        "goldenComments": golden_count,
        "provisionalComments": provisional_count,
        "paperReady": paper_ready_count == golden_count,
        "sizeBands": dict(sorted(band_counts.items())),
        "partitionCounts": observed_partition_counts,
        "partitionPolicyPreserved": partition_policy_preserved,
    }


def attach_corpus_digest(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.pop("corpusDigest", None)
    result["corpusDigest"] = sha256_json(result)
    return result
