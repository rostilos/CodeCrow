from __future__ import annotations

import copy
import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .corpus import validate_corpus
from .execution_corpus import (
    EXECUTION_CORPUS_KIND,
    assert_label_free_execution_value,
    build_execution_corpus,
    known_label_values,
    validate_execution_corpus,
)
from .github import GitHubClient
from .judge import (
    LOCATION_RELATIONS,
    MATCH_VERDICTS,
    OpenAICompatibleJudge,
    PAIR_PROMPT_COMPACTION_POLICY,
    YES_NO_UNCLEAR,
    _candidate_evidence,
    _extract_json,
    _gold_prompt,
    _majority_match,
    _resolved_judge_config,
    _right_added_lines,
    _validate_local_snapshot,
    _validate_match_response,
    _validated_judge_call,
)
from .path_transition import resolve_path_transition
from .replay import (
    _ensure_ref,
    _fork_parts,
    _positive_integer,
    _pull_api_path,
    _pull_observation,
    _ref_api_path,
    _repository_observation,
    _require_fields,
    _require_sha256,
    _utc_timestamp,
    _validate_push_remote,
    _validated_fork_metadata,
    _validated_pull,
    _validated_ref_observation,
    validate_replay_attestation_freshness,
    validate_replay_lock,
)
from .runner import (
    RUN_KIND,
    AnalysisExecutionContext,
    run_analysis,
    runtime_image_projection,
)
from .util import (
    deterministic_git_diff_command,
    hermetic_git_environment,
    public_config,
    read_json,
    require_full_sha,
    require_text,
    run,
    sha256_json,
    sha256_text,
    write_json,
)


POST_FIX_PLAN_KIND = "codecrow-magento2-post-fix-replay-plan"
POST_FIX_LOCK_KIND = "codecrow-magento2-post-fix-replay-lock"
POST_FIX_ATTESTATION_KIND = "codecrow-magento2-post-fix-replay-attestation"
POST_FIX_RUN_KIND = "codecrow-magento2-post-fix-analysis-run"
POST_FIX_JUDGMENT_KIND = "codecrow-magento2-post-fix-judgment-run"
POST_FIX_CONTROL_KIND = "codecrow-magento2-post-fix-control"
POST_FIX_CONTROL_SET_KIND = "codecrow-magento2-post-fix-control-set"

POST_FIX_PROMPT_VERSION = "magento2-verified-f-root-cause-control-2026-07-29"
POST_FIX_SYSTEM = """\
You are judging a separate verified-post-fix control snapshot. Compare one
curated issue that CodeCrow detected at the earlier review snapshot H against
every CodeCrow finding produced at the verified final snapshot F.

Decide only whether each F finding reports the same underlying root cause.
A substantive match requires the same defect or harmful practice, compatible
consequence, and a corrective change that would satisfy both reports. Similar
file, category, or wording alone is not a match. Do not assume the issue is
absent merely because F is a later snapshot, and do not assume a finding is
correct merely because it was produced at F. Return JSON only and judge every
candidate ID exactly once.
"""
POST_FIX_PROMPT_DIGEST = sha256_text(
    POST_FIX_SYSTEM
    + POST_FIX_PROMPT_VERSION
    + PAIR_PROMPT_COMPACTION_POLICY
)

POST_FIX_OUTCOMES = {
    "disappeared",
    "still_detected",
    "unverifiable",
    "not_applicable_primary_unmatched",
}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")

PLAN_FIELDS = {
    "kind",
    "generatedAt",
    "forkRepository",
    "corpusId",
    "corpusDigest",
    "executionCorpusDigest",
    "primaryReplayLockDigest",
    "snapshotPolicy",
    "diffPolicy",
    "cases",
    "planDigest",
}
PLAN_CASE_FIELDS = {
    "caseId",
    "baseRef",
    "baseSha",
    "reviewHeadSha",
    "finalRef",
    "finalSha",
    "diffSha256",
    "fileCount",
    "changedPaths",
    "deletedPaths",
    "ancestryEvidence",
}
LOCK_FIELDS = {
    "kind",
    "generatedAt",
    "forkRepository",
    "corpusId",
    "corpusDigest",
    "executionCorpusDigest",
    "primaryReplayLockDigest",
    "planDigest",
    "plan",
    "cases",
    "lockDigest",
}
LOCK_CASE_FIELDS = {
    "caseId",
    "baseRef",
    "baseSha",
    "reviewHeadSha",
    "finalRef",
    "finalSha",
    "forkPrNumber",
    "forkPrUrl",
}
ATTESTATION_FIELDS = {
    "kind",
    "collectedAt",
    "corpusId",
    "corpusDigest",
    "executionCorpusDigest",
    "primaryReplayLockDigest",
    "postFixReplayLockDigest",
    "planDigest",
    "forkRepository",
    "repositoryObservation",
    "cases",
    "attestationDigest",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _post_fix_forbidden_values(corpus: Mapping[str, Any]) -> set[Any]:
    allowed: set[Any] = {
        corpus.get("corpusId"),
        corpus.get("corpusDigest"),
        corpus.get("repository"),
        corpus.get("defaultBranch"),
    }
    for case in corpus.get("cases") or []:
        if not isinstance(case, Mapping):
            continue
        snapshot = case.get("snapshot")
        source = case.get("sourcePr")
        replay = case.get("replay")
        ancestry = case.get("ancestryEvidence")
        allowed.add(case.get("caseId"))
        if isinstance(snapshot, Mapping):
            allowed.update(
                {
                    snapshot.get("baseSha"),
                    snapshot.get("headSha"),
                    snapshot.get("diffSha256"),
                    *(snapshot.get("changedPaths") or []),
                }
            )
        if isinstance(source, Mapping):
            allowed.update(
                {
                    source.get("finalHeadSha"),
                    source.get("mergeCommitSha"),
                }
            )
        if isinstance(replay, Mapping):
            allowed.update(
                {
                    replay.get("baseRef"),
                    replay.get("headRef"),
                }
            )
        if isinstance(ancestry, Mapping):
            allowed.add(ancestry.get("mergeSecondParentSha"))
    return known_label_values(corpus) - allowed


def _artifact_digest(
    value: Any,
    *,
    kind: str,
    field: str,
) -> str:
    if not isinstance(value, Mapping) or value.get("kind") != kind:
        raise ValueError(f"{kind} artifact kind is invalid")
    payload = dict(value)
    declared = payload.pop(field, None)
    _require_sha256(declared, field)
    if declared != sha256_json(payload):
        raise ValueError(f"{field} mismatch")
    return str(declared)


def _git_output(repository: Path, *arguments: str) -> str:
    return run(
        ["git", "--no-replace-objects", "-C", str(repository), *arguments],
        env=hermetic_git_environment(offline=True),
    )


def _require_commit(repository: Path, sha: str, field: str) -> None:
    require_full_sha(sha, field)
    observed = _git_output(repository, "cat-file", "-t", sha).strip()
    if observed != "commit":
        raise ValueError(f"{field} is not a Git commit")


def _is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        env=hermetic_git_environment(offline=True),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode not in {0, 1}:
        raise RuntimeError(
            "Git ancestry check failed: "
            + completed.stderr.decode("utf-8", errors="replace").strip()
        )
    return completed.returncode == 0


def _diff(repository: Path, base_sha: str, head_sha: str) -> str:
    return run(
        deterministic_git_diff_command(
            repository,
            "--full-index",
            base_sha,
            head_sha,
        ),
        env=hermetic_git_environment(offline=True),
    )


def _paths(
    repository: Path,
    base_sha: str,
    head_sha: str,
    *,
    diff_filter: str | None = None,
) -> list[str]:
    command = deterministic_git_diff_command(
        repository,
        "--name-only",
        "-z",
    )
    if diff_filter is not None:
        command.append(f"--diff-filter={diff_filter}")
    command.extend([base_sha, head_sha])
    return sorted(
        {
            value
            for value in run(
                command,
                env=hermetic_git_environment(offline=True),
            ).split("\0")
            if value
        }
    )


def _post_fix_case(
    case: Mapping[str, Any],
    primary_replay: Mapping[str, Any],
    *,
    repository: Path,
) -> dict[str, Any]:
    case_id = str(case["caseId"])
    base_sha = require_full_sha(
        case["snapshot"]["baseSha"],
        f"{case_id}.baseSha",
    )
    review_head = require_full_sha(
        case["snapshot"]["headSha"],
        f"{case_id}.reviewHeadSha",
    )
    final_sha = require_full_sha(
        case["sourcePr"]["finalHeadSha"],
        f"{case_id}.finalSha",
    )
    for field, sha in (
        ("baseSha", base_sha),
        ("reviewHeadSha", review_head),
        ("finalSha", final_sha),
    ):
        _require_commit(repository, sha, f"{case_id}.{field}")
    if (
        base_sha == review_head
        or review_head == final_sha
        or not _is_ancestor(repository, base_sha, review_head)
        or not _is_ancestor(repository, review_head, final_sha)
    ):
        raise ValueError(
            f"{case_id} does not have strict B < H < verified F ancestry"
        )
    if (
        primary_replay.get("baseSha") != base_sha
        or primary_replay.get("headSha") != review_head
    ):
        raise ValueError(
            f"{case_id} primary replay identity disagrees with the corpus"
        )

    changed_paths = _paths(repository, base_sha, final_sha)
    deleted_paths = _paths(
        repository,
        base_sha,
        final_sha,
        diff_filter="D",
    )
    final_ref = f"benchmark/magento2/{case_id}/verified-f"
    return {
        "caseId": case_id,
        "baseRef": primary_replay["baseRef"],
        "baseSha": base_sha,
        "reviewHeadSha": review_head,
        "finalRef": final_ref,
        "finalSha": final_sha,
        "diffSha256": sha256_text(_diff(repository, base_sha, final_sha)),
        "fileCount": len(changed_paths),
        "changedPaths": changed_paths,
        "deletedPaths": deleted_paths,
        "ancestryEvidence": {
            "baseAncestorReviewHead": True,
            "reviewHeadStrictAncestorFinalHead": True,
            "finalHeadMatchesSourcePr": True,
            "finalHeadIsMergeSecondParent": (
                case["ancestryEvidence"]["mergeSecondParentSha"] == final_sha
            ),
        },
    }


def build_post_fix_plan(
    corpus: Mapping[str, Any],
    primary_replay_lock: Mapping[str, Any],
    *,
    repository: Path,
) -> dict[str, Any]:
    summary = validate_corpus(corpus, paper_ready=True, required_cases=50)
    execution_summary = validate_execution_corpus(
        build_execution_corpus(corpus)
    )
    primary_by_case = validate_replay_lock(
        primary_replay_lock,
        corpus,
        corpus_summary=summary,
    )
    fork_repository = require_text(
        primary_replay_lock.get("forkRepository"),
        "primary replay forkRepository",
    )
    if not repository.is_dir() or not (repository / ".git").exists():
        raise ValueError("post-fix repository must be a local Git clone")
    cases = [
        _post_fix_case(
            case,
            primary_by_case[str(case["caseId"])],
            repository=repository,
        )
        for case in corpus["cases"]
    ]
    plan = {
        "kind": POST_FIX_PLAN_KIND,
        "generatedAt": _now(),
        "forkRepository": fork_repository,
        "corpusId": summary["corpusId"],
        "corpusDigest": summary["corpusDigest"],
        "executionCorpusDigest": execution_summary[
            "executionCorpusDigest"
        ],
        "primaryReplayLockDigest": primary_replay_lock["lockDigest"],
        "snapshotPolicy": "verified_source_pr_final_head_F",
        "diffPolicy": "git_no_replace_full_index_B_to_F",
        "cases": cases,
    }
    plan["planDigest"] = sha256_json(plan)
    assert_label_free_execution_value(
        plan,
        forbidden_values=_post_fix_forbidden_values(corpus),
        context="post-fix replay plan",
    )
    return plan


def create_post_fix_plan(
    *,
    corpus_path: Path,
    primary_replay_lock_path: Path,
    repository: Path,
    output: Path,
) -> dict[str, Any]:
    corpus = read_json(corpus_path)
    primary_lock = read_json(primary_replay_lock_path)
    if not isinstance(corpus, Mapping) or not isinstance(
        primary_lock, Mapping
    ):
        raise ValueError("corpus and primary replay lock must be objects")
    plan = build_post_fix_plan(
        corpus,
        primary_lock,
        repository=repository,
    )
    write_json(output, plan)
    return plan


def validate_post_fix_plan(
    plan: Any,
    corpus: Mapping[str, Any],
    primary_replay_lock: Mapping[str, Any],
    *,
    repository: Path | None = None,
) -> dict[str, dict[str, Any]]:
    execution_only = corpus.get("kind") == EXECUTION_CORPUS_KIND
    summary = (
        validate_execution_corpus(corpus)
        if execution_only
        else validate_corpus(corpus, paper_ready=True, required_cases=50)
    )
    execution_corpus_digest = (
        summary["executionCorpusDigest"]
        if execution_only
        else build_execution_corpus(corpus)["executionCorpusDigest"]
    )
    primary_by_case = validate_replay_lock(
        primary_replay_lock,
        corpus,
        corpus_summary=summary,
    )
    if not isinstance(plan, Mapping) or plan.get("kind") != POST_FIX_PLAN_KIND:
        raise ValueError("post-fix replay plan kind is invalid")
    _require_fields(plan, PLAN_FIELDS, "post-fix replay plan")
    _artifact_digest(plan, kind=POST_FIX_PLAN_KIND, field="planDigest")
    assert_label_free_execution_value(
        plan,
        forbidden_values=(
            _post_fix_forbidden_values(corpus)
            if corpus.get("kind") != EXECUTION_CORPUS_KIND
            else ()
        ),
        context="post-fix replay plan",
    )
    _utc_timestamp(plan.get("generatedAt"), "post-fix plan generatedAt")
    fork_repository = require_text(
        plan.get("forkRepository"),
        "post-fix plan forkRepository",
    )
    _fork_parts(fork_repository)
    if (
        fork_repository != primary_replay_lock.get("forkRepository")
        or plan.get("primaryReplayLockDigest")
        != primary_replay_lock.get("lockDigest")
        or plan.get("corpusId") != summary["corpusId"]
        or plan.get("corpusDigest") != summary["corpusDigest"]
        or plan.get("executionCorpusDigest") != execution_corpus_digest
        or plan.get("snapshotPolicy")
        != "verified_source_pr_final_head_F"
        or plan.get("diffPolicy") != "git_no_replace_full_index_B_to_F"
    ):
        raise ValueError("post-fix plan corpus/primary replay policy drift")
    cases = plan.get("cases")
    if not isinstance(cases, list) or len(cases) != len(corpus["cases"]):
        raise ValueError("post-fix plan must cover every corpus case")
    expected_ids = [str(case["caseId"]) for case in corpus["cases"]]
    observed_ids = [
        str(item.get("caseId") or "") if isinstance(item, Mapping) else ""
        for item in cases
    ]
    if observed_ids != expected_ids:
        raise ValueError(
            "post-fix plan case order/identity differs from the corpus"
        )
    by_case: dict[str, dict[str, Any]] = {}
    for index, (item, corpus_case) in enumerate(
        zip(cases, corpus["cases"], strict=True),
        start=1,
    ):
        field = f"post-fix plan cases[{index - 1}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{field} must be an object")
        _require_fields(item, PLAN_CASE_FIELDS, field)
        case_id = str(corpus_case["caseId"])
        primary = primary_by_case[case_id]
        final_ref = f"benchmark/magento2/{case_id}/verified-f"
        expected_final = (
            item.get("finalSha")
            if execution_only
            else corpus_case["sourcePr"]["finalHeadSha"]
        )
        require_full_sha(expected_final, f"{field}.finalSha")
        if expected_final == corpus_case["snapshot"]["headSha"]:
            raise ValueError(f"{field} requires strict H < F identity")
        if (
            item.get("baseRef") != primary["baseRef"]
            or item.get("baseSha") != corpus_case["snapshot"]["baseSha"]
            or item.get("reviewHeadSha")
            != corpus_case["snapshot"]["headSha"]
            or item.get("finalRef") != final_ref
            or item.get("finalSha") != expected_final
            or not isinstance(item.get("changedPaths"), list)
            or item.get("changedPaths")
            != sorted(set(item.get("changedPaths") or []))
            or item.get("deletedPaths")
            != sorted(set(item.get("deletedPaths") or []))
            or not set(item.get("deletedPaths") or []).issubset(
                set(item.get("changedPaths") or [])
            )
            or item.get("fileCount") != len(item.get("changedPaths") or [])
        ):
            raise ValueError(f"{field} immutable identity/path fields drift")
        _require_sha256(item.get("diffSha256"), f"{field}.diffSha256")
        ancestry = item.get("ancestryEvidence")
        expected_ancestry = {
            "baseAncestorReviewHead": True,
            "reviewHeadStrictAncestorFinalHead": True,
            "finalHeadMatchesSourcePr": True,
            "finalHeadIsMergeSecondParent": True,
        }
        if ancestry != expected_ancestry:
            raise ValueError(f"{field} ancestry evidence drift")
        if repository is not None:
            if execution_only:
                for sha_field in ("baseSha", "reviewHeadSha", "finalSha"):
                    _require_commit(
                        repository,
                        str(item[sha_field]),
                        f"{field}.{sha_field}",
                    )
                if (
                    not _is_ancestor(
                        repository,
                        str(item["baseSha"]),
                        str(item["reviewHeadSha"]),
                    )
                    or not _is_ancestor(
                        repository,
                        str(item["reviewHeadSha"]),
                        str(item["finalSha"]),
                    )
                    or sha256_text(
                        _diff(
                            repository,
                            str(item["baseSha"]),
                            str(item["finalSha"]),
                        )
                    )
                    != item["diffSha256"]
                    or _paths(
                        repository,
                        str(item["baseSha"]),
                        str(item["finalSha"]),
                    )
                    != item["changedPaths"]
                    or _paths(
                        repository,
                        str(item["baseSha"]),
                        str(item["finalSha"]),
                        diff_filter="D",
                    )
                    != item["deletedPaths"]
                ):
                    raise ValueError(
                        f"{field} local B-to-F Git reconstruction drift"
                    )
            else:
                observed = _post_fix_case(
                    corpus_case,
                    primary,
                    repository=repository,
                )
                if dict(item) != observed:
                    raise ValueError(f"{field} local Git reconstruction drift")
        by_case[case_id] = dict(item)
    return by_case


