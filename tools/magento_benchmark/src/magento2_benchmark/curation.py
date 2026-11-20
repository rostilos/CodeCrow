from __future__ import annotations

import hashlib
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .collect import SELECTION_KIND
from .corpus import (
    VALID_CATEGORIES,
    VALID_SEVERITIES,
    decision_binding_digest,
)
from .github import GitHubClient
from .path_transition import (
    resolve_path_transition,
    validate_path_transition_evidence,
)
from .thread_provenance import (
    REVIEW_THREADS_QUERY,
    build_graphql_thread_archive,
    build_rest_review_thread_evidence,
    build_review_thread_binding,
    rest_review_comment_anchor,
    validate_graphql_thread_archive,
    validate_review_thread_binding,
)
from .util import (
    hermetic_git_environment,
    read_json,
    require_full_sha,
    require_text,
    run,
    sha256_json,
    sha256_text,
    validate_git_evidence_repository,
    write_json,
)


DRAFT_KIND = "codecrow-magento2-review-corpus-draft"
DRAFT_STATUS = "provisional_unscored_anchor_validated"
PACKET_KIND = "codecrow-magento2-curation-packet"
DECISIONS_KIND = "codecrow-magento2-curation-decisions"
DECISIONS_TEMPLATE_KIND = "codecrow-magento2-curation-decisions-template"
THREAD_EVIDENCE_KIND = "codecrow-magento2-review-thread-evidence"
DRAFT_SOURCE_ARCHIVE_KIND = "codecrow-magento2-draft-source-archive"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _timestamp(value: Any, field: str) -> str:
    text = require_text(value, field)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    return text


