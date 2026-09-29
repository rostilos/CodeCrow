from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from .util import require_text, sha256_json


GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
GRAPHQL_OPERATION_NAME = "MagentoBenchmarkReviewThreads"
GRAPHQL_THREAD_ARCHIVE_KIND = (
    "codecrow-magento2-graphql-thread-page-archive"
)
REST_THREAD_EVIDENCE_KIND = (
    "codecrow-magento2-rest-review-thread-evidence"
)
REVIEW_THREADS_QUERY = """\
query MagentoBenchmarkReviewThreads(
  $owner: String!,
  $name: String!,
  $number: Int!,
  $after: String
) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          originalLine
          startLine
          originalStartLine
          diffSide
          startDiffSide
          comments(first: 100) {
            pageInfo { hasNextPage endCursor }
            nodes {
              databaseId
              url
              body
              createdAt
              updatedAt
              author { login __typename }
              replyTo { databaseId }
              pullRequestReview {
                databaseId
                state
                commit { oid }
              }
            }
          }
        }
      }
    }
  }
}
"""

ARCHIVE_FIELDS = {
    "kind",
    "operationName",
    "pullRequest",
    "pageCount",
    "pages",
    "archiveDigest",
}
PAGE_FIELDS = {
    "pageIndex",
    "request",
    "requestDigest",
    "response",
    "responseDigest",
    "previousPageDigest",
    "pageDigest",
}
REQUEST_FIELDS = {"method", "endpoint", "payload"}
PAYLOAD_FIELDS = {"query", "variables"}
VARIABLE_FIELDS = {"owner", "name", "number", "after"}
THREAD_BINDING_FIELDS = {
    "threadEvidenceDigest",
    "threadDigest",
    "graphqlArchiveDigest",
    "graphqlPageDigest",
    "restThreadEvidence",
    "thread",
}
REST_THREAD_EVIDENCE_FIELDS = {
    "kind",
    "pullRequest",
    "rootCommentId",
    "sourceArchiveDigest",
    "sourceArchiveCaseEvidenceDigest",
    "comments",
    "submittedReviews",
    "evidenceDigest",
}
NORMALIZED_MESSAGE_FIELDS = {
    "id",
    "url",
    "author",
    "authorType",
    "authorAssociation",
    "body",
    "createdAt",
    "updatedAt",
    "commitId",
    "originalCommitId",
    "inReplyToId",
}
REST_ANCHOR_FIELDS = {
    "commit_id",
    "original_commit_id",
    "path",
    "line",
    "original_line",
    "start_line",
    "original_start_line",
    "side",
    "start_side",
    "subject_type",
}
GRAPHQL_THREAD_ANCHOR_FIELDS = {
    "path",
    "line",
    "originalLine",
    "startLine",
    "originalStartLine",
    "diffSide",
    "startDiffSide",
}


def _sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _nullable_positive_integer(value: Any, field: str) -> int | None:
    if value is None:
        return None
    return _positive_integer(value, field)


def rest_review_comment_anchor(
    value: Any,
    *,
    field: str,
    require_right_line: bool = True,
) -> dict[str, Any]:
    """Return the exact current/original REST coordinates for one root.

    GitHub's ``line`` and ``start_line`` describe the current PR head and can
    become null or move as later commits update the diff. The benchmark's H
    anchor is instead ``original_commit_id`` + ``original_line`` and, for a
    multiline range, ``original_start_line``. ``side`` and ``start_side`` are
    the corresponding end/start diff sides.
    """

    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    missing = sorted(REST_ANCHOR_FIELDS - set(value))
    if missing:
        raise ValueError(
            f"{field} is missing raw REST anchor fields: "
            + ", ".join(missing)
        )
    current_commit = require_text(value.get("commit_id"), f"{field}.commit_id")
    original_commit = require_text(
        value.get("original_commit_id"),
        f"{field}.original_commit_id",
    )
    for name, commit in (
        ("commit_id", current_commit),
        ("original_commit_id", original_commit),
    ):
        if len(commit) != 40 or any(
            character not in "0123456789abcdef" for character in commit
        ):
            raise ValueError(f"{field}.{name} must be a full lowercase Git SHA")
    path = require_text(value.get("path"), f"{field}.path")
    current_line = _nullable_positive_integer(
        value.get("line"),
        f"{field}.line",
    )
    original_line = _positive_integer(
        value.get("original_line"),
        f"{field}.original_line",
    )
    current_start_line = _nullable_positive_integer(
        value.get("start_line"),
        f"{field}.start_line",
    )
    original_start_line = _nullable_positive_integer(
        value.get("original_start_line"),
        f"{field}.original_start_line",
    )
    current_side = value.get("side")
    original_side = value.get("original_side")
    side = original_side or current_side
    start_side = value.get("start_side")
    if current_side not in {"LEFT", "RIGHT"}:
        raise ValueError(f"{field}.side must be LEFT or RIGHT")
    if original_side not in {None, "LEFT", "RIGHT"}:
        raise ValueError(
            f"{field}.original_side must be LEFT, RIGHT, or absent"
        )
    if start_side not in {None, "LEFT", "RIGHT"}:
        raise ValueError(f"{field}.start_side must be LEFT, RIGHT, or null")
    if value.get("subject_type") != "line":
        raise ValueError(f"{field}.subject_type must be line")
    if (original_start_line is None) is not (start_side is None):
        raise ValueError(
            f"{field} original_start_line/start_side range shape is invalid"
        )
    if current_start_line is not None and current_line is None:
        raise ValueError(
            f"{field}.start_line cannot exist when current line is null"
        )
    if original_start_line is None and current_start_line is not None:
        raise ValueError(
            f"{field}.start_line cannot create a range absent at H"
        )
    if require_right_line and side != "RIGHT":
        raise ValueError(f"{field} does not have a RIGHT-side H end anchor")
    return {
        "currentCommitId": current_commit,
        "originalCommitId": original_commit,
        "path": path,
        "currentLine": current_line,
        "originalLine": original_line,
        "currentStartLine": current_start_line,
        "originalStartLine": original_start_line,
        "currentSide": current_side,
        "side": side,
        "startSide": start_side,
    }


