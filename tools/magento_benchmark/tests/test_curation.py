from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from magento2_benchmark.curation import (
    _decision_map,
    _packet_evidence,
    _review_comment_matches_source,
    _submitted_review_matches_source,
    _validate_excluded_decision,
    _validate_draft,
    exclusion_decision_binding_digest,
    release_selection,
)
from magento2_benchmark.util import read_json, sha256_json, sha256_text

from conftest import make_decisions, make_release_evidence, write_json


ADJUDICATION_AT = "2026-07-29T12:00:00Z"


def source_archive_path(tmp_path, draft):
    draft_bytes_path = write_json(
        tmp_path / "source-archive-draft.json",
        draft,
    )
    records = []
    for case in draft["cases"]:
        comments = []
        review_ids = set()
        for source in case["gold_comments"]:
            review_ids.add(int(source["review_id"]))
            comments.append(
                {
                    "id": int(source["id"]),
                    "user": {
                        "login": source["reviewer"],
                        "type": "User",
                    },
                    "pull_request_review_id": source["review_id"],
                    "body": source["body"],
                    "author_association": source["author_association"],
                    "created_at": source["created_at"],
                    "updated_at": source["updated_at"],
                    "html_url": source["url"],
                    "original_commit_id": source["original_commit_id"],
                    "path": source["path"],
                    "diff_hunk": source["diff_hunk"],
                    "side": source["side"],
                    "in_reply_to_id": None,
                    **(
                        {"commit_id": source["api_commit_id"]}
                        if "api_commit_id" in source
                        else {}
                    ),
                    **(
                        {"line": source["raw_current_line"]}
                        if "raw_current_line" in source
                        else {}
                    ),
                    **(
                        {"original_line": source["raw_original_line"]}
                        if "raw_original_line" in source
                        else {}
                    ),
                    **(
                        {"position": source["raw_position"]}
                        if "raw_position" in source
                        else {}
                    ),
                    **(
                        {"original_position": source["raw_original_position"]}
                        if "raw_original_position" in source
                        else {}
                    ),
                    **(
                        {
                            "start_line": source[
                                "raw_current_start_line"
                            ]
                        }
                        if "raw_current_start_line" in source
                        else {}
                    ),
                    **(
                        {
                            "original_start_line": source[
                                "raw_original_start_line"
                            ]
                        }
                        if "raw_original_start_line" in source
                        else {}
                    ),
                    **(
                        {"start_side": source["raw_start_side"]}
                        if "raw_start_side" in source
                        else {}
                    ),
                    "subject_type": "line",
                }
            )
        pull = {
            "number": case["pr_number"],
            "state": "closed",
            "merged_at": case["merged_at"],
            "title": case["title"],
            "merge_commit_sha": case["merge_commit_sha"],
            "user": {"login": case["pr_author"]},
            "base": {"ref": case["base_branch"]},
            "head": {"sha": case["final_head_sha"]},
        }
        submitted_reviews = []
        for review_id in sorted(review_ids):
            review_source = next(
                source
                for source in case["gold_comments"]
                if int(source["review_id"]) == review_id
            )
            submitted_reviews.append(
                {
                    "id": review_id,
                    "user": {
                        "login": review_source["reviewer"],
                        "type": "User",
                    },
                    "state": "COMMENTED",
                    "submitted_at": review_source["created_at"],
                    "commit_id": review_source["original_commit_id"],
                    "pull_request_url": (
                        "https://api.github.com/repos/magento/magento2/pulls/"
                        f"{case['pr_number']}"
                    ),
                }
            )
        record = {
            "pullRequest": case["pr_number"],
            "pull": pull,
            "selectedComments": copy.deepcopy(comments),
            "allReviewComments": comments,
            "submittedReviews": submitted_reviews,
        }
        record["caseEvidenceDigest"] = sha256_json(record)
        records.append(record)
    archive = {
        "kind": "codecrow-magento2-draft-source-archive",
        "generatedAt": ADJUDICATION_AT,
        "githubApiVersion": "2022-11-28",
        "draftFileSha256": hashlib.sha256(
            draft_bytes_path.read_bytes()
        ).hexdigest(),
        "draftSha256": sha256_json(draft),
        "cases": records,
    }
    archive["archiveDigest"] = sha256_json(archive)
    return write_json(tmp_path / "source-archive.json", archive)