def _validate_draft(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("corpus draft must be an object")
    if value.get("kind") != DRAFT_KIND:
        raise ValueError(f"corpus draft kind must be {DRAFT_KIND}")
    if value.get("repository") != "magento/magento2":
        raise ValueError("corpus draft repository must be magento/magento2")
    if value.get("base_branch") != "2.4-develop":
        raise ValueError("corpus draft base branch must be 2.4-develop")
    if value.get("status") != DRAFT_STATUS:
        raise ValueError(f"corpus draft status must be {DRAFT_STATUS}")
    cases = value.get("cases")
    if not isinstance(cases, list) or len(cases) != 50:
        raise ValueError("corpus draft must contain exactly 50 cases")
    numbers: set[int] = set()
    case_ids: set[str] = set()
    comment_ids: set[int] = set()
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("corpus draft case must be an object")
        number = case.get("pr_number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError("draft PR number must be a positive integer")
        if number in numbers:
            raise ValueError(f"duplicate draft PR: {number}")
        numbers.add(number)
        case_id = require_text(case.get("case_id"), "draft.case_id")
        if case_id in case_ids:
            raise ValueError(f"duplicate draft case ID: {case_id}")
        case_ids.add(case_id)
        if case.get("repository") != "magento/magento2":
            raise ValueError(f"PR {number} repository is not magento/magento2")
        if case.get("base_branch") != "2.4-develop":
            raise ValueError(f"PR {number} does not target 2.4-develop")
        if case.get("pr_url") != (
            f"https://github.com/magento/magento2/pull/{number}"
        ):
            raise ValueError(f"PR {number} URL is not canonical")
        author = require_text(case.get("pr_author"), f"PR {number}.pr_author")
        require_text(case.get("title"), f"PR {number}.title")
        require_text(case.get("merged_at"), f"PR {number}.merged_at")
        require_full_sha(case.get("benchmark_base_sha"), "benchmark_base_sha")
        head = require_full_sha(case.get("benchmark_head_sha"), "benchmark_head_sha")
        require_full_sha(case.get("final_head_sha"), "final_head_sha")
        require_full_sha(case.get("merge_commit_sha"), "merge_commit_sha")
        require_full_sha(
            case.get("merge_first_parent_sha"),
            "merge_first_parent_sha",
        )
        count = case.get("checkpoint_changed_files")
        if isinstance(count, bool) or not isinstance(count, int) or not 3 <= count <= 80:
            raise ValueError(f"PR {number} draft file count must be 3..80")
        paths = case.get("changed_files")
        if (
            not isinstance(paths, list)
            or len(paths) != count
            or any(not isinstance(path, str) or not path for path in paths)
            or paths != sorted(paths)
            or len(paths) != len(set(paths))
        ):
            raise ValueError(
                f"PR {number} changed_files must be a sorted unique snapshot manifest"
            )
        expected_band = (
            "small" if count <= 10 else "medium" if count <= 30 else "large"
        )
        if case.get("size_band") != expected_band:
            raise ValueError(f"PR {number} size band must be {expected_band}")
        comments = case.get("gold_comments")
        if not isinstance(comments, list) or not comments:
            raise ValueError(f"PR {number} has no draft comments")
        for comment in comments:
            if not isinstance(comment, Mapping):
                raise ValueError(f"PR {number} draft comment must be an object")
            comment_id = comment.get("id")
            if (
                isinstance(comment_id, bool)
                or not isinstance(comment_id, int)
                or comment_id < 1
                or comment_id in comment_ids
            ):
                raise ValueError("draft comment IDs must be unique positive integers")
            comment_ids.add(comment_id)
            review_id = comment.get("review_id")
            if (
                isinstance(review_id, bool)
                or not isinstance(review_id, int)
                or review_id < 1
            ):
                raise ValueError(
                    f"draft comment {comment_id} review_id must be a "
                    "positive integer"
                )
            if comment.get("original_commit_id") != head:
                raise ValueError(
                    f"PR {number} comment {comment_id} targets another checkpoint"
                )
            require_full_sha(
                comment.get("api_commit_id"),
                f"comment {comment_id}.api_commit_id",
            )
            if comment.get("side") != "RIGHT" or comment.get("thread_root") is not True:
                raise ValueError(
                    f"PR {number} comment {comment_id} is not a root RIGHT comment"
                )
            raw_current_line = comment.get("raw_current_line")
            raw_original_line = comment.get("raw_original_line")
            raw_current_start_line = comment.get("raw_current_start_line")
            raw_original_start_line = comment.get("raw_original_start_line")
            raw_start_side = comment.get("raw_start_side")
            for raw_name, raw_value, nullable in (
                ("raw_current_line", raw_current_line, True),
                ("raw_original_line", raw_original_line, False),
                ("raw_current_start_line", raw_current_start_line, True),
                ("raw_original_start_line", raw_original_start_line, True),
            ):
                if (
                    (raw_value is None and not nullable)
                    or isinstance(raw_value, bool)
                    or (
                        raw_value is not None
                        and (not isinstance(raw_value, int) or raw_value < 1)
                    )
                ):
                    raise ValueError(
                        f"comment {comment_id}.{raw_name} is invalid"
                    )
            if (
                comment.get("line") != raw_original_line
                or comment.get("start_line") != raw_original_start_line
            ):
                raise ValueError(
                    f"comment {comment_id} normalized H line range does not "
                    "match its raw original coordinates"
                )
            if raw_start_side not in {None, "LEFT", "RIGHT"} or (
                (raw_original_start_line is None)
                is not (raw_start_side is None)
            ):
                raise ValueError(
                    f"comment {comment_id} original range/start side is invalid"
                )
            if (
                raw_current_start_line is not None
                and (
                    raw_current_line is None
                    or raw_original_start_line is None
                )
            ):
                raise ValueError(
                    f"comment {comment_id} has a current start without a "
                    "current end or original range"
                )
            reviewer = require_text(
                comment.get("reviewer"),
                f"comment {comment_id}.reviewer",
            )
            if reviewer.casefold() == author.casefold():
                raise ValueError(f"comment {comment_id} is by the PR author")
            require_text(comment.get("body"), f"comment {comment_id}.body")
            comment_url = require_text(
                comment.get("url"),
                f"comment {comment_id}.url",
            )
            if comment_url != (
                "https://github.com/magento/magento2/pull/"
                f"{number}#discussion_r{comment_id}"
            ):
                raise ValueError(
                    f"comment {comment_id} URL is not canonical for PR {number}"
                )
            _timestamp(
                comment.get("created_at"),
                f"comment {comment_id}.created_at",
            )
            _timestamp(
                comment.get("updated_at"),
                f"comment {comment_id}.updated_at",
            )
            path = require_text(
                comment.get("path"),
                f"comment {comment_id}.path",
            )
            if path not in paths:
                raise ValueError(
                    f"comment {comment_id} path is outside the checkpoint diff"
                )
            if comment.get("path_status_at_checkpoint") not in {"A", "M", "R"}:
                raise ValueError(
                    f"comment {comment_id} targets a deleted/unknown path"
                )
            if comment.get("path_changed_before_merge") is not True:
                raise ValueError(
                    f"comment {comment_id} has no later path-change evidence"
                )
            anchor = comment.get("anchor_validation")
            if not isinstance(anchor, Mapping) or any(
                anchor.get(field) is not True
                for field in (
                    "side_is_right",
                    "path_in_checkpoint_diff",
                    "path_not_deleted",
                    "checkpoint_blob_resolved",
                    "diff_hunk_present",
                    "original_line_present",
                    "exact_line_content_match",
                )
            ):
                raise ValueError(
                    f"comment {comment_id} does not have a validated checkpoint anchor"
                )
            if anchor.get("validation_mode") not in {
                "raw_original_line_exact_content_match",
                (
                    "raw_original_position_hunk_terminal_with_adjacent_"
                    "checkpoint_line"
                ),
            }:
                raise ValueError(
                    f"comment {comment_id} has an unsupported anchor mapping"
                )
            if comment.get("gold_status") != "provisional":
                raise ValueError(
                    f"draft comment {comment_id} must remain provisional"
                )
            if (comment.get("adjudication") or {}).get("include_in_scoring") is not False:
                raise ValueError(
                    f"draft comment {comment_id} must remain excluded from scoring"
                )
    return value


def validate_draft_file(path: Path) -> dict[str, Any]:
    draft = _validate_draft(read_json(path))
    bands = Counter(str(case["size_band"]) for case in draft["cases"])
    return {
        "status": draft["status"],
        "paperReady": False,
        "scoringEnabled": False,
        "cases": len(draft["cases"]),
        "reviewComments": sum(
            len(case["gold_comments"]) for case in draft["cases"]
        ),
        "sizeBands": dict(sorted(bands.items())),
        "fileSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "canonicalDigest": sha256_json(draft),
    }


def _review_comment_matches_source(
    comment: Mapping[str, Any],
    source: Mapping[str, Any],
) -> bool:
    try:
        anchor = rest_review_comment_anchor(
            comment,
            field=f"review comment {source.get('id')}",
        )
    except ValueError:
        return False
    user = (
        comment.get("user")
        if isinstance(comment.get("user"), Mapping)
        else {}
    )
    if (
        comment.get("id") != source.get("id")
        or comment.get("pull_request_review_id") != source.get("review_id")
        or user.get("type") != "User"
        or user.get("login") != source.get("reviewer")
        or comment.get("body") != source.get("body")
        or comment.get("author_association")
        != source.get("author_association")
        or comment.get("created_at") != source.get("created_at")
        or comment.get("updated_at") != source.get("updated_at")
        or comment.get("html_url") != source.get("url")
        or comment.get("original_commit_id")
        != source.get("original_commit_id")
        or comment.get("path") != source.get("path")
        or comment.get("diff_hunk") != source.get("diff_hunk")
        or anchor["side"] != source.get("side")
        or anchor["originalLine"] != source.get("line")
        or anchor["originalStartLine"] != source.get("start_line")
        or comment.get("in_reply_to_id") is not None
    ):
        return False
    optional_raw_fields = {
        "api_commit_id": "commit_id",
        "raw_current_line": "line",
        "raw_original_line": "original_line",
        "raw_position": "position",
        "raw_original_position": "original_position",
        "raw_current_start_line": "start_line",
        "raw_original_start_line": "original_start_line",
        "raw_start_side": "start_side",
    }
    return all(
        source_key not in source
        or comment.get(api_key) == source.get(source_key)
        for source_key, api_key in optional_raw_fields.items()
    )


def _submitted_review_matches_source(
    review: Mapping[str, Any] | None,
    source: Mapping[str, Any],
    *,
    pull_request: int,
) -> bool:
    if not isinstance(review, Mapping):
        return False
    user = (
        review.get("user")
        if isinstance(review.get("user"), Mapping)
        else {}
    )
    try:
        _timestamp(
            review.get("submitted_at"),
            f"review {source.get('review_id')}.submitted_at",
        )
        require_full_sha(
            review.get("commit_id"),
            f"review {source.get('review_id')}.commit_id",
        )
    except ValueError:
        return False
    return (
        review.get("id") == source.get("review_id")
        and user.get("type") == "User"
        and user.get("login") == source.get("reviewer")
        and review.get("state")
        in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"}
        and review.get("pull_request_url")
        == (
            "https://api.github.com/repos/magento/magento2/pulls/"
            f"{pull_request}"
        )
    )


def _records_by_positive_id(
    records: list[Mapping[str, Any]],
    *,
    label: str,
) -> dict[int, Mapping[str, Any]]:
    indexed: dict[int, Mapping[str, Any]] = {}
    for record in records:
        identifier = record.get("id")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier < 1
            or identifier in indexed
        ):
            raise ValueError(f"{label} IDs must be unique positive integers")
        indexed[identifier] = record
    return indexed


def archive_draft_sources(
    client: GitHubClient,
    *,
    draft_path: Path,
    output: Path,
) -> dict[str, Any]:
    """Archive exact GitHub REST inputs and fail on any draft-source drift."""

    draft = _validate_draft(read_json(draft_path))
    cases = []
    for case in draft["cases"]:
        number = int(case["pr_number"])
        pull = client.get(f"/repos/magento/magento2/pulls/{number}")
        comments = list(
            client.paginate(
                f"/repos/magento/magento2/pulls/{number}/comments"
            )
        )
        reviews = list(
            client.paginate(
                f"/repos/magento/magento2/pulls/{number}/reviews"
            )
        )
        if not isinstance(pull, Mapping):
            raise ValueError(f"GitHub returned invalid PR {number}")
        pull_user = (
            pull.get("user")
            if isinstance(pull.get("user"), Mapping)
            else {}
        )
        pull_base = (
            pull.get("base")
            if isinstance(pull.get("base"), Mapping)
            else {}
        )
        pull_head = (
            pull.get("head")
            if isinstance(pull.get("head"), Mapping)
            else {}
        )
        if (
            pull.get("number") != number
            or pull.get("state") != "closed"
            or pull.get("merged_at") != case.get("merged_at")
            or pull.get("title") != case.get("title")
            or pull.get("merge_commit_sha") != case.get("merge_commit_sha")
            or pull_user.get("login") != case.get("pr_author")
            or pull_base.get("ref") != case.get("base_branch")
            or pull_head.get("sha") != case.get("final_head_sha")
        ):
            raise ValueError(f"GitHub PR {number} drifted from the draft")
        if (
            any(not isinstance(comment, Mapping) for comment in comments)
            or any(not isinstance(review, Mapping) for review in reviews)
        ):
            raise ValueError(f"GitHub returned malformed review data for PR {number}")
        comments_by_id = _records_by_positive_id(
            comments,
            label=f"PR {number} review comment",
        )
        reviews_by_id = _records_by_positive_id(
            reviews,
            label=f"PR {number} submitted review",
        )
        selected_comments = []
        for source in case["gold_comments"]:
            comment_id = int(source["id"])
            comment = comments_by_id.get(comment_id)
            if (
                not isinstance(comment, Mapping)
                or not _review_comment_matches_source(comment, source)
                or not _submitted_review_matches_source(
                    reviews_by_id.get(int(source["review_id"])),
                    source,
                    pull_request=number,
                )
            ):
                raise ValueError(
                    f"PR {number} comment {comment_id} drifted from the draft"
                )
            selected_comments.append(dict(comment))
        case_record = {
            "pullRequest": number,
            "pull": dict(pull),
            "selectedComments": selected_comments,
            "allReviewComments": comments,
            "submittedReviews": reviews,
        }
        case_record["caseEvidenceDigest"] = sha256_json(case_record)
        cases.append(case_record)
    result = {
        "kind": DRAFT_SOURCE_ARCHIVE_KIND,
        "generatedAt": _now(),
        "githubApiVersion": "2022-11-28",
        "draftFileSha256": hashlib.sha256(draft_path.read_bytes()).hexdigest(),
        "draftSha256": sha256_json(draft),
        "cases": cases,
    }
    result["archiveDigest"] = sha256_json(result)
    write_json(output, result)
    return result


def _source_archive_evidence(
    path: Path | None,
    *,
    draft: Mapping[str, Any],
    draft_digest: str,
    draft_file_sha256: str,
) -> tuple[
    str | None,
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
]:
    if path is None:
        return None, {}, {}
    value = read_json(path)
    if (
        not isinstance(value, Mapping)
        or value.get("kind") != DRAFT_SOURCE_ARCHIVE_KIND
    ):
        raise ValueError("source archive kind is invalid")
    if value.get("githubApiVersion") != "2022-11-28":
        raise ValueError("source archive GitHub API version is invalid")
    _timestamp(value.get("generatedAt"), "source archive generatedAt")
    digest_value = dict(value)
    declared = digest_value.pop("archiveDigest", None)
    if (
        not isinstance(declared, str)
        or declared != sha256_json(digest_value)
    ):
        raise ValueError("source archive digest mismatch")
    if (
        value.get("draftSha256") != draft_digest
        or value.get("draftFileSha256") != draft_file_sha256
    ):
        raise ValueError("source archive belongs to another draft")
    records = value.get("cases")
    if not isinstance(records, list):
        raise ValueError("source archive cases must be an array")
    by_pull: dict[int, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("source archive case must be an object")
        number = record.get("pullRequest")
        if (
            isinstance(number, bool)
            or not isinstance(number, int)
            or number in by_pull
        ):
            raise ValueError("source archive pull identities are invalid")
        case_digest = dict(record)
        case_declared = case_digest.pop("caseEvidenceDigest", None)
        if case_declared != sha256_json(case_digest):
            raise ValueError(
                f"source archive PR {number} evidence digest mismatch"
            )
        by_pull[number] = record

    expected_pulls = {int(case["pr_number"]) for case in draft["cases"]}
    if set(by_pull) != expected_pulls:
        raise ValueError("source archive does not cover the exact draft PR set")
    evidence_by_pull: dict[int, dict[str, Any]] = {}
    rest_threads_by_root: dict[int, dict[str, Any]] = {}
    for case in draft["cases"]:
        number = int(case["pr_number"])
        record = by_pull[number]
        pull = record.get("pull")
        selected = record.get("selectedComments")
        all_comments = record.get("allReviewComments")
        reviews = record.get("submittedReviews")
        if (
            not isinstance(pull, Mapping)
            or not isinstance(selected, list)
            or not isinstance(all_comments, list)
            or not isinstance(reviews, list)
            or any(not isinstance(item, Mapping) for item in selected)
            or any(not isinstance(item, Mapping) for item in all_comments)
            or any(not isinstance(item, Mapping) for item in reviews)
        ):
            raise ValueError(f"source archive PR {number} is malformed")
        pull_user = (
            pull.get("user")
            if isinstance(pull.get("user"), Mapping)
            else {}
        )
        pull_base = (
            pull.get("base")
            if isinstance(pull.get("base"), Mapping)
            else {}
        )
        pull_head = (
            pull.get("head")
            if isinstance(pull.get("head"), Mapping)
            else {}
        )
        if (
            pull.get("number") != number
            or pull.get("state") != "closed"
            or pull.get("merged_at") != case.get("merged_at")
            or pull.get("title") != case.get("title")
            or pull.get("merge_commit_sha") != case.get("merge_commit_sha")
            or pull_user.get("login") != case.get("pr_author")
            or pull_base.get("ref") != case.get("base_branch")
            or pull_head.get("sha") != case.get("final_head_sha")
        ):
            raise ValueError(f"source archive PR {number} drifted from the draft")
        all_by_id = _records_by_positive_id(
            all_comments,
            label=f"source archive PR {number} review comment",
        )
        selected_by_id = _records_by_positive_id(
            selected,
            label=f"source archive PR {number} selected comment",
        )
        expected_ids = {
            int(comment["id"]) for comment in case["gold_comments"]
        }
        if (
            set(selected_by_id) != expected_ids
            or not expected_ids.issubset(all_by_id)
            or any(
                selected_by_id[comment_id] != all_by_id[comment_id]
                for comment_id in expected_ids
            )
        ):
            raise ValueError(
                f"source archive PR {number} selected comments drifted"
            )
        reviews_by_id = _records_by_positive_id(
            reviews,
            label=f"source archive PR {number} submitted review",
        )
        for source in case["gold_comments"]:
            comment_id = int(source["id"])
            comment = selected_by_id[comment_id]
            if (
                not _review_comment_matches_source(comment, source)
                or not _submitted_review_matches_source(
                    reviews_by_id.get(int(source["review_id"])),
                    source,
                    pull_request=number,
                )
            ):
                raise ValueError(
                    f"source archive PR {number} comment {comment_id} drifted"
                )
        referenced_review_ids = {
            int(source["review_id"]) for source in case["gold_comments"]
        }
        evidence_by_pull[number] = {
            "archiveDigest": declared,
            "caseEvidenceDigest": record["caseEvidenceDigest"],
            "pullResponseSha256": sha256_json(pull),
            "selectedCommentResponseSha256": {
                str(comment_id): sha256_json(selected_by_id[comment_id])
                for comment_id in sorted(expected_ids)
            },
            "submittedReviewResponseSha256": {
                str(review_id): sha256_json(reviews_by_id[review_id])
                for review_id in sorted(referenced_review_ids)
            },
        }
        for source in case["gold_comments"]:
            comment_id = int(source["id"])
            if comment_id in rest_threads_by_root:
                raise ValueError(
                    "source archive review comment IDs must be globally unique"
                )
            rest_threads_by_root[comment_id] = (
                build_rest_review_thread_evidence(
                    pull_request=number,
                    root_comment_id=comment_id,
                    all_comments=all_comments,
                    all_submitted_reviews=reviews,
                    source_archive_digest=str(declared),
                    source_archive_case_evidence_digest=str(
                        record["caseEvidenceDigest"]
                    ),
                )
            )
    return declared, evidence_by_pull, rest_threads_by_root


def _git_show(
    repository: Path,
    revision: str,
    path: str,
    *,
    git_env: Mapping[str, str],
) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), "show", f"{revision}:{path}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        errors="replace",
        check=False,
        env=dict(git_env),
    )
    return completed.stdout if completed.returncode == 0 else ""