def apply_post_fix_plan(
    client: GitHubClient,
    *,
    execution_corpus_path: Path,
    primary_replay_lock_path: Path,
    plan_path: Path,
    output: Path,
    confirm_fork: str,
    source_repository: Path | None = None,
    git_remote: str | None = None,
) -> dict[str, Any]:
    corpus = read_json(execution_corpus_path)
    primary_lock = read_json(primary_replay_lock_path)
    plan = read_json(plan_path)
    if not all(
        isinstance(item, Mapping)
        for item in (corpus, primary_lock, plan)
    ):
        raise ValueError("post-fix replay inputs must be objects")
    validate_post_fix_plan(
        plan,
        corpus,
        primary_lock,
        repository=source_repository,
    )
    fork_repository = str(plan["forkRepository"])
    if confirm_fork != fork_repository:
        raise ValueError(
            "--confirm-fork must exactly match the post-fix plan fork"
        )
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
    for index, item in enumerate(plan["cases"], start=1):
        _ensure_ref(
            client,
            fork_repository=fork_repository,
            ref=item["baseRef"],
            sha=item["baseSha"],
            source_repository=source_repository,
            git_remote=git_remote,
        )
        _ensure_ref(
            client,
            fork_repository=fork_repository,
            ref=item["finalRef"],
            sha=item["finalSha"],
            source_repository=source_repository,
            git_remote=git_remote,
        )
        pull_case = {
            "caseId": item["caseId"],
            "baseRef": item["baseRef"],
            "baseSha": item["baseSha"],
            "headRef": item["finalRef"],
            "headSha": item["finalSha"],
        }
        pull = client.find_pull(
            fork_repository,
            owner=owner,
            head=item["finalRef"],
        )
        if pull is None:
            title = (
                "Magento 2 verified-F benchmark control "
                f"{index:03d}"
            )
            body = (
                "Immutable CodeCrow Magento 2 verified-F control "
                "fixture.\n\n"
                f"Fixture: `{item['caseId']}`\n"
                f"Base snapshot: `{item['baseSha']}`\n"
                f"Final snapshot: `{item['finalSha']}`"
            )
            pull = client.create_pull(
                fork_repository,
                title=title,
                body=body,
                base=item["baseRef"],
                head=f"{owner}:{item['finalRef']}",
            )
        number, url = _validated_pull(
            pull,
            fork_repository=fork_repository,
            case=pull_case,
        )
        if number in pull_numbers:
            raise RuntimeError(
                "GitHub returned one PR for multiple post-fix cases"
            )
        pull_numbers.add(number)
        locked.append(
            {
                "caseId": item["caseId"],
                "baseRef": item["baseRef"],
                "baseSha": item["baseSha"],
                "reviewHeadSha": item["reviewHeadSha"],
                "finalRef": item["finalRef"],
                "finalSha": item["finalSha"],
                "forkPrNumber": number,
                "forkPrUrl": url,
            }
        )
    lock = {
        "kind": POST_FIX_LOCK_KIND,
        "generatedAt": _now(),
        "forkRepository": fork_repository,
        "corpusId": plan["corpusId"],
        "corpusDigest": plan["corpusDigest"],
        "executionCorpusDigest": plan["executionCorpusDigest"],
        "primaryReplayLockDigest": plan["primaryReplayLockDigest"],
        "planDigest": plan["planDigest"],
        "plan": dict(plan),
        "cases": locked,
    }
    lock["lockDigest"] = sha256_json(lock)
    assert_label_free_execution_value(
        lock,
        forbidden_values=_post_fix_forbidden_values(corpus),
        context="post-fix replay lock",
    )
    validate_post_fix_lock(
        lock,
        corpus,
        primary_lock,
        repository=source_repository,
    )
    write_json(output, lock)
    return lock


def validate_post_fix_lock(
    lock: Any,
    corpus: Mapping[str, Any],
    primary_replay_lock: Mapping[str, Any],
    *,
    repository: Path | None = None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(lock, Mapping) or lock.get("kind") != POST_FIX_LOCK_KIND:
        raise ValueError("post-fix replay lock kind is invalid")
    _require_fields(lock, LOCK_FIELDS, "post-fix replay lock")
    _artifact_digest(lock, kind=POST_FIX_LOCK_KIND, field="lockDigest")
    assert_label_free_execution_value(
        lock,
        forbidden_values=(
            _post_fix_forbidden_values(corpus)
            if corpus.get("kind") != EXECUTION_CORPUS_KIND
            else ()
        ),
        context="post-fix replay lock",
    )
    _utc_timestamp(lock.get("generatedAt"), "post-fix lock generatedAt")
    plan_by_case = validate_post_fix_plan(
        lock.get("plan"),
        corpus,
        primary_replay_lock,
        repository=repository,
    )
    plan = lock["plan"]
    for field in (
        "forkRepository",
        "corpusId",
        "corpusDigest",
        "executionCorpusDigest",
        "primaryReplayLockDigest",
        "planDigest",
    ):
        expected = (
            plan["planDigest"] if field == "planDigest" else plan[field]
        )
        if lock.get(field) != expected:
            raise ValueError(f"post-fix lock {field} binding drift")
    cases = lock.get("cases")
    if not isinstance(cases, list):
        raise ValueError("post-fix lock cases must be an array")
    expected_ids = [str(case["caseId"]) for case in corpus["cases"]]
    observed_ids = [
        str(item.get("caseId") or "") if isinstance(item, Mapping) else ""
        for item in cases
    ]
    if observed_ids != expected_ids:
        raise ValueError("post-fix lock case order/identity drift")
    fork_repository = str(lock["forkRepository"])
    by_case = {}
    pull_numbers: set[int] = set()
    for index, item in enumerate(cases):
        field = f"post-fix lock cases[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{field} must be an object")
        _require_fields(item, LOCK_CASE_FIELDS, field)
        plan_case = plan_by_case[str(item["caseId"])]
        expected = {
            "caseId": plan_case["caseId"],
            "baseRef": plan_case["baseRef"],
            "baseSha": plan_case["baseSha"],
            "reviewHeadSha": plan_case["reviewHeadSha"],
            "finalRef": plan_case["finalRef"],
            "finalSha": plan_case["finalSha"],
        }
        if any(item.get(key) != value for key, value in expected.items()):
            raise ValueError(f"{field} identity drift")
        number = _positive_integer(
            item.get("forkPrNumber"),
            f"{field}.forkPrNumber",
        )
        if number in pull_numbers:
            raise ValueError("post-fix PR numbers must be unique")
        pull_numbers.add(number)
        if item.get("forkPrUrl") != (
            f"https://github.com/{fork_repository}/pull/{number}"
        ):
            raise ValueError(f"{field}.forkPrUrl is not canonical")
        by_case[str(item["caseId"])] = dict(item)
    return by_case


def verify_post_fix_replay(
    client: GitHubClient,
    *,
    execution_corpus_path: Path,
    primary_replay_lock_path: Path,
    post_fix_replay_lock_path: Path,
    output: Path,
) -> dict[str, Any]:
    if not getattr(client, "token", None):
        raise RuntimeError(
            "a GitHub token is required for live post-fix replay verification"
        )
    corpus = read_json(execution_corpus_path)
    primary_lock = read_json(primary_replay_lock_path)
    post_fix_lock = read_json(post_fix_replay_lock_path)
    if not all(
        isinstance(item, Mapping)
        for item in (corpus, primary_lock, post_fix_lock)
    ):
        raise ValueError("post-fix replay inputs must be objects")
    by_case = validate_post_fix_lock(
        post_fix_lock,
        corpus,
        primary_lock,
    )
    fork_repository = str(post_fix_lock["forkRepository"])
    repository_observation = _repository_observation(
        client.get(f"/repos/{fork_repository}"),
        fork_repository=fork_repository,
    )
    observations = []
    for corpus_case in corpus["cases"]:
        locked = by_case[str(corpus_case["caseId"])]
        base_ref = _validated_ref_observation(
            client.get_ref(fork_repository, locked["baseRef"]),
            fork_repository=fork_repository,
            ref=locked["baseRef"],
            sha=locked["baseSha"],
        )
        final_ref = _validated_ref_observation(
            client.get_ref(fork_repository, locked["finalRef"]),
            fork_repository=fork_repository,
            ref=locked["finalRef"],
            sha=locked["finalSha"],
        )
        pull_case = {
            "caseId": locked["caseId"],
            "baseRef": locked["baseRef"],
            "baseSha": locked["baseSha"],
            "headRef": locked["finalRef"],
            "headSha": locked["finalSha"],
            "forkPrNumber": locked["forkPrNumber"],
            "forkPrUrl": locked["forkPrUrl"],
        }
        pull = _pull_observation(
            client.get(
                _pull_api_path(
                    fork_repository,
                    locked["forkPrNumber"],
                )
            ),
            fork_repository=fork_repository,
            case=pull_case,
        )
        observations.append(
            {
                "caseId": locked["caseId"],
                "baseRef": base_ref,
                "finalRef": final_ref,
                "pullRequest": pull,
            }
        )
    attestation = {
        "kind": POST_FIX_ATTESTATION_KIND,
        "collectedAt": _now(),
        "corpusId": post_fix_lock["corpusId"],
        "corpusDigest": post_fix_lock["corpusDigest"],
        "executionCorpusDigest": post_fix_lock["executionCorpusDigest"],
        "primaryReplayLockDigest": post_fix_lock[
            "primaryReplayLockDigest"
        ],
        "postFixReplayLockDigest": post_fix_lock["lockDigest"],
        "planDigest": post_fix_lock["planDigest"],
        "forkRepository": fork_repository,
        "repositoryObservation": repository_observation,
        "cases": observations,
    }
    attestation["attestationDigest"] = sha256_json(attestation)
    assert_label_free_execution_value(
        attestation,
        forbidden_values=_post_fix_forbidden_values(corpus),
        context="post-fix replay attestation",
    )
    validate_post_fix_attestation(
        attestation,
        post_fix_lock,
        corpus,
        primary_lock,
    )
    write_json(output, attestation)
    return attestation


