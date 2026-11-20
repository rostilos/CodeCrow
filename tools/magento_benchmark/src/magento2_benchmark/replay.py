from __future__ import annotations

import math
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .corpus import validate_corpus
from .execution_corpus import (
    EXECUTION_CORPUS_KIND,
    assert_label_free_execution_value,
    build_execution_corpus,
    validate_execution_corpus,
)
from .github import GitHubClient
from .util import (
    read_json,
    require_full_sha,
    require_text,
    run,
    sha256_json,
    write_json,
)


PLAN_KIND = "codecrow-magento2-replay-plan"
LOCK_KIND = "codecrow-magento2-replay-lock"
ATTESTATION_KIND = "codecrow-magento2-replay-attestation"
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

PLAN_FIELDS = {
    "kind",
    "generatedAt",
    "forkRepository",
    "corpusId",
    "corpusDigest",
    "executionCorpusDigest",
    "cases",
    "planDigest",
}
PLAN_CASE_FIELDS = {
    "caseId",
    "baseRef",
    "baseSha",
    "headRef",
    "headSha",
    "title",
    "body",
    "head",
}
LOCK_FIELDS = {
    "kind",
    "generatedAt",
    "forkRepository",
    "corpusId",
    "corpusDigest",
    "executionCorpusDigest",
    "planDigest",
    "plan",
    "cases",
    "lockDigest",
}
LOCK_CASE_FIELDS = {
    "caseId",
    "baseRef",
    "baseSha",
    "headRef",
    "headSha",
    "forkPrNumber",
    "forkPrUrl",
}
ATTESTATION_FIELDS = {
    "kind",
    "collectedAt",
    "corpusId",
    "corpusDigest",
    "executionCorpusDigest",
    "replayLockDigest",
    "planDigest",
    "forkRepository",
    "repositoryObservation",
    "cases",
    "attestationDigest",
}
REPOSITORY_OBSERVATION_FIELDS = {
    "apiPath",
    "repositoryId",
    "nodeId",
    "fullName",
    "fork",
    "upstreamRepository",
}
ATTESTATION_CASE_FIELDS = {
    "caseId",
    "baseRef",
    "headRef",
    "pullRequest",
}
REF_OBSERVATION_FIELDS = {
    "apiPath",
    "name",
    "qualifiedName",
    "sha",
    "objectType",
    "objectApiUrl",
}
PULL_OBSERVATION_FIELDS = {
    "apiPath",
    "pullRequestId",
    "nodeId",
    "number",
    "htmlUrl",
    "state",
    "baseRepository",
    "baseRef",
    "baseSha",
    "headRepository",
    "headRef",
    "headSha",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _fork_parts(value: str) -> tuple[str, str]:
    parts = value.split("/", 1)
    if (
        len(parts) != 2
        or not all(parts)
        or any(
            re.fullmatch(r"[A-Za-z0-9_.-]+", part) is None
            for part in parts
        )
    ):
        raise ValueError("fork repository must use owner/name form")
    return parts[0], parts[1]


def _require_fields(
    value: Mapping[str, Any],
    expected: set[str],
    field: str,
) -> None:
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        unexpected = sorted(observed - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise ValueError(f"{field} fields are invalid ({'; '.join(details)})")


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256_HEX.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _utc_datetime(value: Any, field: str) -> datetime:
    text = require_text(value, field)
    if not text.endswith("Z"):
        raise ValueError(f"{field} must be a UTC ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be a UTC ISO-8601 timestamp") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field} must be a UTC ISO-8601 timestamp")
    return parsed


def _utc_timestamp(value: Any, field: str) -> str:
    text = require_text(value, field)
    _utc_datetime(text, field)
    return text


def validate_replay_attestation_freshness(
    attestation: Mapping[str, Any],
    *,
    reference_at: Any,
    max_age_seconds: Any,
) -> float:
    """Require a live observation made shortly before the frozen run began."""

    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(float(max_age_seconds))
        or max_age_seconds <= 0
    ):
        raise ValueError(
            "replay attestation max age must be a positive number of seconds"
        )
    collected = _utc_datetime(
        attestation.get("collectedAt"),
        "replay attestation collectedAt",
    )
    reference = _utc_datetime(reference_at, "replay freshness referenceAt")
    age = (reference - collected).total_seconds()
    if age < 0:
        raise ValueError(
            "replay attestation was collected after the frozen run began"
        )
    if age > float(max_age_seconds):
        raise ValueError(
            "replay attestation is stale for the frozen run start"
        )
    return age


