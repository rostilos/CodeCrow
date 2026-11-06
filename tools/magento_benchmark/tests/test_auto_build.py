from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from magento2_benchmark.auto_build import (
    AUTOMATIC_AUDIT_KIND,
    AUTOMATIC_CORPUS_KIND,
    AUTOMATIC_ROOT_EVIDENCE_KIND,
    DEFAULT_MATERIALIZATION_JOBS,
    DEFAULT_SELECTION_SEED,
    LEGITIMACY_TIERS,
    CandidateRejected,
    _area,
    _candidate_pool_diagnostics,
    _candidate_evidence_mode,
    _change_types,
    _clickhouse_objective_legitimacy,
    _coalesce_flat,
    _complexity,
    _date_band,
    _distribution,
    _flat_candidate_evidence,
    _hydrate_selected_roots,
    _manifest,
    _materialize_evidence,
    _objective_legitimacy,
    _prequalify_official_rest_candidates,
    _read_candidate_rows,
    _selection_policy,
    _validate_flat_candidate_accounting,
    build_automatic_corpus,
    select_balanced_cases,
    validate_automatic_corpus,
    validate_automatic_release_set,
    validate_automatic_root_evidence,
)
from magento2_benchmark.cli import _dispatch, _parser
from magento2_benchmark.github import GitHubClient, GitHubResponse
from magento2_benchmark.util import (
    deterministic_git_diff_command,
    hermetic_git_environment,
    run,
    sha256_json,
    sha256_text,
    write_json,
)


SHA = "a" * 40