def validate_post_fix_attestation(
    attestation: Any,
    post_fix_lock: Mapping[str, Any],
    corpus: Mapping[str, Any],
    primary_replay_lock: Mapping[str, Any],
) -> str:
    by_case = validate_post_fix_lock(
        post_fix_lock,
        corpus,
        primary_replay_lock,
    )
    if (
        not isinstance(attestation, Mapping)
        or attestation.get("kind") != POST_FIX_ATTESTATION_KIND
    ):
        raise ValueError("post-fix replay attestation kind is invalid")
    _require_fields(
        attestation,
        ATTESTATION_FIELDS,
        "post-fix replay attestation",
    )
    digest = _artifact_digest(
        attestation,
        kind=POST_FIX_ATTESTATION_KIND,
        field="attestationDigest",
    )
    assert_label_free_execution_value(
        attestation,
        forbidden_values=(
            _post_fix_forbidden_values(corpus)
            if corpus.get("kind") != EXECUTION_CORPUS_KIND
            else ()
        ),
        context="post-fix replay attestation",
    )
    _utc_timestamp(
        attestation.get("collectedAt"),
        "post-fix replay attestation collectedAt",
    )
    expected_bindings = {
        "corpusId": post_fix_lock["corpusId"],
        "corpusDigest": post_fix_lock["corpusDigest"],
        "executionCorpusDigest": post_fix_lock["executionCorpusDigest"],
        "primaryReplayLockDigest": post_fix_lock[
            "primaryReplayLockDigest"
        ],
        "postFixReplayLockDigest": post_fix_lock["lockDigest"],
        "planDigest": post_fix_lock["planDigest"],
        "forkRepository": post_fix_lock["forkRepository"],
    }
    if any(
        attestation.get(field) != expected
        for field, expected in expected_bindings.items()
    ):
        raise ValueError("post-fix attestation binding drift")
    repository = attestation.get("repositoryObservation")
    if not isinstance(repository, Mapping) or repository != {
        "apiPath": f"/repos/{post_fix_lock['forkRepository']}",
        "repositoryId": repository.get("repositoryId"),
        "nodeId": repository.get("nodeId"),
        "fullName": post_fix_lock["forkRepository"],
        "fork": True,
        "upstreamRepository": "magento/magento2",
    }:
        raise ValueError("post-fix repository observation is invalid")
    _positive_integer(
        repository.get("repositoryId"),
        "post-fix repository id",
    )
    require_text(repository.get("nodeId"), "post-fix repository nodeId")
    cases = attestation.get("cases")
    expected_ids = [str(case["caseId"]) for case in corpus["cases"]]
    observed_ids = [
        str(item.get("caseId") or "") if isinstance(item, Mapping) else ""
        for item in cases or []
    ]
    if observed_ids != expected_ids:
        raise ValueError("post-fix attestation case identity/order drift")
    for index, item in enumerate(cases):
        locked = by_case[str(item["caseId"])]
        if set(item) != {"caseId", "baseRef", "finalRef", "pullRequest"}:
            raise ValueError(
                f"post-fix attestation cases[{index}] fields are invalid"
            )
        expected_base = {
            "apiPath": _ref_api_path(
                str(post_fix_lock["forkRepository"]),
                locked["baseRef"],
            ),
            "name": locked["baseRef"],
            "qualifiedName": f"refs/heads/{locked['baseRef']}",
            "sha": locked["baseSha"],
            "objectType": "commit",
            "objectApiUrl": (
                "https://api.github.com/repos/"
                f"{post_fix_lock['forkRepository']}/git/commits/"
                f"{locked['baseSha']}"
            ),
        }
        expected_final = {
            "apiPath": _ref_api_path(
                str(post_fix_lock["forkRepository"]),
                locked["finalRef"],
            ),
            "name": locked["finalRef"],
            "qualifiedName": f"refs/heads/{locked['finalRef']}",
            "sha": locked["finalSha"],
            "objectType": "commit",
            "objectApiUrl": (
                "https://api.github.com/repos/"
                f"{post_fix_lock['forkRepository']}/git/commits/"
                f"{locked['finalSha']}"
            ),
        }
        pull = item.get("pullRequest")
        expected_pull = {
            "apiPath": _pull_api_path(
                str(post_fix_lock["forkRepository"]),
                int(locked["forkPrNumber"]),
            ),
            "pullRequestId": (
                pull.get("pullRequestId")
                if isinstance(pull, Mapping)
                else None
            ),
            "nodeId": (
                pull.get("nodeId") if isinstance(pull, Mapping) else None
            ),
            "number": locked["forkPrNumber"],
            "htmlUrl": locked["forkPrUrl"],
            "state": (
                pull.get("state") if isinstance(pull, Mapping) else None
            ),
            "baseRepository": post_fix_lock["forkRepository"],
            "baseRef": locked["baseRef"],
            "baseSha": locked["baseSha"],
            "headRepository": post_fix_lock["forkRepository"],
            "headRef": locked["finalRef"],
            "headSha": locked["finalSha"],
        }
        if (
            item.get("baseRef") != expected_base
            or item.get("finalRef") != expected_final
            or not isinstance(pull, Mapping)
            or pull != expected_pull
            or pull.get("state") not in {"open", "closed"}
        ):
            raise ValueError(
                f"post-fix attestation cases[{index}] live identity drift"
            )
        _positive_integer(
            pull.get("pullRequestId"),
            f"post-fix attestation cases[{index}].pullRequestId",
        )
        require_text(
            pull.get("nodeId"),
            f"post-fix attestation cases[{index}].nodeId",
        )
    return digest


def _validated_primary_run(
    value: Any,
    corpus_digest: str,
) -> Mapping[str, Any]:
    digest = _artifact_digest(value, kind=RUN_KIND, field="runDigest")
    if (
        value.get("corpusDigest") != corpus_digest
        or value.get("status") != "completed"
        or not isinstance(value.get("cases"), list)
        or value.get("selectedCaseIds")
        != [str(item.get("caseId") or "") for item in value["cases"]]
    ):
        raise ValueError("paired primary H run is incomplete or corpus-drifted")
    if value.get("runDigest") != digest:
        raise AssertionError("unreachable digest drift")
    return value


