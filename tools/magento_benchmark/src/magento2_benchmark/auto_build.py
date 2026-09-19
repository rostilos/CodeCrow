from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .github import (
    GITHUB_REST_GET_CACHE_KIND,
    GITHUB_REST_GET_CACHE_SCHEMA,
    GitHubClient,
)
from .path_transition import (
    resolve_path_transition,
    validate_path_transition_evidence,
)
from .repository_prep import _verify_origin
from .thread_provenance import rest_review_comment_anchor
from .util import (
    canonical_json,
    deterministic_git_diff_command,
    hermetic_git_environment,
    is_local_git_repository,
    require_full_sha,
    require_text,
    run,
    sha256_json,
    sha256_text,
    validate_git_evidence_repository,
    write_json,
)


AUTOMATIC_CORPUS_KIND = "codecrow-magento2-automatic-review-corpus"
AUTOMATIC_AUDIT_KIND = "codecrow-magento2-automatic-review-corpus-audit"
AUTOMATIC_ROOT_EVIDENCE_KIND = "codecrow-magento2-selected-root-rest-evidence"
TARGET_CASES = 54
STRATA = ("small", "medium", "large")
COMPLEXITIES = ("simple", "moderate", "complex")
EXACT_QUOTA = 18
SIZE_BANDS = {
    "small": (3, 10),
    "medium": (11, 30),
    "large": (31, 80),
}
DIVERSITY_CAPS = {
    "area": 12,
    "reviewer": 8,
    # Historical medium/large Magento reviews are necessarily concentrated in
    # the legacy band. After official REST qualification and excluding review
    # heads that survive only as dangling objects, 42 is the lowest feasible
    # cap; it still forces at least 12 non-legacy cases alongside exact quotas.
    "dateBand": 42,
}
SELECTION_ATTEMPTS = 48
DEFAULT_SELECTION_SEED = "codecrow-magento2-objective-reference-set"
DEFAULT_MATERIALIZATION_JOBS = 8
OFFICIAL_REPOSITORY = "magento/magento2"
OFFICIAL_DEFAULT_BRANCH = "2.4-develop"
OFFICIAL_TARGET_BRANCHES = (
    "2.4-develop",
    "2.3-develop",
    "2.2-develop",
    "develop",
)
DURABLE_OFFICIAL_HEAD_REFS = (
    ("refs/heads/2.4-develop", "refs/remotes/origin/2.4-develop"),
    ("refs/heads/2.3", "refs/remotes/origin/2.3"),
    ("refs/heads/2.2", "refs/remotes/origin/2.2"),
)
DURABLE_OFFICIAL_TARGET_REFS = {
    "2.4-develop": DURABLE_OFFICIAL_HEAD_REFS[0],
    # Magento's retained release branches contain merges made while these
    # historical develop names were the PR target.
    "2.3-develop": DURABLE_OFFICIAL_HEAD_REFS[1],
    "2.2-develop": DURABLE_OFFICIAL_HEAD_REFS[2],
    # The pre-2.4 generic develop lineage is retained by 2.4-develop.
    "develop": DURABLE_OFFICIAL_HEAD_REFS[0],
}
OFFICIAL_API_ROOT = "https://api.github.com/repos/magento/magento2"
OFFICIAL_WEB_ROOT = "https://github.com/magento/magento2"
LEGITIMACY_TIERS = (
    "author_acknowledged_fix",
    "changes_requested_then_approved",
    "reviewer_later_approved_anchor_changed",
    "changes_requested_anchor_changed",
    "github_suggestion_applied",
    "explicit_code_change_applied",
    "php_return_type_added",
    "actionable_anchor_change_applied",
)
OFFICIAL_REST_PROJECTION_FIELDS = {
    "body",
    "reviewer",
    "originalLine",
    "originalStartLine",
    "originalSide",
}
OFFICIAL_REST_REJECTION_CODES = frozenset(
    {
        "official_rest_root_unavailable",
        "official_rest_root_rejected",
        "official_rest_root_drift",
        "official_rest_legitimacy_reply_unavailable",
        "official_rest_legitimacy_reply_rejected",
    }
)

_FIX_WORDS = re.compile(
    r"\b(?:fixed|done|addressed|resolved|implemented|updated|applied|changed)\b",
    re.IGNORECASE,
)
_NON_FIX_CONTEXT = re.compile(
    r"\b(?:not|isn['’]?t|wasn['’]?t|won['’]?t|will|shall|todo|later)\b"
    r".{0,24}\b(?:fixed|done|addressed|resolved|implemented|updated|applied|changed)\b",
    re.IGNORECASE | re.DOTALL,
)
_SUGGESTION = re.compile(
    r"```suggestion[^\n]*\n(.*?)\n```",
    re.IGNORECASE | re.DOTALL,
)
_HUNK = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_FULL_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")
_ZERO_CLICKHOUSE_TIMESTAMP = "1970-01-01 00:00:00"
_EXPLICIT_REPLACEMENTS = (
    re.compile(
        r"\b(?:rename|replace)\s+`(?P<old>[^`\n]+)`\s+"
        r"(?:to|with|by)\s+`(?P<new>[^`\n]+)`",
        re.IGNORECASE,
    ),
    re.compile(
        r"\buse\s+`(?P<new>[^`\n]+)`\s+instead\s+of\s+"
        r"`(?P<old>[^`\n]+)`",
        re.IGNORECASE,
    ),
    re.compile(
        r"\brename\s+(?P<old>[A-Za-z_][A-Za-z0-9_\\:]*)\s+to\s+"
        r"(?P<new>[A-Za-z_][A-Za-z0-9_\\:]*)",
        re.IGNORECASE,
    ),
)
_EXPLICIT_REMOVAL = re.compile(
    r"\bremove\s+(?:the\s+)?(?:`(?P<quoted>[^`\n]+)`|"
    r"(?P<plain>extends\s+[A-Za-z_\\][A-Za-z0-9_\\]*))",
    re.IGNORECASE,
)
_PHP_RETURN_TYPE_REQUEST = re.compile(
    r"\b(?:add|define|missing|need|please|specify)\b.{0,48}\breturn\s+type\b",
    re.IGNORECASE | re.DOTALL,
)
_ACTIONABLE_REVIEW = re.compile(
    r"\b(?:should|must|please|incorrect|wrong|missing|remove|change|instead|"
    r"avoid|bug|break|risk|need|recommend|suggest|add|rename|replace)\b",
    re.IGNORECASE,
)