def _selection_case(index: int) -> dict:
    size = ("small", "medium", "large")[index // 18]
    complexity = ("simple", "moderate", "complex")[index % 3]
    return {
        "caseId": f"case-{index:03d}",
        "sourcePr": {"number": index + 1},
        "strata": {
            "size": size,
            "complexity": complexity,
            "area": f"area-{index % 6}",
            "dateBand": f"date-{index % 3}",
        },
        "goldenComments": [{"reviewer": f"reviewer-{index % 9}"}],
    }


def test_balanced_selection_is_exact_and_input_order_invariant():
    candidates = [_selection_case(index) for index in range(54)]

    forward = select_balanced_cases(candidates)
    reverse = select_balanced_cases(list(reversed(candidates)))

    assert [case["caseId"] for case in forward] == [
        case["caseId"] for case in reverse
    ]
    distribution = _distribution(forward)
    assert distribution["size"] == {
        "small": 18,
        "medium": 18,
        "large": 18,
    }
    assert distribution["complexity"] == {
        "simple": 18,
        "moderate": 18,
        "complex": 18,
    }
    assert max(distribution["area"].values()) <= 12
    assert max(distribution["reviewer"].values()) <= 8
    assert max(distribution["dateBand"].values()) <= 42


def test_balanced_selection_rejects_an_impossible_reviewer_cap():
    candidates = [_selection_case(index) for index in range(54)]
    for case in candidates:
        case["goldenComments"][0]["reviewer"] = "Only-Reviewer"

    with pytest.raises(ValueError, match="cannot satisfy"):
        select_balanced_cases(candidates)


def test_balanced_selection_rejects_joint_size_date_infeasibility(monkeypatch):
    from magento2_benchmark import auto_build

    monkeypatch.setitem(auto_build.DIVERSITY_CAPS, "dateBand", 24)
    candidates = []
    index = 0
    for size, date_band, count in (
        ("small", "middle_2022_2024", 18),
        ("small", "recent_2025_plus", 18),
        ("medium", "legacy_through_2021", 18),
        ("large", "legacy_through_2021", 18),
    ):
        for _ in range(count):
            case = _selection_case(index % 54)
            case["caseId"] = f"joint-date-{index:03d}"
            case["sourcePr"]["number"] = index + 1
            case["strata"].update(
                {
                    "size": size,
                    "complexity": ("simple", "moderate", "complex")[index % 3],
                    "dateBand": date_band,
                }
            )
            candidates.append(case)
            index += 1

    with pytest.raises(ValueError, match="cannot satisfy"):
        select_balanced_cases(candidates)


def test_complexity_is_orthogonal_to_file_count_size_axis():
    def manifest(file_count: int) -> list[dict]:
        per_file, remainder = divmod(80, file_count)
        return [
            {
                "filename": f"app/code/Magento/Catalog/Model/File{index}.php",
                "changes": per_file + (1 if index < remainder else 0),
            }
            for index in range(file_count)
        ]

    assert _complexity(manifest(3), ["production"]) == _complexity(
        manifest(40), ["production"]
    )
    assert _selection_policy(DEFAULT_SELECTION_SEED)["complexityBands"]["complex"] == {
        "minimumScore": 6,
        "maximumScore": 9,
    }


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _user(login: str) -> dict:
    return {"login": login, "type": "User"}


def test_author_fixed_reply_is_objective_legitimacy_evidence():
    created = datetime(2025, 1, 1, tzinfo=timezone.utc)
    root = {
        "id": 10,
        "created_at": _iso(created),
        "body": "Please guard the null value.",
        "user": _user("reviewer"),
        "pull_request_review_id": 5,
    }
    reply = {
        "id": 11,
        "url": "https://api.github.com/repos/magento/magento2/pulls/comments/11",
        "pull_request_url": "https://api.github.com/repos/magento/magento2/pulls/7",
        "html_url": "https://github.com/magento/magento2/pull/7#discussion_r11",
        "in_reply_to_id": 10,
        "created_at": _iso(created + timedelta(hours=1)),
        "body": "Fixed, thank you.",
        "user": _user("author"),
    }

    legitimacy = _objective_legitimacy(
        repository=Path("unused"),
        number=7,
        pull_author="author",
        root=root,
        anchor={"originalCommitId": SHA, "path": "A.php", "originalLine": 1},
        all_comments=[root, reply],
        reviews=[],
        transition={"status": "modified", "sourcePath": "A.php"},
        transition_diff="diff",
        final_sha="b" * 40,
        merged_at=_iso(created + timedelta(hours=2)),
        git_env={},
    )

    assert legitimacy["eligible"] is True
    assert legitimacy["tier"] == "author_acknowledged_fix"
    assert legitimacy["evidence"]["replyCommentId"] == 11


def test_future_fix_reply_is_not_accepted():
    created = datetime(2025, 1, 1, tzinfo=timezone.utc)
    root = {
        "id": 10,
        "created_at": _iso(created),
        "body": "Please guard the null value.",
        "user": _user("reviewer"),
        "pull_request_review_id": 5,
    }
    reply = {
        "id": 11,
        "url": "https://api.github.com/repos/magento/magento2/pulls/comments/11",
        "pull_request_url": "https://api.github.com/repos/magento/magento2/pulls/7",
        "html_url": "https://github.com/magento/magento2/pull/7#discussion_r11",
        "in_reply_to_id": 10,
        "created_at": _iso(created + timedelta(hours=1)),
        "body": "Will be fixed later.",
        "user": _user("author"),
    }

    with pytest.raises(CandidateRejected, match="none of the accepted"):
        _objective_legitimacy(
            repository=Path("unused"),
            number=7,
            pull_author="author",
            root=root,
            anchor={"originalCommitId": SHA, "path": "A.php", "originalLine": 1},
            all_comments=[root, reply],
            reviews=[],
            transition={"status": "modified", "sourcePath": "A.php"},
            transition_diff="diff",
            final_sha="b" * 40,
            merged_at=_iso(created + timedelta(hours=2)),
            git_env={},
        )


def test_embedded_root_created_after_merge_is_rejected():
    merged = datetime(2025, 1, 1, tzinfo=timezone.utc)
    root = {
        "id": 10,
        "created_at": _iso(merged + timedelta(seconds=1)),
        "body": "Please guard the null value.",
        "user": _user("reviewer"),
        "pull_request_review_id": 5,
    }

    with pytest.raises(CandidateRejected, match="after the pull request merged"):
        _objective_legitimacy(
            repository=Path("unused"),
            number=7,
            pull_author="author",
            root=root,
            anchor={"originalCommitId": SHA, "path": "A.php", "originalLine": 1},
            all_comments=[root],
            reviews=[],
            transition={"status": "modified", "sourcePath": "A.php"},
            transition_diff="diff",
            final_sha="b" * 40,
            merged_at=_iso(merged),
            git_env={},
        )


@pytest.mark.parametrize(
    ("reply_author", "reply_offset_hours"),
    [("another-user", 1), ("author", 49)],
)
def test_flat_fix_evidence_requires_author_identity_and_pre_merge_time(
    reply_author,
    reply_offset_hours,
):
    created = datetime(2025, 1, 1, tzinfo=timezone.utc)
    root = {
        "id": 10,
        "created_at": _iso(created),
        "body": "Please guard the null value.",
        "user": _user("reviewer"),
        "_clickhouseEvidence": {
            "rowSha256": "1" * 64,
            "mergedAt": _iso(created + timedelta(hours=48)),
            "replyAt": _iso(created + timedelta(hours=reply_offset_hours)),
            "replyAuthor": reply_author,
            "replyBody": "Fixed, thank you.",
            "replyCommentId": 11,
            "requestedAt": None,
            "requestedReviewer": None,
            "approvedAt": None,
            "approvalReviewer": None,
            "approvalHeadSha": None,
        },
    }

    with pytest.raises(CandidateRejected, match="none of the accepted"):
        _clickhouse_objective_legitimacy(
            repository=Path("unused"),
            number=7,
            pull_author="author",
            root=root,
            anchor={"originalCommitId": SHA, "path": "A.php", "originalLine": 1},
            transition={"status": "modified", "sourcePath": "A.php"},
            transition_diff="diff",
            final_sha="b" * 40,
            git_env={},
        )


def test_flat_fix_evidence_accepts_explicit_author_before_merge():
    created = datetime(2025, 1, 1, tzinfo=timezone.utc)
    root = {
        "id": 10,
        "created_at": _iso(created),
        "body": "Please guard the null value.",
        "user": _user("reviewer"),
        "_clickhouseEvidence": {
            "rowSha256": "1" * 64,
            "mergedAt": _iso(created + timedelta(hours=48)),
            "replyAt": _iso(created + timedelta(hours=1)),
            "replyAuthor": "author",
            "replyBody": "Fixed, thank you.",
            "replyCommentId": 11,
            "requestedAt": None,
            "requestedReviewer": None,
            "approvedAt": None,
            "approvalReviewer": None,
            "approvalHeadSha": None,
        },
    }

    legitimacy = _clickhouse_objective_legitimacy(
        repository=Path("unused"),
        number=7,
        pull_author="author",
        root=root,
        anchor={"originalCommitId": SHA, "path": "A.php", "originalLine": 1},
        transition={"status": "modified", "sourcePath": "A.php"},
        transition_diff="diff",
        final_sha="b" * 40,
        git_env={},
    )

    assert legitimacy["tier"] == "author_acknowledged_fix"
    assert legitimacy["evidence"]["replyAuthor"] == "author"


def test_selected_reply_is_rejected_by_official_rest_root_gate():
    case = _full_case(0)
    case["goldenComments"][0]["legitimacy"]["tier"] = (
        "changes_requested_then_approved"
    )
    number = case["sourcePr"]["number"]
    comment_id = case["goldenComments"][0]["sourceCommentId"]
    evidence = case["goldenComments"][0]["legitimacy"]["evidence"]
    evidence.pop("officialRestRootHydrated")
    evidence.pop("officialRestRootResponseSha256")

    class Client:
        def get(self, _path):
            return {
                "id": comment_id,
                "url": (
                    "https://api.github.com/repos/magento/magento2/"
                    f"pulls/comments/{comment_id}"
                ),
                "pull_request_url": (
                    "https://api.github.com/repos/magento/magento2/"
                    f"pulls/{number}"
                ),
                "html_url": (
                    "https://github.com/magento/magento2/"
                    f"pull/{number}#discussion_r{comment_id}"
                ),
                "in_reply_to_id": 99,
            }

    failures, requests, responses = _hydrate_selected_roots([case], Client())

    assert requests == 1
    assert failures[0]["code"] == "official_rest_root_rejected"
    assert failures[0]["sourceLine"] == 1
    assert responses == {}


def test_unavailable_selected_root_is_rejected_for_deterministic_refill():
    case = _full_case(0)
    case["goldenComments"][0]["legitimacy"]["tier"] = (
        "changes_requested_then_approved"
    )
    evidence = case["goldenComments"][0]["legitimacy"]["evidence"]
    evidence.pop("officialRestRootHydrated")
    evidence.pop("officialRestRootResponseSha256")

    class Client:
        def get(self, _path):
            raise RuntimeError(
                "GitHub GET https://api.github.test/comment failed with HTTP 404: gone"
            )

    failures, requests, responses = _hydrate_selected_roots([case], Client())

    assert requests == 1
    assert failures[0]["code"] == "official_rest_root_unavailable"
    assert responses == {}


def test_official_rest_projects_mutable_root_fields_without_rewriting_history():
    case = _full_case(0)
    case["goldenComments"][0]["legitimacy"]["tier"] = (
        "changes_requested_then_approved"
    )
    number = case["sourcePr"]["number"]
    golden = case["goldenComments"][0]
    historical = {
        name: copy.deepcopy(golden[name])
        for name in ("body", "reviewer", "line", "startLine", "side")
    }
    comment_id = golden["sourceCommentId"]
    evidence = golden["legitimacy"]["evidence"]
    evidence.pop("officialRestRootHydrated")
    evidence.pop("officialRestRootResponseSha256")

    response = {
        "id": comment_id,
        "url": (
            "https://api.github.com/repos/magento/magento2/"
            f"pulls/comments/{comment_id}"
        ),
        "pull_request_url": (
            "https://api.github.com/repos/magento/magento2/"
            f"pulls/{number}"
        ),
        "html_url": golden["url"],
        "in_reply_to_id": None,
        "pull_request_review_id": 77,
        "body": "Please correct this implementation and its test.",
        "created_at": case["snapshot"]["reviewedAt"],
        "updated_at": "2025-01-01T00:01:00Z",
        "user": _user("Renamed-Reviewer"),
        "commit_id": case["sourcePr"]["finalHeadSha"],
        "original_commit_id": golden["originalCommitId"],
        "path": golden["path"],
        "line": None,
        "original_line": 2,
        "start_line": None,
        "original_start_line": 1,
        "side": "RIGHT",
        "original_side": None,
        "start_side": "RIGHT",
        "subject_type": "line",
    }

    class Client:
        def get(self, _path):
            return response

    failures, requests, responses = _hydrate_selected_roots([case], Client())

    assert failures == []
    assert requests == 1
    assert responses == {comment_id: response}
    assert {
        name: golden[name]
        for name in ("body", "reviewer", "line", "startLine", "side")
    } == historical
    assert golden["reviewId"] == 77
    assert evidence["officialRestProjection"] == {
        "bodySha256": sha256_text(response["body"]),
        "reviewer": "Renamed-Reviewer",
        "originalLine": 2,
        "originalStartLine": 1,
        "originalSide": "RIGHT",
        "reviewId": 77,
        "historicalDriftFields": [
            "body",
            "originalLine",
            "originalStartLine",
            "reviewer",
        ],
    }


def test_official_rest_still_rejects_immutable_root_identity_drift():
    case = _full_case(0)
    case["goldenComments"][0]["legitimacy"]["tier"] = (
        "changes_requested_then_approved"
    )
    number = case["sourcePr"]["number"]
    golden = case["goldenComments"][0]
    comment_id = golden["sourceCommentId"]
    evidence = golden["legitimacy"]["evidence"]
    evidence.pop("officialRestRootHydrated")
    evidence.pop("officialRestRootResponseSha256")

    class Client:
        def get(self, _path):
            return {
                "id": comment_id,
                "url": (
                    "https://api.github.com/repos/magento/magento2/"
                    f"pulls/comments/{comment_id}"
                ),
                "pull_request_url": (
                    "https://api.github.com/repos/magento/magento2/"
                    f"pulls/{number}"
                ),
                "html_url": golden["url"],
                "in_reply_to_id": None,
                "pull_request_review_id": 77,
                "body": golden["body"],
                "created_at": case["snapshot"]["reviewedAt"],
                "user": _user(golden["reviewer"]),
                "commit_id": case["sourcePr"]["finalHeadSha"],
                "original_commit_id": "f" * 40,
                "path": golden["path"],
                "line": 1,
                "original_line": 1,
                "start_line": None,
                "original_start_line": None,
                "side": "RIGHT",
                "original_side": None,
                "start_side": None,
                "subject_type": "line",
            }

    failures, requests, responses = _hydrate_selected_roots([case], Client())

    assert requests == 1
    assert failures[0]["code"] == "official_rest_root_drift"
    assert responses == {}


def test_flat_author_fix_hydrates_root_and_sealed_legitimacy_reply():
    case = _full_case(0)
    record = _release_root_evidence({"cases": [case]})["records"][0]
    golden = case["goldenComments"][0]
    evidence = golden["legitimacy"]["evidence"]
    for name in (
        "officialRestRootHydrated",
        "officialRestRootResponseSha256",
        "officialRestProjection",
        "officialRestLegitimacyReplyHydrated",
        "officialRestLegitimacyReplyResponseSha256",
    ):
        evidence.pop(name)
    reply_record = record["legitimacyReplyEvidence"]
    by_id = {
        golden["sourceCommentId"]: {
            "value": record["response"],
            "envelope": record["restGetEnvelope"],
        },
        evidence["replyCommentId"]: {
            "value": reply_record["response"],
            "envelope": reply_record["restGetEnvelope"],
        },
    }

    class Client:
        def request(self, method, path):
            assert method == "GET"
            comment_id = int(path.rsplit("/", 1)[1])
            sealed = by_id[comment_id]
            return GitHubResponse(
                value=copy.deepcopy(sealed["value"]),
                headers={},
                status=200,
                url=sealed["envelope"]["url"],
                cache_envelope=copy.deepcopy(sealed["envelope"]),
            )

    failures, requests, responses = _hydrate_selected_roots([case], Client())

    assert failures == []
    assert requests == 2
    assert responses == {golden["sourceCommentId"]: record["response"]}
    assert evidence["officialRestRootHydrated"] is True
    assert evidence["officialRestLegitimacyReplyHydrated"] is True
    assert evidence["officialRestLegitimacyReplyResponseSha256"] == (
        reply_record["responseSha256"]
    )
    assert case["_officialRestLegitimacyReplyEvidence"] == reply_record


def test_flat_author_fix_offline_root_only_cache_does_not_confer_tier():
    case = _full_case(0)
    record = _release_root_evidence({"cases": [case]})["records"][0]
    golden = case["goldenComments"][0]
    evidence = golden["legitimacy"]["evidence"]
    for name in (
        "officialRestRootHydrated",
        "officialRestRootResponseSha256",
        "officialRestProjection",
        "officialRestLegitimacyReplyHydrated",
        "officialRestLegitimacyReplyResponseSha256",
    ):
        evidence.pop(name)

    class Client:
        def request(self, method, path):
            assert method == "GET"
            comment_id = int(path.rsplit("/", 1)[1])
            if comment_id == evidence["replyCommentId"]:
                raise RuntimeError(
                    "offline GitHub cache miss: "
                    f"https://api.github.com/repos/magento/magento2/"
                    f"pulls/comments/{comment_id}"
                )
            return GitHubResponse(
                value=copy.deepcopy(record["response"]),
                headers={},
                status=200,
                url=record["restGetEnvelope"]["url"],
                cache_envelope=copy.deepcopy(record["restGetEnvelope"]),
            )

    failures, requests, responses = _hydrate_selected_roots([case], Client())

    assert requests == 2
    assert responses == {}
    assert failures[0]["code"] == "official_rest_legitimacy_reply_unavailable"
    assert "officialRestRootHydrated" not in evidence
    assert "officialRestLegitimacyReplyHydrated" not in evidence


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def _commit(repository: Path, text: str) -> str:
    (repository / "A.php").write_text(text, encoding="utf-8")
    _git(repository, "add", "A.php")
    _git(
        repository,
        "-c",
        "user.name=Benchmark",
        "-c",
        "user.email=benchmark@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    return _git(repository, "rev-parse", "HEAD")


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    head = _commit(repository, "<?php\nreturn $old;\n")
    final = _commit(repository, "<?php\nreturn $new;\n")
    return repository, head, final


def test_manifest_summary_is_not_contaminated_by_patch_output(tmp_path):
    repository, head, final = _repository(tmp_path)

    manifest = _manifest(
        repository,
        head,
        final,
        hermetic_git_environment(offline=True),
    )

    assert manifest == [
        {
            "filename": "A.php",
            "status": "modified",
            "additions": 1,
            "deletions": 1,
            "changes": 2,
        }
    ]


def test_materializer_derives_review_base_from_event_time_target(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    for name in ("A.php", "B.php", "C.php"):
        (repository / name).write_text("<?php\n$base = 1;\n", encoding="utf-8")
    _git(repository, "add", "A.php", "B.php", "C.php")
    _git(
        repository,
        "-c",
        "user.name=Benchmark",
        "-c",
        "user.email=benchmark@example.invalid",
        "commit",
        "-m",
        "event target",
    )
    event_base = _git(repository, "rev-parse", "HEAD")
    for name in ("A.php", "B.php", "C.php"):
        (repository / name).write_text("<?php\n$old = 1;\n", encoding="utf-8")
    _git(repository, "add", "A.php", "B.php", "C.php")
    _git(
        repository,
        "-c",
        "user.name=Benchmark",
        "-c",
        "user.email=benchmark@example.invalid",
        "commit",
        "-m",
        "reviewed head",
    )
    head = _git(repository, "rev-parse", "HEAD")
    (repository / "A.php").write_text("<?php\n$new = 1;\n", encoding="utf-8")
    _git(repository, "add", "A.php")
    _git(
        repository,
        "-c",
        "user.name=Benchmark",
        "-c",
        "user.email=benchmark@example.invalid",
        "commit",
        "-m",
        "final head",
    )
    final = _git(repository, "rev-parse", "HEAD")
    final_tree = _git(repository, "rev-parse", f"{final}^{{tree}}")
    merge = _git(
        repository,
        "-c",
        "user.name=Benchmark",
        "-c",
        "user.email=benchmark@example.invalid",
        "commit-tree",
        final_tree,
        "-p",
        event_base,
        "-m",
        "squash merge fixture",
    )
    number = 7
    _git(repository, "update-ref", f"refs/benchmark/pull/{number}", final)
    _git(repository, "update-ref", "refs/remotes/origin/2.4-develop", merge)
    comment_id = 10
    created = "2025-01-01T00:00:00Z"
    evidence = {
        "number": number,
        "line": 1,
        "candidateIds": [comment_id],
        "pull": {
            "number": number,
            "url": f"https://api.github.com/repos/magento/magento2/pulls/{number}",
            "html_url": f"https://github.com/magento/magento2/pull/{number}",
            "title": "Historical base fixture",
            "state": "closed",
            "merged": True,
            "merged_at": "2025-01-02T00:00:00Z",
            "merge_commit_sha": merge,
            "user": _user("author"),
            "head": {"sha": final},
            "base": {
                "sha": event_base,
                "ref": "2.4-develop",
                "repo": {"full_name": "magento/magento2"},
            },
        },
        "comments": [
            {
                "id": comment_id,
                "url": (
                    "https://api.github.com/repos/magento/magento2/"
                    f"pulls/comments/{comment_id}"
                ),
                "html_url": (
                    "https://github.com/magento/magento2/"
                    f"pull/{number}#discussion_r{comment_id}"
                ),
                "pull_request_url": (
                    "https://api.github.com/repos/magento/magento2/"
                    f"pulls/{number}"
                ),
                "in_reply_to_id": None,
                "pull_request_review_id": 5,
                "body": "Please change this assignment.",
                "created_at": created,
                "updated_at": created,
                "user": _user("reviewer"),
                "commit_id": head,
                "original_commit_id": head,
                "path": "A.php",
                "line": 2,
                "original_line": 2,
                "start_line": None,
                "original_start_line": None,
                "side": "RIGHT",
                "original_side": "RIGHT",
                "start_side": None,
                "subject_type": "line",
                "diff_hunk": "@@ -1,2 +1,2 @@\n <?php\n-$base = 1;\n+$old = 1;",
                "_clickhouseEvidence": {
                    "sourceLine": 1,
                    "rowSha256": "1" * 64,
                    "mergedAt": "2025-01-02T00:00:00Z",
                    "replyAt": None,
                    "replyAuthor": None,
                    "replyBody": None,
                    "replyCommentId": None,
                    "requestedAt": None,
                    "requestedReviewer": None,
                    "approvedAt": None,
                    "approvalReviewer": None,
                    "approvalHeadSha": None,
                },
            }
        ],
        "reviews": [],
    }

    cases, rejections = _materialize_evidence(
        evidence,
        repository=repository,
        git_env=hermetic_git_environment(offline=True),
    )

    assert rejections == []
    assert cases[0]["snapshot"]["eventBaseSha"] == event_base
    assert (
        cases[0]["snapshot"]["eventBaseReachabilityRef"]
        == "refs/heads/2.4-develop"
    )
    assert cases[0]["snapshot"]["baseSha"] == event_base
    assert cases[0]["snapshot"]["headSha"] == head
    assert (
        cases[0]["snapshot"]["headReachabilityRef"]
        == "refs/pull/7/head"
    )
    assert (
        cases[0]["sourcePr"]["finalHeadReachabilityRef"]
        == "refs/pull/7/head"
    )
    assert (
        cases[0]["sourcePr"]["mergeCommitReachabilityRef"]
        == "refs/heads/2.4-develop"
    )
    assert (
        cases[0]["goldenComments"][0]["legitimacy"]["tier"]
        == "actionable_anchor_change_applied"
    )

    # Coalescing keeps only the minimum group line at the top level.  Each
    # per-comment rejection must instead point back to that root's exact
    # ClickHouse JSONL row so the release validator can bind comment ID and
    # source line without weakening its drift check.
    flat_row = {
        "_line": 3,
        "pull_request_number": number,
        "comment_id": 10,
        "pr_author": "author",
        "reviewer": "reviewer",
        "final_head_sha": final,
        "merge_commit_sha": merge,
        "event_base_sha": event_base,
        "event_head_sha": head,
        "original_commit_id": head,
        "commit_id": head,
        "comment_created_at": created,
        "comment_updated_at": created,
        "merged_at": "2025-01-02T00:00:00Z",
        "path": "Unknown.php",
        "line": 2,
        "diff_hunk": "@@ -1,2 +1,2 @@\n <?php\n-$base = 1;\n+$old = 1;",
        "comment_body": "Please change this assignment.",
        "target_ref": "2.4-develop",
        "pr_title": "Historical base fixture",
        "pr_body": "Fixture body.",
    }
    second_flat_row = {**flat_row, "_line": 9, "comment_id": 11}
    flat_groups, flat_coalescing_rejections = _coalesce_flat(
        [flat_row, second_flat_row]
    )
    assert flat_coalescing_rejections == []
    assert len(flat_groups) == 1
    assert flat_groups[0]["line"] == 3

    flat_cases, flat_rejections = _materialize_evidence(
        flat_groups[0],
        repository=repository,
        git_env=hermetic_git_environment(offline=True),
    )

    assert flat_cases == []
    assert [rejection["sourceCommentId"] for rejection in flat_rejections] == [
        10,
        11,
    ]
    assert [rejection["sourceLine"] for rejection in flat_rejections] == [3, 9]
    assert {rejection["code"] for rejection in flat_rejections} == {
        "comment_path_not_in_snapshot"
    }

    invalid_flat_group = copy.deepcopy(flat_groups[0])
    invalid_flat_group["pull"]["head"]["sha"] = "0" * 40
    invalid_group_cases, invalid_group_rejections = _materialize_evidence(
        invalid_flat_group,
        repository=repository,
        git_env=hermetic_git_environment(offline=True),
    )

    assert invalid_group_cases == []
    assert [
        rejection["sourceCommentId"]
        for rejection in invalid_group_rejections
    ] == [10, 11]
    assert [
        rejection["sourceLine"] for rejection in invalid_group_rejections
    ] == [3, 9]
    assert {rejection["code"] for rejection in invalid_group_rejections} == {
        "invalid_pull_or_merge_evidence"
    }

    event_tree = _git(repository, "rev-parse", f"{event_base}^{{tree}}")
    direct_merge = _git(
        repository,
        "-c",
        "user.name=Benchmark",
        "-c",
        "user.email=benchmark@example.invalid",
        "commit-tree",
        event_tree,
        "-p",
        final,
        "-m",
        "direct merge fixture",
    )
    direct_evidence = copy.deepcopy(evidence)
    direct_evidence["pull"]["merge_commit_sha"] = direct_merge
    _git(repository, "update-ref", "refs/remotes/origin/2.4-develop", direct_merge)
    direct_cases, direct_rejections = _materialize_evidence(
        direct_evidence,
        repository=repository,
        git_env=hermetic_git_environment(offline=True),
    )
    assert direct_rejections == []
    assert direct_cases[0]["sourcePr"]["mergeCommitSha"] == direct_merge

    unassociated_merge = _git(
        repository,
        "-c",
        "user.name=Benchmark",
        "-c",
        "user.email=benchmark@example.invalid",
        "commit-tree",
        event_tree,
        "-p",
        event_base,
        "-m",
        "unassociated target commit",
    )
    unassociated_evidence = copy.deepcopy(evidence)
    unassociated_evidence["pull"]["merge_commit_sha"] = unassociated_merge
    _git(
        repository,
        "update-ref",
        "refs/remotes/origin/2.4-develop",
        unassociated_merge,
    )
    unassociated_cases, unassociated_rejections = _materialize_evidence(
        unassociated_evidence,
        repository=repository,
        git_env=hermetic_git_environment(offline=True),
    )
    assert unassociated_cases == []
    assert (
        unassociated_rejections[0]["code"]
        == "merge_commit_not_associated_with_final_head"
    )
    _git(repository, "update-ref", "refs/remotes/origin/2.4-develop", merge)

    # A retained branch containing F must not substitute for the exact PR-head
    # tip: otherwise a re-labelled final commit could survive materialization.
    _git(repository, "update-ref", "refs/remotes/origin/2.3", final)
    _git(repository, "update-ref", f"refs/benchmark/pull/{number}", head)
    dangling_final_cases, dangling_final_rejections = _materialize_evidence(
        evidence,
        repository=repository,
        git_env=hermetic_git_environment(offline=True),
    )
    assert dangling_final_cases == []
    assert (
        dangling_final_rejections[0]["code"]
        == "final_head_not_durably_reachable"
    )
    _git(repository, "update-ref", "-d", "refs/remotes/origin/2.3")

    _git(repository, "update-ref", f"refs/benchmark/pull/{number}", final)
    _git(repository, "update-ref", "-d", "refs/remotes/origin/2.4-develop")
    dangling_event_cases, dangling_event_rejections = _materialize_evidence(
        evidence,
        repository=repository,
        git_env=hermetic_git_environment(offline=True),
    )
    assert dangling_event_cases == []
    assert (
        dangling_event_rejections[0]["code"]
        == "event_base_not_durably_reachable"
    )

    _git(repository, "update-ref", "refs/remotes/origin/2.4-develop", event_base)
    dangling_merge_cases, dangling_merge_rejections = _materialize_evidence(
        evidence,
        repository=repository,
        git_env=hermetic_git_environment(offline=True),
    )
    assert dangling_merge_cases == []
    assert (
        dangling_merge_rejections[0]["code"]
        == "merge_commit_not_durably_reachable"
    )


def _review(number: int, identifier: int, state: str, login: str, when: str, commit: str) -> dict:
    return {
        "id": identifier,
        "url": f"https://api.github.com/repos/magento/magento2/pulls/{number}/reviews/{identifier}",
        "pull_request_url": f"https://api.github.com/repos/magento/magento2/pulls/{number}",
        "user": _user(login),
        "state": state,
        "submitted_at": when,
        "commit_id": commit,
    }


def test_later_same_reviewer_approval_is_objective_evidence(tmp_path):
    repository, head, final = _repository(tmp_path)
    created = datetime(2025, 1, 1, tzinfo=timezone.utc)
    root = {
        "id": 10,
        "created_at": _iso(created),
        "body": "This must be corrected.",
        "user": _user("reviewer"),
        "pull_request_review_id": 5,
    }
    reviews = [
        _review(7, 5, "CHANGES_REQUESTED", "reviewer", _iso(created), head),
        _review(
            7,
            6,
            "APPROVED",
            "reviewer",
            _iso(created + timedelta(hours=1)),
            final,
        ),
    ]

    legitimacy = _objective_legitimacy(
        repository=repository,
        number=7,
        pull_author="author",
        root=root,
        anchor={"originalCommitId": head, "path": "A.php", "originalLine": 2},
        all_comments=[root],
        reviews=reviews,
        transition={"status": "modified", "sourcePath": "A.php"},
        transition_diff="diff",
        final_sha=final,
        merged_at=_iso(created + timedelta(hours=2)),
        git_env=hermetic_git_environment(offline=True),
    )

    assert legitimacy["tier"] == "changes_requested_then_approved"
    assert legitimacy["evidence"]["approvalReviewId"] == 6


def test_applied_github_suggestion_is_objective_evidence(tmp_path):
    repository, head, final = _repository(tmp_path)
    diff = run(
        deterministic_git_diff_command(
            repository,
            "--unified=80",
            head,
            final,
            "--",
            ":(literal)A.php",
        ),
        env=hermetic_git_environment(offline=True),
    )
    created = datetime(2025, 1, 1, tzinfo=timezone.utc)
    root = {
        "id": 10,
        "created_at": _iso(created),
        "body": "Use the new value.\n```suggestion\nreturn $new;\n```",
        "user": _user("reviewer"),
        "pull_request_review_id": 5,
    }

    legitimacy = _objective_legitimacy(
        repository=repository,
        number=7,
        pull_author="author",
        root=root,
        anchor={"originalCommitId": head, "path": "A.php", "originalLine": 2},
        all_comments=[root],
        reviews=[],
        transition={
            "status": "modified",
            "sourcePath": "A.php",
            "finalPath": "A.php",
        },
        transition_diff=diff,
        final_sha=final,
        merged_at=_iso(created + timedelta(hours=2)),
        git_env=hermetic_git_environment(offline=True),
    )

    assert legitimacy["tier"] == "github_suggestion_applied"
    assert legitimacy["evidence"]["finalMatchStartLine"] == 2


def _full_case(index: int) -> dict:
    selection = _selection_case(index)
    size = selection["strata"]["size"]
    desired_complexity = selection["strata"]["complexity"]
    file_count = {"small": 3, "medium": 11, "large": 31}[size]
    head = f"{index + 1000:040x}"
    area_token = ("Catalog", "Checkout", "Customer", "Cms", "Inventory", "Graphql")[
        index % 6
    ]
    changed = []
    for item in range(file_count):
        if desired_complexity == "simple":
            filename = (
                f"app/code/Magento/{area_token}{index}/Model/File{item:02d}.php"
            )
            additions = deletions = 1
        elif desired_complexity == "moderate":
            filename = (
                f"app/code/Magento/{area_token}{index}/"
                + ("etc/config.xml" if item == 0 else f"Model/File{item:02d}.php")
            )
            additions = deletions = 100
        else:
            module = f"{area_token}{index}Part{item:02d}"
            if item % 3 == 0:
                suffix = "etc/webapi.xml"
            elif item % 3 == 1:
                suffix = "Test/Unit/File.php"
            else:
                suffix = "Model/File.php"
            filename = f"app/code/Magento/{module}/{suffix}"
            additions = deletions = 100
        changed.append(
            {
                "filename": filename,
                "status": "modified",
                "additions": additions,
                "deletions": deletions,
                "changes": additions + deletions,
            }
        )
    changed.sort(key=lambda item: item["filename"])
    change_types = _change_types(changed)
    complexity, score = _complexity(changed, change_types)
    assert complexity == desired_complexity
    path = changed[0]["filename"]
    comment_id = index + 10_000
    reply_id = index + 20_000
    year = (2021, 2023, 2025)[index % 3]
    reviewed_at = f"{year}-01-01T00:00:00Z"
    merged_at = f"{year}-01-02T00:00:00Z"
    author = f"author-{index}"
    reply_body = "Fixed in the next commit."
    transition = {
        "status": "modified",
        "sourcePath": path,
        "finalPath": path,
        "renameSimilarity": None,
        "checkpointBlobOid": f"{index + 8000:040x}",
        "finalBlobOid": f"{index + 9000:040x}",
        "diffSha256": f"{index + 10_000:064x}",
    }
    return {
        "caseId": selection["caseId"],
        "sourcePr": {
            "number": index + 1,
            "url": f"https://github.com/magento/magento2/pull/{index + 1}",
            "title": f"Fixture {index}",
            "body": f"Fixture description {index}",
            "author": author,
            "baseRef": "2.4-develop",
            "mergedAt": merged_at,
            "finalHeadSha": f"{index + 2000:040x}",
            "finalHeadReachabilityRef": f"refs/pull/{index + 1}/head",
            "mergeCommitSha": f"{index + 3000:040x}",
            "mergeCommitReachabilityRef": "refs/heads/2.4-develop",
        },
        "snapshot": {
            "eventBaseSha": f"{index + 4000:040x}",
            "eventBaseReachabilityRef": "refs/heads/2.4-develop",
            "baseSha": f"{index + 5000:040x}",
            "headSha": head,
            "headReachabilityRef": f"refs/pull/{index + 1}/head",
            "reviewedAt": reviewed_at,
            "fileCount": file_count,
            "additions": sum(item["additions"] for item in changed),
            "deletions": sum(item["deletions"] for item in changed),
            "diffSha256": f"{index + 6000:064x}",
            "manifestSha256": sha256_json(changed),
            "changedFiles": changed,
        },
        "strata": {
            "size": size,
            "complexity": complexity,
            "complexityScore": score,
            "area": _area(changed),
            "dateBand": _date_band(merged_at),
            "changeTypes": change_types,
        },
        "goldenComments": [
            {
                "sourceCommentId": comment_id,
                "url": (
                    f"https://github.com/magento/magento2/pull/{index + 1}"
                    f"#discussion_r{comment_id}"
                ),
                "body": "Please correct this implementation.",
                "path": path,
                "line": 1,
                "startLine": None,
                "side": "RIGHT",
                "reviewer": f"reviewer-{index % 9}",
                "reviewId": index + 50_000,
                "originalCommitId": head,
                "category": "code_quality",
                "legitimacy": {
                    "status": "accepted",
                    "eligible": True,
                    "policy": "objective-evidence-only",
                    "tier": "author_acknowledged_fix",
                    "evidence": {
                        "candidateRootObjectSha256": f"{index + 11_000:064x}",
                        "candidatePullObjectSha256": f"{index + 12_000:064x}",
                        "officialRestRootResponseSha256": f"{index + 7000:064x}",
                        "officialRestRootHydrated": True,
                        "officialRestProjection": {
                            "bodySha256": sha256_text(
                                "Please correct this implementation."
                            ),
                            "reviewer": f"reviewer-{index % 9}",
                            "originalLine": 1,
                            "originalStartLine": None,
                            "originalSide": "RIGHT",
                            "reviewId": index + 50_000,
                            "historicalDriftFields": [],
                        },
                        "clickHouseEventRowSha256": f"{index + 13_000:064x}",
                        "candidateSourceLine": index + 1,
                        "replyCommentId": reply_id,
                        "replyUrl": (
                            f"https://github.com/magento/magento2/pull/{index + 1}"
                            f"#discussion_r{reply_id}"
                        ),
                        "replyCreatedAt": f"{year}-01-01T01:00:00Z",
                        "replyBodySha256": sha256_text(reply_body),
                        "replyAuthor": author,
                        "officialRestLegitimacyReplyResponseSha256": (
                            f"{index + 14_000:064x}"
                        ),
                        "officialRestLegitimacyReplyHydrated": True,
                        "pathTransition": transition,
                    },
                },
            }
        ],
    }


def _tier_evidence(case: dict, tier: str) -> dict:
    golden = case["goldenComments"][0]
    current = golden["legitimacy"]["evidence"]
    common_names = {
        "candidateRootObjectSha256",
        "candidatePullObjectSha256",
        "officialRestRootResponseSha256",
        "officialRestRootHydrated",
        "officialRestProjection",
        "clickHouseEventRowSha256",
        "candidateSourceLine",
        "pathTransition",
    }
    evidence = {
        name: copy.deepcopy(current[name])
        for name in common_names
    }
    reviewer = golden["reviewer"]
    author = case["sourcePr"]["author"]
    original_range = {
        "startLine": golden["startLine"] or golden["line"],
        "line": golden["line"],
    }
    removed_anchor = [
        {
            "line": original_range["startLine"],
            "sha256": "3" * 64,
        }
    ]
    tier_fields = {
        "author_acknowledged_fix": {
            "replyCommentId": 90_001,
            "replyUrl": (
                "https://github.com/magento/magento2/pull/"
                f"{case['sourcePr']['number']}#discussion_r90001"
            ),
            "replyCreatedAt": "2021-01-01T01:00:00Z",
            "replyBodySha256": sha256_text("Fixed in the next commit."),
            "replyAuthor": author,
            "officialRestLegitimacyReplyResponseSha256": current[
                "officialRestLegitimacyReplyResponseSha256"
            ],
            "officialRestLegitimacyReplyHydrated": True,
        },
        "changes_requested_then_approved": {
            "changesRequestedAt": "2021-01-01T00:00:00Z",
            "changesRequestedReviewer": reviewer,
            "approvalSubmittedAt": "2021-01-01T01:00:00Z",
            "approvalReviewer": reviewer,
            "approvalCommitSha": "b" * 40,
        },
        "reviewer_later_approved_anchor_changed": {
            "approvalSubmittedAt": "2021-01-01T01:00:00Z",
            "approvalReviewer": reviewer,
            "approvalCommitSha": "b" * 40,
            "originalRange": original_range,
            "removedAnchoredLines": removed_anchor,
        },
        "changes_requested_anchor_changed": {
            "changesRequestedAt": "2021-01-01T00:00:00Z",
            "changesRequestedReviewer": reviewer,
            "originalRange": original_range,
            "removedAnchoredLines": removed_anchor,
        },
        "github_suggestion_applied": {
            "originalRange": original_range,
            "originalSha256": "5" * 64,
            "suggestionSha256": "6" * 64,
            "finalMatchStartLine": 1,
        },
        "explicit_code_change_applied": {
            "requestedOldTextSha256": "7" * 64,
            "oldOccurrencesAtH": 2,
            "oldOccurrencesAtF": 1,
        },
        "php_return_type_added": {
            "functionName": "execute",
            "functionLineAtH": 1,
            "signatureAtHSha256": "8" * 64,
            "signatureAtFSha256": "9" * 64,
        },
        "actionable_anchor_change_applied": {
            "actionabilityTerms": ["please"],
            "originalRange": original_range,
            "removedAnchoredLines": removed_anchor,
        },
    }
    evidence.update(tier_fields[tier])
    return evidence


def _corpus() -> dict:
    cases = [_full_case(index) for index in range(54)]
    corpus = {
        "kind": AUTOMATIC_CORPUS_KIND,
        "repository": "magento/magento2",
        "corpusId": "magento2-automatic-review-54",
        "scoringReady": True,
        "paperReady": False,
        "metricSemantics": {"label": "reference-set"},
        "selectionPolicy": _selection_policy(DEFAULT_SELECTION_SEED),
        "distribution": _distribution(cases),
        "provenance": {
            "candidatePool": {
                "evidenceMode": "clickhouse-flat",
            },
            "officialRestSelectedRootHydration": {
                "attestedRootCount": 54,
                "evidenceArtifact": "root-evidence.json",
                "evidenceDigest": "e" * 64,
            }
        },
        "cases": cases,
    }
    corpus["corpusDigest"] = sha256_json(corpus)
    return corpus


def _sealed_get_envelope(response: dict, *, status: int = 200) -> dict:
    return GitHubClient._cache_envelope(
        url=response["url"],
        status=status,
        headers={},
        value=copy.deepcopy(response),
        fetched_at="2026-08-26T00:00:00Z",
    )


def _fixture_legitimacy_reply(case: dict) -> dict:
    number = case["sourcePr"]["number"]
    golden = case["goldenComments"][0]
    evidence = golden["legitimacy"]["evidence"]
    reply_id = evidence["replyCommentId"]
    return {
        "id": reply_id,
        "url": (
            "https://api.github.com/repos/magento/magento2/"
            f"pulls/comments/{reply_id}"
        ),
        "pull_request_url": (
            "https://api.github.com/repos/magento/magento2/"
            f"pulls/{number}"
        ),
        "html_url": evidence["replyUrl"],
        "in_reply_to_id": golden["sourceCommentId"],
        "body": "Fixed in the next commit.",
        "created_at": evidence["replyCreatedAt"],
        "updated_at": evidence["replyCreatedAt"],
        "user": _user(case["sourcePr"]["author"]),
    }


def _release_root_evidence(corpus: dict) -> dict:
    records = []
    for case in corpus["cases"]:
        number = case["sourcePr"]["number"]
        golden = case["goldenComments"][0]
        comment_id = golden["sourceCommentId"]
        response = {
            "id": comment_id,
            "url": (
                "https://api.github.com/repos/magento/magento2/"
                f"pulls/comments/{comment_id}"
            ),
            "pull_request_url": (
                "https://api.github.com/repos/magento/magento2/"
                f"pulls/{number}"
            ),
            "html_url": golden["url"],
            "in_reply_to_id": None,
            "pull_request_review_id": golden["reviewId"],
            "body": golden["body"],
            "created_at": case["snapshot"]["reviewedAt"],
            "updated_at": case["snapshot"]["reviewedAt"],
            "user": _user(golden["reviewer"]),
            "commit_id": case["sourcePr"]["finalHeadSha"],
            "original_commit_id": golden["originalCommitId"],
            "path": golden["path"],
            "line": golden["line"],
            "original_line": golden["line"],
            "start_line": golden["startLine"],
            "original_start_line": golden["startLine"],
            "side": golden["side"],
            "start_side": None,
            "subject_type": "line",
        }
        response_digest = sha256_json(response)
        rest_envelope = _sealed_get_envelope(response)
        golden["legitimacy"]["evidence"][
            "officialRestRootResponseSha256"
        ] = response_digest
        legitimacy_reply_evidence = None
        legitimacy = golden["legitimacy"]
        legitimacy_evidence = legitimacy["evidence"]
        if (
            legitimacy["tier"] == "author_acknowledged_fix"
            and "clickHouseEventRowSha256" in legitimacy_evidence
        ):
            reply = _fixture_legitimacy_reply(case)
            reply_digest = sha256_json(reply)
            legitimacy_evidence[
                "officialRestLegitimacyReplyResponseSha256"
            ] = reply_digest
            legitimacy_evidence["officialRestLegitimacyReplyHydrated"] = True
            legitimacy_reply_evidence = {
                "replyCommentId": reply["id"],
                "responseSha256": reply_digest,
                "response": reply,
                "restGetEnvelope": _sealed_get_envelope(reply),
            }
        records.append(
            {
                "caseId": case["caseId"],
                "pullRequest": number,
                "sourceCommentId": comment_id,
                "responseSha256": response_digest,
                "response": response,
                "restGetEnvelope": rest_envelope,
                "legitimacyReplyEvidence": legitimacy_reply_evidence,
            }
        )
    records.sort(
        key=lambda item: (item["pullRequest"], item["sourceCommentId"])
    )
    evidence = {
        "kind": AUTOMATIC_ROOT_EVIDENCE_KIND,
        "repository": "magento/magento2",
        "recordCount": 54,
        "records": records,
    }
    evidence["evidenceDigest"] = sha256_json(evidence)
    return evidence


def test_offline_prequalification_ignores_legacy_cache_file(tmp_path):
    case = _full_case(0)
    record = _release_root_evidence({"cases": [case]})["records"][0]
    client = GitHubClient(cache_dir=tmp_path, offline=True)
    cache_path = client._cache_path("GET", record["response"]["url"])
    assert cache_path is not None
    write_json(cache_path, record["response"])

    eligible, qualified = _prequalify_official_rest_candidates([case], client)

    assert qualified == 0
    assert eligible == []
    assert "_officialRestCacheQualified" not in case


@pytest.mark.parametrize("mutation", ["identity", "reply"])
def test_offline_prequalification_rejects_nonmatching_official_envelope(
    tmp_path,
    mutation,
):
    case = _full_case(0)
    record = _release_root_evidence({"cases": [case]})["records"][0]
    envelope = record["restGetEnvelope"]
    if mutation == "identity":
        envelope["value"]["path"] = "app/code/Magento/Other/Model/File.php"
    else:
        envelope["value"]["in_reply_to_id"] = 999
    envelope["responseSha256"] = sha256_json(envelope["value"])
    envelope.pop("envelopeSha256")
    envelope["envelopeSha256"] = sha256_json(envelope)
    client = GitHubClient(cache_dir=tmp_path, offline=True)
    cache_path = client._cache_path("GET", envelope["url"])
    assert cache_path is not None
    write_json(cache_path, envelope)

    eligible, qualified = _prequalify_official_rest_candidates([case], client)

    assert qualified == 0
    assert eligible == []


def test_offline_prequalification_accepts_exact_official_root_envelope(tmp_path):
    case = _full_case(0)
    record = _release_root_evidence({"cases": [case]})["records"][0]
    client = GitHubClient(cache_dir=tmp_path, offline=True)
    cache_path = client._cache_path("GET", record["response"]["url"])
    assert cache_path is not None
    write_json(cache_path, record["restGetEnvelope"])

    eligible, qualified = _prequalify_official_rest_candidates([case], client)

    assert qualified == 1
    assert eligible == [case]
    assert case["_officialRestCacheQualified"] is True


def _write_release_manifest(release: dict) -> None:
    artifact_paths = [
        release[name]
        for name in ("corpus", "root_evidence", "audit", "query", "candidates")
    ]
    release["checksum_manifest"].write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in sorted(artifact_paths, key=lambda item: item.name)
        ),
        encoding="utf-8",
    )


def _write_release_audit(release: dict) -> None:
    audit = release["audit_value"]
    audit.pop("auditDigest", None)
    audit["auditDigest"] = sha256_json(audit)
    release["audit"].write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _reseal_release(
    release: dict,
    *,
    corpus_changed: bool = False,
    evidence_changed: bool = False,
) -> None:
    corpus = release["corpus_value"]
    evidence = release["root_evidence_value"]
    audit = release["audit_value"]
    if evidence_changed:
        evidence.pop("evidenceDigest", None)
        evidence["evidenceDigest"] = sha256_json(evidence)
        release["root_evidence"].write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        corpus["provenance"]["officialRestSelectedRootHydration"][
            "evidenceDigest"
        ] = evidence["evidenceDigest"]
        corpus_changed = True
    if corpus_changed:
        corpus["distribution"] = _distribution(corpus["cases"])
        corpus.pop("corpusDigest", None)
        corpus["corpusDigest"] = sha256_json(corpus)
        release["corpus"].write_text(
            json.dumps(corpus, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        audit["selectedCaseIds"] = [case["caseId"] for case in corpus["cases"]]
        audit["distribution"] = copy.deepcopy(corpus["distribution"])
        audit["corpusDigest"] = corpus["corpusDigest"]
    if evidence_changed:
        audit["rootEvidenceDigest"] = evidence["evidenceDigest"]
    _write_release_audit(release)
    _write_release_manifest(release)


def _automatic_release_files(tmp_path: Path) -> dict:
    query = tmp_path / "magento2-candidate-query.sql"
    query.write_text(
        "SELECT * FROM github.github_events "
        "WHERE repo_name = 'magento/magento2' "
        "AND event_type = 'PullRequestReviewCommentEvent' "
        "FORMAT JSONEachRow\n",
        encoding="utf-8",
    )
    candidates = tmp_path / "magento2-candidates.jsonl"
    corpus = _corpus()
    candidate_rows = []
    for line_number, case in enumerate(corpus["cases"], start=1):
        source = case["sourcePr"]
        snapshot = case["snapshot"]
        golden = case["goldenComments"][0]
        legitimacy = golden["legitimacy"]["evidence"]
        row = {
            "pull_request_number": source["number"],
            "comment_id": golden["sourceCommentId"],
            "pr_author": source["author"],
            "reviewer": golden["reviewer"],
            "final_head_sha": source["finalHeadSha"],
            "merge_commit_sha": source["mergeCommitSha"],
            "event_base_sha": snapshot["eventBaseSha"],
            "event_head_sha": snapshot["headSha"],
            "original_commit_id": snapshot["headSha"],
            "commit_id": snapshot["headSha"],
            "comment_created_at": snapshot["reviewedAt"],
            "comment_updated_at": snapshot["reviewedAt"],
            "merged_at": source["mergedAt"],
            "path": golden["path"],
            "line": golden["line"],
            "diff_hunk": "@@ -1 +1 @@\n-old\n+new",
            "comment_body": golden["body"],
            "target_ref": source["baseRef"],
            "pr_title": source["title"],
            "pr_body": source["body"],
            "reply_at": legitimacy["replyCreatedAt"],
            "reply_author": legitimacy["replyAuthor"],
            "reply_body": "Fixed in the next commit.",
            "reply_comment_id": legitimacy["replyCommentId"],
            "requested_at": None,
            "requested_reviewer": None,
            "approved_at": None,
            "approval_reviewer": None,
            "approved_head_sha": None,
            "has_suggestion_block": False,
            "fixture": line_number,
        }
        candidate_rows.append(row)
        synthesized = _flat_candidate_evidence({**row, "_line": line_number})
        case["caseId"] = (
            f"m2-auto-pr-{source['number']}-c{golden['sourceCommentId']}-"
            f"{snapshot['headSha'][:12]}"
        )
        legitimacy["candidateRootObjectSha256"] = sha256_json(
            synthesized["comments"][0]
        )
        legitimacy["candidatePullObjectSha256"] = sha256_json(
            synthesized["pull"]
        )
        legitimacy_evidence = legitimacy
        legitimacy_evidence["candidateSourceLine"] = line_number
        legitimacy_evidence["clickHouseEventRowSha256"] = sha256_json(row)
    corpus["cases"].sort(key=lambda case: case["caseId"])
    candidates.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in candidate_rows
        ),
        encoding="utf-8",
    )
    query_bytes = query.read_bytes()
    candidate_bytes = candidates.read_bytes()

    corpus["provenance"] = {
        "sourceRepository": "magento/magento2",
        "candidatePool": {
            "format": "ClickHouse JSONEachRow/JSONL",
            "evidenceMode": "clickhouse-flat",
            "fileName": candidates.name,
            "sha256": hashlib.sha256(candidate_bytes).hexdigest(),
            "byteCount": len(candidate_bytes),
            "rowCount": len(candidate_rows),
        },
        "acquisitionQuery": {
            "fileName": query.name,
            "sha256": hashlib.sha256(query_bytes).hexdigest(),
            "byteCount": len(query_bytes),
            "sourceTable": "github.github_events",
            "outputFormat": "JSONEachRow",
        },
        "localGit": {"materializationJobs": 8},
        "officialRestSelectedRootHydration": {
            "mode": "sealed-official-rest-response-artifact",
            "attestedRootCount": 54,
            "rejectedDiscoveryRows": 0,
            "evidenceArtifact": "magento2-selected-root-evidence.json",
            "evidenceDigest": "e" * 64,
        },
    }
    evidence = _release_root_evidence(corpus)
    corpus["provenance"]["officialRestSelectedRootHydration"][
        "evidenceDigest"
    ] = evidence["evidenceDigest"]
    corpus["corpusDigest"] = sha256_json(
        {key: value for key, value in corpus.items() if key != "corpusDigest"}
    )

    audit = {
        "kind": AUTOMATIC_AUDIT_KIND,
        "generatedAt": "2026-08-26T00:00:00Z",
        "source": {
            "candidateFile": candidates.name,
            "acquisitionQueryFile": query.name,
            "repository": "magento/magento2",
            "acquisitionQuerySha256": hashlib.sha256(query_bytes).hexdigest(),
            "acquisitionQueryBytes": len(query_bytes),
            "candidateSha256": hashlib.sha256(candidate_bytes).hexdigest(),
            "candidateBytes": len(candidate_bytes),
            "format": "ClickHouse JSONEachRow/JSONL",
            "candidateEvidenceMode": "clickhouse-flat",
            "materializationJobs": 8,
        },
        "inputRows": len(candidate_rows),
        "acceptedCandidateCases": len(corpus["cases"]),
        "acceptedCandidatePullRequests": len(corpus["cases"]),
        "candidatePoolDiagnostics": _candidate_pool_diagnostics(corpus["cases"]),
        "rejectedCandidates": 0,
        "rejectionCounts": {},
        "rejections": [],
        "selectedCaseIds": [case["caseId"] for case in corpus["cases"]],
        "gates": {
            "gitEvidence": True,
            "objectiveLegitimacy": True,
            "balancedSelection": True,
            "officialRestSelectedRoots": True,
            "corpusValidation": True,
        },
        "officialRestRejectedCandidates": 0,
        "officialRestHydration": {
            "mode": "cache-only",
            "requestCount": 54,
            "attestedRootCount": 54,
            "rejectedDiscoveryRows": 0,
        },
        "scoringReady": True,
        "paperReady": False,
        "failure": None,
        "corpusDigest": corpus["corpusDigest"],
        "rootEvidenceDigest": evidence["evidenceDigest"],
        "distribution": copy.deepcopy(corpus["distribution"]),
    }

    release = {
        "corpus": tmp_path / "magento2-automatic-corpus.json",
        "root_evidence": tmp_path / "magento2-selected-root-evidence.json",
        "audit": tmp_path / "magento2-automatic-corpus-audit.json",
        "query": query,
        "candidates": candidates,
        "checksum_manifest": tmp_path / "magento2-automatic-artifacts.sha256",
        "corpus_value": corpus,
        "root_evidence_value": evidence,
        "audit_value": audit,
    }
    release["corpus"].write_text(
        json.dumps(corpus, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    release["root_evidence"].write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_release_audit(release)
    _write_release_manifest(release)
    return release


def _validate_release(release: dict) -> dict:
    return validate_automatic_release_set(
        corpus_path=release["corpus"],
        root_evidence_path=release["root_evidence"],
        audit_path=release["audit"],
        acquisition_query_path=release["query"],
        candidates_path=release["candidates"],
        checksum_manifest_path=release["checksum_manifest"],
    )


def test_automatic_corpus_is_scorer_ready_but_never_paper_ready():
    result = validate_automatic_corpus(_corpus())

    assert result["caseCount"] == 54
    assert result["scoringReady"] is True
    assert result["paperReady"] is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "finalHeadReachabilityRef",
            "refs/heads/2.4-develop",
            "finalHeadReachabilityRef is invalid",
        ),
        (
            "mergeCommitReachabilityRef",
            "refs/heads/2.3",
            "mergeCommitReachabilityRef is invalid",
        ),
    ],
)
def test_automatic_corpus_rejects_unbound_final_and_merge_refs(
    field,
    value,
    message,
):
    corpus = _corpus()
    corpus["cases"][0]["sourcePr"][field] = value
    corpus["corpusDigest"] = sha256_json(
        {key: item for key, item in corpus.items() if key != "corpusDigest"}
    )

    with pytest.raises(ValueError, match=message):
        validate_automatic_corpus(corpus)