def graphql_review_thread_anchor(
    value: Any,
    *,
    field: str,
    require_right_line: bool = True,
) -> dict[str, Any]:
    """Return the exact current/original GraphQL thread coordinates."""

    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    missing = sorted(GRAPHQL_THREAD_ANCHOR_FIELDS - set(value))
    if missing:
        raise ValueError(
            f"{field} is missing raw GraphQL anchor fields: "
            + ", ".join(missing)
        )
    path = require_text(value.get("path"), f"{field}.path")
    current_line = _nullable_positive_integer(
        value.get("line"),
        f"{field}.line",
    )
    original_line = _positive_integer(
        value.get("originalLine"),
        f"{field}.originalLine",
    )
    current_start_line = _nullable_positive_integer(
        value.get("startLine"),
        f"{field}.startLine",
    )
    original_start_line = _nullable_positive_integer(
        value.get("originalStartLine"),
        f"{field}.originalStartLine",
    )
    side = value.get("diffSide")
    start_side = value.get("startDiffSide")
    if side not in {"LEFT", "RIGHT"}:
        raise ValueError(f"{field}.diffSide must be LEFT or RIGHT")
    if start_side not in {None, "LEFT", "RIGHT"}:
        raise ValueError(
            f"{field}.startDiffSide must be LEFT, RIGHT, or null"
        )
    if (original_start_line is None) is not (start_side is None):
        raise ValueError(
            f"{field} originalStartLine/startDiffSide range shape is invalid"
        )
    if current_start_line is not None and current_line is None:
        raise ValueError(
            f"{field}.startLine cannot exist when current line is null"
        )
    if original_start_line is None and current_start_line is not None:
        raise ValueError(
            f"{field}.startLine cannot create a range absent at H"
        )
    if require_right_line and side != "RIGHT":
        raise ValueError(f"{field} does not have a RIGHT-side H end anchor")
    return {
        "path": path,
        "currentLine": current_line,
        "originalLine": original_line,
        "currentStartLine": current_start_line,
        "originalStartLine": original_start_line,
        "side": side,
        "startSide": start_side,
    }


def _connection(
    response: Mapping[str, Any],
    *,
    field: str,
) -> Mapping[str, Any]:
    errors = response.get("errors")
    if errors:
        raise ValueError(f"{field} contains GraphQL errors")
    data = response.get("data")
    repository = data.get("repository") if isinstance(data, Mapping) else None
    pull = (
        repository.get("pullRequest")
        if isinstance(repository, Mapping)
        else None
    )
    connection = (
        pull.get("reviewThreads") if isinstance(pull, Mapping) else None
    )
    if not isinstance(connection, Mapping):
        raise ValueError(f"{field} has no reviewThreads connection")
    if not isinstance(connection.get("nodes"), list):
        raise ValueError(f"{field}.nodes must be an array")
    if not isinstance(connection.get("pageInfo"), Mapping):
        raise ValueError(f"{field}.pageInfo must be an object")
    return connection


