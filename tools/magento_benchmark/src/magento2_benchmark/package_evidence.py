from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from .collect import (
    DISCOVERY_KIND,
    DISCOVERY_SELECTION_LINK_KIND,
    SELECTION_KIND,
    validate_discovery,
    validate_discovery_selection_linkage,
)
from .corpus import validate_corpus
from .current_comments import (
    CURRENT_COMMENT_ATTESTATION_KIND,
    validate_current_comment_attestation,
)
from .curation import (
    DECISIONS_KIND,
    DRAFT_KIND,
    DRAFT_SOURCE_ARCHIVE_KIND,
    PACKET_KIND,
    THREAD_EVIDENCE_KIND,
    release_selection,
)
from .replay import (
    ATTESTATION_KIND,
    LOCK_KIND,
    validate_replay_attestation,
    validate_replay_attestation_freshness,
    validate_replay_lock,
)
from .postfix import (
    POST_FIX_ATTESTATION_KIND,
    POST_FIX_CONTROL_KIND,
    POST_FIX_CONTROL_SET_KIND,
    POST_FIX_JUDGMENT_KIND,
    POST_FIX_LOCK_KIND,
    POST_FIX_PLAN_KIND,
    POST_FIX_RUN_KIND,
    validate_analysis_pair,
    validate_post_fix_attestation,
    validate_post_fix_control_set,
    validate_post_fix_judgment,
    validate_post_fix_lock,
    validate_post_fix_plan,
)
from .runner import MAX_PAPER_ATTESTATION_AGE_SECONDS
from .util import read_json, sha256_json, sha256_text


SOURCE_EVIDENCE_KINDS = {
    DISCOVERY_KIND,
    DISCOVERY_SELECTION_LINK_KIND,
    DRAFT_KIND,
    DRAFT_SOURCE_ARCHIVE_KIND,
    THREAD_EVIDENCE_KIND,
}
CURATION_EVIDENCE_KINDS = {
    PACKET_KIND,
    DECISIONS_KIND,
    SELECTION_KIND,
}
REPLAY_EVIDENCE_KINDS = {LOCK_KIND, ATTESTATION_KIND}
CURRENT_COMMENT_EVIDENCE_KINDS = {CURRENT_COMMENT_ATTESTATION_KIND}
POST_FIX_EVIDENCE_KINDS = {
    POST_FIX_PLAN_KIND,
    POST_FIX_LOCK_KIND,
    POST_FIX_ATTESTATION_KIND,
    POST_FIX_RUN_KIND,
    POST_FIX_JUDGMENT_KIND,
    POST_FIX_CONTROL_KIND,
    POST_FIX_CONTROL_SET_KIND,
}