def test_automatic_corpus_rejects_unbound_event_base_ref():
    corpus = _corpus()
    corpus["cases"][0]["snapshot"]["eventBaseReachabilityRef"] = (
        "refs/heads/2.3"
    )
    corpus["corpusDigest"] = sha256_json(
        {key: item for key, item in corpus.items() if key != "corpusDigest"}
    )

    with pytest.raises(ValueError, match="eventBaseReachabilityRef is invalid"):
        validate_automatic_corpus(corpus)


@pytest.mark.parametrize(
    ("base_ref", "merge_ref"),
    [
        ("2.4-develop", "refs/heads/2.4-develop"),
        ("2.3-develop", "refs/heads/2.3"),
        ("2.2-develop", "refs/heads/2.2"),
        ("develop", "refs/heads/2.4-develop"),
    ],
)
def test_automatic_corpus_maps_historical_targets_to_retained_merge_refs(
    base_ref,
    merge_ref,
):
    corpus = _corpus()
    for case in corpus["cases"]:
        case["sourcePr"]["baseRef"] = base_ref
        case["sourcePr"]["mergeCommitReachabilityRef"] = merge_ref
        case["snapshot"]["eventBaseReachabilityRef"] = merge_ref
    corpus["distribution"] = _distribution(corpus["cases"])
    corpus["corpusDigest"] = sha256_json(
        {key: item for key, item in corpus.items() if key != "corpusDigest"}
    )

    result = validate_automatic_corpus(corpus)

    assert result["caseCount"] == 54