def post_fix_cases_from_plan(
    corpus: Mapping[str, Any],
    plan_by_case: Mapping[str, Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    result = []
    for case in corpus["cases"]:
        case_id = str(case["caseId"])
        planned = plan_by_case[case_id]
        result.append(
            {
                "caseId": case_id,
                "partition": case["partition"],
                "sizeBand": case["sizeBand"],
                "snapshot": {
                    "baseSha": planned["baseSha"],
                    "headSha": planned["finalSha"],
                    "fileCount": planned["fileCount"],
                    "changedPaths": list(planned["changedPaths"]),
                    "diffSha256": planned["diffSha256"],
                },
                "replay": {
                    "baseRef": planned["baseRef"],
                    "headRef": planned["finalRef"],
                },
            }
        )
    return tuple(result)


def _registered_analysis_pair(
    registration: Mapping[str, Any],
    primary_run_id: str,
) -> Mapping[str, Any]:
    post_fix = registration.get("postFixPlan")
    pairs = (
        post_fix.get("analysisPairs")
        if isinstance(post_fix, Mapping)
        else None
    )
    matches = [
        item
        for item in pairs or []
        if isinstance(item, Mapping)
        and item.get("primaryAnalysisRunId") == primary_run_id
    ]
    if len(matches) != 1:
        raise ValueError(
            "registration has no unique post-fix analysis pair for H run"
        )
    return matches[0]


def run_post_fix_analysis(
    *,
    execution_corpus_path: Path,
    registration_path: Path,
    primary_replay_lock_path: Path,
    post_fix_replay_lock_path: Path,
    post_fix_replay_attestation_path: Path,
    primary_run_path: Path,
    repository: Path,
    output_dir: Path,
    config: Mapping[str, Any],
    resume: bool = False,
) -> dict[str, Any]:
    corpus = read_json(execution_corpus_path)
    registration = read_json(registration_path)
    primary_lock = read_json(primary_replay_lock_path)
    post_fix_lock = read_json(post_fix_replay_lock_path)
    attestation = read_json(post_fix_replay_attestation_path)
    primary_run = read_json(primary_run_path)
    if not all(
        isinstance(item, Mapping)
        for item in (
            corpus,
            registration,
            primary_lock,
            post_fix_lock,
            attestation,
            primary_run,
        )
    ):
        raise ValueError("post-fix analysis inputs must be JSON objects")
    summary = validate_execution_corpus(corpus)
    registration_payload = dict(registration)
    registration_digest = registration_payload.pop(
        "registrationDigest",
        None,
    )
    if (
        registration.get("kind")
        != "codecrow-magento2-study-registration"
        or registration_digest != sha256_json(registration_payload)
        or not isinstance(registration.get("corpus"), Mapping)
        or registration["corpus"].get("corpusId") != summary["corpusId"]
        or registration["corpus"].get("corpusDigest")
        != summary["corpusDigest"]
        or registration.get("executionCorpusDigest")
        != summary["executionCorpusDigest"]
    ):
        raise ValueError(
            "post-fix analysis registration/execution-corpus binding drift"
        )
    primary_run = _validated_primary_run(
        primary_run,
        summary["corpusDigest"],
    )
    pair = _registered_analysis_pair(
        registration,
        str(primary_run["runId"]),
    )
    plan_by_case = validate_post_fix_lock(
        post_fix_lock,
        corpus,
        primary_lock,
        repository=repository,
    )
    validate_post_fix_attestation(
        attestation,
        post_fix_lock,
        corpus,
        primary_lock,
    )
    post_fix_cases = post_fix_cases_from_plan(
        corpus,
        {
            case_id: post_fix_lock["plan"]["cases"][index]
            for index, case_id in enumerate(
                [str(case["caseId"]) for case in corpus["cases"]]
            )
        },
    )
    replay_by_case = {
        case_id: {
            "caseId": item["caseId"],
            "baseRef": item["baseRef"],
            "baseSha": item["baseSha"],
            "headRef": item["finalRef"],
            "headSha": item["finalSha"],
            "forkPrNumber": item["forkPrNumber"],
            "forkPrUrl": item["forkPrUrl"],
        }
        for case_id, item in plan_by_case.items()
    }
    primary_receipts = primary_run.get("indexReceiptsBefore")
    if (
        not isinstance(primary_receipts, Mapping)
        or primary_run.get("indexReceiptsAfter") != primary_receipts
    ):
        raise ValueError("paired H run has no stable exact B-index receipts")
    primary_roles = primary_run.get("analysisModelRoles")
    expected_response_model = (
        primary_roles.get("reviewPipelineExpectedResponse")
        if isinstance(primary_roles, Mapping)
        else None
    )
    require_text(
        expected_response_model,
        "primary H expected analysis response model",
    )
    pair_control = {
        "primaryAnalysisRunId": primary_run["runId"],
        "primaryAnalysisRunDigest": primary_run["runDigest"],
        "postFixAnalysisRunId": pair["postFixAnalysisRunId"],
        "sameBaseIndexRequired": True,
        "samePublicAnalysisConfigRequired": True,
        "sameRuntimeImagesRequired": True,
        "sameCaseOrderRequired": True,
        "snapshotTransition": "B_to_H_paired_with_B_to_verified_F",
    }
    pair_control["controlDigest"] = sha256_json(pair_control)
    context = AnalysisExecutionContext(
        run_kind=POST_FIX_RUN_KIND,
        cases=post_fix_cases,
        replay_lock=post_fix_lock,
        replay_by_case=replay_by_case,
        replay_attestation=attestation,
        replay_attestation_digest=str(attestation["attestationDigest"]),
        replay_lock_artifact="post-fix-replay-lock.json",
        replay_attestation_artifact=(
            "post-fix-replay-attestation.json"
        ),
        manifest_bindings={
            "snapshotRole": "verified_F",
            "pairedPrimaryRunId": primary_run["runId"],
            "pairedPrimaryRunDigest": primary_run["runDigest"],
            "primaryReplayLockDigest": primary_lock["lockDigest"],
            "postFixReplayPlanDigest": post_fix_lock["planDigest"],
            "analysisPairControl": pair_control,
            "analysisPairControlDigest": pair_control["controlDigest"],
        },
        run_id_prefix="m2f",
        required_analysis_config_digest=str(
            primary_run["analysisConfigDigest"]
        ),
        required_index_receipts=primary_receipts,
        required_runtime_images=runtime_image_projection(
            primary_run.get("runtimeProvenance")
        ),
    )
    result = run_analysis(
        execution_corpus_path=execution_corpus_path,
        replay_lock_path=None,
        repository=repository,
        output_dir=output_dir,
        config=config,
        run_id=str(pair["postFixAnalysisRunId"]),
        model=str(primary_run["analysisModel"]),
        expected_response_model=str(expected_response_model),
        selected_case_ids=None,
        limit=None,
        resume=resume,
        execution_context=context,
    )
    validate_analysis_pair(
        primary_run,
        result,
        corpus=corpus,
        post_fix_lock=post_fix_lock,
        primary_replay_lock=primary_lock,
        post_fix_attestation=attestation,
        post_fix_artifact_root=output_dir,
    )
    assert_label_free_execution_value(
        result,
        context="post-fix analysis run",
    )
    return result


def validate_analysis_pair(
    primary_run: Mapping[str, Any],
    post_fix_run: Mapping[str, Any],
    *,
    corpus: Mapping[str, Any],
    post_fix_lock: Mapping[str, Any],
    primary_replay_lock: Mapping[str, Any],
    post_fix_attestation: Mapping[str, Any],
    post_fix_artifact_root: Path,
) -> dict[str, Any]:
    summary = (
        validate_execution_corpus(corpus)
        if corpus.get("kind") == EXECUTION_CORPUS_KIND
        else validate_corpus(corpus, paper_ready=True, required_cases=50)
    )
    _validated_primary_run(primary_run, summary["corpusDigest"])
    digest = _artifact_digest(
        post_fix_run,
        kind=POST_FIX_RUN_KIND,
        field="runDigest",
    )
    expected_ids = [str(case["caseId"]) for case in corpus["cases"]]
    validate_post_fix_attestation(
        post_fix_attestation,
        post_fix_lock,
        corpus,
        primary_replay_lock,
    )
    expected_pair_control = {
        "primaryAnalysisRunId": primary_run["runId"],
        "primaryAnalysisRunDigest": primary_run["runDigest"],
        "postFixAnalysisRunId": post_fix_run["runId"],
        "sameBaseIndexRequired": True,
        "samePublicAnalysisConfigRequired": True,
        "sameRuntimeImagesRequired": True,
        "sameCaseOrderRequired": True,
        "snapshotTransition": "B_to_H_paired_with_B_to_verified_F",
    }
    expected_pair_control["controlDigest"] = sha256_json(
        expected_pair_control
    )
    execution_corpus_artifact = _post_fix_artifact(
        post_fix_artifact_root,
        post_fix_run.get("executionCorpusArtifact"),
        field="post-fix run execution corpus",
    )
    execution_summary = validate_execution_corpus(
        execution_corpus_artifact
    )
    if (
        post_fix_run.get("corpusDigest") != summary["corpusDigest"]
        or post_fix_run.get("snapshotRole") != "verified_F"
        or post_fix_run.get("pairedPrimaryRunId") != primary_run["runId"]
        or post_fix_run.get("pairedPrimaryRunDigest")
        != primary_run["runDigest"]
        or post_fix_run.get("status") != "completed"
        or post_fix_run.get("selectedCaseIds") != expected_ids
        or primary_run.get("selectedCaseIds") != expected_ids
        or post_fix_run.get("analysisModel")
        != primary_run.get("analysisModel")
        or post_fix_run.get("analysisProvider")
        != primary_run.get("analysisProvider")
        or post_fix_run.get("analysisConfig")
        != primary_run.get("analysisConfig")
        or post_fix_run.get("analysisConfigDigest")
        != primary_run.get("analysisConfigDigest")
        or post_fix_run.get("transport") != primary_run.get("transport")
        or post_fix_run.get("findingSemantics")
        != primary_run.get("findingSemantics")
        or post_fix_run.get("attemptPolicy")
        != primary_run.get("attemptPolicy")
        or runtime_image_projection(post_fix_run.get("runtimeProvenance"))
        != runtime_image_projection(primary_run.get("runtimeProvenance"))
        or post_fix_run.get("indexReceiptsBefore")
        != primary_run.get("indexReceiptsBefore")
        or post_fix_run.get("indexReceiptsAfter")
        != primary_run.get("indexReceiptsAfter")
        or post_fix_run.get("postFixReplayPlanDigest")
        != post_fix_lock.get("planDigest")
        or post_fix_run.get("replayLockDigest")
        != post_fix_lock.get("lockDigest")
        or post_fix_run.get("replayAttestationDigest")
        != post_fix_attestation.get("attestationDigest")
        or post_fix_run.get("primaryReplayLockDigest")
        != primary_replay_lock.get("lockDigest")
        or post_fix_run.get("analysisPairControl")
        != expected_pair_control
        or post_fix_run.get("analysisPairControlDigest")
        != expected_pair_control["controlDigest"]
        or post_fix_run.get("executionCorpusDigest")
        != execution_summary["executionCorpusDigest"]
        or post_fix_run.get("executionCorpusDigest")
        != primary_run.get("executionCorpusDigest")
        or execution_summary["corpusDigest"] != summary["corpusDigest"]
    ):
        raise ValueError("H/F analysis pair controls drift")
    assert_label_free_execution_value(
        post_fix_run,
        forbidden_values=(
            _post_fix_forbidden_values(corpus)
            if corpus.get("kind") != EXECUTION_CORPUS_KIND
            else ()
        ),
        context="post-fix analysis run",
    )
    if _post_fix_artifact(
        post_fix_artifact_root,
        post_fix_run.get("replayLockArtifact"),
        field="post-fix run replay lock",
    ) != post_fix_lock:
        raise ValueError("post-fix run replay lock artifact drift")
    if _post_fix_artifact(
        post_fix_artifact_root,
        post_fix_run.get("replayAttestationArtifact"),
        field="post-fix run replay attestation",
    ) != post_fix_attestation:
        raise ValueError("post-fix run replay attestation artifact drift")
    analysis_config = post_fix_run.get("analysisConfig")
    if not isinstance(analysis_config, Mapping):
        raise ValueError("post-fix run analysis configuration is invalid")
    validate_replay_attestation_freshness(
        post_fix_attestation,
        reference_at=post_fix_run.get("startedAt"),
        max_age_seconds=analysis_config.get(
            "replay_attestation_max_age_seconds",
            3_600,
        ),
    )
    h_cases = {
        str(case.get("caseId") or ""): case
        for case in primary_run.get("cases") or []
        if isinstance(case, Mapping)
    }
    f_cases = {
        str(case.get("caseId") or ""): case
        for case in post_fix_run.get("cases") or []
        if isinstance(case, Mapping)
    }
    if set(h_cases) != set(expected_ids) or set(f_cases) != set(expected_ids):
        raise ValueError("H/F analysis pair case coverage drift")
    return {
        "primaryAnalysisRunId": primary_run["runId"],
        "primaryAnalysisRunDigest": primary_run["runDigest"],
        "postFixAnalysisRunId": post_fix_run["runId"],
        "postFixAnalysisRunDigest": digest,
        "caseCount": len(expected_ids),
    }


def _utc_datetime(value: Any, field: str) -> datetime:
    text = require_text(value, field)
    if not text.endswith("Z"):
        raise ValueError(f"{field} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be a UTC timestamp") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field} must be a UTC timestamp")
    return parsed


def _validated_primary_judgment(
    value: Any,
    *,
    corpus_digest: str,
    primary_run: Mapping[str, Any],
) -> Mapping[str, Any]:
    from .judge import JUDGMENT_KIND

    _artifact_digest(value, kind=JUDGMENT_KIND, field="judgmentDigest")
    if (
        value.get("corpusDigest") != corpus_digest
        or value.get("analysisRunId") != primary_run.get("runId")
        or value.get("analysisRunDigest") != primary_run.get("runDigest")
        or not isinstance(value.get("cases"), list)
    ):
        raise ValueError(
            "paired primary H judgment is incomplete or run/corpus drifted"
        )
    return value


def _registered_judgment_pair(
    registration: Mapping[str, Any],
    primary_judgment_id: str,
) -> Mapping[str, Any]:
    post_fix = registration.get("postFixPlan")
    pairs = (
        post_fix.get("judgmentPairs")
        if isinstance(post_fix, Mapping)
        else None
    )
    matches = [
        item
        for item in pairs or []
        if isinstance(item, Mapping)
        and item.get("primaryJudgmentId") == primary_judgment_id
    ]
    if len(matches) != 1:
        raise ValueError(
            "registration has no unique post-fix judgment pair for H judgment"
        )
    return matches[0]


def _gold_projection(
    gold: Mapping[str, Any],
    *,
    gold_id: str,
) -> dict[str, Any]:
    return {
        "goldId": gold_id,
        "goldSourceId": gold["id"],
        "sourceCommentId": gold["sourceCommentId"],
        "path": gold["path"],
        "line": gold["originalLine"],
        "summary": gold["expectedIssue"]["summary"],
        "rootCause": gold["expectedIssue"].get("rootCause"),
        "failureMode": gold["expectedIssue"].get("failureMode"),
        "requiredChange": gold["expectedIssue"].get("requiredChange"),
        "fixCommitSha": gold["validity"]["fixCommitSha"],
        "decisionDigest": gold["adjudication"]["decisionDigest"],
        "pathTransitionDigest": gold["validity"]["pathTransition"][
            "diffSha256"
        ],
    }


def _gold_fix_bindings(
    corpus_case: Mapping[str, Any],
    planned: Mapping[str, Any],
    *,
    repository: Path | None,
) -> list[dict[str, Any]]:
    case_id = str(corpus_case["caseId"])
    review_head = str(planned["reviewHeadSha"])
    final_sha = str(planned["finalSha"])
    bindings = []
    for index, gold in enumerate(
        corpus_case["goldenComments"],
        start=1,
    ):
        validity = gold["validity"]
        fix_sha = require_full_sha(
            validity.get("fixCommitSha"),
            f"{case_id}.G{index:03d}.fixCommitSha",
        )
        if (
            validity.get("fixedLater") is not True
            or validity.get("disposition") != "fixed"
            or fix_sha == review_head
        ):
            raise ValueError(
                f"{case_id}.G{index:03d} has no strict verified fix"
            )
        transition = validity.get("pathTransition")
        if not isinstance(transition, Mapping):
            raise ValueError(
                f"{case_id}.G{index:03d} path transition is invalid"
            )
        if repository is not None:
            if (
                not _is_ancestor(repository, review_head, fix_sha)
                or not _is_ancestor(repository, fix_sha, final_sha)
            ):
                raise ValueError(
                    f"{case_id}.G{index:03d} fix commit is outside H..F"
                )
            observed, _ = resolve_path_transition(
                repository,
                checkpoint_sha=review_head,
                final_sha=final_sha,
                source_path=str(gold["path"]),
                git_env=hermetic_git_environment(offline=True),
            )
            if observed != transition:
                raise ValueError(
                    f"{case_id}.G{index:03d} H-to-F path transition drift"
                )
        bindings.append(
            {
                "goldId": f"G{index:03d}",
                "fixCommitSha": fix_sha,
                "fixEvidenceDigest": sha256_json(
                    validity.get("fixEvidence")
                ),
                "decisionDigest": gold["adjudication"]["decisionDigest"],
                "pathTransition": dict(transition),
            }
        )
    return bindings


def _candidate_projection(
    findings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "candidateId": f"C{index:03d}",
            **{
                key: value
                for key, value in finding.items()
                if key != "raw"
            },
        }
        for index, finding in enumerate(findings, start=1)
    ]


def _eligible_same_root_cause(edge: Mapping[str, Any]) -> bool:
    return (
        edge.get("verdict") == "substantive_match"
        and all(
            edge.get(field) == "yes"
            for field in (
                "specific_issue",
                "grounded_at_snapshot",
                "same_root_cause",
                "same_failure_or_consequence",
                "compatible_required_change",
            )
        )
        and edge.get("location_relation") not in {"unrelated", "unclear"}
    )


def _validate_post_fix_edge_rubric(
    edge: Any,
    *,
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(edge, Mapping):
        raise ValueError(f"{field} must be an object")
    for rubric_field in (
        "specific_issue",
        "grounded_at_snapshot",
        "same_root_cause",
        "same_failure_or_consequence",
        "compatible_required_change",
    ):
        if edge.get(rubric_field) not in YES_NO_UNCLEAR:
            raise ValueError(
                f"{field}.{rubric_field} has an invalid rubric value"
            )
    if edge.get("location_relation") not in LOCATION_RELATIONS:
        raise ValueError(f"{field}.location_relation is invalid")
    if edge.get("verdict") not in MATCH_VERDICTS:
        raise ValueError(f"{field}.verdict is invalid")
    confidence = edge.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= float(confidence) <= 1
    ):
        raise ValueError(f"{field}.confidence must be between zero and one")
    rationale = edge.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError(f"{field}.rationale must be non-empty")
    return edge


def _validate_post_fix_match_response(
    value: Any,
    *,
    gold_label: str,
    candidate_count: int,
) -> list[dict[str, Any]]:
    normalized = _validate_match_response(
        value,
        gold_label=gold_label,
        candidate_count=candidate_count,
    )
    expected_fields = {
        "candidate_id",
        "specific_issue",
        "grounded_at_snapshot",
        "same_root_cause",
        "same_failure_or_consequence",
        "compatible_required_change",
        "location_relation",
        "verdict",
        "confidence",
        "rationale",
    }
    for index, item in enumerate(normalized):
        if set(item) != expected_fields:
            raise ValueError(
                "post-fix judge response fields are invalid for "
                f"{gold_label}/candidate[{index}]"
            )
        _validate_post_fix_edge_rubric(
            item,
            field=f"{gold_label}/candidate[{index}]",
        )
    return normalized