def release_evidence_args(tmp_path, draft, decisions):
    threads, packet = make_release_evidence(draft, decisions)
    archive_path = source_archive_path(tmp_path, draft)
    archive_digest = read_json(archive_path)["archiveDigest"]
    thread_by_id = {
        int(thread["rootCommentId"]): thread
        for case in threads["cases"]
        for thread in case["threads"]
    }
    comment_context = {
        int(comment["id"]): (f"m2b-{index:03d}", comment)
        for index, case in enumerate(draft["cases"], start=1)
        for comment in case["gold_comments"]
    }
    for comment_key, decision in decisions["comments"].items():
        if not isinstance(decision, dict):
            continue
        adjudication = (
            decision.get("adjudication")
            if isinstance(decision, dict) else None
        )
        records = (
            adjudication.get("records")
            if isinstance(adjudication, dict)
            else None
        )
        for record in records if isinstance(records, list) else []:
            if "recordDigest" not in record:
                continue
            comment_id = int(comment_key)
            case_id, comment = comment_context[comment_id]
            record.pop("recordDigest", None)
            record["sourceArchiveDigest"] = archive_digest
            if decision.get("include") is False:
                record.update(
                    {
                        "caseId": case_id,
                        "sourceCommentId": comment_id,
                        "sourceBodySha256": sha256_text(str(comment["body"])),
                        "decisionDigest": exclusion_decision_binding_digest(
                            decision
                        ),
                        "threadEvidenceDigest": threads[
                            "threadEvidenceDigest"
                        ],
                        "threadDigest": sha256_json(
                            thread_by_id[comment_id]
                        ),
                        "curationPacketDigest": packet["packetDigest"],
                    }
                )
            record["recordDigest"] = sha256_json(record)
    return {
        "source_archive_path": archive_path,
        "thread_evidence_path": write_json(
            tmp_path / "threads.json", threads
        ),
        "curation_packet_path": write_json(
            tmp_path / "packet.json", packet
        ),
    }


def exclusion_decision(
    *,
    annotators: tuple[str, ...] = ("curator-a", "curator-b"),
    bind_records: bool = True,
):
    records = []
    for annotator in annotators:
        record = {
            "annotator": annotator,
            "verdict": "exclude",
            "at": ADJUDICATION_AT,
        }
        if bind_records:
            record["recordDigest"] = sha256_json(record)
        records.append(record)
    return {
        "include": False,
        "exclusionReason": (
            "Not semantically actionable after complete independent review."
        ),
        "adjudication": {
            "status": "excluded",
            "annotators": list(annotators),
            "records": records,
            "adjudicator": "curator-a",
            "at": ADJUDICATION_AT,
            "notes": "",
        },
    }


def append_exclusion_candidate(draft):
    comment = copy.deepcopy(draft["cases"][0]["gold_comments"][0])
    comment_id = 9_000_000_001
    comment["id"] = comment_id
    comment["body"] = "This additional comment is adjudicated for exclusion."
    comment["url"] = (
        "https://github.com/magento/magento2/pull/"
        f"{draft['cases'][0]['pr_number']}#discussion_r{comment_id}"
    )
    draft["cases"][0]["gold_comments"].append(comment)
    return comment_id


def test_draft_requires_exact_checkpoint_comments_and_fifty_unique_prs(
    draft_factory,
):
    draft = draft_factory()
    assert _validate_draft(draft) is draft

    draft.pop("kind")
    with pytest.raises(ValueError, match="corpus draft kind"):
        _validate_draft(draft)

    draft = draft_factory()
    draft["cases"][0]["gold_comments"][0]["original_commit_id"] = "f" * 40
    with pytest.raises(ValueError, match="targets another checkpoint"):
        _validate_draft(draft)

    draft = draft_factory()
    draft["cases"].pop()
    with pytest.raises(ValueError, match="exactly 50 cases"):
        _validate_draft(draft)

    draft = draft_factory()
    draft["cases"][1]["pr_number"] = draft["cases"][0]["pr_number"]
    with pytest.raises(ValueError, match="duplicate draft PR"):
        _validate_draft(draft)