@pytest.mark.parametrize("tier", LEGITIMACY_TIERS)
def test_automatic_corpus_accepts_every_exact_flat_legitimacy_shape(tier):
    corpus = _corpus()
    legitimacy = corpus["cases"][0]["goldenComments"][0]["legitimacy"]
    legitimacy["tier"] = tier
    legitimacy["evidence"] = _tier_evidence(corpus["cases"][0], tier)
    corpus["corpusDigest"] = sha256_json(
        {key: value for key, value in corpus.items() if key != "corpusDigest"}
    )

    result = validate_automatic_corpus(corpus)

    assert result["caseCount"] == 54


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("change_types", "changeTypes drifted"),
        ("area", "area drifted"),
        ("date_band", "dateBand drifted"),
        ("complexity_score", "complexity stratum drifted"),
        ("complexity_band", "complexity stratum drifted"),
        ("reviewed_after_merge", "reviewedAt is after mergedAt"),
    ],
)
def test_automatic_corpus_recomputes_derived_strata_and_review_lifetime(
    mutation,
    message,
):
    corpus = _corpus()
    case = corpus["cases"][0]
    if mutation == "change_types":
        case["strata"]["changeTypes"] = ["production", "tests"]
    elif mutation == "area":
        case["strata"]["area"] = "other"
    elif mutation == "date_band":
        case["strata"]["dateBand"] = "recent_2025_plus"
    elif mutation == "complexity_score":
        case["strata"]["complexityScore"] += 1
    elif mutation == "complexity_band":
        case["strata"]["complexity"] = "complex"
    else:
        case["snapshot"]["reviewedAt"] = "2021-01-03T00:00:00Z"
    corpus["corpusDigest"] = sha256_json(
        {key: value for key, value in corpus.items() if key != "corpusDigest"}
    )

    with pytest.raises(ValueError, match=message):
        validate_automatic_corpus(corpus)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unexpected_field", "fields are invalid"),
        ("post_merge_reply", "outside the PR lifetime"),
        ("wrong_reply_author", "not the PR author"),
    ],
)
def test_automatic_corpus_rejects_unsealed_or_post_merge_legitimacy(
    mutation,
    message,
):
    corpus = _corpus()
    evidence = corpus["cases"][0]["goldenComments"][0]["legitimacy"][
        "evidence"
    ]
    if mutation == "unexpected_field":
        evidence["unsealedProviderField"] = "mutable"
    elif mutation == "post_merge_reply":
        evidence["replyCreatedAt"] = "2021-01-03T00:00:00Z"
    else:
        evidence["replyAuthor"] = "someone-else"
    corpus["corpusDigest"] = sha256_json(
        {key: value for key, value in corpus.items() if key != "corpusDigest"}
    )

    with pytest.raises(ValueError, match=message):
        validate_automatic_corpus(corpus)