def derive_post_fix_outcome(
    *,
    primary_matched: bool,
    candidate_ids: Sequence[str],
    edges: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not primary_matched:
        if edges:
            raise ValueError(
                "primary-unmatched gold must not consume post-fix judge calls"
            )
        return {
            "outcome": "not_applicable_primary_unmatched",
            "remainingCandidateIds": [],
            "unverifiableCandidateIds": [],
        }
    expected_pairs = set(candidate_ids)
    for index, edge in enumerate(edges):
        _validate_post_fix_edge_rubric(
            edge,
            field=f"post-fix edge[{index}]",
        )
    observed_pairs = {
        str(edge.get("candidateId") or "")
        for edge in edges
        if isinstance(edge, Mapping)
    }
    if observed_pairs != expected_pairs or len(edges) != len(expected_pairs):
        raise ValueError(
            "post-fix gold edges must cover every candidate exactly once"
        )
    remaining = sorted(
        str(edge["candidateId"])
        for edge in edges
        if _eligible_same_root_cause(edge)
    )
    unverifiable = sorted(
        str(edge["candidateId"])
        for edge in edges
        if (
            edge.get("verdict") in {"partial", "unverifiable"}
            or (
                edge.get("verdict") == "substantive_match"
                and not _eligible_same_root_cause(edge)
            )
        )
    )
    outcome = (
        "still_detected"
        if remaining
        else "unverifiable"
        if unverifiable
        else "disappeared"
    )
    return {
        "outcome": outcome,
        "remainingCandidateIds": remaining,
        "unverifiableCandidateIds": unverifiable,
    }


def _seal_has_post_fix_run(
    seal_ledger: Mapping[str, Any],
    post_fix_run: Mapping[str, Any],
) -> bool:
    return any(
        isinstance(item, Mapping)
        and item.get("runId") == post_fix_run.get("runId")
        and item.get("runDigest") == post_fix_run.get("runDigest")
        for item in seal_ledger.get("boundPostFixRuns") or []
    )


def judge_post_fix_run(
    *,
    corpus_path: Path,
    registration_path: Path,
    seal_ledger_path: Path,
    primary_run_path: Path,
    primary_judgment_path: Path,
    post_fix_run_path: Path,
    primary_replay_lock_path: Path,
    post_fix_replay_lock_path: Path,
    post_fix_replay_attestation_path: Path,
    registered_primary_run_paths: Sequence[Path],
    registered_post_fix_run_paths: Sequence[Path],
    repository: Path,
    output_dir: Path,
    config: Mapping[str, Any],
    client: Any = None,
) -> dict[str, Any]:
    from .protocol import validate_seal_ledger, validate_study_registration

    corpus = read_json(corpus_path)
    registration = read_json(registration_path)
    seal = read_json(seal_ledger_path)
    primary_run = read_json(primary_run_path)
    primary_judgment = read_json(primary_judgment_path)
    post_fix_run = read_json(post_fix_run_path)
    primary_lock = read_json(primary_replay_lock_path)
    post_fix_lock = read_json(post_fix_replay_lock_path)
    post_fix_attestation = read_json(post_fix_replay_attestation_path)
    registered_primary_runs = [
        read_json(path) for path in registered_primary_run_paths
    ]
    registered_post_fix_runs = [
        read_json(path) for path in registered_post_fix_run_paths
    ]
    if not all(
        isinstance(item, Mapping)
        for item in (
            corpus,
            registration,
            seal,
            primary_run,
            primary_judgment,
            post_fix_run,
            primary_lock,
            post_fix_lock,
            post_fix_attestation,
            *registered_primary_runs,
            *registered_post_fix_runs,
        )
    ):
        raise ValueError("post-fix judgment inputs must be JSON objects")
    summary = validate_corpus(corpus, paper_ready=True, required_cases=50)
    validate_study_registration(registration, corpus)
    if (
        primary_run not in registered_primary_runs
        or post_fix_run not in registered_post_fix_runs
    ):
        raise ValueError(
            "paired H/F runs must be present in the complete registered run set"
        )
    validate_seal_ledger(
        seal,
        registration,
        corpus,
        registered_primary_runs,
        registered_post_fix_runs,
    )
    primary_run = _validated_primary_run(
        primary_run,
        summary["corpusDigest"],
    )
    primary_judgment = _validated_primary_judgment(
        primary_judgment,
        corpus_digest=summary["corpusDigest"],
        primary_run=primary_run,
    )
    validate_analysis_pair(
        primary_run,
        post_fix_run,
        corpus=corpus,
        post_fix_lock=post_fix_lock,
        primary_replay_lock=primary_lock,
        post_fix_attestation=post_fix_attestation,
        post_fix_artifact_root=post_fix_run_path.resolve().parent,
    )
    pair = _registered_judgment_pair(
        registration,
        str(primary_judgment["judgmentId"]),
    )
    if (
        pair.get("primaryAnalysisRunId") != primary_run["runId"]
        or pair.get("postFixAnalysisRunId") != post_fix_run["runId"]
        or not _seal_has_post_fix_run(seal, post_fix_run)
    ):
        raise ValueError(
            "post-fix judgment pair is not registration/seal bound"
        )
    unseal = seal.get("unseal")
    if not isinstance(unseal, Mapping):
        raise ValueError("seal ledger has no unseal evidence")
    unsealed_at = _utc_datetime(unseal.get("at"), "seal unseal.at")
    primary_created_at = _utc_datetime(
        primary_judgment.get("createdAt"),
        "primary judgment createdAt",
    )
    if primary_created_at < unsealed_at:
        raise ValueError("paired primary H judgment predates label unseal")
    # Local import avoids metrics -> postfix module initialization cycles.
    from .metrics import _paper_judgment_failures

    primary_failures = _paper_judgment_failures(
        primary_judgment,
        corpus_cases={
            str(item["caseId"]): item for item in corpus["cases"]
        },
        analysis_run=primary_run,
        artifact_root=primary_judgment_path.resolve().parent,
        repository=repository,
        require_source_reconstruction=True,
    )
    if primary_failures:
        raise ValueError(
            "paired primary H judgment failed semantic reconstruction: "
            + ", ".join(primary_failures)
        )
    _validate_primary_checkpoint_times(
        primary_judgment,
        artifact_root=primary_judgment_path.resolve().parent,
        unsealed_at=unsealed_at,
        created_at=primary_created_at,
    )
    judging_started_at = _now()
    if (
        _utc_datetime(
            judging_started_at,
            "post-fix judging startedAt",
        )
        < unsealed_at
    ):
        raise ValueError("post-fix judging cannot occur before label unseal")

    judge_config, expected_response_model = _resolved_judge_config(
        config,
        model=None,
        expected_response_model=pair.get("expectedResponseModel"),
    )
    repeats = int(judge_config.get("repeats") or 1)
    if repeats < 1 or repeats % 2 == 0:
        raise ValueError("judge.repeats must be a positive odd integer")
    max_prompt_characters = int(
        judge_config.get("max_prompt_characters") or 400_000
    )
    if max_prompt_characters < 10_000:
        raise ValueError("judge.max_prompt_characters must be >= 10000")
    max_structured_retries = int(
        judge_config.get("max_structured_retries") or 3
    )
    if max_structured_retries < 1:
        raise ValueError("judge.max_structured_retries must be >= 1")
    public_judge_config = public_config(judge_config)
    judge_config_digest = sha256_json(public_judge_config)
    if (
        public_judge_config != primary_judgment.get("judgeConfig")
        or judge_config_digest
        != primary_judgment.get("judgeConfigDigest")
        or judge_config.get("model") != primary_judgment.get("judgeModel")
        or pair.get("promptVersion") != POST_FIX_PROMPT_VERSION
        or pair.get("promptDigest") != POST_FIX_PROMPT_DIGEST
    ):
        raise ValueError(
            "post-fix judge model/config/prompt differs from registration "
            "or its paired H judgment"
        )
    judge_client = client or OpenAICompatibleJudge(judge_config)
    if not repository.is_dir() or not (repository / ".git").exists():
        raise ValueError("judge repository must be a local Git clone")
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    checkpoints_dir = output_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)

    plan_by_case = {
        str(item["caseId"]): item for item in post_fix_lock["plan"]["cases"]
    }
    f_cases = {
        str(item["caseId"]): item
        for item in post_fix_run.get("cases") or []
        if isinstance(item, Mapping)
    }
    h_judgment_cases = {
        str(item["caseId"]): item
        for item in primary_judgment.get("cases") or []
        if isinstance(item, Mapping)
    }
    derived_cases = {
        str(item["caseId"]): item
        for item in post_fix_cases_from_plan(corpus, plan_by_case)
    }
    results = []
    for corpus_case in corpus["cases"]:
        case_id = str(corpus_case["caseId"])
        f_case = f_cases.get(case_id)
        h_case = h_judgment_cases.get(case_id)
        if (
            not isinstance(f_case, Mapping)
            or f_case.get("status") != "completed"
            or not isinstance(h_case, Mapping)
            or h_case.get("status") != "scored"
        ):
            raise ValueError(
                f"post-fix judgment requires completed paired H/F case {case_id}"
            )
        case_input_digest = sha256_json(
            {
                "corpusCase": corpus_case,
                "primaryJudgmentCaseDigest": h_case.get("caseDigest"),
                "postFixAnalysisCase": f_case,
                "primaryJudgmentDigest": primary_judgment[
                    "judgmentDigest"
                ],
                "postFixAnalysisRunDigest": post_fix_run["runDigest"],
                "judgeConfigDigest": judge_config_digest,
                "promptVersion": POST_FIX_PROMPT_VERSION,
                "promptDigest": POST_FIX_PROMPT_DIGEST,
            }
        )
        raw_path = raw_dir / f"{case_id}.json"
        if raw_path.exists():
            cached = read_json(raw_path)
            if not isinstance(cached, Mapping):
                raise ValueError(f"invalid post-fix checkpoint {raw_path}")
            payload = dict(cached)
            declared = payload.pop("caseDigest", None)
            if (
                declared != sha256_json(payload)
                or cached.get("caseInputDigest") != case_input_digest
            ):
                raise ValueError(f"stale post-fix checkpoint for {case_id}")
            resumed = dict(cached)
            resumed["rawJudgment"] = str(raw_path.relative_to(output_dir))
            results.append(resumed)
            continue
        derived_case = derived_cases[case_id]
        _validate_local_snapshot(repository, derived_case)
        gold_fix_bindings = _gold_fix_bindings(
            corpus_case,
            plan_by_case[case_id],
            repository=repository,
        )
        findings = list(f_case.get("findings") or [])
        if any(not isinstance(item, Mapping) for item in findings):
            raise ValueError(f"post-fix findings are invalid for {case_id}")
        evidence = _candidate_evidence(
            repository,
            derived_case,
            findings,
        )
        candidate_findings = _candidate_projection(findings)
        candidate_ids = [
            str(item["candidateId"]) for item in candidate_findings
        ]
        primary_matches = {
            str(item.get("goldId") or "")
            for item in h_case.get("assignments") or []
            if isinstance(item, Mapping)
        }
        gold_results = []
        call_records = []
        for gold_index, gold in enumerate(
            corpus_case["goldenComments"],
            start=1,
        ):
            gold_id = f"G{gold_index:03d}"
            primary_matched = gold_id in primary_matches
            edges: list[dict[str, Any]] = []
            if primary_matched and findings:
                per_candidate = {
                    candidate_id: [] for candidate_id in candidate_ids
                }
                for repeat_index in range(repeats):
                    prompt = _gold_prompt(
                        gold_label=gold_id,
                        gold=gold,
                        findings=findings,
                        candidate_evidence=evidence,
                        max_prompt_characters=max_prompt_characters,
                    )
                    binding = {
                        "kind": "post_fix_pair",
                        "caseId": case_id,
                        "goldId": gold_id,
                        "repeat": repeat_index + 1,
                        "caseInputDigest": case_input_digest,
                        "judgeConfigDigest": judge_config_digest,
                        "promptVersion": POST_FIX_PROMPT_VERSION,
                    }
                    checkpoint_name = (
                        "post-fix-"
                        f"{gold_id}-{repeat_index + 1}-"
                        f"{sha256_text(prompt)[:20]}.json"
                    )
                    normalized, call_record = _validated_judge_call(
                        judge_client=judge_client,
                        system=POST_FIX_SYSTEM,
                        prompt=prompt,
                        validator=lambda value: _validate_post_fix_match_response(
                            value,
                            gold_label=gold_id,
                            candidate_count=len(findings),
                        ),
                        checkpoint_path=(
                            checkpoints_dir
                            / case_id
                            / checkpoint_name
                        ),
                        binding=binding,
                        max_structured_retries=max_structured_retries,
                        expected_response_model=expected_response_model,
                    )
                    for item in normalized:
                        per_candidate[str(item["candidate_id"])].append(
                            dict(item)
                        )
                    call_records.append(
                        {
                            "kind": "post_fix_pair",
                            "goldId": gold_id,
                            "repeat": repeat_index + 1,
                            "checkpoint": str(
                                Path("checkpoints")
                                / case_id
                                / checkpoint_name
                            ),
                            **{
                                key: value
                                for key, value in call_record.items()
                                if key != "metadata"
                            },
                            **dict(call_record.get("metadata") or {}),
                        }
                    )
                for candidate_id, values in per_candidate.items():
                    edges.append(
                        {
                            "goldId": gold_id,
                            "candidateId": candidate_id,
                            **_majority_match(values),
                        }
                    )
            derived = derive_post_fix_outcome(
                primary_matched=primary_matched,
                candidate_ids=candidate_ids,
                edges=edges,
            )
            gold_results.append(
                {
                    **_gold_projection(gold, gold_id=gold_id),
                    "primaryMatchedAtH": primary_matched,
                    "edges": edges,
                    **derived,
                }
            )
        case_result = {
            "caseId": case_id,
            "caseInputDigest": case_input_digest,
            "judgeConfigDigest": judge_config_digest,
            "status": "scored",
            "sizeBand": corpus_case["sizeBand"],
            "partition": corpus_case["partition"],
            "postFixSnapshot": {
                "baseSha": plan_by_case[case_id]["baseSha"],
                "reviewHeadSha": plan_by_case[case_id]["reviewHeadSha"],
                "finalSha": plan_by_case[case_id]["finalSha"],
                "diffSha256": plan_by_case[case_id]["diffSha256"],
                "goldFixBindings": gold_fix_bindings,
                "goldFixBindingsDigest": sha256_json(gold_fix_bindings),
            },
            "candidateFindings": candidate_findings,
            "goldOutcomes": gold_results,
            "calls": call_records,
        }
        case_result["caseDigest"] = sha256_json(case_result)
        write_json(raw_path, case_result)
        case_result["rawJudgment"] = str(raw_path.relative_to(output_dir))
        results.append(case_result)
    result = {
        "kind": POST_FIX_JUDGMENT_KIND,
        "judgmentId": pair["postFixJudgmentId"],
        "createdAt": _now(),
        "registrationDigest": registration["registrationDigest"],
        "sealLedgerDigest": seal["sealLedgerDigest"],
        "unsealedAt": unseal["at"],
        "promptVersion": POST_FIX_PROMPT_VERSION,
        "promptDigest": POST_FIX_PROMPT_DIGEST,
        "corpusId": summary["corpusId"],
        "corpusDigest": summary["corpusDigest"],
        "primaryAnalysisRunId": primary_run["runId"],
        "primaryAnalysisRunDigest": primary_run["runDigest"],
        "primaryJudgmentId": primary_judgment["judgmentId"],
        "primaryJudgmentDigest": primary_judgment["judgmentDigest"],
        "postFixAnalysisRunId": post_fix_run["runId"],
        "postFixAnalysisRunDigest": post_fix_run["runDigest"],
        "postFixReplayLockDigest": post_fix_lock["lockDigest"],
        "analysisModel": post_fix_run["analysisModel"],
        "judgeModel": judge_config["model"],
        "judgeExpectedResponseModel": expected_response_model,
        "judgeConfig": public_judge_config,
        "judgeConfigDigest": judge_config_digest,
        "cases": results,
    }
    result["judgmentDigest"] = sha256_json(result)
    validate_post_fix_judgment(
        result,
        corpus=corpus,
        registration=registration,
        seal_ledger=seal,
        primary_run=primary_run,
        primary_judgment=primary_judgment,
        post_fix_run=post_fix_run,
        primary_replay_lock=primary_lock,
        post_fix_lock=post_fix_lock,
        post_fix_attestation=post_fix_attestation,
        post_fix_run_artifact_root=post_fix_run_path.resolve().parent,
        artifact_root=output_dir,
        repository=repository,
    )
    write_json(output_dir / "post-fix-judgment.json", result)
    return result