def test_pr_32187_outdated_multiline_anchor_uses_original_start_line(
    draft_factory,
):
    source = draft_factory()["cases"][0]["gold_comments"][0]
    source.update(
        {
            "id": 577669406,
            "line": 26,
            "start_line": 24,
            "raw_current_line": None,
            "raw_original_line": 26,
            "raw_current_start_line": None,
            "raw_original_start_line": 24,
            "raw_start_side": "RIGHT",
        }
    )
    raw = {
        "id": source["id"],
        "pull_request_review_id": source["review_id"],
        "user": {"login": source["reviewer"], "type": "User"},
        "body": source["body"],
        "author_association": source["author_association"],
        "created_at": source["created_at"],
        "updated_at": source["updated_at"],
        "html_url": source["url"],
        "commit_id": source["api_commit_id"],
        "original_commit_id": source["original_commit_id"],
        "path": source["path"],
        "diff_hunk": source["diff_hunk"],
        "line": None,
        "original_line": 26,
        "start_line": None,
        "original_start_line": 24,
        "side": "RIGHT",
        "start_side": "RIGHT",
        "subject_type": "line",
        "position": source["raw_position"],
        "original_position": source["raw_original_position"],
        "in_reply_to_id": None,
    }

    assert _review_comment_matches_source(raw, source)

    raw["start_line"] = 24
    assert not _review_comment_matches_source(raw, source)
    raw["start_line"] = None
    raw["original_start_line"] = 25
    assert not _review_comment_matches_source(raw, source)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("raw_original_start_line", 7, "normalized H line range"),
        ("raw_current_start_line", 7, "current start without"),
        ("raw_start_side", "LEFT", "original range/start side"),
    ],
)
def test_draft_rejects_inconsistent_raw_start_coordinate_provenance(
    draft_factory,
    field,
    value,
    message,
):
    draft = draft_factory()
    draft["cases"][0]["gold_comments"][0][field] = value

    with pytest.raises(ValueError, match=message):
        _validate_draft(draft)


def test_curation_template_uses_unversioned_kind_discriminator():
    source_digest = "a" * 64
    template = {
        "kind": "codecrow-magento2-curation-decisions-template",
        "source_corpus_sha256": source_digest,
        "decisions": [],
    }

    assert _decision_map(
        template,
        draft_digest="b" * 64,
        draft_file_sha256=source_digest,
    ) == {}

    template["kind"] = "codecrow-magento2-curation-decisions-template-v1"
    with pytest.raises(ValueError, match="shipped template"):
        _decision_map(
            template,
            draft_digest="b" * 64,
            draft_file_sha256=source_digest,
        )


def test_shipped_draft_integrity_uses_unversioned_artifact_kinds():
    data = Path(__file__).parents[1] / "data"
    draft_path = data / "corpus-draft.json"
    draft = read_json(draft_path)
    audit = read_json(data / "corpus-draft-audit.json")
    template = read_json(data / "curation-decisions.template.json")
    digest = hashlib.sha256(draft_path.read_bytes()).hexdigest()

    assert draft["kind"] == "codecrow-magento2-review-corpus-draft"
    assert audit["kind"] == "codecrow-magento2-draft-audit"
    assert template["kind"] == (
        "codecrow-magento2-curation-decisions-template"
    )
    assert "schema_version" not in draft
    assert "schema_version" not in template
    assert "audit_version" not in audit
    assert audit["sha256"] == digest
    assert template["source_corpus_sha256"] == digest
    assert (data / "corpus-draft.sha256").read_text(
        encoding="utf-8"
    ) == f"{digest}  corpus-draft.json\n"


def test_release_requires_a_decision_for_every_comment(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    decisions["comments"].pop(str(draft["cases"][0]["gold_comments"][0]["id"]))
    evidence = release_evidence_args(tmp_path, draft, decisions)

    with pytest.raises(ValueError, match="missing curation decision"):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(tmp_path / "decisions.json", decisions),
            output=tmp_path / "selection.json",
            paper_ready=True,
            **evidence,
        )