def test_automatic_release_validator_binds_every_artifact(tmp_path):
    release = _automatic_release_files(tmp_path)

    result = _validate_release(release)

    assert result["releaseSetValid"] is True
    assert result["recordCount"] == 54
    assert result["candidateRowCount"] == 54
    assert result["checksumEntryCount"] == 5
    assert result["scoringReady"] is True
    assert result["paperReady"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("scoring", "audit is not scoringReady"),
        ("failure", "records a build failure"),
        ("gate", "success gates are incomplete"),
        ("selection", "selected case IDs drifted"),
        ("distribution", "distribution drifted"),
        ("corpus_digest", "corpus digest drifted"),
        ("root_digest", "root evidence digest drifted"),
    ],
)
def test_automatic_release_validator_rejects_resealed_failed_or_drifted_audit(
    tmp_path,
    mutation,
    message,
):
    release = _automatic_release_files(tmp_path)
    audit = release["audit_value"]
    if mutation == "scoring":
        audit["scoringReady"] = False
    elif mutation == "failure":
        audit["failure"] = {"type": "ValueError", "detail": "failed"}
    elif mutation == "gate":
        audit["gates"]["balancedSelection"] = False
    elif mutation == "selection":
        audit["selectedCaseIds"] = list(reversed(audit["selectedCaseIds"]))
    elif mutation == "distribution":
        audit["distribution"]["size"]["small"] = 17
    elif mutation == "corpus_digest":
        audit["corpusDigest"] = "0" * 64
    else:
        audit["rootEvidenceDigest"] = "0" * 64
    _write_release_audit(release)
    _write_release_manifest(release)

    with pytest.raises(ValueError, match=message):
        _validate_release(release)


