#!/usr/bin/env python3
"""Replay host publication policy over explicitly reviewed branch findings."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


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
)
from service.review.orchestrator.reconciliation import (  # noqa: E402
    deduplicate_final_issues,
)


VALIDATION_REQUIRED = {
    "branch_issue_id",
    "project_id",
    "branch_name",
    "validation_status",
}
ISSUE_REQUIRED = {
    "branch_issue_id",
    "branch_severity",
    "branch_issue_category",
    "branch_file_path",
    "branch_line_number",
    "current_line_number",
    "issue_scope",
    "branch_title",
    "branch_reason",
    "branch_suggested_fix_description",
    "branch_code_snippet",
}
LABELED_STATUSES = {
    "GENUINE_CURRENT",
    "DUPLICATE_OF_GENUINE",
    "FALSE_POSITIVE",
    "STALE_GENUINE_RESOLVED_IN_CODE",
}


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    csv.field_size_limit(sys.maxsize)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or ()), [
            {key: value or "" for key, value in row.items()}
            for row in reader
        ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _issue(row: Mapping[str, str]) -> CodeReviewIssue:
    try:
        line = max(
            1,
            int(
                row.get("current_line_number")
                or row.get("branch_line_number")
                or 1
            ),
        )
    except ValueError:
        line = 1
    return CodeReviewIssue(
        id=row["branch_issue_id"].strip(),
        severity=row["branch_severity"].strip(),
        category=row["branch_issue_category"].strip(),
        file=row["branch_file_path"].strip(),
        line=line,
        scope=row["issue_scope"].strip() or "LINE",
        title=row["branch_title"],
        reason=row["branch_reason"],
        suggestedFixDescription=row["branch_suggested_fix_description"],
        codeSnippet=row["branch_code_snippet"],
    )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _metrics(
    rows: Iterable[tuple[Mapping[str, str], CodeReviewIssue]],
    published_ids: set[str],
    positive: Callable[[str], bool],
) -> dict[str, Any]:
    counts = Counter()
    for validation, issue in rows:
        expected = positive(validation["validation_status"])
        published = str(issue.id) in published_ids
        counts[
            "truePositives"
            if expected and published
            else "falseNegatives"
            if expected
            else "falsePositives"
            if published
            else "trueNegatives"
        ] += 1
    true_positives = counts["truePositives"]
    false_positives = counts["falsePositives"]
    false_negatives = counts["falseNegatives"]
    return {
        "truePositives": true_positives,
        "falsePositives": false_positives,
        "falseNegatives": false_negatives,
        "trueNegatives": counts["trueNegatives"],
        "precision": _ratio(
            true_positives,
            true_positives + false_positives,
        ),
        "labeledCandidateRecall": _ratio(
            true_positives,
            true_positives + false_negatives,
        ),
    }


def _file_family(issue: CodeReviewIssue) -> str:
    path = Path(issue.file or "")
    suffix = path.suffix.lower()
    if suffix == ".xml":
        return f"xml:{path.name.lower()}"
    return suffix.lstrip(".") or "<none>"


def _metric_breakdown(
    rows: Iterable[tuple[Mapping[str, str], CodeReviewIssue]],
    published_ids: set[str],
    positive: Callable[[str], bool],
    value: Callable[[CodeReviewIssue], str],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[tuple[Mapping[str, str], CodeReviewIssue]]] = {}
    for validation, issue in rows:
        key = value(issue).strip() or "<none>"
        grouped.setdefault(key, []).append((validation, issue))
    return {
        key: {
            "candidates": len(grouped[key]),
            **_metrics(grouped[key], published_ids, positive),
        }
        for key in sorted(grouped)
    }


def _breakdowns(
    rows: list[tuple[Mapping[str, str], CodeReviewIssue]],
    published_ids: set[str],
    positive: Callable[[str], bool],
) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "severity": _metric_breakdown(
            rows,
            published_ids,
            positive,
            lambda issue: (issue.severity or "").upper(),
        ),
        "category": _metric_breakdown(
            rows,
            published_ids,
            positive,
            lambda issue: (issue.category or "").upper(),
        ),
        "fileFamily": _metric_breakdown(
            rows,
            published_ids,
            positive,
            _file_family,
        ),
    }


def replay(
    validation_path: Path,
    issue_path: Path,
    *,
    precision_target: float = 0.60,
    max_recall_loss: float = 0.05,
) -> dict[str, Any]:
    validation_path = validation_path.resolve()
    issue_path = issue_path.resolve()
    validation_columns, validation_rows = _read_csv(validation_path)
    issue_columns, issue_rows = _read_csv(issue_path)
    missing_validation = sorted(
        VALIDATION_REQUIRED - set(validation_columns)
    )
    missing_issue = sorted(ISSUE_REQUIRED - set(issue_columns))
    if missing_validation:
        raise ValueError(
            "validation export is missing columns: "
            + ", ".join(missing_validation)
        )
    if missing_issue:
        raise ValueError(
            "issue export is missing columns: " + ", ".join(missing_issue)
        )

    issue_by_id: dict[str, Mapping[str, str]] = {}
    for row in issue_rows:
        identity = row["branch_issue_id"].strip()
        if not identity:
            raise ValueError("issue export contains an empty branch_issue_id")
        if identity in issue_by_id:
            raise ValueError(f"issue export repeats branch issue {identity}")
        issue_by_id[identity] = row

    labeled = []
    missing_issue_ids = []
    for validation in validation_rows:
        status = validation["validation_status"].strip()
        if status not in LABELED_STATUSES:
            continue
        identity = validation["branch_issue_id"].strip()
        issue_row = issue_by_id.get(identity)
        if issue_row is None:
            missing_issue_ids.append(identity)
            continue
        labeled.append((validation, _issue(issue_row)))
    if missing_issue_ids:
        raise ValueError(
            "labeled validation rows are absent from issue export: "
            + ", ".join(sorted(missing_issue_ids)[:20])
        )
    if not labeled:
        raise ValueError("no explicit reviewed findings are shared by the exports")

    projects = {
        (
            validation["project_id"].strip(),
            validation["branch_name"].strip(),
        )
        for validation, _ in labeled
    }
    if len(projects) != 1:
        raise ValueError("labeled cohort spans multiple project/branch identities")

    excluded = Counter()
    contract_ready = []
    for validation, issue in labeled:
        if not (issue.codeSnippet or "").strip():
            excluded["missingCodeSnippet"] += 1
            continue
        if (issue.severity or "").strip().upper() == "INFO":
            excluded["infoSeverity"] += 1
            continue
        contract_ready.append((validation, issue))
    if not contract_ready:
        raise ValueError("no contract-ready labeled candidates remain")

    request = ReviewRequestDto.model_construct(
        previousCodeAnalysisIssues=[],
    )
    candidates = [issue for _, issue in contract_ready]
    policy_kept, dropped = _drop_non_publishable_issues(candidates, request)
    current = deduplicate_final_issues(policy_kept)
    all_ids = {str(issue.id) for issue in candidates}
    policy_ids = {str(issue.id) for issue in policy_kept}
    current_ids = {str(issue.id) for issue in current}

    origin_positive = lambda status: status != "FALSE_POSITIVE"
    current_unique_positive = lambda status: status == "GENUINE_CURRENT"
    baseline_origin = _metrics(contract_ready, all_ids, origin_positive)
    current_origin = _metrics(contract_ready, current_ids, origin_positive)
    baseline_unique = _metrics(
        contract_ready,
        all_ids,
        current_unique_positive,
    )
    current_unique = _metrics(
        contract_ready,
        current_ids,
        current_unique_positive,
    )
    recall_loss = (
        baseline_origin["labeledCandidateRecall"]
        - current_origin["labeledCandidateRecall"]
    )
    checks = {
        "originCandidatePrecision": (
            current_origin["precision"] >= precision_target
        ),
        "originCandidateRecallLoss": recall_loss <= max_recall_loss,
        "zeroProviderCalls": True,
    }
    status_counts = Counter(
        validation["validation_status"] for validation, _ in labeled
    )
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "scope": (
            "human-labeled generated-candidate publication replay; "
            "not candidate-generation recall"
        ),
        "inputs": {
            "validation": {
                "name": validation_path.name,
                "sha256": _sha256(validation_path),
                "rows": len(validation_rows),
            },
            "issues": {
                "name": issue_path.name,
                "sha256": _sha256(issue_path),
                "rows": len(issue_rows),
            },
        },
        "cohort": {
            "projectId": next(iter(projects))[0],
            "branch": next(iter(projects))[1],
            "explicitlyReviewed": len(labeled),
            "labelCounts": dict(sorted(status_counts.items())),
            "contractReady": len(contract_ready),
            "contractExclusions": dict(sorted(excluded.items())),
        },
        "targets": {
            "precision": precision_target,
            "maxLabeledCandidateRecallLoss": max_recall_loss,
        },
        "checks": checks,
        "originCandidateCorrectness": {
            "positiveLabels": [
                "GENUINE_CURRENT",
                "DUPLICATE_OF_GENUINE",
                "STALE_GENUINE_RESOLVED_IN_CODE",
            ],
            "baseline": baseline_origin,
            "current": current_origin,
            "pairedDeltas": {
                "precision": (
                    current_origin["precision"]
                    - baseline_origin["precision"]
                ),
                "labeledCandidateRecall": (
                    current_origin["labeledCandidateRecall"]
                    - baseline_origin["labeledCandidateRecall"]
                ),
            },
        },
        "currentUniqueActionability": {
            "positiveLabels": ["GENUINE_CURRENT"],
            "baseline": baseline_unique,
            "current": current_unique,
            "pairedDeltas": {
                "precision": (
                    current_unique["precision"]
                    - baseline_unique["precision"]
                ),
                "labeledCandidateRecall": (
                    current_unique["labeledCandidateRecall"]
                    - baseline_unique["labeledCandidateRecall"]
                ),
            },
        },
        "diagnosticBreakdowns": {
            "scope": (
                "reporting only; these strata do not change publication policy"
            ),
            "originCandidateCorrectness": {
                "baseline": _breakdowns(
                    contract_ready,
                    all_ids,
                    origin_positive,
                ),
                "current": _breakdowns(
                    contract_ready,
                    current_ids,
                    origin_positive,
                ),
            },
            "currentUniqueActionability": {
                "baseline": _breakdowns(
                    contract_ready,
                    all_ids,
                    current_unique_positive,
                ),
                "current": _breakdowns(
                    contract_ready,
                    current_ids,
                    current_unique_positive,
                ),
            },
        },
        "publication": {
            "published": len(current_ids),
            "withheld": len(all_ids - current_ids),
            "nonPublishablePolicyWithheld": len(all_ids - policy_ids),
            "deterministicDedupWithheld": len(policy_ids - current_ids),
            "dropTokens": len(dropped),
            "modelCalls": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "cost": 0.0,
        },
        "limitations": [
            (
                "The exports contain findings generated by prior reviews; "
                "defects no reviewed mode generated are absent."
            ),
            (
                "The original source snapshots and diffs are not bundled, so "
                "source-anchor, lifecycle, plugin-evidence, and "
                "candidate-generation changes are not replayed. The final "
                "field-only deterministic dedup runs, but original batch "
                "provenance and plugin proof IDs are unavailable."
            ),
            (
                "STALE_GENUINE_RESOLVED_IN_CODE is positive only in the "
                "origin-correctness lens and negative in current actionability."
            ),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("validation_export", type=Path)
    parser.add_argument("issue_export", type=Path)
    parser.add_argument("--precision-target", type=float, default=0.60)
    parser.add_argument("--max-recall-loss", type=float, default=0.05)
    parser.add_argument("--enforce", action="store_true")
    arguments = parser.parse_args()
    try:
        report = replay(
            arguments.validation_export,
            arguments.issue_export,
            precision_target=arguments.precision_target,
            max_recall_loss=arguments.max_recall_loss,
        )
    except Exception as exception:
        print(json.dumps({
            "status": "error",
            "error": f"{type(exception).__name__}: {exception}",
        }, indent=2, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if arguments.enforce and report["status"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