def test_paper_ready_release_requires_bound_source_archive(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    threads, packet = make_release_evidence(draft, decisions)

    with pytest.raises(ValueError, match="REST source archive"):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(
                tmp_path / "decisions.json",
                decisions,
            ),
            thread_evidence_path=write_json(
                tmp_path / "threads.json",
                threads,
            ),
            curation_packet_path=write_json(
                tmp_path / "packet.json",
                packet,
            ),
            output=tmp_path / "selection.json",
            paper_ready=True,
        )


def test_paper_release_rejects_resealed_comment_review_link_tamper(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    evidence = release_evidence_args(tmp_path, draft, decisions)
    archive = read_json(evidence["source_archive_path"])
    first = archive["cases"][0]
    for collection in ("selectedComments", "allReviewComments"):
        first[collection][0]["pull_request_review_id"] += 1
    first.pop("caseEvidenceDigest")
    first["caseEvidenceDigest"] = sha256_json(first)
    archive.pop("archiveDigest")
    archive["archiveDigest"] = sha256_json(archive)
    write_json(evidence["source_archive_path"], archive)

    with pytest.raises(ValueError, match="comment .* drifted"):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(
                tmp_path / "decisions.json",
                decisions,
            ),
            output=tmp_path / "selection.json",
            paper_ready=True,
            **evidence,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "PENDING"),
        ("submitted_at", None),
        (
            "pull_request_url",
            "https://api.github.com/repos/other/repository/pulls/1",
        ),
    ],
)
def test_paper_release_rejects_unsubmitted_or_foreign_review(
    tmp_path,
    draft_factory,
    field,
    value,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    evidence = release_evidence_args(tmp_path, draft, decisions)
    archive = read_json(evidence["source_archive_path"])
    first = archive["cases"][0]
    first["submittedReviews"][0][field] = value
    first.pop("caseEvidenceDigest")
    first["caseEvidenceDigest"] = sha256_json(first)
    archive.pop("archiveDigest")
    archive["archiveDigest"] = sha256_json(archive)
    write_json(evidence["source_archive_path"], archive)

    with pytest.raises(ValueError, match="comment .* drifted"):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(
                tmp_path / "decisions.json",
                decisions,
            ),
            output=tmp_path / "selection.json",
            paper_ready=True,
            **evidence,
        )


def test_submitted_review_binding_rejects_pending_review(draft_factory):
    draft = draft_factory()
    case = draft["cases"][0]
    source = case["gold_comments"][0]
    assert not _submitted_review_matches_source(
        {
            "id": source["review_id"],
            "user": {"login": source["reviewer"], "type": "User"},
            "state": "PENDING",
            "submitted_at": None,
            "commit_id": source["original_commit_id"],
            "pull_request_url": (
                "https://api.github.com/repos/magento/magento2/pulls/"
                f"{case['pr_number']}"
            ),
        },
        source,
        pull_request=case["pr_number"],
    )


def test_paper_release_rejects_resealed_duplicate_raw_comment_id(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    evidence = release_evidence_args(tmp_path, draft, decisions)
    archive = read_json(evidence["source_archive_path"])
    first = archive["cases"][0]
    first["allReviewComments"].append(
        copy.deepcopy(first["allReviewComments"][0])
    )
    first.pop("caseEvidenceDigest")
    first["caseEvidenceDigest"] = sha256_json(first)
    archive.pop("archiveDigest")
    archive["archiveDigest"] = sha256_json(archive)
    write_json(evidence["source_archive_path"], archive)

    with pytest.raises(ValueError, match="unique positive integers"):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(
                tmp_path / "decisions.json",
                decisions,
            ),
            output=tmp_path / "selection.json",
            paper_ready=True,
            **evidence,
        )