def _ref_api_path(fork_repository: str, ref: str) -> str:
    encoded = urllib.parse.quote(ref, safe="")
    return f"/repos/{fork_repository}/git/ref/heads/{encoded}"


def _pull_api_path(fork_repository: str, number: int) -> str:
    return f"/repos/{fork_repository}/pulls/{number}"


def _expected_plan_case(
    case: Mapping[str, Any],
    *,
    index: int,
    owner: str,
) -> dict[str, Any]:
    opaque = case["caseId"]
    base_ref = case["replay"]["baseRef"]
    head_ref = case["replay"]["headRef"]
    title = f"Magento 2 review benchmark fixture {index:03d}"
    body = (
        "Immutable CodeCrow Magento 2 benchmark fixture.\n\n"
        f"Fixture: `{opaque}`\n"
        f"Base snapshot: `{case['snapshot']['baseSha']}`\n"
        f"Review snapshot: `{case['snapshot']['headSha']}`\n"
        f"Diff digest: `{case['snapshot']['diffSha256']}`\n\n"
        "Reviewer evidence is deliberately retained outside this pull request."
    )
    return {
        "caseId": opaque,
        "baseRef": base_ref,
        "baseSha": case["snapshot"]["baseSha"],
        "headRef": head_ref,
        "headSha": case["snapshot"]["headSha"],
        "title": title,
        "body": body,
        "head": f"{owner}:{head_ref}",
    }