def _post_fix_artifact(
    artifact_root: Path,
    relative_name: Any,
    *,
    field: str,
) -> Mapping[str, Any]:
    if (
        not artifact_root.is_dir()
        or artifact_root.is_symlink()
        or not isinstance(relative_name, str)
        or not relative_name
    ):
        raise ValueError(f"{field} artifact root/path is invalid")
    relative = Path(relative_name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{field} artifact path is unsafe")
    root = artifact_root.resolve()
    unresolved = root
    for part in relative.parts:
        unresolved = unresolved / part
        if unresolved.is_symlink():
            raise ValueError(f"{field} artifact path contains a symlink")
    path = unresolved.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{field} artifact path escapes its root") from exc
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{field} artifact is missing or not a regular file")
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} artifact must be a JSON object")
    return value


def _validate_primary_checkpoint_times(
    judgment: Mapping[str, Any],
    *,
    artifact_root: Path,
    unsealed_at: datetime,
    created_at: datetime,
) -> None:
    for case in judgment.get("cases") or []:
        if not isinstance(case, Mapping) or case.get("status") != "scored":
            continue
        for call in case.get("calls") or []:
            if not isinstance(call, Mapping):
                raise ValueError("primary judgment call ledger is invalid")
            checkpoint = _post_fix_artifact(
                artifact_root,
                call.get("checkpoint"),
                field="primary judgment checkpoint",
            )
            completed_at = _utc_datetime(
                checkpoint.get("completedAt"),
                "primary judgment checkpoint completedAt",
            )
            if completed_at < unsealed_at or completed_at > created_at:
                raise ValueError(
                    "primary judgment checkpoint completion is outside "
                    "unseal/judgment time"
                )