def build_graphql_thread_archive(
    *,
    pull_request: int,
    pages: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    endpoint: str = GRAPHQL_ENDPOINT,
) -> dict[str, Any]:
    """Seal the exact GraphQL request/response sequence for one pull request."""

    _positive_integer(pull_request, "pull_request")
    if endpoint != GRAPHQL_ENDPOINT:
        raise ValueError(
            "Magento thread evidence must use the canonical GitHub GraphQL "
            "endpoint"
        )
    if not pages:
        raise ValueError("GraphQL thread archive must contain at least one page")
    sealed_pages = []
    previous_page_digest: str | None = None
    for index, (variables, response) in enumerate(pages, start=1):
        request = {
            "method": "POST",
            "endpoint": endpoint,
            "payload": {
                "query": REVIEW_THREADS_QUERY,
                "variables": dict(variables),
            },
        }
        page = {
            "pageIndex": index,
            "request": request,
            "requestDigest": sha256_json(request),
            "response": dict(response),
            "responseDigest": sha256_json(response),
            "previousPageDigest": previous_page_digest,
        }
        page["pageDigest"] = sha256_json(page)
        sealed_pages.append(page)
        previous_page_digest = page["pageDigest"]
    archive = {
        "kind": GRAPHQL_THREAD_ARCHIVE_KIND,
        "operationName": GRAPHQL_OPERATION_NAME,
        "pullRequest": pull_request,
        "pageCount": len(sealed_pages),
        "pages": sealed_pages,
    }
    archive["archiveDigest"] = sha256_json(archive)
    validate_graphql_thread_archive(
        archive,
        pull_request=pull_request,
    )
    return archive


def validate_graphql_thread_archive(
    value: Any,
    *,
    pull_request: int,
) -> tuple[dict[str, Any], dict[int, tuple[dict[str, Any], str]]]:
    """Validate raw pages, request identity, and the complete cursor chain."""

    _positive_integer(pull_request, "pull_request")
    if not isinstance(value, Mapping) or set(value) != ARCHIVE_FIELDS:
        raise ValueError("GraphQL thread archive fields are invalid")
    if value.get("kind") != GRAPHQL_THREAD_ARCHIVE_KIND:
        raise ValueError("GraphQL thread archive kind is invalid")
    if value.get("operationName") != GRAPHQL_OPERATION_NAME:
        raise ValueError("GraphQL thread archive operation is invalid")
    if value.get("pullRequest") != pull_request:
        raise ValueError("GraphQL thread archive belongs to another PR")
    pages = value.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("GraphQL thread archive has no raw pages")
    if value.get("pageCount") != len(pages):
        raise ValueError("GraphQL thread archive page count mismatch")
    archive_value = dict(value)
    archive_digest = archive_value.pop("archiveDigest", None)
    _sha256(archive_digest, "GraphQL thread archive digest")
    if archive_digest != sha256_json(archive_value):
        raise ValueError("GraphQL thread archive digest mismatch")

    expected_after: str | None = None
    previous_page_digest: str | None = None
    observed_page_digests: set[str] = set()
    threads_by_root: dict[int, tuple[dict[str, Any], str]] = {}
    for index, page in enumerate(pages, start=1):
        field = f"GraphQL thread archive page {index}"
        if not isinstance(page, Mapping) or set(page) != PAGE_FIELDS:
            raise ValueError(f"{field} fields are invalid")
        if page.get("pageIndex") != index:
            raise ValueError(f"{field} index is not contiguous")
        if page.get("previousPageDigest") != previous_page_digest:
            raise ValueError(f"{field} does not bind the previous raw page")

        request = page.get("request")
        if not isinstance(request, Mapping) or set(request) != REQUEST_FIELDS:
            raise ValueError(f"{field} request fields are invalid")
        if (
            request.get("method") != "POST"
            or request.get("endpoint") != GRAPHQL_ENDPOINT
        ):
            raise ValueError(f"{field} request endpoint identity is invalid")
        payload = request.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != PAYLOAD_FIELDS:
            raise ValueError(f"{field} request payload fields are invalid")
        if payload.get("query") != REVIEW_THREADS_QUERY:
            raise ValueError(f"{field} query text is not the frozen operation")
        variables = payload.get("variables")
        if not isinstance(variables, Mapping) or set(variables) != VARIABLE_FIELDS:
            raise ValueError(f"{field} request variables are invalid")
        if dict(variables) != {
            "owner": "magento",
            "name": "magento2",
            "number": pull_request,
            "after": expected_after,
        }:
            raise ValueError(f"{field} request variables break pagination")
        request_digest = _sha256(
            page.get("requestDigest"),
            f"{field} request digest",
        )
        if request_digest != sha256_json(request):
            raise ValueError(f"{field} request digest mismatch")

        response = page.get("response")
        if not isinstance(response, Mapping):
            raise ValueError(f"{field} response must be an object")
        response_digest = _sha256(
            page.get("responseDigest"),
            f"{field} response digest",
        )
        if response_digest != sha256_json(response):
            raise ValueError(f"{field} response digest mismatch")
        page_value = dict(page)
        page_digest = page_value.pop("pageDigest", None)
        _sha256(page_digest, f"{field} digest")
        if page_digest != sha256_json(page_value):
            raise ValueError(f"{field} digest mismatch")
        if page_digest in observed_page_digests:
            raise ValueError("GraphQL thread archive repeats a raw page")
        observed_page_digests.add(page_digest)

        connection = _connection(response, field=f"{field} response")
        page_info = connection["pageInfo"]
        has_next = page_info.get("hasNextPage")
        if not isinstance(has_next, bool):
            raise ValueError(f"{field} has invalid hasNextPage metadata")
        end_cursor = page_info.get("endCursor")
        if has_next and (not isinstance(end_cursor, str) or not end_cursor):
            raise ValueError(f"{field} has no next-page cursor")
        is_final = index == len(pages)
        if has_next is is_final:
            if is_final:
                raise ValueError(
                    "GraphQL thread archive is missing a declared next page"
                )
            raise ValueError(
                "GraphQL thread archive contains a page after pagination ended"
            )
        expected_after = end_cursor if has_next else None

        for thread in connection["nodes"]:
            if not isinstance(thread, Mapping):
                raise ValueError(f"{field} contains a non-object thread")
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
            if isinstance(root_id, bool) or not isinstance(root_id, int):
                raise ValueError(f"{field} thread has no numeric root comment")
            if root_id in threads_by_root:
                raise ValueError(
                    f"GraphQL thread archive repeats root comment {root_id}"
                )
            threads_by_root[root_id] = (dict(thread), page_digest)
        previous_page_digest = page_digest
    return dict(value), threads_by_root


