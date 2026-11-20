from __future__ import annotations

import os
import re
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .corpus import (
    attach_corpus_digest,
    decision_binding_digest,
    validate_corpus,
)
from .github import GITHUB_API_VERSION, GitHubClient
from .path_transition import (
    resolve_path_transition,
    validate_path_transition_evidence,
)
from .thread_provenance import (
    rest_review_comment_anchor,
    validate_graphql_thread_archive,
    validate_review_thread_binding,
)
from .util import (
    deterministic_git_diff_command,
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


SELECTION_KIND = "codecrow-magento2-review-selection"
DISCOVERY_KIND = "codecrow-magento2-review-discovery"
DISCOVERY_SELECTION_LINK_KIND = (
    "codecrow-magento2-review-discovery-selection-link"
)
DISCOVERY_API_URL = "https://api.github.com"
DISCOVERY_POLICY_VERSION = "magento2-review-candidate-discovery-2026-07-29"
DISCOVERY_LINK_POLICY_VERSION = (
    "magento2-review-discovery-selection-link-2026-07-29"
)
DISCOVERY_PER_PAGE = 100
DISCOVERY_ENDPOINT_SUFFIX = "/pulls/comments"
ACTIONABLE_HINT = re.compile(
    r"\b("
    r"add|avoid|break|bug|change|could|incorrect|instead|missing|move|must|"
    r"need|please|recommend|remove|risk|should|wrong"
    r")\b",
    re.IGNORECASE,
)
DISCOVERY_BASE_QUERY = {
    "sort": "created",
    "direction": "desc",
}
DISCOVERY_FILTER_POLICY = {
    "version": DISCOVERY_POLICY_VERSION,
    "scope": "candidate_generation_only_not_gold_eligibility",
    "predicateOrder": [
        "root_non_reply_inline_comment",
        "human_github_user",
        "submitted_review_identity_present",
        "original_commit_identity_present",
        "nonempty_path_and_body",
        "right_side_original_or_current_anchor",
        "canonical_repository_pull_request_url",
        "positive_comment_and_review_ids",
        "lowercase_full_original_commit_sha",
        "nonempty_reviewer_and_created_updated_timestamps",
    ],
    "grouping": [
        "canonical_pull_request_number",
        "exact_original_commit_id",
    ],
    "actionableHint": {
        "pattern": ACTIONABLE_HINT.pattern,
        "flags": ["IGNORECASE"],
        "purpose": "ranking_hint_only_not_semantic_label",
    },
    "rejectionReasons": [
        "not_root_human_right_review_comment",
        "noncanonical_pull_request_url",
        "invalid_candidate_identity",
    ],
}
DISCOVERY_ORDERING_POLICY = {
    "version": DISCOVERY_POLICY_VERSION,
    "candidateOrder": [
        {"field": "actionableHintCount", "direction": "descending"},
        {"field": "reviewCommentCount", "direction": "descending"},
        {"field": "pullRequest", "direction": "descending"},
        {"field": "headSha", "direction": "ascending"},
    ],
    "commentIds": "ascending_numeric",
    "reviewers": "ascending_unicode_codepoint_unique",
}
DISCOVERY_PAGINATION_POLICY = {
    "version": DISCOVERY_POLICY_VERSION,
    "method": "GET",
    "firstPage": 1,
    "perPage": DISCOVERY_PER_PAGE,
    "pageParameter": "page",
    "termination": "first_short_page_or_requested_max_pages",
    "concurrency": "sequential",
    "deduplication": "none_duplicate_comment_ids_fail",
}
DISCOVERY_TOP_LEVEL_FIELDS = {
    "kind",
    "generatedAt",
    "sourceMode",
    "githubApiVersion",
    "repository",
    "source",
    "filterPolicy",
    "orderingPolicy",
    "pages",
    "pageCount",
    "terminationReason",
    "rawPages",
    "rawCommentCount",
    "rawCommentPopulationDigest",
    "rejectedCommentCount",
    "rejectionCounts",
    "candidateCount",
    "candidates",
    "candidateSetDigest",
    "discoveryDigest",
}
DISCOVERY_PAGE_FIELDS = {
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
DISCOVERY_SOURCE_FIELDS = {
    "apiUrl",
    "endpoint",
    "baseQuery",
    "paginationPolicy",
}
DISCOVERY_REQUEST_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "codecrow-magento2-benchmark",
    "X-GitHub-Api-Version": GITHUB_API_VERSION,
}
DISCOVERY_LINK_POLICY = {
    "version": DISCOVERY_LINK_POLICY_VERSION,
    "candidateIdentity": "exact_pull_request_and_original_commit_id",
    "commentMembership": "every_selected_comment_id_is_in_bound_candidate",
    "selectionOrder": "preserved_and_digest_bound",
    "unselectedPool": "bound_by_candidate_set_digest",
}
DISCOVERY_LINK_FIELDS = {
    "kind",
    "generatedAt",
    "repository",
    "discoveryDigest",
    "candidateSetDigest",
    "selectionKind",
    "selectionDigest",
    "selectionCaseCount",
    "linkagePolicy",
    "selectedCandidates",
    "selectedCandidateDigest",
    "linkageDigest",
}
DISCOVERY_LINK_CASE_FIELDS = {
    "selectionIndex",
    "selectionCaseId",
    "pullRequest",
    "headSha",
    "selectedCommentIds",
    "candidateDigest",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _repo_parts(repository: str) -> tuple[str, str]:
    parts = repository.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("repository must be in owner/name form")
    return parts[0], parts[1]


def _root_human_right_comment(comment: Mapping[str, Any]) -> bool:
    user = comment.get("user")
    return (
        isinstance(user, Mapping)
        and user.get("type") == "User"
        and comment.get("in_reply_to_id") is None
        and comment.get("pull_request_review_id") is not None
        and comment.get("original_commit_id") is not None
        and isinstance(comment.get("path"), str)
        and bool(comment["path"])
        and (comment.get("original_side") or comment.get("side")) == "RIGHT"
        and isinstance(comment.get("body"), str)
        and bool(comment["body"].strip())
    )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return str(value)
    return None


def _discovery_endpoint(repository: str) -> str:
    _repo_parts(repository)
    return f"/repos/{repository}{DISCOVERY_ENDPOINT_SUFFIX}"


def _discovery_page_query(page: int) -> dict[str, Any]:
    return {
        **DISCOVERY_BASE_QUERY,
        "per_page": DISCOVERY_PER_PAGE,
        "page": page,
    }


def _discovery_page_url(endpoint: str, page: int) -> str:
    return (
        f"{DISCOVERY_API_URL}/{endpoint.lstrip('/')}?"
        + urllib.parse.urlencode(_discovery_page_query(page))
    )


def _discovery_request(endpoint: str, page: int) -> dict[str, Any]:
    query = _discovery_page_query(page)
    return {
        "method": "GET",
        "path": endpoint,
        "query": query,
        "url": _discovery_page_url(endpoint, page),
        "representationHeaders": DISCOVERY_REQUEST_HEADERS,
    }


def _positive_int(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and value > 0
    )


def _candidate_comment_identity(
    comment: Mapping[str, Any],
    *,
    repository: str,
) -> tuple[int, str] | str:
    if not _root_human_right_comment(comment):
        return "not_root_human_right_review_comment"
    pull_url = comment.get("pull_request_url")
    prefix = f"{DISCOVERY_API_URL}/repos/{repository}/pulls/"
    if (
        not isinstance(pull_url, str)
        or not pull_url.startswith(prefix)
        or not pull_url[len(prefix) :].isdigit()
        or pull_url[len(prefix) :].startswith("0")
    ):
        return "noncanonical_pull_request_url"
    pull_request = int(pull_url[len(prefix) :])
    user = comment.get("user")
    original_commit_id = comment.get("original_commit_id")
    if (
        not _positive_int(comment.get("id"))
        or not _positive_int(comment.get("pull_request_review_id"))
        or not isinstance(user, Mapping)
        or not isinstance(user.get("login"), str)
        or not user["login"].strip()
        or not isinstance(original_commit_id, str)
        or re.fullmatch(r"[0-9a-f]{40}", original_commit_id) is None
        or not isinstance(comment.get("created_at"), str)
        or not comment["created_at"]
        or not isinstance(comment.get("updated_at"), str)
        or not comment["updated_at"]
    ):
        return "invalid_candidate_identity"
    return pull_request, original_commit_id


def _candidate_sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        -int(item["actionableHintCount"]),
        -int(item["reviewCommentCount"]),
        -int(item["pullRequest"]),
        str(item["headSha"]),
    )


def _derive_discovery_candidates(
    comments: Iterable[Any],
    *,
    repository: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    grouped: dict[tuple[int, str], list[Mapping[str, Any]]] = defaultdict(list)
    rejection_counts = {
        reason: 0
        for reason in DISCOVERY_FILTER_POLICY["rejectionReasons"]
    }
    observed_comment_ids: set[int] = set()
    for comment in comments:
        if not isinstance(comment, Mapping):
            raise ValueError("GitHub discovery page contains a non-object comment")
        raw_comment_id = comment.get("id")
        if _positive_int(raw_comment_id):
            comment_id = int(raw_comment_id)
            if comment_id in observed_comment_ids:
                raise ValueError(
                    f"GitHub discovery pagination repeated comment {comment_id}"
                )
            observed_comment_ids.add(comment_id)
        identity = _candidate_comment_identity(
            comment,
            repository=repository,
        )
        if isinstance(identity, str):
            rejection_counts[identity] += 1
            continue
        comment_id = int(comment["id"])
        grouped[identity].append(comment)

    candidates: list[dict[str, Any]] = []
    for (number, head_sha), snapshot_comments in grouped.items():
        actionable = [
            comment
            for comment in snapshot_comments
            if ACTIONABLE_HINT.search(str(comment["body"]))
        ]
        candidates.append(
            {
                "pullRequest": number,
                "headSha": head_sha,
                "reviewCommentCount": len(snapshot_comments),
                "actionableHintCount": len(actionable),
                "commentIds": sorted(
                    int(comment["id"]) for comment in snapshot_comments
                ),
                "reviewers": sorted(
                    {
                        str(comment["user"]["login"])
                        for comment in snapshot_comments
                    }
                ),
                "firstCommentAt": min(
                    str(comment["created_at"])
                    for comment in snapshot_comments
                ),
                "lastCommentAt": max(
                    str(comment["created_at"])
                    for comment in snapshot_comments
                ),
            }
        )
    candidates.sort(key=_candidate_sort_key)
    return candidates, rejection_counts


def _discovery_page(
    *,
    endpoint: str,
    page_index: int,
    response: Any,
    previous_page_digest: str | None,
) -> dict[str, Any]:
    if response.status != 200 or not isinstance(response.value, list):
        raise ValueError(
            f"GitHub returned an invalid discovery page {page_index}"
        )
    if any(not isinstance(item, Mapping) for item in response.value):
        raise ValueError(
            f"GitHub returned a malformed discovery page {page_index}"
        )
    raw_page = [dict(item) for item in response.value]
    page = {
        "pageIndex": page_index,
        "request": _discovery_request(endpoint, page_index),
        "status": response.status,
        "etag": _header(response.headers, "etag"),
        "lastModified": _header(response.headers, "last-modified"),
        "response": raw_page,
        "responseDigest": sha256_json(raw_page),
        "previousPageDigest": previous_page_digest,
    }
    page["pageDigest"] = sha256_json(page)
    return page


def discover(
    client: GitHubClient,
    *,
    repository: str,
    pages: int,
    output: Path,
) -> dict[str, Any]:
    """Collect and seal a deterministic candidate pool without gold labels."""

    if isinstance(pages, bool) or not isinstance(pages, int) or pages < 1:
        raise ValueError("discovery pages must be a positive integer")
    if client.api_url != DISCOVERY_API_URL:
        raise ValueError(
            "Magento discovery requires the canonical GitHub API endpoint "
            f"{DISCOVERY_API_URL}"
        )
    endpoint = _discovery_endpoint(repository)
    raw_pages: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    previous_page_digest: str | None = None
    termination_reason = "requested_max_pages"
    for page_index in range(1, pages + 1):
        response = client.request(
            "GET",
            endpoint,
            query=_discovery_page_query(page_index),
        )
        page = _discovery_page(
            endpoint=endpoint,
            page_index=page_index,
            response=response,
            previous_page_digest=previous_page_digest,
        )
        raw_pages.append(page)
        previous_page_digest = str(page["pageDigest"])
        comments.extend(page["response"])
        if len(page["response"]) < DISCOVERY_PER_PAGE:
            termination_reason = "short_page"
            break
    candidates, rejection_counts = _derive_discovery_candidates(
        comments,
        repository=repository,
    )
    rejected = sum(rejection_counts.values())
    payload = {
        "kind": DISCOVERY_KIND,
        "generatedAt": _now(),
        "sourceMode": "cache-only" if client.offline else "live",
        "githubApiVersion": GITHUB_API_VERSION,
        "repository": repository,
        "source": {
            "apiUrl": DISCOVERY_API_URL,
            "endpoint": endpoint,
            "baseQuery": DISCOVERY_BASE_QUERY,
            "paginationPolicy": DISCOVERY_PAGINATION_POLICY,
        },
        "filterPolicy": DISCOVERY_FILTER_POLICY,
        "orderingPolicy": DISCOVERY_ORDERING_POLICY,
        "pages": pages,
        "pageCount": len(raw_pages),
        "terminationReason": termination_reason,
        "rawPages": raw_pages,
        "rawCommentCount": len(comments),
        "rawCommentPopulationDigest": sha256_json(comments),
        "rejectedCommentCount": rejected,
        "rejectionCounts": rejection_counts,
        "candidateCount": len(candidates),
        "candidates": candidates,
        "candidateSetDigest": sha256_json(candidates),
    }
    payload["discoveryDigest"] = sha256_json(payload)
    validate_discovery(payload)
    write_json(output, payload)
    return payload


def _validate_discovery_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("discovery generatedAt must be a UTC timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("discovery generatedAt must be a UTC timestamp") from exc


def validate_discovery(
    value: Any,
    *,
    repository: str | None = None,
) -> dict[str, Any]:
    """Recompute raw-page, filtering, ordering, and candidate-set provenance."""

    if not isinstance(value, Mapping) or set(value) != DISCOVERY_TOP_LEVEL_FIELDS:
        raise ValueError("discovery artifact fields are invalid")
    if value.get("kind") != DISCOVERY_KIND:
        raise ValueError("discovery artifact kind is invalid")
    digest_value = dict(value)
    declared_digest = digest_value.pop("discoveryDigest", None)
    if declared_digest != sha256_json(digest_value):
        raise ValueError("discovery artifact digest mismatch")
    _validate_discovery_timestamp(value.get("generatedAt"))
    if value.get("sourceMode") not in {"live", "cache-only"}:
        raise ValueError("discovery source mode is invalid")
    if value.get("githubApiVersion") != GITHUB_API_VERSION:
        raise ValueError("discovery GitHub API version is invalid")

    artifact_repository = value.get("repository")
    if not isinstance(artifact_repository, str):
        raise ValueError("discovery repository is invalid")
    _repo_parts(artifact_repository)
    if repository is not None and artifact_repository != repository:
        raise ValueError(
            "discovery repository does not match the requested repository"
        )
    endpoint = _discovery_endpoint(artifact_repository)
    source = value.get("source")
    if not isinstance(source, Mapping) or set(source) != DISCOVERY_SOURCE_FIELDS:
        raise ValueError("discovery source fields are invalid")
    if source != {
        "apiUrl": DISCOVERY_API_URL,
        "endpoint": endpoint,
        "baseQuery": DISCOVERY_BASE_QUERY,
        "paginationPolicy": DISCOVERY_PAGINATION_POLICY,
    }:
        raise ValueError("discovery endpoint/query/pagination policy drift")
    if value.get("filterPolicy") != DISCOVERY_FILTER_POLICY:
        raise ValueError("discovery rejection/filter policy drift")
    if value.get("orderingPolicy") != DISCOVERY_ORDERING_POLICY:
        raise ValueError("discovery candidate ordering policy drift")

    requested_pages = value.get("pages")
    page_count = value.get("pageCount")
    raw_pages = value.get("rawPages")
    if (
        isinstance(requested_pages, bool)
        or not isinstance(requested_pages, int)
        or requested_pages < 1
        or isinstance(page_count, bool)
        or not isinstance(page_count, int)
        or page_count < 1
        or page_count > requested_pages
        or not isinstance(raw_pages, list)
        or len(raw_pages) != page_count
    ):
        raise ValueError("discovery page coverage is invalid")

    comments: list[dict[str, Any]] = []
    previous_page_digest: str | None = None
    for page_index, page in enumerate(raw_pages, start=1):
        if not isinstance(page, Mapping) or set(page) != DISCOVERY_PAGE_FIELDS:
            raise ValueError(f"discovery page {page_index} fields are invalid")
        if (
            page.get("pageIndex") != page_index
            or page.get("request") != _discovery_request(endpoint, page_index)
            or page.get("status") != 200
            or page.get("previousPageDigest") != previous_page_digest
            or (
                page.get("etag") is not None
                and not isinstance(page.get("etag"), str)
            )
            or (
                page.get("lastModified") is not None
                and not isinstance(page.get("lastModified"), str)
            )
        ):
            raise ValueError(
                f"discovery page {page_index} request/response chain is invalid"
            )
        response = page.get("response")
        if (
            not isinstance(response, list)
            or any(not isinstance(item, Mapping) for item in response)
            or page.get("responseDigest") != sha256_json(response)
        ):
            raise ValueError(f"discovery page {page_index} response is invalid")
        page_digest_value = dict(page)
        page_digest = page_digest_value.pop("pageDigest", None)
        if page_digest != sha256_json(page_digest_value):
            raise ValueError(f"discovery page {page_index} digest mismatch")
        if page_index < page_count and len(response) != DISCOVERY_PER_PAGE:
            raise ValueError("discovery pagination continued after a short page")
        previous_page_digest = str(page_digest)
        comments.extend(dict(item) for item in response)

    final_page_size = len(raw_pages[-1]["response"])
    termination_reason = value.get("terminationReason")
    if termination_reason == "short_page":
        if final_page_size >= DISCOVERY_PER_PAGE:
            raise ValueError("discovery short-page termination is invalid")
    elif termination_reason == "requested_max_pages":
        if page_count != requested_pages or final_page_size != DISCOVERY_PER_PAGE:
            raise ValueError("discovery max-page termination is invalid")
    else:
        raise ValueError("discovery termination reason is invalid")

    if (
        value.get("rawCommentCount") != len(comments)
        or value.get("rawCommentPopulationDigest") != sha256_json(comments)
    ):
        raise ValueError("discovery raw comment population drift")
    candidates, rejection_counts = _derive_discovery_candidates(
        comments,
        repository=artifact_repository,
    )
    rejected = sum(rejection_counts.values())
    if (
        value.get("rejectedCommentCount") != rejected
        or value.get("rejectionCounts") != rejection_counts
    ):
        raise ValueError("discovery rejection accounting drift")
    if (
        value.get("candidateCount") != len(candidates)
        or value.get("candidates") != candidates
        or value.get("candidateSetDigest") != sha256_json(candidates)
    ):
        raise ValueError("discovery candidate set/order drift")
    return dict(value)


def _selection_identity(
    selection: Any,
) -> tuple[str, str, list[dict[str, Any]]]:
    if not isinstance(selection, Mapping):
        raise ValueError("selection linkage source must be an object")
    kind = selection.get("kind")
    if kind == SELECTION_KIND:
        digest_value = dict(selection)
        declared_digest = digest_value.pop("selectionDigest", None)
        if declared_digest != sha256_json(digest_value):
            raise ValueError("selection linkage source digest is invalid")
        selection_digest = str(declared_digest)
        raw_cases = selection.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise ValueError("selection linkage source has no cases")
        cases = []
        for index, case in enumerate(raw_cases, start=1):
            if not isinstance(case, Mapping):
                raise ValueError("selection linkage case must be an object")
            pull_request = case.get("pullRequest")
            if not _positive_int(pull_request):
                raise ValueError("selection linkage PR number is invalid")
            comment_ids = case.get("commentIds")
            if (
                not isinstance(comment_ids, list)
                or not comment_ids
                or any(not _positive_int(item) for item in comment_ids)
                or len(comment_ids) != len(set(comment_ids))
            ):
                raise ValueError("selection linkage comment IDs are invalid")
            cases.append(
                {
                    "selectionIndex": index,
                    "selectionCaseId": require_text(
                        case.get("caseId"),
                        "selection linkage case ID",
                    ),
                    "pullRequest": int(pull_request),
                    "headSha": require_full_sha(
                        case.get("headSha"),
                        "selection linkage head SHA",
                    ),
                    "selectedCommentIds": [int(item) for item in comment_ids],
                }
            )
        return str(kind), selection_digest, cases
    if kind == "codecrow-magento2-review-corpus-draft":
        if selection.get("repository") != "magento/magento2":
            raise ValueError("draft linkage repository is invalid")
        raw_cases = selection.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise ValueError("draft linkage source has no cases")
        cases = []
        for index, case in enumerate(raw_cases, start=1):
            if not isinstance(case, Mapping):
                raise ValueError("draft linkage case must be an object")
            pull_request = case.get("pr_number")
            if not _positive_int(pull_request):
                raise ValueError("draft linkage PR number is invalid")
            comments = case.get("gold_comments")
            if not isinstance(comments, list) or not comments:
                raise ValueError("draft linkage case has no selected comments")
            comment_ids = [
                comment.get("id") if isinstance(comment, Mapping) else None
                for comment in comments
            ]
            if (
                any(not _positive_int(item) for item in comment_ids)
                or len(comment_ids) != len(set(comment_ids))
            ):
                raise ValueError("draft linkage comment IDs are invalid")
            cases.append(
                {
                    "selectionIndex": index,
                    "selectionCaseId": require_text(
                        case.get("case_id"),
                        "draft linkage case ID",
                    ),
                    "pullRequest": int(pull_request),
                    "headSha": require_full_sha(
                        case.get("benchmark_head_sha"),
                        "draft linkage head SHA",
                    ),
                    "selectedCommentIds": [int(item) for item in comment_ids],
                }
            )
        return str(kind), sha256_json(selection), cases
    raise ValueError("selection linkage source kind is unsupported")


def _linked_selection_candidates(
    *,
    discovery: Mapping[str, Any],
    selection: Any,
) -> tuple[str, str, list[dict[str, Any]]]:
    if (
        isinstance(selection, Mapping)
        and selection.get("repository") is not None
        and selection.get("repository") != discovery["repository"]
    ):
        raise ValueError("selection and discovery repositories differ")
    selection_kind, selection_digest, selection_cases = _selection_identity(
        selection
    )
    by_identity: dict[tuple[int, str], Mapping[str, Any]] = {}
    for candidate in discovery["candidates"]:
        identity = (
            int(candidate["pullRequest"]),
            str(candidate["headSha"]),
        )
        if identity in by_identity:
            raise ValueError("discovery contains a duplicate candidate identity")
        by_identity[identity] = candidate
    linked = []
    observed_selection_identities: set[tuple[int, str]] = set()
    for case in selection_cases:
        identity = (case["pullRequest"], case["headSha"])
        if identity in observed_selection_identities:
            raise ValueError("selection contains a duplicate discovery candidate")
        observed_selection_identities.add(identity)
        candidate = by_identity.get(identity)
        if candidate is None:
            raise ValueError(
                "selection case is absent from the bound discovery candidate set: "
                f"PR {identity[0]} at {identity[1]}"
            )
        candidate_comment_ids = set(candidate["commentIds"])
        if not set(case["selectedCommentIds"]).issubset(candidate_comment_ids):
            raise ValueError(
                f"selection PR {identity[0]} contains a comment outside the "
                "bound discovery candidate"
            )
        linked.append(
            {
                **case,
                "candidateDigest": sha256_json(candidate),
            }
        )
    return selection_kind, selection_digest, linked


def build_discovery_selection_linkage(
    discovery: Any,
    *,
    selection: Any,
) -> dict[str, Any]:
    """Bind an ordered draft/released selection to an exact discovery pool."""

    validated = validate_discovery(discovery)
    selection_kind, selection_digest, linked = _linked_selection_candidates(
        discovery=validated,
        selection=selection,
    )
    linkage = {
        "kind": DISCOVERY_SELECTION_LINK_KIND,
        "generatedAt": _now(),
        "repository": validated["repository"],
        "discoveryDigest": validated["discoveryDigest"],
        "candidateSetDigest": validated["candidateSetDigest"],
        "selectionKind": selection_kind,
        "selectionDigest": selection_digest,
        "selectionCaseCount": len(linked),
        "linkagePolicy": DISCOVERY_LINK_POLICY,
        "selectedCandidates": linked,
        "selectedCandidateDigest": sha256_json(linked),
    }
    linkage["linkageDigest"] = sha256_json(linkage)
    validate_discovery_selection_linkage(
        linkage,
        discovery=validated,
        selection=selection,
    )
    return linkage


def link_discovery_selection(
    *,
    discovery_path: Path,
    selection_path: Path,
    output: Path,
) -> dict[str, Any]:
    """Create the standalone linkage artifact used by the CLI workflow."""

    linkage = build_discovery_selection_linkage(
        read_json(discovery_path),
        selection=read_json(selection_path),
    )
    write_json(output, linkage)
    return linkage


def validate_discovery_selection_linkage(
    value: Any,
    *,
    discovery: Any,
    selection: Any,
) -> dict[str, Any]:
    """Verify a selection remains an ordered subset of the sealed candidate pool."""

    validated = validate_discovery(discovery)
    if not isinstance(value, Mapping) or set(value) != DISCOVERY_LINK_FIELDS:
        raise ValueError("discovery-selection linkage fields are invalid")
    if value.get("kind") != DISCOVERY_SELECTION_LINK_KIND:
        raise ValueError("discovery-selection linkage kind is invalid")
    digest_value = dict(value)
    declared_digest = digest_value.pop("linkageDigest", None)
    if declared_digest != sha256_json(digest_value):
        raise ValueError("discovery-selection linkage digest mismatch")
    _validate_discovery_timestamp(value.get("generatedAt"))
    selection_kind, selection_digest, expected = _linked_selection_candidates(
        discovery=validated,
        selection=selection,
    )
    linked = value.get("selectedCandidates")
    if (
        value.get("repository") != validated["repository"]
        or value.get("discoveryDigest") != validated["discoveryDigest"]
        or value.get("candidateSetDigest") != validated["candidateSetDigest"]
        or value.get("selectionKind") != selection_kind
        or value.get("selectionDigest") != selection_digest
        or value.get("selectionCaseCount") != len(expected)
        or value.get("linkagePolicy") != DISCOVERY_LINK_POLICY
        or not isinstance(linked, list)
        or any(
            not isinstance(item, Mapping)
            or set(item) != DISCOVERY_LINK_CASE_FIELDS
            for item in linked
        )
        or linked != expected
        or value.get("selectedCandidateDigest") != sha256_json(expected)
    ):
        raise ValueError("discovery-selection linkage drift")
    return dict(value)


def _offline_git_environment() -> dict[str, str]:
    return hermetic_git_environment(offline=True)


def _is_working_git_repository(
    path: Path,
    *,
    git_env: Mapping[str, str] | None,
) -> bool:
    if not path.is_dir():
        return False
    try:
        return (
            run(
                [
                    "git",
                    "-C",
                    str(path),
                    "rev-parse",
                    "--is-inside-work-tree",
                ],
                env=git_env,
            ).strip()
            == "true"
        )
    except RuntimeError:
        return False


def _ensure_git_repository(
    path: Path,
    repository: str,
    *,
    offline: bool = False,
    git_env: Mapping[str, str] | None = None,
) -> None:
    remote = f"https://github.com/{repository}.git"
    if _is_working_git_repository(path, git_env=git_env):
        validate_git_evidence_repository(path)
        if not offline:
            run(
                [
                    "git",
                    "-C",
                    str(path),
                    "remote",
                    "set-url",
                    "origin",
                    remote,
                ],
                env=git_env,
            )
        return
    if offline:
        raise ValueError(
            "offline materialization requires an existing local Git clone "
            f"or linked worktree at {path}; cloning is disabled"
        )
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"repository path is non-empty and is not Git: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "git",
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            remote,
            str(path),
        ],
        env=git_env,
    )
    validate_git_evidence_repository(path)


def _fetch_case(
    repository: Path,
    number: int,
    revisions: Iterable[str],
    *,
    offline: bool = False,
    git_env: Mapping[str, str] | None = None,
) -> None:
    required_revisions = [
        require_full_sha(revision, f"PR {number} required revision")
        for revision in revisions
        if revision
    ]
    if offline:
        missing = []
        for revision in required_revisions:
            try:
                run(
                    [
                        "git",
                        "-C",
                        str(repository),
                        "cat-file",
                        "-e",
                        f"{revision}^{{commit}}",
                    ],
                    env=git_env,
                )
            except RuntimeError:
                missing.append(revision)
        if missing:
            raise ValueError(
                f"offline materialization cannot resolve PR {number}; "
                "required local Git commit objects are missing: "
                + ", ".join(missing)
            )
        return
    run(
        [
            "git",
            "-C",
            str(repository),
            "fetch",
            "--filter=blob:none",
            "--no-tags",
            "origin",
            f"pull/{number}/head:refs/remotes/origin/benchmark-pr/{number}",
        ],
        env=git_env,
    )
    for revision in required_revisions:
        try:
            run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "cat-file",
                    "-e",
                    f"{revision}^{{commit}}",
                ],
                env=git_env,
            )
        except RuntimeError:
            run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "fetch",
                    "--filter=blob:none",
                    "--no-tags",
                    "origin",
                    revision,
                ],
                env=git_env,
            )