class CandidateRejected(ValueError):
    """A candidate failed an objective automatic-corpus gate."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _reject(code: str, detail: str) -> None:
    raise CandidateRejected(code, detail)


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _timestamp(value: Any, field: str) -> datetime:
    text = require_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: Any, field: str) -> str:
    return (
        _timestamp(value, field)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _clickhouse_timestamp(
    value: Any,
    field: str,
    *,
    optional: bool = False,
) -> str | None:
    """Normalize the public GitHub-event mirror's UTC DateTime values."""

    if optional and value in (None, "", _ZERO_CLICKHOUSE_TIMESTAMP):
        return None
    text = require_text(value, field)
    if text == _ZERO_CLICKHOUSE_TIMESTAMP:
        raise ValueError(f"{field} is the ClickHouse zero timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a ClickHouse UTC timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (
        parsed.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _flat_row_value(
    row: Mapping[str, Any],
    name: str,
    *aliases: str,
) -> Any:
    value = _first(row, (name, *aliases))
    if value is None:
        raise ValueError(
            f"ClickHouse candidate line {row.get('_line')} is missing {name}"
        )
    return value


def _terminal_hunk_right_line(diff_hunk: Any, current_line: Any) -> int:
    """Recover old GitHub comment lines whose event payload predates `line`."""

    if isinstance(current_line, int) and not isinstance(current_line, bool) and current_line > 0:
        return current_line
    hunk = require_text(diff_hunk, "ClickHouse candidate.diff_hunk")
    lines = hunk.splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if _HUNK.match(line)),
        None,
    )
    if header_index is None:
        raise ValueError("ClickHouse candidate diff_hunk has no hunk header")
    match = _HUNK.match(lines[header_index])
    assert match is not None
    right_line = int(match.group(1))
    terminal: int | None = None
    terminal_side: str | None = None
    for line in lines[header_index + 1 :]:
        if line.startswith("\\ No newline"):
            continue
        if line.startswith("-") and not line.startswith("---"):
            terminal_side = "LEFT"
            continue
        if line.startswith("+++"):
            continue
        terminal = right_line
        terminal_side = "RIGHT"
        right_line += 1
    if terminal is None or terminal_side != "RIGHT":
        raise ValueError(
            "ClickHouse candidate does not end on a recoverable RIGHT-side line"
        )
    return terminal


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _sequence(value: Any, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result = []
    for index, item in enumerate(value):
        result.append(_mapping(item, f"{field}[{index}]"))
    return result


def _decode_export_value(value: Any) -> Any:
    """Decode JSON-encoded ClickHouse object/array columns once."""

    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        return value
    return decoded if isinstance(decoded, (dict, list)) else value


def _first(row: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return _decode_export_value(row[name])
    return None


def _read_candidate_rows(path: Path) -> tuple[list[dict[str, Any]], str, int]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read ClickHouse candidate JSONL {path}: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"candidate JSONL line {line_number} is invalid JSON: {exc}"
            ) from exc
        if not isinstance(value, Mapping):
            raise ValueError(
                f"candidate JSONL line {line_number} must be an object"
            )
        if "_line" in value:
            raise ValueError(
                f"candidate JSONL line {line_number} uses reserved field _line"
            )
        row = {str(key): _decode_export_value(item) for key, item in value.items()}
        row["_line"] = line_number
        rows.append(row)
    if not rows:
        raise ValueError("ClickHouse candidate JSONL contains no candidate rows")
    return rows, hashlib.sha256(raw).hexdigest(), len(raw)


def _read_acquisition_query(path: Path) -> tuple[str, str, int]:
    try:
        raw = path.read_bytes()
        query = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read UTF-8 acquisition query {path}: {exc}") from exc
    if not query.strip():
        raise ValueError("acquisition query is empty")
    required_fragments = (
        "github.github_events",
        "magento/magento2",
        "PullRequestReviewCommentEvent",
        "FORMAT JSONEachRow",
    )
    if any(fragment not in query for fragment in required_fragments):
        raise ValueError(
            "acquisition query does not identify the official Magento event "
            "source and JSONEachRow output contract"
        )
    return query, hashlib.sha256(raw).hexdigest(), len(raw)


def _human_login(user: Any, field: str) -> str:
    user = _mapping(user, field)
    if user.get("type") != "User":
        raise ValueError(f"{field} is not a GitHub human User")
    login = require_text(user.get("login"), f"{field}.login")
    lowered = login.casefold()
    if lowered.endswith("[bot]") or lowered in {
        "dependabot",
        "dependabot-preview",
        "github-actions",
    }:
        raise ValueError(f"{field} is an automation account")
    return login


def _official_pull(pull: Any, number: int) -> Mapping[str, Any]:
    pull = _mapping(pull, "pull")
    expected_api = f"{OFFICIAL_API_ROOT}/pulls/{number}"
    expected_web = f"{OFFICIAL_WEB_ROOT}/pull/{number}"
    if pull.get("url") != expected_api or pull.get("html_url") != expected_web:
        raise ValueError("pull is not the canonical official Magento GitHub PR")
    if _positive_int(pull.get("number"), "pull.number") != number:
        raise ValueError("pull number does not match candidate")
    base = _mapping(pull.get("base"), "pull.base")
    base_repo = _mapping(base.get("repo"), "pull.base.repo")
    if base_repo.get("full_name") != OFFICIAL_REPOSITORY:
        raise ValueError("pull base repository is not magento/magento2")
    if base.get("ref") not in OFFICIAL_TARGET_BRANCHES:
        raise ValueError("pull base ref is not an approved historical develop branch")
    if pull.get("state") != "closed" or not pull.get("merged_at"):
        raise ValueError("pull is not merged")
    if pull.get("merged") is False:
        raise ValueError("pull explicitly reports merged=false")
    if pull.get("body") is not None and not isinstance(pull.get("body"), str):
        raise ValueError("pull.body must be a string or null")
    _human_login(pull.get("user"), "pull.user")
    require_full_sha(_mapping(pull.get("head"), "pull.head").get("sha"), "pull.head.sha")
    require_full_sha(pull.get("merge_commit_sha"), "pull.merge_commit_sha")
    require_full_sha(base.get("sha"), "pull.base.sha")
    _timestamp_text(pull.get("merged_at"), "pull.merged_at")
    return pull


def _official_comment(comment: Any, number: int) -> tuple[Mapping[str, Any], dict[str, Any]]:
    comment = _mapping(comment, "review comment")
    comment_id = _positive_int(comment.get("id"), "review comment.id")
    if comment.get("url") != f"{OFFICIAL_API_ROOT}/pulls/comments/{comment_id}":
        raise ValueError("review comment API URL is not canonical")
    if comment.get("pull_request_url") != f"{OFFICIAL_API_ROOT}/pulls/{number}":
        raise ValueError("review comment belongs to another pull request")
    web_prefix = f"{OFFICIAL_WEB_ROOT}/pull/{number}#discussion_r"
    if comment.get("html_url") != f"{web_prefix}{comment_id}":
        raise ValueError("review comment web URL is not canonical")
    if comment.get("in_reply_to_id") is not None:
        raise ValueError("selected review comment is not a root comment")
    _human_login(comment.get("user"), "review comment.user")
    _positive_int(comment.get("pull_request_review_id"), "review comment.review_id")
    require_text(comment.get("body"), "review comment.body")
    _timestamp_text(comment.get("created_at"), "review comment.created_at")
    anchor = rest_review_comment_anchor(
        comment,
        field=f"REST review comment {comment_id}",
        require_right_line=True,
    )
    return comment, anchor


def _official_legitimacy_reply(
    value: Any,
    *,
    number: int,
    root_comment_id: int,
    pull_author: str,
    evidence: Mapping[str, Any],
    reviewed_at: str,
    merged_at: str,
) -> Mapping[str, Any]:
    """Validate the official reply that proves a flat author-fix tier."""

    reply = _mapping(value, "author acknowledgement reply")
    reply_id = _positive_int(reply.get("id"), "author acknowledgement reply.id")
    expected_reply_id = _positive_int(
        evidence.get("replyCommentId"),
        "author acknowledgement evidence.replyCommentId",
    )
    if reply_id != expected_reply_id:
        raise ValueError("author acknowledgement reply ID drifted")
    expected_api_url = f"{OFFICIAL_API_ROOT}/pulls/comments/{reply_id}"
    expected_web_url = f"{OFFICIAL_WEB_ROOT}/pull/{number}#discussion_r{reply_id}"
    if reply.get("url") != expected_api_url:
        raise ValueError("author acknowledgement reply API URL is not canonical")
    if reply.get("html_url") != expected_web_url:
        raise ValueError("author acknowledgement reply web URL is not canonical")
    if evidence.get("replyUrl") != expected_web_url:
        raise ValueError("author acknowledgement evidence reply URL drifted")
    if reply.get("pull_request_url") != f"{OFFICIAL_API_ROOT}/pulls/{number}":
        raise ValueError("author acknowledgement reply belongs to another pull request")
    if (
        _positive_int(
            reply.get("in_reply_to_id"),
            "author acknowledgement reply.in_reply_to_id",
        )
        != root_comment_id
    ):
        raise ValueError("author acknowledgement reply belongs to another root")
    reply_author = _human_login(
        reply.get("user"),
        "author acknowledgement reply.user",
    )
    candidate_author = require_text(
        evidence.get("replyAuthor"),
        "author acknowledgement evidence.replyAuthor",
    )
    if (
        reply_author.casefold() != pull_author.casefold()
        or reply_author.casefold() != candidate_author.casefold()
    ):
        raise ValueError("author acknowledgement reply author drifted")
    created_at = _timestamp_text(
        reply.get("created_at"),
        "author acknowledgement reply.created_at",
    )
    if created_at != evidence.get("replyCreatedAt"):
        raise ValueError("author acknowledgement reply timestamp drifted")
    body = require_text(reply.get("body"), "author acknowledgement reply.body")
    if sha256_text(body) != evidence.get("replyBodySha256"):
        raise ValueError("author acknowledgement reply body drifted")
    reply_time = _timestamp(created_at, "author acknowledgement reply.created_at")
    if not (
        _timestamp(reviewed_at, "reviewedAt")
        < reply_time
        <= _timestamp(merged_at, "mergedAt")
    ):
        raise ValueError("author acknowledgement reply is outside the PR lifetime")
    return reply


def _official_review(review: Any, number: int) -> Mapping[str, Any]:
    review = _mapping(review, "submitted review")
    review_id = _positive_int(review.get("id"), "submitted review.id")
    if review.get("url") != f"{OFFICIAL_API_ROOT}/pulls/{number}/reviews/{review_id}":
        raise ValueError("submitted review API URL is not canonical")
    if review.get("pull_request_url") != f"{OFFICIAL_API_ROOT}/pulls/{number}":
        raise ValueError("submitted review belongs to another pull request")
    _human_login(review.get("user"), "submitted review.user")
    if review.get("state") not in {
        "APPROVED",
        "CHANGES_REQUESTED",
        "COMMENTED",
        "DISMISSED",
        "PENDING",
    }:
        raise ValueError("submitted review state is invalid")
    if review.get("submitted_at") is not None:
        _timestamp_text(review.get("submitted_at"), "submitted review.submitted_at")
    commit_id = review.get("commit_id")
    if commit_id is not None:
        require_full_sha(commit_id, "submitted review.commit_id")
    return review


def _official_reply(comment: Any, number: int) -> Mapping[str, Any]:
    comment = _mapping(comment, "review reply")
    comment_id = _positive_int(comment.get("id"), "review reply.id")
    if comment.get("url") != f"{OFFICIAL_API_ROOT}/pulls/comments/{comment_id}":
        raise ValueError("review reply API URL is not canonical")
    if comment.get("pull_request_url") != f"{OFFICIAL_API_ROOT}/pulls/{number}":
        raise ValueError("review reply belongs to another pull request")
    _human_login(comment.get("user"), "review reply.user")
    _timestamp_text(comment.get("created_at"), "review reply.created_at")
    return comment


def _candidate_number(row: Mapping[str, Any], pull: Any = None) -> int:
    value = _first(
        row,
        (
            "pull_request_number",
            "pr_number",
            "pullRequestNumber",
            "number",
            "c.number",
        ),
    )
    if value is None and isinstance(pull, Mapping):
        value = pull.get("number")
    return _positive_int(value, f"candidate line {row.get('_line')}.pull request")


def _candidate_comment_ids(row: Mapping[str, Any]) -> list[int]:
    values: list[Any] = []
    singular = _first(
        row,
        ("comment_id", "review_comment_id", "source_comment_id"),
    )
    if singular is not None:
        values.append(singular)
    plural = _first(
        row,
        ("comment_ids", "review_comment_ids", "source_comment_ids"),
    )
    if isinstance(plural, list):
        values.extend(plural)
    raw_comment = _first(
        row,
        (
            "reviewComment",
            "review_comment",
            "rootComment",
            "root_comment",
            "source_comment",
            "comment",
            "comment_response",
        ),
    )
    if isinstance(raw_comment, Mapping):
        values.append(raw_comment.get("id"))
    raw_roots = _first(
        row,
        ("rootComments", "root_comments", "candidate_comments"),
    )
    if isinstance(raw_roots, list):
        values.extend(
            item.get("id") for item in raw_roots if isinstance(item, Mapping)
        )
    result = []
    for index, value in enumerate(values):
        identifier = _positive_int(
            value,
            f"candidate line {row.get('_line')}.comment_ids[{index}]",
        )
        if identifier not in result:
            result.append(identifier)
    return result


def _embedded_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    pull = _first(
        row,
        ("pull", "pullRequest", "pull_request", "pr", "pull_response"),
    )
    if not isinstance(pull, Mapping):
        raise ValueError("embedded candidate has no raw REST pull object")
    number = _candidate_number(row, pull)
    root = _first(
        row,
        (
            "reviewComment",
            "review_comment",
            "rootComment",
            "root_comment",
            "source_comment",
            "comment",
            "comment_response",
        ),
    )
    all_comments = _first(
        row,
        (
            "allReviewComments",
            "all_review_comments",
            "review_comments",
            "comments",
        ),
    )
    replies = _first(row, ("replies", "review_replies"))
    roots = _first(
        row,
        ("rootComments", "root_comments", "candidate_comments"),
    )
    comments: list[Mapping[str, Any]] = []
    for value, field in (
        (all_comments, "all review comments"),
        (replies, "review replies"),
        (roots, "candidate comments"),
    ):
        if value is not None:
            comments.extend(_sequence(value, field))
    if isinstance(root, Mapping):
        comments.append(root)
    comments_by_id: dict[int, Mapping[str, Any]] = {}
    for comment in comments:
        identifier = _positive_int(comment.get("id"), "embedded comment.id")
        previous = comments_by_id.get(identifier)
        if previous is not None and canonical_json(previous) != canonical_json(comment):
            raise ValueError(f"embedded comment {identifier} has conflicting objects")
        comments_by_id[identifier] = comment
    candidate_ids = _candidate_comment_ids(row)
    if not candidate_ids:
        candidate_ids = sorted(
            identifier
            for identifier, comment in comments_by_id.items()
            if comment.get("in_reply_to_id") is None
        )
    if not candidate_ids:
        raise ValueError("embedded candidate has no root review comment IDs")
    missing = [identifier for identifier in candidate_ids if identifier not in comments_by_id]
    if missing:
        raise ValueError(f"embedded candidate omits selected comments {missing}")
    reviews_value = _first(
        row,
        ("reviews", "submitted_reviews", "submittedReviews"),
    )
    reviews = (
        []
        if reviews_value is None
        else _sequence(reviews_value, "submitted reviews")
    )
    return {
        "number": number,
        "pull": pull,
        "comments": list(comments_by_id.values()),
        "reviews": reviews,
        "candidateIds": candidate_ids,
        "line": row.get("_line"),
    }


def _hydrate_evidence(
    client: GitHubClient,
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    requested: dict[int, set[int]] = defaultdict(set)
    source_lines: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        raw_pull = _first(
            row,
            ("pull", "pullRequest", "pull_request", "pr", "pull_response"),
        )
        number = _candidate_number(row, raw_pull)
        identifiers = _candidate_comment_ids(row)
        if not identifiers:
            raise ValueError(
                f"candidate line {row.get('_line')} must name comment IDs "
                "when --hydrate-github is used"
            )
        requested[number].update(identifiers)
        source_lines[number].append(int(row["_line"]))
    hydrated = []
    for number in sorted(requested):
        pull = client.get(f"/repos/{OFFICIAL_REPOSITORY}/pulls/{number}")
        comments = list(
            client.paginate(
                f"/repos/{OFFICIAL_REPOSITORY}/pulls/{number}/comments"
            )
        )
        reviews = list(
            client.paginate(
                f"/repos/{OFFICIAL_REPOSITORY}/pulls/{number}/reviews"
            )
        )
        by_id = {
            int(comment["id"]): comment
            for comment in comments
            if isinstance(comment, Mapping)
            and isinstance(comment.get("id"), int)
        }
        missing = sorted(requested[number] - set(by_id))
        if missing:
            raise ValueError(
                f"official REST hydration for PR {number} omitted comments {missing}"
            )
        hydrated.append(
            {
                "number": number,
                "pull": pull,
                "comments": comments,
                "reviews": reviews,
                "candidateIds": sorted(requested[number]),
                "line": min(source_lines[number]),
            }
        )
    return hydrated


def _coalesce_embedded(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[int, dict[str, Any]] = {}
    for row in rows:
        evidence = _embedded_evidence(row)
        number = evidence["number"]
        group = groups.get(number)
        if group is None:
            group = {
                "number": number,
                "pull": evidence["pull"],
                "comments": {},
                "reviews": {},
                "candidateIds": set(),
                "line": evidence["line"],
            }
            groups[number] = group
        elif canonical_json(group["pull"]) != canonical_json(evidence["pull"]):
            raise ValueError(f"PR {number} has conflicting embedded pull objects")
        for comment in evidence["comments"]:
            identifier = _positive_int(comment.get("id"), "embedded comment.id")
            previous = group["comments"].get(identifier)
            if previous is not None and canonical_json(previous) != canonical_json(comment):
                raise ValueError(f"PR {number} comment {identifier} conflicts across rows")
            group["comments"][identifier] = comment
        for review in evidence["reviews"]:
            identifier = _positive_int(review.get("id"), "embedded review.id")
            previous = group["reviews"].get(identifier)
            if previous is not None and canonical_json(previous) != canonical_json(review):
                raise ValueError(f"PR {number} review {identifier} conflicts across rows")
            group["reviews"][identifier] = review
        group["candidateIds"].update(evidence["candidateIds"])
        group["line"] = min(int(group["line"]), int(evidence["line"]))
    return [
        {
            "number": number,
            "pull": group["pull"],
            "comments": list(group["comments"].values()),
            "reviews": list(group["reviews"].values()),
            "candidateIds": sorted(group["candidateIds"]),
            "line": group["line"],
        }
        for number, group in sorted(groups.items())
    ]


def _flat_candidate_key(row: Mapping[str, Any]) -> tuple[int, int]:
    return (
        _candidate_number(row),
        _positive_int(
            _flat_row_value(row, "comment_id"),
            f"candidate line {row.get('_line')}.comment_id",
        ),
    )


def _flat_candidate_score(row: Mapping[str, Any]) -> tuple[int, str]:
    evidence = sum(
        1
        for name in ("approved_at", "requested_at", "reply_at")
        if row.get(name) not in (None, "", _ZERO_CLICKHOUSE_TIMESTAMP)
    )
    evidence += int(bool(row.get("has_suggestion_block")))
    unsigned = {key: value for key, value in row.items() if key != "_line"}
    return evidence, canonical_json(unsigned)


def _flat_candidate_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one flattened ClickHouse JSONEachRow record into source evidence.

    The public mirror freezes the historical review content and H anchor. The
    selected root is independently attested with its current official REST
    representation before a corpus can become scoring-ready.
    """

    number, comment_id = _flat_candidate_key(row)
    author = require_text(
        _flat_row_value(row, "pr_author"),
        f"candidate line {row.get('_line')}.pr_author",
    )
    reviewer = require_text(
        _flat_row_value(row, "reviewer"),
        f"candidate line {row.get('_line')}.reviewer",
    )
    if author.casefold() == reviewer.casefold():
        raise ValueError("ClickHouse candidate is a self-review")
    final_sha = require_full_sha(
        _flat_row_value(row, "final_head_sha"),
        "ClickHouse candidate.final_head_sha",
    )
    merge_sha = require_full_sha(
        _flat_row_value(row, "merge_commit_sha", "m.merge_commit_sha"),
        "ClickHouse candidate.merge_commit_sha",
    )
    event_base = require_full_sha(
        _flat_row_value(row, "event_base_sha"),
        "ClickHouse candidate.event_base_sha",
    )
    event_head = require_full_sha(
        _flat_row_value(row, "event_head_sha"),
        "ClickHouse candidate.event_head_sha",
    )
    original_commit = require_full_sha(
        _flat_row_value(row, "original_commit_id", "c.original_commit_id"),
        "ClickHouse candidate.original_commit_id",
    )
    current_commit = require_full_sha(
        _flat_row_value(row, "commit_id"),
        "ClickHouse candidate.commit_id",
    )
    if original_commit != event_head:
        raise ValueError(
            "ClickHouse root was not created on the event-time PR head"
        )
    created_at = _clickhouse_timestamp(
        _flat_row_value(row, "comment_created_at"),
        "ClickHouse candidate.comment_created_at",
    )
    updated_at = _clickhouse_timestamp(
        _flat_row_value(row, "comment_updated_at"),
        "ClickHouse candidate.comment_updated_at",
    )
    merged_at = _clickhouse_timestamp(
        _flat_row_value(row, "merged_at", "m.merged_at"),
        "ClickHouse candidate.merged_at",
    )
    path = require_text(
        _flat_row_value(row, "path", "c.path"),
        "ClickHouse candidate.path",
    )
    line = _terminal_hunk_right_line(
        _flat_row_value(row, "diff_hunk"),
        row.get("line"),
    )
    body = require_text(
        _flat_row_value(row, "comment_body"),
        "ClickHouse candidate.comment_body",
    )
    target_ref = require_text(
        _flat_row_value(row, "target_ref"),
        "ClickHouse candidate.target_ref",
    )
    if target_ref not in OFFICIAL_TARGET_BRANCHES:
        raise ValueError("ClickHouse candidate target_ref is not approved")
    event_record = {
        "sourceLine": int(row["_line"]),
        "rowSha256": sha256_json(
            {key: value for key, value in row.items() if key != "_line"}
        ),
        "mergedAt": merged_at,
        "authorAssociation": str(row.get("author_association") or ""),
        "requestedAt": _clickhouse_timestamp(
            row.get("requested_at"),
            "ClickHouse candidate.requested_at",
            optional=True,
        ),
        "requestedReviewer": row.get("requested_reviewer") or None,
        "approvedAt": _clickhouse_timestamp(
            row.get("approved_at"),
            "ClickHouse candidate.approved_at",
            optional=True,
        ),
        "approvalReviewer": row.get("approval_reviewer") or None,
        "approvalHeadSha": row.get("approved_head_sha") or None,
        "replyAt": _clickhouse_timestamp(
            row.get("reply_at"),
            "ClickHouse candidate.reply_at",
            optional=True,
        ),
        "replyAuthor": row.get("reply_author") or None,
        "replyBody": row.get("reply_body") or None,
        "replyCommentId": (
            None
            if row.get("reply_comment_id") in (None, "", "0", 0)
            else _positive_int(
                row.get("reply_comment_id"),
                "ClickHouse candidate.reply_comment_id",
            )
        ),
        "hasSuggestionBlock": bool(row.get("has_suggestion_block")),
    }
    root = {
        "id": comment_id,
        "url": f"{OFFICIAL_API_ROOT}/pulls/comments/{comment_id}",
        "html_url": f"{OFFICIAL_WEB_ROOT}/pull/{number}#discussion_r{comment_id}",
        "pull_request_url": f"{OFFICIAL_API_ROOT}/pulls/{number}",
        "in_reply_to_id": None,
        # The public event schema does not expose the review ID.  This value is
        # replaced by official REST hydration before validation or release.
        "pull_request_review_id": comment_id,
        "body": body,
        "created_at": created_at,
        "updated_at": updated_at,
        "user": {"login": reviewer, "type": "User"},
        "commit_id": current_commit,
        "original_commit_id": original_commit,
        "path": path,
        "line": line,
        "original_line": line,
        "start_line": None,
        "original_start_line": None,
        "side": "RIGHT",
        "original_side": "RIGHT",
        "start_side": None,
        "subject_type": "line",
        "diff_hunk": str(row["diff_hunk"]),
        "_clickhouseEvidence": event_record,
    }
    pull = {
        "number": number,
        "url": f"{OFFICIAL_API_ROOT}/pulls/{number}",
        "html_url": f"{OFFICIAL_WEB_ROOT}/pull/{number}",
        "title": require_text(
            _flat_row_value(row, "pr_title"),
            "ClickHouse candidate.pr_title",
        ),
        "body": _flat_row_value(row, "pr_body"),
        "state": "closed",
        "merged": True,
        "merged_at": merged_at,
        "merge_commit_sha": merge_sha,
        "user": {"login": author, "type": "User"},
        "head": {"sha": final_sha},
        "base": {
            "sha": event_base,
            "ref": target_ref,
            "repo": {"full_name": OFFICIAL_REPOSITORY},
        },
    }
    return {
        "number": number,
        "pull": pull,
        "comments": [root],
        "reviews": [],
        "candidateIds": [comment_id],
        "line": int(row["_line"]),
        "sourceMode": "clickhouse-flat",
    }


def _coalesce_flat(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_key: dict[tuple[int, int], Mapping[str, Any]] = {}
    for row in rows:
        key = _flat_candidate_key(row)
        previous = by_key.get(key)
        if previous is None or _flat_candidate_score(row) > _flat_candidate_score(previous):
            by_key[key] = row
    groups: dict[tuple[int, str], dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for key in sorted(by_key):
        row = by_key[key]
        try:
            evidence = _flat_candidate_evidence(row)
        except ValueError as exc:
            rejected.append(
                {
                    "pullRequest": key[0],
                    "sourceCommentId": key[1],
                    "sourceLine": row.get("_line"),
                    "code": "invalid_flat_candidate_evidence",
                    "detail": str(exc),
                }
            )
            continue
        event_base = str(evidence["pull"]["base"]["sha"])
        group_key = (int(evidence["number"]), event_base)
        group = groups.get(group_key)
        if group is None:
            group = {
                **evidence,
                "comments": {},
                "candidateIds": set(),
            }
            groups[group_key] = group
        elif canonical_json(group["pull"]) != canonical_json(evidence["pull"]):
            raise ValueError(
                f"PR {evidence['number']} has conflicting flat pull evidence"
            )
        for comment in evidence["comments"]:
            identifier = int(comment["id"])
            previous = group["comments"].get(identifier)
            if previous is not None and canonical_json(previous) != canonical_json(comment):
                raise ValueError(
                    f"PR {evidence['number']} comment {identifier} conflicts across rows"
                )
            group["comments"][identifier] = comment
        group["candidateIds"].update(evidence["candidateIds"])
        group["line"] = min(int(group["line"]), int(evidence["line"]))
    evidence_groups = [
        {
            **{key: value for key, value in group.items() if key not in {"comments", "candidateIds"}},
            "comments": list(group["comments"].values()),
            "candidateIds": sorted(group["candidateIds"]),
        }
        for _, group in sorted(groups.items())
    ]
    return evidence_groups, rejected


def _candidate_evidence_groups(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    mode = _candidate_evidence_mode(rows)
    if mode == "embedded-rest":
        return _coalesce_embedded(rows), mode, []
    evidence, rejected = _coalesce_flat(rows)
    return evidence, mode, rejected


def _candidate_evidence_mode(rows: Sequence[Mapping[str, Any]]) -> str:
    """Derive one release mode from immutable candidate-row structure."""

    embedded = [
        isinstance(
            _first(
                row,
                ("pull", "pullRequest", "pull_request", "pr", "pull_response"),
            ),
            Mapping,
        )
        for row in rows
    ]
    if all(embedded):
        return "embedded-rest"
    if any(embedded):
        raise ValueError(
            "candidate JSONL cannot mix embedded REST and flat ClickHouse rows"
        )
    return "clickhouse-flat"


def _commit(repository: Path, revision: Any, field: str, git_env: Mapping[str, str]) -> str:
    sha = require_full_sha(revision, field)
    resolved = run(
        ["git", "-C", str(repository), "rev-parse", "--verify", f"{sha}^{{commit}}"],
        env=git_env,
    ).strip()
    if resolved != sha:
        raise ValueError(f"{field} does not resolve to the exact local commit")
    return sha


def _parents(repository: Path, revision: str, git_env: Mapping[str, str]) -> list[str]:
    line = run(
        ["git", "-C", str(repository), "rev-list", "--parents", "-n", "1", revision],
        env=git_env,
    ).strip()
    return line.split()[1:]


def _tree(repository: Path, revision: str, git_env: Mapping[str, str]) -> str:
    """Return the exact tree object named by an already validated commit."""

    return require_full_sha(
        run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(repository),
                "rev-parse",
                "--verify",
                f"{revision}^{{tree}}",
            ],
            env=git_env,
        ).strip(),
        f"tree for {revision}",
    )


def _ancestor(repository: Path, older: str, newer: str, git_env: Mapping[str, str]) -> None:
    run(
        ["git", "-C", str(repository), "merge-base", "--is-ancestor", older, newer],
        env=git_env,
    )


def _git_version(git_env: Mapping[str, str]) -> str:
    return run(["git", "--version"], env=git_env).strip()


def _durable_head_ref(
    repository: Path,
    number: int,
    head_sha: str,
    git_env: Mapping[str, str],
) -> str | None:
    """Return an official PR/branch ref that contains a review or final head."""

    refs = (
        (f"refs/pull/{number}/head", f"refs/benchmark/pull/{number}"),
        *DURABLE_OFFICIAL_HEAD_REFS,
    )
    for source_ref, local_ref in refs:
        present = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(repository),
                "show-ref",
                "--verify",
                "--quiet",
                local_ref,
            ],
            env=dict(git_env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if present.returncode == 1:
            continue
        if present.returncode != 0:
            raise ValueError(f"cannot inspect durable Git ref {local_ref}")
        contained = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(repository),
                "merge-base",
                "--is-ancestor",
                head_sha,
                local_ref,
            ],
            env=dict(git_env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if contained.returncode == 0:
            return source_ref
        if contained.returncode != 1:
            raise ValueError(f"cannot test historical head against {local_ref}")
    return None


def _exact_pull_head_ref(
    repository: Path,
    number: int,
    final_sha: str,
    git_env: Mapping[str, str],
) -> str | None:
    """Return the official PR-head ref only when its prepared tip is exactly F."""

    source_ref = f"refs/pull/{number}/head"
    local_ref = f"refs/benchmark/pull/{number}"
    resolved = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "show-ref",
            "--hash",
            "--verify",
            local_ref,
        ],
        env=dict(git_env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if resolved.returncode == 1:
        return None
    if resolved.returncode != 0:
        raise ValueError(f"cannot inspect prepared pull ref {local_ref}")
    if resolved.stdout.strip() != final_sha:
        return None
    return source_ref


def _durable_target_ref(
    repository: Path,
    target_branch: str,
    commit_sha: str,
    git_env: Mapping[str, str],
) -> str | None:
    """Return the retained official target ref that contains a target commit."""

    try:
        source_ref, local_ref = DURABLE_OFFICIAL_TARGET_REFS[target_branch]
    except KeyError as exc:
        raise ValueError(
            f"pull base ref {target_branch!r} has no durable official mapping"
        ) from exc
    present = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "show-ref",
            "--verify",
            "--quiet",
            local_ref,
        ],
        env=dict(git_env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if present.returncode == 1:
        return None
    if present.returncode != 0:
        raise ValueError(f"cannot inspect durable Git ref {local_ref}")
    contained = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "merge-base",
            "--is-ancestor",
            commit_sha,
            local_ref,
        ],
        env=dict(git_env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if contained.returncode == 0:
        return source_ref
    if contained.returncode == 1:
        return None
    raise ValueError(f"cannot test merged commit against {local_ref}")


def _name_status(
    repository: Path,
    base: str,
    head: str,
    git_env: Mapping[str, str],
) -> list[tuple[str, list[str]]]:
    raw = run(
        deterministic_git_diff_command(
            repository,
            "--name-status",
            "-z",
            base,
            head,
        ),
        env=git_env,
    )
    tokens = raw.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    result = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        count = 2 if status.startswith(("R", "C")) else 1
        paths = tokens[index + 1 : index + 1 + count]
        if not status or len(paths) != count:
            raise ValueError("malformed deterministic Git name-status output")
        result.append((status, paths))
        index += count + 1
    return result


def _numstat(
    repository: Path,
    base: str,
    head: str,
    git_env: Mapping[str, str],
) -> dict[str, tuple[int, int]]:
    raw = run(
        deterministic_git_diff_command(
            repository,
            "--numstat",
            "-z",
            base,
            head,
        ),
        env=git_env,
    )
    tokens = raw.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    result: dict[str, tuple[int, int]] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        pieces = token.split("\t", 2)
        if len(pieces) != 3:
            raise ValueError("malformed deterministic Git numstat output")
        added_raw, deleted_raw, path = pieces
        added = 0 if added_raw == "-" else int(added_raw)
        deleted = 0 if deleted_raw == "-" else int(deleted_raw)
        if path:
            final_path = path
            index += 1
        else:
            if index + 2 >= len(tokens):
                raise ValueError("malformed renamed Git numstat output")
            final_path = tokens[index + 2]
            index += 3
        if final_path in result:
            raise ValueError(f"duplicate Git numstat path {final_path}")
        result[final_path] = (added, deleted)
    return result


def _manifest(
    repository: Path,
    base: str,
    head: str,
    git_env: Mapping[str, str],
) -> list[dict[str, Any]]:
    status_names = {
        "A": "added",
        "D": "removed",
        "M": "modified",
        "T": "changed",
        "U": "changed",
        "X": "changed",
        "B": "changed",
    }
    stats = _numstat(repository, base, head, git_env)
    result = []
    for raw_status, paths in _name_status(repository, base, head, git_env):
        code = raw_status[0]
        if code in {"R", "C"}:
            previous, filename = paths
            status = "renamed" if code == "R" else "copied"
        else:
            previous = None
            filename = paths[0]
            status = status_names.get(code)
            if status is None:
                raise ValueError(f"unsupported Git status {raw_status}")
        additions, deletions = stats.get(filename, (0, 0))
        item: dict[str, Any] = {
            "filename": filename,
            "status": status,
            "additions": additions,
            "deletions": deletions,
            "changes": additions + deletions,
        }
        if previous is not None:
            item["previous_filename"] = previous
        result.append(item)
    result.sort(key=lambda item: (item["filename"], item["status"]))
    if set(stats) != {item["filename"] for item in result}:
        raise ValueError("Git name-status and numstat manifests disagree")
    return result


def _snapshot_diff(
    repository: Path,
    base: str,
    head: str,
    git_env: Mapping[str, str],
) -> str:
    return run(
        deterministic_git_diff_command(
            repository,
            "--full-index",
            "--binary",
            "--no-text",
            base,
            head,
        ),
        env=git_env,
    )


def _path_diff(
    repository: Path,
    base: str,
    head: str,
    path: str,
    git_env: Mapping[str, str],
) -> str:
    return run(
        deterministic_git_diff_command(
            repository,
            "--unified=80",
            base,
            head,
            "--",
            f":(literal){path}",
        ),
        env=git_env,
    )


def _right_lines(diff: str) -> set[int]:
    lines: set[int] = set()
    current: int | None = None
    for line in diff.splitlines():
        if line.startswith("@@"):
            match = _HUNK.match(line)
            current = int(match.group(1)) if match else None
            continue
        if current is None or line.startswith("\\ No newline"):
            continue
        if line.startswith("-") and not line.startswith("---"):
            continue
        if not line.startswith("+++"):
            lines.add(current)
            current += 1
    return lines


def _blob_text(
    repository: Path,
    revision: str,
    path: str,
    git_env: Mapping[str, str],
) -> str:
    return run(
        ["git", "-C", str(repository), "show", f"{revision}:{path}"],
        env=git_env,
    )


def _contains_sequence(haystack: Sequence[str], needle: Sequence[str]) -> list[int]:
    if not needle or len(needle) > len(haystack):
        return []
    return [
        index
        for index in range(len(haystack) - len(needle) + 1)
        if list(haystack[index : index + len(needle)]) == list(needle)
    ]


def _diff_applies_replacement(
    diff: str,
    original: Sequence[str],
    replacement: Sequence[str],
) -> bool:
    removed = [line[1:] for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")]
    added = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    original_nonempty = [line for line in original if line.strip()]
    replacement_nonempty = [line for line in replacement if line.strip()]
    return (
        bool(original_nonempty)
        and bool(replacement_nonempty)
        and all(line in removed for line in original_nonempty)
        and all(line in added for line in replacement_nonempty)
    )


def _diff_contains_text_change(diff: str, old: str, new: str | None) -> bool:
    removed = [
        line[1:]
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    added = [
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    if not any(old in line for line in removed):
        return False
    return new is None or any(new in line for line in added)


def _anchored_range_change(
    anchor: Mapping[str, Any],
    transition: Mapping[str, Any],
    transition_diff: str,
) -> dict[str, Any] | None:
    """Prove that H→F removed text on the exact reviewed old-side range."""

    start_line = int(anchor.get("originalStartLine") or anchor["originalLine"])
    end_line = int(anchor["originalLine"])
    old_line: int | None = None
    removed: list[dict[str, Any]] = []
    for raw_line in transition_diff.splitlines():
        match = _FULL_HUNK.match(raw_line)
        if match:
            old_line = int(match.group(1))
            continue
        if old_line is None or raw_line.startswith("\\ No newline"):
            continue
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            if start_line <= old_line <= end_line:
                removed.append(
                    {
                        "line": old_line,
                        "sha256": sha256_text(raw_line[1:]),
                    }
                )
            old_line += 1
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            continue
        elif raw_line.startswith(" "):
            old_line += 1
    if not removed:
        return None
    return {
        "originalRange": {"startLine": start_line, "line": end_line},
        "removedAnchoredLines": removed,
        "pathTransition": dict(transition),
    }


def _explicit_change_request(body: str) -> tuple[str, str | None] | None:
    for pattern in _EXPLICIT_REPLACEMENTS:
        match = pattern.search(body)
        if match:
            old = match.group("old").strip()
            new = match.group("new").strip()
            if old and new and old != new:
                return old, new
    removal = _EXPLICIT_REMOVAL.search(body)
    if removal:
        old = (removal.group("quoted") or removal.group("plain") or "").strip()
        if old:
            return old, None
    return None


def _applied_explicit_change(
    *,
    repository: Path,
    body: str,
    anchor: Mapping[str, Any],
    transition: Mapping[str, Any],
    transition_diff: str,
    final_sha: str,
    git_env: Mapping[str, str],
) -> dict[str, Any] | None:
    requested = _explicit_change_request(body)
    final_path = transition.get("finalPath")
    if requested is None or not isinstance(final_path, str):
        return None
    old, new = requested
    checkpoint_text = _blob_text(
        repository,
        str(anchor["originalCommitId"]),
        str(anchor["path"]),
        git_env,
    )
    final_text = _blob_text(repository, final_sha, final_path, git_env)
    old_before = checkpoint_text.count(old)
    old_after = final_text.count(old)
    new_before = checkpoint_text.count(new) if new is not None else 0
    new_after = final_text.count(new) if new is not None else 0
    if old_before < 1 or old_after >= old_before:
        return None
    if new is not None and new_after <= new_before:
        return None
    if not _diff_contains_text_change(transition_diff, old, new):
        return None
    evidence = {
        "requestedOldTextSha256": sha256_text(old),
        "oldOccurrencesAtH": old_before,
        "oldOccurrencesAtF": old_after,
        "pathTransition": dict(transition),
    }
    if new is not None:
        evidence.update(
            {
                "requestedNewTextSha256": sha256_text(new),
                "newOccurrencesAtH": new_before,
                "newOccurrencesAtF": new_after,
            }
        )
    return evidence


def _php_function_signatures(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    result: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        match = re.search(r"\bfunction\s+&?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
        if not match:
            continue
        pieces = [line[match.start() :]]
        for following in lines[index + 1 : index + 40]:
            if "{" in "\n".join(pieces) or ";" in "\n".join(pieces):
                break
            pieces.append(following)
        signature = "\n".join(pieces)
        terminator = min(
            (position for position in (signature.find("{"), signature.find(";")) if position >= 0),
            default=-1,
        )
        if terminator < 0:
            continue
        declaration = signature[:terminator]
        closing = declaration.rfind(")")
        if closing < 0:
            continue
        result.append(
            {
                "name": match.group(1),
                "line": index + 1,
                "hasReturnType": declaration[closing + 1 :].lstrip().startswith(":"),
                "sha256": sha256_text(declaration),
            }
        )
    return result


def _applied_php_return_type(
    *,
    repository: Path,
    body: str,
    anchor: Mapping[str, Any],
    transition: Mapping[str, Any],
    final_sha: str,
    git_env: Mapping[str, str],
) -> dict[str, Any] | None:
    final_path = transition.get("finalPath")
    if not _PHP_RETURN_TYPE_REQUEST.search(body) or not isinstance(final_path, str):
        return None
    checkpoint = _blob_text(
        repository,
        str(anchor["originalCommitId"]),
        str(anchor["path"]),
        git_env,
    )
    final = _blob_text(repository, final_sha, final_path, git_env)
    anchor_line = int(anchor["originalLine"])
    before = [
        item
        for item in _php_function_signatures(checkpoint)
        if not item["hasReturnType"] and abs(int(item["line"]) - anchor_line) <= 80
    ]
    after_by_name = {
        str(item["name"]): item
        for item in _php_function_signatures(final)
        if item["hasReturnType"]
    }
    matches = [
        (item, after_by_name[str(item["name"])])
        for item in before
        if str(item["name"]) in after_by_name
    ]
    if len(matches) != 1:
        return None
    old, new = matches[0]
    return {
        "functionName": old["name"],
        "functionLineAtH": old["line"],
        "signatureAtHSha256": old["sha256"],
        "signatureAtFSha256": new["sha256"],
        "pathTransition": dict(transition),
    }


def _clickhouse_objective_legitimacy(
    *,
    repository: Path,
    number: int,
    pull_author: str,
    root: Mapping[str, Any],
    anchor: Mapping[str, Any],
    transition: Mapping[str, Any],
    transition_diff: str,
    final_sha: str,
    git_env: Mapping[str, str],
) -> dict[str, Any]:
    source = _mapping(root.get("_clickhouseEvidence"), "ClickHouse event evidence")
    root_created = _timestamp(root.get("created_at"), "root comment.created_at")
    merged_at = _timestamp(source.get("mergedAt"), "ClickHouse mergedAt")
    if root_created > merged_at:
        raise CandidateRejected(
            "post_merge_root_comment",
            f"comment {root.get('id')} was created after the pull request merged",
        )
    source_digest = _sha256(source.get("rowSha256"), "ClickHouse event row digest")

    reply_at_value = source.get("replyAt")
    reply_author = source.get("replyAuthor")
    reply_body = source.get("replyBody")
    reply_id = source.get("replyCommentId")
    if (
        reply_at_value is not None
        and isinstance(reply_author, str)
        and reply_author.casefold() == pull_author.casefold()
        and isinstance(reply_body, str)
        and reply_id is not None
    ):
        reply_at = _timestamp(reply_at_value, "ClickHouse replyAt")
        if (
            reply_at > root_created
            and reply_at <= merged_at
            and _FIX_WORDS.search(reply_body)
            and not _NON_FIX_CONTEXT.search(reply_body)
        ):
            return {
                "status": "accepted",
                "eligible": True,
                "policy": "objective-evidence-only",
                "tier": "author_acknowledged_fix",
                "evidence": {
                    "replyCommentId": int(reply_id),
                    "replyUrl": (
                        f"{OFFICIAL_WEB_ROOT}/pull/{number}#discussion_r{int(reply_id)}"
                    ),
                    "replyCreatedAt": _timestamp_text(reply_at_value, "replyAt"),
                    "replyBodySha256": sha256_text(reply_body),
                    "replyAuthor": reply_author,
                    "clickHouseEventRowSha256": source_digest,
                    "pathTransition": dict(transition),
                },
            }

    requested_value = source.get("requestedAt")
    requested_reviewer = source.get("requestedReviewer")
    approved_value = source.get("approvedAt")
    approval_reviewer = source.get("approvalReviewer")
    approval_sha_value = source.get("approvalHeadSha")
    root_reviewer = _human_login(root.get("user"), "root comment.user")
    if (
        requested_value is not None
        and isinstance(requested_reviewer, str)
        and requested_reviewer.casefold() == root_reviewer.casefold()
        and approved_value is not None
        and isinstance(approval_reviewer, str)
        and approval_reviewer.casefold() == root_reviewer.casefold()
        and approval_sha_value
    ):
        requested_at = _timestamp(requested_value, "ClickHouse requestedAt")
        approved_at = _timestamp(approved_value, "ClickHouse approvedAt")
        if (
            abs((requested_at - root_created).total_seconds()) <= 3600
            and requested_at <= merged_at
            and approved_at > max(root_created, requested_at)
            and approved_at <= merged_at
        ):
            approval_sha = _commit(
                repository,
                approval_sha_value,
                "ClickHouse approvalHeadSha",
                git_env,
            )
            _ancestor(repository, str(anchor["originalCommitId"]), approval_sha, git_env)
            _ancestor(repository, approval_sha, final_sha, git_env)
            return {
                "status": "accepted",
                "eligible": True,
                "policy": "objective-evidence-only",
                "tier": "changes_requested_then_approved",
                "evidence": {
                    "changesRequestedAt": _timestamp_text(requested_value, "requestedAt"),
                    "changesRequestedReviewer": requested_reviewer,
                    "approvalSubmittedAt": _timestamp_text(approved_value, "approvedAt"),
                    "approvalReviewer": approval_reviewer,
                    "approvalCommitSha": approval_sha,
                    "clickHouseEventRowSha256": source_digest,
                    "pathTransition": dict(transition),
                },
            }

    anchor_change = _anchored_range_change(anchor, transition, transition_diff)
    if (
        approved_value is not None
        and isinstance(approval_reviewer, str)
        and approval_reviewer.casefold() == root_reviewer.casefold()
        and approval_sha_value
        and anchor_change is not None
    ):
        approved_at = _timestamp(approved_value, "ClickHouse approvedAt")
        if root_created < approved_at <= merged_at:
            try:
                approval_sha = _commit(
                    repository,
                    approval_sha_value,
                    "ClickHouse approvalHeadSha",
                    git_env,
                )
                _ancestor(
                    repository,
                    str(anchor["originalCommitId"]),
                    approval_sha,
                    git_env,
                )
                _ancestor(repository, approval_sha, final_sha, git_env)
            except (RuntimeError, ValueError):
                approval_sha = None
            if approval_sha is not None:
                return {
                    "status": "accepted",
                    "eligible": True,
                    "policy": "objective-evidence-only",
                    "tier": "reviewer_later_approved_anchor_changed",
                    "evidence": {
                        "approvalSubmittedAt": _timestamp_text(
                            approved_value,
                            "approvedAt",
                        ),
                        "approvalReviewer": approval_reviewer,
                        "approvalCommitSha": approval_sha,
                        "clickHouseEventRowSha256": source_digest,
                        **anchor_change,
                    },
                }

    if (
        requested_value is not None
        and isinstance(requested_reviewer, str)
        and requested_reviewer.casefold() == root_reviewer.casefold()
        and anchor_change is not None
    ):
        requested_at = _timestamp(requested_value, "ClickHouse requestedAt")
        if (
            abs((requested_at - root_created).total_seconds()) <= 3600
            and requested_at <= merged_at
        ):
            return {
                "status": "accepted",
                "eligible": True,
                "policy": "objective-evidence-only",
                "tier": "changes_requested_anchor_changed",
                "evidence": {
                    "changesRequestedAt": _timestamp_text(
                        requested_value,
                        "requestedAt",
                    ),
                    "changesRequestedReviewer": requested_reviewer,
                    "clickHouseEventRowSha256": source_digest,
                    **anchor_change,
                },
            }

    suggestions = _SUGGESTION.findall(str(root.get("body") or ""))
    final_path = transition.get("finalPath")
    if len(suggestions) == 1 and isinstance(final_path, str):
        replacement = suggestions[0].splitlines()
        start_line = int(anchor.get("originalStartLine") or anchor["originalLine"])
        end_line = int(anchor["originalLine"])
        checkpoint_lines = _blob_text(
            repository,
            str(anchor["originalCommitId"]),
            str(anchor["path"]),
            git_env,
        ).splitlines()
        if 1 <= start_line <= end_line <= len(checkpoint_lines):
            original = checkpoint_lines[start_line - 1 : end_line]
            final_lines = _blob_text(repository, final_sha, final_path, git_env).splitlines()
            matches = _contains_sequence(final_lines, replacement)
            close_matches = [
                index for index in matches if abs((index + 1) - start_line) <= 80
            ]
            if (
                replacement
                and replacement != original
                and len(matches) == 1
                and len(close_matches) == 1
                and not _contains_sequence(final_lines, original)
                and _diff_applies_replacement(transition_diff, original, replacement)
            ):
                return {
                    "status": "accepted",
                    "eligible": True,
                    "policy": "objective-evidence-only",
                    "tier": "github_suggestion_applied",
                    "evidence": {
                        "originalRange": {"startLine": start_line, "line": end_line},
                        "originalSha256": sha256_text("\n".join(original)),
                        "suggestionSha256": sha256_text("\n".join(replacement)),
                        "finalMatchStartLine": close_matches[0] + 1,
                        "clickHouseEventRowSha256": source_digest,
                        "pathTransition": dict(transition),
                    },
                }

    explicit = _applied_explicit_change(
        repository=repository,
        body=str(root.get("body") or ""),
        anchor=anchor,
        transition=transition,
        transition_diff=transition_diff,
        final_sha=final_sha,
        git_env=git_env,
    )
    if explicit is not None:
        explicit["clickHouseEventRowSha256"] = source_digest
        return {
            "status": "accepted",
            "eligible": True,
            "policy": "objective-evidence-only",
            "tier": "explicit_code_change_applied",
            "evidence": explicit,
        }

    return_type = _applied_php_return_type(
        repository=repository,
        body=str(root.get("body") or ""),
        anchor=anchor,
        transition=transition,
        final_sha=final_sha,
        git_env=git_env,
    )
    if return_type is not None:
        return_type["clickHouseEventRowSha256"] = source_digest
        return {
            "status": "accepted",
            "eligible": True,
            "policy": "objective-evidence-only",
            "tier": "php_return_type_added",
            "evidence": return_type,
        }

    if anchor_change is not None:
        actionability_terms = sorted(
            {match.group(0).casefold() for match in _ACTIONABLE_REVIEW.finditer(str(root.get("body") or ""))}
        )
        if actionability_terms:
            return {
                "status": "accepted",
                "eligible": True,
                "policy": "objective-evidence-only",
                "tier": "actionable_anchor_change_applied",
                "evidence": {
                    "actionabilityTerms": actionability_terms,
                    "clickHouseEventRowSha256": source_digest,
                    **anchor_change,
                },
            }

    raise CandidateRejected(
        "no_objective_legitimacy_evidence",
        f"comment {root.get('id')} has none of the accepted objective evidence tiers",
    )


def _objective_legitimacy(
    *,
    repository: Path,
    number: int,
    pull_author: str,
    root: Mapping[str, Any],
    anchor: Mapping[str, Any],
    all_comments: Sequence[Mapping[str, Any]],
    reviews: Sequence[Mapping[str, Any]],
    transition: Mapping[str, Any],
    transition_diff: str,
    final_sha: str,
    merged_at: Any,
    git_env: Mapping[str, str],
) -> dict[str, Any]:
    root_id = _positive_int(root.get("id"), "root comment.id")
    root_created = _timestamp(root.get("created_at"), "root comment.created_at")
    merged = _timestamp(merged_at, "pull.merged_at")
    if root_created > merged:
        raise CandidateRejected(
            "post_merge_root_comment",
            f"comment {root_id} was created after the pull request merged",
        )
    author_replies = []
    for candidate in all_comments:
        if candidate.get("in_reply_to_id") != root_id:
            continue
        try:
            reply = _official_reply(candidate, number)
            login = _human_login(reply.get("user"), "review reply.user")
            created = _timestamp(reply.get("created_at"), "review reply.created_at")
            body = require_text(reply.get("body"), "review reply.body")
        except ValueError:
            continue
        if (
            login.casefold() == pull_author.casefold()
            and created > root_created
            and created <= merged
            and _FIX_WORDS.search(body)
            and not _NON_FIX_CONTEXT.search(body)
        ):
            author_replies.append(reply)
    if author_replies:
        reply = min(
            author_replies,
            key=lambda item: (
                _timestamp(item["created_at"], "review reply.created_at"),
                int(item["id"]),
            ),
        )
        return {
            "status": "accepted",
            "eligible": True,
            "policy": "objective-evidence-only",
            "tier": "author_acknowledged_fix",
            "evidence": {
                "replyCommentId": int(reply["id"]),
                "replyUrl": (
                    f"{OFFICIAL_WEB_ROOT}/pull/{number}"
                    f"#discussion_r{int(reply['id'])}"
                ),
                "replyCreatedAt": _timestamp_text(reply["created_at"], "reply.created_at"),
                "replyBodySha256": sha256_text(str(reply["body"])),
                "replyAuthor": _human_login(reply.get("user"), "review reply.user"),
                "candidateReplyObjectSha256": sha256_json(reply),
                "pathTransition": dict(transition),
            },
        }

    reviews_by_id: dict[int, Mapping[str, Any]] = {}
    valid_reviews = []
    for candidate in reviews:
        try:
            review = _official_review(candidate, number)
        except ValueError:
            continue
        reviews_by_id[int(review["id"])] = review
        valid_reviews.append(review)
    root_review_id = int(root["pull_request_review_id"])
    root_review = reviews_by_id.get(root_review_id)
    reviewer = _human_login(root.get("user"), "root comment.user")
    if root_review is not None:
        root_review_user = _human_login(root_review.get("user"), "root submitted review.user")
        submitted = root_review.get("submitted_at")
        if (
            root_review.get("state") == "CHANGES_REQUESTED"
            and root_review_user.casefold() == reviewer.casefold()
            and submitted is not None
        ):
            submitted_at = _timestamp(submitted, "root submitted review.submitted_at")
            if submitted_at > merged:
                submitted_at = None
            approvals = []
            for review in valid_reviews if submitted_at is not None else ():
                if review.get("state") != "APPROVED" or review.get("submitted_at") is None:
                    continue
                approval_user = _human_login(review.get("user"), "approval.user")
                approved_at = _timestamp(review["submitted_at"], "approval.submitted_at")
                if (
                    approval_user.casefold() != reviewer.casefold()
                    or approved_at <= max(root_created, submitted_at)
                    or approved_at > merged
                ):
                    continue
                commit_id = review.get("commit_id")
                if commit_id is None:
                    continue
                try:
                    approval_sha = _commit(repository, commit_id, "approval.commit_id", git_env)
                    _ancestor(repository, str(anchor["originalCommitId"]), approval_sha, git_env)
                    _ancestor(repository, approval_sha, final_sha, git_env)
                except (RuntimeError, ValueError):
                    continue
                approvals.append((approved_at, int(review["id"]), approval_sha, review))
            if approvals:
                _, _, approval_sha, approval = min(
                    approvals,
                    key=lambda item: (item[0], item[1]),
                )
                return {
                    "status": "accepted",
                    "eligible": True,
                    "policy": "objective-evidence-only",
                    "tier": "changes_requested_then_approved",
                    "evidence": {
                        "changesRequestedReviewId": root_review_id,
                        "changesRequestedAt": _timestamp_text(
                            submitted,
                            "root submitted review.submitted_at",
                        ),
                        "changesRequestedReviewer": root_review_user,
                        "candidateChangesRequestedReviewSha256": sha256_json(root_review),
                        "approvalReviewId": int(approval["id"]),
                        "approvalCommitSha": approval_sha,
                        "approvalSubmittedAt": _timestamp_text(
                            approval["submitted_at"], "approval.submitted_at"
                        ),
                        "approvalReviewer": _human_login(
                            approval.get("user"),
                            "approval.user",
                        ),
                        "candidateApprovalReviewSha256": sha256_json(approval),
                        "pathTransition": dict(transition),
                    },
                }

    suggestions = _SUGGESTION.findall(str(root.get("body") or ""))
    final_path = transition.get("finalPath")
    if len(suggestions) == 1 and isinstance(final_path, str):
        replacement = suggestions[0].splitlines()
        start_line = int(anchor.get("originalStartLine") or anchor["originalLine"])
        end_line = int(anchor["originalLine"])
        checkpoint_lines = _blob_text(
            repository,
            str(anchor["originalCommitId"]),
            str(anchor["path"]),
            git_env,
        ).splitlines()
        if 1 <= start_line <= end_line <= len(checkpoint_lines):
            original = checkpoint_lines[start_line - 1 : end_line]
            final_lines = _blob_text(repository, final_sha, final_path, git_env).splitlines()
            matches = _contains_sequence(final_lines, replacement)
            close_matches = [
                index
                for index in matches
                if abs((index + 1) - start_line) <= 80
            ]
            if (
                replacement
                and replacement != original
                and len(matches) == 1
                and len(close_matches) == 1
                and not _contains_sequence(final_lines, original)
                and _diff_applies_replacement(transition_diff, original, replacement)
            ):
                return {
                    "status": "accepted",
                    "eligible": True,
                    "policy": "objective-evidence-only",
                    "tier": "github_suggestion_applied",
                    "evidence": {
                        "originalRange": {"startLine": start_line, "line": end_line},
                        "originalSha256": sha256_text("\n".join(original)),
                        "suggestionSha256": sha256_text("\n".join(replacement)),
                        "finalMatchStartLine": close_matches[0] + 1,
                        "pathTransition": dict(transition),
                    },
                }

    raise CandidateRejected(
        "no_objective_legitimacy_evidence",
        f"comment {root_id} has none of the accepted objective evidence tiers",
    )


def _change_types(manifest: Sequence[Mapping[str, Any]]) -> list[str]:
    tags: set[str] = set()
    for item in manifest:
        path = str(item["filename"]).casefold()
        if "/test" in path or path.startswith("dev/tests/") or path.startswith("test/"):
            tags.add("tests")
        else:
            tags.add("production")
        if path.endswith((".md", ".rst", ".txt")) or "/docs/" in path:
            tags.add("docs")
        if any(token in path for token in ("/api/", "etc/webapi", "graphql", "schema.graphql")):
            tags.add("api")
        if path.endswith((".xml", ".yml", ".yaml", ".json", ".ini")):
            tags.add("config")
        if any(token in path for token in ("db_schema", "setup/patch", "migration")):
            tags.add("schema")
        if any(token in path for token in ("view/frontend", ".phtml", ".less", ".js", ".css")):
            tags.add("frontend")
        if any(token in path for token in ("composer.json", "composer.lock", "package.json")):
            tags.add("dependency")
        if any(token in path for token in ("security", "crypt", "encrypt", "auth", "acl")):
            tags.add("security")
    return sorted(tags)


def _module_roots(manifest: Sequence[Mapping[str, Any]]) -> set[str]:
    roots = set()
    for item in manifest:
        parts = str(item["filename"]).split("/")
        lowered = [part.casefold() for part in parts]
        if len(parts) >= 4 and lowered[:3] == ["app", "code", "magento"]:
            roots.add(f"app/code/Magento/{parts[3]}")
        elif len(parts) >= 4 and lowered[:3] == ["lib", "internal", "magento"]:
            roots.add(f"lib/internal/Magento/{parts[3]}")
        elif len(parts) >= 2:
            roots.add("/".join(parts[:2]))
        else:
            roots.add(parts[0])
    return roots


def _area_for_path(path: str) -> str:
    lowered = path.casefold()
    if any(token in lowered for token in ("catalog", "product", "search", "review")):
        return "catalog"
    if any(token in lowered for token in ("checkout", "sales", "quote", "payment", "shipping")):
        return "checkout_sales"
    if any(token in lowered for token in ("customer", "company", "oauth", "authorization")):
        return "customer_identity"
    if any(token in lowered for token in ("cms", "media", "pagebuilder", "widget")):
        return "content_media"
    if any(token in lowered for token in ("inventory", "msi", "stock", "source")):
        return "inventory"
    if any(token in lowered for token in ("graphql", "webapi", "/api/")):
        return "api_graphql"
    if any(token in lowered for token in ("view/frontend", "theme", ".phtml", ".less", ".js")):
        return "storefront_ui"
    if lowered.startswith(("lib/", "setup/", "app/etc/")) or "/framework/" in lowered:
        return "framework_platform"
    if lowered.startswith(("dev/tests/", "test/", "dev/tools/")):
        return "testing_tooling"
    return "other"


def _area(manifest: Sequence[Mapping[str, Any]]) -> str:
    counts = Counter(_area_for_path(str(item["filename"])) for item in manifest)
    return min(counts, key=lambda area: (-counts[area], area))


def _date_band(merged_at: str) -> str:
    year = _timestamp(merged_at, "mergedAt").year
    if year <= 2021:
        return "legacy_through_2021"
    if year <= 2024:
        return "middle_2022_2024"
    return "recent_2025_plus"


def _complexity(
    manifest: Sequence[Mapping[str, Any]],
    change_types: Sequence[str],
) -> tuple[str, int]:
    lines = sum(int(item["changes"]) for item in manifest)
    modules = len(_module_roots(manifest))
    # File count is already the independently balanced size axis.  Including it
    # here makes the two requested strata mechanically collinear (in practice,
    # every large Magento checkpoint became complex) and can make equal quotas
    # mathematically impossible.  Complexity therefore measures change depth
    # and coupling, not scope a second time.
    score = 0 if lines <= 80 else 1 if lines <= 400 else 2
    score += 0 if modules <= 1 else 1 if modules == 2 else 2
    score += 0 if len(change_types) <= 2 else 1 if len(change_types) == 3 else 2
    if "api" in change_types:
        score += 1
    if "schema" in change_types or "config" in change_types:
        score += 1
    if {"production", "tests"}.issubset(change_types):
        score += 1
    band = "simple" if score <= 2 else "moderate" if score <= 5 else "complex"
    return band, score


def _size(file_count: int) -> str:
    for name in STRATA:
        low, high = SIZE_BANDS[name]
        if low <= file_count <= high:
            return name
    raise ValueError(f"file count {file_count} is outside the 3-80 policy")


def _category(path: str) -> str:
    lowered = path.casefold()
    if "/test" in lowered or lowered.startswith(("dev/tests/", "test/")):
        return "testing"
    if lowered.endswith((".md", ".rst")) or "/docs/" in lowered:
        return "documentation"
    if any(token in lowered for token in ("security", "crypt", "encrypt", "auth", "acl")):
        return "security"
    return "code_quality"


def _eligible_review_path(path: str) -> bool:
    normalized = "/" + path.casefold().strip("/")
    segments = tuple(segment for segment in normalized.split("/") if segment)
    if normalized.startswith(("/dev/", "/generated/", "/var/", "/pub/static/")):
        return False
    if normalized.startswith("/vendor/") and any(
        segment in {"test", "tests"} for segment in segments
    ):
        return False
    return True


def _candidate_receipt_coordinates(
    evidence: Mapping[str, Any],
) -> list[tuple[int, Any]]:
    """Return each coalesced root and the source row that supplied that root."""

    source_lines: dict[int, Any] = {}
    for raw in evidence.get("comments") or []:
        if not isinstance(raw, Mapping):
            continue
        try:
            identifier = _positive_int(raw.get("id"), "review comment.id")
        except ValueError:
            continue
        source_line = evidence.get("line")
        clickhouse_source = raw.get("_clickhouseEvidence")
        if isinstance(clickhouse_source, Mapping):
            root_source_line = clickhouse_source.get("sourceLine")
            if root_source_line is not None:
                source_line = root_source_line
        source_lines[identifier] = source_line

    coordinates = []
    for value in sorted(set(evidence.get("candidateIds") or []), key=int):
        identifier = _positive_int(value, "candidate comment ID")
        coordinates.append(
            (identifier, source_lines.get(identifier, evidence.get("line")))
        )
    return coordinates


def _group_rejection_receipts(
    evidence: Mapping[str, Any],
    *,
    number: int,
    code: str,
    detail: str,
) -> list[dict[str, Any]]:
    """Emit one rejection for every root affected by a group-level failure."""

    coordinates = _candidate_receipt_coordinates(evidence)
    if not coordinates:
        return [
            {
                "pullRequest": number,
                "sourceLine": evidence.get("line"),
                "code": code,
                "detail": detail,
            }
        ]
    return [
        {
            "pullRequest": number,
            "sourceCommentId": comment_id,
            "sourceLine": source_line,
            "code": code,
            "detail": detail,
        }
        for comment_id, source_line in coordinates
    ]


def _materialize_evidence(
    evidence: Mapping[str, Any],
    *,
    repository: Path,
    git_env: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    number = int(evidence["number"])
    rejections: list[dict[str, Any]] = []
    try:
        pull = _official_pull(evidence.get("pull"), number)
        pull_author = _human_login(pull.get("user"), "pull.user")
        final_sha = _commit(
            repository,
            _mapping(pull.get("head"), "pull.head").get("sha"),
            "pull.head.sha",
            git_env,
        )
        merge_sha = _commit(
            repository,
            pull.get("merge_commit_sha"),
            "pull.merge_commit_sha",
            git_env,
        )
        event_base = _commit(
            repository,
            _mapping(pull.get("base"), "pull.base").get("sha"),
            "pull.base.sha",
            git_env,
        )
        target_branch = require_text(
            _mapping(pull.get("base"), "pull.base").get("ref"),
            "pull.base.ref",
        )
        event_base_reachability_ref = _durable_target_ref(
            repository,
            target_branch,
            event_base,
            git_env,
        )
        final_head_reachability_ref = _exact_pull_head_ref(
            repository,
            number,
            final_sha,
            git_env,
        )
        merge_commit_reachability_ref = _durable_target_ref(
            repository,
            target_branch,
            merge_sha,
            git_env,
        )
        merge_parents = _parents(repository, merge_sha, git_env)
        if not merge_parents:
            raise ValueError(
                "merge commit has no parent and cannot represent a merged pull request"
            )
        merge_associated_with_final = (
            merge_sha == final_sha
            or final_sha in merge_parents
            or _tree(repository, merge_sha, git_env)
            == _tree(repository, final_sha, git_env)
        )
    except (RuntimeError, ValueError) as exc:
        return [], _group_rejection_receipts(
            evidence,
            number=number,
            code="invalid_pull_or_merge_evidence",
            detail=str(exc),
        )
    if event_base_reachability_ref is None:
        return [], _group_rejection_receipts(
            evidence,
            number=number,
            code="event_base_not_durably_reachable",
            detail=(
                "event base is not contained by the retained official "
                f"target ref for {target_branch}"
            ),
        )
    if final_head_reachability_ref is None:
        return [], _group_rejection_receipts(
            evidence,
            number=number,
            code="final_head_not_durably_reachable",
            detail="F is not the exact tip of its prepared official pull-head ref",
        )
    if merge_commit_reachability_ref is None:
        return [], _group_rejection_receipts(
            evidence,
            number=number,
            code="merge_commit_not_durably_reachable",
            detail=(
                "merge commit is not contained by the retained official "
                f"target ref for {target_branch}"
            ),
        )
    if not merge_associated_with_final:
        return [], _group_rejection_receipts(
            evidence,
            number=number,
            code="merge_commit_not_associated_with_final_head",
            detail=(
                "M is neither F, a direct child of F, nor an exact-tree "
                "equivalent of F"
            ),
        )

    comments_by_id: dict[int, Mapping[str, Any]] = {}
    for raw in evidence.get("comments") or []:
        if not isinstance(raw, Mapping):
            continue
        try:
            identifier = _positive_int(raw.get("id"), "review comment.id")
        except ValueError:
            continue
        previous = comments_by_id.get(identifier)
        if previous is not None and canonical_json(previous) != canonical_json(raw):
            return [], _group_rejection_receipts(
                evidence,
                number=number,
                code="conflicting_comment_evidence",
                detail=f"comment {identifier} has conflicting raw objects",
            )
        comments_by_id[identifier] = raw
    reviews = [
        item
        for item in (evidence.get("reviews") or [])
        if isinstance(item, Mapping)
    ]
    snapshot_cache: dict[str, dict[str, Any]] = {}
    transition_cache: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
    cases: list[dict[str, Any]] = []

    source_lines = dict(_candidate_receipt_coordinates(evidence))
    for comment_id in sorted(source_lines):
        raw_root = comments_by_id.get(int(comment_id))
        source_line = source_lines[comment_id]
        try:
            if raw_root is None:
                _reject("missing_root_comment", f"comment {comment_id} is absent")
            root, anchor = _official_comment(raw_root, number)
            reviewer = _human_login(root.get("user"), "root comment.user")
            if reviewer.casefold() == pull_author.casefold():
                _reject(
                    "self_review_comment",
                    f"comment {comment_id} was authored by the PR author",
                )
            head_sha = _commit(
                repository,
                anchor["originalCommitId"],
                "review comment.original_commit_id",
                git_env,
            )
            if head_sha not in snapshot_cache:
                head_reachability_ref = _durable_head_ref(
                    repository,
                    number,
                    head_sha,
                    git_env,
                )
                if head_reachability_ref is None:
                    _reject(
                        "review_head_not_durably_reachable",
                        f"comment {comment_id} H is not contained by its official "
                        "pull ref or a retained official Magento branch",
                    )
                base_sha = require_full_sha(
                    run(
                        [
                            "git",
                            "-C",
                            str(repository),
                            "merge-base",
                            head_sha,
                            event_base,
                        ],
                        env=git_env,
                    ).strip(),
                    "derived review merge base",
                )
                if base_sha == head_sha:
                    _reject(
                        "empty_review_snapshot",
                        f"comment {comment_id} has B == H",
                    )
                _ancestor(repository, base_sha, head_sha, git_env)
                manifest = _manifest(repository, base_sha, head_sha, git_env)
                if not 3 <= len(manifest) <= 80:
                    _reject(
                        "file_count_out_of_range",
                        f"comment {comment_id} snapshot changes {len(manifest)} files",
                    )
                diff = _snapshot_diff(repository, base_sha, head_sha, git_env)
                if not diff:
                    _reject(
                        "empty_review_diff",
                        f"comment {comment_id} snapshot diff is empty",
                    )
                changes = _change_types(manifest)
                complexity, complexity_score = _complexity(manifest, changes)
                snapshot_cache[head_sha] = {
                    "eventBaseSha": event_base,
                    "eventBaseReachabilityRef": event_base_reachability_ref,
                    "baseSha": base_sha,
                    "headSha": head_sha,
                    "headReachabilityRef": head_reachability_ref,
                    "fileCount": len(manifest),
                    "additions": sum(int(item["additions"]) for item in manifest),
                    "deletions": sum(int(item["deletions"]) for item in manifest),
                    "diffSha256": sha256_text(diff),
                    "manifestSha256": sha256_json(manifest),
                    "changedFiles": manifest,
                    "_size": _size(len(manifest)),
                    "_complexity": complexity,
                    "_complexityScore": complexity_score,
                    "_area": _area(manifest),
                    "_changeTypes": changes,
                }
            snapshot = snapshot_cache[head_sha]
            path = str(anchor["path"])
            if not _eligible_review_path(path):
                _reject(
                    "comment_path_excluded_by_magento_plugin",
                    f"comment {comment_id} path {path} is excluded by the Magento plugin",
                )
            filenames = {str(item["filename"]) for item in snapshot["changedFiles"]}
            if path not in filenames:
                _reject(
                    "comment_path_not_in_snapshot",
                    f"comment {comment_id} path {path} is not changed in B..H",
                )
            review_path_diff = _path_diff(
                repository,
                str(snapshot["baseSha"]),
                head_sha,
                path,
                git_env,
            )
            anchored_lines = _right_lines(review_path_diff)
            start_line = int(anchor.get("originalStartLine") or anchor["originalLine"])
            end_line = int(anchor["originalLine"])
            if not all(line in anchored_lines for line in range(start_line, end_line + 1)):
                _reject(
                    "anchor_not_in_review_diff",
                    f"comment {comment_id} original range is not on the H-side B..H diff",
                )
            transition_key = (head_sha, path)
            if transition_key not in transition_cache:
                transition_cache[transition_key] = resolve_path_transition(
                    repository,
                    checkpoint_sha=head_sha,
                    final_sha=final_sha,
                    source_path=path,
                    git_env=git_env,
                )
            transition, transition_diff = transition_cache[transition_key]
            if isinstance(root.get("_clickhouseEvidence"), Mapping):
                clickhouse_source = _mapping(
                    root.get("_clickhouseEvidence"),
                    "ClickHouse event evidence",
                )
                legitimacy = _clickhouse_objective_legitimacy(
                    repository=repository,
                    number=number,
                    pull_author=pull_author,
                    root=root,
                    anchor=anchor,
                    transition=transition,
                    transition_diff=transition_diff,
                    final_sha=final_sha,
                    git_env=git_env,
                )
                legitimacy["evidence"]["candidateSourceLine"] = _positive_int(
                    clickhouse_source.get("sourceLine"),
                    "ClickHouse event sourceLine",
                )
            else:
                legitimacy = _objective_legitimacy(
                    repository=repository,
                    number=number,
                    pull_author=pull_author,
                    root=root,
                    anchor=anchor,
                    all_comments=list(comments_by_id.values()),
                    reviews=reviews,
                    transition=transition,
                    transition_diff=transition_diff,
                    final_sha=final_sha,
                    merged_at=pull.get("merged_at"),
                    git_env=git_env,
                )
            legitimacy["evidence"].update(
                {
                    "candidateRootObjectSha256": sha256_json(root),
                    "candidatePullObjectSha256": sha256_json(pull),
                }
            )
            reviewed_at = _timestamp_text(root.get("created_at"), "root.created_at")
            public_snapshot = {
                key: copy.deepcopy(value)
                for key, value in snapshot.items()
                if not key.startswith("_")
            }
            public_snapshot["reviewedAt"] = reviewed_at
            case = {
                "caseId": f"m2-auto-pr-{number}-c{comment_id}-{head_sha[:12]}",
                "sourcePr": {
                    "number": number,
                    "url": pull["html_url"],
                    "title": require_text(pull.get("title"), "pull.title"),
                    "body": pull.get("body") if isinstance(pull.get("body"), str) else "",
                    "author": pull_author,
                    "baseRef": target_branch,
                    "mergedAt": _timestamp_text(pull.get("merged_at"), "pull.merged_at"),
                    "finalHeadSha": final_sha,
                    "finalHeadReachabilityRef": final_head_reachability_ref,
                    "mergeCommitSha": merge_sha,
                    "mergeCommitReachabilityRef": merge_commit_reachability_ref,
                },
                "snapshot": public_snapshot,
                "strata": {
                    "size": snapshot["_size"],
                    "complexity": snapshot["_complexity"],
                    "complexityScore": snapshot["_complexityScore"],
                    "area": snapshot["_area"],
                    "dateBand": _date_band(str(pull["merged_at"])),
                    "changeTypes": snapshot["_changeTypes"],
                },
                "goldenComments": [
                    {
                        "sourceCommentId": int(root["id"]),
                        "url": root["html_url"],
                        "body": str(root["body"]),
                        "path": path,
                        "line": end_line,
                        "startLine": anchor.get("originalStartLine"),
                        "side": anchor["side"],
                        "reviewer": reviewer,
                        "reviewId": int(root["pull_request_review_id"]),
                        "originalCommitId": head_sha,
                        "category": _category(path),
                        "legitimacy": legitimacy,
                    }
                ],
            }
            cases.append(case)
        except CandidateRejected as exc:
            rejections.append(
                {
                    "pullRequest": number,
                    "sourceCommentId": int(comment_id),
                    "sourceLine": source_line,
                    "code": exc.code,
                    "detail": exc.detail,
                }
            )
        except (RuntimeError, ValueError) as exc:
            rejections.append(
                {
                    "pullRequest": number,
                    "sourceCommentId": int(comment_id),
                    "sourceLine": source_line,
                    "code": "invalid_candidate_evidence",
                    "detail": str(exc),
                }
            )
    return cases, rejections


def _case_reviewers(case: Mapping[str, Any]) -> tuple[str, ...]:
    comments = case.get("goldenComments")
    if not isinstance(comments, list) or len(comments) != 1:
        raise ValueError(f"case {case.get('caseId')} must have exactly one golden comment")
    reviewer = require_text(comments[0].get("reviewer"), "golden comment reviewer")
    return (reviewer.casefold(),)


def _case_pr(case: Mapping[str, Any]) -> int:
    source = _mapping(case.get("sourcePr"), "case.sourcePr")
    return _positive_int(source.get("number"), "case.sourcePr.number")


def _canonical_candidates(cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(cases):
        case = dict(_mapping(raw, f"candidate case {index}"))
        case_id = require_text(case.get("caseId"), f"candidate case {index}.caseId")
        strata = _mapping(case.get("strata"), f"candidate {case_id}.strata")
        if strata.get("size") not in STRATA:
            raise ValueError(f"candidate {case_id} has invalid size stratum")
        if strata.get("complexity") not in COMPLEXITIES:
            raise ValueError(f"candidate {case_id} has invalid complexity stratum")
        require_text(strata.get("area"), f"candidate {case_id}.strata.area")
        require_text(strata.get("dateBand"), f"candidate {case_id}.strata.dateBand")
        _case_pr(case)
        _case_reviewers(case)
        previous = by_id.get(case_id)
        if previous is not None and canonical_json(previous) != canonical_json(case):
            raise ValueError(f"candidate caseId {case_id} has conflicting objects")
        by_id[case_id] = case
    return [by_id[case_id] for case_id in sorted(by_id)]


def _selection_counts(cases: Sequence[Mapping[str, Any]]) -> dict[str, Counter[str]]:
    counts: dict[str, Counter[str]] = {
        "size": Counter(),
        "complexity": Counter(),
        "area": Counter(),
        "reviewer": Counter(),
        "dateBand": Counter(),
    }
    for case in cases:
        strata = case["strata"]
        counts["size"][str(strata["size"])] += 1
        counts["complexity"][str(strata["complexity"])] += 1
        counts["area"][str(strata["area"])] += 1
        counts["dateBand"][str(strata["dateBand"])] += 1
        for reviewer in _case_reviewers(case):
            counts["reviewer"][reviewer] += 1
    return counts


def _fits_selection(
    case: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    counts: Mapping[str, Counter[str]],
) -> bool:
    return _fits_selection_state(
        case,
        {_case_pr(item) for item in selected},
        counts,
    )


def _fits_selection_state(
    case: Mapping[str, Any],
    selected_pull_requests: set[int],
    counts: Mapping[str, Counter[str]],
) -> bool:
    size = str(case["strata"]["size"])
    complexity = str(case["strata"]["complexity"])
    if counts["size"][size] >= EXACT_QUOTA or counts["complexity"][complexity] >= EXACT_QUOTA:
        return False
    if _case_pr(case) in selected_pull_requests:
        return False
    area = str(case["strata"]["area"])
    date_band = str(case["strata"]["dateBand"])
    if counts["area"][area] >= DIVERSITY_CAPS["area"]:
        return False
    if counts["dateBand"][date_band] >= DIVERSITY_CAPS["dateBand"]:
        return False
    return all(
        counts["reviewer"][reviewer] < DIVERSITY_CAPS["reviewer"]
        for reviewer in _case_reviewers(case)
    )


def _maxflow_bipartite(
    *,
    left_labels: Sequence[str],
    right_labels: Sequence[str],
    capacities: Mapping[tuple[str, str], int],
    left_capacity: Mapping[str, int],
    right_capacity: Mapping[str, int],
    target: int,
) -> int:
    source = "source"
    sink = "sink"
    graph: dict[str, dict[str, int]] = defaultdict(dict)

    def edge(left: str, right: str, capacity: int) -> None:
        graph[left][right] = capacity
        graph[right].setdefault(left, 0)

    for left in left_labels:
        edge(source, f"left:{left}", max(0, int(left_capacity[left])))
        for right in right_labels:
            edge(
                f"left:{left}",
                f"right:{right}",
                max(0, int(capacities.get((left, right), 0))),
            )
    for right in right_labels:
        edge(
            f"right:{right}",
            sink,
            max(0, int(right_capacity[right])),
        )
    total = 0
    while True:
        parent: dict[str, str | None] = {source: None}
        queue = [source]
        for node in queue:
            for child, capacity in graph[node].items():
                if capacity > 0 and child not in parent:
                    parent[child] = node
                    queue.append(child)
                    if child == sink:
                        break
            if sink in parent:
                break
        if sink not in parent:
            return total
        amount = target
        node = sink
        while parent[node] is not None:
            amount = min(amount, graph[parent[node]][node])
            node = parent[node]  # type: ignore[assignment]
        node = sink
        while parent[node] is not None:
            previous = parent[node]
            graph[previous][node] -= amount
            graph[node][previous] += amount
            node = previous
        total += amount


def _maxflow_quota(
    capacities: Mapping[tuple[str, str], int],
    size_remaining: Mapping[str, int],
    complexity_remaining: Mapping[str, int],
) -> int:
    return _maxflow_bipartite(
        left_labels=STRATA,
        right_labels=COMPLEXITIES,
        capacities=capacities,
        left_capacity=size_remaining,
        right_capacity=complexity_remaining,
        target=sum(size_remaining.values()),
    )


def _remaining_feasible(
    candidates: Sequence[Mapping[str, Any]],
    selected: Sequence[Mapping[str, Any]],
    counts: Mapping[str, Counter[str]],
) -> bool:
    remaining_total = TARGET_CASES - len(selected)
    if remaining_total == 0:
        return all(counts["size"][band] == EXACT_QUOTA for band in STRATA) and all(
            counts["complexity"][band] == EXACT_QUOTA for band in COMPLEXITIES
        )
    if remaining_total < 0:
        return False
    selected_pull_requests = {_case_pr(case) for case in selected}
    available = [
        case
        for case in candidates
        if _fits_selection_state(case, selected_pull_requests, counts)
    ]
    if len({_case_pr(case) for case in available}) < remaining_total:
        return False
    size_remaining = {band: EXACT_QUOTA - counts["size"][band] for band in STRATA}
    complexity_remaining = {
        band: EXACT_QUOTA - counts["complexity"][band]
        for band in COMPLEXITIES
    }
    for band in STRATA:
        if len({_case_pr(case) for case in available if case["strata"]["size"] == band}) < size_remaining[band]:
            return False
    for band in COMPLEXITIES:
        if len({_case_pr(case) for case in available if case["strata"]["complexity"] == band}) < complexity_remaining[band]:
            return False
    capacities = {
        (size, complexity): len(
            {
                _case_pr(case)
                for case in available
                if case["strata"]["size"] == size
                and case["strata"]["complexity"] == complexity
            }
        )
        for size in STRATA
        for complexity in COMPLEXITIES
    }
    if _maxflow_quota(capacities, size_remaining, complexity_remaining) < remaining_total:
        return False

    def capped_dimension_values(
        case: Mapping[str, Any],
        dimension: str,
    ) -> tuple[str, ...]:
        if dimension == "reviewer":
            return _case_reviewers(case)
        return (str(case["strata"][dimension]),)

    # A one-dimensional headroom check misses binding cross-constraints.  For
    # example, the size quotas may require more legacy medium/large PRs than the
    # date cap allows even when both marginal supplies look sufficient.  These
    # small bipartite flows reject such pools before the greedy selector runs.
    exact_dimensions = {
        "size": (STRATA, size_remaining),
        "complexity": (COMPLEXITIES, complexity_remaining),
    }
    for exact_name, (exact_labels, exact_remaining) in exact_dimensions.items():
        for capped_name in ("area", "reviewer", "dateBand"):
            capped_labels = sorted(
                {
                    value
                    for case in available
                    for value in capped_dimension_values(case, capped_name)
                }
            )
            joint_pull_requests: dict[tuple[str, str], set[int]] = defaultdict(set)
            for case in available:
                exact_value = str(case["strata"][exact_name])
                for capped_value in capped_dimension_values(case, capped_name):
                    joint_pull_requests[(exact_value, capped_value)].add(
                        _case_pr(case)
                    )
            joint_capacities = {
                key: len(numbers) for key, numbers in joint_pull_requests.items()
            }
            capped_remaining = {
                value: max(
                    0,
                    DIVERSITY_CAPS[capped_name]
                    - counts[capped_name][value],
                )
                for value in capped_labels
            }
            if (
                _maxflow_bipartite(
                    left_labels=exact_labels,
                    right_labels=capped_labels,
                    capacities=joint_capacities,
                    left_capacity=exact_remaining,
                    right_capacity=capped_remaining,
                    target=remaining_total,
                )
                < remaining_total
            ):
                return False
    def capped_headroom(dimension: str) -> int:
        pull_requests: dict[str, set[int]] = defaultdict(set)
        for case in available:
            values = capped_dimension_values(case, dimension)
            for value in values:
                pull_requests[value].add(_case_pr(case))
        cap = DIVERSITY_CAPS[dimension]
        return sum(
            min(
                len(numbers),
                max(0, cap - counts[dimension][value]),
            )
            for value, numbers in pull_requests.items()
        )

    return all(
        capped_headroom(dimension) >= remaining_total
        for dimension in ("area", "reviewer", "dateBand")
    )


def _selection_tie(seed: str, attempt: int, case_id: str) -> str:
    return sha256_text(f"{seed}\0{attempt}\0{case_id}")


def _case_cell(case: Mapping[str, Any]) -> tuple[str, str]:
    strata = case["strata"]
    return str(strata["size"]), str(strata["complexity"])


def _case_has_official_root_attestation(case: Mapping[str, Any]) -> bool:
    if case.get("_officialRestCacheQualified") is True:
        return True
    comments = case.get("goldenComments")
    if not isinstance(comments, list) or len(comments) != 1:
        return False
    legitimacy = comments[0].get("legitimacy")
    if not isinstance(legitimacy, Mapping):
        return False
    evidence = legitimacy.get("evidence")
    return (
        isinstance(evidence, Mapping)
        and evidence.get("officialRestRootHydrated") is True
    )


def _mark_valid_cached_root_candidates(
    cases: Sequence[dict[str, Any]],
    client: GitHubClient,
) -> int:
    """Prefer candidates with a validated local REST envelope.

    This inspection never performs network I/O. It makes cache-only rebuilds
    deterministic when the frozen release deliberately contains only the 54
    selected official responses, without treating an unvalidated legacy cache
    file as evidence.
    """

    qualified = 0
    for case in cases:
        case.pop("_officialRestCacheQualified", None)
        number = _case_pr(case)
        golden = case["goldenComments"][0]
        comment_id = int(golden["sourceCommentId"])
        response = client.inspect_cached_get(
            f"/repos/{OFFICIAL_REPOSITORY}/pulls/comments/{comment_id}"
        )
        if response is None or response.status != 200:
            continue
        try:
            comment, anchor = _official_comment(response.value, number)
            reviewer = _human_login(comment.get("user"), "cached root.user")
            expected_identity = {
                "id": comment_id,
                "url": golden["url"],
                "path": golden["path"],
                "originalCommitId": golden["originalCommitId"],
                "reviewedAt": case["snapshot"]["reviewedAt"],
            }
            actual_identity = {
                "id": int(comment["id"]),
                "url": comment["html_url"],
                "path": anchor["path"],
                "originalCommitId": anchor["originalCommitId"],
                "reviewedAt": _timestamp_text(
                    comment["created_at"],
                    "cached root.created_at",
                ),
            }
            if actual_identity != expected_identity:
                continue
            _official_rest_projection(comment, anchor, reviewer, golden)
        except (TypeError, ValueError):
            continue
        case["_officialRestCacheQualified"] = True
        qualified += 1
    return qualified


def _prequalify_official_rest_candidates(
    cases: Sequence[dict[str, Any]],
    client: GitHubClient,
) -> tuple[list[dict[str, Any]], int]:
    """Return candidates eligible for the selected-root hydration mode."""

    qualified = _mark_valid_cached_root_candidates(cases, client)
    if not client.offline:
        return list(cases), qualified
    return (
        [
            case
            for case in cases
            if case.get("_officialRestCacheQualified") is True
        ],
        qualified,
    )


def _cell_label(cell: tuple[str, str]) -> str:
    return f"{cell[0]}::{cell[1]}"


def _quota_matrices(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[tuple[str, str], int]]:
    """Enumerate deterministic 3x3 quotas with exact row and column sums."""

    pull_requests: dict[tuple[str, str], set[int]] = defaultdict(set)
    for case in candidates:
        pull_requests[_case_cell(case)].add(_case_pr(case))
    capacity = {
        (size, complexity): len(pull_requests[(size, complexity)])
        for size in STRATA
        for complexity in COMPLEXITIES
    }
    cells = [(size, complexity) for size in STRATA for complexity in COMPLEXITIES]
    matrices: list[dict[tuple[str, str], int]] = []
    # Four cells determine every 3x3 table once all row/column sums are 18.
    for small_simple in range(EXACT_QUOTA + 1):
        for small_moderate in range(EXACT_QUOTA + 1):
            small_complex = EXACT_QUOTA - small_simple - small_moderate
            if small_complex < 0:
                continue
            for medium_simple in range(EXACT_QUOTA + 1):
                large_simple = EXACT_QUOTA - small_simple - medium_simple
                if large_simple < 0:
                    continue
                for medium_moderate in range(EXACT_QUOTA + 1):
                    medium_complex = (
                        EXACT_QUOTA - medium_simple - medium_moderate
                    )
                    large_moderate = (
                        EXACT_QUOTA - small_moderate - medium_moderate
                    )
                    large_complex = (
                        EXACT_QUOTA - small_complex - medium_complex
                    )
                    values = (
                        small_simple,
                        small_moderate,
                        small_complex,
                        medium_simple,
                        medium_moderate,
                        medium_complex,
                        large_simple,
                        large_moderate,
                        large_complex,
                    )
                    if min(values) < 0:
                        continue
                    matrix = dict(zip(cells, values, strict=True))
                    if all(matrix[cell] <= capacity[cell] for cell in cells):
                        matrices.append(matrix)

    def score(matrix: Mapping[tuple[str, str], int]) -> tuple[Any, ...]:
        populated = sum(value > 0 for value in matrix.values())
        utilization = [
            matrix[cell] / capacity[cell]
            for cell in cells
            if capacity[cell] > 0
        ]
        return (
            -populated,
            max(utilization, default=0.0),
            sum(utilization),
            tuple(matrix[cell] for cell in cells),
        )

    return sorted(matrices, key=score)


def _assign_cells_to_pull_requests(
    candidates: Sequence[Mapping[str, Any]],
    matrix: Mapping[tuple[str, str], int],
    *,
    seed: str,
    attempt: int,
) -> list[tuple[tuple[str, str], int]] | None:
    """Find a unique-PR assignment that also enforces the date-band cap."""

    pull_cells: dict[int, set[tuple[str, str]]] = defaultdict(set)
    pull_date: dict[int, str] = {}
    attested_pull_cells: set[tuple[int, tuple[str, str]]] = set()
    for case in candidates:
        number = _case_pr(case)
        cell = _case_cell(case)
        if matrix[cell] == 0:
            continue
        pull_cells[number].add(cell)
        if _case_has_official_root_attestation(case):
            attested_pull_cells.add((number, cell))
        date_band = str(case["strata"]["dateBand"])
        previous = pull_date.setdefault(number, date_band)
        if previous != date_band:
            raise ValueError(f"pull request {number} spans conflicting date bands")

    source = "source"
    sink = "sink"
    graph: dict[str, dict[str, int]] = defaultdict(dict)

    def edge(left: str, right: str, capacity: int) -> None:
        graph[left][right] = capacity
        graph[right].setdefault(left, 0)

    active_cells = [cell for cell, quota in matrix.items() if quota > 0]
    active_cells.sort(
        key=lambda cell: (
            len({number for number, cells in pull_cells.items() if cell in cells})
            / matrix[cell],
            cell,
        )
    )
    assignment_edges: list[tuple[tuple[str, str], int, str, str]] = []
    for cell in active_cells:
        cell_node = f"cell:{_cell_label(cell)}"
        edge(source, cell_node, matrix[cell])
        numbers = sorted(
            (number for number, cells in pull_cells.items() if cell in cells),
            key=lambda number: (
                (number, cell) not in attested_pull_cells,
                _selection_tie(seed, attempt, f"{_cell_label(cell)}:{number}"),
                number,
            ),
        )
        for number in numbers:
            pull_node = f"pull:{number}"
            edge(cell_node, pull_node, 1)
            assignment_edges.append((cell, number, cell_node, pull_node))
    for number in sorted(pull_cells):
        edge(f"pull:{number}", f"date:{pull_date[number]}", 1)
    for date_band in sorted(set(pull_date.values())):
        edge(
            f"date:{date_band}",
            sink,
            DIVERSITY_CAPS["dateBand"],
        )

    total = 0
    while True:
        parent: dict[str, str | None] = {source: None}
        queue = [source]
        for node in queue:
            for child, capacity in graph[node].items():
                if capacity > 0 and child not in parent:
                    parent[child] = node
                    queue.append(child)
                    if child == sink:
                        break
            if sink in parent:
                break
        if sink not in parent:
            break
        amount = TARGET_CASES
        node = sink
        while parent[node] is not None:
            amount = min(amount, graph[parent[node]][node])
            node = parent[node]  # type: ignore[assignment]
        node = sink
        while parent[node] is not None:
            previous = parent[node]
            graph[previous][node] -= amount
            graph[node][previous] += amount
            node = previous
        total += amount
    if total < TARGET_CASES:
        return None
    assignments = [
        (cell, number)
        for cell, number, cell_node, pull_node in assignment_edges
        if graph[cell_node][pull_node] == 0
        and graph[pull_node][cell_node] == 1
    ]
    if len(assignments) != TARGET_CASES:
        raise ValueError("cell/PR/date flow produced an invalid assignment")
    return sorted(assignments, key=lambda item: (item[0], item[1]))


def _select_assigned_cases(
    candidates: Sequence[Mapping[str, Any]],
    assignments: Sequence[tuple[tuple[str, str], int]],
    *,
    seed: str,
    attempt: int,
    node_budget: int = 250_000,
) -> list[dict[str, Any]] | None:
    raw_options: dict[
        tuple[tuple[str, str], int],
        list[Mapping[str, Any]],
    ] = defaultdict(list)
    assigned = set(assignments)
    for case in candidates:
        key = (_case_cell(case), _case_pr(case))
        if key in assigned:
            raw_options[key].append(case)
    options: dict[
        tuple[tuple[str, str], int],
        list[Mapping[str, Any]],
    ] = {}
    for assignment in assignments:
        by_signature: dict[
            tuple[str, tuple[str, ...]],
            tuple[tuple[bool, str, str], Mapping[str, Any]],
        ] = {}
        for case in raw_options[assignment]:
            signature = (
                str(case["strata"]["area"]),
                _case_reviewers(case),
            )
            rank = (
                not _case_has_official_root_attestation(case),
                _selection_tie(seed, attempt, f"case:{case['caseId']}"),
                str(case["caseId"]),
            )
            previous = by_signature.get(signature)
            if previous is None or rank < previous[0]:
                by_signature[signature] = (rank, case)
        options[assignment] = [
            value[1]
            for value in sorted(by_signature.values(), key=lambda value: value[0])
        ]
        if not options[assignment]:
            return None

    selected: list[Mapping[str, Any]] = []
    area_counts: Counter[str] = Counter()
    reviewer_counts: Counter[str] = Counter()
    nodes = 0

    def search(
        remaining: list[tuple[tuple[str, str], int]],
    ) -> list[dict[str, Any]] | None:
        nonlocal nodes
        nodes += 1
        if nodes > node_budget:
            return None
        if not remaining:
            return [dict(case) for case in selected]
        eligible: dict[
            tuple[tuple[str, str], int],
            list[Mapping[str, Any]],
        ] = {}
        for assignment in remaining:
            eligible[assignment] = [
                case
                for case in options[assignment]
                if area_counts[str(case["strata"]["area"])]
                < DIVERSITY_CAPS["area"]
                and all(
                    reviewer_counts[reviewer] < DIVERSITY_CAPS["reviewer"]
                    for reviewer in _case_reviewers(case)
                )
            ]
            if not eligible[assignment]:
                return None
        assignment = min(
            remaining,
            key=lambda item: (
                len(eligible[item]),
                -max(
                    area_counts[str(case["strata"]["area"])]
                    / DIVERSITY_CAPS["area"]
                    for case in eligible[item]
                ),
                _selection_tie(
                    seed,
                    attempt,
                    f"assignment:{_cell_label(item[0])}:{item[1]}",
                ),
            ),
        )

        def score(case: Mapping[str, Any]) -> tuple[Any, ...]:
            area = str(case["strata"]["area"])
            reviewers = _case_reviewers(case)
            pressure = max(
                area_counts[area] / DIVERSITY_CAPS["area"],
                *(
                    reviewer_counts[reviewer] / DIVERSITY_CAPS["reviewer"]
                    for reviewer in reviewers
                ),
            )
            return (
                pressure,
                area_counts[area],
                max(reviewer_counts[reviewer] for reviewer in reviewers),
                _selection_tie(seed, attempt, str(case["caseId"])),
                str(case["caseId"]),
            )

        rest = [item for item in remaining if item != assignment]
        for chosen in sorted(eligible[assignment], key=score):
            area = str(chosen["strata"]["area"])
            reviewers = _case_reviewers(chosen)
            selected.append(chosen)
            area_counts[area] += 1
            for reviewer in reviewers:
                reviewer_counts[reviewer] += 1
            result = search(rest)
            if result is not None:
                return result
            selected.pop()
            area_counts[area] -= 1
            for reviewer in reviewers:
                reviewer_counts[reviewer] -= 1
        return None

    result = search(list(assignments))
    if result is None:
        return None
    try:
        _validate_selected_cases(result)
    except ValueError:
        return None
    return result


def select_balanced_cases(
    cases: Sequence[Mapping[str, Any]],
    *,
    seed: str = DEFAULT_SELECTION_SEED,
) -> list[dict[str, Any]]:
    """Select the fixed 54-case reference set under all published caps."""

    seed = require_text(seed, "selection seed")
    candidates = _canonical_candidates(cases)
    if len(candidates) == TARGET_CASES:
        try:
            _validate_selected_cases(candidates)
        except ValueError as exc:
            raise ValueError(
                "candidate pool cannot satisfy the fixed quotas and diversity caps"
            ) from exc
        return candidates
    if not _remaining_feasible(candidates, [], _selection_counts([])):
        raise ValueError("candidate pool cannot satisfy the fixed quotas and diversity caps")
    chosen = None
    for matrix in _quota_matrices(candidates):
        first_assignment = _assign_cells_to_pull_requests(
            candidates,
            matrix,
            seed=seed,
            attempt=0,
        )
        if first_assignment is None:
            continue
        for attempt in range(SELECTION_ATTEMPTS):
            assignments = (
                first_assignment
                if attempt == 0
                else _assign_cells_to_pull_requests(
                    candidates,
                    matrix,
                    seed=seed,
                    attempt=attempt,
                )
            )
            assert assignments is not None
            chosen = _select_assigned_cases(
                candidates,
                assignments,
                seed=seed,
                attempt=attempt,
            )
            if chosen is not None:
                break
        if chosen is not None:
            break
    if chosen is None:
        raise ValueError("candidate pool has no selection satisfying all fixed constraints")
    chosen = sorted(chosen, key=lambda case: str(case["caseId"]))
    _validate_selected_cases(chosen)
    return chosen


def _validate_selected_cases(cases: Sequence[Mapping[str, Any]]) -> None:
    if len(cases) != TARGET_CASES:
        raise ValueError(f"automatic corpus must contain exactly {TARGET_CASES} cases")
    if len({_case_pr(case) for case in cases}) != TARGET_CASES:
        raise ValueError("automatic corpus must use one unique pull request per case")
    if len({str(case["caseId"]) for case in cases}) != TARGET_CASES:
        raise ValueError("automatic corpus case IDs must be unique")
    counts = _selection_counts(cases)
    if {band: counts["size"][band] for band in STRATA} != {
        band: EXACT_QUOTA for band in STRATA
    }:
        raise ValueError("automatic corpus size quotas are not exactly balanced")
    if {band: counts["complexity"][band] for band in COMPLEXITIES} != {
        band: EXACT_QUOTA for band in COMPLEXITIES
    }:
        raise ValueError("automatic corpus complexity quotas are not exactly balanced")
    for dimension, cap in DIVERSITY_CAPS.items():
        if any(value > cap for value in counts[dimension].values()):
            raise ValueError(f"automatic corpus exceeds the {dimension} diversity cap")


def _official_rest_historical_drift(
    projection: Mapping[str, Any],
    golden: Mapping[str, Any],
) -> list[str]:
    """Name current REST fields that differ from the frozen event-time gold."""

    historical = {
        "body": sha256_text(str(golden["body"])),
        "reviewer": str(golden["reviewer"]),
        "originalLine": int(golden["line"]),
        "originalStartLine": golden["startLine"],
        "originalSide": str(golden["side"]),
    }
    current = {
        "body": projection["bodySha256"],
        "reviewer": projection["reviewer"],
        "originalLine": projection["originalLine"],
        "originalStartLine": projection["originalStartLine"],
        "originalSide": projection["originalSide"],
    }
    return sorted(
        name
        for name in OFFICIAL_REST_PROJECTION_FIELDS
        if current[name] != historical[name]
    )


def _official_rest_projection(
    comment: Mapping[str, Any],
    anchor: Mapping[str, Any],
    reviewer: str,
    golden: Mapping[str, Any],
) -> dict[str, Any]:
    """Project mutable current REST values without rewriting historical gold."""

    projection: dict[str, Any] = {
        "bodySha256": sha256_text(str(comment["body"])),
        "reviewer": reviewer,
        "originalLine": int(anchor["originalLine"]),
        "originalStartLine": anchor["originalStartLine"],
        "originalSide": str(anchor["side"]),
        "reviewId": int(comment["pull_request_review_id"]),
    }
    projection["historicalDriftFields"] = _official_rest_historical_drift(
        projection,
        golden,
    )
    return projection


def _requires_flat_legitimacy_reply(golden: Mapping[str, Any]) -> bool:
    legitimacy = golden.get("legitimacy")
    if not isinstance(legitimacy, Mapping):
        return False
    evidence = legitimacy.get("evidence")
    return (
        legitimacy.get("tier") == "author_acknowledged_fix"
        and isinstance(evidence, Mapping)
        and "clickHouseEventRowSha256" in evidence
    )


def _validated_sealed_rest_get(
    response_value: Any,
    envelope_value: Any,
    *,
    expected_url: str,
    observed_status: Any,
    field: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    response = _mapping(response_value, f"{field}.response")
    envelope = _mapping(envelope_value, f"{field}.restGetEnvelope")
    cached, envelope_error = GitHubClient._validate_cache_envelope(
        envelope,
        expected_url=expected_url,
    )
    if (
        observed_status != 200
        or cached is None
        or cached.status != 200
        or cached.value != response
    ):
        detail = f": {envelope_error}" if envelope_error else ""
        raise ValueError(f"{field} is not a sealed HTTP 200 GET{detail}")
    return response, envelope, sha256_json(response)


def _hydrate_selected_roots(
    cases: Sequence[dict[str, Any]],
    client: GitHubClient,
) -> tuple[list[dict[str, Any]], int, dict[int, Mapping[str, Any]]]:
    """Attest selected roots and identify discovery rows that are not roots.

    GitHub's public event mirror does not expose ``in_reply_to_id``.  A row can
    therefore pass all offline gates and still be a thread reply.  Such rows
    are rejected here so the caller can deterministically refill the balanced
    selection from the remaining pool.
    """

    failures: list[dict[str, Any]] = []
    request_count = 0
    responses: dict[int, Mapping[str, Any]] = {}
    for case in cases:
        number = _case_pr(case)
        golden = case["goldenComments"][0]
        evidence = golden["legitimacy"]["evidence"]
        if evidence.get("officialRestRootHydrated") is True:
            continue
        comment_id = int(golden["sourceCommentId"])
        request_count += 1
        request_path = (
            f"/repos/{OFFICIAL_REPOSITORY}/pulls/comments/{comment_id}"
        )
        try:
            request_method = getattr(client, "request", None)
            if callable(request_method):
                github_response = request_method("GET", request_path)
                raw = github_response.value
                envelope = github_response.cache_envelope
            else:
                # Small unit-test clients may implement only get(). Production
                # releases always use GitHubClient and must carry an envelope.
                raw = client.get(request_path)
                envelope = None
        except RuntimeError as exc:
            detail = str(exc)
            if "failed with HTTP 404:" not in detail and "failed with HTTP 410:" not in detail:
                raise
            failures.append(
                {
                    "caseId": case["caseId"],
                    "pullRequest": number,
                    "sourceCommentId": comment_id,
                    "code": "official_rest_root_unavailable",
                    "detail": (
                        f"official REST no longer exposes selected comment "
                        f"{comment_id}"
                    ),
                }
            )
            continue
        try:
            comment, anchor = _official_comment(raw, number)
            reviewer = _human_login(comment.get("user"), "hydrated root.user")
        except ValueError as exc:
            failures.append(
                {
                    "caseId": case["caseId"],
                    "pullRequest": number,
                    "sourceCommentId": comment_id,
                    "code": "official_rest_root_rejected",
                    "detail": str(exc),
                }
            )
            continue
        expected_identity = {
            "id": comment_id,
            "url": golden["url"],
            "path": golden["path"],
            "originalCommitId": golden["originalCommitId"],
            "reviewedAt": case["snapshot"]["reviewedAt"],
        }
        actual_identity = {
            "id": int(comment["id"]),
            "url": comment["html_url"],
            "path": anchor["path"],
            "originalCommitId": anchor["originalCommitId"],
            "reviewedAt": _timestamp_text(comment["created_at"], "hydrated root.created_at"),
        }
        if actual_identity != expected_identity:
            failures.append(
                {
                    "caseId": case["caseId"],
                    "pullRequest": number,
                    "sourceCommentId": comment_id,
                    "code": "official_rest_root_drift",
                    "detail": (
                        f"official REST hydration drifted for selected comment "
                        f"{comment_id}"
                    ),
                }
            )
            continue
        reply_artifact: dict[str, Any] | None = None
        if _requires_flat_legitimacy_reply(golden):
            reply_id = int(evidence["replyCommentId"])
            reply_path = (
                f"/repos/{OFFICIAL_REPOSITORY}/pulls/comments/{reply_id}"
            )
            if not callable(request_method):
                failures.append(
                    {
                        "caseId": case["caseId"],
                        "pullRequest": number,
                        "sourceCommentId": comment_id,
                        "code": "official_rest_legitimacy_reply_rejected",
                        "detail": (
                            "flat author acknowledgement requires a sealed "
                            "GitHubClient REST GET"
                        ),
                    }
                )
                continue
            request_count += 1
            try:
                github_reply = request_method("GET", reply_path)
                reply, reply_envelope, reply_digest = _validated_sealed_rest_get(
                    github_reply.value,
                    github_reply.cache_envelope,
                    expected_url=f"{OFFICIAL_API_ROOT}/pulls/comments/{reply_id}",
                    observed_status=github_reply.status,
                    field="author acknowledgement reply evidence",
                )
                _official_legitimacy_reply(
                    reply,
                    number=number,
                    root_comment_id=comment_id,
                    pull_author=str(case["sourcePr"]["author"]),
                    evidence=evidence,
                    reviewed_at=str(case["snapshot"]["reviewedAt"]),
                    merged_at=str(case["sourcePr"]["mergedAt"]),
                )
            except RuntimeError as exc:
                detail = str(exc)
                recoverable = any(
                    marker in detail
                    for marker in (
                        "failed with HTTP 404:",
                        "failed with HTTP 410:",
                        "offline GitHub cache miss:",
                        "invalid cached GitHub GET response",
                    )
                )
                if not recoverable:
                    raise
                failures.append(
                    {
                        "caseId": case["caseId"],
                        "pullRequest": number,
                        "sourceCommentId": comment_id,
                        "code": "official_rest_legitimacy_reply_unavailable",
                        "detail": detail,
                    }
                )
                continue
            except (AttributeError, ValueError) as exc:
                failures.append(
                    {
                        "caseId": case["caseId"],
                        "pullRequest": number,
                        "sourceCommentId": comment_id,
                        "code": "official_rest_legitimacy_reply_rejected",
                        "detail": str(exc),
                    }
                )
                continue
            reply_artifact = {
                "replyCommentId": reply_id,
                "responseSha256": reply_digest,
                "response": copy.deepcopy(reply),
                "restGetEnvelope": copy.deepcopy(reply_envelope),
            }
        projection = _official_rest_projection(comment, anchor, reviewer, golden)
        # Flat GitHub-event rows do not expose pull_request_review_id, so this
        # identifier is filled from the selected-root REST response. All
        # historical fields that earned legitimacy remain frozen; the current
        # mutable rendering is retained separately and projected explicitly.
        golden["reviewId"] = int(comment["pull_request_review_id"])
        evidence["officialRestRootResponseSha256"] = sha256_json(comment)
        evidence["officialRestRootHydrated"] = True
        evidence["officialRestProjection"] = projection
        if reply_artifact is not None:
            evidence["officialRestLegitimacyReplyResponseSha256"] = (
                reply_artifact["responseSha256"]
            )
            evidence["officialRestLegitimacyReplyHydrated"] = True
            case["_officialRestLegitimacyReplyEvidence"] = reply_artifact
        if envelope is not None:
            case["_officialRestGetEnvelope"] = copy.deepcopy(envelope)
        responses[comment_id] = comment
    cases_by_id = {str(case["caseId"]): case for case in cases}
    for failure in failures:
        failed_case = cases_by_id.get(str(failure.get("caseId")))
        if failed_case is None:
            continue
        try:
            legitimacy = _mapping(
                failed_case["goldenComments"][0].get("legitimacy"),
                "failed case legitimacy",
            )
            candidate_evidence = _mapping(
                legitimacy.get("evidence"),
                "failed case legitimacy evidence",
            )
            source_line = candidate_evidence.get("candidateSourceLine")
            if source_line is not None:
                failure["sourceLine"] = _positive_int(
                    source_line,
                    "failed case candidateSourceLine",
                )
        except (IndexError, KeyError, TypeError, ValueError):
            # Embedded REST candidates do not carry a flat JSONL row binding.
            continue
    return failures, request_count, responses


def _root_evidence_artifact(
    cases: Sequence[Mapping[str, Any]],
    responses: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    records = []
    for case in cases:
        number = _case_pr(case)
        golden = case["goldenComments"][0]
        comment_id = int(golden["sourceCommentId"])
        response = responses.get(comment_id)
        if response is None:
            raise ValueError(
                f"selected root {comment_id} has no retained official REST response"
            )
        case_object = case if isinstance(case, dict) else None
        envelope_value = (
            case_object.pop("_officialRestGetEnvelope", None)
            if case_object is not None
            else None
        )
        reply_evidence_value = (
            case_object.pop("_officialRestLegitimacyReplyEvidence", None)
            if case_object is not None
            else None
        )
        if case_object is not None:
            case_object.pop("_officialRestCacheQualified", None)
        envelope = _mapping(
            envelope_value,
            f"selected root {comment_id} REST GET envelope",
        )
        envelope_fields = {
            "kind",
            "schema",
            "method",
            "url",
            "status",
            "fetchedAt",
            "etag",
            "headers",
            "value",
            "responseSha256",
            "envelopeSha256",
        }
        if set(envelope) != envelope_fields:
            raise ValueError(
                f"selected root {comment_id} REST GET envelope fields are invalid"
            )
        expected_url = f"{OFFICIAL_API_ROOT}/pulls/comments/{comment_id}"
        if (
            envelope.get("kind") != GITHUB_REST_GET_CACHE_KIND
            or envelope.get("schema") != GITHUB_REST_GET_CACHE_SCHEMA
            or envelope.get("method") != "GET"
            or envelope.get("url") != expected_url
            or envelope.get("status") != 200
        ):
            raise ValueError(
                f"selected root {comment_id} REST GET request identity is invalid"
            )
        fetched_at = _timestamp_text(
            envelope.get("fetchedAt"),
            f"selected root {comment_id} REST GET fetchedAt",
        )
        if fetched_at != envelope.get("fetchedAt"):
            raise ValueError(
                f"selected root {comment_id} REST GET fetchedAt is not canonical UTC"
            )
        headers = _mapping(
            envelope.get("headers"),
            f"selected root {comment_id} REST GET headers",
        )
        if any(
            not isinstance(name, str)
            or not name
            or not isinstance(value, str)
            for name, value in headers.items()
        ):
            raise ValueError(
                f"selected root {comment_id} REST GET headers are invalid"
            )
        folded_headers = [name.casefold() for name in headers]
        if len(folded_headers) != len(set(folded_headers)):
            raise ValueError(
                f"selected root {comment_id} REST GET headers repeat a name"
            )
        etags = [
            value
            for name, value in headers.items()
            if name.casefold() == "etag"
        ]
        expected_etag = etags[0] if len(etags) == 1 else None
        if envelope.get("etag") != expected_etag:
            raise ValueError(
                f"selected root {comment_id} REST GET ETag drifted"
            )
        response_digest = sha256_json(response)
        if (
            envelope.get("value") != response
            or envelope.get("responseSha256") != response_digest
            or envelope.get("envelopeSha256")
            != sha256_json(
                {
                    key: value
                    for key, value in envelope.items()
                    if key != "envelopeSha256"
                }
            )
            or golden["legitimacy"]["evidence"].get(
                "officialRestRootResponseSha256"
            )
            != response_digest
        ):
            raise ValueError(
                f"selected root {comment_id} REST response digest drifted"
            )
        legitimacy_reply_evidence: dict[str, Any] | None = None
        if _requires_flat_legitimacy_reply(golden):
            reply_artifact = _mapping(
                reply_evidence_value,
                f"selected root {comment_id} legitimacy reply evidence",
            )
            _exact_fields(
                reply_artifact,
                {
                    "replyCommentId",
                    "responseSha256",
                    "response",
                    "restGetEnvelope",
                },
                f"selected root {comment_id} legitimacy reply evidence",
            )
            reply_id = _positive_int(
                reply_artifact.get("replyCommentId"),
                f"selected root {comment_id} legitimacy reply ID",
            )
            reply, reply_envelope, reply_digest = _validated_sealed_rest_get(
                reply_artifact.get("response"),
                reply_artifact.get("restGetEnvelope"),
                expected_url=f"{OFFICIAL_API_ROOT}/pulls/comments/{reply_id}",
                observed_status=_mapping(
                    reply_artifact.get("restGetEnvelope"),
                    "legitimacy reply envelope",
                ).get("status"),
                field=f"selected root {comment_id} legitimacy reply evidence",
            )
            legitimacy_evidence = golden["legitimacy"]["evidence"]
            if (
                reply_id != legitimacy_evidence.get("replyCommentId")
                or reply_artifact.get("responseSha256") != reply_digest
                or legitimacy_evidence.get(
                    "officialRestLegitimacyReplyResponseSha256"
                )
                != reply_digest
                or legitimacy_evidence.get(
                    "officialRestLegitimacyReplyHydrated"
                )
                is not True
            ):
                raise ValueError(
                    f"selected root {comment_id} legitimacy reply digest drifted"
                )
            _official_legitimacy_reply(
                reply,
                number=number,
                root_comment_id=comment_id,
                pull_author=str(case["sourcePr"]["author"]),
                evidence=legitimacy_evidence,
                reviewed_at=str(case["snapshot"]["reviewedAt"]),
                merged_at=str(case["sourcePr"]["mergedAt"]),
            )
            legitimacy_reply_evidence = {
                "replyCommentId": reply_id,
                "responseSha256": reply_digest,
                "response": copy.deepcopy(reply),
                "restGetEnvelope": copy.deepcopy(reply_envelope),
            }
        elif reply_evidence_value is not None:
            raise ValueError(
                f"selected root {comment_id} has unexpected legitimacy reply evidence"
            )
        records.append(
            {
                "caseId": case["caseId"],
                "pullRequest": number,
                "sourceCommentId": comment_id,
                "responseSha256": response_digest,
                "response": copy.deepcopy(response),
                "restGetEnvelope": copy.deepcopy(envelope),
                "legitimacyReplyEvidence": legitimacy_reply_evidence,
            }
        )
    records.sort(key=lambda item: (int(item["pullRequest"]), int(item["sourceCommentId"])))
    artifact: dict[str, Any] = {
        "kind": AUTOMATIC_ROOT_EVIDENCE_KIND,
        "repository": OFFICIAL_REPOSITORY,
        "recordCount": len(records),
        "records": records,
    }
    artifact["evidenceDigest"] = sha256_json(artifact)
    return artifact


def _distribution(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = _selection_counts(cases)
    change_types: Counter[str] = Counter()
    for case in cases:
        for tag in case["strata"].get("changeTypes") or []:
            change_types[str(tag)] += 1
    return {
        "caseCount": len(cases),
        "size": {name: counts["size"][name] for name in STRATA},
        "complexity": {
            name: counts["complexity"][name] for name in COMPLEXITIES
        },
        "area": dict(sorted(counts["area"].items())),
        "reviewer": dict(sorted(counts["reviewer"].items())),
        "dateBand": dict(sorted(counts["dateBand"].items())),
        "changeTypes": dict(sorted(change_types.items())),
    }


def _candidate_pool_diagnostics(
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize the eligible pool so failed balancing remains observable."""

    def summarize(values: Iterable[tuple[str, int]]) -> dict[str, Any]:
        case_counts: Counter[str] = Counter()
        pull_requests: dict[str, set[int]] = defaultdict(set)
        for value, number in values:
            case_counts[value] += 1
            pull_requests[value].add(number)
        return {
            value: {
                "caseCount": case_counts[value],
                "pullRequestCount": len(pull_requests[value]),
            }
            for value in sorted(case_counts)
        }

    canonical = _canonical_candidates(cases)
    dimensions: dict[str, Any] = {}
    for name in ("size", "complexity", "area", "dateBand"):
        dimensions[name] = summarize(
            (str(case["strata"][name]), _case_pr(case)) for case in canonical
        )
    dimensions["reviewer"] = summarize(
        (_case_reviewers(case)[0], _case_pr(case)) for case in canonical
    )
    dimensions["targetBranch"] = summarize(
        (str(case["sourcePr"]["baseRef"]), _case_pr(case)) for case in canonical
    )
    dimensions["legitimacyTier"] = summarize(
        (
            str(case["goldenComments"][0]["legitimacy"]["tier"]),
            _case_pr(case),
        )
        for case in canonical
    )
    matrix = {
        size: {
            complexity: len(
                {
                    _case_pr(case)
                    for case in canonical
                    if case["strata"]["size"] == size
                    and case["strata"]["complexity"] == complexity
                }
            )
            for complexity in COMPLEXITIES
        }
        for size in STRATA
    }
    capacities = {
        (size, complexity): matrix[size][complexity]
        for size in STRATA
        for complexity in COMPLEXITIES
    }
    return {
        "caseCount": len(canonical),
        "pullRequestCount": len({_case_pr(case) for case in canonical}),
        "dimensions": dimensions,
        "distinctPullRequestSizeComplexityMatrix": matrix,
        "sizeComplexityQuotaMaxFlow": _maxflow_quota(
            capacities,
            {name: EXACT_QUOTA for name in STRATA},
            {name: EXACT_QUOTA for name in COMPLEXITIES},
        ),
    }


def _selection_policy(seed: str) -> dict[str, Any]:
    return {
        "targetCases": TARGET_CASES,
        "uniquePullRequestPerCase": True,
        "exactSizeQuota": {name: EXACT_QUOTA for name in STRATA},
        "exactComplexityQuota": {
            name: EXACT_QUOTA for name in COMPLEXITIES
        },
        "sizeBands": {
            name: {"minimumFiles": SIZE_BANDS[name][0], "maximumFiles": SIZE_BANDS[name][1]}
            for name in STRATA
        },
        "complexityBands": {
            "simple": {"minimumScore": 0, "maximumScore": 2},
            "moderate": {"minimumScore": 3, "maximumScore": 5},
            "complex": {"minimumScore": 6, "maximumScore": 9},
        },
        "diversityCaps": dict(DIVERSITY_CAPS),
        "dateBands": {
            "legacy_through_2021": {"maximumYear": 2021},
            "middle_2022_2024": {"minimumYear": 2022, "maximumYear": 2024},
            "recent_2025_plus": {"minimumYear": 2025},
        },
        "legitimacyPolicy": {
            "name": "objective-evidence-only",
            "rootMustBeHumanOfficialRestComment": True,
            "pullMustBeMerged": True,
            "tiers": list(LEGITIMACY_TIERS),
        },
        "gitGates": {
            "base": "merge-base(reviewed H, event-time target)",
            "reviewedHeadToFinal": (
                "H..F tree transition; ancestry is not required after a force push"
            ),
            "snapshotDiff": "deterministic B..H Myers full-index",
            "checkpointToFinalPathMustChange": True,
            "minimumFiles": 3,
            "maximumFiles": 80,
        },
        "selectionSeed": seed,
        "sizeComplexityCellPolicy": (
            "maximize populated feasible cells, then minimize peak and total "
            "eligible-pool utilization"
        ),
        "algorithm": (
            "canonical size/complexity quota-matrix enumeration with cell/PR "
            "and capped-dimension max-flow prechecks, followed by fixed-seed "
            "urgency selection with deterministic hashed retries"
        ),
    }


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _validate_manifest(snapshot: Mapping[str, Any], field: str) -> None:
    changed = snapshot.get("changedFiles")
    if not isinstance(changed, list) or not changed:
        raise ValueError(f"{field}.changedFiles must be a non-empty array")
    if snapshot.get("fileCount") != len(changed):
        raise ValueError(f"{field}.fileCount does not match changedFiles")
    filenames = []
    additions = deletions = 0
    for index, item in enumerate(changed):
        item = _mapping(item, f"{field}.changedFiles[{index}]")
        filename = require_text(item.get("filename"), f"{field}.changedFiles[{index}].filename")
        filenames.append(filename)
        if item.get("status") not in {
            "added",
            "removed",
            "modified",
            "renamed",
            "copied",
            "changed",
        }:
            raise ValueError(f"{field}.changedFiles[{index}].status is invalid")
        item_additions = item.get("additions")
        item_deletions = item.get("deletions")
        item_changes = item.get("changes")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (item_additions, item_deletions, item_changes)
        ):
            raise ValueError(f"{field}.changedFiles[{index}] statistics are invalid")
        if item_changes != item_additions + item_deletions:
            raise ValueError(f"{field}.changedFiles[{index}].changes is invalid")
        additions += item_additions
        deletions += item_deletions
    if filenames != sorted(filenames) or len(set(filenames)) != len(filenames):
        raise ValueError(f"{field}.changedFiles must be uniquely sorted")
    if snapshot.get("additions") != additions or snapshot.get("deletions") != deletions:
        raise ValueError(f"{field} aggregate line statistics drifted")
    if _sha256(snapshot.get("manifestSha256"), f"{field}.manifestSha256") != sha256_json(changed):
        raise ValueError(f"{field}.manifestSha256 mismatch")


def _exact_fields(
    value: Mapping[str, Any],
    expected: set[str],
    field: str,
) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValueError(
            f"{field} fields are invalid: missing={missing}, extra={extra}"
        )


def _exact_nonnegative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _canonical_timestamp(value: Any, field: str) -> datetime:
    normalized = _timestamp_text(value, field)
    if value != normalized:
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    return _timestamp(normalized, field)


def _validate_original_range(
    evidence: Mapping[str, Any],
    *,
    golden: Mapping[str, Any],
    field: str,
) -> tuple[int, int]:
    value = _mapping(evidence.get("originalRange"), f"{field}.originalRange")
    _exact_fields(value, {"startLine", "line"}, f"{field}.originalRange")
    start = _positive_int(value.get("startLine"), f"{field}.originalRange.startLine")
    end = _positive_int(value.get("line"), f"{field}.originalRange.line")
    expected_start = int(golden.get("startLine") or golden["line"])
    if start != expected_start or end != int(golden["line"]) or start > end:
        raise ValueError(f"{field}.originalRange drifted from the golden anchor")
    return start, end


def _validate_removed_anchor_lines(
    evidence: Mapping[str, Any],
    *,
    start: int,
    end: int,
    field: str,
) -> None:
    removed = _sequence(
        evidence.get("removedAnchoredLines"),
        f"{field}.removedAnchoredLines",
    )
    if not removed:
        raise ValueError(f"{field}.removedAnchoredLines must not be empty")
    lines: list[int] = []
    for index, item in enumerate(removed):
        item_field = f"{field}.removedAnchoredLines[{index}]"
        _exact_fields(item, {"line", "sha256"}, item_field)
        line = _positive_int(item.get("line"), f"{item_field}.line")
        if not start <= line <= end:
            raise ValueError(f"{item_field}.line is outside originalRange")
        _sha256(item.get("sha256"), f"{item_field}.sha256")
        lines.append(line)
    if lines != sorted(set(lines)):
        raise ValueError(f"{field}.removedAnchoredLines are not canonically ordered")


def _validate_official_rest_projection(
    value: Any,
    *,
    golden: Mapping[str, Any],
    field: str,
) -> Mapping[str, Any]:
    projection = _mapping(value, field)
    _exact_fields(
        projection,
        {
            "bodySha256",
            "reviewer",
            "originalLine",
            "originalStartLine",
            "originalSide",
            "reviewId",
            "historicalDriftFields",
        },
        field,
    )
    _sha256(projection.get("bodySha256"), f"{field}.bodySha256")
    require_text(projection.get("reviewer"), f"{field}.reviewer")
    _positive_int(projection.get("originalLine"), f"{field}.originalLine")
    start_line = projection.get("originalStartLine")
    if start_line is not None:
        _positive_int(start_line, f"{field}.originalStartLine")
        # GitHub can start a multiline range on LEFT and end it on RIGHT.
        # Those numbers belong to different diff-side coordinate spaces, so
        # originalStartLine may be greater than originalLine. The sealed raw
        # REST response retains start_side and is validated separately.
    if projection.get("originalSide") != "RIGHT":
        raise ValueError(f"{field}.originalSide must be RIGHT")
    review_id = _positive_int(projection.get("reviewId"), f"{field}.reviewId")
    if review_id != golden.get("reviewId"):
        raise ValueError(f"{field}.reviewId drifted from the golden comment")
    drift = projection.get("historicalDriftFields")
    if (
        not isinstance(drift, list)
        or drift != sorted(set(drift))
        or any(
            not isinstance(name, str)
            or name not in OFFICIAL_REST_PROJECTION_FIELDS
            for name in drift
        )
    ):
        raise ValueError(f"{field}.historicalDriftFields is invalid")
    if drift != _official_rest_historical_drift(projection, golden):
        raise ValueError(f"{field}.historicalDriftFields is not deterministic")
    return projection


def _validate_legitimacy_evidence(
    legitimacy: Mapping[str, Any],
    *,
    evidence_mode: str,
    source: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    golden: Mapping[str, Any],
    field: str,
) -> None:
    tier = str(legitimacy["tier"])
    evidence = _mapping(legitimacy.get("evidence"), f"{field}.evidence")
    common = {
        "candidateRootObjectSha256",
        "candidatePullObjectSha256",
        "officialRestRootResponseSha256",
        "officialRestRootHydrated",
        "officialRestProjection",
        "pathTransition",
    }
    flat = evidence_mode == "clickhouse-flat"
    if ("clickHouseEventRowSha256" in evidence) is not flat:
        raise ValueError(
            f"{field}.evidence shape does not match {evidence_mode}"
        )
    if flat:
        common.update({"clickHouseEventRowSha256", "candidateSourceLine"})
        _sha256(
            evidence.get("clickHouseEventRowSha256"),
            f"{field}.evidence.clickHouseEventRowSha256",
        )
        _positive_int(
            evidence.get("candidateSourceLine"),
            f"{field}.evidence.candidateSourceLine",
        )
    elif "candidateSourceLine" in evidence:
        raise ValueError(f"{field}.evidence has a source line without a row digest")

    for name in (
        "candidateRootObjectSha256",
        "candidatePullObjectSha256",
        "officialRestRootResponseSha256",
    ):
        _sha256(evidence.get(name), f"{field}.evidence.{name}")
    if evidence.get("officialRestRootHydrated") is not True:
        raise ValueError(f"{field}.evidence lacks official REST root attestation")
    _validate_official_rest_projection(
        evidence.get("officialRestProjection"),
        golden=golden,
        field=f"{field}.evidence.officialRestProjection",
    )

    path = str(golden["path"])
    transition = _mapping(
        evidence.get("pathTransition"),
        f"{field}.evidence.pathTransition",
    )
    transition_digest = _sha256(
        transition.get("diffSha256"),
        f"{field}.evidence.pathTransition.diffSha256",
    )
    validate_path_transition_evidence(
        transition,
        source_path=path,
        diff_sha256=transition_digest,
    )

    reviewed_at = _timestamp(
        snapshot.get("reviewedAt"),
        f"{field}.snapshot.reviewedAt",
    )
    merged_at = _timestamp(
        source.get("mergedAt"),
        f"{field}.sourcePr.mergedAt",
    )
    reviewer = str(golden["reviewer"])
    author = str(source["author"])

    if tier == "author_acknowledged_fix":
        tier_fields = {
            "replyCommentId",
            "replyUrl",
            "replyCreatedAt",
            "replyBodySha256",
            "replyAuthor",
        }
        if flat:
            tier_fields.update(
                {
                    "officialRestLegitimacyReplyResponseSha256",
                    "officialRestLegitimacyReplyHydrated",
                }
            )
        else:
            tier_fields.add("candidateReplyObjectSha256")
        _exact_fields(evidence, common | tier_fields, f"{field}.evidence")
        reply_id = _positive_int(
            evidence.get("replyCommentId"),
            f"{field}.evidence.replyCommentId",
        )
        if evidence.get("replyUrl") != (
            f"{OFFICIAL_WEB_ROOT}/pull/{int(source['number'])}"
            f"#discussion_r{reply_id}"
        ):
            raise ValueError(f"{field}.evidence.replyUrl is not canonical")
        reply_at = _canonical_timestamp(
            evidence.get("replyCreatedAt"),
            f"{field}.evidence.replyCreatedAt",
        )
        if not reviewed_at < reply_at <= merged_at:
            raise ValueError(f"{field}.evidence reply disposition is outside the PR lifetime")
        if str(evidence.get("replyAuthor", "")).casefold() != author.casefold():
            raise ValueError(f"{field}.evidence.replyAuthor is not the PR author")
        _sha256(evidence.get("replyBodySha256"), f"{field}.evidence.replyBodySha256")
        if flat:
            _sha256(
                evidence.get("officialRestLegitimacyReplyResponseSha256"),
                (
                    f"{field}.evidence."
                    "officialRestLegitimacyReplyResponseSha256"
                ),
            )
            if evidence.get("officialRestLegitimacyReplyHydrated") is not True:
                raise ValueError(
                    f"{field}.evidence lacks official REST legitimacy reply"
                )
        else:
            _sha256(
                evidence.get("candidateReplyObjectSha256"),
                f"{field}.evidence.candidateReplyObjectSha256",
            )
        return

    if tier == "changes_requested_then_approved":
        tier_fields = {
            "changesRequestedAt",
            "changesRequestedReviewer",
            "approvalSubmittedAt",
            "approvalReviewer",
            "approvalCommitSha",
        }
        if not flat:
            tier_fields.update(
                {
                    "changesRequestedReviewId",
                    "candidateChangesRequestedReviewSha256",
                    "approvalReviewId",
                    "candidateApprovalReviewSha256",
                }
            )
        _exact_fields(evidence, common | tier_fields, f"{field}.evidence")
        requested_at = _canonical_timestamp(
            evidence.get("changesRequestedAt"),
            f"{field}.evidence.changesRequestedAt",
        )
        approved_at = _canonical_timestamp(
            evidence.get("approvalSubmittedAt"),
            f"{field}.evidence.approvalSubmittedAt",
        )
        if requested_at > merged_at or not max(reviewed_at, requested_at) < approved_at <= merged_at:
            raise ValueError(f"{field}.evidence review disposition is outside the PR lifetime")
        if (
            str(evidence.get("changesRequestedReviewer", "")).casefold()
            != reviewer.casefold()
            or str(evidence.get("approvalReviewer", "")).casefold()
            != reviewer.casefold()
        ):
            raise ValueError(f"{field}.evidence review identity drifted")
        require_full_sha(
            evidence.get("approvalCommitSha"),
            f"{field}.evidence.approvalCommitSha",
        )
        if not flat:
            requested_id = _positive_int(
                evidence.get("changesRequestedReviewId"),
                f"{field}.evidence.changesRequestedReviewId",
            )
            approval_id = _positive_int(
                evidence.get("approvalReviewId"),
                f"{field}.evidence.approvalReviewId",
            )
            if requested_id == approval_id:
                raise ValueError(f"{field}.evidence repeats a review disposition ID")
            _sha256(
                evidence.get("candidateChangesRequestedReviewSha256"),
                f"{field}.evidence.candidateChangesRequestedReviewSha256",
            )
            _sha256(
                evidence.get("candidateApprovalReviewSha256"),
                f"{field}.evidence.candidateApprovalReviewSha256",
            )
        return

    if tier == "reviewer_later_approved_anchor_changed":
        if not flat:
            raise ValueError(f"{field}.evidence tier is unavailable for embedded REST rows")
        tier_fields = {
            "approvalSubmittedAt",
            "approvalReviewer",
            "approvalCommitSha",
            "originalRange",
            "removedAnchoredLines",
        }
        _exact_fields(evidence, common | tier_fields, f"{field}.evidence")
        approved_at = _canonical_timestamp(
            evidence.get("approvalSubmittedAt"),
            f"{field}.evidence.approvalSubmittedAt",
        )
        if not reviewed_at < approved_at <= merged_at:
            raise ValueError(f"{field}.evidence approval is outside the PR lifetime")
        if str(evidence.get("approvalReviewer", "")).casefold() != reviewer.casefold():
            raise ValueError(f"{field}.evidence.approvalReviewer drifted")
        require_full_sha(
            evidence.get("approvalCommitSha"),
            f"{field}.evidence.approvalCommitSha",
        )
        start, end = _validate_original_range(evidence, golden=golden, field=field)
        _validate_removed_anchor_lines(evidence, start=start, end=end, field=field)
        return

    if tier == "changes_requested_anchor_changed":
        if not flat:
            raise ValueError(f"{field}.evidence tier is unavailable for embedded REST rows")
        tier_fields = {
            "changesRequestedAt",
            "changesRequestedReviewer",
            "originalRange",
            "removedAnchoredLines",
        }
        _exact_fields(evidence, common | tier_fields, f"{field}.evidence")
        requested_at = _canonical_timestamp(
            evidence.get("changesRequestedAt"),
            f"{field}.evidence.changesRequestedAt",
        )
        if (
            abs((requested_at - reviewed_at).total_seconds()) > 3600
            or requested_at > merged_at
        ):
            raise ValueError(f"{field}.evidence changes request is outside the PR lifetime")
        if str(evidence.get("changesRequestedReviewer", "")).casefold() != reviewer.casefold():
            raise ValueError(f"{field}.evidence.changesRequestedReviewer drifted")
        start, end = _validate_original_range(evidence, golden=golden, field=field)
        _validate_removed_anchor_lines(evidence, start=start, end=end, field=field)
        return

    if tier == "github_suggestion_applied":
        tier_fields = {
            "originalRange",
            "originalSha256",
            "suggestionSha256",
            "finalMatchStartLine",
        }
        _exact_fields(evidence, common | tier_fields, f"{field}.evidence")
        _validate_original_range(evidence, golden=golden, field=field)
        original_digest = _sha256(
            evidence.get("originalSha256"),
            f"{field}.evidence.originalSha256",
        )
        suggestion_digest = _sha256(
            evidence.get("suggestionSha256"),
            f"{field}.evidence.suggestionSha256",
        )
        if original_digest == suggestion_digest:
            raise ValueError(f"{field}.evidence suggestion does not change the anchor")
        _positive_int(
            evidence.get("finalMatchStartLine"),
            f"{field}.evidence.finalMatchStartLine",
        )
        if transition.get("finalPath") is None:
            raise ValueError(f"{field}.evidence suggestion has no final path")
        return

    if tier == "explicit_code_change_applied":
        if not flat:
            raise ValueError(f"{field}.evidence tier is unavailable for embedded REST rows")
        tier_fields = {
            "requestedOldTextSha256",
            "oldOccurrencesAtH",
            "oldOccurrencesAtF",
        }
        new_fields = {
            "requestedNewTextSha256",
            "newOccurrencesAtH",
            "newOccurrencesAtF",
        }
        if set(evidence) & new_fields:
            tier_fields.update(new_fields)
        _exact_fields(evidence, common | tier_fields, f"{field}.evidence")
        _sha256(
            evidence.get("requestedOldTextSha256"),
            f"{field}.evidence.requestedOldTextSha256",
        )
        old_h = _exact_nonnegative_integer(
            evidence.get("oldOccurrencesAtH"),
            f"{field}.evidence.oldOccurrencesAtH",
        )
        old_f = _exact_nonnegative_integer(
            evidence.get("oldOccurrencesAtF"),
            f"{field}.evidence.oldOccurrencesAtF",
        )
        if old_h < 1 or old_f >= old_h:
            raise ValueError(f"{field}.evidence old occurrence counts are invalid")
        if new_fields <= tier_fields:
            _sha256(
                evidence.get("requestedNewTextSha256"),
                f"{field}.evidence.requestedNewTextSha256",
            )
            new_h = _exact_nonnegative_integer(
                evidence.get("newOccurrencesAtH"),
                f"{field}.evidence.newOccurrencesAtH",
            )
            new_f = _exact_nonnegative_integer(
                evidence.get("newOccurrencesAtF"),
                f"{field}.evidence.newOccurrencesAtF",
            )
            if new_f <= new_h:
                raise ValueError(f"{field}.evidence new occurrence counts are invalid")
        if transition.get("finalPath") is None:
            raise ValueError(f"{field}.evidence explicit change has no final path")
        return

    if tier == "php_return_type_added":
        if not flat:
            raise ValueError(f"{field}.evidence tier is unavailable for embedded REST rows")
        tier_fields = {
            "functionName",
            "functionLineAtH",
            "signatureAtHSha256",
            "signatureAtFSha256",
        }
        _exact_fields(evidence, common | tier_fields, f"{field}.evidence")
        name = require_text(
            evidence.get("functionName"),
            f"{field}.evidence.functionName",
        )
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
            raise ValueError(f"{field}.evidence.functionName is invalid")
        _positive_int(
            evidence.get("functionLineAtH"),
            f"{field}.evidence.functionLineAtH",
        )
        at_h = _sha256(
            evidence.get("signatureAtHSha256"),
            f"{field}.evidence.signatureAtHSha256",
        )
        at_f = _sha256(
            evidence.get("signatureAtFSha256"),
            f"{field}.evidence.signatureAtFSha256",
        )
        if at_h == at_f or transition.get("finalPath") is None:
            raise ValueError(f"{field}.evidence PHP signature transition is invalid")
        return

    if tier == "actionable_anchor_change_applied":
        if not flat:
            raise ValueError(f"{field}.evidence tier is unavailable for embedded REST rows")
        tier_fields = {
            "actionabilityTerms",
            "originalRange",
            "removedAnchoredLines",
        }
        _exact_fields(evidence, common | tier_fields, f"{field}.evidence")
        terms = evidence.get("actionabilityTerms")
        historical_terms = sorted(
            {
                match.group(0).casefold()
                for match in _ACTIONABLE_REVIEW.finditer(str(golden["body"]))
            }
        )
        if (
            not isinstance(terms, list)
            or not terms
            or terms != sorted(set(terms))
            or terms != historical_terms
            or any(
                not isinstance(term, str)
                or term != term.casefold()
                or _ACTIONABLE_REVIEW.fullmatch(term) is None
                for term in terms
            )
        ):
            raise ValueError(f"{field}.evidence actionability terms drifted")
        start, end = _validate_original_range(evidence, golden=golden, field=field)
        _validate_removed_anchor_lines(evidence, start=start, end=end, field=field)
        return

    raise ValueError(f"{field}.evidence tier is unsupported")


def validate_automatic_corpus(value: Any) -> dict[str, Any]:
    """Validate scorer readiness without consulting mutable network state."""

    corpus = _mapping(value, "automatic corpus")
    required = {
        "kind",
        "repository",
        "corpusId",
        "corpusDigest",
        "scoringReady",
        "paperReady",
        "metricSemantics",
        "selectionPolicy",
        "distribution",
        "provenance",
        "cases",
    }
    if set(corpus) != required:
        raise ValueError("automatic corpus top-level fields are invalid")
    if corpus.get("kind") != AUTOMATIC_CORPUS_KIND:
        raise ValueError("automatic corpus kind is invalid")
    if corpus.get("repository") != OFFICIAL_REPOSITORY:
        raise ValueError("automatic corpus repository is invalid")
    if corpus.get("corpusId") != "magento2-automatic-review-54":
        raise ValueError("automatic corpus ID is invalid")
    if corpus.get("paperReady") is not False:
        raise ValueError("automatic corpus must never claim paperReady")
    if corpus.get("scoringReady") is not True:
        raise ValueError("automatic corpus is not scoringReady")
    semantics = _mapping(corpus.get("metricSemantics"), "metricSemantics")
    if semantics.get("label") != "reference-set":
        raise ValueError("automatic corpus metrics must be labelled reference-set")
    policy = _mapping(corpus.get("selectionPolicy"), "selectionPolicy")
    if policy.get("targetCases") != TARGET_CASES:
        raise ValueError("automatic corpus target case policy drifted")
    if policy.get("exactSizeQuota") != {name: EXACT_QUOTA for name in STRATA}:
        raise ValueError("automatic corpus size policy drifted")
    if policy.get("exactComplexityQuota") != {
        name: EXACT_QUOTA for name in COMPLEXITIES
    }:
        raise ValueError("automatic corpus complexity policy drifted")
    if policy.get("diversityCaps") != DIVERSITY_CAPS:
        raise ValueError("automatic corpus diversity policy drifted")
    provenance = _mapping(corpus.get("provenance"), "provenance")
    candidate_pool = _mapping(
        provenance.get("candidatePool"),
        "provenance.candidatePool",
    )
    evidence_mode = candidate_pool.get("evidenceMode")
    if evidence_mode not in {"clickhouse-flat", "embedded-rest"}:
        raise ValueError("automatic corpus candidate evidence mode is invalid")
    hydration = _mapping(
        provenance.get("officialRestSelectedRootHydration"),
        "provenance.officialRestSelectedRootHydration",
    )
    require_text(
        hydration.get("evidenceArtifact"),
        "official REST root evidence artifact",
    )
    _sha256(
        hydration.get("evidenceDigest"),
        "official REST root evidence digest",
    )
    if hydration.get("attestedRootCount") != TARGET_CASES:
        raise ValueError("official REST root attestation count drifted")
    cases = corpus.get("cases")
    if not isinstance(cases, list):
        raise ValueError("automatic corpus cases must be an array")
    canonical = _canonical_candidates(cases)
    if [case["caseId"] for case in cases] != [case["caseId"] for case in canonical]:
        raise ValueError("automatic corpus cases must be canonically sorted")
    comment_ids: set[int] = set()
    for index, case in enumerate(canonical):
        field = f"cases[{index}]"
        source = _mapping(case.get("sourcePr"), f"{field}.sourcePr")
        expected_source_fields = {
            "number",
            "url",
            "title",
            "body",
            "author",
            "baseRef",
            "mergedAt",
            "finalHeadSha",
            "finalHeadReachabilityRef",
            "mergeCommitSha",
            "mergeCommitReachabilityRef",
        }
        if set(source) != expected_source_fields:
            raise ValueError(f"{field}.sourcePr fields are invalid")
        number = _positive_int(source.get("number"), f"{field}.sourcePr.number")
        if source.get("url") != f"{OFFICIAL_WEB_ROOT}/pull/{number}":
            raise ValueError(f"{field}.sourcePr.url is not canonical")
        require_text(source.get("title"), f"{field}.sourcePr.title")
        if not isinstance(source.get("body"), str):
            raise ValueError(f"{field}.sourcePr.body must be a string")
        author = require_text(source.get("author"), f"{field}.sourcePr.author")
        base_ref = source.get("baseRef")
        if base_ref not in OFFICIAL_TARGET_BRANCHES:
            raise ValueError(f"{field}.sourcePr.baseRef is invalid")
        merged_at = _timestamp(
            source.get("mergedAt"),
            f"{field}.sourcePr.mergedAt",
        )
        require_full_sha(source.get("finalHeadSha"), f"{field}.sourcePr.finalHeadSha")
        require_full_sha(source.get("mergeCommitSha"), f"{field}.sourcePr.mergeCommitSha")
        expected_final_head_ref = f"refs/pull/{number}/head"
        allowed_reachability_refs = {
            expected_final_head_ref,
            *(source_ref for source_ref, _local_ref in DURABLE_OFFICIAL_HEAD_REFS),
        }
        if source.get("finalHeadReachabilityRef") != expected_final_head_ref:
            raise ValueError(
                f"{field}.sourcePr.finalHeadReachabilityRef is invalid"
            )
        expected_merge_ref = DURABLE_OFFICIAL_TARGET_REFS[str(base_ref)][0]
        if source.get("mergeCommitReachabilityRef") != expected_merge_ref:
            raise ValueError(
                f"{field}.sourcePr.mergeCommitReachabilityRef is invalid"
            )
        snapshot = _mapping(case.get("snapshot"), f"{field}.snapshot")
        for name in ("eventBaseSha", "baseSha", "headSha"):
            require_full_sha(snapshot.get(name), f"{field}.snapshot.{name}")
        if snapshot.get("eventBaseReachabilityRef") != expected_merge_ref:
            raise ValueError(
                f"{field}.snapshot.eventBaseReachabilityRef is invalid"
            )
        if snapshot.get("headReachabilityRef") not in allowed_reachability_refs:
            raise ValueError(f"{field}.snapshot.headReachabilityRef is invalid")
        if snapshot["baseSha"] == snapshot["headSha"]:
            raise ValueError(f"{field} has an empty B..H identity")
        reviewed_at = _timestamp(
            snapshot.get("reviewedAt"),
            f"{field}.snapshot.reviewedAt",
        )
        if reviewed_at > merged_at:
            raise ValueError(f"{field}.snapshot.reviewedAt is after mergedAt")
        _sha256(snapshot.get("diffSha256"), f"{field}.snapshot.diffSha256")
        _validate_manifest(snapshot, f"{field}.snapshot")
        manifest = _sequence(
            snapshot.get("changedFiles"),
            f"{field}.snapshot.changedFiles",
        )
        strata = _mapping(case.get("strata"), f"{field}.strata")
        expected_strata_fields = {
            "size",
            "complexity",
            "complexityScore",
            "area",
            "dateBand",
            "changeTypes",
        }
        if set(strata) != expected_strata_fields:
            raise ValueError(f"{field}.strata fields are invalid")
        expected_size = _size(int(snapshot["fileCount"]))
        expected_change_types = _change_types(manifest)
        expected_complexity, expected_score = _complexity(
            manifest,
            expected_change_types,
        )
        expected_area = _area(manifest)
        expected_date_band = _date_band(str(source["mergedAt"]))
        if strata.get("size") != expected_size:
            raise ValueError(f"{field} size stratum does not match file count")
        if (
            strata.get("complexityScore") != expected_score
            or strata.get("complexity") != expected_complexity
        ):
            raise ValueError(f"{field} complexity stratum drifted from manifest")
        if strata.get("changeTypes") != expected_change_types:
            raise ValueError(f"{field} changeTypes drifted from manifest")
        if strata.get("area") != expected_area:
            raise ValueError(f"{field} area drifted from manifest")
        if strata.get("dateBand") != expected_date_band:
            raise ValueError(f"{field} dateBand drifted from mergedAt")
        golden = case["goldenComments"][0]
        comment_id = _positive_int(golden.get("sourceCommentId"), f"{field}.golden comment ID")
        if comment_id in comment_ids:
            raise ValueError("automatic corpus repeats a source review comment")
        comment_ids.add(comment_id)
        if golden.get("url") != f"{OFFICIAL_WEB_ROOT}/pull/{number}#discussion_r{comment_id}":
            raise ValueError(f"{field}.golden comment URL is not canonical")
        require_text(golden.get("body"), f"{field}.golden comment body")
        path = require_text(golden.get("path"), f"{field}.golden comment path")
        if path not in {item["filename"] for item in snapshot["changedFiles"]}:
            raise ValueError(f"{field}.golden comment path is not in changedFiles")
        if golden.get("side") != "RIGHT":
            raise ValueError(f"{field}.golden comment is not a RIGHT-side anchor")
        _positive_int(golden.get("line"), f"{field}.golden comment line")
        start_line = golden.get("startLine")
        if start_line is not None:
            _positive_int(start_line, f"{field}.golden comment startLine")
            if start_line > golden["line"]:
                raise ValueError(f"{field}.golden comment range is inverted")
        reviewer = require_text(golden.get("reviewer"), f"{field}.golden comment reviewer")
        if reviewer.casefold() == author.casefold():
            raise ValueError(f"{field}.golden comment is a self-review")
        _positive_int(golden.get("reviewId"), f"{field}.golden comment reviewId")
        if golden.get("originalCommitId") != snapshot["headSha"]:
            raise ValueError(f"{field}.golden comment is not bound to snapshot H")
        legitimacy = _mapping(golden.get("legitimacy"), f"{field}.legitimacy")
        if (
            legitimacy.get("status") != "accepted"
            or legitimacy.get("eligible") is not True
            or legitimacy.get("policy") != "objective-evidence-only"
            or legitimacy.get("tier") not in LEGITIMACY_TIERS
        ):
            raise ValueError(f"{field}.legitimacy is not objectively eligible")
        _validate_legitimacy_evidence(
            legitimacy,
            evidence_mode=str(evidence_mode),
            source=source,
            snapshot=snapshot,
            golden=golden,
            field=f"{field}.legitimacy",
        )
    _validate_selected_cases(canonical)
    expected_distribution = _distribution(canonical)
    if corpus.get("distribution") != expected_distribution:
        raise ValueError("automatic corpus distribution drifted")
    digest = _sha256(corpus.get("corpusDigest"), "corpusDigest")
    unsigned = dict(corpus)
    unsigned.pop("corpusDigest")
    if digest != sha256_json(unsigned):
        raise ValueError("automatic corpus digest mismatch")
    return {
        "kind": AUTOMATIC_CORPUS_KIND,
        "corpusId": corpus["corpusId"],
        "caseCount": len(canonical),
        "scoringReady": True,
        "paperReady": False,
        "corpusDigest": digest,
        "distribution": expected_distribution,
    }


def validate_automatic_root_evidence(
    corpus_value: Any,
    evidence_value: Any,
) -> dict[str, Any]:
    """Verify the frozen official REST roots against every selected case."""

    validate_automatic_corpus(corpus_value)
    corpus = _mapping(corpus_value, "automatic corpus")
    evidence = _mapping(evidence_value, "automatic root evidence")
    required = {
        "kind",
        "repository",
        "recordCount",
        "records",
        "evidenceDigest",
    }
    if set(evidence) != required:
        raise ValueError("automatic root evidence top-level fields are invalid")
    if evidence.get("kind") != AUTOMATIC_ROOT_EVIDENCE_KIND:
        raise ValueError("automatic root evidence kind is invalid")
    if evidence.get("repository") != OFFICIAL_REPOSITORY:
        raise ValueError("automatic root evidence repository is invalid")
    digest = _sha256(evidence.get("evidenceDigest"), "evidenceDigest")
    unsigned = dict(evidence)
    unsigned.pop("evidenceDigest")
    if digest != sha256_json(unsigned):
        raise ValueError("automatic root evidence digest mismatch")
    provenance = _mapping(corpus["provenance"], "provenance")
    hydration = _mapping(
        provenance.get("officialRestSelectedRootHydration"),
        "provenance.officialRestSelectedRootHydration",
    )
    if hydration.get("evidenceDigest") != digest:
        raise ValueError("corpus does not bind the supplied root evidence")
    records = _sequence(evidence.get("records"), "root evidence.records")
    if evidence.get("recordCount") != TARGET_CASES or len(records) != TARGET_CASES:
        raise ValueError("root evidence must contain exactly 54 records")
    record_order = [
        (
            _positive_int(
                record.get("pullRequest"),
                f"root evidence.records[{index}].pullRequest",
            ),
            _positive_int(
                record.get("sourceCommentId"),
                f"root evidence.records[{index}].sourceCommentId",
            ),
            require_text(
                record.get("caseId"),
                f"root evidence.records[{index}].caseId",
            ),
        )
        for index, record in enumerate(records)
    ]
    if record_order != sorted(record_order):
        raise ValueError("root evidence records are not canonically ordered")
    by_case: dict[str, Mapping[str, Any]] = {}
    for record in records:
        case_id = require_text(record.get("caseId"), "root evidence caseId")
        if case_id in by_case:
            raise ValueError("root evidence repeats a caseId")
        by_case[case_id] = record
    for case in corpus["cases"]:
        case_id = str(case["caseId"])
        record = by_case.get(case_id)
        if record is None:
            raise ValueError(f"root evidence is missing {case_id}")
        number = _case_pr(case)
        golden = case["goldenComments"][0]
        comment_id = int(golden["sourceCommentId"])
        _exact_fields(
            record,
            {
                "caseId",
                "pullRequest",
                "sourceCommentId",
                "responseSha256",
                "response",
                "restGetEnvelope",
                "legitimacyReplyEvidence",
            },
            f"root evidence {case_id}",
        )
        if (
            record.get("pullRequest") != number
            or record.get("sourceCommentId") != comment_id
        ):
            raise ValueError(f"root evidence identity drifted for {case_id}")
        response = _mapping(record.get("response"), f"root evidence {case_id}.response")
        response_digest = sha256_json(response)
        envelope = _mapping(
            record.get("restGetEnvelope"),
            f"root evidence {case_id}.restGetEnvelope",
        )
        expected_url = f"{OFFICIAL_API_ROOT}/pulls/comments/{comment_id}"
        cached_response, envelope_error = GitHubClient._validate_cache_envelope(
            envelope,
            expected_url=expected_url,
        )
        if (
            cached_response is None
            or cached_response.status != 200
            or cached_response.value != response
            or record.get("responseSha256") != response_digest
            or golden["legitimacy"]["evidence"].get(
                "officialRestRootResponseSha256"
            )
            != response_digest
        ):
            detail = f": {envelope_error}" if envelope_error else ""
            raise ValueError(
                f"root evidence response/envelope drifted for {case_id}{detail}"
            )
        comment, anchor = _official_comment(response, number)
        reviewer = _human_login(comment.get("user"), "root evidence user")
        current_projection = _official_rest_projection(
            comment,
            anchor,
            reviewer,
            golden,
        )
        if (
            int(comment["id"]) != comment_id
            or comment["html_url"] != golden["url"]
            or anchor["path"] != golden["path"]
            or anchor["originalCommitId"] != golden["originalCommitId"]
            or _timestamp_text(comment["created_at"], "root evidence created_at")
            != case["snapshot"]["reviewedAt"]
            or current_projection
            != golden["legitimacy"]["evidence"]["officialRestProjection"]
        ):
            raise ValueError(f"root evidence content drifted for {case_id}")
        reply_evidence_value = record.get("legitimacyReplyEvidence")
        if _requires_flat_legitimacy_reply(golden):
            reply_evidence = _mapping(
                reply_evidence_value,
                f"root evidence {case_id}.legitimacyReplyEvidence",
            )
            _exact_fields(
                reply_evidence,
                {
                    "replyCommentId",
                    "responseSha256",
                    "response",
                    "restGetEnvelope",
                },
                f"root evidence {case_id}.legitimacyReplyEvidence",
            )
            reply_id = _positive_int(
                reply_evidence.get("replyCommentId"),
                f"root evidence {case_id} legitimacy reply ID",
            )
            reply_envelope_value = _mapping(
                reply_evidence.get("restGetEnvelope"),
                f"root evidence {case_id} legitimacy reply envelope",
            )
            reply, _reply_envelope, reply_digest = _validated_sealed_rest_get(
                reply_evidence.get("response"),
                reply_envelope_value,
                expected_url=f"{OFFICIAL_API_ROOT}/pulls/comments/{reply_id}",
                observed_status=reply_envelope_value.get("status"),
                field=f"root evidence {case_id}.legitimacyReplyEvidence",
            )
            legitimacy_evidence = golden["legitimacy"]["evidence"]
            if (
                reply_id != legitimacy_evidence.get("replyCommentId")
                or reply_evidence.get("responseSha256") != reply_digest
                or legitimacy_evidence.get(
                    "officialRestLegitimacyReplyResponseSha256"
                )
                != reply_digest
                or legitimacy_evidence.get(
                    "officialRestLegitimacyReplyHydrated"
                )
                is not True
            ):
                raise ValueError(
                    f"root evidence legitimacy reply digest drifted for {case_id}"
                )
            _official_legitimacy_reply(
                reply,
                number=number,
                root_comment_id=comment_id,
                pull_author=str(case["sourcePr"]["author"]),
                evidence=legitimacy_evidence,
                reviewed_at=str(case["snapshot"]["reviewedAt"]),
                merged_at=str(case["sourcePr"]["mergedAt"]),
            )
        elif reply_evidence_value is not None:
            raise ValueError(
                f"root evidence has unexpected legitimacy reply for {case_id}"
            )
    if set(by_case) != {str(case["caseId"]) for case in corpus["cases"]}:
        raise ValueError("root evidence contains an unselected case")
    return {
        "kind": AUTOMATIC_ROOT_EVIDENCE_KIND,
        "recordCount": TARGET_CASES,
        "evidenceDigest": digest,
        "corpusDigest": corpus["corpusDigest"],
    }


def _audit_digest(audit: dict[str, Any]) -> dict[str, Any]:
    value = dict(audit)
    value["auditDigest"] = sha256_json(value)
    return value


def _checksum_manifest_entries(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read checksum manifest {path}: {exc}") from exc
    if not lines:
        raise ValueError("automatic release checksum manifest is empty")
    entries: dict[str, str] = {}
    filenames: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        match = re.fullmatch(r"([0-9a-f]{64}) ([ *])(.+)", line)
        if match is None:
            raise ValueError(
                "automatic release checksum manifest line "
                f"{line_number} is not sha256sum-compatible"
            )
        digest, _marker, filename = match.groups()
        if (
            not filename
            or Path(filename).is_absolute()
            or Path(filename).name != filename
            or filename in {".", ".."}
        ):
            raise ValueError(
                "automatic release checksum manifest filenames must be "
                "single relative path components"
            )
        if filename in entries:
            raise ValueError(
                f"automatic release checksum manifest repeats {filename}"
            )
        entries[filename] = digest
        filenames.append(filename)
    if filenames != sorted(filenames):
        raise ValueError(
            "automatic release checksum manifest entries are not sorted by filename"
        )
    return entries


def _artifact_sha256(path: Path, field: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError(f"cannot read {field} {path}: {exc}") from exc


def _write_checksum_manifest(path: Path, artifacts: Sequence[Path]) -> None:
    if len(artifacts) != 5 or len({artifact.name for artifact in artifacts}) != 5:
        raise ValueError(
            "automatic release checksum manifest requires five distinct basenames"
        )
    lines = [
        f"{_artifact_sha256(artifact, 'release artifact')}  {artifact.name}\n"
        for artifact in sorted(artifacts, key=lambda item: item.name)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text("".join(lines), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_json_artifact(path: Path, field: str) -> tuple[Any, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {field} {path}: {exc}") from exc
    return value, hashlib.sha256(raw).hexdigest()


def _require_exact_integer(value: Any, expected: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise ValueError(f"{field} does not match the supplied release artifact")


def _validate_candidate_pool_diagnostics(
    value: Any,
    *,
    accepted_cases: int,
    accepted_pull_requests: int,
) -> None:
    diagnostics = _mapping(value, "audit.candidatePoolDiagnostics")
    required = {
        "caseCount",
        "pullRequestCount",
        "dimensions",
        "distinctPullRequestSizeComplexityMatrix",
        "sizeComplexityQuotaMaxFlow",
    }
    _exact_fields(diagnostics, required, "audit.candidatePoolDiagnostics")
    _require_exact_integer(
        diagnostics.get("caseCount"),
        accepted_cases,
        "candidate pool diagnostic case count",
    )
    _require_exact_integer(
        diagnostics.get("pullRequestCount"),
        accepted_pull_requests,
        "candidate pool diagnostic pull request count",
    )
    dimensions = _mapping(
        diagnostics.get("dimensions"),
        "audit.candidatePoolDiagnostics.dimensions",
    )
    expected_dimensions = {
        "size",
        "complexity",
        "area",
        "dateBand",
        "reviewer",
        "targetBranch",
        "legitimacyTier",
    }
    _exact_fields(
        dimensions,
        expected_dimensions,
        "audit.candidatePoolDiagnostics.dimensions",
    )
    for name in sorted(expected_dimensions):
        entries = _mapping(
            dimensions.get(name),
            f"audit.candidatePoolDiagnostics.dimensions.{name}",
        )
        observed_cases = 0
        for label, raw_counts in entries.items():
            require_text(label, f"audit candidate pool {name} label")
            counts = _mapping(
                raw_counts,
                f"audit.candidatePoolDiagnostics.dimensions.{name}.{label}",
            )
            _exact_fields(
                counts,
                {"caseCount", "pullRequestCount"},
                f"audit.candidatePoolDiagnostics.dimensions.{name}.{label}",
            )
            case_count = _exact_nonnegative_integer(
                counts.get("caseCount"),
                f"audit candidate pool {name}.{label}.caseCount",
            )
            pull_count = _exact_nonnegative_integer(
                counts.get("pullRequestCount"),
                f"audit candidate pool {name}.{label}.pullRequestCount",
            )
            if pull_count > min(case_count, accepted_pull_requests):
                raise ValueError(
                    f"audit candidate pool {name}.{label} pull count is impossible"
                )
            observed_cases += case_count
        if observed_cases != accepted_cases:
            raise ValueError(
                f"audit candidate pool {name} case counts do not cover the pool"
            )

    raw_matrix = _mapping(
        diagnostics.get("distinctPullRequestSizeComplexityMatrix"),
        "audit candidate pool size/complexity matrix",
    )
    _exact_fields(raw_matrix, set(STRATA), "audit candidate pool size/complexity matrix")
    capacities: dict[tuple[str, str], int] = {}
    for size in STRATA:
        row = _mapping(raw_matrix.get(size), f"audit candidate pool matrix.{size}")
        _exact_fields(row, set(COMPLEXITIES), f"audit candidate pool matrix.{size}")
        for complexity in COMPLEXITIES:
            capacity = _exact_nonnegative_integer(
                row.get(complexity),
                f"audit candidate pool matrix.{size}.{complexity}",
            )
            if capacity > accepted_pull_requests:
                raise ValueError("audit candidate pool matrix capacity is impossible")
            capacities[(size, complexity)] = capacity
    expected_flow = _maxflow_quota(
        capacities,
        {name: EXACT_QUOTA for name in STRATA},
        {name: EXACT_QUOTA for name in COMPLEXITIES},
    )
    _require_exact_integer(
        diagnostics.get("sizeComplexityQuotaMaxFlow"),
        expected_flow,
        "candidate pool diagnostic max flow",
    )
    if expected_flow < TARGET_CASES:
        raise ValueError("audit candidate pool cannot support the selected quotas")


def _validate_audit_rejections(
    audit: Mapping[str, Any],
    *,
    candidate_rows: Sequence[Mapping[str, Any]],
    evidence_mode: str,
) -> None:
    rejections = _sequence(audit.get("rejections"), "audit.rejections")
    _require_exact_integer(
        audit.get("rejectedCandidates"),
        len(rejections),
        "audit rejected candidate count",
    )
    expected_order = sorted(
        rejections,
        key=lambda item: (
            int(item.get("pullRequest") or 0),
            int(item.get("sourceCommentId") or 0),
            str(item.get("code") or ""),
        ),
    )
    if list(rejections) != expected_order:
        raise ValueError("audit rejections are not canonically ordered")
    rows_by_line = {int(row["_line"]): row for row in candidate_rows}
    codes: Counter[str] = Counter()
    for index, rejection in enumerate(rejections):
        field = f"audit.rejections[{index}]"
        code = require_text(rejection.get("code"), f"{field}.code")
        require_text(rejection.get("detail"), f"{field}.detail")
        codes[code] += 1
        number_value = rejection.get("pullRequest")
        comment_value = rejection.get("sourceCommentId")
        if number_value is not None:
            _positive_int(number_value, f"{field}.pullRequest")
        if comment_value is not None:
            _positive_int(comment_value, f"{field}.sourceCommentId")
        source_line = rejection.get("sourceLine")
        if source_line is None:
            continue
        source_line = _positive_int(source_line, f"{field}.sourceLine")
        row = rows_by_line.get(source_line)
        if row is None:
            raise ValueError(f"{field}.sourceLine is absent from candidate JSONL")
        if evidence_mode != "clickhouse-flat":
            continue
        try:
            row_number, row_comment = _flat_candidate_key(row)
        except ValueError:
            continue
        if number_value is not None and int(number_value) != row_number:
            raise ValueError(f"{field}.pullRequest drifted from sourceLine")
        if comment_value is not None and int(comment_value) != row_comment:
            raise ValueError(f"{field}.sourceCommentId drifted from sourceLine")
    expected_counts = dict(sorted(codes.items()))
    if audit.get("rejectionCounts") != expected_counts:
        raise ValueError("audit rejectionCounts drifted from rejections")
    unknown_official_codes = sorted(
        code
        for code in codes
        if code.startswith("official_rest_")
        and code not in OFFICIAL_REST_REJECTION_CODES
    )
    if unknown_official_codes:
        raise ValueError(
            "audit contains unknown official REST rejection codes: "
            f"{unknown_official_codes}"
        )
    official_rest_count = sum(
        count
        for code, count in codes.items()
        if code in OFFICIAL_REST_REJECTION_CODES
    )
    _require_exact_integer(
        audit.get("officialRestRejectedCandidates"),
        official_rest_count,
        "audit official REST rejected candidate count",
    )


def _validate_flat_candidate_accounting(
    audit: Mapping[str, Any],
    *,
    candidate_rows: Sequence[Mapping[str, Any]],
    accepted_cases: int,
) -> None:
    """Prove every flat input row has one pre-hydration outcome.

    Official REST failures occur after Git materialization, so those receipts
    overlap the accepted partition rather than consuming another input row.
    """

    rows_by_line = {int(row["_line"]): row for row in candidate_rows}
    identities_by_line = {
        line: _flat_candidate_key(row) for line, row in rows_by_line.items()
    }
    materialization_lines: set[int] = set()
    official_rest_lines: set[int] = set()
    for index, rejection in enumerate(
        _sequence(audit.get("rejections"), "audit.rejections")
    ):
        field = f"audit.rejections[{index}]"
        source_line = _positive_int(
            rejection.get("sourceLine"),
            f"{field}.sourceLine",
        )
        if source_line not in rows_by_line:
            raise ValueError(f"{field}.sourceLine is absent from candidate JSONL")
        code = require_text(rejection.get("code"), f"{field}.code")
        target = (
            official_rest_lines
            if code in OFFICIAL_REST_REJECTION_CODES
            else materialization_lines
        )
        if source_line in target:
            phase = (
                "official REST"
                if target is official_rest_lines
                else "pre-hydration"
            )
            raise ValueError(
                f"flat candidate sourceLine {source_line} has duplicate {phase} "
                "rejection receipts"
            )
        target.add(source_line)

    overlap = materialization_lines & official_rest_lines
    if overlap:
        raise ValueError(
            "official REST rejection receipts overlap pre-hydration rejections "
            f"at source lines {sorted(overlap)}"
        )

    expected_input_rows = accepted_cases + len(materialization_lines)
    if expected_input_rows != len(candidate_rows):
        raise ValueError(
            "flat candidate accounting has silent rows: "
            f"inputRows={len(candidate_rows)}, acceptedCandidateCases="
            f"{accepted_cases}, preHydrationRejections="
            f"{len(materialization_lines)}"
        )

    accepted_lines = set(rows_by_line) - materialization_lines
    accepted_identities = [identities_by_line[line] for line in accepted_lines]
    if len(set(accepted_identities)) != len(accepted_identities):
        raise ValueError("accepted flat candidate identities are not unique")
    if not official_rest_lines <= accepted_lines:
        raise ValueError(
            "official REST rejection is not an overlap with an accepted flat row"
        )


def _validate_selected_candidate_rows(
    corpus: Mapping[str, Any],
    candidate_rows: Sequence[Mapping[str, Any]],
    *,
    evidence_mode: str,
) -> None:
    """Bind selected cases to exact rows and their deterministic projection."""

    if evidence_mode == "clickhouse-flat":
        by_line = {int(row["_line"]): row for row in candidate_rows}
        selected_lines: set[int] = set()
        for index, case in enumerate(corpus["cases"]):
            field = f"cases[{index}]"
            source = _mapping(case.get("sourcePr"), f"{field}.sourcePr")
            golden = _mapping(
                case["goldenComments"][0],
                f"{field}.goldenComments[0]",
            )
            legitimacy = _mapping(
                golden.get("legitimacy"),
                f"{field}.legitimacy",
            )
            evidence = _mapping(
                legitimacy.get("evidence"),
                f"{field}.legitimacy.evidence",
            )
            source_line = _positive_int(
                evidence.get("candidateSourceLine"),
                f"{field}.legitimacy.evidence.candidateSourceLine",
            )
            if source_line in selected_lines:
                raise ValueError("selected cases repeat a candidate JSONL source line")
            selected_lines.add(source_line)
            row = by_line.get(source_line)
            if row is None:
                raise ValueError(f"{field} candidate source line is absent")
            row_digest = sha256_json(
                {key: value for key, value in row.items() if key != "_line"}
            )
            if evidence.get("clickHouseEventRowSha256") != row_digest:
                raise ValueError(f"{field} candidate row digest drifted")
            row_number, row_comment = _flat_candidate_key(row)
            if (
                row_number != int(source["number"])
                or row_comment != int(golden["sourceCommentId"])
            ):
                raise ValueError(f"{field} candidate row identity drifted")
            candidate = _flat_candidate_evidence(row)
            candidate_pull = _official_pull(candidate["pull"], row_number)
            candidate_root, candidate_anchor = _official_comment(
                candidate["comments"][0],
                row_number,
            )
            candidate_author = _human_login(
                candidate_pull.get("user"),
                f"{field} candidate pull user",
            )
            expected_source = {
                "number": row_number,
                "url": candidate_pull["html_url"],
                "title": candidate_pull["title"],
                "body": (
                    candidate_pull["body"]
                    if isinstance(candidate_pull.get("body"), str)
                    else ""
                ),
                "author": candidate_author,
                "baseRef": candidate_pull["base"]["ref"],
                "mergedAt": _timestamp_text(
                    candidate_pull["merged_at"],
                    f"{field} candidate pull merged_at",
                ),
                "finalHeadSha": candidate_pull["head"]["sha"],
                "mergeCommitSha": candidate_pull["merge_commit_sha"],
            }
            if {
                name: source.get(name) for name in expected_source
            } != expected_source:
                raise ValueError(
                    f"{field} source PR projection drifted from candidate row"
                )
            snapshot = _mapping(case.get("snapshot"), f"{field}.snapshot")
            expected_snapshot = {
                "eventBaseSha": candidate_pull["base"]["sha"],
                "headSha": candidate_root["original_commit_id"],
                "reviewedAt": _timestamp_text(
                    candidate_root["created_at"],
                    f"{field} candidate root created_at",
                ),
            }
            if {
                name: snapshot.get(name) for name in expected_snapshot
            } != expected_snapshot:
                raise ValueError(
                    f"{field} snapshot projection drifted from candidate row"
                )
            candidate_reviewer = _human_login(
                candidate_root.get("user"),
                f"{field} candidate root user",
            )
            expected_golden = {
                "sourceCommentId": int(candidate_root["id"]),
                "url": candidate_root["html_url"],
                "body": str(candidate_root["body"]),
                "path": candidate_anchor["path"],
                "line": int(candidate_anchor["originalLine"]),
                "startLine": candidate_anchor["originalStartLine"],
                "side": candidate_anchor["side"],
                "reviewer": candidate_reviewer,
                "originalCommitId": candidate_root["original_commit_id"],
                "category": _category(str(candidate_anchor["path"])),
            }
            if {
                name: golden.get(name) for name in expected_golden
            } != expected_golden:
                raise ValueError(
                    f"{field} golden comment projection drifted from candidate row"
                )
            expected_case_id = (
                f"m2-auto-pr-{row_number}-c{row_comment}-"
                f"{candidate_root['original_commit_id'][:12]}"
            )
            if case.get("caseId") != expected_case_id:
                raise ValueError(f"{field} caseId drifted from candidate row")
            if evidence.get("candidateRootObjectSha256") != sha256_json(
                candidate_root
            ):
                raise ValueError(f"{field} candidate root object digest drifted")
            if evidence.get("candidatePullObjectSha256") != sha256_json(
                candidate_pull
            ):
                raise ValueError(f"{field} candidate pull object digest drifted")
        return

    if evidence_mode != "embedded-rest":
        raise ValueError("corpus candidate evidence mode is invalid")
    row_identities: set[tuple[int, int]] = set()
    for row in candidate_rows:
        try:
            number = _candidate_number(row, _first(row, ("pull", "pullRequest", "pull_request", "pr", "pull_response")))
            comment_ids = _candidate_comment_ids(row)
        except ValueError:
            continue
        row_identities.update((number, comment_id) for comment_id in comment_ids)
    for case in corpus["cases"]:
        identity = (
            int(case["sourcePr"]["number"]),
            int(case["goldenComments"][0]["sourceCommentId"]),
        )
        if identity not in row_identities:
            raise ValueError(
                f"selected case {case['caseId']} is absent from embedded candidate JSONL"
            )


def validate_automatic_release_set(
    *,
    corpus_path: Path,
    root_evidence_path: Path,
    audit_path: Path,
    acquisition_query_path: Path,
    candidates_path: Path,
    checksum_manifest_path: Path,
) -> dict[str, Any]:
    """Validate every file, row mode, and cross-binding in one release set."""

    resolved = {
        path.resolve()
        for path in (
            corpus_path,
            root_evidence_path,
            audit_path,
            acquisition_query_path,
            candidates_path,
            checksum_manifest_path,
        )
    }
    if len(resolved) != 6:
        raise ValueError("automatic release inputs must be six distinct files")

    corpus_value, corpus_file_digest = _read_json_artifact(
        corpus_path,
        "automatic corpus",
    )
    evidence_value, evidence_file_digest = _read_json_artifact(
        root_evidence_path,
        "automatic root evidence",
    )
    audit_value, audit_file_digest = _read_json_artifact(
        audit_path,
        "automatic corpus audit",
    )

    corpus_validation = validate_automatic_corpus(corpus_value)
    evidence_validation = validate_automatic_root_evidence(
        corpus_value,
        evidence_value,
    )
    corpus = _mapping(corpus_value, "automatic corpus")
    audit = _mapping(audit_value, "automatic corpus audit")

    if audit.get("kind") != AUTOMATIC_AUDIT_KIND:
        raise ValueError("automatic corpus audit kind is invalid")
    audit_digest = _sha256(audit.get("auditDigest"), "audit.auditDigest")
    unsigned_audit = dict(audit)
    unsigned_audit.pop("auditDigest")
    if audit_digest != sha256_json(unsigned_audit):
        raise ValueError("automatic corpus audit digest mismatch")
    _timestamp(audit.get("generatedAt"), "audit.generatedAt")
    if audit.get("scoringReady") is not True:
        raise ValueError("automatic corpus audit is not scoringReady")
    if audit.get("paperReady") is not False:
        raise ValueError("automatic corpus audit must never claim paperReady")
    if audit.get("failure") is not None:
        raise ValueError("automatic corpus audit records a build failure")

    expected_gates = {
        "gitEvidence",
        "objectiveLegitimacy",
        "balancedSelection",
        "officialRestSelectedRoots",
        "corpusValidation",
    }
    gates = _mapping(audit.get("gates"), "audit.gates")
    if set(gates) != expected_gates or any(gates[name] is not True for name in gates):
        raise ValueError("automatic corpus audit success gates are incomplete")

    expected_case_ids = [str(case["caseId"]) for case in corpus["cases"]]
    if audit.get("selectedCaseIds") != expected_case_ids:
        raise ValueError("automatic corpus audit selected case IDs drifted")
    if audit.get("distribution") != corpus.get("distribution"):
        raise ValueError("automatic corpus audit distribution drifted")
    if audit.get("corpusDigest") != corpus.get("corpusDigest"):
        raise ValueError("automatic corpus audit corpus digest drifted")
    if audit.get("rootEvidenceDigest") != evidence_validation["evidenceDigest"]:
        raise ValueError("automatic corpus audit root evidence digest drifted")

    _query, query_digest, query_bytes = _read_acquisition_query(
        acquisition_query_path
    )
    candidate_rows, candidate_digest, candidate_bytes = _read_candidate_rows(
        candidates_path
    )
    derived_candidate_mode = _candidate_evidence_mode(candidate_rows)
    provenance = _mapping(corpus.get("provenance"), "corpus.provenance")
    candidate_pool = _mapping(
        provenance.get("candidatePool"),
        "corpus.provenance.candidatePool",
    )
    acquisition = _mapping(
        provenance.get("acquisitionQuery"),
        "corpus.provenance.acquisitionQuery",
    )
    local_git = _mapping(
        provenance.get("localGit"),
        "corpus.provenance.localGit",
    )
    hydration = _mapping(
        provenance.get("officialRestSelectedRootHydration"),
        "corpus.provenance.officialRestSelectedRootHydration",
    )
    source = _mapping(audit.get("source"), "audit.source")

    if candidate_pool.get("fileName") != candidates_path.name:
        raise ValueError("corpus candidate filename does not match supplied JSONL")
    if acquisition.get("fileName") != acquisition_query_path.name:
        raise ValueError("corpus query filename does not match supplied SQL")
    if hydration.get("evidenceArtifact") != root_evidence_path.name:
        raise ValueError(
            "corpus root evidence filename does not match supplied artifact"
        )
    if source.get("candidateFile") != candidates_path.name:
        raise ValueError("audit candidate filename does not match supplied JSONL")
    if source.get("acquisitionQueryFile") != acquisition_query_path.name:
        raise ValueError("audit query filename does not match supplied SQL")
    if source.get("repository") != OFFICIAL_REPOSITORY:
        raise ValueError("audit source repository is invalid")

    if candidate_pool.get("sha256") != candidate_digest:
        raise ValueError("corpus candidate SHA-256 does not match supplied JSONL")
    _require_exact_integer(
        candidate_pool.get("byteCount"),
        candidate_bytes,
        "corpus candidate byte count",
    )
    _require_exact_integer(
        candidate_pool.get("rowCount"),
        len(candidate_rows),
        "corpus candidate row count",
    )
    if acquisition.get("sha256") != query_digest:
        raise ValueError("corpus query SHA-256 does not match supplied SQL")
    _require_exact_integer(
        acquisition.get("byteCount"),
        query_bytes,
        "corpus query byte count",
    )
    if candidate_pool.get("format") != "ClickHouse JSONEachRow/JSONL":
        raise ValueError("corpus candidate format is invalid")
    if candidate_pool.get("evidenceMode") != derived_candidate_mode:
        raise ValueError(
            "corpus candidate evidence mode does not match candidate-row structure"
        )
    if acquisition.get("sourceTable") != "github.github_events":
        raise ValueError("corpus acquisition source table is invalid")
    if acquisition.get("outputFormat") != "JSONEachRow":
        raise ValueError("corpus acquisition output format is invalid")
    if hydration.get("mode") != "sealed-official-rest-response-artifact":
        raise ValueError("corpus official REST provenance mode is invalid")
    materialization_jobs = local_git.get("materializationJobs")
    if (
        isinstance(materialization_jobs, bool)
        or not isinstance(materialization_jobs, int)
        or not 1 <= materialization_jobs <= 32
    ):
        raise ValueError("corpus materialization job count is invalid")

    source_pairs = (
        ("candidateSha256", candidate_digest),
        ("candidateBytes", candidate_bytes),
        ("acquisitionQuerySha256", query_digest),
        ("acquisitionQueryBytes", query_bytes),
        ("format", candidate_pool.get("format")),
        ("candidateEvidenceMode", candidate_pool.get("evidenceMode")),
        ("materializationJobs", local_git.get("materializationJobs")),
    )
    for name, expected in source_pairs:
        if source.get(name) != expected:
            raise ValueError(f"audit source {name} drifted from corpus/input")
    _require_exact_integer(
        audit.get("inputRows"),
        len(candidate_rows),
        "audit input row count",
    )
    accepted_cases = _exact_nonnegative_integer(
        audit.get("acceptedCandidateCases"),
        "audit.acceptedCandidateCases",
    )
    accepted_pull_requests = _exact_nonnegative_integer(
        audit.get("acceptedCandidatePullRequests"),
        "audit.acceptedCandidatePullRequests",
    )
    if accepted_cases < TARGET_CASES or accepted_pull_requests < TARGET_CASES:
        raise ValueError("audit accepted candidate pool is smaller than the release")
    if accepted_pull_requests > accepted_cases:
        raise ValueError("audit accepted pull request count exceeds accepted cases")
    _validate_candidate_pool_diagnostics(
        audit.get("candidatePoolDiagnostics"),
        accepted_cases=accepted_cases,
        accepted_pull_requests=accepted_pull_requests,
    )
    _validate_audit_rejections(
        audit,
        candidate_rows=candidate_rows,
        evidence_mode=derived_candidate_mode,
    )
    if derived_candidate_mode == "clickhouse-flat":
        _validate_flat_candidate_accounting(
            audit,
            candidate_rows=candidate_rows,
            accepted_cases=accepted_cases,
        )
    _validate_selected_candidate_rows(
        corpus,
        candidate_rows,
        evidence_mode=derived_candidate_mode,
    )

    audit_hydration = _mapping(
        audit.get("officialRestHydration"),
        "audit.officialRestHydration",
    )
    if audit_hydration.get("mode") not in {"live-with-cache", "cache-only"}:
        raise ValueError("audit official REST hydration mode is invalid")
    request_count = audit_hydration.get("requestCount")
    if (
        isinstance(request_count, bool)
        or not isinstance(request_count, int)
        or request_count < TARGET_CASES
    ):
        raise ValueError("audit official REST request count is invalid")
    _require_exact_integer(
        audit_hydration.get("attestedRootCount"),
        TARGET_CASES,
        "audit official REST attested root count",
    )
    rejected_roots = hydration.get("rejectedDiscoveryRows")
    if (
        isinstance(rejected_roots, bool)
        or not isinstance(rejected_roots, int)
        or rejected_roots < 0
        or audit_hydration.get("rejectedDiscoveryRows") != rejected_roots
        or audit.get("officialRestRejectedCandidates") != rejected_roots
    ):
        raise ValueError("official REST rejection counts drifted")

    artifact_paths = (
        corpus_path,
        root_evidence_path,
        audit_path,
        acquisition_query_path,
        candidates_path,
    )
    if len({path.name for path in artifact_paths}) != len(artifact_paths):
        raise ValueError("automatic release artifact filenames must be distinct")
    checksum_entries = _checksum_manifest_entries(checksum_manifest_path)
    expected_names = {path.name for path in artifact_paths}
    if set(checksum_entries) != expected_names:
        missing = sorted(expected_names - set(checksum_entries))
        extra = sorted(set(checksum_entries) - expected_names)
        raise ValueError(
            "automatic release checksum manifest entry set drifted: "
            f"missing={missing}, extra={extra}"
        )
    observed_digests = {
        corpus_path.resolve(): corpus_file_digest,
        root_evidence_path.resolve(): evidence_file_digest,
        audit_path.resolve(): audit_file_digest,
        acquisition_query_path.resolve(): query_digest,
        candidates_path.resolve(): candidate_digest,
    }
    for artifact_path in artifact_paths:
        observed = observed_digests[artifact_path.resolve()]
        if checksum_entries[artifact_path.name] != observed:
            raise ValueError(
                "automatic release checksum mismatch for "
                f"{artifact_path.name}"
            )

    return {
        **evidence_validation,
        "releaseSetValid": True,
        "scoringReady": corpus_validation["scoringReady"],
        "paperReady": corpus_validation["paperReady"],
        "auditDigest": audit_digest,
        "candidateRowCount": len(candidate_rows),
        "candidateSha256": candidate_digest,
        "acquisitionQuerySha256": query_digest,
        "checksumManifest": checksum_manifest_path.name,
        "checksumEntryCount": len(checksum_entries),
    }


def build_automatic_corpus(
    *,
    candidates_path: Path,
    acquisition_query_path: Path,
    repository_path: Path,
    output: Path,
    audit_output: Path,
    root_evidence_output: Path,
    checksum_manifest_output: Path,
    github_client: GitHubClient | None = None,
    selection_seed: str = DEFAULT_SELECTION_SEED,
    materialization_jobs: int = DEFAULT_MATERIALIZATION_JOBS,
) -> dict[str, Any]:
    """Build and release the fixed objective-evidence Magento reference set."""

    audit: dict[str, Any] = {
        "kind": AUTOMATIC_AUDIT_KIND,
        "generatedAt": _now(),
        "source": {
            "candidateFile": candidates_path.name,
            "acquisitionQueryFile": acquisition_query_path.name,
            "repository": OFFICIAL_REPOSITORY,
        },
        "inputRows": 0,
        "acceptedCandidateCases": 0,
        "acceptedCandidatePullRequests": 0,
        "rejectedCandidates": 0,
        "rejectionCounts": {},
        "rejections": [],
        "selectedCaseIds": [],
        "gates": {
            "gitEvidence": False,
            "objectiveLegitimacy": False,
            "balancedSelection": False,
            "officialRestSelectedRoots": False,
            "corpusValidation": False,
        },
        "scoringReady": False,
        "paperReady": False,
        "failure": None,
    }
    release_paths = (
        acquisition_query_path,
        candidates_path,
        output,
        audit_output,
        root_evidence_output,
        checksum_manifest_output,
    )
    resolved_release_paths = [path.resolve() for path in release_paths]
    failure_audit_safe = (
        resolved_release_paths.count(audit_output.resolve()) == 1
    )
    try:
        if len(set(resolved_release_paths)) != 6:
            raise ValueError(
                "query, candidates, corpus, audit, root evidence, and checksum "
                "manifest paths must be distinct"
            )
        manifest_artifacts = (
            output,
            root_evidence_output,
            audit_output,
            acquisition_query_path,
            candidates_path,
        )
        if len({path.name for path in manifest_artifacts}) != 5:
            raise ValueError(
                "automatic release artifact basenames must be distinct"
            )
        if output.parent.resolve() != root_evidence_output.parent.resolve():
            raise ValueError(
                "root evidence output must be a sibling of the corpus output"
            )
        if (
            isinstance(materialization_jobs, bool)
            or not isinstance(materialization_jobs, int)
            or not 1 <= materialization_jobs <= 32
        ):
            raise ValueError("materialization_jobs must be an integer from 1 to 32")
        if (
            github_client is not None
            and github_client.api_url.rstrip("/") != "https://api.github.com"
        ):
            raise ValueError(
                "automatic corpus hydration requires the official GitHub API origin"
            )
        _, acquisition_query_digest, acquisition_query_bytes = (
            _read_acquisition_query(acquisition_query_path)
        )
        rows, candidate_digest, candidate_bytes = _read_candidate_rows(candidates_path)
        audit["inputRows"] = len(rows)
        audit["source"].update(
            {
                "acquisitionQuerySha256": acquisition_query_digest,
                "acquisitionQueryBytes": acquisition_query_bytes,
                "candidateSha256": candidate_digest,
                "candidateBytes": candidate_bytes,
                "format": "ClickHouse JSONEachRow/JSONL",
            }
        )
        if not is_local_git_repository(repository_path):
            raise ValueError("--repository-path is not a local Git repository")
        validate_git_evidence_repository(repository_path)
        _verify_origin(repository_path)
        git_env = hermetic_git_environment(offline=True)
        git_version = _git_version(git_env)
        audit["gates"]["gitEvidence"] = True
        evidence_groups, candidate_mode, conversion_rejections = (
            _candidate_evidence_groups(rows)
        )
        audit["source"]["candidateEvidenceMode"] = candidate_mode
        audit["source"]["materializationJobs"] = materialization_jobs
        candidates: list[dict[str, Any]] = []
        rejections: list[dict[str, Any]] = list(conversion_rejections)
        with ThreadPoolExecutor(max_workers=materialization_jobs) as executor:
            materialized = executor.map(
                lambda evidence: _materialize_evidence(
                    evidence,
                    repository=repository_path,
                    git_env=git_env,
                ),
                evidence_groups,
            )
            for accepted, rejected in materialized:
                candidates.extend(accepted)
                rejections.extend(rejected)
        rejections.sort(
            key=lambda item: (
                int(item.get("pullRequest") or 0),
                int(item.get("sourceCommentId") or 0),
                str(item.get("code") or ""),
            )
        )
        audit["acceptedCandidateCases"] = len(candidates)
        audit["acceptedCandidatePullRequests"] = len({_case_pr(case) for case in candidates})
        audit["candidatePoolDiagnostics"] = _candidate_pool_diagnostics(candidates)
        audit["rejectedCandidates"] = len(rejections)
        audit["rejections"] = rejections
        audit["rejectionCounts"] = dict(
            sorted(Counter(item["code"] for item in rejections).items())
        )
        audit["gates"]["objectiveLegitimacy"] = bool(candidates) and all(
            case["goldenComments"][0]["legitimacy"].get("eligible") is True
            for case in candidates
        )
        if github_client is None:
            raise ValueError(
                "official REST hydration of all 54 selected roots is required "
                "for a scoring-ready release; use --hydrate-github with live "
                "or --offline cached responses"
            )
        selection_candidates, cache_qualified = _prequalify_official_rest_candidates(
            candidates,
            github_client,
        )
        audit["officialRestValidatedCacheCandidates"] = cache_qualified
        selected = select_balanced_cases(selection_candidates, seed=selection_seed)
        audit["selectedCaseIds"] = [case["caseId"] for case in selected]
        audit["gates"]["balancedSelection"] = True
        hydration_rejections: list[dict[str, Any]] = []
        hydration_request_count = 0
        hydrated_responses: dict[int, Mapping[str, Any]] = {}
        hydration_mode = "cache-only" if github_client.offline else "live-with-cache"
        remaining = list(selection_candidates)
        while True:
            audit["gates"]["balancedSelection"] = False
            selected = select_balanced_cases(remaining, seed=selection_seed)
            audit["selectedCaseIds"] = [case["caseId"] for case in selected]
            audit["gates"]["balancedSelection"] = True
            failures, request_count, responses = _hydrate_selected_roots(
                selected,
                github_client,
            )
            hydration_request_count += request_count
            hydrated_responses.update(responses)
            if not failures:
                break
            audit["gates"]["balancedSelection"] = False
            audit["selectedCaseIds"] = []
            failed_ids = {str(item["caseId"]) for item in failures}
            hydration_rejections.extend(failures)
            rejections.extend(failures)
            rejections.sort(
                key=lambda item: (
                    int(item.get("pullRequest") or 0),
                    int(item.get("sourceCommentId") or 0),
                    str(item.get("code") or ""),
                )
            )
            audit["rejectedCandidates"] = len(rejections)
            audit["rejections"] = rejections
            audit["rejectionCounts"] = dict(
                sorted(Counter(item["code"] for item in rejections).items())
            )
            audit["officialRestRejectedCandidates"] = len(hydration_rejections)
            audit["officialRestHydration"] = {
                "mode": hydration_mode,
                "requestCount": hydration_request_count,
                "attestedRootCount": len(hydrated_responses),
                "rejectedDiscoveryRows": len(hydration_rejections),
            }
            remaining = [
                case for case in remaining if str(case["caseId"]) not in failed_ids
            ]
        audit["officialRestRejectedCandidates"] = len(hydration_rejections)
        audit["gates"]["officialRestSelectedRoots"] = True
        audit["officialRestHydration"] = {
            "mode": hydration_mode,
            "requestCount": hydration_request_count,
            "attestedRootCount": len(selected),
            "rejectedDiscoveryRows": len(hydration_rejections),
        }
        root_evidence = _root_evidence_artifact(selected, hydrated_responses)
        corpus: dict[str, Any] = {
            "kind": AUTOMATIC_CORPUS_KIND,
            "repository": OFFICIAL_REPOSITORY,
            "corpusId": "magento2-automatic-review-54",
            "scoringReady": True,
            "paperReady": False,
            "metricSemantics": {
                "label": "reference-set",
                "precision": "precision against accepted human-review reference issues",
                "recall": "recall of accepted human-review reference issues",
                "f1": "harmonic mean of reference-set precision and recall",
                "falsePositive": (
                    "an unmatched finding in this reference set; not proof that "
                    "the finding is technically invalid"
                ),
            },
            "selectionPolicy": _selection_policy(selection_seed),
            "distribution": _distribution(selected),
            "provenance": {
                "sourceRepository": OFFICIAL_REPOSITORY,
                "sourceDefaultBranch": OFFICIAL_DEFAULT_BRANCH,
                "sourceTargetBranches": list(OFFICIAL_TARGET_BRANCHES),
                "candidatePool": {
                    "format": "ClickHouse JSONEachRow/JSONL",
                    "evidenceMode": candidate_mode,
                    "fileName": candidates_path.name,
                    "sha256": candidate_digest,
                    "byteCount": candidate_bytes,
                    "rowCount": len(rows),
                },
                "acquisitionQuery": {
                    "fileName": acquisition_query_path.name,
                    "sha256": acquisition_query_digest,
                    "byteCount": acquisition_query_bytes,
                    "sourceTable": "github.github_events",
                    "outputFormat": "JSONEachRow",
                },
                "localGit": {
                    "gitVersion": git_version,
                    "materializationJobs": materialization_jobs,
                    "offlineObjectResolution": True,
                    "shallowHistoryRejected": True,
                    "replaceObjectsDisabled": True,
                },
                "officialRestSelectedRootHydration": {
                    "mode": "sealed-official-rest-response-artifact",
                    "attestedRootCount": len(selected),
                    "rejectedDiscoveryRows": len(hydration_rejections),
                    "endpointShape": "/repos/magento/magento2/pulls/comments/{id}",
                    "bulkConversationEnumeration": False,
                    "evidenceArtifact": root_evidence_output.name,
                    "evidenceDigest": root_evidence["evidenceDigest"],
                },
            },
            "cases": selected,
        }
        corpus["corpusDigest"] = sha256_json(corpus)
        validation = validate_automatic_corpus(corpus)
        audit["gates"]["corpusValidation"] = True
        audit["scoringReady"] = True
        audit["corpusDigest"] = corpus["corpusDigest"]
        audit["rootEvidenceDigest"] = root_evidence["evidenceDigest"]
        audit["distribution"] = corpus["distribution"]
        success_audit = _audit_digest(audit)
        output.parent.mkdir(parents=True, exist_ok=True)
        audit_output.parent.mkdir(parents=True, exist_ok=True)
        checksum_manifest_output.parent.mkdir(parents=True, exist_ok=True)
        with (
            tempfile.TemporaryDirectory(
                prefix=".magento2-automatic-release.",
                dir=output.parent,
            ) as payload_stage_name,
            tempfile.TemporaryDirectory(
                prefix=".magento2-automatic-audit.",
                dir=audit_output.parent,
            ) as audit_stage_name,
            tempfile.TemporaryDirectory(
                prefix=".magento2-automatic-manifest.",
                dir=checksum_manifest_output.parent,
            ) as manifest_stage_name,
        ):
            payload_stage = Path(payload_stage_name)
            staged_output = payload_stage / output.name
            staged_root_evidence = payload_stage / root_evidence_output.name
            staged_audit = Path(audit_stage_name) / audit_output.name
            staged_manifest = (
                Path(manifest_stage_name) / checksum_manifest_output.name
            )
            staged_artifacts = (
                staged_output,
                staged_root_evidence,
                staged_audit,
                acquisition_query_path,
                candidates_path,
            )
            write_json(staged_root_evidence, root_evidence)
            write_json(staged_output, corpus)
            write_json(staged_audit, success_audit)
            _write_checksum_manifest(staged_manifest, staged_artifacts)
            release_validation = validate_automatic_release_set(
                corpus_path=staged_output,
                root_evidence_path=staged_root_evidence,
                audit_path=staged_audit,
                acquisition_query_path=acquisition_query_path,
                candidates_path=candidates_path,
                checksum_manifest_path=staged_manifest,
            )
            os.replace(staged_root_evidence, root_evidence_output)
            os.replace(staged_output, output)
            os.replace(staged_audit, audit_output)
            os.replace(staged_manifest, checksum_manifest_output)
        release_validation["checksumManifest"] = checksum_manifest_output.name
        return {
            **validation,
            **release_validation,
            "auditDigest": success_audit["auditDigest"],
            "output": str(output),
            "auditOutput": str(audit_output),
            "rootEvidenceOutput": str(root_evidence_output),
            "rootEvidenceDigest": root_evidence["evidenceDigest"],
            "checksumManifestOutput": str(checksum_manifest_output),
        }
    except Exception as exc:
        audit["scoringReady"] = False
        audit["gates"]["corpusValidation"] = False
        audit["failure"] = {
            "type": type(exc).__name__,
            "detail": str(exc),
        }
        if failure_audit_safe:
            write_json(audit_output, _audit_digest(audit))
        raise