def _graph_message_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    author = value.get("author")
    review = value.get("pullRequestReview")
    reply = value.get("replyTo")
    return {
        "id": value.get("databaseId"),
        "url": value.get("url"),
        "author": (
            author.get("login") if isinstance(author, Mapping) else None
        ),
        "authorType": (
            author.get("__typename") if isinstance(author, Mapping) else None
        ),
        "body": value.get("body"),
        "createdAt": value.get("createdAt"),
        "updatedAt": value.get("updatedAt"),
        "commitId": (
            ((review.get("commit") or {}).get("oid"))
            if isinstance(review, Mapping)
            else None
        ),
        "inReplyToId": (
            reply.get("databaseId") if isinstance(reply, Mapping) else None
        ),
    }


def _records_by_positive_id(
    values: Any,
    *,
    field: str,
) -> dict[int, Mapping[str, Any]]:
    if not isinstance(values, list):
        raise ValueError(f"{field} must be an array")
    result: dict[int, Mapping[str, Any]] = {}
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ValueError(f"{field}[{index}] must be an object")
        identifier = value.get("id")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier < 1
            or identifier in result
        ):
            raise ValueError(
                f"{field} IDs must be unique positive integers"
            )
        result[identifier] = value
    return result


def _rest_thread_records(
    value: Any,
    *,
    pull_request: int | None = None,
    root_comment_id: int | None = None,
    source_archive_digest: str | None = None,
    source_archive_case_evidence_digest: str | None = None,
) -> tuple[
    dict[str, Any],
    dict[int, Mapping[str, Any]],
    dict[int, Mapping[str, Any]],
]:
    if (
        not isinstance(value, Mapping)
        or set(value) != REST_THREAD_EVIDENCE_FIELDS
    ):
        raise ValueError("REST review thread evidence fields are invalid")
    if value.get("kind") != REST_THREAD_EVIDENCE_KIND:
        raise ValueError("REST review thread evidence kind is invalid")
    observed_pull = _positive_integer(
        value.get("pullRequest"),
        "REST review thread pullRequest",
    )
    observed_root = _positive_integer(
        value.get("rootCommentId"),
        "REST review thread rootCommentId",
    )
    if pull_request is not None and observed_pull != _positive_integer(
        pull_request,
        "pull_request",
    ):
        raise ValueError("REST review thread belongs to another PR")
    if root_comment_id is not None and observed_root != _positive_integer(
        root_comment_id,
        "root_comment_id",
    ):
        raise ValueError("REST review thread root identity drift")

    observed_archive_digest = _sha256(
        value.get("sourceArchiveDigest"),
        "REST review thread source archive digest",
    )
    observed_case_digest = _sha256(
        value.get("sourceArchiveCaseEvidenceDigest"),
        "REST review thread source archive case digest",
    )
    if (
        source_archive_digest is not None
        and observed_archive_digest
        != _sha256(
            source_archive_digest,
            "expected source archive digest",
        )
    ):
        raise ValueError("REST review thread source archive digest mismatch")
    if (
        source_archive_case_evidence_digest is not None
        and observed_case_digest
        != _sha256(
            source_archive_case_evidence_digest,
            "expected source archive case digest",
        )
    ):
        raise ValueError(
            "REST review thread source archive case digest mismatch"
        )

    digest_value = dict(value)
    declared_digest = _sha256(
        digest_value.pop("evidenceDigest", None),
        "REST review thread evidence digest",
    )
    if declared_digest != sha256_json(digest_value):
        raise ValueError("REST review thread evidence digest mismatch")

    comments = value.get("comments")
    comments_by_id = _records_by_positive_id(
        comments,
        field="REST review thread comments",
    )
    if not isinstance(comments, list) or not comments:
        raise ValueError("REST review thread must contain its root comment")
    root = comments_by_id.get(observed_root)
    if root is None or comments[0].get("id") != observed_root:
        raise ValueError("REST review thread root must be the first comment")
    if root.get("in_reply_to_id") is not None:
        raise ValueError("REST review thread root is itself a reply")
    replies = list(comments[1:])
    if any(
        reply.get("in_reply_to_id") != observed_root
        for reply in replies
    ):
        raise ValueError(
            "REST review thread contains a comment outside the root thread"
        )
    expected_replies = sorted(
        replies,
        key=lambda item: (
            str(item.get("created_at") or ""),
            int(item["id"]),
        ),
    )
    if replies != expected_replies:
        raise ValueError(
            "REST review thread replies are not in canonical order"
        )

    referenced_review_ids: set[int] = set()
    for comment in comments:
        review_id = comment.get("pull_request_review_id")
        if (
            isinstance(review_id, bool)
            or not isinstance(review_id, int)
            or review_id < 1
        ):
            raise ValueError(
                "REST review thread comment has no submitted review identity"
            )
        referenced_review_ids.add(review_id)
    submitted_reviews = value.get("submittedReviews")
    reviews_by_id = _records_by_positive_id(
        submitted_reviews,
        field="REST review thread submitted reviews",
    )
    if set(reviews_by_id) != referenced_review_ids:
        raise ValueError(
            "REST review thread does not contain the exact referenced "
            "submitted reviews"
        )
    if not isinstance(submitted_reviews, list) or [
        item["id"] for item in submitted_reviews
    ] != sorted(referenced_review_ids):
        raise ValueError(
            "REST review thread submitted reviews are not in canonical order"
        )
    return dict(value), comments_by_id, reviews_by_id