def test_release_cannot_drop_the_only_gold_comment_from_a_case(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    comment_id = draft["cases"][0]["gold_comments"][0]["id"]
    decisions["comments"][str(comment_id)] = exclusion_decision()
    evidence = release_evidence_args(tmp_path, draft, decisions)

    with pytest.raises(ValueError, match="no accepted comment"):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(tmp_path / "decisions.json", decisions),
            output=tmp_path / "selection.json",
            paper_ready=True,
            **evidence,
        )


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        (
            exclusion_decision(annotators=("curator-a",)),
            "two distinct annotators",
        ),
        (
            exclusion_decision(bind_records=False),
            "record digest mismatch",
        ),
    ],
)
def test_paper_ready_release_rejects_single_or_unbound_exclusion(
    tmp_path,
    draft_factory,
    decision,
    message,
):
    draft = draft_factory()
    comment_id = append_exclusion_candidate(draft)
    decisions = make_decisions(draft)
    decisions["comments"][str(comment_id)] = decision
    evidence = release_evidence_args(tmp_path, draft, decisions)

    with pytest.raises(ValueError, match=message):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(tmp_path / "decisions.json", decisions),
            output=tmp_path / "selection.json",
            paper_ready=True,
            **evidence,
        )


def test_paper_ready_release_accepts_digest_bound_dual_exclusion(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    comment_id = append_exclusion_candidate(draft)
    decisions = make_decisions(draft)
    decisions["comments"][str(comment_id)] = exclusion_decision()
    evidence = release_evidence_args(tmp_path, draft, decisions)

    selection = release_selection(
        draft_path=write_json(tmp_path / "draft.json", draft),
        decisions_path=write_json(tmp_path / "decisions.json", decisions),
        output=tmp_path / "selection.json",
        paper_ready=True,
        **evidence,
    )

    assert selection["paperReadyRequested"] is True
    assert comment_id not in selection["cases"][0]["commentIds"]


def test_paper_ready_exclusion_requires_complete_thread_and_packet():
    source = {
        "body": "A real reviewer issue.",
        "updated_at": ADJUDICATION_AT,
    }
    decision = exclusion_decision()
    decision_digest = exclusion_decision_binding_digest(decision)
    for record in decision["adjudication"]["records"]:
        record.pop("recordDigest", None)
        record.update(
            {
                "caseId": "m2b-001",
                "sourceCommentId": 1,
                "sourceBodySha256": sha256_text(source["body"]),
                "decisionDigest": decision_digest,
                "sourceArchiveDigest": "a" * 64,
                "threadEvidenceDigest": "b" * 64,
                "threadDigest": None,
                "curationPacketDigest": "c" * 64,
            }
        )
        record["recordDigest"] = sha256_json(record)

    with pytest.raises(ValueError, match="complete GraphQL thread"):
        _validate_excluded_decision(
            decision,
            case_id="m2b-001",
            comment_id=1,
            paper_ready=True,
            source_comment=source,
            source_archive_digest="a" * 64,
            thread=None,
            thread_evidence_digest="b" * 64,
            packet_comment=None,
            packet_digest="c" * 64,
        )


def test_paper_release_rejects_resealed_packet_without_verifiable_path_diff(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    evidence = release_evidence_args(tmp_path, draft, decisions)
    packet = read_json(evidence["curation_packet_path"])
    packet["cases"][0]["comments"][0].pop("checkpointToFinalPathDiff")
    packet.pop("packetDigest")
    packet["packetDigest"] = sha256_json(packet)
    write_json(evidence["curation_packet_path"], packet)

    with pytest.raises(ValueError, match="checkpoint-to-final diff"):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(
                tmp_path / "decisions.json",
                decisions,
            ),
            output=tmp_path / "selection.json",
            paper_ready=True,
            **evidence,
        )


def test_paper_release_rejects_resealed_packet_h_range_start_drift(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    evidence = release_evidence_args(tmp_path, draft, decisions)
    packet = read_json(evidence["curation_packet_path"])
    packet["cases"][0]["comments"][0]["startLine"] = 1
    packet.pop("packetDigest")
    packet["packetDigest"] = sha256_json(packet)
    write_json(evidence["curation_packet_path"], packet)

    with pytest.raises(ValueError, match="drifted from the draft"):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(
                tmp_path / "decisions.json",
                decisions,
            ),
            output=tmp_path / "selection.json",
            paper_ready=True,
            **evidence,
        )


def test_release_retains_exact_source_archive_object_digests(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    evidence = release_evidence_args(tmp_path, draft, decisions)
    archive = read_json(evidence["source_archive_path"])

    selection = release_selection(
        draft_path=write_json(tmp_path / "draft.json", draft),
        decisions_path=write_json(tmp_path / "decisions.json", decisions),
        output=tmp_path / "selection.json",
        paper_ready=True,
        **evidence,
    )

    released = selection["cases"][0]["sourceArchiveEvidence"]
    archived = archive["cases"][0]
    source_comment = draft["cases"][0]["gold_comments"][0]
    assert released["archiveDigest"] == archive["archiveDigest"]
    assert released["caseEvidenceDigest"] == archived["caseEvidenceDigest"]
    assert released["pullResponseSha256"] == sha256_json(archived["pull"])
    assert released["selectedCommentResponseSha256"][
        str(source_comment["id"])
    ] == sha256_json(archived["selectedComments"][0])
    assert released["submittedReviewResponseSha256"][
        str(source_comment["review_id"])
    ] == sha256_json(archived["submittedReviews"][0])


def test_release_carries_packet_path_transition_into_source_evidence(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    evidence = release_evidence_args(tmp_path, draft, decisions)
    packet = read_json(evidence["curation_packet_path"])
    comment_id = draft["cases"][0]["gold_comments"][0]["id"]
    expected = packet["cases"][0]["comments"][0]["pathTransition"]

    selection = release_selection(
        draft_path=write_json(tmp_path / "draft.json", draft),
        decisions_path=write_json(tmp_path / "decisions.json", decisions),
        output=tmp_path / "selection.json",
        paper_ready=True,
        **evidence,
    )

    assert selection["cases"][0]["sourceCommentEvidence"][
        str(comment_id)
    ]["pathTransition"] == expected


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda comment: comment["pathTransition"].__setitem__(
                "finalPath",
                "app/code/Other/Path.php",
            ),
            "modified path transition is inconsistent",
        ),
        (
            lambda comment: comment["finalSource"].__setitem__(
                "blobOid",
                "f" * 40,
            ),
            "finalSource path/blob identity drift",
        ),
        (
            lambda comment: comment.pop("pathTransition"),
            "path transition fields are invalid",
        ),
    ],
)
def test_packet_validation_rejects_inconsistent_path_transition_evidence(
    tmp_path,
    draft_factory,
    mutate,
    message,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    _, packet = make_release_evidence(draft, decisions)
    mutate(packet["cases"][0]["comments"][0])
    packet.pop("packetDigest")
    packet["packetDigest"] = sha256_json(packet)
    packet_path = write_json(tmp_path / "packet.json", packet)

    with pytest.raises(ValueError, match=message):
        _packet_evidence(
            packet_path,
            draft=draft,
            draft_digest=sha256_json(draft),
        )


def test_packet_deleted_transition_requires_explicit_missing_final_source(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    _, packet = make_release_evidence(draft, decisions)
    comment = packet["cases"][0]["comments"][0]
    transition = comment["pathTransition"]
    transition.update(
        {
            "status": "deleted",
            "finalPath": None,
            "renameSimilarity": None,
            "finalBlobOid": None,
        }
    )
    comment["finalSource"] = {
        "available": False,
        "path": None,
        "blobOid": None,
        "startLine": None,
        "endLine": None,
        "content": "",
    }
    packet.pop("packetDigest")
    packet["packetDigest"] = sha256_json(packet)

    evidence, _ = _packet_evidence(
        write_json(tmp_path / "packet.json", packet),
        draft=draft,
        draft_digest=sha256_json(draft),
    )

    assert evidence[comment["commentId"]]["pathTransition"]["status"] == "deleted"
    assert evidence[comment["commentId"]]["finalSource"]["available"] is False


def test_paper_ready_release_requires_graphql_rest_message_reconciliation(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    threads, packet = make_release_evidence(draft, decisions)
    threads["cases"][0]["threads"][0][
        "messageIdsReconciledWithRest"
    ] = False
    threads.pop("threadEvidenceDigest")
    threads["threadEvidenceDigest"] = sha256_json(threads)

    with pytest.raises(ValueError, match="complete GraphQL thread"):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(
                tmp_path / "decisions.json",
                decisions,
            ),
            thread_evidence_path=write_json(
                tmp_path / "threads.json",
                threads,
            ),
            curation_packet_path=write_json(
                tmp_path / "packet.json",
                packet,
            ),
            source_archive_path=source_archive_path(tmp_path, draft),
            output=tmp_path / "selection.json",
            paper_ready=True,
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda decision: decision.__setitem__("issuePresentAtSnapshot", False),
            "issuePresentAtSnapshot=true",
        ),
        (
            lambda decision: decision.__setitem__("threadComplete", False),
            "complete thread",
        ),
        (
            lambda decision: decision["adjudication"].__setitem__(
                "annotators", ["one-person"]
            ),
            "two annotators",
        ),
        (
            lambda decision: decision.__setitem__("fixEvidence", [{}]),
            "at least two fix evidence",
        ),
    ],
)
def test_paper_ready_release_enforces_decision_evidence(
    tmp_path,
    draft_factory,
    mutate,
    message,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    evidence = release_evidence_args(tmp_path, draft, decisions)
    key = str(draft["cases"][0]["gold_comments"][0]["id"])
    mutate(decisions["comments"][key])

    with pytest.raises(ValueError, match=message):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(tmp_path / "decisions.json", decisions),
            output=tmp_path / "selection.json",
            paper_ready=True,
            **evidence,
        )


