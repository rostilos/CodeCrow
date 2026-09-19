from __future__ import annotations

import hashlib
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .curation import (
    _records_by_positive_id,
    _review_comment_matches_source,
    _validate_draft,
)
from .github import GITHUB_API_VERSION, GitHubClient
from .util import read_json, sha256_json, write_json


CURRENT_COMMENT_ATTESTATION_KIND = (
    "codecrow-magento2-current-review-comment-attestation"
)
RELEASE_BLOCKERS = [
    "complete_raw_pull_and_submitted_review_archive",
    "authenticated_graphql_thread_resolution_and_outdated_state",
    "two_independent_human_curators_and_adjudication",
    "paper_ready_release_selection_and_materialization",
]

TOP_LEVEL_FIELDS = {
    "kind",
    "generatedAt",
    "sourceMode",
    "githubApiVersion",
    "repository",
    "draftFileSha256",
    "draftSha256",
    "paperReady",
    "scoringEnabled",
    "currentSelectedCommentsVerified",
    "caseCount",
    "selectedRootCount",
    "completeRestReplyCount",
    "releaseBlockers",
    "warning",
    "cases",
    "attestationDigest",
}
CASE_FIELDS = {
    "pullRequest",
    "pageCount",
    "pages",
    "allReviewCommentCount",
    "allReviewCommentsDigest",
    "selectedRoots",
    "threads",
    "caseEvidenceDigest",
}
PAGE_FIELDS = {
    "pageIndex",
    "request",
    "status",
    "etag",
    "lastModified",
    "response",
    "responseDigest",
    "previousPageDigest",
    "pageDigest",
}
SELECTED_ROOT_FIELDS = {
    "commentId",
    "response",
    "responseDigest",
}
THREAD_FIELDS = {
    "rootCommentId",
    "replyCount",
    "comments",
    "threadDigest",
}


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return str(value)
    return None


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _page_url(
    client: GitHubClient,
    path: str,
    *,
    page: int,
) -> str:
    query = urllib.parse.urlencode({"per_page": 100, "page": page})
    return f"{client.api_url}/{path.lstrip('/')}?{query}"