def build_rest_review_thread_evidence(
    *,
    pull_request: int,
    root_comment_id: int,
    all_comments: Sequence[Mapping[str, Any]],
    all_submitted_reviews: Sequence[Mapping[str, Any]],
    source_archive_digest: str,
    source_archive_case_evidence_digest: str,
) -> dict[str, Any]:
    """Seal the exact REST root, all replies, and their submitted reviews."""

    pull_request = _positive_integer(pull_request, "pull_request")
    root_comment_id = _positive_integer(
        root_comment_id,
        "root_comment_id",
    )
    comments_by_id = _records_by_positive_id(
        list(all_comments),
        field="source archive review comments",
    )
    root = comments_by_id.get(root_comment_id)
    if root is None:
        raise ValueError(
            f"source archive has no review comment {root_comment_id}"
        )
    if root.get("in_reply_to_id") is not None:
        raise ValueError(
            f"source archive comment {root_comment_id} is not a thread root"
        )
    replies = sorted(
        (
            comment
            for identifier, comment in comments_by_id.items()
            if identifier != root_comment_id
            and comment.get("in_reply_to_id") == root_comment_id
        ),
        key=lambda item: (
            str(item.get("created_at") or ""),
            int(item["id"]),
        ),
    )
    comments = [root, *replies]
    review_ids = {
        _positive_integer(
            comment.get("pull_request_review_id"),
            "source archive review comment pull_request_review_id",
        )
        for comment in comments
    }
    reviews_by_id = _records_by_positive_id(
        list(all_submitted_reviews),
        field="source archive submitted reviews",
    )
    missing_reviews = sorted(review_ids - set(reviews_by_id))
    if missing_reviews:
        raise ValueError(
            "source archive is missing submitted reviews for REST thread: "
            + ", ".join(str(identifier) for identifier in missing_reviews)
        )
    evidence = {
        "kind": REST_THREAD_EVIDENCE_KIND,
        "pullRequest": pull_request,
        "rootCommentId": root_comment_id,
        "sourceArchiveDigest": _sha256(
            source_archive_digest,
            "source archive digest",
        ),
        "sourceArchiveCaseEvidenceDigest": _sha256(
            source_archive_case_evidence_digest,
            "source archive case evidence digest",
        ),
        "comments": copy.deepcopy(comments),
        "submittedReviews": copy.deepcopy(
            [reviews_by_id[identifier] for identifier in sorted(review_ids)]
        ),
    }
    evidence["evidenceDigest"] = sha256_json(evidence)
    validate_rest_review_thread_evidence(
        evidence,
        pull_request=pull_request,
        root_comment_id=root_comment_id,
        source_archive_digest=source_archive_digest,
        source_archive_case_evidence_digest=(
            source_archive_case_evidence_digest
        ),
    )
    return evidence


def validate_rest_review_thread_evidence(
    value: Any,
    *,
    pull_request: int | None = None,
    root_comment_id: int | None = None,
    source_archive_digest: str | None = None,
    source_archive_case_evidence_digest: str | None = None,
) -> dict[str, Any]:
    """Validate the exact raw REST projection and its archive bindings."""

    validated, _, _ = _rest_thread_records(
        value,
        pull_request=pull_request,
        root_comment_id=root_comment_id,
        source_archive_digest=source_archive_digest,
        source_archive_case_evidence_digest=(
            source_archive_case_evidence_digest
        ),
    )
    return validated