def test_paper_ready_release_requires_atomic_issue(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    evidence = release_evidence_args(tmp_path, draft, decisions)
    key = str(draft["cases"][0]["gold_comments"][0]["id"])
    decisions["comments"][key]["atomic"] = False

    with pytest.raises(ValueError, match="atomic"):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(tmp_path / "decisions.json", decisions),
            output=tmp_path / "selection.json",
            paper_ready=True,
            **evidence,
        )


def test_release_retains_dual_adjudicator_provenance(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    evidence = release_evidence_args(tmp_path, draft, decisions)

    selection = release_selection(
        draft_path=write_json(tmp_path / "draft.json", draft),
        decisions_path=write_json(tmp_path / "decisions.json", decisions),
        output=tmp_path / "selection.json",
        paper_ready=True,
        **evidence,
    )

    annotation = next(iter(selection["cases"][0]["expectedIssues"].values()))
    assert annotation["adjudication"]["annotators"] == ["curator-a", "curator-b"]


def test_paper_release_rejects_record_reused_for_another_comment(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    evidence = release_evidence_args(tmp_path, draft, decisions)
    first_id = str(draft["cases"][0]["gold_comments"][0]["id"])
    second_id = str(draft["cases"][1]["gold_comments"][0]["id"])
    reused = copy.deepcopy(
        decisions["comments"][first_id]["adjudication"]["records"][0]
    )
    decisions["comments"][second_id]["adjudication"]["records"][0] = reused

    with pytest.raises(ValueError, match="evidence binding mismatch"):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(tmp_path / "decisions.json", decisions),
            output=tmp_path / "selection.json",
            paper_ready=True,
            **evidence,
        )


def test_paper_release_binds_records_to_the_semantic_decision(
    tmp_path,
    draft_factory,
):
    draft = draft_factory()
    decisions = make_decisions(draft)
    evidence = release_evidence_args(tmp_path, draft, decisions)
    first_id = str(draft["cases"][0]["gold_comments"][0]["id"])
    decisions["comments"][first_id]["requiredChange"] = (
        "A different required change."
    )

    with pytest.raises(ValueError, match="evidence binding mismatch"):
        release_selection(
            draft_path=write_json(tmp_path / "draft.json", draft),
            decisions_path=write_json(tmp_path / "decisions.json", decisions),
            output=tmp_path / "selection.json",
            paper_ready=True,
            **evidence,
        )