@pytest.mark.parametrize("input_name", ["query", "candidates"])
def test_automatic_release_validator_rejects_resealed_input_drift(
    tmp_path,
    input_name,
):
    release = _automatic_release_files(tmp_path)
    with release[input_name].open("a", encoding="utf-8") as stream:
        stream.write("\n" if input_name == "query" else '{"candidate":3}\n')
    _write_release_manifest(release)

    with pytest.raises(ValueError, match="corpus (query|candidate) SHA-256"):
        _validate_release(release)


def test_automatic_release_validator_requires_exact_checksum_entry_set(tmp_path):
    release = _automatic_release_files(tmp_path)
    with release["checksum_manifest"].open("a", encoding="utf-8") as stream:
        stream.write(f"{'0' * 64}  unrelated.json\n")

    with pytest.raises(ValueError, match="checksum manifest entry set drifted"):
        _validate_release(release)


def test_automatic_release_validator_checks_every_manifest_hash(tmp_path):
    release = _automatic_release_files(tmp_path)
    lines = release["checksum_manifest"].read_text(encoding="utf-8").splitlines()
    lines[0] = f"{'0' * 64}{lines[0][64:]}"
    release["checksum_manifest"].write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="checksum mismatch"):
        _validate_release(release)


def test_automatic_release_validator_requires_sorted_checksum_entries(tmp_path):
    release = _automatic_release_files(tmp_path)
    lines = release["checksum_manifest"].read_text(encoding="utf-8").splitlines()
    release["checksum_manifest"].write_text(
        "\n".join(reversed(lines)) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not sorted by filename"):
        _validate_release(release)


def test_automatic_release_validator_binds_selected_case_to_candidate_row(tmp_path):
    release = _automatic_release_files(tmp_path)
    candidate_rows = [
        json.loads(line)
        for line in release["candidates"].read_text(encoding="utf-8").splitlines()
    ]
    evidence = release["corpus_value"]["cases"][0]["goldenComments"][0][
        "legitimacy"
    ]["evidence"]
    evidence["candidateSourceLine"] = 2
    evidence["clickHouseEventRowSha256"] = sha256_json(candidate_rows[1])
    _reseal_release(release, corpus_changed=True)

    with pytest.raises(ValueError, match="candidate row identity drifted"):
        _validate_release(release)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("historical_body", "golden comment projection drifted"),
        ("historical_reviewer", "golden comment projection drifted"),
        ("event_base", "snapshot projection drifted"),
        ("final_head", "source PR projection drifted"),
        ("candidate_root_digest", "candidate root object digest drifted"),
        ("candidate_pull_digest", "candidate pull object digest drifted"),
    ],
)
def test_automatic_release_validator_binds_flat_row_projection(
    tmp_path,
    mutation,
    message,
):
    release = _automatic_release_files(tmp_path)
    corpus = release["corpus_value"]
    case = corpus["cases"][0]
    golden = case["goldenComments"][0]
    evidence = golden["legitimacy"]["evidence"]

    if mutation == "historical_body":
        golden["body"] = "Fabricated event-time review body."
        evidence["officialRestProjection"]["historicalDriftFields"] = ["body"]
    elif mutation == "historical_reviewer":
        golden["reviewer"] = "alternate-reviewer"
        evidence["officialRestProjection"]["historicalDriftFields"] = [
            "reviewer"
        ]
        corpus["distribution"] = _distribution(corpus["cases"])
    elif mutation == "event_base":
        case["snapshot"]["eventBaseSha"] = "f" * 40
    elif mutation == "final_head":
        case["sourcePr"]["finalHeadSha"] = "e" * 40
    elif mutation == "candidate_root_digest":
        evidence["candidateRootObjectSha256"] = "0" * 64
    elif mutation == "candidate_pull_digest":
        evidence["candidatePullObjectSha256"] = "0" * 64
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(mutation)
    _reseal_release(release, corpus_changed=True)

    with pytest.raises(ValueError, match=message):
        _validate_release(release)