def _cross_reconcile_messages(
    *,
    raw_messages: Sequence[Any],
    normalized_messages: Sequence[Any],
    rest_evidence: Mapping[str, Any],
    pull_request: int,
    root_comment_id: int,
    source_archive_digest: str | None,
    source_archive_case_evidence_digest: str | None,
) -> bool:
    _, rest_comments, rest_reviews = _rest_thread_records(
        rest_evidence,
        pull_request=pull_request,
        root_comment_id=root_comment_id,
        source_archive_digest=source_archive_digest,
        source_archive_case_evidence_digest=(
            source_archive_case_evidence_digest
        ),
    )
    raw_ids = [
        message.get("databaseId")
        if isinstance(message, Mapping)
        else None
        for message in raw_messages
    ]
    if (
        not raw_ids
        or raw_ids[0] != root_comment_id
        or any(
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier < 1
            for identifier in raw_ids
        )
        or len(raw_ids) != len(set(raw_ids))
        or set(raw_ids) != set(rest_comments)
    ):
        raise ValueError(
            "raw GraphQL and REST review thread message coverage drift"
        )
    if len(normalized_messages) != len(raw_messages):
        raise ValueError("normalized review thread message coverage drift")

    for index, (raw_message, normalized_message) in enumerate(
        zip(raw_messages, normalized_messages, strict=True)
    ):
        if not isinstance(raw_message, Mapping) or not isinstance(
            normalized_message,
            Mapping,
        ):
            raise ValueError("review thread message is malformed")
        if set(normalized_message) != NORMALIZED_MESSAGE_FIELDS:
            raise ValueError(
                f"normalized review thread message {index} fields drifted"
            )
        identifier = int(raw_message["databaseId"])
        rest_message = rest_comments[identifier]
        rest_author = rest_message.get("user")
        graph_author = raw_message.get("author")
        rest_reply = rest_message.get("in_reply_to_id")
        graph_reply_value = raw_message.get("replyTo")
        graph_reply = (
            graph_reply_value.get("databaseId")
            if isinstance(graph_reply_value, Mapping)
            else None
        )
        graph_review = raw_message.get("pullRequestReview")
        if not isinstance(graph_review, Mapping):
            raise ValueError(
                f"raw GraphQL review thread message {identifier} has no "
                "submitted review"
            )
        review_id = graph_review.get("databaseId")
        if (
            isinstance(review_id, bool)
            or not isinstance(review_id, int)
            or review_id
            != rest_message.get("pull_request_review_id")
        ):
            raise ValueError(
                f"raw GraphQL and REST message {identifier} review identity "
                "drift"
            )
        rest_review = rest_reviews.get(review_id)
        if rest_review is None:
            raise ValueError(
                f"REST message {identifier} has no raw submitted review"
            )
        graph_commit_value = graph_review.get("commit")
        graph_commit = (
            graph_commit_value.get("oid")
            if isinstance(graph_commit_value, Mapping)
            else None
        )
        graph_to_rest = {
            "url": (
                raw_message.get("url"),
                rest_message.get("html_url"),
            ),
            "author": (
                (
                    graph_author.get("login")
                    if isinstance(graph_author, Mapping)
                    else None
                ),
                (
                    rest_author.get("login")
                    if isinstance(rest_author, Mapping)
                    else None
                ),
            ),
            "author type": (
                (
                    graph_author.get("__typename")
                    if isinstance(graph_author, Mapping)
                    else None
                ),
                (
                    rest_author.get("type")
                    if isinstance(rest_author, Mapping)
                    else None
                ),
            ),
            "body": (
                raw_message.get("body"),
                rest_message.get("body"),
            ),
            "created timestamp": (
                raw_message.get("createdAt"),
                rest_message.get("created_at"),
            ),
            "updated timestamp": (
                raw_message.get("updatedAt"),
                rest_message.get("updated_at"),
            ),
            "reply identity": (graph_reply, rest_reply),
            "review state": (
                graph_review.get("state"),
                rest_review.get("state"),
            ),
            "review commit": (
                graph_commit,
                rest_review.get("commit_id"),
            ),
        }
        for field, (graph_value, rest_value) in graph_to_rest.items():
            if graph_value != rest_value:
                raise ValueError(
                    f"raw GraphQL and REST message {identifier} {field} drift"
                )

        expected_normalized = {
            **_graph_message_projection(raw_message),
            "authorAssociation": rest_message.get("author_association"),
            "originalCommitId": rest_message.get("original_commit_id"),
        }
        if dict(normalized_message) != expected_normalized:
            raise ValueError(
                f"normalized review thread message {index} drifted from "
                "the exact raw GraphQL/REST evidence"
            )
    return True