def _post_fix_provider_content(value: Mapping[str, Any]) -> str:
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("post-fix provider response has no choice")
    first = choices[0]
    message = first.get("message") if isinstance(first, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if isinstance(content, list):
        if any(not isinstance(item, Mapping) for item in content):
            raise ValueError("post-fix provider response content is invalid")
        content = "".join(str(item.get("text") or "") for item in content)
    if not isinstance(content, str):
        raise ValueError("post-fix provider response has no text content")
    return content


def _post_fix_expected_request(
    judge_config: Mapping[str, Any],
    *,
    system: str,
    prompt: str,
) -> dict[str, Any]:
    model = require_text(judge_config.get("model"), "post-fix judge model")
    custom = judge_config.get("custom_parameters")
    if custom is not None and not isinstance(custom, Mapping):
        raise ValueError("post-fix judge custom_parameters must be an object")
    reserved = {"model", "messages", "response_format", "temperature"}
    if reserved.intersection(custom or {}):
        raise ValueError(
            "post-fix judge custom parameters override reserved fields"
        )
    try:
        temperature = float(judge_config.get("temperature") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("post-fix judge temperature is invalid") from exc
    request: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    request.update(dict(custom or {}))
    return request


def _validate_post_fix_provider_metadata(
    metadata: Any,
    *,
    judge_config: Mapping[str, Any],
    expected_response_model: str,
    system: str,
    prompt: str,
    response_value: Any,
    field: str,
) -> Mapping[str, Any]:
    expected_fields = {
        "usage",
        "responseId",
        "model",
        "promptSha256",
        "rawContentSha256",
        "request",
        "requestSha256",
        "providerResponse",
        "providerResponseSha256",
    }
    if not isinstance(metadata, Mapping) or set(metadata) != expected_fields:
        raise ValueError(f"{field} provider metadata fields are invalid")
    request = _post_fix_expected_request(
        judge_config,
        system=system,
        prompt=prompt,
    )
    if (
        metadata.get("request") != request
        or metadata.get("requestSha256") != sha256_json(request)
    ):
        raise ValueError(f"{field} provider request binding drift")
    provider_response = metadata.get("providerResponse")
    if (
        not isinstance(provider_response, Mapping)
        or metadata.get("providerResponseSha256")
        != sha256_json(provider_response)
    ):
        raise ValueError(f"{field} provider response digest drift")
    content = _post_fix_provider_content(provider_response)
    if (
        metadata.get("rawContentSha256") != sha256_text(content)
        or _extract_json(content) != response_value
    ):
        raise ValueError(f"{field} provider response content drift")
    response_id = metadata.get("responseId")
    if (
        not isinstance(response_id, str)
        or not response_id.strip()
        or provider_response.get("id") != response_id
    ):
        raise ValueError(f"{field} provider response ID drift")
    if (
        metadata.get("model") != expected_response_model
        or provider_response.get("model") != expected_response_model
    ):
        raise ValueError(f"{field} provider response model drift")
    if (
        metadata.get("usage") != provider_response.get("usage")
        or metadata.get("promptSha256")
        != sha256_text(system + "\n" + prompt)
    ):
        raise ValueError(f"{field} provider usage/prompt binding drift")
    return metadata


def _post_fix_prompt_evidence(
    prompt: Any,
    *,
    gold_id: str,
    gold: Mapping[str, Any],
    findings: list[Mapping[str, Any]],
    changed_paths: set[str],
    maximum: int,
    field: str,
) -> list[Mapping[str, Any]]:
    marker = "\n\nOUTPUT SCHEMA:\n"
    if (
        not isinstance(prompt, str)
        or len(prompt) > maximum
        or not prompt.startswith("INPUT:\n")
        or marker not in prompt
    ):
        raise ValueError(f"{field} prompt format/limit is invalid")
    input_text, _ = prompt[len("INPUT:\n") :].split(marker, 1)
    try:
        input_value = json.loads(input_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} prompt input is invalid") from exc
    candidates = (
        input_value.get("candidates")
        if isinstance(input_value, Mapping)
        else None
    )
    if (
        not isinstance(candidates, list)
        or len(candidates) != len(findings)
        or any(not isinstance(item, Mapping) for item in candidates)
    ):
        raise ValueError(f"{field} prompt candidate universe is invalid")
    evidence = [item.get("frozen_evidence") for item in candidates]
    if any(not isinstance(item, Mapping) for item in evidence):
        raise ValueError(f"{field} prompt frozen evidence is invalid")
    expected_prompt = _gold_prompt(
        gold_label=gold_id,
        gold=gold,
        findings=findings,
        candidate_evidence=list(evidence),
        max_prompt_characters=maximum,
    )
    if prompt != expected_prompt:
        raise ValueError(f"{field} prompt content binding drift")
    for index, (finding, item) in enumerate(
        zip(findings, evidence, strict=True),
        start=1,
    ):
        path = finding.get("path")
        line = finding.get("line")
        path_diff = item.get("pathDiff")
        source_window = item.get("headSourceWindow")
        for digest_field in ("pathDiffSha256", "headSourceSha256"):
            _require_sha256(
                item.get(digest_field),
                f"{field}.candidate[{index}].{digest_field}",
            )
        if (
            item.get("inFrozenDiff")
            != (isinstance(path, str) and path in changed_paths)
            or not isinstance(path_diff, str)
            or not isinstance(source_window, str)
        ):
            raise ValueError(f"{field} prompt source evidence drift")
        if "[truncated " not in path_diff:
            if (
                item.get("pathDiffSha256") != sha256_text(path_diff)
                or item.get("lineOnAddedRightSide")
                != (
                    isinstance(line, int)
                    and not isinstance(line, bool)
                    and line in _right_added_lines(path_diff)
                )
            ):
                raise ValueError(f"{field} prompt path evidence drift")
    return list(evidence)


def _validate_post_fix_call(
    call: Any,
    *,
    artifact_root: Path,
    case_id: str,
    gold_id: str,
    repeat: int,
    case_input_digest: str,
    judge_config: Mapping[str, Any],
    judge_config_digest: str,
    expected_response_model: str,
    max_structured_retries: int,
    findings: list[Mapping[str, Any]],
    gold: Mapping[str, Any],
    changed_paths: set[str],
    max_prompt_characters: int,
    unsealed_at: datetime,
    judgment_created_at: datetime,
) -> list[dict[str, Any]]:
    field = f"{case_id}/{gold_id}/repeat-{repeat}"
    if not isinstance(call, Mapping):
        raise ValueError(f"{field} call is missing")
    checkpoint = _post_fix_artifact(
        artifact_root,
        call.get("checkpoint"),
        field=f"{field} checkpoint",
    )
    expected_checkpoint_fields = {
        "bindingDigest",
        "completedAt",
        "system",
        "prompt",
        "response",
        "metadata",
        "rejectedStructuredResponses",
        "callDigest",
    }
    if set(checkpoint) != expected_checkpoint_fields:
        raise ValueError(f"{field} checkpoint fields are invalid")
    checkpoint_payload = dict(checkpoint)
    declared = checkpoint_payload.pop("callDigest")
    if declared != sha256_json(checkpoint_payload):
        raise ValueError(f"{field} checkpoint digest drift")
    completed_at = _utc_datetime(
        checkpoint.get("completedAt"),
        f"{field} completedAt",
    )
    if completed_at < unsealed_at or completed_at > judgment_created_at:
        raise ValueError(
            f"{field} checkpoint completion is outside unseal/judgment time"
        )
    system = checkpoint.get("system")
    prompt = checkpoint.get("prompt")
    if system != POST_FIX_SYSTEM:
        raise ValueError(f"{field} checkpoint system prompt drift")
    _post_fix_prompt_evidence(
        prompt,
        gold_id=gold_id,
        gold=gold,
        findings=findings,
        changed_paths=changed_paths,
        maximum=max_prompt_characters,
        field=field,
    )
    expected_checkpoint = str(
        Path("checkpoints")
        / case_id
        / (
            f"post-fix-{gold_id}-{repeat}-"
            f"{sha256_text(str(prompt))[:20]}.json"
        )
    )
    if call.get("checkpoint") != expected_checkpoint:
        raise ValueError(f"{field} checkpoint path binding drift")
    binding = {
        "kind": "post_fix_pair",
        "caseId": case_id,
        "goldId": gold_id,
        "repeat": repeat,
        "caseInputDigest": case_input_digest,
        "judgeConfigDigest": judge_config_digest,
        "promptVersion": POST_FIX_PROMPT_VERSION,
    }
    expected_binding_digest = sha256_json(
        {
            **binding,
            "systemSha256": sha256_text(POST_FIX_SYSTEM),
            "promptSha256": sha256_text(str(prompt)),
        }
    )
    if checkpoint.get("bindingDigest") != expected_binding_digest:
        raise ValueError(f"{field} checkpoint binding drift")
    response = checkpoint.get("response")
    normalized = _validate_post_fix_match_response(
        response,
        gold_label=gold_id,
        candidate_count=len(findings),
    )
    metadata = _validate_post_fix_provider_metadata(
        checkpoint.get("metadata"),
        judge_config=judge_config,
        expected_response_model=expected_response_model,
        system=POST_FIX_SYSTEM,
        prompt=str(prompt),
        response_value=response,
        field=field,
    )
    rejected = checkpoint.get("rejectedStructuredResponses")
    if (
        not isinstance(rejected, list)
        or len(rejected) >= max_structured_retries
    ):
        raise ValueError(f"{field} rejected response ledger is invalid")
    for index, item in enumerate(rejected, start=1):
        if (
            not isinstance(item, Mapping)
            or set(item)
            != {
                "attempt",
                "response",
                "metadata",
                "validationError",
            }
            or item.get("attempt") != index
            or not isinstance(item.get("validationError"), str)
            or not item.get("validationError")
        ):
            raise ValueError(f"{field} rejected response record is invalid")
        try:
            _validate_post_fix_match_response(
                item.get("response"),
                gold_label=gold_id,
                candidate_count=len(findings),
            )
        except (TypeError, ValueError):
            pass
        else:
            raise ValueError(f"{field} rejected response was valid")
        _validate_post_fix_provider_metadata(
            item.get("metadata"),
            judge_config=judge_config,
            expected_response_model=expected_response_model,
            system=POST_FIX_SYSTEM,
            prompt=str(prompt),
            response_value=item.get("response"),
            field=f"{field}/rejected-{index}",
        )
    metadata_value = dict(metadata)
    if set(metadata_value).intersection(
        {
            "kind",
            "goldId",
            "repeat",
            "checkpoint",
            *expected_checkpoint_fields,
        }
    ):
        raise ValueError(f"{field} provider metadata field collision")
    expected_call = {
        "kind": "post_fix_pair",
        "goldId": gold_id,
        "repeat": repeat,
        "checkpoint": expected_checkpoint,
        **{
            key: item
            for key, item in checkpoint.items()
            if key != "metadata"
        },
        **metadata_value,
    }
    if dict(call) != expected_call:
        raise ValueError(f"{field} call/checkpoint projection drift")
    return normalized


def validate_post_fix_judgment(
    value: Any,
    *,
    corpus: Mapping[str, Any],
    registration: Mapping[str, Any],
    seal_ledger: Mapping[str, Any],
    primary_run: Mapping[str, Any],
    primary_judgment: Mapping[str, Any],
    post_fix_run: Mapping[str, Any],
    primary_replay_lock: Mapping[str, Any],
    post_fix_lock: Mapping[str, Any],
    post_fix_attestation: Mapping[str, Any],
    post_fix_run_artifact_root: Path,
    artifact_root: Path,
    repository: Path | None = None,
) -> dict[str, Any]:
    summary = validate_corpus(corpus, paper_ready=True, required_cases=50)
    _validated_primary_run(primary_run, summary["corpusDigest"])
    _validated_primary_judgment(
        primary_judgment,
        corpus_digest=summary["corpusDigest"],
        primary_run=primary_run,
    )
    validate_analysis_pair(
        primary_run,
        post_fix_run,
        corpus=corpus,
        post_fix_lock=post_fix_lock,
        primary_replay_lock=primary_replay_lock,
        post_fix_attestation=post_fix_attestation,
        post_fix_artifact_root=post_fix_run_artifact_root,
    )
    digest = _artifact_digest(
        value,
        kind=POST_FIX_JUDGMENT_KIND,
        field="judgmentDigest",
    )
    judgment_fields = {
        "kind",
        "judgmentId",
        "createdAt",
        "registrationDigest",
        "sealLedgerDigest",
        "unsealedAt",
        "promptVersion",
        "promptDigest",
        "corpusId",
        "corpusDigest",
        "primaryAnalysisRunId",
        "primaryAnalysisRunDigest",
        "primaryJudgmentId",
        "primaryJudgmentDigest",
        "postFixAnalysisRunId",
        "postFixAnalysisRunDigest",
        "postFixReplayLockDigest",
        "analysisModel",
        "judgeModel",
        "judgeExpectedResponseModel",
        "judgeConfig",
        "judgeConfigDigest",
        "cases",
        "judgmentDigest",
    }
    if not isinstance(value, Mapping) or set(value) != judgment_fields:
        raise ValueError("post-fix judgment fields are invalid")
    pair = _registered_judgment_pair(
        registration,
        str(primary_judgment["judgmentId"]),
    )
    unseal = seal_ledger.get("unseal")
    if not isinstance(unseal, Mapping):
        raise ValueError("seal ledger has no unseal evidence")
    judge_config = value.get("judgeConfig")
    if not isinstance(judge_config, Mapping):
        raise ValueError("post-fix judge configuration is invalid")
    try:
        repeats = int(judge_config.get("repeats") or 1)
        max_prompt_characters = int(
            judge_config.get("max_prompt_characters") or 400_000
        )
        max_structured_retries = int(
            judge_config.get("max_structured_retries") or 3
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("post-fix judge numeric policy is invalid") from exc
    expected_response_model = (
        judge_config.get("expected_response_model")
        or judge_config.get("model")
    )
    if (
        repeats < 1
        or repeats % 2 == 0
        or max_prompt_characters < 10_000
        or max_structured_retries < 1
        or value.get("judgeConfigDigest") != sha256_json(judge_config)
        or value.get("judgeModel") != judge_config.get("model")
        or value.get("judgeExpectedResponseModel")
        != expected_response_model
        or pair.get("expectedResponseModel") != expected_response_model
    ):
        raise ValueError("post-fix judge policy/model binding drift")
    if (
        value.get("judgmentId") != pair.get("postFixJudgmentId")
        or value.get("registrationDigest")
        != registration.get("registrationDigest")
        or value.get("sealLedgerDigest")
        != seal_ledger.get("sealLedgerDigest")
        or value.get("unsealedAt") != unseal.get("at")
        or value.get("corpusId") != summary["corpusId"]
        or value.get("corpusDigest") != summary["corpusDigest"]
        or value.get("primaryAnalysisRunId") != primary_run["runId"]
        or value.get("primaryAnalysisRunDigest") != primary_run["runDigest"]
        or value.get("primaryJudgmentId")
        != primary_judgment["judgmentId"]
        or value.get("primaryJudgmentDigest")
        != primary_judgment["judgmentDigest"]
        or value.get("postFixAnalysisRunId") != post_fix_run["runId"]
        or value.get("postFixAnalysisRunDigest")
        != post_fix_run["runDigest"]
        or value.get("postFixReplayLockDigest")
        != post_fix_lock["lockDigest"]
        or value.get("analysisModel") != post_fix_run.get("analysisModel")
        or value.get("promptVersion") != POST_FIX_PROMPT_VERSION
        or value.get("promptDigest") != POST_FIX_PROMPT_DIGEST
        or value.get("judgeModel") != primary_judgment.get("judgeModel")
        or value.get("judgeConfig") != primary_judgment.get("judgeConfig")
        or value.get("judgeConfigDigest")
        != primary_judgment.get("judgeConfigDigest")
        or not _seal_has_post_fix_run(seal_ledger, post_fix_run)
    ):
        raise ValueError("post-fix judgment identity/control binding drift")
    judgment_created_at = _utc_datetime(
        value.get("createdAt"),
        "post-fix judgment createdAt",
    )
    unsealed_at = _utc_datetime(unseal.get("at"), "seal unseal.at")
    if judgment_created_at < unsealed_at:
        raise ValueError("post-fix judgment predates label unseal")
    cases = value.get("cases")
    if not isinstance(cases, list):
        raise ValueError("post-fix judgment cases must be an array")
    expected_ids = [str(case["caseId"]) for case in corpus["cases"]]
    if [
        str(item.get("caseId") or "") if isinstance(item, Mapping) else ""
        for item in cases
    ] != expected_ids:
        raise ValueError("post-fix judgment case order/coverage drift")
    f_cases = {
        str(item["caseId"]): item for item in post_fix_run["cases"]
    }
    h_cases = {
        str(item["caseId"]): item for item in primary_judgment["cases"]
    }
    plan_cases = {
        str(item["caseId"]): item for item in post_fix_lock["plan"]["cases"]
    }
    counts = Counter()
    for case_index, (case_result, corpus_case) in enumerate(
        zip(cases, corpus["cases"], strict=True)
    ):
        if not isinstance(case_result, Mapping):
            raise ValueError(
                f"post-fix judgment cases[{case_index}] is invalid"
            )
        case_id = str(corpus_case["caseId"])
        raw_name = case_result.get("rawJudgment")
        raw_value = _post_fix_artifact(
            artifact_root,
            raw_name,
            field=f"{case_id} raw judgment",
        )
        f_findings = list(f_cases[case_id].get("findings") or [])
        if any(not isinstance(item, Mapping) for item in f_findings):
            raise ValueError(f"post-fix findings are invalid for {case_id}")
        planned = plan_cases[case_id]
        h_case = h_cases[case_id]
        case_input_digest = sha256_json(
            {
                "corpusCase": corpus_case,
                "primaryJudgmentCaseDigest": h_case.get("caseDigest"),
                "postFixAnalysisCase": f_cases[case_id],
                "primaryJudgmentDigest": primary_judgment[
                    "judgmentDigest"
                ],
                "postFixAnalysisRunDigest": post_fix_run["runDigest"],
                "judgeConfigDigest": value["judgeConfigDigest"],
                "promptVersion": POST_FIX_PROMPT_VERSION,
                "promptDigest": POST_FIX_PROMPT_DIGEST,
            }
        )
        expected_snapshot = {
            "baseSha": planned["baseSha"],
            "reviewHeadSha": planned["reviewHeadSha"],
            "finalSha": planned["finalSha"],
            "diffSha256": planned["diffSha256"],
        }
        gold_fix_bindings = _gold_fix_bindings(
            corpus_case,
            planned,
            repository=repository,
        )
        expected_snapshot.update(
            {
                "goldFixBindings": gold_fix_bindings,
                "goldFixBindingsDigest": sha256_json(gold_fix_bindings),
            }
        )
        primary_matches = {
            str(item.get("goldId") or "")
            for item in h_case.get("assignments") or []
            if isinstance(item, Mapping)
        }
        candidate_ids = [
            f"C{index:03d}"
            for index in range(1, len(f_findings) + 1)
        ]
        calls_value = raw_value.get("calls")
        if not isinstance(calls_value, list) or any(
            not isinstance(item, Mapping) for item in calls_value
        ):
            raise ValueError(f"post-fix calls are invalid for {case_id}")
        calls = list(calls_value)
        observed_keys = [
            (
                str(item.get("kind") or ""),
                str(item.get("goldId") or ""),
                item.get("repeat"),
            )
            for item in calls
        ]
        call_by_key = {
            key: call for key, call in zip(observed_keys, calls, strict=True)
        }
        if len(call_by_key) != len(calls):
            raise ValueError(f"post-fix calls are duplicated for {case_id}")
        expected_keys = []
        expected_calls = []
        expected_outcomes = []
        for gold_index, gold in enumerate(
            corpus_case["goldenComments"],
            start=1,
        ):
            gold_id = f"G{gold_index:03d}"
            primary_matched = gold_id in primary_matches
            per_candidate = {
                candidate_id: [] for candidate_id in candidate_ids
            }
            if primary_matched and f_findings:
                for repeat in range(1, repeats + 1):
                    key = ("post_fix_pair", gold_id, repeat)
                    expected_keys.append(key)
                    call = call_by_key.get(key)
                    normalized = _validate_post_fix_call(
                        call,
                        artifact_root=artifact_root,
                        case_id=case_id,
                        gold_id=gold_id,
                        repeat=repeat,
                        case_input_digest=case_input_digest,
                        judge_config=judge_config,
                        judge_config_digest=str(
                            value["judgeConfigDigest"]
                        ),
                        expected_response_model=str(
                            expected_response_model
                        ),
                        max_structured_retries=max_structured_retries,
                        findings=f_findings,
                        gold=gold,
                        changed_paths=set(planned["changedPaths"]),
                        max_prompt_characters=max_prompt_characters,
                        unsealed_at=unsealed_at,
                        judgment_created_at=judgment_created_at,
                    )
                    expected_calls.append(dict(call))
                    for item in normalized:
                        per_candidate[str(item["candidate_id"])].append(
                            dict(item)
                        )
            edges = []
            for candidate_id, repeat_values in per_candidate.items():
                if primary_matched and len(repeat_values) != repeats:
                    raise ValueError(
                        f"post-fix repeats are incomplete for "
                        f"{case_id}/{gold_id}/{candidate_id}"
                    )
                if repeat_values:
                    edges.append(
                        {
                            "goldId": gold_id,
                            "candidateId": candidate_id,
                            **_majority_match(repeat_values),
                        }
                    )
            derived = derive_post_fix_outcome(
                primary_matched=primary_matched,
                candidate_ids=candidate_ids,
                edges=edges,
            )
            expected_outcomes.append(
                {
                    **_gold_projection(gold, gold_id=gold_id),
                    "primaryMatchedAtH": primary_matched,
                    "edges": edges,
                    **derived,
                }
            )
            counts[str(derived["outcome"])] += 1
        if observed_keys != expected_keys:
            raise ValueError(
                f"post-fix call set/order drift for {case_id}"
            )
        expected_raw = {
            "caseId": case_id,
            "caseInputDigest": case_input_digest,
            "judgeConfigDigest": value["judgeConfigDigest"],
            "status": "scored",
            "sizeBand": corpus_case["sizeBand"],
            "partition": corpus_case["partition"],
            "postFixSnapshot": expected_snapshot,
            "candidateFindings": _candidate_projection(f_findings),
            "goldOutcomes": expected_outcomes,
            "calls": expected_calls,
        }
        expected_raw["caseDigest"] = sha256_json(expected_raw)
        if dict(raw_value) != expected_raw:
            raise ValueError(
                f"post-fix raw judgment derivation drift for {case_id}"
            )
        expected_projection = dict(expected_raw)
        expected_projection["rawJudgment"] = raw_name
        if dict(case_result) != expected_projection:
            raise ValueError(
                f"post-fix judgment case projection drift for {case_id}"
            )
    return {
        "judgmentId": value["judgmentId"],
        "judgmentDigest": digest,
        "counts": {
            outcome: counts.get(outcome, 0)
            for outcome in sorted(POST_FIX_OUTCOMES)
        },
    }


def build_post_fix_control(
    *,
    corpus_path: Path,
    registration_path: Path,
    seal_ledger_path: Path,
    primary_replay_lock_path: Path,
    primary_run_path: Path,
    primary_judgment_path: Path,
    post_fix_run_path: Path,
    post_fix_replay_lock_path: Path,
    post_fix_replay_attestation_path: Path,
    post_fix_judgment_path: Path,
    repository: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    artifacts = {
        "corpus": read_json(corpus_path),
        "registration": read_json(registration_path),
        "seal": read_json(seal_ledger_path),
        "primaryLock": read_json(primary_replay_lock_path),
        "primaryRun": read_json(primary_run_path),
        "primaryJudgment": read_json(primary_judgment_path),
        "postFixRun": read_json(post_fix_run_path),
        "postFixLock": read_json(post_fix_replay_lock_path),
        "postFixAttestation": read_json(post_fix_replay_attestation_path),
        "postFixJudgment": read_json(post_fix_judgment_path),
    }
    if not all(isinstance(item, Mapping) for item in artifacts.values()):
        raise ValueError("post-fix control inputs must be JSON objects")
    validation = validate_post_fix_judgment(
        artifacts["postFixJudgment"],
        corpus=artifacts["corpus"],
        registration=artifacts["registration"],
        seal_ledger=artifacts["seal"],
        primary_run=artifacts["primaryRun"],
        primary_judgment=artifacts["primaryJudgment"],
        post_fix_run=artifacts["postFixRun"],
        primary_replay_lock=artifacts["primaryLock"],
        post_fix_lock=artifacts["postFixLock"],
        post_fix_attestation=artifacts["postFixAttestation"],
        post_fix_run_artifact_root=post_fix_run_path.resolve().parent,
        artifact_root=post_fix_judgment_path.parent,
        repository=repository,
    )
    validate_post_fix_attestation(
        artifacts["postFixAttestation"],
        artifacts["postFixLock"],
        artifacts["corpus"],
        artifacts["primaryLock"],
    )
    counts = validation["counts"]
    denominator = (
        counts["disappeared"]
        + counts["still_detected"]
        + counts["unverifiable"]
    )
    control = {
        "kind": POST_FIX_CONTROL_KIND,
        "controlId": str(artifacts["postFixJudgment"]["judgmentId"]),
        "createdAt": _now(),
        "registrationDigest": artifacts["registration"][
            "registrationDigest"
        ],
        "sealLedgerDigest": artifacts["seal"]["sealLedgerDigest"],
        "corpusId": artifacts["corpus"]["corpusId"],
        "corpusDigest": artifacts["corpus"]["corpusDigest"],
        "primaryAnalysisRunId": artifacts["primaryRun"]["runId"],
        "primaryAnalysisRunDigest": artifacts["primaryRun"]["runDigest"],
        "primaryJudgmentId": artifacts["primaryJudgment"]["judgmentId"],
        "primaryJudgmentDigest": artifacts["primaryJudgment"][
            "judgmentDigest"
        ],
        "postFixAnalysisRunId": artifacts["postFixRun"]["runId"],
        "postFixAnalysisRunDigest": artifacts["postFixRun"]["runDigest"],
        "postFixReplayLockDigest": artifacts["postFixLock"]["lockDigest"],
        "postFixReplayAttestationDigest": artifacts[
            "postFixAttestation"
        ]["attestationDigest"],
        "postFixJudgmentId": artifacts["postFixJudgment"]["judgmentId"],
        "postFixJudgmentDigest": artifacts["postFixJudgment"][
            "judgmentDigest"
        ],
        "endpoint": "conditional_H_true_positive_detection_disappearance_at_F",
        "summary": {
            "primaryMatchedGoldDenominator": denominator,
            "disappeared": counts["disappeared"],
            "stillDetected": counts["still_detected"],
            "unverifiable": counts["unverifiable"],
            "primaryUnmatchedNotApplicable": counts[
                "not_applicable_primary_unmatched"
            ],
            "disappearanceRateConditionalOnPrimaryTruePositives": (
                counts["disappeared"] / denominator
                if denominator
                else None
            ),
            "isRecall": False,
        },
        "caseOutcomes": [
            {
                "caseId": case["caseId"],
                "caseDigest": case["caseDigest"],
                "outcomes": [
                    {
                        "goldId": gold["goldId"],
                        "primaryMatchedAtH": gold["primaryMatchedAtH"],
                        "outcome": gold["outcome"],
                    }
                    for gold in case["goldOutcomes"]
                ],
            }
            for case in artifacts["postFixJudgment"]["cases"]
        ],
    }
    control["controlDigest"] = sha256_json(control)
    validate_post_fix_control(
        control,
        corpus=artifacts["corpus"],
        registration=artifacts["registration"],
        seal_ledger=artifacts["seal"],
        primary_replay_lock=artifacts["primaryLock"],
        primary_run=artifacts["primaryRun"],
        primary_judgment=artifacts["primaryJudgment"],
        post_fix_run=artifacts["postFixRun"],
        post_fix_lock=artifacts["postFixLock"],
        post_fix_attestation=artifacts["postFixAttestation"],
        post_fix_judgment=artifacts["postFixJudgment"],
        post_fix_run_artifact_root=post_fix_run_path.resolve().parent,
        post_fix_judgment_artifact_root=(
            post_fix_judgment_path.resolve().parent
        ),
        repository=repository,
    )
    if output_path is not None:
        write_json(output_path, control)
    return control


def validate_post_fix_control(
    value: Any,
    *,
    corpus: Mapping[str, Any],
    registration: Mapping[str, Any],
    seal_ledger: Mapping[str, Any],
    primary_replay_lock: Mapping[str, Any],
    primary_run: Mapping[str, Any],
    primary_judgment: Mapping[str, Any],
    post_fix_run: Mapping[str, Any],
    post_fix_lock: Mapping[str, Any],
    post_fix_attestation: Mapping[str, Any],
    post_fix_judgment: Mapping[str, Any],
    post_fix_run_artifact_root: Path,
    post_fix_judgment_artifact_root: Path,
    repository: Path | None = None,
) -> dict[str, Any]:
    digest = _artifact_digest(
        value,
        kind=POST_FIX_CONTROL_KIND,
        field="controlDigest",
    )
    control_fields = {
        "kind",
        "controlId",
        "createdAt",
        "registrationDigest",
        "sealLedgerDigest",
        "corpusId",
        "corpusDigest",
        "primaryAnalysisRunId",
        "primaryAnalysisRunDigest",
        "primaryJudgmentId",
        "primaryJudgmentDigest",
        "postFixAnalysisRunId",
        "postFixAnalysisRunDigest",
        "postFixReplayLockDigest",
        "postFixReplayAttestationDigest",
        "postFixJudgmentId",
        "postFixJudgmentDigest",
        "endpoint",
        "summary",
        "caseOutcomes",
        "controlDigest",
    }
    if not isinstance(value, Mapping) or set(value) != control_fields:
        raise ValueError("post-fix control fields are invalid")
    validate_post_fix_attestation(
        post_fix_attestation,
        post_fix_lock,
        corpus,
        primary_replay_lock,
    )
    validation = validate_post_fix_judgment(
        post_fix_judgment,
        corpus=corpus,
        registration=registration,
        seal_ledger=seal_ledger,
        primary_run=primary_run,
        primary_judgment=primary_judgment,
        post_fix_run=post_fix_run,
        primary_replay_lock=primary_replay_lock,
        post_fix_lock=post_fix_lock,
        post_fix_attestation=post_fix_attestation,
        post_fix_run_artifact_root=post_fix_run_artifact_root,
        artifact_root=post_fix_judgment_artifact_root,
        repository=repository,
    )
    counts = validation["counts"]
    denominator = (
        counts["disappeared"]
        + counts["still_detected"]
        + counts["unverifiable"]
    )
    expected_summary = {
        "primaryMatchedGoldDenominator": denominator,
        "disappeared": counts["disappeared"],
        "stillDetected": counts["still_detected"],
        "unverifiable": counts["unverifiable"],
        "primaryUnmatchedNotApplicable": counts[
            "not_applicable_primary_unmatched"
        ],
        "disappearanceRateConditionalOnPrimaryTruePositives": (
            counts["disappeared"] / denominator if denominator else None
        ),
        "isRecall": False,
    }
    expected_bindings = {
        "controlId": post_fix_judgment["judgmentId"],
        "registrationDigest": registration["registrationDigest"],
        "sealLedgerDigest": seal_ledger["sealLedgerDigest"],
        "corpusId": corpus["corpusId"],
        "corpusDigest": corpus["corpusDigest"],
        "primaryAnalysisRunId": primary_run["runId"],
        "primaryAnalysisRunDigest": primary_run["runDigest"],
        "primaryJudgmentId": primary_judgment["judgmentId"],
        "primaryJudgmentDigest": primary_judgment["judgmentDigest"],
        "postFixAnalysisRunId": post_fix_run["runId"],
        "postFixAnalysisRunDigest": post_fix_run["runDigest"],
        "postFixReplayLockDigest": post_fix_lock["lockDigest"],
        "postFixReplayAttestationDigest": post_fix_attestation[
            "attestationDigest"
        ],
        "postFixJudgmentId": post_fix_judgment["judgmentId"],
        "postFixJudgmentDigest": post_fix_judgment["judgmentDigest"],
        "endpoint": (
            "conditional_H_true_positive_detection_disappearance_at_F"
        ),
        "summary": expected_summary,
    }
    if any(value.get(key) != expected for key, expected in expected_bindings.items()):
        raise ValueError("post-fix control binding/summary drift")
    expected_cases = [
        {
            "caseId": case["caseId"],
            "caseDigest": case["caseDigest"],
            "outcomes": [
                {
                    "goldId": gold["goldId"],
                    "primaryMatchedAtH": gold["primaryMatchedAtH"],
                    "outcome": gold["outcome"],
                }
                for gold in case["goldOutcomes"]
            ],
        }
        for case in post_fix_judgment["cases"]
    ]
    if value.get("caseOutcomes") != expected_cases:
        raise ValueError("post-fix control case outcome projection drift")
    _utc_datetime(value.get("createdAt"), "post-fix control createdAt")
    return {
        "controlId": value["controlId"],
        "controlDigest": digest,
        "summary": expected_summary,
    }


def build_post_fix_control_set(
    controls: Sequence[Mapping[str, Any]],
    *,
    registration: Mapping[str, Any],
    corpus: Mapping[str, Any],
    control_contexts: Mapping[str, Mapping[str, Any]],
    repository: Path | None = None,
) -> dict[str, Any]:
    expected_ids = sorted(
        str(item["postFixJudgmentId"])
        for item in registration["postFixPlan"]["judgmentPairs"]
    )
    ordered = sorted(controls, key=lambda item: str(item.get("controlId") or ""))
    observed_ids = [str(item.get("controlId") or "") for item in ordered]
    if observed_ids != expected_ids:
        raise ValueError(
            "post-fix control set must cover every registered control exactly"
        )
    result = {
        "kind": POST_FIX_CONTROL_SET_KIND,
        "createdAt": _now(),
        "registrationDigest": registration["registrationDigest"],
        "corpusDigest": corpus["corpusDigest"],
        "controls": [
            {
                "controlId": item["controlId"],
                "controlDigest": item["controlDigest"],
            }
            for item in ordered
        ],
    }
    result["controlSetDigest"] = sha256_json(result)
    validate_post_fix_control_set(
        result,
        controls=controls,
        corpus=corpus,
        registration=registration,
        control_contexts=control_contexts,
        repository=repository,
    )
    return result


def validate_post_fix_control_set(
    value: Any,
    *,
    controls: Sequence[Mapping[str, Any]],
    corpus: Mapping[str, Any],
    registration: Mapping[str, Any],
    control_contexts: Mapping[str, Mapping[str, Any]],
    repository: Path | None = None,
) -> dict[str, Any]:
    """Semantically validate the exact preregistered verified-F control set.

    ``control_contexts`` is keyed by control ID. Each value must contain the
    exact primary replay lock plus the H/F run, judgment, replay, and seal
    artifacts named below; the validator does not accept a hash-only summary.
    """

    digest = _artifact_digest(
        value,
        kind=POST_FIX_CONTROL_SET_KIND,
        field="controlSetDigest",
    )
    expected_fields = {
        "kind",
        "createdAt",
        "registrationDigest",
        "corpusDigest",
        "controls",
        "controlSetDigest",
    }
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise ValueError("post-fix control-set fields are invalid")
    _utc_datetime(value.get("createdAt"), "post-fix control set createdAt")
    if (
        value.get("registrationDigest")
        != registration.get("registrationDigest")
        or value.get("corpusDigest") != corpus.get("corpusDigest")
    ):
        raise ValueError(
            "post-fix control set registration/corpus binding drift"
        )
    expected_ids = sorted(
        str(item["postFixJudgmentId"])
        for item in registration["postFixPlan"]["judgmentPairs"]
    )
    ordered_controls = sorted(
        controls,
        key=lambda item: str(item.get("controlId") or ""),
    )
    observed_ids = [
        str(item.get("controlId") or "") for item in ordered_controls
    ]
    if (
        observed_ids != expected_ids
        or set(control_contexts) != set(expected_ids)
    ):
        raise ValueError(
            "post-fix controls/contexts must cover every registered control "
            "exactly"
        )
    projections = []
    summaries = []
    required_context_fields = {
        "sealLedger",
        "primaryReplayLock",
        "primaryRun",
        "primaryJudgment",
        "postFixRun",
        "postFixLock",
        "postFixAttestation",
        "postFixJudgment",
        "postFixRunArtifactRoot",
        "postFixJudgmentArtifactRoot",
    }
    artifact_context_fields = {
        "postFixRunArtifactRoot",
        "postFixJudgmentArtifactRoot",
    }
    for control in ordered_controls:
        control_id = str(control["controlId"])
        context = control_contexts[control_id]
        if (
            not isinstance(context, Mapping)
            or set(context) != required_context_fields
            or any(
                not isinstance(context[field], Mapping)
                for field in required_context_fields
                - artifact_context_fields
            )
            or any(
                not isinstance(context[field], Path)
                for field in artifact_context_fields
            )
        ):
            raise ValueError(
                f"post-fix control context for {control_id} is incomplete"
            )
        validation = validate_post_fix_control(
            control,
            corpus=corpus,
            registration=registration,
            seal_ledger=context["sealLedger"],
            primary_replay_lock=context["primaryReplayLock"],
            primary_run=context["primaryRun"],
            primary_judgment=context["primaryJudgment"],
            post_fix_run=context["postFixRun"],
            post_fix_lock=context["postFixLock"],
            post_fix_attestation=context["postFixAttestation"],
            post_fix_judgment=context["postFixJudgment"],
            post_fix_run_artifact_root=context[
                "postFixRunArtifactRoot"
            ],
            post_fix_judgment_artifact_root=context[
                "postFixJudgmentArtifactRoot"
            ],
            repository=repository,
        )
        projections.append(
            {
                "controlId": validation["controlId"],
                "controlDigest": validation["controlDigest"],
            }
        )
        summaries.append(
            {
                "controlId": validation["controlId"],
                "summary": validation["summary"],
            }
        )
    if value.get("controls") != projections:
        raise ValueError("post-fix control-set digest projection drift")
    return {
        "controlSetDigest": digest,
        "controlCount": len(projections),
        "controls": projections,
        "summaries": summaries,
    }


def create_post_fix_control_set(
    *,
    manifest_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Build a semantic control set from an explicit path-only input manifest."""

    manifest = read_json(manifest_path)
    if not isinstance(manifest, Mapping) or set(manifest) != {
        "corpus",
        "registration",
        "controls",
    }:
        raise ValueError("post-fix control-set manifest fields are invalid")
    root = manifest_path.resolve().parent

    def resolve_path(relative: Any, field: str) -> Path:
        text = require_text(relative, field)
        candidate = Path(text)
        path = candidate if candidate.is_absolute() else root / candidate
        return path.resolve()

    def load(relative: Any, field: str) -> Mapping[str, Any]:
        value = read_json(resolve_path(relative, field))
        if not isinstance(value, Mapping):
            raise ValueError(f"{field} must resolve to a JSON object")
        return value

    corpus = load(manifest["corpus"], "manifest.corpus")
    registration = load(
        manifest["registration"],
        "manifest.registration",
    )
    entries = manifest.get("controls")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest.controls must be a non-empty array")
    entry_fields = {
        "control",
        "sealLedger",
        "primaryReplayLock",
        "primaryRun",
        "primaryJudgment",
        "postFixRun",
        "postFixLock",
        "postFixAttestation",
        "postFixJudgment",
    }
    controls = []
    contexts = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping) or set(entry) != entry_fields:
            raise ValueError(
                f"manifest.controls[{index}] fields are invalid"
            )
        loaded = {
            field: load(
                entry[field],
                f"manifest.controls[{index}].{field}",
            )
            for field in entry_fields
        }
        control = loaded.pop("control")
        control_id = require_text(
            control.get("controlId"),
            f"manifest.controls[{index}].control.controlId",
        )
        if control_id in contexts:
            raise ValueError("manifest control IDs must be unique")
        controls.append(control)
        loaded["postFixRunArtifactRoot"] = resolve_path(
            entry["postFixRun"],
            f"manifest.controls[{index}].postFixRun",
        ).parent
        loaded["postFixJudgmentArtifactRoot"] = resolve_path(
            entry["postFixJudgment"],
            f"manifest.controls[{index}].postFixJudgment",
        ).parent
        contexts[control_id] = loaded
    result = build_post_fix_control_set(
        controls,
        registration=registration,
        corpus=corpus,
        control_contexts=contexts,
    )
    write_json(output_path, result)
    return result
