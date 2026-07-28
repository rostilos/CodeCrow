#!/usr/bin/env python3
"""Replay the current deterministic publication gate over adjudicated findings."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INFERENCE_SOURCE = (
    PROJECT_ROOT / "python-ecosystem" / "inference-orchestrator" / "src"
)
if str(INFERENCE_SOURCE) not in sys.path:
    sys.path.insert(0, str(INFERENCE_SOURCE))

from model.dtos import ReviewRequestDto  # noqa: E402
from model.output_schemas import CodeReviewIssue  # noqa: E402
from service.review.orchestrator.verification_agent import (  # noqa: E402
    _drop_non_publishable_issues,
    _is_self_disqualifying_issue,
)


REQUIRED_COLUMNS = {
    "id",
    "origin_analysis_id",
    "origin_pr_number",
    "origin_commit_hash",
    "severity",
    "issue_category",
    "file_path",
    "line_number",
    "issue_scope",
    "title",
    "reason",
    "suggested_fix_description",
    "code_snippet",
    "snapshot_anchor_state",
    "diff_path_state",
    "review_verdict",
}

SOURCE_ANCHOR_STATES = {
    "exact-line",
    "moved",
    "missing-snippet",
    "snippet-not-found",
}
PUBLISHABLE_SOURCE_ANCHOR_STATES = {"exact-line", "moved"}


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _issue(row: Mapping[str, str]) -> CodeReviewIssue:
    try:
        line = max(1, int(row.get("line_number") or 1))
    except ValueError:
        line = 1
    return CodeReviewIssue(
        id=row["id"],
        severity=row["severity"],
        category=row["issue_category"],
        file=row["file_path"],
        line=line,
        scope=row.get("issue_scope") or "LINE",
        title=row.get("title") or "",
        reason=row.get("reason") or "",
        suggestedFixDescription=row.get("suggested_fix_description") or "",
        codeSnippet=row.get("code_snippet") or "",
    )


def _load_readiness_manifest(
    corpus_path: Path,
    corpus_bytes: bytes,
    rows: list[Mapping[str, str]],
) -> tuple[Path, dict[str, Any]]:
    manifest_path = corpus_path.with_suffix(
        corpus_path.suffix + ".manifest.json"
    )
    if not manifest_path.is_file():
        raise ValueError(
            f"quality-readiness manifest is missing: {manifest_path}"
        )
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exception:
        raise ValueError(
            "quality-readiness manifest is not valid JSON"
        ) from exception
    if not isinstance(manifest, dict):
        raise ValueError("quality-readiness manifest must be a JSON object")
    if manifest.get("status") != "completed":
        raise ValueError("quality-readiness manifest is not completed")
    if manifest.get("qualityReady") is not True:
        reasons = manifest.get("qualityReadinessReasons") or []
        raise ValueError(
            "quality-readiness manifest is not ready"
            + (f": {'; '.join(map(str, reasons))}" if reasons else "")
        )

    source_export = manifest.get("sourceExport")
    corpus = manifest.get("corpus")
    cohort = manifest.get("cohort")
    snapshot = manifest.get("sourceSnapshot")
    diff = manifest.get("diff")
    if not all(
        isinstance(value, dict)
        for value in (source_export, corpus, cohort, snapshot, diff)
    ):
        raise ValueError(
            "quality-readiness manifest is missing corpus/cohort evidence"
        )
    actual_corpus_digest = hashlib.sha256(corpus_bytes).hexdigest()
    if corpus.get("sha256") != actual_corpus_digest:
        raise ValueError(
            "quality-readiness manifest corpus digest does not match"
        )
    if cohort.get("findingCount") != len(rows):
        raise ValueError(
            "quality-readiness manifest finding count does not match"
        )
    identity_fields = (
        ("origin_analysis_id", "originAnalysisId"),
        ("origin_pr_number", "originPrNumber"),
        ("origin_commit_hash", "originCommitHash"),
    )
    for row_field, manifest_field in identity_fields:
        values = {row[row_field].strip() for row in rows}
        if len(values) != 1:
            raise ValueError(
                f"adjudicated corpus spans multiple {row_field} values"
            )
        if cohort.get(manifest_field) != next(iter(values)):
            raise ValueError(
                f"quality-readiness manifest {manifest_field} does not match"
            )
    if not source_export.get("sha256"):
        raise ValueError(
            "quality-readiness manifest has no source-export digest"
        )
    if snapshot.get("supplied") is not True:
        raise ValueError(
            "quality-readiness manifest has no source snapshot"
        )
    if not snapshot.get("referencedFileSetSha256"):
        raise ValueError(
            "quality-readiness manifest has no referenced-source digest"
        )
    if diff.get("supplied") is not True or not diff.get("sha256"):
        raise ValueError("quality-readiness manifest has no pinned diff")
    return manifest_path, manifest


def _metrics(
    rows: list[Mapping[str, str]],
    published_ids: set[str],
) -> dict[str, Any]:
    true_positives = sum(
        row["review_verdict"] == "TP" and row["id"] in published_ids
        for row in rows
    )
    false_positives = sum(
        row["review_verdict"] == "FP" and row["id"] in published_ids
        for row in rows
    )
    false_negatives = sum(
        row["review_verdict"] == "TP" and row["id"] not in published_ids
        for row in rows
    )
    return {
        "truePositives": true_positives,
        "falsePositives": false_positives,
        "falseNegatives": false_negatives,
        "precision": _ratio(
            true_positives,
            true_positives + false_positives,
        ),
        "labeledCandidateRecall": _ratio(
            true_positives,
            true_positives + false_negatives,
        ),
        "published": true_positives + false_positives,
        "withheld": false_negatives + sum(
            row["review_verdict"] == "FP" and row["id"] not in published_ids
            for row in rows
        ),
        "modelCalls": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cost": 0.0,
    }


def run_replay(
    corpus_path: Path,
    *,
    precision_target: float = 0.60,
    max_recall_loss: float = 0.05,
    require_ready_manifest: bool = True,
) -> dict[str, Any]:
    corpus_bytes = corpus_path.read_bytes()
    csv.field_size_limit(sys.maxsize)
    with corpus_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or ())
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError(
                "adjudicated corpus is missing columns: " + ", ".join(missing)
            )
        rows = [dict(row) for row in reader]
    if not rows:
        raise ValueError("adjudicated corpus is empty")
    invalid_verdicts = sorted({
        row["review_verdict"]
        for row in rows
        if row["review_verdict"] not in {"TP", "FP"}
    })
    if invalid_verdicts:
        raise ValueError(
            "review_verdict must be TP or FP: " + ", ".join(invalid_verdicts)
        )
    identities = [row["id"] for row in rows]
    if any(not identity for identity in identities):
        raise ValueError("every adjudicated row requires a non-empty id")
    if len(identities) != len(set(identities)):
        raise ValueError("adjudicated finding ids must be unique")
    for field in (
        "origin_analysis_id",
        "origin_pr_number",
        "origin_commit_hash",
    ):
        if any(not row[field].strip() for row in rows):
            raise ValueError(
                f"every adjudicated row requires a non-empty {field}"
            )
    if any(row["diff_path_state"] != "changed" for row in rows):
        raise ValueError(
            "every adjudicated row must reference a path in the pinned diff"
        )
    invalid_anchor_states = sorted({
        row["snapshot_anchor_state"]
        for row in rows
        if row["snapshot_anchor_state"] not in SOURCE_ANCHOR_STATES
    })
    if invalid_anchor_states:
        raise ValueError(
            "adjudicated corpus has invalid snapshot_anchor_state values: "
            + ", ".join(invalid_anchor_states)
        )

    manifest_path = None
    manifest = None
    if require_ready_manifest:
        manifest_path, manifest = _load_readiness_manifest(
            corpus_path,
            corpus_bytes,
            rows,
        )

    issues = [_issue(row) for row in rows]
    request = ReviewRequestDto(
        projectId=0,
        projectVcsWorkspace="offline",
        projectVcsRepoSlug="adjudicated-publication-replay",
        projectWorkspace="offline",
        projectNamespace="adjudicated-publication-replay",
        aiProvider="PROVIDER_FREE",
        aiModel="no-model",
        aiApiKey="not-used",
    )
    kept, _ = _drop_non_publishable_issues(issues, request)
    policy_kept_ids = {str(issue.id) for issue in kept}
    kept_ids = {
        row["id"]
        for row in rows
        if (
            row["id"] in policy_kept_ids
            and row["snapshot_anchor_state"]
            in PUBLISHABLE_SOURCE_ANCHOR_STATES
        )
    }
    all_ids = set(identities)
    baseline = _metrics(rows, all_ids)
    current = _metrics(rows, kept_ids)

    drop_reasons = Counter()
    verdict_by_drop_reason = Counter()
    for row, issue in zip(rows, issues):
        if row["id"] in kept_ids:
            continue
        reasons = []
        if issue.severity.strip().upper() == "INFO":
            reasons.append("info-severity")
        if not (issue.codeSnippet or "").strip():
            reasons.append("missing-current-source-anchor")
        elif row["snapshot_anchor_state"] == "snippet-not-found":
            reasons.append("stale-current-source-anchor")
        if _is_self_disqualifying_issue(issue):
            reasons.append("self-disqualifying")
        if not reasons:
            reasons.append("other")
        for reason in reasons:
            drop_reasons[reason] += 1
            verdict_by_drop_reason[(reason, row["review_verdict"])] += 1

    by_category = {}
    for category in sorted({row["issue_category"] for row in rows}):
        category_rows = [
            row for row in rows if row["issue_category"] == category
        ]
        category_ids = {
            row["id"] for row in category_rows if row["id"] in kept_ids
        }
        by_category[category] = {
            "baseline": _metrics(
                category_rows,
                {row["id"] for row in category_rows},
            ),
            "current": _metrics(category_rows, category_ids),
        }

    recall_loss = (
        baseline["labeledCandidateRecall"]
        - current["labeledCandidateRecall"]
    )
    checks = {
        "generalPrecision": current["precision"] >= precision_target,
        "labeledCandidateRecallLoss": recall_loss <= max_recall_loss,
        "providerCalls": (
            baseline["modelCalls"] == 0 and current["modelCalls"] == 0
        ),
    }
    return {
        "status": "completed",
        "qualityGateStatus": (
            "passed" if all(checks.values()) else "failed"
        ),
        "corpus": {
            "path": str(corpus_path),
            "sha256": hashlib.sha256(corpus_bytes).hexdigest(),
            "readinessManifest": (
                {
                    "path": str(manifest_path),
                    "sha256": hashlib.sha256(
                        manifest_path.read_bytes()
                    ).hexdigest(),
                    "sourceExportSha256": manifest[
                        "sourceExport"
                    ]["sha256"],
                    "referencedFileSetSha256": manifest[
                        "sourceSnapshot"
                    ]["referencedFileSetSha256"],
                    "diffSha256": manifest["diff"]["sha256"],
                }
                if manifest_path is not None and manifest is not None
                else None
            ),
            "findingCount": len(rows),
            "analysisCount": len({
                row.get("origin_analysis_id")
                for row in rows
                if row.get("origin_analysis_id")
            }),
            "pullRequestCount": len({
                row.get("origin_pr_number")
                for row in rows
                if row.get("origin_pr_number")
            }),
        },
        "targets": {
            "precision": precision_target,
            "maxLabeledCandidateRecallLoss": max_recall_loss,
        },
        "checks": checks,
        "baseline": baseline,
        "currentDeterministicPublicationGate": current,
        "pairedDeltas": {
            "precision": current["precision"] - baseline["precision"],
            "labeledCandidateRecall": (
                current["labeledCandidateRecall"]
                - baseline["labeledCandidateRecall"]
            ),
            "truePositives": (
                current["truePositives"] - baseline["truePositives"]
            ),
            "falsePositives": (
                current["falsePositives"] - baseline["falsePositives"]
            ),
            "falseNegatives": (
                current["falseNegatives"] - baseline["falseNegatives"]
            ),
            "modelCalls": 0,
            "cost": 0.0,
        },
        "dropReasons": {
            reason: {
                "total": drop_reasons[reason],
                "truePositives": verdict_by_drop_reason[(reason, "TP")],
                "falsePositives": verdict_by_drop_reason[(reason, "FP")],
            }
            for reason in sorted(drop_reasons)
        },
        "byCategory": by_category,
        "limitations": [
            (
                "The corpus contains adjudicated generated findings, not all "
                "reviewable defects. Labeled-candidate recall therefore measures "
                "publication retention and cannot measure candidate-generation FNs."
            ),
            (
                "This replay exercises the current provider-free publication "
                "gate only. Historical exact RAG/plugin evidence and review-model "
                "verification outputs are not reconstructed."
            ),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--precision-target", type=float, default=0.60)
    parser.add_argument("--max-recall-loss", type=float, default=0.05)
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Exit non-zero when the configured quality gate is not met.",
    )
    arguments = parser.parse_args()
    try:
        report = run_replay(
            arguments.corpus,
            precision_target=arguments.precision_target,
            max_recall_loss=arguments.max_recall_loss,
        )
    except Exception as exception:
        report = {
            "status": "failed",
            "error": f"{type(exception).__name__}: {exception}",
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "completed":
        return 1
    if arguments.enforce and report["qualityGateStatus"] != "passed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