def _canonical_thread(
    *,
    root: Mapping[str, Any],
    all_comments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    root_id = _positive_integer(root.get("id"), "review comment root ID")
    replies = sorted(
        (
            dict(comment)
            for comment in all_comments
            if comment.get("in_reply_to_id") == root_id
        ),
        key=lambda item: (
            str(item.get("created_at") or ""),
            int(item["id"]),
        ),
    )
    thread = {
        "rootCommentId": root_id,
        "replyCount": len(replies),
        "comments": [dict(root), *replies],
    }
    thread["threadDigest"] = sha256_json(thread)
    return thread


def _fetch_all_review_comments(
    client: GitHubClient,
    *,
    repository: str,
    pull_request: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = f"/repos/{repository}/pulls/{pull_request}/comments"
    comments: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    previous_page_digest: str | None = None
    page_index = 1
    while True:
        response = client.request(
            "GET",
            path,
            query={"per_page": 100, "page": page_index},
        )
        if response.status != 200 or not isinstance(response.value, list):
            raise ValueError(
                f"GitHub returned invalid review comments for PR {pull_request}"
            )
        if any(not isinstance(item, Mapping) for item in response.value):
            raise ValueError(
                f"GitHub returned malformed review comments for PR {pull_request}"
            )
        raw_page = [dict(item) for item in response.value]
        page = {
            "pageIndex": page_index,
            "request": {
                "method": "GET",
                "url": _page_url(client, path, page=page_index),
            },
            "status": response.status,
            "etag": _header(response.headers, "etag"),
            "lastModified": _header(response.headers, "last-modified"),
            "response": raw_page,
            "responseDigest": sha256_json(raw_page),
            "previousPageDigest": previous_page_digest,
        }
        page["pageDigest"] = sha256_json(page)
        pages.append(page)
        previous_page_digest = page["pageDigest"]
        comments.extend(raw_page)
        if len(raw_page) < 100:
            break
        page_index += 1
        if page_index > 100:
            raise ValueError(
                f"GitHub review-comment pagination is unbounded for PR {pull_request}"
            )
    return comments, pages


def _validate_selected_root(
    *,
    raw: Mapping[str, Any],
    source: Mapping[str, Any],
    repository: str,
    pull_request: int,
) -> None:
    comment_id = _positive_integer(
        source.get("id"),
        f"PR {pull_request} selected review comment ID",
    )
    if not _review_comment_matches_source(raw, source):
        raise ValueError(
            f"PR {pull_request} comment {comment_id} drifted from the draft"
        )
    if raw.get("pull_request_url") != (
        f"https://api.github.com/repos/{repository}/pulls/{pull_request}"
    ):
        raise ValueError(
            f"PR {pull_request} comment {comment_id} belongs to another pull request"
        )


def validate_current_comment_attestation(
    value: Any,
    *,
    draft_path: Path,
    repository: str,
) -> dict[str, Any]:
    """Recompute every attestation digest and draft/current-comment binding."""

    draft = _validate_draft(read_json(draft_path))
    if not isinstance(value, Mapping) or set(value) != TOP_LEVEL_FIELDS:
        raise ValueError("current-comment attestation fields are invalid")
    if value.get("kind") != CURRENT_COMMENT_ATTESTATION_KIND:
        raise ValueError("current-comment attestation kind is invalid")
    if value.get("githubApiVersion") != GITHUB_API_VERSION:
        raise ValueError("current-comment attestation API version is invalid")
    if value.get("repository") != repository or draft.get("repository") != repository:
        raise ValueError("current-comment attestation repository is invalid")
    if value.get("sourceMode") not in {"live", "cache-only"}:
        raise ValueError("current-comment attestation source mode is invalid")
    if (
        value.get("paperReady") is not False
        or value.get("scoringEnabled") is not False
        or value.get("currentSelectedCommentsVerified") is not True
        or value.get("releaseBlockers") != RELEASE_BLOCKERS
    ):
        raise ValueError("current-comment attestation publication status is invalid")
    generated_at = value.get("generatedAt")
    if not isinstance(generated_at, str) or not generated_at.endswith("Z"):
        raise ValueError("current-comment attestation generatedAt is invalid")
    try:
        datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "current-comment attestation generatedAt is invalid"
        ) from exc
    warning = value.get("warning")
    if not isinstance(warning, str) or not warning:
        raise ValueError("current-comment attestation warning is missing")

    if value.get("draftFileSha256") != hashlib.sha256(
        draft_path.read_bytes()
    ).hexdigest() or value.get("draftSha256") != sha256_json(draft):
        raise ValueError("current-comment attestation draft binding mismatch")
    digest_value = dict(value)
    declared_digest = digest_value.pop("attestationDigest", None)
    if declared_digest != sha256_json(digest_value):
        raise ValueError("current-comment attestation digest mismatch")

    cases = value.get("cases")
    if not isinstance(cases, list) or len(cases) != len(draft["cases"]):
        raise ValueError("current-comment attestation case coverage is incomplete")
    total_roots = 0
    total_replies = 0
    for case_index, (case_record, source_case) in enumerate(
        zip(cases, draft["cases"], strict=True),
        start=1,
    ):
        if not isinstance(case_record, Mapping) or set(case_record) != CASE_FIELDS:
            raise ValueError(
                f"current-comment attestation case {case_index} fields are invalid"
            )
        pull_request = int(source_case["pr_number"])
        if case_record.get("pullRequest") != pull_request:
            raise ValueError("current-comment attestation case order drift")
        case_digest_value = dict(case_record)
        case_digest = case_digest_value.pop("caseEvidenceDigest", None)
        if case_digest != sha256_json(case_digest_value):
            raise ValueError(
                f"PR {pull_request} current-comment evidence digest mismatch"
            )

        pages = case_record.get("pages")
        if (
            not isinstance(pages, list)
            or not pages
            or case_record.get("pageCount") != len(pages)
        ):
            raise ValueError(f"PR {pull_request} has invalid raw page coverage")
        raw_comments: list[dict[str, Any]] = []
        previous_page_digest: str | None = None
        for page_index, page in enumerate(pages, start=1):
            if not isinstance(page, Mapping) or set(page) != PAGE_FIELDS:
                raise ValueError(
                    f"PR {pull_request} raw page {page_index} fields are invalid"
                )
            request = page.get("request")
            expected_url = (
                f"https://api.github.com/repos/{repository}/pulls/"
                f"{pull_request}/comments?per_page=100&page={page_index}"
            )
            if (
                page.get("pageIndex") != page_index
                or page.get("status") != 200
                or page.get("previousPageDigest") != previous_page_digest
                or request != {"method": "GET", "url": expected_url}
            ):
                raise ValueError(
                    f"PR {pull_request} raw page {page_index} request chain is invalid"
                )
            response = page.get("response")
            if (
                not isinstance(response, list)
                or any(not isinstance(item, Mapping) for item in response)
                or page.get("responseDigest") != sha256_json(response)
            ):
                raise ValueError(
                    f"PR {pull_request} raw page {page_index} response is invalid"
                )
            page_digest_value = dict(page)
            page_digest = page_digest_value.pop("pageDigest", None)
            if page_digest != sha256_json(page_digest_value):
                raise ValueError(
                    f"PR {pull_request} raw page {page_index} digest mismatch"
                )
            if page_index < len(pages) and len(response) != 100:
                raise ValueError(
                    f"PR {pull_request} raw pagination ended before the final page"
                )
            if page_index == len(pages) and len(response) >= 100:
                raise ValueError(
                    f"PR {pull_request} raw pagination is missing a final page"
                )
            previous_page_digest = page_digest
            raw_comments.extend(dict(item) for item in response)

        comments_by_id = _records_by_positive_id(
            raw_comments,
            label=f"PR {pull_request} current review comment",
        )
        if (
            case_record.get("allReviewCommentCount") != len(raw_comments)
            or case_record.get("allReviewCommentsDigest")
            != sha256_json(raw_comments)
        ):
            raise ValueError(f"PR {pull_request} raw comment population drift")
        expected_pull_url = (
            f"https://api.github.com/repos/{repository}/pulls/{pull_request}"
        )
        if any(
            comment.get("pull_request_url") != expected_pull_url
            for comment in raw_comments
        ):
            raise ValueError(
                f"PR {pull_request} raw comment list contains another pull request"
            )

        selected = case_record.get("selectedRoots")
        threads = case_record.get("threads")
        source_comments = source_case["gold_comments"]
        if (
            not isinstance(selected, list)
            or not isinstance(threads, list)
            or len(selected) != len(source_comments)
            or len(threads) != len(source_comments)
        ):
            raise ValueError(
                f"PR {pull_request} selected current-comment coverage is incomplete"
            )
        for selected_record, thread, source in zip(
            selected,
            threads,
            source_comments,
            strict=True,
        ):
            if (
                not isinstance(selected_record, Mapping)
                or set(selected_record) != SELECTED_ROOT_FIELDS
            ):
                raise ValueError(
                    f"PR {pull_request} selected root fields are invalid"
                )
            comment_id = int(source["id"])
            raw = comments_by_id.get(comment_id)
            if (
                raw is None
                or selected_record.get("commentId") != comment_id
                or selected_record.get("response") != raw
                or selected_record.get("responseDigest") != sha256_json(raw)
            ):
                raise ValueError(
                    f"PR {pull_request} selected root {comment_id} raw binding drift"
                )
            _validate_selected_root(
                raw=raw,
                source=source,
                repository=repository,
                pull_request=pull_request,
            )

            expected_thread = _canonical_thread(
                root=raw,
                all_comments=raw_comments,
            )
            if (
                not isinstance(thread, Mapping)
                or set(thread) != THREAD_FIELDS
                or dict(thread) != expected_thread
            ):
                raise ValueError(
                    f"PR {pull_request} selected root {comment_id} thread drift"
                )
            total_roots += 1
            total_replies += expected_thread["replyCount"]

    if (
        value.get("caseCount") != len(cases)
        or value.get("selectedRootCount") != total_roots
        or value.get("completeRestReplyCount") != total_replies
    ):
        raise ValueError("current-comment attestation aggregate counts drift")
    return dict(value)


def attest_current_comments(
    client: GitHubClient,
    *,
    draft_path: Path,
    output: Path,
    repository: str,
) -> dict[str, Any]:
    """Fetch every current REST comment list once and seal selected roots/threads."""

    draft = _validate_draft(read_json(draft_path))
    if draft.get("repository") != repository:
        raise ValueError("draft repository does not match configured repository")
    cases = []
    selected_root_count = 0
    reply_count = 0
    for source_case in draft["cases"]:
        pull_request = int(source_case["pr_number"])
        all_comments, pages = _fetch_all_review_comments(
            client,
            repository=repository,
            pull_request=pull_request,
        )
        comments_by_id = _records_by_positive_id(
            all_comments,
            label=f"PR {pull_request} current review comment",
        )
        selected_roots = []
        threads = []
        for source in source_case["gold_comments"]:
            comment_id = int(source["id"])
            raw = comments_by_id.get(comment_id)
            if raw is None:
                raise ValueError(
                    f"PR {pull_request} has no selected comment {comment_id}"
                )
            _validate_selected_root(
                raw=raw,
                source=source,
                repository=repository,
                pull_request=pull_request,
            )
            selected_roots.append(
                {
                    "commentId": comment_id,
                    "response": dict(raw),
                    "responseDigest": sha256_json(raw),
                }
            )
            thread = _canonical_thread(
                root=raw,
                all_comments=all_comments,
            )
            threads.append(thread)
            selected_root_count += 1
            reply_count += thread["replyCount"]
        case_record = {
            "pullRequest": pull_request,
            "pageCount": len(pages),
            "pages": pages,
            "allReviewCommentCount": len(all_comments),
            "allReviewCommentsDigest": sha256_json(all_comments),
            "selectedRoots": selected_roots,
            "threads": threads,
        }
        case_record["caseEvidenceDigest"] = sha256_json(case_record)
        cases.append(case_record)

    source_mode = "cache-only" if client.offline else "live"
    result = {
        "kind": CURRENT_COMMENT_ATTESTATION_KIND,
        "generatedAt": _now(),
        "sourceMode": source_mode,
        "githubApiVersion": GITHUB_API_VERSION,
        "repository": repository,
        "draftFileSha256": hashlib.sha256(draft_path.read_bytes()).hexdigest(),
        "draftSha256": sha256_json(draft),
        "paperReady": False,
        "scoringEnabled": False,
        "currentSelectedCommentsVerified": True,
        "caseCount": len(cases),
        "selectedRootCount": selected_root_count,
        "completeRestReplyCount": reply_count,
        "releaseBlockers": list(RELEASE_BLOCKERS),
        "warning": (
            "This cache-only artifact verifies exact cached REST comment bodies "
            "and complete REST reply sets, but does not prove when GitHub last "
            "served them and is not paper-ready."
            if client.offline
            else (
                "This live REST artifact verifies current comment bodies and "
                "complete REST reply sets, but is not a gold set or a substitute "
                "for the full PR/review archive, authenticated GraphQL threads, "
                "and independent human adjudication."
            )
        ),
        "cases": cases,
    }
    result["attestationDigest"] = sha256_json(result)
    validate_current_comment_attestation(
        result,
        draft_path=draft_path,
        repository=repository,
    )
    write_json(output, result)
    return result