def _execution_source(
    value: Mapping[str, Any],
    *,
    corpus_summary: Mapping[str, Any] | None = None,
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    """Return the label-free replay source and its validated summary.

    Primary pre-unseal commands pass the execution corpus directly. The
    released-corpus compatibility branch exists only for post-unseal consumers
    that already possess labels (metrics/package verification); it immediately
    projects away labels before replay comparison.
    """

    if value.get("kind") == EXECUTION_CORPUS_KIND:
        summary = validate_execution_corpus(value)
        return value, summary
    summary = corpus_summary or validate_corpus(value)
    execution = build_execution_corpus(value, require_paper_ready=False)
    execution_summary = validate_execution_corpus(execution)
    if (
        execution_summary["corpusId"] != summary["corpusId"]
        or execution_summary["corpusDigest"] != summary["corpusDigest"]
    ):
        raise ValueError("execution projection does not match released corpus")
    return execution, execution_summary


def _repository_from_remote(url: str) -> str | None:
    value = url.strip()
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urllib.parse.urlparse(value)
        if parsed.hostname != "github.com":
            return None
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    try:
        owner, repository = _fork_parts(path)
    except ValueError:
        return None
    return f"{owner}/{repository}"


def _validate_push_remote(
    *,
    source_repository: Path | None,
    git_remote: str | None,
    fork_repository: str,
) -> None:
    if (source_repository is None) != (git_remote is None):
        raise ValueError(
            "--source-repository and --git-remote must be supplied together"
        )
    if source_repository is None or git_remote is None:
        return
    if not (source_repository / ".git").exists():
        raise ValueError("--source-repository must be a local Git clone")
    remote_urls = [git_remote]
    try:
        resolved = run(
            [
                "git",
                "-C",
                str(source_repository),
                "remote",
                "get-url",
                "--push",
                "--all",
                git_remote,
            ]
        )
        remote_urls = [line.strip() for line in resolved.splitlines() if line.strip()]
    except RuntimeError:
        # `git push` also accepts a literal URL. It still has to name the
        # exact confirmed GitHub fork.
        pass
    if not remote_urls or any(
        (observed := _repository_from_remote(remote_url)) is None
        or observed.casefold() != fork_repository.casefold()
        for remote_url in remote_urls
    ):
        raise ValueError(
            "every effective --git-remote push URL must resolve to the exact "
            "confirmed GitHub fork"
        )


def build_plan(
    execution_corpus: Mapping[str, Any],
    *,
    fork_repository: str,
) -> dict[str, Any]:
    execution, summary = _execution_source(execution_corpus)
    owner, _ = _fork_parts(fork_repository)
    cases = [
        _expected_plan_case(case, index=index, owner=owner)
        for index, case in enumerate(execution["cases"], start=1)
    ]
    plan = {
        "kind": PLAN_KIND,
        "generatedAt": _now(),
        "forkRepository": fork_repository,
        "corpusId": summary["corpusId"],
        "corpusDigest": summary["corpusDigest"],
        "executionCorpusDigest": summary["executionCorpusDigest"],
        "cases": cases,
    }
    assert_label_free_execution_value(plan, context="primary H replay plan")
    plan["planDigest"] = sha256_json(plan)
    return plan


def create_plan(
    *,
    execution_corpus_path: Path,
    fork_repository: str,
    output: Path,
) -> dict[str, Any]:
    execution_corpus = read_json(execution_corpus_path)
    if (
        not isinstance(execution_corpus, Mapping)
        or execution_corpus.get("kind") != EXECUTION_CORPUS_KIND
    ):
        raise ValueError(
            "replay-plan requires a label-free analysis execution corpus"
        )
    plan = build_plan(execution_corpus, fork_repository=fork_repository)
    write_json(output, plan)
    return plan


def _validate_plan(plan: Any) -> Mapping[str, Any]:
    if not isinstance(plan, Mapping) or plan.get("kind") != PLAN_KIND:
        raise ValueError("replay plan kind is invalid")
    assert_label_free_execution_value(plan, context="primary H replay plan")
    _require_fields(plan, PLAN_FIELDS, "replay plan")
    digest_payload = dict(plan)
    declared = digest_payload.pop("planDigest", None)
    _require_sha256(declared, "replay plan planDigest")
    if declared != sha256_json(digest_payload):
        raise ValueError("replay plan digest mismatch")
    require_text(plan.get("generatedAt"), "replay plan generatedAt")
    fork_repository = require_text(
        plan.get("forkRepository"),
        "replay plan forkRepository",
    )
    owner, _ = _fork_parts(fork_repository)
    if fork_repository.casefold() == "magento/magento2":
        raise ValueError("replay fixtures must be created in a separate fork")
    require_text(plan.get("corpusId"), "replay plan corpusId")
    _require_sha256(plan.get("corpusDigest"), "replay plan corpusDigest")
    _require_sha256(
        plan.get("executionCorpusDigest"),
        "replay plan executionCorpusDigest",
    )
    cases = plan.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("replay plan has no cases")
    case_ids: set[str] = set()
    for index, case in enumerate(cases):
        field = f"replay plan cases[{index}]"
        if not isinstance(case, Mapping):
            raise ValueError(f"{field} must be an object")
        _require_fields(case, PLAN_CASE_FIELDS, field)
        case_id = require_text(case.get("caseId"), f"{field}.caseId")
        if case_id in case_ids:
            raise ValueError("replay plan case identities must be unique")
        case_ids.add(case_id)
        base_ref = require_text(case.get("baseRef"), f"{field}.baseRef")
        head_ref = require_text(case.get("headRef"), f"{field}.headRef")
        if base_ref == head_ref:
            raise ValueError(f"{field} refs must be distinct")
        base_sha = require_full_sha(case.get("baseSha"), f"{field}.baseSha")
        head_sha = require_full_sha(case.get("headSha"), f"{field}.headSha")
        if base_sha == head_sha:
            raise ValueError(f"{field} SHAs must be distinct")
        require_text(case.get("title"), f"{field}.title")
        require_text(case.get("body"), f"{field}.body")
        if case.get("head") != f"{owner}:{head_ref}":
            raise ValueError(f"{field}.head is not bound to the fork owner/ref")
    return plan


def validate_replay_lock(
    lock: Any,
    corpus: Mapping[str, Any],
    *,
    corpus_summary: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Validate a replay-apply lock against every frozen corpus identity.

    The embedded replay plan makes the planDigest independently reproducible
    from the lock artifact. Strict field and order checks also distinguish a
    complete `replay-apply` result from a hand-written subset that merely has a
    valid self-digest.
    """

    execution, summary = _execution_source(
        corpus,
        corpus_summary=corpus_summary,
    )
    if not isinstance(lock, Mapping) or lock.get("kind") != LOCK_KIND:
        raise ValueError("replay lock kind is invalid")
    assert_label_free_execution_value(lock, context="primary H replay lock")
    _require_fields(lock, LOCK_FIELDS, "replay lock")
    digest_payload = dict(lock)
    declared_digest = digest_payload.pop("lockDigest", None)
    _require_sha256(declared_digest, "replay lock lockDigest")
    if declared_digest != sha256_json(digest_payload):
        raise ValueError("replay lock digest mismatch")
    require_text(lock.get("generatedAt"), "replay lock generatedAt")

    fork_repository = require_text(
        lock.get("forkRepository"),
        "replay lock forkRepository",
    )
    owner, _ = _fork_parts(fork_repository)
    if fork_repository.casefold() == "magento/magento2":
        raise ValueError("replay lock must target a separate fork")
    if lock.get("corpusId") != summary["corpusId"]:
        raise ValueError("replay lock corpusId belongs to a different corpus")
    if lock.get("corpusDigest") != summary["corpusDigest"]:
        raise ValueError("replay lock corpusDigest belongs to a different corpus")
    if lock.get("executionCorpusDigest") != summary["executionCorpusDigest"]:
        raise ValueError(
            "replay lock executionCorpusDigest belongs to a different "
            "execution corpus"
        )
    plan_digest = _require_sha256(
        lock.get("planDigest"),
        "replay lock planDigest",
    )

    plan = _validate_plan(lock.get("plan"))
    if plan.get("planDigest") != plan_digest:
        raise ValueError("replay lock planDigest does not bind its embedded plan")
    for field, expected in (
        ("forkRepository", fork_repository),
        ("corpusId", summary["corpusId"]),
        ("corpusDigest", summary["corpusDigest"]),
        ("executionCorpusDigest", summary["executionCorpusDigest"]),
    ):
        if plan.get(field) != expected:
            raise ValueError(
                f"replay lock embedded plan {field} is not lock/corpus bound"
            )

    corpus_cases = execution.get("cases")
    if not isinstance(corpus_cases, list):
        # This should already have been rejected by validate_corpus. Keep the
        # boundary defensive when a trusted precomputed summary is supplied.
        raise ValueError("corpus cases must be an array")
    expected_plan_cases = [
        _expected_plan_case(case, index=index, owner=owner)
        for index, case in enumerate(corpus_cases, start=1)
    ]
    if plan.get("cases") != expected_plan_cases:
        raise ValueError(
            "replay lock embedded plan cases do not exactly match the corpus"
        )

    values = lock.get("cases")
    if not isinstance(values, list):
        raise ValueError("replay lock cases must be an array")
    expected_ids = [case["caseId"] for case in corpus_cases]
    observed_ids = [
        str(value.get("caseId") or "")
        if isinstance(value, Mapping)
        else ""
        for value in values
    ]
    if observed_ids != expected_ids:
        raise ValueError(
            "replay lock cases must exactly match corpus case order and set"
        )

    by_case: dict[str, dict[str, Any]] = {}
    pull_numbers: set[int] = set()
    for index, (value, corpus_case, plan_case) in enumerate(
        zip(values, corpus_cases, expected_plan_cases, strict=True)
    ):
        field = f"replay lock cases[{index}]"
        if not isinstance(value, Mapping):
            raise ValueError(f"{field} must be an object")
        _require_fields(value, LOCK_CASE_FIELDS, field)
        case_id = corpus_case["caseId"]
        expected_identity = {
            "caseId": case_id,
            "baseRef": corpus_case["replay"]["baseRef"],
            "baseSha": corpus_case["snapshot"]["baseSha"],
            "headRef": corpus_case["replay"]["headRef"],
            "headSha": corpus_case["snapshot"]["headSha"],
        }
        for identity_field, expected in expected_identity.items():
            if value.get(identity_field) != expected:
                raise ValueError(
                    f"{field}.{identity_field} does not match corpus"
                )
            if plan_case.get(identity_field) != expected:
                raise ValueError(
                    f"{field}.{identity_field} does not match embedded plan"
                )
        pull_number = value.get("forkPrNumber")
        if (
            isinstance(pull_number, bool)
            or not isinstance(pull_number, int)
            or pull_number <= 0
        ):
            raise ValueError(f"{field}.forkPrNumber must be a positive integer")
        if pull_number in pull_numbers:
            raise ValueError("replay lock fork PR numbers must be unique")
        pull_numbers.add(pull_number)
        expected_url = (
            f"https://github.com/{fork_repository}/pull/{pull_number}"
        )
        if value.get("forkPrUrl") != expected_url:
            raise ValueError(
                f"{field}.forkPrUrl is not canonical for forkPrNumber"
            )
        by_case[case_id] = dict(value)
    return by_case


def _validated_fork_metadata(
    value: Any,
    *,
    fork_repository: str,
) -> Mapping[str, Any]:
    parent = value.get("parent") if isinstance(value, Mapping) else None
    source = value.get("source") if isinstance(value, Mapping) else None
    upstream_names = {
        str(item.get("full_name") or "").casefold()
        for item in (parent, source)
        if isinstance(item, Mapping)
    }
    if (
        not isinstance(value, Mapping)
        or value.get("full_name") != fork_repository
        or value.get("fork") is not True
        or "magento/magento2" not in upstream_names
    ):
        raise ValueError(
            f"{fork_repository} is not a GitHub fork of magento/magento2"
        )
    return value


def _validated_ref_observation(
    value: Any,
    *,
    fork_repository: str,
    ref: str,
    sha: str,
) -> dict[str, Any]:
    field = f"{fork_repository}:{ref}"
    if not isinstance(value, Mapping):
        raise RuntimeError(f"GitHub ref observation for {field} is not an object")
    qualified_ref = f"refs/heads/{ref}"
    if value.get("ref") != qualified_ref:
        raise RuntimeError(
            f"GitHub ref observation for {field} has the wrong identity"
        )
    git_object = value.get("object")
    if (
        not isinstance(git_object, Mapping)
        or git_object.get("sha") != sha
        or git_object.get("type") != "commit"
    ):
        raise RuntimeError(
            f"GitHub ref observation for {field} has the wrong commit"
        )
    expected_object_url = (
        f"https://api.github.com/repos/{fork_repository}/git/commits/{sha}"
    )
    if git_object.get("url") != expected_object_url:
        raise RuntimeError(
            f"GitHub ref observation for {field} has a non-canonical object URL"
        )
    return {
        "apiPath": _ref_api_path(fork_repository, ref),
        "name": ref,
        "qualifiedName": qualified_ref,
        "sha": sha,
        "objectType": "commit",
        "objectApiUrl": expected_object_url,
    }


def _ensure_ref(
    client: GitHubClient,
    *,
    fork_repository: str,
    ref: str,
    sha: str,
    source_repository: Path | None,
    git_remote: str | None,
) -> None:
    try:
        current = client.get_ref(fork_repository, ref)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        current = None
    if isinstance(current, Mapping):
        observed = ((current.get("object") or {}).get("sha"))
        if observed != sha:
            raise RuntimeError(
                f"fork ref {ref} already exists at {observed}, expected {sha}; "
                "the script will not overwrite benchmark refs"
            )
        return
    try:
        client.create_ref(fork_repository, ref, sha)
    except RuntimeError as exc:
        message = str(exc).casefold()
        missing_object = (
            "http 422" in message
            and (
                "object does not exist" in message
                or "reference update failed" in message
                or "not a valid object" in message
            )
        )
        if not missing_object:
            raise
        if source_repository is None or not git_remote:
            raise RuntimeError(
                f"GitHub could not create {ref} directly. The commit may not "
                "yet exist in the fork; rerun with --source-repository and "
                "--git-remote so it can be pushed without force."
            )
        run(
            [
                "git",
                "-C",
                str(source_repository),
                "push",
                "--porcelain",
                git_remote,
                f"{sha}:refs/heads/{ref}",
            ]
        )
        observed = client.get_ref(fork_repository, ref)
        if ((observed.get("object") or {}).get("sha")) != sha:
            raise RuntimeError(
                f"pushed fork ref {ref} could not be verified at {sha}"
            )


def _validated_pull(
    pull: Any,
    *,
    fork_repository: str,
    case: Mapping[str, Any],
) -> tuple[int, str]:
    if not isinstance(pull, Mapping):
        raise RuntimeError(
            f"fork PR for {case['caseId']} returned a non-object"
        )
    number = pull.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise RuntimeError(
            f"fork PR for {case['caseId']} has no positive PR number"
        )
    expected_url = f"https://github.com/{fork_repository}/pull/{number}"
    if pull.get("html_url") != expected_url:
        raise RuntimeError(
            f"fork PR for {case['caseId']} has a non-canonical URL"
        )
    expected_api_url = (
        f"https://api.github.com/repos/{fork_repository}/pulls/{number}"
    )
    if pull.get("url") != expected_api_url:
        raise RuntimeError(
            f"fork PR for {case['caseId']} has a non-canonical API URL"
        )
    for side, expected_ref, expected_sha in (
        ("base", case["baseRef"], case["baseSha"]),
        ("head", case["headRef"], case["headSha"]),
    ):
        value = pull.get(side)
        if not isinstance(value, Mapping):
            raise RuntimeError(
                f"fork PR for {case['caseId']} has no {side} identity"
            )
        if value.get("ref") != expected_ref or value.get("sha") != expected_sha:
            raise RuntimeError(
                f"fork PR for {case['caseId']} does not match its frozen "
                f"{side} ref/SHA"
            )
        repository = value.get("repo")
        if (
            not isinstance(repository, Mapping)
            or repository.get("full_name") != fork_repository
        ):
            raise RuntimeError(
                f"fork PR for {case['caseId']} {side} is not in the "
                "confirmed fork"
            )
    return number, expected_url


def apply_plan(
    client: GitHubClient,
    *,
    plan_path: Path,
    output: Path,
    confirm_fork: str,
    source_repository: Path | None = None,
    git_remote: str | None = None,
) -> dict[str, Any]:
    """Create immutable fork branches and PRs.

    This function is intentionally not called by collection or analysis. It is
    the sole external mutation boundary and requires an exact fork confirmation.
    Existing matching refs/PRs are reused; divergent refs are never overwritten.
    """

    plan = _validate_plan(read_json(plan_path))
    fork_repository = require_text(
        plan.get("forkRepository"),
        "plan.forkRepository",
    )
    if confirm_fork != fork_repository:
        raise ValueError(
            "--confirm-fork must exactly match the replay plan fork repository"
        )
    if fork_repository.casefold() == "magento/magento2":
        raise ValueError("replay fixtures must be created in a separate fork")
    _validate_push_remote(
        source_repository=source_repository,
        git_remote=git_remote,
        fork_repository=fork_repository,
    )
    owner, _ = _fork_parts(fork_repository)
    client.require_token()
    _validated_fork_metadata(
        client.get(f"/repos/{fork_repository}"),
        fork_repository=fork_repository,
    )
    locked = []
    pull_numbers: set[int] = set()
    for case in plan["cases"]:
        _ensure_ref(
            client,
            fork_repository=fork_repository,
            ref=case["baseRef"],
            sha=case["baseSha"],
            source_repository=source_repository,
            git_remote=git_remote,
        )
        _ensure_ref(
            client,
            fork_repository=fork_repository,
            ref=case["headRef"],
            sha=case["headSha"],
            source_repository=source_repository,
            git_remote=git_remote,
        )
        pull = client.find_pull(
            fork_repository,
            owner=owner,
            head=case["headRef"],
        )
        if pull is None:
            pull = client.create_pull(
                fork_repository,
                title=case["title"],
                body=case["body"],
                base=case["baseRef"],
                head=case["head"],
            )
        pull_number, pull_url = _validated_pull(
            pull,
            fork_repository=fork_repository,
            case=case,
        )
        if pull_number in pull_numbers:
            raise RuntimeError(
                "GitHub returned one fork PR for multiple replay cases"
            )
        pull_numbers.add(pull_number)
        locked.append(
            {
                "caseId": case["caseId"],
                "baseRef": case["baseRef"],
                "baseSha": case["baseSha"],
                "headRef": case["headRef"],
                "headSha": case["headSha"],
                "forkPrNumber": pull_number,
                "forkPrUrl": pull_url,
            }
        )
    lock = {
        "kind": LOCK_KIND,
        "generatedAt": _now(),
        "forkRepository": fork_repository,
        "corpusId": plan["corpusId"],
        "corpusDigest": plan["corpusDigest"],
        "executionCorpusDigest": plan["executionCorpusDigest"],
        "planDigest": plan["planDigest"],
        "plan": dict(plan),
        "cases": locked,
    }
    lock["lockDigest"] = sha256_json(lock)
    write_json(output, lock)
    return lock


def _repository_observation(
    value: Any,
    *,
    fork_repository: str,
) -> dict[str, Any]:
    metadata = _validated_fork_metadata(
        value,
        fork_repository=fork_repository,
    )
    repository_id = _positive_integer(
        metadata.get("id"),
        "fork repository id",
    )
    node_id = require_text(
        metadata.get("node_id"),
        "fork repository node_id",
    )
    return {
        "apiPath": f"/repos/{fork_repository}",
        "repositoryId": repository_id,
        "nodeId": node_id,
        "fullName": fork_repository,
        "fork": True,
        "upstreamRepository": "magento/magento2",
    }


def _pull_observation(
    value: Any,
    *,
    fork_repository: str,
    case: Mapping[str, Any],
) -> dict[str, Any]:
    number, html_url = _validated_pull(
        value,
        fork_repository=fork_repository,
        case=case,
    )
    if number != case.get("forkPrNumber") or html_url != case.get("forkPrUrl"):
        raise RuntimeError(
            f"live fork PR identity drift for {case['caseId']}"
        )
    if not isinstance(value, Mapping):
        raise RuntimeError(
            f"fork PR for {case['caseId']} returned a non-object"
        )
    pull_id = _positive_integer(
        value.get("id"),
        f"fork PR for {case['caseId']} id",
    )
    node_id = require_text(
        value.get("node_id"),
        f"fork PR for {case['caseId']} node_id",
    )
    state = value.get("state")
    if state not in {"open", "closed"}:
        raise RuntimeError(
            f"fork PR for {case['caseId']} has an invalid state"
        )
    return {
        "apiPath": _pull_api_path(fork_repository, number),
        "pullRequestId": pull_id,
        "nodeId": node_id,
        "number": number,
        "htmlUrl": html_url,
        "state": state,
        "baseRepository": fork_repository,
        "baseRef": case["baseRef"],
        "baseSha": case["baseSha"],
        "headRepository": fork_repository,
        "headRef": case["headRef"],
        "headSha": case["headSha"],
    }


def verify_replay(
    client: GitHubClient,
    *,
    execution_corpus_path: Path,
    replay_lock_path: Path,
    output: Path,
) -> dict[str, Any]:
    """Live-check all fork refs/PRs and emit an offline-verifiable attestation."""

    if not getattr(client, "token", None):
        raise RuntimeError(
            "a GitHub token is required for the 151-request live replay "
            "verification"
        )
    execution_corpus = read_json(execution_corpus_path)
    if (
        not isinstance(execution_corpus, Mapping)
        or execution_corpus.get("kind") != EXECUTION_CORPUS_KIND
    ):
        raise ValueError(
            "verify-replay requires a label-free analysis execution corpus"
        )
    summary = validate_execution_corpus(execution_corpus)
    lock = read_json(replay_lock_path)
    lock_by_case = validate_replay_lock(
        lock,
        execution_corpus,
        corpus_summary=summary,
    )
    fork_repository = lock["forkRepository"]
    repository_observation = _repository_observation(
        client.get(f"/repos/{fork_repository}"),
        fork_repository=fork_repository,
    )
    observations = []
    for corpus_case in execution_corpus["cases"]:
        case_id = corpus_case["caseId"]
        locked = lock_by_case[case_id]
        base_ref = _validated_ref_observation(
            client.get_ref(fork_repository, locked["baseRef"]),
            fork_repository=fork_repository,
            ref=locked["baseRef"],
            sha=locked["baseSha"],
        )
        head_ref = _validated_ref_observation(
            client.get_ref(fork_repository, locked["headRef"]),
            fork_repository=fork_repository,
            ref=locked["headRef"],
            sha=locked["headSha"],
        )
        pull_request = _pull_observation(
            client.get(
                _pull_api_path(
                    fork_repository,
                    locked["forkPrNumber"],
                )
            ),
            fork_repository=fork_repository,
            case=locked,
        )
        observations.append(
            {
                "caseId": case_id,
                "baseRef": base_ref,
                "headRef": head_ref,
                "pullRequest": pull_request,
            }
        )
    attestation = {
        "kind": ATTESTATION_KIND,
        "collectedAt": _now(),
        "corpusId": summary["corpusId"],
        "corpusDigest": summary["corpusDigest"],
        "executionCorpusDigest": summary["executionCorpusDigest"],
        "replayLockDigest": lock["lockDigest"],
        "planDigest": lock["planDigest"],
        "forkRepository": fork_repository,
        "repositoryObservation": repository_observation,
        "cases": observations,
    }
    attestation["attestationDigest"] = sha256_json(attestation)
    # Exercise the offline consumer before publishing the receipt.
    validate_replay_attestation(
        attestation,
        lock,
        execution_corpus,
        corpus_summary=summary,
    )
    write_json(output, attestation)
    return attestation


def validate_replay_attestation(
    attestation: Any,
    lock: Mapping[str, Any],
    corpus: Mapping[str, Any],
    *,
    corpus_summary: Mapping[str, Any] | None = None,
) -> str:
    """Validate a sealed live observation without making network requests."""

    execution, summary = _execution_source(
        corpus,
        corpus_summary=corpus_summary,
    )
    lock_by_case = validate_replay_lock(
        lock,
        corpus,
        corpus_summary=summary,
    )
    if (
        not isinstance(attestation, Mapping)
        or attestation.get("kind") != ATTESTATION_KIND
    ):
        raise ValueError("replay attestation kind is invalid")
    assert_label_free_execution_value(
        attestation,
        context="primary H replay attestation",
    )
    _require_fields(attestation, ATTESTATION_FIELDS, "replay attestation")
    digest_payload = dict(attestation)
    declared_digest = digest_payload.pop("attestationDigest", None)
    _require_sha256(
        declared_digest,
        "replay attestation attestationDigest",
    )
    if declared_digest != sha256_json(digest_payload):
        raise ValueError("replay attestation digest mismatch")
    _utc_timestamp(
        attestation.get("collectedAt"),
        "replay attestation collectedAt",
    )
    expected_bindings = {
        "corpusId": summary["corpusId"],
        "corpusDigest": summary["corpusDigest"],
        "executionCorpusDigest": summary["executionCorpusDigest"],
        "replayLockDigest": lock["lockDigest"],
        "planDigest": lock["planDigest"],
        "forkRepository": lock["forkRepository"],
    }
    for field, expected in expected_bindings.items():
        if attestation.get(field) != expected:
            raise ValueError(
                f"replay attestation {field} is not corpus/lock bound"
            )

    fork_repository = lock["forkRepository"]
    repository = attestation.get("repositoryObservation")
    if not isinstance(repository, Mapping):
        raise ValueError("replay attestation repositoryObservation is invalid")
    _require_fields(
        repository,
        REPOSITORY_OBSERVATION_FIELDS,
        "replay attestation repositoryObservation",
    )
    expected_repository = {
        "apiPath": f"/repos/{fork_repository}",
        "fullName": fork_repository,
        "fork": True,
        "upstreamRepository": "magento/magento2",
    }
    for field, expected in expected_repository.items():
        if repository.get(field) != expected:
            raise ValueError(
                "replay attestation repository observation is not canonical"
            )
    _positive_integer(
        repository.get("repositoryId"),
        "replay attestation repositoryId",
    )
    require_text(
        repository.get("nodeId"),
        "replay attestation repository nodeId",
    )

    cases = attestation.get("cases")
    if not isinstance(cases, list):
        raise ValueError("replay attestation cases must be an array")
    expected_ids = [case["caseId"] for case in execution["cases"]]
    observed_ids = [
        str(case.get("caseId") or "")
        if isinstance(case, Mapping)
        else ""
        for case in cases
    ]
    if observed_ids != expected_ids:
        raise ValueError(
            "replay attestation cases must exactly match corpus order and set"
        )

    pull_ids: set[int] = set()
    pull_nodes: set[str] = set()
    for index, observed in enumerate(cases):
        field = f"replay attestation cases[{index}]"
        if not isinstance(observed, Mapping):
            raise ValueError(f"{field} must be an object")
        _require_fields(observed, ATTESTATION_CASE_FIELDS, field)
        case_id = expected_ids[index]
        locked = lock_by_case[case_id]
        for side in ("base", "head"):
            ref_field = f"{side}Ref"
            ref = observed.get(ref_field)
            if not isinstance(ref, Mapping):
                raise ValueError(f"{field}.{ref_field} must be an object")
            _require_fields(ref, REF_OBSERVATION_FIELDS, f"{field}.{ref_field}")
            name = locked[f"{side}Ref"]
            sha = locked[f"{side}Sha"]
            expected_ref = {
                "apiPath": _ref_api_path(fork_repository, name),
                "name": name,
                "qualifiedName": f"refs/heads/{name}",
                "sha": sha,
                "objectType": "commit",
                "objectApiUrl": (
                    "https://api.github.com/repos/"
                    f"{fork_repository}/git/commits/{sha}"
                ),
            }
            if dict(ref) != expected_ref:
                raise ValueError(
                    f"{field}.{ref_field} does not match the replay lock"
                )
        pull = observed.get("pullRequest")
        if not isinstance(pull, Mapping):
            raise ValueError(f"{field}.pullRequest must be an object")
        _require_fields(
            pull,
            PULL_OBSERVATION_FIELDS,
            f"{field}.pullRequest",
        )
        pull_id = _positive_integer(
            pull.get("pullRequestId"),
            f"{field}.pullRequest.pullRequestId",
        )
        node_id = require_text(
            pull.get("nodeId"),
            f"{field}.pullRequest.nodeId",
        )
        if pull_id in pull_ids or node_id in pull_nodes:
            raise ValueError("replay attestation PR identities must be unique")
        pull_ids.add(pull_id)
        pull_nodes.add(node_id)
        if pull.get("state") not in {"open", "closed"}:
            raise ValueError(f"{field}.pullRequest.state is invalid")
        expected_pull = {
            "apiPath": _pull_api_path(
                fork_repository,
                locked["forkPrNumber"],
            ),
            "number": locked["forkPrNumber"],
            "htmlUrl": locked["forkPrUrl"],
            "baseRepository": fork_repository,
            "baseRef": locked["baseRef"],
            "baseSha": locked["baseSha"],
            "headRepository": fork_repository,
            "headRef": locked["headRef"],
            "headSha": locked["headSha"],
        }
        for pull_field, expected in expected_pull.items():
            if pull.get(pull_field) != expected:
                raise ValueError(
                    f"{field}.pullRequest.{pull_field} does not match lock"
                )
    return str(declared_digest)
