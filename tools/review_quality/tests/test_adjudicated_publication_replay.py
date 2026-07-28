from __future__ import annotations

import csv
import hashlib
import json

from tools.review_quality.adjudicated_publication_replay import (
    REQUIRED_COLUMNS,
    run_replay,
)


def _write_corpus(path, rows):
    fieldnames = sorted(REQUIRED_COLUMNS)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "severity": "HIGH",
                "issue_category": "BUG_RISK",
                "file_path": "src/example.php",
                "line_number": "1",
                "issue_scope": "LINE",
                "title": "Example",
                "reason": "A concrete post-change defect remains.",
                "suggested_fix_description": "Correct the defect.",
                "code_snippet": "broken();",
                "snapshot_anchor_state": "exact-line",
                "diff_path_state": "changed",
                "origin_analysis_id": "10",
                "origin_pr_number": "20",
                "origin_commit_hash": "a" * 40,
                **row,
            })
    corpus_bytes = path.read_bytes()
    manifest = {
        "status": "completed",
        "qualityReady": True,
        "qualityReadinessReasons": [],
        "sourceExport": {"sha256": "b" * 64},
        "cohort": {
            "originAnalysisId": "10",
            "originPrNumber": "20",
            "originCommitHash": "a" * 40,
            "findingCount": len(rows),
        },
        "corpus": {
            "sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        },
        "sourceSnapshot": {
            "supplied": True,
            "referencedFileSetSha256": "c" * 64,
        },
        "diff": {"supplied": True, "sha256": "d" * 64},
    }
    path.with_suffix(path.suffix + ".manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def test_replay_reports_raw_paired_counts_and_candidate_retention(tmp_path):
    corpus = tmp_path / "adjudicated.csv"
    _write_corpus(corpus, [
        {"id": "tp-kept", "review_verdict": "TP"},
        {"id": "fp-info", "review_verdict": "FP", "severity": "INFO"},
        {"id": "tp-info", "review_verdict": "TP", "severity": "INFO"},
        {
            "id": "fp-self",
            "review_verdict": "FP",
            "reason": "The current diff correctly fixes the reported issue.",
            "suggested_fix_description": "No fix required.",
        },
    ])

    report = run_replay(corpus, require_ready_manifest=False)

    assert report["status"] == "completed"
    assert report["corpus"]["findingCount"] == 4
    assert report["baseline"]["truePositives"] == 2
    assert report["baseline"]["falsePositives"] == 2
    assert report["currentDeterministicPublicationGate"] == {
        "truePositives": 1,
        "falsePositives": 0,
        "falseNegatives": 1,
        "precision": 1.0,
        "labeledCandidateRecall": 0.5,
        "published": 1,
        "withheld": 3,
        "modelCalls": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cost": 0.0,
    }
    assert report["dropReasons"] == {
        "info-severity": {
            "total": 2,
            "truePositives": 1,
            "falsePositives": 1,
        },
        "self-disqualifying": {
            "total": 1,
            "truePositives": 0,
            "falsePositives": 1,
        },
    }


def test_replay_rejects_duplicate_finding_ids(tmp_path):
    corpus = tmp_path / "duplicate.csv"
    _write_corpus(corpus, [
        {"id": "same", "review_verdict": "TP"},
        {"id": "same", "review_verdict": "FP"},
    ])

    try:
        run_replay(corpus)
    except ValueError as exception:
        assert "must be unique" in str(exception)
    else:
        raise AssertionError("duplicate identities must be rejected")


def test_replay_drops_missing_anchor_instead_of_inserting_placeholder(
    tmp_path,
):
    corpus = tmp_path / "missing-anchor.csv"
    _write_corpus(corpus, [{
        "id": "fp-without-anchor",
        "review_verdict": "FP",
        "code_snippet": "",
    }])

    report = run_replay(corpus)

    assert report["currentDeterministicPublicationGate"][
        "falsePositives"
    ] == 0
    assert report["dropReasons"]["missing-current-source-anchor"] == {
        "total": 1,
        "truePositives": 0,
        "falsePositives": 1,
    }


def test_replay_drops_nonempty_anchor_absent_from_pinned_snapshot(tmp_path):
    corpus = tmp_path / "stale-anchor.csv"
    _write_corpus(corpus, [{
        "id": "fp-with-stale-anchor",
        "review_verdict": "FP",
        "code_snippet": "removed_call();",
        "snapshot_anchor_state": "snippet-not-found",
    }])

    report = run_replay(corpus)

    assert report["currentDeterministicPublicationGate"][
        "falsePositives"
    ] == 0
    assert report["dropReasons"]["stale-current-source-anchor"] == {
        "total": 1,
        "truePositives": 0,
        "falsePositives": 1,
    }


def test_replay_rejects_corpus_changed_after_readiness_manifest(tmp_path):
    corpus = tmp_path / "tampered.csv"
    _write_corpus(corpus, [{
        "id": "candidate",
        "review_verdict": "TP",
    }])
    with corpus.open("a", encoding="utf-8") as handle:
        handle.write("\n")

    try:
        run_replay(corpus)
    except ValueError as exception:
        assert "corpus digest does not match" in str(exception)
    else:
        raise AssertionError("tampered corpus must be rejected")


def test_replay_rejects_unready_manifest(tmp_path):
    corpus = tmp_path / "unready.csv"
    _write_corpus(corpus, [{
        "id": "candidate",
        "review_verdict": "TP",
    }])
    manifest_path = corpus.with_suffix(".csv.manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["qualityReady"] = False
    manifest["qualityReadinessReasons"] = ["one finding remains unlabeled"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        run_replay(corpus)
    except ValueError as exception:
        assert "one finding remains unlabeled" in str(exception)
    else:
        raise AssertionError("unready corpus must be rejected")