def _parents(
    repository: Path,
    revision: str,
    *,
    git_env: Mapping[str, str] | None = None,
) -> list[str]:
    line = run(
        ["git", "-C", str(repository), "rev-list", "--parents", "-n", "1", revision],
        env=git_env,
    ).strip()
    return line.split()[1:]


def _prove_ancestor(
    repository: Path,
    ancestor: str,
    descendant: str,
    *,
    git_env: Mapping[str, str] | None = None,
) -> None:
    run(
        [
            "git",
            "-C",
            str(repository),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        env=git_env,
    )


def _ancestry_evidence(
    repository: Path,
    *,
    base_sha: str,
    reviewed_head_sha: str,
    final_head_sha: str,
    merge_commit_sha: str,
    mainline_cutoff_sha: str,
    git_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    parents = _parents(
        repository,
        merge_commit_sha,
        git_env=git_env,
    )
    if len(parents) != 2 or parents[1] != final_head_sha:
        raise ValueError(
            "the final PR head must be the second parent of its two-parent "
            "mainline merge"
        )
    mainline_parent = parents[0]
    _prove_ancestor(
        repository,
        base_sha,
        reviewed_head_sha,
        git_env=git_env,
    )
    _prove_ancestor(
        repository,
        reviewed_head_sha,
        final_head_sha,
        git_env=git_env,
    )
    merge_base = run(
        [
            "git",
            "-C",
            str(repository),
            "merge-base",
            reviewed_head_sha,
            mainline_parent,
        ],
        env=git_env,
    ).strip()
    if merge_base != base_sha:
        raise ValueError(
            "the frozen base is not the review-head/mainline-parent merge base"
        )
    _prove_ancestor(
        repository,
        merge_commit_sha,
        mainline_cutoff_sha,
        git_env=git_env,
    )
    evidence = {
        "schema": "codecrow.magento2-review-ancestry",
        "baseSha": base_sha,
        "reviewedHeadSha": reviewed_head_sha,
        "finalHeadSha": final_head_sha,
        "mergeCommitSha": merge_commit_sha,
        "mergeParents": parents,
        "mergeFirstParentSha": mainline_parent,
        "mergeSecondParentSha": final_head_sha,
        "mainlineCutoffSha": mainline_cutoff_sha,
        "reviewedMainlineMergeBaseSha": merge_base,
        "checks": {
            "baseAncestorReviewedHead": True,
            "reviewedHeadAncestorFinalHead": True,
            "finalHeadIsMergeSecondParent": True,
            "baseIsReviewedMainlineMergeBase": True,
            "mergeCommitAncestorMainlineCutoff": True,
        },
    }
    evidence["evidenceDigest"] = sha256_json(evidence)
    return evidence


def _derive_base(
    repository: Path,
    *,
    head_sha: str,
    merge_sha: str,
    recorded_base: str | None,
    git_env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    if recorded_base:
        recorded = require_full_sha(recorded_base, "selection.baseSha")
        run(
            [
                "git",
                "-C",
                str(repository),
                "merge-base",
                "--is-ancestor",
                recorded,
                head_sha,
            ],
            env=git_env,
        )
        parents = _parents(repository, merge_sha, git_env=git_env)
        if len(parents) >= 2:
            derived = run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "merge-base",
                    head_sha,
                    parents[0],
                ],
                env=git_env,
            ).strip()
            if derived != recorded:
                raise ValueError(
                    "recorded pull base does not match the review head/mainline "
                    "merge-base"
                )
        return recorded, "recorded_pull_base"
    parents = _parents(repository, merge_sha, git_env=git_env)
    if len(parents) < 2:
        raise ValueError(
            "cannot derive the historical PR base from a non-merge commit; "
            "record baseSha in the selection"
        )
    base = run(
        ["git", "-C", str(repository), "merge-base", head_sha, parents[0]],
        env=git_env,
    ).strip()
    return (
        require_full_sha(base, "derived base SHA"),
        "merge_base_with_merge_first_parent",
    )


def _snapshot_diff(
    repository: Path,
    base: str,
    head: str,
    *,
    git_env: Mapping[str, str] | None = None,
) -> tuple[str, list[str]]:
    try:
        diff = run(
            deterministic_git_diff_command(
                repository,
                "--full-index",
                base,
                head,
            ),
            env=git_env,
        )
        raw_paths = run(
            deterministic_git_diff_command(
                repository,
                "--name-only",
                "-z",
                base,
                head,
            ),
            env=git_env,
        )
    except RuntimeError as exc:
        if git_env is None:
            raise
        raise ValueError(
            "offline materialization cannot resolve the exact local Git "
            f"objects for snapshot {base}..{head}"
        ) from exc
    paths = sorted({path for path in raw_paths.split("\0") if path})
    return diff, paths


def _right_line_kinds(diff: str) -> dict[int, str]:
    lines: dict[int, str] = {}
    current: int | None = None
    for line in diff.splitlines():
        if line.startswith("@@"):
            match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            current = int(match.group(1)) if match else None
            continue
        if current is None or line.startswith("\\ No newline"):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            lines[current] = "added"
            current += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        else:
            lines[current] = "context"
            current += 1
    return lines


def _path_diff(
    repository: Path,
    base: str,
    head: str,
    path: str,
    *,
    git_env: Mapping[str, str] | None = None,
) -> str:
    try:
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
    except RuntimeError as exc:
        if git_env is None:
            raise
        raise ValueError(
            "offline materialization cannot resolve the exact local Git "
            f"objects for {path} at {base}..{head}"
        ) from exc


def _annotation(
    selected: Mapping[str, Any],
    comment_id: int,
) -> Mapping[str, Any]:
    annotations = selected.get("expectedIssues")
    if not isinstance(annotations, Mapping):
        raise ValueError("selection.expectedIssues must be an object")
    value = annotations.get(str(comment_id))
    if not isinstance(value, Mapping):
        raise ValueError(f"selection has no expected issue for comment {comment_id}")
    return value


def _sha256_digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _source_object_digest_map(
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
        result[identifier] = _sha256_digest(digest, f"{field}.{key}")
    return result


def _validated_source_archive_evidence(
    selected: Mapping[str, Any],
    *,
    source_archive_digest: Any,
    pull: Mapping[str, Any],
    comments_by_id: Mapping[int, Mapping[str, Any]],
    reviews_by_id: Mapping[int, Mapping[str, Any]],
    paper_ready: bool,
) -> dict[str, Any] | None:
    value = selected.get("sourceArchiveEvidence")
    if value is None and not paper_ready:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(
            f"PR {selected.get('pullRequest')} has no source archive case evidence"
        )
    expected_fields = {
        "archiveDigest",
        "caseEvidenceDigest",
        "pullResponseSha256",
        "selectedCommentResponseSha256",
        "submittedReviewResponseSha256",
    }
    if set(value) != expected_fields:
        raise ValueError(
            f"PR {selected.get('pullRequest')} source archive evidence fields drifted"
        )
    archive_digest = _sha256_digest(
        value.get("archiveDigest"),
        "sourceArchiveEvidence.archiveDigest",
    )
    if archive_digest != source_archive_digest:
        raise ValueError(
            f"PR {selected.get('pullRequest')} source archive digest drift"
        )
    _sha256_digest(
        value.get("caseEvidenceDigest"),
        "sourceArchiveEvidence.caseEvidenceDigest",
    )
    pull_digest = _sha256_digest(
        value.get("pullResponseSha256"),
        "sourceArchiveEvidence.pullResponseSha256",
    )
    if pull_digest != sha256_json(pull):
        raise ValueError(
            f"PR {selected.get('pullRequest')} pull response drifted from "
            "the source archive"
        )
    comment_digests = _source_object_digest_map(
        value.get("selectedCommentResponseSha256"),
        field="sourceArchiveEvidence.selectedCommentResponseSha256",
    )
    review_digests = _source_object_digest_map(
        value.get("submittedReviewResponseSha256"),
        field="sourceArchiveEvidence.submittedReviewResponseSha256",
    )
    for comment_id, expected in comment_digests.items():
        comment = comments_by_id.get(comment_id)
        if comment is None or sha256_json(comment) != expected:
            raise ValueError(
                f"PR {selected.get('pullRequest')} comment {comment_id} "
                "drifted from the source archive"
            )
    for review_id, expected in review_digests.items():
        review = reviews_by_id.get(review_id)
        if review is None or sha256_json(review) != expected:
            raise ValueError(
                f"PR {selected.get('pullRequest')} review {review_id} "
                "drifted from the source archive"
            )
    return {
        "archiveDigest": archive_digest,
        "caseEvidenceDigest": value["caseEvidenceDigest"],
        "pullResponseSha256": pull_digest,
        "selectedCommentResponseSha256": {
            str(identifier): digest
            for identifier, digest in sorted(comment_digests.items())
        },
        "submittedReviewResponseSha256": {
            str(identifier): digest
            for identifier, digest in sorted(review_digests.items())
        },
    }


def _validated_curation_path_transition(
    repository: Path,
    *,
    checkpoint_sha: str,
    final_sha: str,
    source_path: str,
    source_evidence: Mapping[str, Any],
    annotation: Mapping[str, Any],
    paper_ready: bool,
    git_env: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    declared = source_evidence.get("pathTransition")
    if not isinstance(declared, Mapping):
        if paper_ready:
            raise ValueError(
                f"paper-ready comment on {source_path} has no bound "
                "checkpoint-to-final path transition"
            )
        return None
    declared_transition = validate_path_transition_evidence(
        declared,
        source_path=source_path,
        diff_sha256=declared.get("diffSha256"),
    )
    try:
        observed_transition, _ = resolve_path_transition(
            repository,
            checkpoint_sha=checkpoint_sha,
            final_sha=final_sha,
            source_path=source_path,
            git_env=git_env,
        )
    except RuntimeError as exc:
        if git_env is None:
            raise
        raise ValueError(
            "offline materialization cannot resolve the exact local Git "
            f"objects for {source_path} at {checkpoint_sha}..{final_sha}"
        ) from exc
    if declared_transition != observed_transition:
        raise ValueError(
            f"curation path transition for {source_path} does not match "
            "the deterministic checkpoint-to-final Git diff"
        )
    fix_evidence = annotation.get("fixEvidence")
    if not isinstance(fix_evidence, list) or not any(
        isinstance(item, Mapping)
        and item.get("kind") == "code_change"
        and item.get("artifactDigest")
        == observed_transition["diffSha256"]
        for item in fix_evidence
    ):
        raise ValueError(
            f"curation code-change evidence for {source_path} is not bound "
            "to the deterministic checkpoint-to-final Git diff"
        )
    return observed_transition


def _validated_review_thread_evidence(
    *,
    pull_request: int,
    comment_id: int,
    source_evidence: Mapping[str, Any],
    annotation: Mapping[str, Any],
    archive: Mapping[str, Any] | None,
    thread_evidence_digest: Any,
    paper_ready: bool,
    source_archive_evidence: Mapping[str, Any] | None = None,
    comments_by_id: Mapping[int, Mapping[str, Any]] | None = None,
    reviews_by_id: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    binding = source_evidence.get("reviewThreadEvidence")
    if binding is None and not paper_ready:
        return None
    if not isinstance(archive, Mapping):
        raise ValueError(
            f"PR {pull_request} has no raw GraphQL thread page archive"
        )
    validate_graphql_thread_archive(
        archive,
        pull_request=pull_request,
    )
    if not isinstance(binding, Mapping):
        raise ValueError(
            f"comment {comment_id} has no raw GraphQL thread binding"
        )
    evidence_digest = _sha256_digest(
        thread_evidence_digest,
        "selection.threadEvidenceDigest",
    )
    if paper_ready and not isinstance(source_archive_evidence, Mapping):
        raise ValueError(
            f"comment {comment_id} has no source archive case binding"
        )
    source_archive_digest = (
        source_archive_evidence.get("archiveDigest")
        if isinstance(source_archive_evidence, Mapping)
        else None
    )
    source_archive_case_digest = (
        source_archive_evidence.get("caseEvidenceDigest")
        if isinstance(source_archive_evidence, Mapping)
        else None
    )
    validated = validate_review_thread_binding(
        binding,
        archive=archive,
        root_comment_id=comment_id,
        thread_evidence_digest=evidence_digest,
        require_complete=paper_ready,
        source_archive_digest=(
            str(source_archive_digest)
            if source_archive_digest is not None
            else None
        ),
        source_archive_case_evidence_digest=(
            str(source_archive_case_digest)
            if source_archive_case_digest is not None
            else None
        ),
    )
    rest_evidence = validated.get("restThreadEvidence")
    if isinstance(rest_evidence, Mapping):
        if comments_by_id is None or reviews_by_id is None:
            if paper_ready:
                raise ValueError(
                    f"comment {comment_id} cannot be cross-checked against "
                    "the current REST cache"
                )
        else:
            raw_comments = rest_evidence.get("comments")
            raw_reviews = rest_evidence.get("submittedReviews")
            if not isinstance(raw_comments, list) or not isinstance(
                raw_reviews,
                list,
            ):
                raise ValueError(
                    f"comment {comment_id} raw REST thread is malformed"
                )
            expected_comment_ids = {
                identifier
                for identifier, comment in comments_by_id.items()
                if identifier == comment_id
                or comment.get("in_reply_to_id") == comment_id
            }
            archived_comment_ids = {
                int(comment["id"])
                for comment in raw_comments
                if isinstance(comment, Mapping)
            }
            if expected_comment_ids != archived_comment_ids:
                raise ValueError(
                    f"comment {comment_id} REST thread coverage drifted "
                    "after release"
                )
            for raw_comment in raw_comments:
                if not isinstance(raw_comment, Mapping):
                    raise ValueError(
                        f"comment {comment_id} raw REST thread is malformed"
                    )
                identifier = int(raw_comment["id"])
                if comments_by_id.get(identifier) != raw_comment:
                    raise ValueError(
                        f"comment {identifier} raw REST response drifted "
                        "after release"
                    )
            for raw_review in raw_reviews:
                if not isinstance(raw_review, Mapping):
                    raise ValueError(
                        f"comment {comment_id} raw submitted review is "
                        "malformed"
                    )
                review_id = int(raw_review["id"])
                if reviews_by_id.get(review_id) != raw_review:
                    raise ValueError(
                        f"review {review_id} raw REST response drifted "
                        "after release"
                    )
    adjudication = annotation.get("adjudication")
    if not isinstance(adjudication, Mapping):
        raise ValueError(
            f"comment {comment_id} has no released adjudication"
        )
    if (
        adjudication.get("threadEvidenceDigest") != evidence_digest
        or adjudication.get("threadDigest") != validated["threadDigest"]
    ):
        raise ValueError(
            f"comment {comment_id} adjudication is not bound to its raw "
            "GraphQL thread evidence"
        )
    return validated


def _golden_comment(
    *,
    repository: Path,
    base_sha: str,
    head_sha: str,
    diff_sha256: str,
    changed_paths: list[str],
    comment: Mapping[str, Any],
    review: Mapping[str, Any],
    annotation: Mapping[str, Any],
    case_id: str,
    path_transition: Mapping[str, Any] | None = None,
    review_thread_evidence: Mapping[str, Any] | None = None,
    git_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    comment_id = int(comment["id"])
    raw_anchor = rest_review_comment_anchor(
        comment,
        field=f"comment {comment_id}",
    )
    path = require_text(comment.get("path"), f"comment {comment_id}.path")
    if path not in changed_paths:
        raise ValueError(f"comment {comment_id} is outside the frozen diff")
    source_line = raw_anchor["originalLine"]
    line = annotation.get("originalLineOverride", source_line)
    resolution = annotation.get("originalLineResolution")
    if line != source_line:
        if (
            isinstance(line, bool)
            or not isinstance(line, int)
            or line < 1
            or abs(line - source_line) > 1
            or not isinstance(resolution, Mapping)
            or resolution.get("method")
            != "raw_original_position_hunk_terminal_with_adjacent_checkpoint_line"
        ):
            raise ValueError(
                f"comment {comment_id} has an invalid original-line override"
            )
    path_diff = _path_diff(
        repository,
        base_sha,
        head_sha,
        path,
        git_env=git_env,
    )
    right_lines = _right_line_kinds(path_diff)
    line_kind = right_lines.get(line)
    if line_kind not in {"added", "context"}:
        raise ValueError(
            f"comment {comment_id} line {line} is not on the RIGHT side of "
            "the frozen snapshot diff"
        )
    body = require_text(comment.get("body"), f"comment {comment_id}.body")
    expected = {
        "summary": require_text(
            annotation.get("summary"),
            f"comment {comment_id}.expectedIssue.summary",
        ),
        "rootCause": str(annotation.get("rootCause") or ""),
        "failureMode": str(annotation.get("failureMode") or ""),
        "requiredChange": str(annotation.get("requiredChange") or ""),
        "category": annotation.get("category"),
        "severity": annotation.get("severity"),
        "actionable": True,
        "atomic": bool(annotation.get("atomic", False)),
    }
    fix_sha = annotation.get("fixCommitSha")
    fixed_later = bool(fix_sha)
    fix_evidence = annotation.get("fixEvidence")
    if not isinstance(fix_evidence, list):
        fix_evidence = []
    if fix_sha:
        require_full_sha(fix_sha, f"comment {comment_id}.fixCommitSha")
        run(
            [
                "git",
                "-C",
                str(repository),
                "merge-base",
                "--is-ancestor",
                head_sha,
                fix_sha,
            ],
            env=git_env,
        )
        fix_diff = _path_diff(
            repository,
            head_sha,
            fix_sha,
            path,
            git_env=git_env,
        )
        if not fix_diff:
            raise ValueError(
                f"comment {comment_id} fix commit does not change {path}"
            )

    adjudication = annotation.get("adjudication")
    if not isinstance(adjudication, Mapping):
        adjudication = {}
    normalized_adjudication = {
        "status": adjudication.get("status") or "provisional",
        "annotators": list(adjudication.get("annotators") or []),
        "records": list(adjudication.get("records") or []),
        "adjudicator": adjudication.get("adjudicator") or "unassigned",
        "at": adjudication.get("at") or _now(),
        "notes": adjudication.get("notes") or "",
        "threadComplete": adjudication.get("threadComplete") is True,
        "threadDisposition": adjudication.get("threadDisposition"),
        "threadEvidenceDigest": adjudication.get("threadEvidenceDigest"),
        "threadDigest": adjudication.get("threadDigest"),
        "curationPacketDigest": adjudication.get("curationPacketDigest"),
    }
    decision_digest = decision_binding_digest(
        expected_issue=expected,
        fix_commit_sha=fix_sha,
        fix_evidence=fix_evidence,
        adjudication=normalized_adjudication,
    )
    declared_decision_digest = adjudication.get("decisionDigest")
    if (
        declared_decision_digest is not None
        and declared_decision_digest != decision_digest
    ):
        raise ValueError(
            f"comment {comment_id} adjudication decision digest drift"
        )
    normalized_adjudication["decisionDigest"] = decision_digest
    return {
        "id": f"{case_id}-comment-{comment_id}",
        "sourceCommentId": comment_id,
        "sourceUrl": require_text(
            comment.get("html_url"),
            f"comment {comment_id}.html_url",
        ),
        "reviewId": int(comment["pull_request_review_id"]),
        "reviewer": str(comment["user"]["login"]),
        "reviewerType": str(comment["user"]["type"]),
        "reviewerAssociation": str(comment.get("author_association") or "UNKNOWN"),
        "createdAt": str(comment["created_at"]),
        "path": path,
        "side": raw_anchor["side"],
        "originalLine": line,
        "sourceCurrentCommitId": raw_anchor["currentCommitId"],
        "sourceCurrentLine": raw_anchor["currentLine"],
        "sourceOriginalLine": source_line,
        "sourceCurrentStartLine": raw_anchor["currentStartLine"],
        "sourceOriginalStartLine": raw_anchor["originalStartLine"],
        "sourceStartSide": raw_anchor["startSide"],
        "originalLineResolution": dict(resolution) if resolution else None,
        "originalCommitId": raw_anchor["originalCommitId"],
        "body": body,
        "bodySha256": sha256_text(body),
        "diffHunk": require_text(
            comment.get("diff_hunk"),
            f"comment {comment_id}.diff_hunk",
        ),
        "threadRoot": True,
        "expectedIssue": expected,
        "validity": {
            "status": "present_at_snapshot",
            "snapshotSha": head_sha,
            "method": (
                "exact_original_commit_position_resolved_anchor"
                if line != source_line
                else (
                    "exact_original_commit_and_diff_context_anchor"
                    if line_kind == "context"
                    else "exact_original_commit_and_diff_anchor"
                )
            ),
            "anchorValidation": {
                "status": "exact",
                "path": path,
                "line": line,
                "lineKind": line_kind,
                "diffSha256": diff_sha256,
            },
            "fixedLater": fixed_later if fix_sha else None,
            "fixCommitSha": fix_sha,
            "disposition": "fixed" if fix_sha else "unresolved",
            "fixEvidence": fix_evidence,
            **(
                {"pathTransition": dict(path_transition)}
                if isinstance(path_transition, Mapping)
                else {}
            ),
        },
        "adjudication": normalized_adjudication,
        "reviewState": review.get("state"),
        "reviewSubmittedAt": review.get("submitted_at"),
        "sourceUpdatedAt": comment.get("updated_at"),
        "sourceApiResponse": dict(comment),
        "sourceApiResponseSha256": sha256_json(comment),
        "sourceReviewResponse": dict(review),
        "sourceReviewResponseSha256": sha256_json(review),
        **(
            {"reviewThreadEvidence": dict(review_thread_evidence)}
            if isinstance(review_thread_evidence, Mapping)
            else {}
        ),
    }


def materialize(
    client: GitHubClient,
    *,
    selection_path: Path,
    repository_path: Path,
    output: Path,
    repository: str = "magento/magento2",
    default_branch: str = "2.4-develop",
    required_cases: int = 50,
) -> dict[str, Any]:
    selection = read_json(selection_path)
    if not isinstance(selection, Mapping) or selection.get("kind") != SELECTION_KIND:
        raise ValueError(f"{selection_path} is not a Magento selection manifest")
    digest_payload = dict(selection)
    declared_selection_digest = digest_payload.pop("selectionDigest", None)
    if declared_selection_digest != sha256_json(digest_payload):
        raise ValueError("selectionDigest is missing or invalid")
    selected_cases = selection.get("cases")
    if not isinstance(selected_cases, list) or len(selected_cases) != required_cases:
        raise ValueError(
            f"selection must contain exactly {required_cases} cases"
        )
    offline = client.offline
    git_env = hermetic_git_environment(offline=offline)
    _ensure_git_repository(
        repository_path,
        repository,
        offline=offline,
        git_env=git_env,
    )
    paper_ready_requested = selection.get("paperReadyRequested") is True
    raw_mainline_cutoff = selection.get("mainlineCutoffSha")
    mainline_cutoff = (
        require_full_sha(
            raw_mainline_cutoff,
            "selection.mainlineCutoffSha",
        )
        if raw_mainline_cutoff is not None or paper_ready_requested
        else None
    )

    cases = []
    owner, name = _repo_parts(repository)
    for selected in selected_cases:
        if not isinstance(selected, Mapping):
            raise ValueError("selection cases must be objects")
        number = int(selected["pullRequest"])
        head_sha = require_full_sha(selected.get("headSha"), "selection.headSha")
        pull = client.get(f"/repos/{owner}/{name}/pulls/{number}")
        if not isinstance(pull, Mapping):
            raise ValueError(f"GitHub returned invalid PR {number}")
        if not pull.get("merged_at") or pull.get("state") != "closed":
            raise ValueError(f"PR {number} is not merged")
        if (pull.get("base") or {}).get("ref") != default_branch:
            raise ValueError(f"PR {number} was not merged to {default_branch}")
        merge_sha = require_full_sha(
            pull.get("merge_commit_sha"),
            f"PR {number}.merge_commit_sha",
        )
        final_head = require_full_sha(
            (pull.get("head") or {}).get("sha"),
            f"PR {number}.head.sha",
        )
        annotation_fix_shas = []
        expected_issues = selected.get("expectedIssues")
        if isinstance(expected_issues, Mapping):
            annotation_fix_shas = [
                str(item["fixCommitSha"])
                for item in expected_issues.values()
                if isinstance(item, Mapping) and item.get("fixCommitSha")
            ]
        recorded_base = (
            str(selected["baseSha"]) if selected.get("baseSha") else None
        )
        _fetch_case(
            repository_path,
            number,
            [
                head_sha,
                merge_sha,
                final_head,
                *([recorded_base] if recorded_base else []),
                *annotation_fix_shas,
                *([mainline_cutoff] if mainline_cutoff else []),
            ],
            offline=offline,
            git_env=git_env,
        )
        merge_parents = _parents(
            repository_path,
            merge_sha,
            git_env=git_env,
        )
        if len(merge_parents) != 2 or merge_parents[1] != final_head:
            raise ValueError(
                f"PR {number} final head is not the second parent of its "
                "two-parent mainline merge; "
                "this collector requires reconstructable merge parentage"
            )
        for fix_sha in annotation_fix_shas:
            require_full_sha(fix_sha, f"PR {number}.fixCommitSha")
            run(
                [
                    "git",
                    "-C",
                    str(repository_path),
                    "merge-base",
                    "--is-ancestor",
                    head_sha,
                    fix_sha,
                ],
                env=git_env,
            )
            run(
                [
                    "git",
                    "-C",
                    str(repository_path),
                    "merge-base",
                    "--is-ancestor",
                    fix_sha,
                    final_head,
                ],
                env=git_env,
            )
        base_sha, derivation = _derive_base(
            repository_path,
            head_sha=head_sha,
            merge_sha=merge_sha,
            recorded_base=recorded_base,
            git_env=git_env,
        )
        ancestry_evidence = (
            _ancestry_evidence(
                repository_path,
                base_sha=base_sha,
                reviewed_head_sha=head_sha,
                final_head_sha=final_head,
                merge_commit_sha=merge_sha,
                mainline_cutoff_sha=mainline_cutoff,
                git_env=git_env,
            )
            if mainline_cutoff is not None
            else None
        )
        diff, changed_paths = _snapshot_diff(
            repository_path,
            base_sha,
            head_sha,
            git_env=git_env,
        )
        diff_digest = sha256_text(diff)

        all_comments = list(
            client.paginate(f"/repos/{owner}/{name}/pulls/{number}/comments")
        )
        all_reviews = list(
            client.paginate(f"/repos/{owner}/{name}/pulls/{number}/reviews")
        )
        comments_by_id = {
            int(item["id"]): item
            for item in all_comments
            if isinstance(item, Mapping) and item.get("id")
        }
        reviews_by_id = {
            int(item["id"]): item
            for item in all_reviews
            if isinstance(item, Mapping) and item.get("id")
        }
        source_archive_evidence = _validated_source_archive_evidence(
            selected,
            source_archive_digest=selection.get("sourceArchiveDigest"),
            pull=pull,
            comments_by_id=comments_by_id,
            reviews_by_id=reviews_by_id,
            paper_ready=paper_ready_requested,
        )
        selected_comment_ids = selected.get("commentIds")
        if not isinstance(selected_comment_ids, list) or not selected_comment_ids:
            raise ValueError(f"PR {number} has no selected comments")
        source_comment_evidence = selected.get("sourceCommentEvidence")
        if not isinstance(source_comment_evidence, Mapping):
            raise ValueError(
                f"PR {number} selection has no bound source-comment evidence"
            )
        raw_graphql_archive = selected.get("graphqlThreadArchive")
        if raw_graphql_archive is not None:
            if not isinstance(raw_graphql_archive, Mapping):
                raise ValueError(
                    f"PR {number} GraphQL thread archive is malformed"
                )
            validate_graphql_thread_archive(
                raw_graphql_archive,
                pull_request=number,
            )
        elif paper_ready_requested:
            raise ValueError(
                f"paper-ready PR {number} has no raw GraphQL thread "
                "page archive"
            )
        case_id = str(selected.get("caseId") or f"magento2-pr-{number}")
        golden = []
        for raw_id in selected_comment_ids:
            comment_id = int(raw_id)
            comment = comments_by_id.get(comment_id)
            if comment is None:
                raise ValueError(f"PR {number} comment {comment_id} was not found")
            if not _root_human_right_comment(comment):
                raise ValueError(
                    f"PR {number} comment {comment_id} is not a root human review comment"
                )
            if str(comment["original_commit_id"]) != head_sha:
                raise ValueError(
                    f"PR {number} comment {comment_id} targets a different head"
                )
            source_evidence = source_comment_evidence.get(str(comment_id))
            user = comment.get("user")
            if not isinstance(source_evidence, Mapping) or any(
                (
                    source_evidence.get("bodySha256")
                    != sha256_text(str(comment.get("body") or "")),
                    source_evidence.get("updatedAt") != comment.get("updated_at"),
                    source_evidence.get("reviewer")
                    != (
                        user.get("login") if isinstance(user, Mapping) else None
                    ),
                    source_evidence.get("originalCommitId")
                    != comment.get("original_commit_id"),
                    source_archive_evidence is not None
                    and source_evidence.get("reviewId")
                    != comment.get("pull_request_review_id"),
                    source_archive_evidence is not None
                    and source_evidence.get("sourceApiResponseSha256")
                    != sha256_json(comment),
                )
            ):
                raise ValueError(
                    f"PR {number} comment {comment_id} drifted after curation"
                )
            if str(comment["user"]["login"]).casefold() == str(
                (pull.get("user") or {}).get("login")
            ).casefold():
                raise ValueError(
                    f"PR {number} comment {comment_id} is by the PR author"
                )
            review_id = int(comment["pull_request_review_id"])
            review = reviews_by_id.get(review_id)
            if review is None:
                raise ValueError(
                    f"PR {number} comment {comment_id} has no submitted review"
                )
            if source_archive_evidence is not None and (
                source_evidence.get("sourceReviewResponseSha256")
                != sha256_json(review)
            ):
                raise ValueError(
                    f"PR {number} review {review_id} drifted after curation"
                )
            annotation = _annotation(selected, comment_id)
            path_transition = _validated_curation_path_transition(
                repository_path,
                checkpoint_sha=head_sha,
                final_sha=final_head,
                source_path=str(comment["path"]),
                source_evidence=source_evidence,
                annotation=annotation,
                paper_ready=paper_ready_requested,
                git_env=git_env,
            )
            review_thread_evidence = _validated_review_thread_evidence(
                pull_request=number,
                comment_id=comment_id,
                source_evidence=source_evidence,
                annotation=annotation,
                archive=(
                    raw_graphql_archive
                    if isinstance(raw_graphql_archive, Mapping)
                    else None
                ),
                thread_evidence_digest=selection.get(
                    "threadEvidenceDigest"
                ),
                paper_ready=paper_ready_requested,
                source_archive_evidence=source_archive_evidence,
                comments_by_id=comments_by_id,
                reviews_by_id=reviews_by_id,
            )
            golden.append(
                _golden_comment(
                    repository=repository_path,
                    base_sha=base_sha,
                    head_sha=head_sha,
                    diff_sha256=diff_digest,
                    changed_paths=changed_paths,
                    comment=comment,
                    review=review,
                    annotation=annotation,
                    case_id=case_id,
                    path_transition=path_transition,
                    review_thread_evidence=review_thread_evidence,
                    git_env=git_env,
                )
            )
        reviewed_at = max(comment["createdAt"] for comment in golden)
        replay_prefix = f"benchmark/magento2/{case_id}"
        file_count = len(changed_paths)
        if 3 <= file_count <= 10:
            size_band = "small"
        elif 11 <= file_count <= 30:
            size_band = "medium"
        elif 31 <= file_count <= 80:
            size_band = "large"
        else:
            raise ValueError(
                f"PR {number} snapshot has {file_count} files; expected 3..80"
            )
        cases.append(
            {
                "caseId": case_id,
                "partition": selected.get("partition") or "development",
                "sizeBand": size_band,
                "sourcePr": {
                    "number": number,
                    "url": f"https://github.com/{repository}/pull/{number}",
                    "title": str(pull.get("title") or f"PR {number}"),
                    "author": str((pull.get("user") or {}).get("login") or "unknown"),
                    "baseRef": default_branch,
                    "mergedAt": str(pull["merged_at"]),
                    "finalHeadSha": final_head,
                    "mergeCommitSha": merge_sha,
                    "changedFiles": int(pull.get("changed_files") or 0),
                    "sourceApiResponse": dict(pull),
                    "sourceApiResponseSha256": sha256_json(pull),
                },
                "snapshot": {
                    "baseSha": base_sha,
                    "headSha": head_sha,
                    "reviewedAt": reviewed_at,
                    "fileCount": file_count,
                    "changedPaths": changed_paths,
                    "diffSha256": diff_digest,
                    "derivation": derivation,
                },
                "goldenComments": golden,
                **(
                    {"sourceArchiveEvidence": source_archive_evidence}
                    if source_archive_evidence is not None
                    else {}
                ),
                **(
                    {"ancestryEvidence": ancestry_evidence}
                    if ancestry_evidence is not None
                    else {}
                ),
                **(
                    {"graphqlThreadArchive": dict(raw_graphql_archive)}
                    if isinstance(raw_graphql_archive, Mapping)
                    else {}
                ),
                "replay": {
                    "baseRef": f"{replay_prefix}/base",
                    "headRef": f"{replay_prefix}/head",
                    "forkPrNumber": None,
                    "forkPrUrl": None,
                },
            }
        )

    corpus = {
        "kind": "codecrow-magento2-review-corpus",
        "corpusId": str(
            selection.get("corpusId") or "magento2-core-review-50"
        ),
        "generatedAt": _now(),
        "repository": repository,
        "defaultBranch": default_branch,
        "selectionPolicy": {
            "requiredCases": required_cases,
            "mergedOnly": True,
            "reviewCommentsRequired": True,
            "snapshotRule": "exact_original_review_commit",
            "bands": {
                "small": {"minFiles": 3, "maxFiles": 10},
                "medium": {"minFiles": 11, "maxFiles": 30},
                "large": {"minFiles": 31, "maxFiles": 80},
            },
            "requiredBands": selection.get("requiredBands")
            or {"small": 1, "medium": 1, "large": 1},
            "selectionSeed": selection.get("selectionSeed"),
            "partitionPolicy": selection.get("partitionPolicy"),
            "mainlineCutoffSha": selection.get("mainlineCutoffSha"),
        },
        "provenance": {
            "collector": "magento2_benchmark.collect",
            "collectedAt": _now(),
            "githubApiVersion": GITHUB_API_VERSION,
            "gitVersion": run(["git", "--version"]).strip(),
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
            "sourceArchiveDigest": selection.get("sourceArchiveDigest"),
            "threadEvidenceDigest": selection.get(
                "threadEvidenceDigest"
            ),
            "selectionDigest": declared_selection_digest,
            "selectionFileSha256": sha256_text(
                selection_path.read_text(encoding="utf-8")
            ),
        },
        "cases": sorted(cases, key=lambda case: case["caseId"]),
    }
    corpus = attach_corpus_digest(corpus)
    validate_corpus(
        corpus,
        required_cases=required_cases,
        paper_ready=selection.get("paperReadyRequested") is True,
    )
    write_json(output, corpus)
    return corpus