def _cross_reconcile_thread_anchor(
    *,
    raw_thread: Mapping[str, Any],
    normalized_thread: Mapping[str, Any],
    rest_evidence: Mapping[str, Any],
    pull_request: int,
    root_comment_id: int,
    source_archive_digest: str | None,
    source_archive_case_evidence_digest: str | None,
) -> None:
    """Require one exact H/current coordinate projection across all sources."""

    _, rest_comments, _ = _rest_thread_records(
        rest_evidence,
        pull_request=pull_request,
        root_comment_id=root_comment_id,
        source_archive_digest=source_archive_digest,
        source_archive_case_evidence_digest=(
            source_archive_case_evidence_digest
        ),
    )
    rest_anchor = rest_review_comment_anchor(
        rest_comments[root_comment_id],
        field=f"REST review comment {root_comment_id}",
    )
    graph_anchor = graphql_review_thread_anchor(
        raw_thread,
        field=f"GraphQL review thread {root_comment_id}",
    )
    comparable_rest = {
        key: rest_anchor[key]
        for key in (
            "path",
            "currentLine",
            "originalLine",
            "currentStartLine",
            "originalStartLine",
            "side",
            "startSide",
        )
    }
    if graph_anchor != comparable_rest:
        raise ValueError(
            f"raw GraphQL and REST thread {root_comment_id} anchor drift"
        )
    expected_normalized = {
        "path": graph_anchor["path"],
        "line": graph_anchor["currentLine"],
        "originalLine": graph_anchor["originalLine"],
        "startLine": graph_anchor["currentStartLine"],
        "originalStartLine": graph_anchor["originalStartLine"],
        "diffSide": graph_anchor["side"],
        "startDiffSide": graph_anchor["startSide"],
    }
    missing = sorted(set(expected_normalized) - set(normalized_thread))
    if missing:
        raise ValueError(
            f"normalized review thread {root_comment_id} is missing anchor "
            "fields: "
            + ", ".join(missing)
        )
    if any(
        normalized_thread.get(key) != expected
        for key, expected in expected_normalized.items()
    ):
        raise ValueError(
            f"normalized review thread {root_comment_id} anchor drift"
        )