def test_release_mode_cannot_be_relabelled_to_remove_flat_reply_attestation(
    tmp_path,
):
    release = _automatic_release_files(tmp_path)
    corpus = release["corpus_value"]
    root_evidence = release["root_evidence_value"]
    corpus["provenance"]["candidatePool"]["evidenceMode"] = "embedded-rest"
    release["audit_value"]["source"][
        "candidateEvidenceMode"
    ] = "embedded-rest"
    case = corpus["cases"][0]
    legitimacy = case["goldenComments"][0]["legitimacy"]
    assert legitimacy["tier"] == "author_acknowledged_fix"
    evidence = legitimacy["evidence"]
    for name in (
        "clickHouseEventRowSha256",
        "candidateSourceLine",
        "officialRestLegitimacyReplyResponseSha256",
        "officialRestLegitimacyReplyHydrated",
    ):
        evidence.pop(name)
    evidence["candidateReplyObjectSha256"] = "9" * 64
    record = next(
        item
        for item in root_evidence["records"]
        if item["caseId"] == case["caseId"]
    )
    record["legitimacyReplyEvidence"] = None
    _reseal_release(release, corpus_changed=True, evidence_changed=True)

    with pytest.raises(
        ValueError,
        match="does not match embedded-rest|candidate-row structure",
    ):
        _validate_release(release)


def test_candidate_evidence_mode_is_derived_and_mixed_rows_are_rejected():
    flat = {"pull_request_number": 7, "comment_id": 70}
    embedded = {"pull": {"number": 8}, "comment_id": 80}

    assert _candidate_evidence_mode([flat]) == "clickhouse-flat"
    assert _candidate_evidence_mode([embedded]) == "embedded-rest"
    with pytest.raises(ValueError, match="cannot mix"):
        _candidate_evidence_mode([flat, embedded])