def _source_window(source: str, line: int, radius: int = 30) -> dict[str, Any]:
    lines = source.splitlines()
    if not lines:
        return {
            "startLine": None,
            "endLine": None,
            "content": "",
        }
    anchor = min(max(line, 1), len(lines))
    start = max(1, anchor - radius)
    end = min(len(lines), anchor + radius)
    return {
        "startLine": start,
        "endLine": end,
        "content": "\n".join(
            f"{number:>6} {lines[number - 1]}"
            for number in range(start, end + 1)
        ),
    }


SOURCE_EVIDENCE_FIELDS = {
    "available",
    "path",
    "blobOid",
    "startLine",
    "endLine",
    "content",
}


def _source_evidence(
    source: str | None,
    *,
    path: str | None,
    blob_oid: str | None,
    line: int,
) -> dict[str, Any]:
    if source is None:
        return {
            "available": False,
            "path": None,
            "blobOid": None,
            "startLine": None,
            "endLine": None,
            "content": "",
        }
    window = _source_window(source, line)
    return {
        "available": True,
        "path": path,
        "blobOid": blob_oid,
        **window,
    }


def _validate_source_evidence(
    value: Any,
    *,
    available: bool,
    path: str | None,
    blob_oid: str | None,
    field: str,
) -> None:
    if not isinstance(value, Mapping) or set(value) != SOURCE_EVIDENCE_FIELDS:
        raise ValueError(f"{field} fields are invalid")
    if value.get("available") is not available:
        raise ValueError(f"{field} availability is invalid")
    if value.get("path") != path or value.get("blobOid") != blob_oid:
        raise ValueError(f"{field} path/blob identity drift")
    content = value.get("content")
    if not isinstance(content, str):
        raise ValueError(f"{field}.content must be a string")
    start = value.get("startLine")
    end = value.get("endLine")
    if available:
        require_text(path, f"{field}.path")
        require_full_sha(blob_oid, f"{field}.blobOid")
        if start is None or end is None:
            if start is not None or end is not None or content:
                raise ValueError(f"{field} empty-source window is invalid")
        elif (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 1
            or end < start
        ):
            raise ValueError(f"{field} source window is invalid")
    elif (
        path is not None
        or blob_oid is not None
        or start is not None
        or end is not None
        or content
    ):
        raise ValueError(f"{field} deleted-source sentinel is invalid")