def build_review_thread_binding(
    *,
    thread: Mapping[str, Any],
    archive: Mapping[str, Any],
    thread_evidence_digest: str,
    rest_thread_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root_comment_id = _positive_integer(
        thread.get("rootCommentId"),
        "thread.rootCommentId",
    )
    _, raw_threads = validate_graphql_thread_archive(
        archive,
        pull_request=_positive_integer(
            archive.get("pullRequest"),
            "archive.pullRequest",
        ),
    )
    raw_entry = raw_threads.get(root_comment_id)
    if raw_entry is None:
        raise ValueError(
            f"raw GraphQL pages do not contain thread root {root_comment_id}"
        )
    binding = {
        "threadEvidenceDigest": thread_evidence_digest,
        "threadDigest": sha256_json(thread),
        "graphqlArchiveDigest": archive["archiveDigest"],
        "graphqlPageDigest": raw_entry[1],
        "restThreadEvidence": (
            copy.deepcopy(rest_thread_evidence)
            if isinstance(rest_thread_evidence, Mapping)
            else None
        ),
        "thread": dict(thread),
    }
    validate_review_thread_binding(
        binding,
        archive=archive,
        root_comment_id=root_comment_id,
        thread_evidence_digest=thread_evidence_digest,
        require_complete=False,
    )
    return binding


def validate_review_thread_binding(
    value: Any,
    *,
    archive: Mapping[str, Any],
    root_comment_id: int,
    thread_evidence_digest: str,
    require_complete: bool,
    source_archive_digest: str | None = None,
    source_archive_case_evidence_digest: str | None = None,
) -> dict[str, Any]:
    """Bind a normalized thread to exact raw GraphQL and REST evidence."""

    _positive_integer(root_comment_id, "root_comment_id")
    if not isinstance(value, Mapping) or set(value) != THREAD_BINDING_FIELDS:
        raise ValueError("review thread binding fields are invalid")
    expected_evidence_digest = _sha256(
        thread_evidence_digest,
        "thread evidence digest",
    )
    if value.get("threadEvidenceDigest") != expected_evidence_digest:
        raise ValueError("review thread binding belongs to another archive")
    archive_value, raw_threads = validate_graphql_thread_archive(
        archive,
        pull_request=_positive_integer(
            archive.get("pullRequest"),
            "archive.pullRequest",
        ),
    )
    if value.get("graphqlArchiveDigest") != archive_value["archiveDigest"]:
        raise ValueError("review thread binding archive digest mismatch")
    raw_entry = raw_threads.get(root_comment_id)
    if raw_entry is None:
        raise ValueError(
            f"raw GraphQL pages do not contain thread root {root_comment_id}"
        )
    raw_thread, raw_page_digest = raw_entry
    if value.get("graphqlPageDigest") != raw_page_digest:
        raise ValueError("review thread binding raw page digest mismatch")
    rest_evidence = value.get("restThreadEvidence")
    if rest_evidence is not None and not isinstance(rest_evidence, Mapping):
        raise ValueError("REST review thread evidence must be an object")
    if require_complete and not isinstance(rest_evidence, Mapping):
        raise ValueError(
            "paper-ready review thread has no exact raw REST evidence"
        )

    thread = value.get("thread")
    if not isinstance(thread, Mapping):
        raise ValueError("normalized review thread must be an object")
    if thread.get("rootCommentId") != root_comment_id:
        raise ValueError("normalized review thread root identity drift")
    thread_digest = _sha256(
        value.get("threadDigest"),
        "normalized review thread digest",
    )
    if thread_digest != sha256_json(thread):
        raise ValueError("normalized review thread digest mismatch")
    if thread.get("sourceSha256") != sha256_json(raw_thread):
        raise ValueError("normalized review thread is not bound to its raw node")
    if thread.get("resolutionMetadataAvailable") is not True:
        raise ValueError("normalized review thread has no GraphQL metadata")
    for key in (
        "isResolved",
        "isOutdated",
        "path",
        "line",
        "originalLine",
        "startLine",
        "originalStartLine",
        "diffSide",
        "startDiffSide",
    ):
        if key not in thread or key not in raw_thread:
            raise ValueError(
                f"normalized/raw review thread is missing {key}"
            )
        if thread.get(key) != raw_thread.get(key):
            raise ValueError(f"normalized review thread {key} drift")

    comments = raw_thread.get("comments")
    raw_messages = (
        comments.get("nodes") if isinstance(comments, Mapping) else None
    )
    comment_page_info = (
        comments.get("pageInfo") if isinstance(comments, Mapping) else None
    )
    if not isinstance(raw_messages, list) or not isinstance(
        comment_page_info, Mapping
    ):
        raise ValueError("raw GraphQL thread comments are malformed")
    messages = thread.get("messages")
    if not isinstance(messages, list) or len(messages) != len(raw_messages):
        raise ValueError("normalized review thread message coverage drift")
    for index, (message, raw_message) in enumerate(
        zip(messages, raw_messages, strict=True)
    ):
        if not isinstance(message, Mapping) or not isinstance(
            raw_message, Mapping
        ):
            raise ValueError("normalized review thread message is malformed")
        expected = _graph_message_projection(raw_message)
        if any(message.get(key) != expected_value for key, expected_value in expected.items()):
            raise ValueError(
                f"normalized review thread message {index} drifted from "
                "the raw GraphQL page"
            )
    raw_ids = [message.get("databaseId") for message in raw_messages]
    normalized_ids = [
        message.get("id") if isinstance(message, Mapping) else None
        for message in messages
    ]
    graphql_normalized_reconciled = (
        bool(raw_ids)
        and all(
            isinstance(identifier, int) and not isinstance(identifier, bool)
            for identifier in raw_ids
        )
        and len(raw_ids) == len(set(raw_ids))
        and raw_ids == normalized_ids
    )
    if isinstance(rest_evidence, Mapping):
        _cross_reconcile_thread_anchor(
            raw_thread=raw_thread,
            normalized_thread=thread,
            rest_evidence=rest_evidence,
            pull_request=int(archive_value["pullRequest"]),
            root_comment_id=root_comment_id,
            source_archive_digest=source_archive_digest,
            source_archive_case_evidence_digest=(
                source_archive_case_evidence_digest
            ),
        )
        rest_reconciled = _cross_reconcile_messages(
            raw_messages=raw_messages,
            normalized_messages=messages,
            rest_evidence=rest_evidence,
            pull_request=int(archive_value["pullRequest"]),
            root_comment_id=root_comment_id,
            source_archive_digest=source_archive_digest,
            source_archive_case_evidence_digest=(
                source_archive_case_evidence_digest
            ),
        )
    else:
        # Provisional, non-paper artifacts created before a REST source
        # archive exists remain inspectable. Publication never enters this
        # branch because require_complete rejects missing REST evidence.
        rest_reconciled = (
            thread.get("messageIdsReconciledWithRest") is True
        )
    reconciled = graphql_normalized_reconciled and rest_reconciled
    if thread.get("messageIdsReconciledWithRest") is not reconciled:
        raise ValueError(
            "normalized review thread REST reconciliation status drift"
        )
    expected_complete = bool(
        comment_page_info.get("hasNextPage") is False and reconciled
    )
    if thread.get("complete") is not expected_complete:
        raise ValueError(
            "normalized review thread is not a complete GraphQL thread"
        )
    if require_complete and not expected_complete:
        raise ValueError(
            "paper-ready review thread is not fully covered by GraphQL and REST"
        )
    return dict(value)