def _digest_artifact(
    value: Any,
    *,
    kind: str,
    digest_field: str,
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or value.get("kind") != kind:
        raise ValueError(f"{label} kind is invalid")
    payload = dict(value)
    declared = payload.pop(digest_field, None)
    if (
        not isinstance(declared, str)
        or len(declared) != 64
        or declared != sha256_json(payload)
    ):
        raise ValueError(f"{label} digest mismatch")
    return value


def _selection_projection(
    *,
    corpus: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> None:
    corpus_by_id = {
        str(case["caseId"]): case
        for case in corpus["cases"]
        if isinstance(case, Mapping)
    }
    selection_cases = selection.get("cases")
    if not isinstance(selection_cases, list):
        raise ValueError("released selection cases are invalid")
    if len(selection_cases) != len(corpus_by_id):
        raise ValueError("released selection/corpus case coverage mismatch")
    for selected in selection_cases:
        if not isinstance(selected, Mapping):
            raise ValueError("released selection case is invalid")
        case_id = str(selected.get("caseId") or "")
        case = corpus_by_id.get(case_id)
        if case is None:
            raise ValueError(
                f"released selection contains unknown corpus case {case_id!r}"
            )
        source_pr = case["sourcePr"]
        snapshot = case["snapshot"]
        if (
            selected.get("pullRequest") != source_pr.get("number")
            or selected.get("partition") != case.get("partition")
            or selected.get("baseSha") != snapshot.get("baseSha")
            or selected.get("headSha") != snapshot.get("headSha")
        ):
            raise ValueError(
                f"{case_id}: released selection/corpus identity drift"
            )
        if selected.get("sourceArchiveEvidence") != case.get(
            "sourceArchiveEvidence"
        ):
            raise ValueError(
                f"{case_id}: source archive projection drift"
            )
        if selected.get("graphqlThreadArchive") != case.get(
            "graphqlThreadArchive"
        ):
            raise ValueError(
                f"{case_id}: GraphQL archive projection drift"
            )
        golden = {
            int(comment["sourceCommentId"]): comment
            for comment in case["goldenComments"]
            if isinstance(comment, Mapping)
        }
        selected_ids = selected.get("commentIds")
        if (
            not isinstance(selected_ids, list)
            or [int(item) for item in selected_ids] != list(golden)
        ):
            raise ValueError(
                f"{case_id}: released comment order/set drift"
            )
        expected_issues = selected.get("expectedIssues")
        source_evidence = selected.get("sourceCommentEvidence")
        if not isinstance(expected_issues, Mapping) or not isinstance(
            source_evidence,
            Mapping,
        ):
            raise ValueError(
                f"{case_id}: released curation projections are invalid"
            )
        for comment_id, comment in golden.items():
            key = str(comment_id)
            annotation = expected_issues.get(key)
            evidence = source_evidence.get(key)
            if not isinstance(annotation, Mapping) or not isinstance(
                evidence,
                Mapping,
            ):
                raise ValueError(
                    f"{case_id}/{comment_id}: curation projection is missing"
                )
            expected_issue = {
                field: annotation.get(field)
                for field in (
                    "summary",
                    "rootCause",
                    "failureMode",
                    "requiredChange",
                    "category",
                    "severity",
                    "atomic",
                )
            }
            expected_issue["actionable"] = True
            if comment.get("expectedIssue") != expected_issue:
                raise ValueError(
                    f"{case_id}/{comment_id}: expected-issue projection drift"
                )
            validity = comment.get("validity")
            if not isinstance(validity, Mapping) or any(
                (
                    validity.get("fixedLater") is not True,
                    validity.get("fixCommitSha")
                    != annotation.get("fixCommitSha"),
                    validity.get("fixEvidence")
                    != annotation.get("fixEvidence"),
                    validity.get("pathTransition")
                    != evidence.get("pathTransition"),
                )
            ):
                raise ValueError(
                    f"{case_id}/{comment_id}: fix-evidence projection drift"
                )
            if comment.get("adjudication") != annotation.get(
                "adjudication"
            ):
                raise ValueError(
                    f"{case_id}/{comment_id}: adjudication projection drift"
                )
            evidence_projection = {
                "bodySha256": comment.get("bodySha256"),
                "updatedAt": comment.get("sourceUpdatedAt"),
                "reviewer": comment.get("reviewer"),
                "originalCommitId": comment.get("originalCommitId"),
                "reviewId": comment.get("reviewId"),
                "sourceApiResponseSha256": comment.get(
                    "sourceApiResponseSha256"
                ),
                "sourceReviewResponseSha256": comment.get(
                    "sourceReviewResponseSha256"
                ),
                "pathTransition": validity.get("pathTransition"),
                "reviewThreadEvidence": comment.get(
                    "reviewThreadEvidence"
                ),
            }
            if evidence != {
                key: value
                for key, value in evidence_projection.items()
                if value is not None
            }:
                raise ValueError(
                    f"{case_id}/{comment_id}: source-evidence projection drift"
                )


def validate_source_curation_bundle(
    *,
    corpus: Mapping[str, Any],
    draft_path: Path,
    discovery_path: Path,
    discovery_selection_link_path: Path,
    source_archive_path: Path,
    thread_evidence_path: Path,
    curation_packet_path: Path,
    decisions_path: Path,
    selection_path: Path,
    current_comment_attestation_path: Path,
) -> dict[str, Any]:
    """Validate the raw-source -> curation -> released-corpus chain."""

    corpus_summary = validate_corpus(corpus, paper_ready=True)
    selection = _digest_artifact(
        read_json(selection_path),
        kind=SELECTION_KIND,
        digest_field="selectionDigest",
        label="released selection",
    )
    discovery = validate_discovery(
        read_json(discovery_path),
        repository=str(corpus["repository"]),
    )
    linkage = validate_discovery_selection_linkage(
        read_json(discovery_selection_link_path),
        discovery=discovery,
        selection=selection,
    )
    with tempfile.TemporaryDirectory(
        prefix="magento2-package-selection-"
    ) as directory:
        regenerated = release_selection(
            draft_path=draft_path,
            decisions_path=decisions_path,
            output=Path(directory) / "selection.json",
            paper_ready=True,
            source_archive_path=source_archive_path,
            thread_evidence_path=thread_evidence_path,
            curation_packet_path=curation_packet_path,
        )
    observed = dict(selection)
    expected = dict(regenerated)
    for payload in (observed, expected):
        payload.pop("generatedAt", None)
        payload.pop("selectionDigest", None)
    if observed != expected:
        raise ValueError(
            "released selection is not exactly derivable from packaged "
            "source and curation evidence"
        )

    provenance = corpus.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("released corpus provenance is missing")
    source_archive = _digest_artifact(
        read_json(source_archive_path),
        kind=DRAFT_SOURCE_ARCHIVE_KIND,
        digest_field="archiveDigest",
        label="source archive",
    )
    thread_evidence = _digest_artifact(
        read_json(thread_evidence_path),
        kind=THREAD_EVIDENCE_KIND,
        digest_field="threadEvidenceDigest",
        label="thread evidence",
    )
    packet = _digest_artifact(
        read_json(curation_packet_path),
        kind=PACKET_KIND,
        digest_field="packetDigest",
        label="curation packet",
    )
    if (
        corpus.get("corpusId") != selection.get("corpusId")
        or provenance.get("selectionDigest")
        != selection.get("selectionDigest")
        or provenance.get("selectionFileSha256")
        != sha256_text(selection_path.read_text(encoding="utf-8"))
        or provenance.get("sourceArchiveDigest")
        != source_archive.get("archiveDigest")
        or provenance.get("threadEvidenceDigest")
        != thread_evidence.get("threadEvidenceDigest")
        or selection.get("sourceArchiveDigest")
        != source_archive.get("archiveDigest")
        or selection.get("threadEvidenceDigest")
        != thread_evidence.get("threadEvidenceDigest")
        or selection.get("curationPacketDigest")
        != packet.get("packetDigest")
    ):
        raise ValueError(
            "packaged source/curation artifacts do not bind the released corpus"
        )
    _selection_projection(corpus=corpus, selection=selection)

    current = validate_current_comment_attestation(
        read_json(current_comment_attestation_path),
        draft_path=draft_path,
        repository=str(corpus["repository"]),
    )
    if current.get("sourceMode") != "live":
        raise ValueError(
            "publication package requires a live current-comment attestation"
        )
    return {
        "corpusDigest": corpus_summary["corpusDigest"],
        "selectionDigest": str(selection["selectionDigest"]),
        "discoveryDigest": str(discovery["discoveryDigest"]),
        "discoverySelectionLinkageDigest": str(
            linkage["linkageDigest"]
        ),
        "sourceArchiveDigest": str(source_archive["archiveDigest"]),
        "threadEvidenceDigest": str(
            thread_evidence["threadEvidenceDigest"]
        ),
        "curationPacketDigest": str(packet["packetDigest"]),
        "currentCommentAttestationDigest": str(
            current["attestationDigest"]
        ),
    }


def validate_primary_replay_bundle(
    *,
    corpus: Mapping[str, Any],
    replay_lock: Mapping[str, Any],
    replay_attestation: Mapping[str, Any],
    analysis_runs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate the live H replay and bind it to every primary analysis run."""

    validate_replay_lock(replay_lock, corpus)
    attestation_digest = validate_replay_attestation(
        replay_attestation,
        replay_lock,
        corpus,
    )
    if not analysis_runs:
        raise ValueError("publication package has no primary analysis runs")
    for run in analysis_runs:
        run_id = str(run.get("runId") or "unknown")
        analysis_config = run.get("analysisConfig")
        if (
            run.get("replayLockDigest") != replay_lock.get("lockDigest")
            or run.get("replayAttestationDigest") != attestation_digest
            or not isinstance(analysis_config, Mapping)
            or analysis_config.get("require_replay_attestation") is not True
        ):
            raise ValueError(
                f"primary run {run_id} is not bound to the packaged live replay"
            )
        max_age = analysis_config.get(
            "replay_attestation_max_age_seconds",
            MAX_PAPER_ATTESTATION_AGE_SECONDS,
        )
        validate_replay_attestation_freshness(
            replay_attestation,
            reference_at=run.get("startedAt"),
            max_age_seconds=max_age,
        )
    return {
        "lockDigest": str(replay_lock["lockDigest"]),
        "attestationDigest": attestation_digest,
        "analysisRuns": len(analysis_runs),
    }


def validate_post_fix_package_bundle(
    *,
    corpus: Mapping[str, Any],
    registration: Mapping[str, Any],
    seal_ledger: Mapping[str, Any],
    primary_replay_lock: Mapping[str, Any],
    primary_runs: Sequence[Mapping[str, Any]],
    primary_judgments: Sequence[Mapping[str, Any]],
    post_fix_plan: Mapping[str, Any],
    post_fix_lock: Mapping[str, Any],
    post_fix_attestation: Mapping[str, Any],
    post_fix_runs: Sequence[Mapping[str, Any]],
    post_fix_judgments: Sequence[Mapping[str, Any]],
    post_fix_controls: Sequence[Mapping[str, Any]],
    post_fix_control_set: Mapping[str, Any],
    post_fix_run_roots: Mapping[str, Path],
    post_fix_judgment_roots: Mapping[str, Path],
    repository: Path | None = None,
) -> dict[str, Any]:
    """Validate every registered H/F pair and its conditional endpoint."""

    validate_post_fix_plan(
        post_fix_plan,
        corpus,
        primary_replay_lock,
        repository=repository,
    )
    if (
        post_fix_lock.get("plan") != post_fix_plan
        or post_fix_lock.get("planDigest")
        != post_fix_plan.get("planDigest")
    ):
        raise ValueError(
            "standalone post-fix plan differs from the replay lock plan"
        )
    validate_post_fix_lock(
        post_fix_lock,
        corpus,
        primary_replay_lock,
    )
    validate_post_fix_attestation(
        post_fix_attestation,
        post_fix_lock,
        corpus,
        primary_replay_lock,
    )
    primary_by_id = {
        str(run.get("runId") or ""): run for run in primary_runs
    }
    post_fix_by_id = {
        str(run.get("runId") or ""): run for run in post_fix_runs
    }
    primary_judgment_by_id = {
        str(judgment.get("judgmentId") or ""): judgment
        for judgment in primary_judgments
    }
    post_fix_judgment_by_id = {
        str(judgment.get("judgmentId") or ""): judgment
        for judgment in post_fix_judgments
    }
    post_fix_registration = registration.get("postFixPlan")
    if not isinstance(post_fix_registration, Mapping):
        raise ValueError("registration has no post-fix plan")
    expected_run_ids = sorted(
        str(pair.get("postFixAnalysisRunId") or "")
        for pair in post_fix_registration.get("analysisPairs") or []
        if isinstance(pair, Mapping)
    )
    expected_judgment_ids = sorted(
        str(pair.get("postFixJudgmentId") or "")
        for pair in post_fix_registration.get("judgmentPairs") or []
        if isinstance(pair, Mapping)
    )
    if (
        not expected_run_ids
        or sorted(post_fix_by_id) != expected_run_ids
        or not expected_judgment_ids
        or sorted(post_fix_judgment_by_id) != expected_judgment_ids
        or sorted(post_fix_run_roots) != expected_run_ids
        or sorted(post_fix_judgment_roots) != expected_judgment_ids
    ):
        raise ValueError(
            "post-fix package does not cover the exact registered H/F pairs"
        )
    for post_fix_run in post_fix_runs:
        primary_id = str(
            post_fix_run.get("pairedPrimaryRunId") or ""
        )
        primary_run = primary_by_id.get(primary_id)
        if primary_run is None:
            raise ValueError(
                f"post-fix run has unknown primary pair {primary_id!r}"
            )
        validate_analysis_pair(
            primary_run,
            post_fix_run,
            corpus=corpus,
            post_fix_lock=post_fix_lock,
            primary_replay_lock=primary_replay_lock,
            post_fix_attestation=post_fix_attestation,
            post_fix_artifact_root=post_fix_run_roots[
                str(post_fix_run["runId"])
            ],
        )

    contexts: dict[str, dict[str, Mapping[str, Any]]] = {}
    for post_fix_judgment in post_fix_judgments:
        control_id = str(
            post_fix_judgment.get("judgmentId") or ""
        )
        primary_judgment = primary_judgment_by_id.get(
            str(post_fix_judgment.get("primaryJudgmentId") or "")
        )
        post_fix_run = post_fix_by_id.get(
            str(post_fix_judgment.get("postFixAnalysisRunId") or "")
        )
        if primary_judgment is None or post_fix_run is None:
            raise ValueError(
                f"post-fix judgment {control_id!r} has an unknown H/F pair"
            )
        primary_run = primary_by_id.get(
            str(primary_judgment.get("analysisRunId") or "")
        )
        if primary_run is None:
            raise ValueError(
                f"post-fix judgment {control_id!r} has no primary run"
            )
        validate_post_fix_judgment(
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
            post_fix_run_artifact_root=post_fix_run_roots[
                str(post_fix_run["runId"])
            ],
            artifact_root=post_fix_judgment_roots[control_id],
            repository=repository,
        )
        contexts[control_id] = {
            "sealLedger": seal_ledger,
            "primaryReplayLock": primary_replay_lock,
            "primaryRun": primary_run,
            "primaryJudgment": primary_judgment,
            "postFixRun": post_fix_run,
            "postFixLock": post_fix_lock,
            "postFixAttestation": post_fix_attestation,
            "postFixJudgment": post_fix_judgment,
            "postFixRunArtifactRoot": post_fix_run_roots[
                str(post_fix_run["runId"])
            ],
            "postFixJudgmentArtifactRoot": post_fix_judgment_roots[
                control_id
            ],
        }
    controls_by_id = {
        str(control.get("controlId") or ""): control
        for control in post_fix_controls
    }
    if (
        len(controls_by_id) != len(post_fix_controls)
        or sorted(controls_by_id) != expected_judgment_ids
    ):
        raise ValueError(
            "post-fix controls do not cover registered judgments exactly"
        )
    summary = validate_post_fix_control_set(
        post_fix_control_set,
        controls=[
            controls_by_id[control_id]
            for control_id in expected_judgment_ids
        ],
        corpus=corpus,
        registration=registration,
        control_contexts=contexts,
        repository=repository,
    )
    return {
        "planDigest": str(post_fix_plan["planDigest"]),
        "lockDigest": str(post_fix_lock["lockDigest"]),
        "attestationDigest": str(
            post_fix_attestation["attestationDigest"]
        ),
        "analysisRuns": len(post_fix_runs),
        "judgments": len(post_fix_judgments),
        "controlSetDigest": summary["controlSetDigest"],
        "controls": summary["controlCount"],
    }