def _curation_path_evidence(
    repository: Path,
    *,
    checkpoint_sha: str,
    final_sha: str,
    source_path: str,
    source_line: int,
    git_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if git_env is None:
        git_env = hermetic_git_environment()
    checkpoint_source = _git_show(
        repository,
        checkpoint_sha,
        source_path,
        git_env=git_env,
    )
    transition, path_diff = resolve_path_transition(
        repository,
        checkpoint_sha=checkpoint_sha,
        final_sha=final_sha,
        source_path=source_path,
        git_env=git_env,
    )
    final_path = transition["finalPath"]
    final_source = (
        _git_show(
            repository,
            final_sha,
            str(final_path),
            git_env=git_env,
        )
        if final_path is not None
        else None
    )
    return {
        "pathTransition": transition,
        "checkpointSource": _source_evidence(
            checkpoint_source,
            path=source_path,
            blob_oid=transition["checkpointBlobOid"],
            line=source_line,
        ),
        "finalSource": _source_evidence(
            final_source,
            path=str(final_path) if final_path is not None else None,
            blob_oid=transition["finalBlobOid"],
            line=source_line,
        ),
        "checkpointToFinalPathDiff": path_diff,
        "checkpointToFinalPathDiffSha256": transition["diffSha256"],
    }


def _graphql_review_threads(
    client: GitHubClient,
    *,
    pull_request: int,
) -> tuple[dict[int, Mapping[str, Any]], dict[str, Any] | None]:
    if not client.token and not client.offline:
        return {}, None
    after: str | None = None
    by_root: dict[int, Mapping[str, Any]] = {}
    raw_pages: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    while True:
        variables = {
            "owner": "magento",
            "name": "magento2",
            "number": pull_request,
            "after": after,
        }
        response = client.graphql(REVIEW_THREADS_QUERY, variables)
        if not isinstance(response, Mapping):
            raise ValueError(
                f"GitHub GraphQL returned a non-object for PR {pull_request}"
            )
        raw_pages.append((variables, response))
        repository = (response.get("data") or {}).get("repository")
        pull = (
            repository.get("pullRequest")
            if isinstance(repository, Mapping)
            else None
        )
        connection = (
            pull.get("reviewThreads") if isinstance(pull, Mapping) else None
        )
        if not isinstance(connection, Mapping):
            raise ValueError(
                f"GitHub GraphQL returned no review threads for PR "
                f"{pull_request}"
            )
        for thread in connection.get("nodes") or []:
            if not isinstance(thread, Mapping):
                continue
            comments = thread.get("comments")
            nodes = (
                comments.get("nodes")
                if isinstance(comments, Mapping)
                else None
            )
            root = (
                nodes[0]
                if isinstance(nodes, list)
                and nodes
                and isinstance(nodes[0], Mapping)
                else None
            )
            root_id = root.get("databaseId") if root else None
            if isinstance(root_id, int):
                by_root[root_id] = thread
        page_info = connection.get("pageInfo")
        if not isinstance(page_info, Mapping) or page_info.get(
            "hasNextPage"
        ) is not True:
            break
        after = page_info.get("endCursor")
        if not isinstance(after, str) or not after:
            raise ValueError("GitHub GraphQL thread pagination has no cursor")
    archive = build_graphql_thread_archive(
        pull_request=pull_request,
        pages=raw_pages,
        endpoint=f"{client.api_url}/graphql",
    )
    return by_root, archive


def hydrate_review_threads(
    client: GitHubClient,
    *,
    draft_path: Path,
    output: Path,
) -> dict[str, Any]:
    """Fetch every inline reply and submitted-review record for draft roots."""

    draft = _validate_draft(read_json(draft_path))
    cases = []
    graphql_enabled = bool(client.token or client.offline)
    all_graphql_complete = graphql_enabled
    for case in draft["cases"]:
        number = int(case["pr_number"])
        graphql_threads, graphql_archive = _graphql_review_threads(
            client,
            pull_request=number,
        )
        comments = list(
            client.paginate(
                f"/repos/magento/magento2/pulls/{number}/comments"
            )
        )
        reviews = list(
            client.paginate(
                f"/repos/magento/magento2/pulls/{number}/reviews"
            )
        )
        by_id = {
            int(item["id"]): item
            for item in comments
            if isinstance(item, Mapping) and item.get("id")
        }
        roots = []
        for draft_comment in case["gold_comments"]:
            root_id = int(draft_comment["id"])
            root = by_id.get(root_id)
            if root is None:
                raise ValueError(
                    f"GitHub no longer returned selected comment {root_id}"
                )
            if root.get("in_reply_to_id") is not None:
                raise ValueError(f"selected comment {root_id} is not a thread root")
            replies = [
                item
                for item in comments
                if isinstance(item, Mapping)
                and int(item.get("in_reply_to_id") or 0) == root_id
            ]

            def public_comment(item: Mapping[str, Any]) -> dict[str, Any]:
                user = item.get("user")
                return {
                    "id": int(item["id"]),
                    "url": item.get("html_url"),
                    "author": (
                        user.get("login") if isinstance(user, Mapping) else None
                    ),
                    "authorType": (
                        user.get("type") if isinstance(user, Mapping) else None
                    ),
                    "authorAssociation": item.get("author_association"),
                    "body": item.get("body"),
                    "createdAt": item.get("created_at"),
                    "updatedAt": item.get("updated_at"),
                    "commitId": item.get("commit_id"),
                    "originalCommitId": item.get("original_commit_id"),
                    "inReplyToId": item.get("in_reply_to_id"),
                }

            graph_thread = graphql_threads.get(root_id)
            graph_comments = (
                graph_thread.get("comments")
                if isinstance(graph_thread, Mapping)
                else None
            )
            graph_nodes = (
                graph_comments.get("nodes")
                if isinstance(graph_comments, Mapping)
                else None
            )
            rest_by_id = {
                int(item["id"]): item
                for item in [root, *replies]
                if isinstance(item, Mapping) and item.get("id")
            }
            graph_ids = (
                [
                    item.get("databaseId")
                    for item in graph_nodes
                    if isinstance(item, Mapping)
                ]
                if isinstance(graph_nodes, list)
                else []
            )
            message_ids_reconciled = bool(
                graph_ids
                and all(isinstance(item, int) for item in graph_ids)
                and len(graph_ids) == len(set(graph_ids))
                and graph_ids[0] == root_id
                and set(graph_ids) == set(rest_by_id)
            )
            graph_complete = bool(
                isinstance(graph_nodes, list)
                and isinstance(graph_comments.get("pageInfo"), Mapping)
                and graph_comments["pageInfo"].get("hasNextPage") is False
                and message_ids_reconciled
            )
            if graph_thread is None or not graph_complete:
                all_graphql_complete = False
            messages = []
            if isinstance(graph_nodes, list):
                for graph_comment in graph_nodes:
                    if not isinstance(graph_comment, Mapping):
                        continue
                    database_id = graph_comment.get("databaseId")
                    rest_comment = rest_by_id.get(database_id)
                    author = graph_comment.get("author")
                    review = graph_comment.get("pullRequestReview")
                    messages.append(
                        {
                            "id": database_id,
                            "url": graph_comment.get("url"),
                            "author": (
                                author.get("login")
                                if isinstance(author, Mapping)
                                else None
                            ),
                            "authorType": (
                                author.get("__typename")
                                if isinstance(author, Mapping)
                                else None
                            ),
                            "authorAssociation": (
                                rest_comment.get("author_association")
                                if isinstance(rest_comment, Mapping)
                                else None
                            ),
                            "body": graph_comment.get("body"),
                            "createdAt": graph_comment.get("createdAt"),
                            "updatedAt": graph_comment.get("updatedAt"),
                            "commitId": (
                                ((review.get("commit") or {}).get("oid"))
                                if isinstance(review, Mapping)
                                else None
                            ),
                            "originalCommitId": (
                                rest_comment.get("original_commit_id")
                                if isinstance(rest_comment, Mapping)
                                else None
                            ),
                            "inReplyToId": (
                                (graph_comment.get("replyTo") or {}).get(
                                    "databaseId"
                                )
                                if isinstance(
                                    graph_comment.get("replyTo"), Mapping
                                )
                                else None
                            ),
                        }
                    )
            if not messages:
                messages = [
                    public_comment(root),
                    *[
                        public_comment(reply)
                        for reply in sorted(
                            replies,
                            key=lambda item: (
                                str(item.get("created_at") or ""),
                                int(item["id"]),
                            ),
                        )
                    ],
                ]
            roots.append(
                {
                    "rootCommentId": root_id,
                    "messages": messages,
                    "complete": graph_complete,
                    "messageIdsReconciledWithRest": message_ids_reconciled,
                    "resolutionMetadataAvailable": graph_thread is not None,
                    "isResolved": (
                        graph_thread.get("isResolved")
                        if isinstance(graph_thread, Mapping)
                        else None
                    ),
                    "isOutdated": (
                        graph_thread.get("isOutdated")
                        if isinstance(graph_thread, Mapping)
                        else None
                    ),
                    "path": (
                        graph_thread.get("path")
                        if isinstance(graph_thread, Mapping)
                        else root.get("path")
                    ),
                    "line": (
                        graph_thread.get("line")
                        if isinstance(graph_thread, Mapping)
                        else root.get("line")
                    ),
                    "originalLine": (
                        graph_thread.get("originalLine")
                        if isinstance(graph_thread, Mapping)
                        else root.get("original_line")
                    ),
                    "startLine": (
                        graph_thread.get("startLine")
                        if isinstance(graph_thread, Mapping)
                        else root.get("start_line")
                    ),
                    "originalStartLine": (
                        graph_thread.get("originalStartLine")
                        if isinstance(graph_thread, Mapping)
                        else root.get("original_start_line")
                    ),
                    "diffSide": (
                        graph_thread.get("diffSide")
                        if isinstance(graph_thread, Mapping)
                        else root.get("side")
                    ),
                    "startDiffSide": (
                        graph_thread.get("startDiffSide")
                        if isinstance(graph_thread, Mapping)
                        else root.get("start_side")
                    ),
                    "sourceSha256": sha256_json(
                        graph_thread
                        if isinstance(graph_thread, Mapping)
                        else {
                            "root": root,
                            "replies": replies,
                        }
                    ),
                }
            )
        public_reviews = []
        for review in reviews:
            if not isinstance(review, Mapping):
                continue
            user = review.get("user")
            public_reviews.append(
                {
                    "id": review.get("id"),
                    "author": (
                        user.get("login") if isinstance(user, Mapping) else None
                    ),
                    "state": review.get("state"),
                    "commitId": review.get("commit_id"),
                    "submittedAt": review.get("submitted_at"),
                    "body": review.get("body"),
                    "url": review.get("html_url"),
                }
            )
        case_evidence = {
            "pullRequest": number,
            "threads": roots,
            "reviews": public_reviews,
        }
        if graphql_archive is not None:
            case_evidence["graphqlPageArchive"] = graphql_archive
            case_evidence["graphqlResponseDigests"] = [
                page["responseDigest"]
                for page in graphql_archive["pages"]
            ]
        cases.append(case_evidence)
    result = {
        "kind": THREAD_EVIDENCE_KIND,
        "generatedAt": _now(),
        "draftSha256": sha256_json(draft),
        "source": (
            "GitHub GraphQL review threads plus REST comments/reviews"
            if graphql_enabled
            else "GitHub REST comments/reviews; no resolution metadata"
        ),
        "graphqlResolutionMetadataAvailable": all_graphql_complete,
        "cases": cases,
    }
    result["threadEvidenceDigest"] = sha256_json(result)
    write_json(output, result)
    return result


def _thread_evidence(
    path: Path | None,
    *,
    draft: Mapping[str, Any],
    draft_digest: str,
    require_raw_graphql: bool = False,
) -> tuple[
    dict[int, Mapping[str, Any]],
    dict[int, Mapping[str, Any]],
    dict[int, Mapping[str, Any]],
    str | None,
]:
    if path is None:
        if require_raw_graphql:
            raise ValueError(
                "paper-ready release requires raw GraphQL page evidence"
            )
        return {}, {}, {}, None
    value = read_json(path)
    if (
        not isinstance(value, Mapping)
        or value.get("kind") != THREAD_EVIDENCE_KIND
        or value.get("draftSha256") != draft_digest
    ):
        raise ValueError("thread evidence belongs to a different corpus draft")
    digest_value = dict(value)
    declared = digest_value.pop("threadEvidenceDigest", None)
    if declared != sha256_json(digest_value):
        raise ValueError("thread evidence digest mismatch")
    cases = value.get("cases")
    draft_cases = draft.get("cases")
    if (
        not isinstance(cases, list)
        or not isinstance(draft_cases, list)
        or len(cases) != len(draft_cases)
    ):
        raise ValueError(
            "thread evidence must cover the exact draft case set"
        )
    threads_by_root: dict[int, Mapping[str, Any]] = {}
    archives_by_pull: dict[int, Mapping[str, Any]] = {}
    bindings_by_root: dict[int, Mapping[str, Any]] = {}
    for case, draft_case in zip(cases, draft_cases, strict=True):
        if not isinstance(case, Mapping) or not isinstance(
            draft_case, Mapping
        ):
            raise ValueError("thread evidence case is malformed")
        pull_request = int(draft_case["pr_number"])
        if case.get("pullRequest") != pull_request:
            raise ValueError(
                f"thread evidence PR {pull_request} identity drift"
            )
        raw_threads = case.get("threads")
        if not isinstance(raw_threads, list):
            raise ValueError(
                f"thread evidence PR {pull_request} threads are malformed"
            )
        expected_root_ids = {
            int(comment["id"])
            for comment in draft_case.get("gold_comments") or []
            if isinstance(comment, Mapping)
        }
        observed_root_ids: set[int] = set()
        archive = case.get("graphqlPageArchive")
        if archive is None:
            if require_raw_graphql:
                raise ValueError(
                    f"paper-ready PR {pull_request} has no raw GraphQL "
                    "page archive"
                )
        else:
            validated_archive, _ = validate_graphql_thread_archive(
                archive,
                pull_request=pull_request,
            )
            response_digests = case.get("graphqlResponseDigests")
            expected_response_digests = [
                page["responseDigest"]
                for page in validated_archive["pages"]
            ]
            if response_digests != expected_response_digests:
                raise ValueError(
                    f"thread evidence PR {pull_request} response-digest "
                    "index drift"
                )
            archives_by_pull[pull_request] = validated_archive
        for thread in raw_threads:
            if not isinstance(thread, Mapping):
                raise ValueError(
                    f"thread evidence PR {pull_request} contains a "
                    "malformed thread"
                )
            root_id = thread.get("rootCommentId")
            if (
                isinstance(root_id, bool)
                or not isinstance(root_id, int)
                or root_id < 1
                or root_id in threads_by_root
            ):
                raise ValueError(
                    "thread evidence root IDs must be unique positive "
                    "integers"
                )
            observed_root_ids.add(root_id)
            threads_by_root[root_id] = thread
            if archive is not None:
                bindings_by_root[root_id] = build_review_thread_binding(
                    thread=thread,
                    archive=archive,
                    thread_evidence_digest=str(declared),
                )
        if observed_root_ids != expected_root_ids:
            raise ValueError(
                f"thread evidence PR {pull_request} does not cover the "
                "exact selected root-comment set"
            )
    return (
        threads_by_root,
        archives_by_pull,
        bindings_by_root,
        str(declared),
    )


def _packet_evidence(
    path: Path | None,
    *,
    draft: Mapping[str, Any],
    draft_digest: str,
) -> tuple[dict[int, Mapping[str, Any]], str | None]:
    if path is None:
        return {}, None
    value = read_json(path)
    if (
        not isinstance(value, Mapping)
        or value.get("kind") != PACKET_KIND
        or value.get("draftSha256") != draft_digest
    ):
        raise ValueError("curation packet belongs to a different corpus draft")
    digest_value = dict(value)
    declared = digest_value.pop("packetDigest", None)
    if declared != sha256_json(digest_value):
        raise ValueError("curation packet digest mismatch")
    packet_cases = value.get("cases")
    draft_cases = draft.get("cases")
    if (
        not isinstance(packet_cases, list)
        or not isinstance(draft_cases, list)
        or len(packet_cases) != len(draft_cases)
    ):
        raise ValueError("curation packet must cover the exact draft case set")
    result: dict[int, Mapping[str, Any]] = {}
    for index, (case, source_case) in enumerate(
        zip(packet_cases, draft_cases, strict=True)
    ):
        if not isinstance(case, Mapping) or not isinstance(source_case, Mapping):
            raise ValueError(f"curation packet case {index} is malformed")
        expected_case = {
            "draftCaseId": source_case.get("case_id"),
            "pullRequest": source_case.get("pr_number"),
            "sourceUrl": source_case.get("pr_url"),
            "baseSha": source_case.get("benchmark_base_sha"),
            "headSha": source_case.get("benchmark_head_sha"),
            "finalHeadSha": source_case.get("final_head_sha"),
            "sizeBand": source_case.get("size_band"),
            "fileCount": source_case.get("checkpoint_changed_files"),
        }
        if any(case.get(field) != expected for field, expected in expected_case.items()):
            raise ValueError(
                f"curation packet PR {source_case.get('pr_number')} identity drift"
            )
        packet_comments = case.get("comments")
        source_comments = source_case.get("gold_comments")
        if (
            not isinstance(packet_comments, list)
            or not isinstance(source_comments, list)
            or len(packet_comments) != len(source_comments)
        ):
            raise ValueError(
                f"curation packet PR {source_case.get('pr_number')} does not "
                "cover the exact draft comment set"
            )
        for packet_comment, source_comment in zip(
            packet_comments,
            source_comments,
            strict=True,
        ):
            if not isinstance(packet_comment, Mapping) or not isinstance(
                source_comment, Mapping
            ):
                raise ValueError("curation packet comment is malformed")
            comment_id = source_comment.get("id")
            expected_comment = {
                "commentId": comment_id,
                "sourceUrl": source_comment.get("url"),
                "reviewer": source_comment.get("reviewer"),
                "reviewerAssociation": source_comment.get(
                    "author_association"
                ),
                "createdAt": source_comment.get("created_at"),
                "body": source_comment.get("body"),
                "path": source_comment.get("path"),
                "line": source_comment.get("line"),
                "startLine": source_comment.get("start_line"),
                "diffHunk": source_comment.get("diff_hunk"),
            }
            if any(
                packet_comment.get(field) != expected
                for field, expected in expected_comment.items()
            ):
                raise ValueError(
                    f"curation packet comment {comment_id} drifted from the draft"
                )
            path_diff = require_text(
                packet_comment.get("checkpointToFinalPathDiff"),
                f"curation packet comment {comment_id} checkpoint-to-final diff",
            )
            path_diff_sha256 = sha256_text(path_diff)
            if (
                packet_comment.get("checkpointToFinalPathDiffSha256")
                != path_diff_sha256
            ):
                raise ValueError(
                    f"curation packet comment {comment_id} path-diff digest mismatch"
                )
            transition = validate_path_transition_evidence(
                packet_comment.get("pathTransition"),
                source_path=str(source_comment.get("path") or ""),
                diff_sha256=path_diff_sha256,
            )
            _validate_source_evidence(
                packet_comment.get("checkpointSource"),
                available=True,
                path=transition["sourcePath"],
                blob_oid=transition["checkpointBlobOid"],
                field=f"curation packet comment {comment_id}.checkpointSource",
            )
            final_available = transition["status"] != "deleted"
            _validate_source_evidence(
                packet_comment.get("finalSource"),
                available=final_available,
                path=transition["finalPath"],
                blob_oid=transition["finalBlobOid"],
                field=f"curation packet comment {comment_id}.finalSource",
            )
            if (
                isinstance(comment_id, bool)
                or not isinstance(comment_id, int)
                or comment_id < 1
                or comment_id in result
            ):
                raise ValueError(
                    "curation packet comment IDs must be unique positive integers"
                )
            result[comment_id] = packet_comment
    return result, str(declared)


def export_curation_packet(
    *,
    draft_path: Path,
    repository: Path,
    output: Path,
    thread_evidence_path: Path | None = None,
) -> dict[str, Any]:
    draft = _validate_draft(read_json(draft_path))
    draft_digest = sha256_json(draft)
    threads, _, _, _ = _thread_evidence(
        thread_evidence_path,
        draft=draft,
        draft_digest=draft_digest,
    )
    if not (repository / ".git").exists():
        raise ValueError("curation repository must be a local Magento Git clone")
    validate_git_evidence_repository(repository)
    git_env = hermetic_git_environment()
    packet_cases = []
    for case in draft["cases"]:
        base = str(case["benchmark_base_sha"])
        head = str(case["benchmark_head_sha"])
        final = str(case["final_head_sha"])
        run(
            ["git", "-C", str(repository), "cat-file", "-e", f"{base}^{{commit}}"],
            env=git_env,
        )
        run(
            ["git", "-C", str(repository), "cat-file", "-e", f"{head}^{{commit}}"],
            env=git_env,
        )
        run(
            ["git", "-C", str(repository), "cat-file", "-e", f"{final}^{{commit}}"],
            env=git_env,
        )
        comments = []
        for comment in case["gold_comments"]:
            path = str(comment["path"])
            line = int(comment["line"])
            path_evidence = _curation_path_evidence(
                repository,
                checkpoint_sha=head,
                final_sha=final,
                source_path=path,
                source_line=line,
                git_env=git_env,
            )
            thread = threads.get(int(comment["id"]))
            comments.append(
                {
                    "commentId": comment["id"],
                    "sourceUrl": comment["url"],
                    "reviewer": comment["reviewer"],
                    "reviewerAssociation": comment["author_association"],
                    "createdAt": comment["created_at"],
                    "body": comment["body"],
                    "path": path,
                    "line": line,
                    "startLine": comment.get("start_line"),
                    "diffHunk": comment["diff_hunk"],
                    **path_evidence,
                    "sampledReplies": list(comment.get("sampled_replies") or []),
                    "fullThread": (
                        list(thread.get("messages") or []) if thread else None
                    ),
                    "threadComplete": bool(
                        thread and thread.get("complete") is True
                    ),
                    "resolutionMetadataAvailable": bool(
                        thread
                        and thread.get("resolutionMetadataAvailable") is True
                    ),
                    "questions": {
                        "semanticActionable": None,
                        "issuePresentAtSnapshot": None,
                        "acceptedOrRequiredByReview": None,
                        "fixedOrSupersededInFinalHead": None,
                        "sameRootCauseFix": None,
                    },
                }
            )
        packet_cases.append(
            {
                "draftCaseId": case["case_id"],
                "pullRequest": case["pr_number"],
                "sourceUrl": case["pr_url"],
                "baseSha": base,
                "headSha": head,
                "finalHeadSha": final,
                "sizeBand": case["size_band"],
                "fileCount": case["checkpoint_changed_files"],
                "comments": comments,
            }
        )
    result = {
        "kind": PACKET_KIND,
        "generatedAt": _now(),
        "draftSha256": draft_digest,
        "warning": (
            "This packet is evidence for adjudication, not a gold set. Full GitHub "
            "threads must be collected before a comment can be paper-ready."
        ),
        "cases": packet_cases,
    }
    result["packetDigest"] = sha256_json(result)
    write_json(output, result)
    return result


def _decision_map(
    value: Any,
    *,
    draft_digest: str,
    draft_file_sha256: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("curation decisions must be an object")
    if value.get("kind") == DECISIONS_KIND:
        if value.get("draftSha256") != draft_digest:
            raise ValueError("curation decisions belong to a different draft")
        comments = value.get("comments")
        if not isinstance(comments, Mapping):
            raise ValueError("curation decisions comments must be an object")
        return comments
    if value.get("kind") != DECISIONS_TEMPLATE_KIND:
        raise ValueError(
            "curation decisions must be the shipped template or use kind "
            f"{DECISIONS_KIND}"
        )
    if value.get("source_corpus_sha256") != draft_file_sha256:
        raise ValueError("curation decision template belongs to a different draft")
    raw_decisions = value.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("curation decision template decisions must be an array")
    comments: dict[str, Any] = {}
    field_names = {
        "semantic_actionable": "semanticActionable",
        "issue_present_at_checkpoint": "issuePresentAtSnapshot",
        "accepted_or_required_by_review": "acceptedOrRequiredByReview",
        "fixed_or_superseded_in_final_head": "fixedOrSupersededInFinalHead",
        "same_root_cause_fix": "sameRootCauseFix",
        "thread_complete": "threadComplete",
        "thread_disposition": "threadDisposition",
        "root_cause": "rootCause",
        "failure_mode": "failureMode",
        "required_change": "requiredChange",
        "fix_commit_sha": "fixCommitSha",
        "fix_evidence": "fixEvidence",
        "exclusion_reason": "exclusionReason",
    }
    for item in raw_decisions:
        if not isinstance(item, Mapping):
            raise ValueError("curation template decision must be an object")
        comment_id = item.get("comment_id")
        if isinstance(comment_id, bool) or not isinstance(comment_id, int):
            raise ValueError("curation template comment_id must be an integer")
        key = str(comment_id)
        if key in comments:
            raise ValueError(f"duplicate curation decision: {comment_id}")
        normalized = dict(item)
        for source, target in field_names.items():
            if source in item:
                normalized[target] = item[source]
        comments[key] = normalized
    return comments


def _paper_thread_digest(
    thread: Mapping[str, Any] | None,
    *,
    source_comment: Mapping[str, Any],
    comment_id: int,
) -> str:
    if (
        thread is None
        or thread.get("complete") is not True
        or thread.get("messageIdsReconciledWithRest") is not True
        or thread.get("resolutionMetadataAvailable") is not True
        or not isinstance(thread.get("isResolved"), bool)
        or not isinstance(thread.get("isOutdated"), bool)
    ):
        raise ValueError(
            f"paper-ready comment {comment_id} needs complete GraphQL "
            "thread resolution evidence"
        )
    messages = thread.get("messages")
    root = (
        messages[0]
        if isinstance(messages, list)
        and messages
        and isinstance(messages[0], Mapping)
        else None
    )
    if (
        root is None
        or root.get("id") != comment_id
        or root.get("body") != source_comment.get("body")
        or root.get("updatedAt") != source_comment.get("updated_at")
    ):
        raise ValueError(
            f"paper-ready comment {comment_id} thread root drifted from "
            "the curated source comment"
        )
    return sha256_json(thread)


def _accepted_annotation(
    decision: Mapping[str, Any],
    *,
    case_id: str,
    comment_id: int,
    paper_ready: bool,
    source_comment: Mapping[str, Any],
    source_archive_digest: str | None,
    thread: Mapping[str, Any] | None,
    thread_evidence_digest: str | None,
    packet_comment: Mapping[str, Any] | None,
    packet_digest: str | None,
) -> dict[str, Any]:
    for field in (
        "semanticActionable",
        "issuePresentAtSnapshot",
        "acceptedOrRequiredByReview",
        "fixedOrSupersededInFinalHead",
        "sameRootCauseFix",
    ):
        if decision.get(field) is not True:
            raise ValueError(
                f"included comment {comment_id} requires {field}=true"
            )
    if decision.get("threadDisposition") != "fixed":
        raise ValueError(
            f"included comment {comment_id} threadDisposition must be fixed"
        )
    summary = require_text(decision.get("summary"), f"{comment_id}.summary")
    root_cause = require_text(
        decision.get("rootCause"), f"{comment_id}.rootCause"
    )
    failure_mode = require_text(
        decision.get("failureMode"), f"{comment_id}.failureMode"
    )
    required_change = require_text(
        decision.get("requiredChange"), f"{comment_id}.requiredChange"
    )
    category = decision.get("category")
    severity = decision.get("severity")
    if category not in VALID_CATEGORIES:
        raise ValueError(f"included comment {comment_id} has invalid category")
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"included comment {comment_id} has invalid severity")
    fix_sha = require_full_sha(
        decision.get("fixCommitSha"),
        f"{comment_id}.fixCommitSha",
    )
    evidence = decision.get("fixEvidence")
    if not isinstance(evidence, list) or len(evidence) < 2:
        raise ValueError(
            f"included comment {comment_id} needs at least two fix evidence items"
        )
    evidence_kinds = {
        str(item.get("kind") or "")
        for item in evidence
        if isinstance(item, Mapping)
    }
    if "code_change" not in evidence_kinds or len(evidence_kinds) < 2:
        raise ValueError(
            f"included comment {comment_id} needs code_change plus another "
            "independent fix signal"
        )
    adjudication = decision.get("adjudication")
    if not isinstance(adjudication, Mapping):
        raise ValueError(f"included comment {comment_id} has no adjudication")
    status = adjudication.get("status")
    if status not in {"accepted", "provisional"}:
        raise ValueError(f"included comment {comment_id} adjudication is invalid")
    annotators = adjudication.get("annotators")
    normalized_annotators = (
        [
            item.strip()
            for item in annotators
            if isinstance(item, str) and item.strip()
        ]
        if isinstance(annotators, list)
        else []
    )
    records = adjudication.get("records")
    thread_digest = sha256_json(thread) if thread is not None else None
    expected_issue = {
        "summary": summary,
        "rootCause": root_cause,
        "failureMode": failure_mode,
        "requiredChange": required_change,
        "category": category,
        "severity": severity,
        "actionable": True,
        "atomic": decision.get("atomic") is True,
    }
    normalized_adjudication = {
        "status": status,
        "annotators": normalized_annotators,
        "adjudicator": adjudication.get("adjudicator"),
        "at": adjudication.get("at"),
        "notes": str(adjudication.get("notes") or ""),
        "threadComplete": decision.get("threadComplete") is True,
        "threadDisposition": decision.get("threadDisposition"),
        "threadEvidenceDigest": thread_evidence_digest,
        "threadDigest": thread_digest,
        "curationPacketDigest": packet_digest,
    }
    decision_digest = decision_binding_digest(
        expected_issue=expected_issue,
        fix_commit_sha=fix_sha,
        fix_evidence=evidence,
        adjudication=normalized_adjudication,
    )
    if paper_ready:
        if decision.get("atomic") is not True:
            raise ValueError(
                f"paper-ready comment {comment_id} must be one atomic issue"
            )
        if decision.get("threadComplete") is not True:
            raise ValueError(
                f"paper-ready comment {comment_id} needs a complete thread"
            )
        if status != "accepted":
            raise ValueError(
                f"paper-ready comment {comment_id} must be accepted"
            )
        if len(set(normalized_annotators)) < 2:
            raise ValueError(
                f"paper-ready comment {comment_id} needs two annotators"
            )
        thread_digest = _paper_thread_digest(
            thread,
            source_comment=source_comment,
            comment_id=comment_id,
        )
        if packet_comment is None or (
            packet_comment.get("body") != source_comment.get("body")
            or packet_comment.get("commentId") != comment_id
        ):
            raise ValueError(
                f"paper-ready comment {comment_id} is absent from the bound "
                "curation packet"
            )
        expected_artifacts = {
            "code_change": packet_comment.get(
                "checkpointToFinalPathDiffSha256"
            ),
            "thread": thread_digest,
            "review_thread": thread_digest,
        }
        for item in evidence:
            kind = str(item.get("kind") or "") if isinstance(item, Mapping) else ""
            expected_digest = expected_artifacts.get(kind)
            if (
                expected_digest is not None
                and item.get("artifactDigest") != expected_digest
            ):
                raise ValueError(
                    f"paper-ready comment {comment_id} {kind} evidence is "
                    "not bound to the collected artifact"
                )
        if not any(
            isinstance(item, Mapping)
            and item.get("kind") == "code_change"
            and item.get("artifactDigest")
            == packet_comment["checkpointToFinalPathDiffSha256"]
            for item in evidence
        ):
            raise ValueError(
                f"paper-ready comment {comment_id} needs code-change evidence "
                "bound by digest"
            )
        if not any(
            isinstance(item, Mapping)
            and item.get("kind") in {"thread", "review_thread"}
            and item.get("artifactDigest") == thread_digest
            for item in evidence
        ):
            raise ValueError(
                f"paper-ready comment {comment_id} needs thread evidence "
                "bound by digest"
            )
        if not isinstance(records, list) or len(records) < 2:
            raise ValueError(
                f"paper-ready comment {comment_id} needs independent "
                "annotator records"
            )
        record_annotators = set()
        normalized_records = []
        expected_record_bindings = {
            "caseId": case_id,
            "sourceCommentId": comment_id,
            "sourceBodySha256": sha256_text(
                str(source_comment.get("body") or "")
            ),
            "decisionDigest": decision_digest,
            "sourceArchiveDigest": source_archive_digest,
            "threadEvidenceDigest": thread_evidence_digest,
            "threadDigest": thread_digest,
            "curationPacketDigest": packet_digest,
        }
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError(
                    f"paper-ready comment {comment_id} has invalid "
                    "annotator record"
                )
            record_value = dict(record)
            declared_record_digest = record_value.pop("recordDigest", None)
            if declared_record_digest != sha256_json(record_value):
                raise ValueError(
                    f"paper-ready comment {comment_id} annotator record "
                    "digest mismatch"
                )
            annotator = require_text(
                record.get("annotator"),
                f"{comment_id}.adjudication.records.annotator",
            ).strip()
            if record.get("verdict") != "accept":
                raise ValueError(
                    f"paper-ready comment {comment_id} annotator did not accept"
                )
            if any(
                record.get(field) != expected
                for field, expected in expected_record_bindings.items()
            ):
                raise ValueError(
                    f"paper-ready comment {comment_id} annotator record "
                    "evidence binding mismatch"
                )
            require_text(
                record.get("at"),
                f"{comment_id}.adjudication.records.at",
            )
            record_annotators.add(annotator)
            normalized_records.append(dict(record))
        if record_annotators != set(normalized_annotators):
            raise ValueError(
                f"paper-ready comment {comment_id} annotator records do not "
                "match declared annotators"
            )
    else:
        normalized_records = (
            [dict(item) for item in records if isinstance(item, Mapping)]
            if isinstance(records, list)
            else []
        )
    return {
        "summary": summary,
        "rootCause": root_cause,
        "failureMode": failure_mode,
        "requiredChange": required_change,
        "category": category,
        "severity": severity,
        "atomic": decision.get("atomic") is True,
        "fixCommitSha": fix_sha,
        "fixEvidence": evidence,
        "adjudication": {
            "status": status,
            "annotators": normalized_annotators,
            "records": normalized_records,
            "adjudicator": require_text(
                adjudication.get("adjudicator"),
                f"{comment_id}.adjudication.adjudicator",
            ),
            "at": require_text(
                adjudication.get("at"),
                f"{comment_id}.adjudication.at",
            ),
            "notes": str(adjudication.get("notes") or ""),
            "threadComplete": decision.get("threadComplete") is True,
            "threadDisposition": decision.get("threadDisposition"),
            "threadEvidenceDigest": thread_evidence_digest,
            "threadDigest": thread_digest,
            "curationPacketDigest": packet_digest,
            "decisionDigest": decision_digest,
        },
    }


def _validate_excluded_decision(
    decision: Mapping[str, Any],
    *,
    case_id: str,
    comment_id: int,
    paper_ready: bool,
    source_comment: Mapping[str, Any],
    source_archive_digest: str | None,
    thread: Mapping[str, Any] | None,
    thread_evidence_digest: str | None,
    packet_comment: Mapping[str, Any] | None,
    packet_digest: str | None,
) -> None:
    require_text(
        decision.get("exclusionReason"),
        f"{comment_id}.exclusionReason",
    )
    if not paper_ready:
        return
    if decision.get("include") is not False:
        raise ValueError(
            f"paper-ready excluded comment {comment_id} must set include=false"
        )

    adjudication = decision.get("adjudication")
    if not isinstance(adjudication, Mapping):
        raise ValueError(
            f"paper-ready excluded comment {comment_id} has no exclusion "
            "adjudication"
        )
    if adjudication.get("status") != "excluded":
        raise ValueError(
            f"paper-ready excluded comment {comment_id} adjudication status "
            "must be excluded"
        )

    raw_annotators = adjudication.get("annotators")
    if (
        not isinstance(raw_annotators, list)
        or len(raw_annotators) < 2
        or any(
            not isinstance(annotator, str) or not annotator.strip()
            for annotator in raw_annotators
        )
    ):
        raise ValueError(
            f"paper-ready excluded comment {comment_id} needs at least two "
            "distinct annotators"
        )
    annotators = [annotator.strip() for annotator in raw_annotators]
    if len(set(annotators)) != len(annotators):
        raise ValueError(
            f"paper-ready excluded comment {comment_id} needs at least two "
            "distinct annotators"
        )

    require_text(
        adjudication.get("adjudicator"),
        f"{comment_id}.adjudication.adjudicator",
    )
    _timestamp(
        adjudication.get("at"),
        f"{comment_id}.adjudication.at",
    )
    thread_digest = _paper_thread_digest(
        thread,
        source_comment=source_comment,
        comment_id=comment_id,
    )
    if packet_comment is None or (
        packet_comment.get("commentId") != comment_id
        or packet_comment.get("body") != source_comment.get("body")
    ):
        raise ValueError(
            f"paper-ready excluded comment {comment_id} is absent from the "
            "bound curation packet"
        )

    records = adjudication.get("records")
    if not isinstance(records, list) or len(records) != len(annotators):
        raise ValueError(
            f"paper-ready excluded comment {comment_id} needs one independent "
            "digest-bound record per annotator"
        )

    decision_digest = exclusion_decision_binding_digest(decision)
    expected_record_bindings = {
        "caseId": case_id,
        "sourceCommentId": comment_id,
        "sourceBodySha256": sha256_text(
            str(source_comment.get("body") or "")
        ),
        "decisionDigest": decision_digest,
        "sourceArchiveDigest": source_archive_digest,
        "threadEvidenceDigest": thread_evidence_digest,
        "threadDigest": thread_digest,
        "curationPacketDigest": packet_digest,
    }
    record_annotators: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError(
                f"paper-ready excluded comment {comment_id} has an invalid "
                "annotator record"
            )
        record_value = dict(record)
        declared_record_digest = record_value.pop("recordDigest", None)
        if declared_record_digest != sha256_json(record_value):
            raise ValueError(
                f"paper-ready excluded comment {comment_id} annotator record "
                "digest mismatch"
            )
        annotator = require_text(
            record.get("annotator"),
            f"{comment_id}.adjudication.records.annotator",
        ).strip()
        if record.get("verdict") != "exclude":
            raise ValueError(
                f"paper-ready excluded comment {comment_id} annotator did "
                "not exclude"
            )
        if any(
            record.get(field) != expected
            for field, expected in expected_record_bindings.items()
        ):
            raise ValueError(
                f"paper-ready excluded comment {comment_id} annotator "
                "record evidence binding mismatch"
            )
        _timestamp(
            record.get("at"),
            f"{comment_id}.adjudication.records.at",
        )
        if annotator in record_annotators:
            raise ValueError(
                f"paper-ready excluded comment {comment_id} has duplicate "
                "annotator records"
            )
        record_annotators.add(annotator)

    if record_annotators != set(annotators):
        raise ValueError(
            f"paper-ready excluded comment {comment_id} annotator records do "
            "not match declared annotators"
        )


def exclusion_decision_binding_digest(
    decision: Mapping[str, Any],
) -> str:
    adjudication = (
        decision.get("adjudication")
        if isinstance(decision.get("adjudication"), Mapping)
        else {}
    )
    return sha256_json(
        {
            "include": decision.get("include"),
            "exclusionReason": decision.get("exclusionReason"),
            "adjudication": {
                field: adjudication.get(field)
                for field in (
                    "status",
                    "annotators",
                    "adjudicator",
                    "at",
                    "notes",
                )
            },
        }
    )


def _stratified_partitions(
    cases: list[Mapping[str, Any]],
    *,
    sealed_total: int,
    seed: str,
) -> dict[str, str]:
    if not 0 < sealed_total < len(cases):
        raise ValueError("sealed partition size must be between zero and case count")
    by_band: dict[str, list[Mapping[str, Any]]] = {}
    for case in cases:
        by_band.setdefault(str(case["size_band"]), []).append(case)
    fraction = sealed_total / len(cases)
    quotas = {
        band: int(len(values) * fraction)
        for band, values in by_band.items()
    }
    remaining = sealed_total - sum(quotas.values())
    remainders = sorted(
        by_band,
        key=lambda band: (
            -(len(by_band[band]) * fraction - quotas[band]),
            band,
        ),
    )
    for band in remainders[:remaining]:
        quotas[band] += 1
    sealed_ids = set()
    for band, values in sorted(by_band.items()):
        ranked = sorted(
            values,
            key=lambda case: sha256_text(
                f"{seed}:{band}:{case['case_id']}"
            ),
        )
        sealed_ids.update(
            str(case["case_id"]) for case in ranked[: quotas[band]]
        )
    if len(sealed_ids) != sealed_total:
        raise AssertionError("stratified partition allocation drift")
    return {
        str(case["case_id"]): (
            "sealed" if str(case["case_id"]) in sealed_ids else "development"
        )
        for case in cases
    }


def release_selection(
    *,
    draft_path: Path,
    decisions_path: Path,
    output: Path,
    paper_ready: bool = False,
    source_archive_path: Path | None = None,
    thread_evidence_path: Path | None = None,
    curation_packet_path: Path | None = None,
) -> dict[str, Any]:
    draft = _validate_draft(read_json(draft_path))
    draft_digest = sha256_json(draft)
    draft_file_sha256 = hashlib.sha256(draft_path.read_bytes()).hexdigest()
    decisions = _decision_map(
        read_json(decisions_path),
        draft_digest=draft_digest,
        draft_file_sha256=draft_file_sha256,
    )
    (
        source_archive_digest,
        source_archive_cases,
        rest_threads_by_root,
    ) = _source_archive_evidence(
        source_archive_path,
        draft=draft,
        draft_digest=draft_digest,
        draft_file_sha256=draft_file_sha256,
    )
    if paper_ready and source_archive_digest is None:
        raise ValueError(
            "paper-ready release requires a digest-bound REST source archive"
        )
    (
        threads,
        graphql_archives,
        thread_bindings,
        thread_evidence_digest,
    ) = _thread_evidence(
        thread_evidence_path,
        draft=draft,
        draft_digest=draft_digest,
        require_raw_graphql=paper_ready,
    )
    thread_evidence_value = (
        read_json(thread_evidence_path)
        if thread_evidence_path is not None
        else None
    )
    if paper_ready and (
        not isinstance(thread_evidence_value, Mapping)
        or thread_evidence_value.get("graphqlResolutionMetadataAvailable")
        is not True
    ):
        raise ValueError(
            "paper-ready release requires complete GraphQL thread evidence"
        )
    packet_comments, packet_digest = _packet_evidence(
        curation_packet_path,
        draft=draft,
        draft_digest=draft_digest,
    )
    if paper_ready and packet_digest is None:
        raise ValueError(
            "paper-ready release requires a digest-bound curation packet"
        )
    partition_seed = "fixed-evidence-first-2026-07-29"
    partitions = _stratified_partitions(
        list(draft["cases"]),
        sealed_total=20,
        seed=partition_seed,
    )
    cases = []
    observed_decisions: set[str] = set()
    for index, draft_case in enumerate(draft["cases"], start=1):
        released_case_id = f"m2b-{index:03d}"
        pull_request = int(draft_case["pr_number"])
        source_archive_case = source_archive_cases.get(pull_request)
        if paper_ready and source_archive_case is None:
            raise ValueError(
                f"paper-ready PR {pull_request} has no source archive evidence"
            )
        selected_ids = []
        annotations: dict[str, Any] = {}
        source_evidence: dict[str, Any] = {}
        for comment in draft_case["gold_comments"]:
            comment_id = int(comment["id"])
            key = str(comment_id)
            decision = decisions.get(key)
            if not isinstance(decision, Mapping):
                raise ValueError(f"missing curation decision for comment {comment_id}")
            observed_decisions.add(key)
            if decision.get("include") is not True:
                _validate_excluded_decision(
                    decision,
                    case_id=released_case_id,
                    comment_id=comment_id,
                    paper_ready=paper_ready,
                    source_comment=comment,
                    source_archive_digest=source_archive_digest,
                    thread=threads.get(comment_id),
                    thread_evidence_digest=thread_evidence_digest,
                    packet_comment=packet_comments.get(comment_id),
                    packet_digest=packet_digest,
                )
                continue
            packet_comment = packet_comments.get(comment_id)
            annotation = _accepted_annotation(
                decision,
                case_id=released_case_id,
                comment_id=comment_id,
                paper_ready=paper_ready,
                source_comment=comment,
                source_archive_digest=source_archive_digest,
                thread=threads.get(comment_id),
                thread_evidence_digest=thread_evidence_digest,
                packet_comment=packet_comment,
                packet_digest=packet_digest,
            )
            anchor = comment.get("anchor_validation")
            if (
                isinstance(anchor, Mapping)
                and anchor.get("validation_mode")
                == "raw_original_position_hunk_terminal_with_adjacent_checkpoint_line"
            ):
                resolved_line = anchor.get("resolved_checkpoint_line")
                if (
                    isinstance(resolved_line, bool)
                    or not isinstance(resolved_line, int)
                    or resolved_line < 1
                ):
                    raise ValueError(
                        f"comment {comment_id} has an invalid resolved line"
                    )
                annotation["originalLineOverride"] = resolved_line
                annotation["originalLineResolution"] = {
                    "method": anchor["validation_mode"],
                    "sourceOriginalLine": comment.get("raw_original_line"),
                    "sourceOriginalPosition": comment.get(
                        "raw_original_position"
                    ),
                    "warning": anchor.get("warning"),
                }
            annotations[key] = annotation
            archived_comment_digest = (
                source_archive_case["selectedCommentResponseSha256"].get(key)
                if source_archive_case is not None
                else None
            )
            archived_review_digest = (
                source_archive_case["submittedReviewResponseSha256"].get(
                    str(comment["review_id"])
                )
                if source_archive_case is not None
                else None
            )
            packet_transition = (
                packet_comment.get("pathTransition")
                if isinstance(packet_comment, Mapping)
                else None
            )
            thread_binding = thread_bindings.get(comment_id)
            rest_thread_evidence = rest_threads_by_root.get(comment_id)
            graphql_archive = graphql_archives.get(pull_request)
            normalized_thread = threads.get(comment_id)
            if (
                isinstance(graphql_archive, Mapping)
                and isinstance(normalized_thread, Mapping)
                and isinstance(rest_thread_evidence, Mapping)
            ):
                thread_binding = build_review_thread_binding(
                    thread=normalized_thread,
                    archive=graphql_archive,
                    thread_evidence_digest=str(thread_evidence_digest),
                    rest_thread_evidence=rest_thread_evidence,
                )
                validate_review_thread_binding(
                    thread_binding,
                    archive=graphql_archive,
                    root_comment_id=comment_id,
                    thread_evidence_digest=str(thread_evidence_digest),
                    require_complete=paper_ready,
                    source_archive_digest=str(source_archive_digest),
                    source_archive_case_evidence_digest=str(
                        source_archive_case["caseEvidenceDigest"]
                    ),
                )
            if paper_ready and not isinstance(thread_binding, Mapping):
                raise ValueError(
                    f"paper-ready comment {comment_id} is not bound to a "
                    "raw GraphQL page and exact REST thread"
                )
            if paper_ready and not isinstance(rest_thread_evidence, Mapping):
                raise ValueError(
                    f"paper-ready comment {comment_id} has no exact raw REST "
                    "thread evidence"
                )
            source_evidence[key] = {
                "bodySha256": sha256_text(str(comment["body"])),
                "updatedAt": comment.get("updated_at"),
                "reviewer": comment.get("reviewer"),
                "originalCommitId": comment.get("original_commit_id"),
                "reviewId": comment.get("review_id"),
                "sourceApiResponseSha256": archived_comment_digest,
                "sourceReviewResponseSha256": archived_review_digest,
                **(
                    {"pathTransition": dict(packet_transition)}
                    if isinstance(packet_transition, Mapping)
                    else {}
                ),
                **(
                    {"reviewThreadEvidence": dict(thread_binding)}
                    if isinstance(thread_binding, Mapping)
                    else {}
                ),
            }
            selected_ids.append(comment_id)
        if not selected_ids:
            raise ValueError(
                f"PR {draft_case['pr_number']} has no accepted comment; "
                "the released selection must remain a 50-case benchmark"
            )
        cases.append(
            {
                "caseId": released_case_id,
                "pullRequest": draft_case["pr_number"],
                "headSha": draft_case["benchmark_head_sha"],
                "baseSha": draft_case["benchmark_base_sha"],
                "partition": partitions[str(draft_case["case_id"])],
                "commentIds": selected_ids,
                "expectedIssues": annotations,
                "sourceCommentEvidence": source_evidence,
                "sourceArchiveEvidence": source_archive_case,
                **(
                    {
                        "graphqlThreadArchive": dict(
                            graphql_archives[pull_request]
                        )
                    }
                    if pull_request in graphql_archives
                    else {}
                ),
            }
        )
    unknown = sorted(set(decisions) - observed_decisions)
    if unknown:
        raise ValueError(
            "curation decisions contain unknown comment IDs: "
            + ", ".join(unknown[:10])
        )
    selection = {
        "kind": SELECTION_KIND,
        "corpusId": "magento2-core-review-50",
        "generatedAt": _now(),
        "sourceDraftSha256": draft_digest,
        "decisionsSha256": sha256_json(read_json(decisions_path)),
        "sourceArchiveDigest": source_archive_digest,
        "threadEvidenceDigest": thread_evidence_digest,
        "curationPacketDigest": packet_digest,
        "paperReadyRequested": paper_ready,
        "selectionSeed": partition_seed,
        "partitionPolicy": {
            "method": "deterministic_label_blind_stratified_by_size_band",
            "developmentCases": 30,
            "sealedCases": 20,
        },
        "mainlineCutoffSha": str(
            max(draft["cases"], key=lambda case: str(case["merged_at"]))[
                "merge_commit_sha"
            ]
        ),
        "requiredBands": {"small": 1, "medium": 1, "large": 1},
        "cases": cases,
    }
    selection["selectionDigest"] = sha256_json(selection)
    write_json(output, selection)
    return selection
