#!/usr/bin/env python3
"""Prepare one immutable review cohort for explicit human TP/FP adjudication."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shlex
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


SOURCE_REQUIRED_COLUMNS = {
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
}

ADJUDICATION_COLUMNS = (
    "review_verdict",
    "adjudication_note",
    "adjudicator",
    "adjudicated_at",
)

OUTPUT_COLUMNS = (
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
    *ADJUDICATION_COLUMNS,
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    csv.field_size_limit(sys.maxsize)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or ())
        return columns, [
            {
                key: "" if value is None else value
                for key, value in row.items()
            }
            for row in reader
        ]


def _numeric_sort_key(value: str) -> tuple[int, int | str]:
    stripped = value.strip()
    try:
        return 0, int(stripped)
    except ValueError:
        return 1, stripped


def _selected_rows(
    source_path: Path,
    *,
    analysis_id: str,
    pr_number: str | None,
    commit_hash: str | None,
) -> list[dict[str, str]]:
    columns, rows = _read_csv(source_path)
    missing = sorted(SOURCE_REQUIRED_COLUMNS - set(columns))
    if missing:
        raise ValueError(
            "source export is missing columns: " + ", ".join(missing)
        )

    selected = [
        row
        for row in rows
        if row["origin_analysis_id"].strip() == analysis_id
    ]
    if pr_number is not None:
        selected = [
            row
            for row in selected
            if row["origin_pr_number"].strip() == pr_number
        ]
    if commit_hash is not None:
        selected = [
            row
            for row in selected
            if row["origin_commit_hash"].strip() == commit_hash
        ]
    if not selected:
        raise ValueError("no findings match the requested cohort identity")

    identities = [row["id"].strip() for row in selected]
    if any(not identity for identity in identities):
        raise ValueError("every selected finding requires a non-empty id")
    if len(identities) != len(set(identities)):
        raise ValueError("selected finding ids must be unique")

    for field in (
        "origin_analysis_id",
        "origin_pr_number",
        "origin_commit_hash",
    ):
        values = {row[field].strip() for row in selected}
        if "" in values:
            raise ValueError(f"selected cohort contains an empty {field}")
        if len(values) != 1:
            raise ValueError(
                f"selected cohort spans multiple {field} values: "
                + ", ".join(sorted(values))
            )

    return sorted(selected, key=lambda row: _numeric_sort_key(row["id"]))


def _load_labels(
    labels_path: Path | None,
    selected_ids: set[str],
) -> dict[str, dict[str, str]]:
    if labels_path is None:
        return {}
    columns, rows = _read_csv(labels_path)
    missing = sorted({"id", *ADJUDICATION_COLUMNS} - set(columns))
    if missing:
        raise ValueError(
            "labels file is missing columns: " + ", ".join(missing)
        )
    labels: dict[str, dict[str, str]] = {}
    for row in rows:
        identity = row["id"].strip()
        if not identity:
            raise ValueError("labels file contains an empty id")
        if identity in labels:
            raise ValueError(f"labels file repeats finding id {identity}")
        if identity not in selected_ids:
            raise ValueError(
                f"labels file contains finding outside selected cohort: {identity}"
            )
        verdict = row["review_verdict"].strip().upper()
        if verdict not in {"", "TP", "FP"}:
            raise ValueError(
                f"review_verdict for {identity} must be blank, TP, or FP"
            )
        labels[identity] = {
            "review_verdict": verdict,
            "adjudication_note": row["adjudication_note"],
            "adjudicator": row["adjudicator"],
            "adjudicated_at": row["adjudicated_at"],
        }
    return labels


def _safe_source_path(source_root: Path, repository_path: str) -> Path | None:
    normalized = repository_path.strip().replace("\\", "/").lstrip("/")
    if not normalized:
        return None
    candidate = (source_root / normalized).resolve()
    try:
        candidate.relative_to(source_root.resolve())
    except ValueError:
        return None
    return candidate


def _anchor_state(
    source_root: Path | None,
    repository_path: str,
    line_number: str,
    snippet: str,
) -> str:
    if source_root is None:
        return "not-checked"
    source_path = _safe_source_path(source_root, repository_path)
    if source_path is None:
        return "invalid-path"
    if not source_path.is_file():
        return "missing-file"
    if not snippet.strip():
        return "missing-snippet"

    try:
        content = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "unreadable-file"
    offset = content.find(snippet)
    if offset < 0:
        return "snippet-not-found"
    try:
        expected_line = max(1, int(line_number))
    except ValueError:
        return "found-other-line"
    actual_line = content.count("\n", 0, offset) + 1
    return (
        "exact-line"
        if actual_line == expected_line
        else "found-other-line"
    )


def _diff_paths(diff_path: Path | None) -> set[str] | None:
    if diff_path is None:
        return None
    paths: set[str] = set()
    with diff_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith("diff --git "):
                continue
            try:
                tokens = shlex.split(line.rstrip("\n"))
            except ValueError:
                continue
            if len(tokens) < 4:
                continue
            for token, prefix in ((tokens[2], "a/"), (tokens[3], "b/")):
                if token.startswith(prefix):
                    paths.add(token[len(prefix):].replace("\\", "/"))
    return paths


def _source_file_set_digest(
    source_root: Path | None,
    repository_paths: Iterable[str],
) -> str | None:
    if source_root is None:
        return None
    digest = hashlib.sha256()
    for repository_path in sorted(set(repository_paths)):
        normalized = repository_path.replace("\\", "/").lstrip("/")
        digest.update(normalized.encode("utf-8"))
        digest.update(b"\0")
        source_path = _safe_source_path(source_root, normalized)
        if source_path is None:
            digest.update(b"<invalid-path>")
        elif not source_path.is_file():
            digest.update(b"<missing-file>")
        else:
            digest.update(source_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _csv_bytes(rows: list[Mapping[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=OUTPUT_COLUMNS,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def prepare_corpus(
    source_path: Path,
    output_path: Path,
    *,
    analysis_id: str,
    pr_number: str | None = None,
    commit_hash: str | None = None,
    labels_path: Path | None = None,
    source_root: Path | None = None,
    diff_path: Path | None = None,
) -> dict[str, Any]:
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    if source_root is not None:
        source_root = source_root.resolve()
        if not source_root.is_dir():
            raise ValueError(f"source root is not a directory: {source_root}")
    if diff_path is not None:
        diff_path = diff_path.resolve()
        if not diff_path.is_file():
            raise ValueError(f"diff is not a file: {diff_path}")

    selected = _selected_rows(
        source_path,
        analysis_id=analysis_id.strip(),
        pr_number=pr_number.strip() if pr_number is not None else None,
        commit_hash=commit_hash.strip() if commit_hash is not None else None,
    )
    selected_ids = {row["id"].strip() for row in selected}
    labels = _load_labels(
        labels_path.resolve() if labels_path is not None else None,
        selected_ids,
    )
    changed_paths = _diff_paths(diff_path)

    output_rows = []
    for source_row in selected:
        identity = source_row["id"].strip()
        repository_path = source_row["file_path"].strip().replace("\\", "/")
        label = labels.get(identity, {})
        output_rows.append({
            "id": identity,
            "origin_analysis_id": source_row["origin_analysis_id"].strip(),
            "origin_pr_number": source_row["origin_pr_number"].strip(),
            "origin_commit_hash": source_row["origin_commit_hash"].strip(),
            "severity": source_row["severity"].strip(),
            "issue_category": source_row["issue_category"].strip(),
            "file_path": repository_path,
            "line_number": source_row["line_number"].strip(),
            "issue_scope": source_row["issue_scope"].strip() or "LINE",
            "title": source_row["title"],
            "reason": source_row["reason"],
            "suggested_fix_description": source_row[
                "suggested_fix_description"
            ],
            "code_snippet": source_row["code_snippet"],
            "snapshot_anchor_state": _anchor_state(
                source_root,
                repository_path,
                source_row["line_number"],
                source_row["code_snippet"],
            ),
            "diff_path_state": (
                "not-checked"
                if changed_paths is None
                else (
                    "changed"
                    if repository_path in changed_paths
                    else "not-changed"
                )
            ),
            "review_verdict": label.get("review_verdict", ""),
            "adjudication_note": label.get("adjudication_note", ""),
            "adjudicator": label.get("adjudicator", ""),
            "adjudicated_at": label.get("adjudicated_at", ""),
        })

    corpus_bytes = _csv_bytes(output_rows)
    _atomic_write(output_path, corpus_bytes)

    anchor_states = Counter(row["snapshot_anchor_state"] for row in output_rows)
    diff_states = Counter(row["diff_path_state"] for row in output_rows)
    verdicts = Counter(
        row["review_verdict"] or "unlabeled"
        for row in output_rows
    )
    quality_reasons = []
    if source_root is None:
        quality_reasons.append("source snapshot was not supplied")
    if diff_path is None:
        quality_reasons.append("base-to-head diff was not supplied")
    if verdicts["unlabeled"]:
        quality_reasons.append(
            f"{verdicts['unlabeled']} finding(s) remain unlabeled"
        )
    unavailable_source_count = sum(
        anchor_states[state]
        for state in (
            "invalid-path",
            "missing-file",
            "unreadable-file",
        )
    )
    if source_root is not None and unavailable_source_count:
        quality_reasons.append(
            f"{unavailable_source_count} finding source file(s) are unavailable"
        )
    not_changed_count = diff_states["not-changed"]
    if diff_path is not None and not_changed_count:
        quality_reasons.append(
            f"{not_changed_count} finding path(s) are absent from the supplied diff"
        )

    manifest = {
        "status": "completed",
        "qualityReady": not quality_reasons,
        "qualityReadinessReasons": quality_reasons,
        "sourceExport": {
            "name": source_path.name,
            "sha256": _sha256_file(source_path),
        },
        "cohort": {
            "originAnalysisId": output_rows[0]["origin_analysis_id"],
            "originPrNumber": output_rows[0]["origin_pr_number"],
            "originCommitHash": output_rows[0]["origin_commit_hash"],
            "findingCount": len(output_rows),
        },
        "corpus": {
            "name": output_path.name,
            "sha256": _sha256_bytes(corpus_bytes),
            "verdictCounts": dict(sorted(verdicts.items())),
        },
        "sourceSnapshot": {
            "supplied": source_root is not None,
            "referencedFileSetSha256": _source_file_set_digest(
                source_root,
                (row["file_path"] for row in output_rows),
            ),
            "anchorStates": dict(sorted(anchor_states.items())),
        },
        "diff": {
            "supplied": diff_path is not None,
            "sha256": _sha256_file(diff_path) if diff_path is not None else None,
            "changedPathCount": (
                len(changed_paths) if changed_paths is not None else None
            ),
            "pathStates": dict(sorted(diff_states.items())),
        },
        "labelPolicy": (
            "TP/FP labels are copied only from --labels. Resolution state, "
            "resolution actor, and issue lifecycle are never interpreted as "
            "review verdicts."
        ),
    }
    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    _atomic_write(
        manifest_path,
        (
            json.dumps(manifest, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8"),
    )
    return {
        **manifest,
        "manifest": {"name": manifest_path.name},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_export", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--analysis-id", required=True)
    parser.add_argument("--pr-number")
    parser.add_argument("--commit-hash")
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--diff", type=Path)
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit non-zero unless snapshot, diff, anchors, and labels are complete.",
    )
    arguments = parser.parse_args()
    try:
        report = prepare_corpus(
            arguments.source_export,
            arguments.output,
            analysis_id=arguments.analysis_id,
            pr_number=arguments.pr_number,
            commit_hash=arguments.commit_hash,
            labels_path=arguments.labels,
            source_root=arguments.source_root,
            diff_path=arguments.diff,
        )
    except Exception as exception:
        print(json.dumps({
            "status": "failed",
            "error": f"{type(exception).__name__}: {exception}",
        }, indent=2, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    if arguments.require_ready and not report["qualityReady"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
