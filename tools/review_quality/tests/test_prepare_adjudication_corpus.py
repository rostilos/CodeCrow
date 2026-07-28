from __future__ import annotations

import csv
import json

import pytest

from tools.review_quality.prepare_adjudication_corpus import (
    OUTPUT_COLUMNS,
    prepare_corpus,
)


def _write_csv(path, fieldnames, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _source_row(**updates):
    row = {
        "id": "12",
        "origin_analysis_id": "44",
        "origin_pr_number": "91",
        "origin_commit_hash": "a" * 40,
        "severity": "HIGH",
        "issue_category": "BUG_RISK",
        "file_path": "app/code/Acme/Checkout/Model/Thing.php",
        "line_number": "2",
        "issue_scope": "LINE",
        "title": "Incorrect return",
        "reason": "The changed method returns the wrong value.",
        "suggested_fix_description": "Return the expected value.",
        "code_snippet": "return false;",
        "is_resolved": "true",
        "resolved_by": "Some User",
    }
    row.update(updates)
    return row


def test_prepares_one_identity_and_does_not_infer_labels(tmp_path):
    source = tmp_path / "branch-issues.csv"
    source_rows = [
        _source_row(),
        _source_row(
            id="13",
            file_path="app/code/Acme/Checkout/Model/Other.php",
            line_number="1",
            code_snippet="return true;",
            is_resolved="false",
            resolved_by="",
        ),
        _source_row(
            id="99",
            origin_analysis_id="45",
            origin_pr_number="92",
            origin_commit_hash="b" * 40,
        ),
    ]
    _write_csv(source, list(source_rows[0]), source_rows)

    source_root = tmp_path / "head"
    first = source_root / "app/code/Acme/Checkout/Model/Thing.php"
    first.parent.mkdir(parents=True)
    first.write_text("<?php\nreturn false;\n", encoding="utf-8")
    second = source_root / "app/code/Acme/Checkout/Model/Other.php"
    second.write_text("return true;\n", encoding="utf-8")
    diff = tmp_path / "change.diff"
    diff.write_text(
        "diff --git a/app/code/Acme/Checkout/Model/Thing.php "
        "b/app/code/Acme/Checkout/Model/Thing.php\n"
        "diff --git a/app/code/Acme/Checkout/Model/Other.php "
        "b/app/code/Acme/Checkout/Model/Other.php\n",
        encoding="utf-8",
    )
    output = tmp_path / "adjudication.csv"

    report = prepare_corpus(
        source,
        output,
        analysis_id="44",
        source_root=source_root,
        diff_path=diff,
    )

    with output.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["id"] for row in rows] == ["12", "13"]
    assert all(row["review_verdict"] == "" for row in rows)
    assert [row["snapshot_anchor_state"] for row in rows] == [
        "exact-line",
        "exact-line",
    ]
    assert all(row["diff_path_state"] == "changed" for row in rows)
    assert report["cohort"] == {
        "originAnalysisId": "44",
        "originPrNumber": "91",
        "originCommitHash": "a" * 40,
        "findingCount": 2,
    }
    assert report["qualityReady"] is False
    assert report["qualityReadinessReasons"] == [
        "2 finding(s) remain unlabeled",
    ]
    manifest = json.loads(
        output.with_suffix(".csv.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["labelPolicy"].startswith(
        "TP/FP labels are copied only from --labels"
    )


def test_preserves_only_explicit_labels_and_becomes_ready(tmp_path):
    source = tmp_path / "source.csv"
    source_row = _source_row()
    _write_csv(source, list(source_row), [source_row])
    labels = tmp_path / "labels.csv"
    _write_csv(labels, OUTPUT_COLUMNS, [{
        **{column: "" for column in OUTPUT_COLUMNS},
        "id": "12",
        "review_verdict": "fp",
        "adjudication_note": "The caller already handles this value.",
        "adjudicator": "reviewer",
        "adjudicated_at": "2026-07-25T00:00:00Z",
    }])
    source_root = tmp_path / "head"
    source_file = source_root / source_row["file_path"]
    source_file.parent.mkdir(parents=True)
    source_file.write_text("<?php\nreturn false;\n", encoding="utf-8")
    diff = tmp_path / "change.diff"
    diff.write_text(
        f"diff --git a/{source_row['file_path']} "
        f"b/{source_row['file_path']}\n",
        encoding="utf-8",
    )
    output = tmp_path / "corpus.csv"

    report = prepare_corpus(
        source,
        output,
        analysis_id="44",
        labels_path=labels,
        source_root=source_root,
        diff_path=diff,
    )

    with output.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["review_verdict"] == "FP"
    assert row["adjudication_note"] == (
        "The caller already handles this value."
    )
    assert report["qualityReady"] is True
    assert report["qualityReadinessReasons"] == []


def test_rejects_analysis_that_spans_multiple_pr_identities(tmp_path):
    source = tmp_path / "source.csv"
    rows = [
        _source_row(),
        _source_row(id="13", origin_pr_number="92"),
    ]
    _write_csv(source, list(rows[0]), rows)

    with pytest.raises(ValueError, match="multiple origin_pr_number"):
        prepare_corpus(
            source,
            tmp_path / "output.csv",
            analysis_id="44",
        )


def test_rejects_label_for_another_cohort(tmp_path):
    source = tmp_path / "source.csv"
    source_row = _source_row()
    _write_csv(source, list(source_row), [source_row])
    labels = tmp_path / "labels.csv"
    _write_csv(labels, OUTPUT_COLUMNS, [{
        **{column: "" for column in OUTPUT_COLUMNS},
        "id": "999",
        "review_verdict": "TP",
    }])

    with pytest.raises(ValueError, match="outside selected cohort"):
        prepare_corpus(
            source,
            tmp_path / "output.csv",
            analysis_id="44",
            labels_path=labels,
        )


def test_missing_candidate_snippet_is_recorded_but_can_be_adjudicated(
    tmp_path,
):
    source = tmp_path / "source.csv"
    source_row = _source_row(code_snippet="")
    _write_csv(source, list(source_row), [source_row])
    labels = tmp_path / "labels.csv"
    _write_csv(labels, OUTPUT_COLUMNS, [{
        **{column: "" for column in OUTPUT_COLUMNS},
        "id": "12",
        "review_verdict": "FP",
    }])
    source_root = tmp_path / "head"
    source_file = source_root / source_row["file_path"]
    source_file.parent.mkdir(parents=True)
    source_file.write_text("<?php\nreturn false;\n", encoding="utf-8")
    diff = tmp_path / "change.diff"
    diff.write_text(
        f"diff --git a/{source_row['file_path']} "
        f"b/{source_row['file_path']}\n",
        encoding="utf-8",
    )

    report = prepare_corpus(
        source,
        tmp_path / "corpus.csv",
        analysis_id="44",
        labels_path=labels,
        source_root=source_root,
        diff_path=diff,
    )

    assert report["qualityReady"] is True
    assert report["sourceSnapshot"]["anchorStates"] == {
        "missing-snippet": 1,
    }