def test_candidate_jsonl_rejects_reserved_source_line_field(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        json.dumps(
            {
                "_line": 999,
                "pull_request_number": 7,
                "comment_id": 70,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="reserved field _line"):
        _read_candidate_rows(candidates)


def test_automatic_release_validator_rejects_noncanonical_root_record_order(
    tmp_path,
):
    release = _automatic_release_files(tmp_path)
    release["root_evidence_value"]["records"].reverse()
    _reseal_release(release, evidence_changed=True)

    with pytest.raises(ValueError, match="not canonically ordered"):
        _validate_release(release)


def test_automatic_release_validator_recomputes_rejection_counts(tmp_path):
    release = _automatic_release_files(tmp_path)
    release["audit_value"]["rejectedCandidates"] = 1
    _reseal_release(release)

    with pytest.raises(ValueError, match="rejected candidate count"):
        _validate_release(release)


def test_flat_candidate_accounting_partitions_rows_before_rest_hydration():
    candidate_rows = [
        {"_line": 1, "pull_request_number": 7, "comment_id": 70},
        {"_line": 2, "pull_request_number": 8, "comment_id": 80},
    ]
    official_overlap = {
        "rejections": [
            {
                "sourceLine": 1,
                "code": "official_rest_root_drift",
            }
        ]
    }

    _validate_flat_candidate_accounting(
        official_overlap,
        candidate_rows=candidate_rows,
        accepted_cases=2,
    )
    with pytest.raises(ValueError, match="silent rows"):
        _validate_flat_candidate_accounting(
            official_overlap,
            candidate_rows=candidate_rows,
            accepted_cases=1,
        )

    materialization_and_rest = {
        "rejections": [
            {
                "sourceLine": 1,
                "code": "official_rest_root_drift",
            },
            {
                "sourceLine": 2,
                "code": "invalid_candidate_evidence",
            },
        ]
    }
    _validate_flat_candidate_accounting(
        materialization_and_rest,
        candidate_rows=candidate_rows,
        accepted_cases=1,
    )


def test_automatic_release_validator_rejects_silent_flat_candidate_row(tmp_path):
    release = _automatic_release_files(tmp_path)
    rows = [
        json.loads(line)
        for line in release["candidates"].read_text(encoding="utf-8").splitlines()
    ]
    silent = copy.deepcopy(rows[-1])
    silent["pull_request_number"] = 900_001
    silent["comment_id"] = 900_002
    rows.append(silent)
    release["candidates"].write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    candidate_bytes = release["candidates"].read_bytes()
    candidate_digest = hashlib.sha256(candidate_bytes).hexdigest()
    candidate_pool = release["corpus_value"]["provenance"]["candidatePool"]
    candidate_pool.update(
        {
            "sha256": candidate_digest,
            "byteCount": len(candidate_bytes),
            "rowCount": len(rows),
        }
    )
    release["audit_value"]["source"].update(
        {
            "candidateSha256": candidate_digest,
            "candidateBytes": len(candidate_bytes),
        }
    )
    release["audit_value"]["inputRows"] = len(rows)
    _reseal_release(release, corpus_changed=True)

    with pytest.raises(ValueError, match="silent rows"):
        _validate_release(release)


def test_automatic_release_validator_checks_pool_diagnostics(tmp_path):
    release = _automatic_release_files(tmp_path)
    release["audit_value"]["candidatePoolDiagnostics"]["caseCount"] = 53
    _reseal_release(release)

    with pytest.raises(ValueError, match="diagnostic case count"):
        _validate_release(release)


def test_automatic_release_validator_binds_provenance_filename(tmp_path):
    release = _automatic_release_files(tmp_path)
    renamed = tmp_path / "renamed-candidates.jsonl"
    renamed.write_bytes(release["candidates"].read_bytes())
    release["candidates"] = renamed
    _write_release_manifest(release)

    with pytest.raises(ValueError, match="candidate filename"):
        _validate_release(release)


def test_automatic_release_validator_checks_candidate_row_count(tmp_path):
    release = _automatic_release_files(tmp_path)
    corpus = release["corpus_value"]
    corpus["provenance"]["candidatePool"]["rowCount"] = 1
    corpus["corpusDigest"] = sha256_json(
        {key: value for key, value in corpus.items() if key != "corpusDigest"}
    )
    release["corpus"].write_text(
        json.dumps(corpus, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    release["audit_value"]["corpusDigest"] = corpus["corpusDigest"]
    _write_release_audit(release)
    _write_release_manifest(release)

    with pytest.raises(ValueError, match="candidate row count"):
        _validate_release(release)


def test_automatic_release_validator_rejects_audit_self_digest_tampering(tmp_path):
    release = _automatic_release_files(tmp_path)
    release["audit_value"]["scoringReady"] = False
    release["audit"].write_text(
        json.dumps(release["audit_value"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="audit digest mismatch"):
        _validate_release(release)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("parent", "another root"),
        ("pull_request", "another pull request"),
        ("author", "author drifted"),
        ("time", "timestamp drifted"),
        ("body", "body drifted"),
        ("id", "reply ID drifted"),
        ("status", "sealed HTTP 200"),
        ("envelope", "sealed HTTP 200"),
    ],
)
def test_automatic_release_rejects_unsealed_or_drifted_legitimacy_reply(
    tmp_path,
    mutation,
    message,
):
    release = _automatic_release_files(tmp_path)
    corpus = release["corpus_value"]
    record = release["root_evidence_value"]["records"][0]
    reply_evidence = record["legitimacyReplyEvidence"]
    response = reply_evidence["response"]
    if mutation == "parent":
        response["in_reply_to_id"] += 1
    elif mutation == "pull_request":
        response["pull_request_url"] = (
            "https://api.github.com/repos/magento/magento2/pulls/999999"
        )
    elif mutation == "author":
        response["user"] = _user("different-author")
    elif mutation == "time":
        response["created_at"] = "2021-01-01T02:00:00Z"
    elif mutation == "body":
        response["body"] = "This is not the acquired acknowledgement."
    elif mutation == "id":
        response["id"] += 1

    response_digest = sha256_json(response)
    reply_evidence["responseSha256"] = response_digest
    legitimacy = corpus["cases"][0]["goldenComments"][0]["legitimacy"][
        "evidence"
    ]
    legitimacy[
        "officialRestLegitimacyReplyResponseSha256"
    ] = response_digest
    envelope = reply_evidence["restGetEnvelope"]
    envelope["value"] = copy.deepcopy(response)
    envelope["responseSha256"] = response_digest
    if mutation == "status":
        envelope["status"] = 201
    elif mutation == "envelope":
        envelope["schema"] = "tampered-cache-envelope-schema"
    envelope.pop("envelopeSha256")
    envelope["envelopeSha256"] = sha256_json(envelope)
    _reseal_release(release, evidence_changed=True)

    with pytest.raises(ValueError, match=message):
        _validate_release(release)


def test_flat_author_fix_root_only_attestation_is_not_scorer_ready():
    corpus = _corpus()
    evidence = corpus["cases"][0]["goldenComments"][0]["legitimacy"][
        "evidence"
    ]
    evidence.pop("officialRestLegitimacyReplyResponseSha256")
    evidence.pop("officialRestLegitimacyReplyHydrated")
    corpus["corpusDigest"] = sha256_json(
        {key: value for key, value in corpus.items() if key != "corpusDigest"}
    )

    with pytest.raises(ValueError, match="officialRestLegitimacyReply"):
        validate_automatic_corpus(corpus)


def test_frozen_root_evidence_is_bound_to_every_case():
    corpus = _corpus()
    evidence = _release_root_evidence(corpus)
    corpus["provenance"]["officialRestSelectedRootHydration"][
        "evidenceDigest"
    ] = evidence["evidenceDigest"]
    corpus["corpusDigest"] = sha256_json(
        {key: value for key, value in corpus.items() if key != "corpusDigest"}
    )

    result = validate_automatic_root_evidence(corpus, evidence)

    assert result["recordCount"] == 54

    first_record = evidence["records"][0]
    first_case = corpus["cases"][0]
    first_record["response"]["created_at"] = "2025-01-01T00:00:01Z"
    response_digest = sha256_json(first_record["response"])
    first_record["responseSha256"] = response_digest
    envelope = first_record["restGetEnvelope"]
    envelope["value"] = copy.deepcopy(first_record["response"])
    envelope["responseSha256"] = response_digest
    envelope.pop("envelopeSha256")
    envelope["envelopeSha256"] = sha256_json(envelope)
    first_case["goldenComments"][0]["legitimacy"]["evidence"][
        "officialRestRootResponseSha256"
    ] = response_digest
    evidence["evidenceDigest"] = sha256_json(
        {key: value for key, value in evidence.items() if key != "evidenceDigest"}
    )
    corpus["provenance"]["officialRestSelectedRootHydration"][
        "evidenceDigest"
    ] = evidence["evidenceDigest"]
    corpus["corpusDigest"] = sha256_json(
        {key: value for key, value in corpus.items() if key != "corpusDigest"}
    )

    with pytest.raises(ValueError, match="root evidence content drifted"):
        validate_automatic_root_evidence(corpus, evidence)


def test_root_evidence_accepts_cross_side_inverted_current_projection_without_changing_gold():
    corpus = _corpus()
    case = corpus["cases"][0]
    golden = case["goldenComments"][0]
    golden["legitimacy"]["tier"] = "actionable_anchor_change_applied"
    golden["legitimacy"]["evidence"] = _tier_evidence(
        case,
        "actionable_anchor_change_applied",
    )
    evidence = _release_root_evidence(corpus)
    historical = {
        name: copy.deepcopy(golden[name])
        for name in ("body", "reviewer", "line", "startLine", "side")
    }
    record = evidence["records"][0]
    response = record["response"]
    response.update(
        {
            "body": "Current provider-edited wording.",
            "user": _user("renamed-reviewer"),
            "pull_request_review_id": 765_432,
            "line": None,
            "original_line": 31,
            "start_line": None,
            "original_start_line": 32,
            "side": "RIGHT",
            "start_side": "LEFT",
        }
    )
    golden["reviewId"] = response["pull_request_review_id"]
    projection = {
        "bodySha256": sha256_text(response["body"]),
        "reviewer": "renamed-reviewer",
        "originalLine": 31,
        "originalStartLine": 32,
        "originalSide": "RIGHT",
        "reviewId": response["pull_request_review_id"],
        "historicalDriftFields": [
            "body",
            "originalLine",
            "originalStartLine",
            "reviewer",
        ],
    }
    golden["legitimacy"]["evidence"]["officialRestProjection"] = projection
    response_digest = sha256_json(response)
    golden["legitimacy"]["evidence"][
        "officialRestRootResponseSha256"
    ] = response_digest
    record["responseSha256"] = response_digest
    envelope = record["restGetEnvelope"]
    envelope["value"] = copy.deepcopy(response)
    envelope["responseSha256"] = response_digest
    envelope.pop("envelopeSha256")
    envelope["envelopeSha256"] = sha256_json(envelope)
    evidence.pop("evidenceDigest")
    evidence["evidenceDigest"] = sha256_json(evidence)
    corpus["provenance"]["officialRestSelectedRootHydration"][
        "evidenceDigest"
    ] = evidence["evidenceDigest"]
    corpus.pop("corpusDigest")
    corpus["corpusDigest"] = sha256_json(corpus)

    result = validate_automatic_root_evidence(corpus, evidence)

    assert result["recordCount"] == 54
    assert record["response"]["start_side"] == "LEFT"
    assert record["restGetEnvelope"]["value"] == record["response"]
    assert record["responseSha256"] == sha256_json(record["response"])
    assert golden["legitimacy"]["evidence"][
        "officialRestRootResponseSha256"
    ] == record["responseSha256"]
    assert {
        name: golden[name]
        for name in ("body", "reviewer", "line", "startLine", "side")
    } == historical


def test_automatic_corpus_rejects_nondeterministic_rest_drift_projection():
    corpus = _corpus()
    projection = corpus["cases"][0]["goldenComments"][0]["legitimacy"][
        "evidence"
    ]["officialRestProjection"]
    projection["bodySha256"] = sha256_text("provider-edited wording")
    corpus["corpusDigest"] = sha256_json(
        {key: value for key, value in corpus.items() if key != "corpusDigest"}
    )

    with pytest.raises(ValueError, match="historicalDriftFields is not deterministic"):
        validate_automatic_corpus(corpus)


@pytest.mark.parametrize("mutation", ["digest", "paper", "comment"])
def test_automatic_corpus_validation_detects_tampering(mutation):
    corpus = _corpus()
    if mutation == "digest":
        corpus["corpusDigest"] = "0" * 64
    elif mutation == "paper":
        corpus["paperReady"] = True
        corpus["corpusDigest"] = sha256_json(
            {key: value for key, value in corpus.items() if key != "corpusDigest"}
        )
    else:
        corpus["cases"][0]["goldenComments"][0]["originalCommitId"] = "f" * 40
        corpus["corpusDigest"] = sha256_json(
            {key: value for key, value in corpus.items() if key != "corpusDigest"}
        )

    with pytest.raises(ValueError):
        validate_automatic_corpus(corpus)


def test_failed_builder_writes_non_scoring_audit(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text("{}\n", encoding="utf-8")
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    query = tmp_path / "query.sql"
    query.write_text(
        "SELECT * FROM github.github_events "
        "WHERE repo_name = 'magento/magento2' "
        "AND event_type = 'PullRequestReviewCommentEvent' "
        "FORMAT JSONEachRow\n",
        encoding="utf-8",
    )
    audit = tmp_path / "audit.json"
    checksum_manifest = tmp_path / "artifacts.sha256"
    checksum_manifest.write_text("retained release manifest\n", encoding="utf-8")

    with pytest.raises(ValueError):
        build_automatic_corpus(
            candidates_path=candidates,
            acquisition_query_path=query,
            repository_path=repository,
            output=tmp_path / "corpus.json",
            audit_output=audit,
            root_evidence_output=tmp_path / "root-evidence.json",
            checksum_manifest_output=checksum_manifest,
        )

    value = json.loads(audit.read_text(encoding="utf-8"))
    assert value["scoringReady"] is False
    assert value["paperReady"] is False
    assert value["failure"]["type"] == "ValueError"
    assert not (tmp_path / "corpus.json").exists()
    assert checksum_manifest.read_text(encoding="utf-8") == (
        "retained release manifest\n"
    )


def test_builder_preflight_collision_never_overwrites_candidate_input(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    candidate_bytes = b'{"candidate": true}\n'
    candidates.write_bytes(candidate_bytes)
    query = tmp_path / "query.sql"
    query.write_text("placeholder", encoding="utf-8")
    audit = tmp_path / "audit.json"

    with pytest.raises(ValueError, match="paths must be distinct"):
        build_automatic_corpus(
            candidates_path=candidates,
            acquisition_query_path=query,
            repository_path=tmp_path / "unused-repository",
            output=tmp_path / "corpus.json",
            audit_output=audit,
            root_evidence_output=tmp_path / "root-evidence.json",
            checksum_manifest_output=candidates,
        )

    assert candidates.read_bytes() == candidate_bytes
    assert json.loads(audit.read_text(encoding="utf-8"))["scoringReady"] is False
    assert not (tmp_path / "corpus.json").exists()
    assert not (tmp_path / "root-evidence.json").exists()


def test_builder_preflight_never_uses_input_collision_as_failure_audit(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    candidate_bytes = b'{"candidate": true}\n'
    candidates.write_bytes(candidate_bytes)
    query = tmp_path / "query.sql"
    query.write_text("placeholder", encoding="utf-8")
    checksum_manifest = tmp_path / "artifacts.sha256"
    checksum_manifest.write_text("retained\n", encoding="utf-8")

    with pytest.raises(ValueError, match="paths must be distinct"):
        build_automatic_corpus(
            candidates_path=candidates,
            acquisition_query_path=query,
            repository_path=tmp_path / "unused-repository",
            output=tmp_path / "corpus.json",
            audit_output=candidates,
            root_evidence_output=tmp_path / "root-evidence.json",
            checksum_manifest_output=checksum_manifest,
        )

    assert candidates.read_bytes() == candidate_bytes
    assert checksum_manifest.read_text(encoding="utf-8") == "retained\n"
    assert not (tmp_path / "corpus.json").exists()
    assert not (tmp_path / "root-evidence.json").exists()


def test_builder_requires_sibling_corpus_and_root_evidence_outputs(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text("{}\n", encoding="utf-8")
    query = tmp_path / "query.sql"
    query.write_text("placeholder", encoding="utf-8")
    repository = tmp_path / "repository"
    repository.mkdir()
    audit = tmp_path / "audit.json"
    separate = tmp_path / "separate"
    separate.mkdir()

    with pytest.raises(ValueError, match="must be a sibling"):
        build_automatic_corpus(
            candidates_path=candidates,
            acquisition_query_path=query,
            repository_path=repository,
            output=tmp_path / "corpus.json",
            audit_output=audit,
            root_evidence_output=separate / "root-evidence.json",
            checksum_manifest_output=tmp_path / "artifacts.sha256",
        )

    value = json.loads(audit.read_text(encoding="utf-8"))
    assert value["scoringReady"] is False
    assert value["failure"]["type"] == "ValueError"


def test_auto_build_cli_hydrates_only_when_requested(monkeypatch):
    captured = {}
    client = object()
    monkeypatch.setattr(
        "magento2_benchmark.cli._github_client",
        lambda *_args, **kwargs: client,
    )
    monkeypatch.setattr(
        "magento2_benchmark.cli.build_automatic_corpus",
        lambda **kwargs: captured.update(kwargs) or {"kind": AUTOMATIC_CORPUS_KIND},
    )
    args = _parser().parse_args(
        [
            "auto-build",
            "--candidates",
            "pool.jsonl",
            "--acquisition-query",
            "candidate-query.sql",
            "--repository-path",
            "magento2",
            "--output",
            "data/magento2-automatic-corpus.json",
            "--audit-output",
            "data/magento2-automatic-corpus-audit.json",
            "--root-evidence-output",
            "data/magento2-selected-root-evidence.json",
            "--checksum-manifest-output",
            "data/magento2-automatic-artifacts.sha256",
        ]
    )

    _dispatch(args, {"github": {}})

    assert captured["github_client"] is None
    assert captured["candidates_path"].name == "pool.jsonl"
    assert captured["acquisition_query_path"].name == "candidate-query.sql"
    assert captured["root_evidence_output"].name == "magento2-selected-root-evidence.json"
    assert captured["checksum_manifest_output"].name == (
        "magento2-automatic-artifacts.sha256"
    )
    assert captured["selection_seed"] == DEFAULT_SELECTION_SEED
    assert captured["materialization_jobs"] == DEFAULT_MATERIALIZATION_JOBS


def test_validate_automatic_cli_forwards_complete_release_set(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "magento2_benchmark.cli.validate_automatic_release_set",
        lambda **kwargs: captured.update(kwargs) or {"releaseSetValid": True},
    )
    args = _parser().parse_args(
        [
            "validate-automatic",
            "--corpus",
            "magento2-automatic-corpus.json",
            "--root-evidence",
            "magento2-selected-root-evidence.json",
            "--audit",
            "magento2-automatic-corpus-audit.json",
            "--acquisition-query",
            "magento2-candidate-query.sql",
            "--candidates",
            "magento2-candidates.jsonl",
            "--checksum-manifest",
            "magento2-automatic-artifacts.sha256",
        ]
    )

    result = _dispatch(args, {"github": {}})

    assert result == {"releaseSetValid": True}
    assert captured["corpus_path"].name == "magento2-automatic-corpus.json"
    assert captured["root_evidence_path"].name == (
        "magento2-selected-root-evidence.json"
    )
    assert captured["audit_path"].name == "magento2-automatic-corpus-audit.json"
    assert captured["acquisition_query_path"].name == (
        "magento2-candidate-query.sql"
    )
    assert captured["candidates_path"].name == "magento2-candidates.jsonl"
    assert captured["checksum_manifest_path"].name == (
        "magento2-automatic-artifacts.sha256"
    )
