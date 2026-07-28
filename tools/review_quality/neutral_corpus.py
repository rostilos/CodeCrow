"""Materialize fixed, disconnected review-quality corpus candidates.

The fixtures in this module are intentionally small, but they are not analyzer
rules.  Each head revision introduces a semantic regression whose proof lives in
unchanged related code or tests.  This makes the cases useful for comparing the
generic fallback with deterministic plugin/RAG context without contacting a
repository provider or a review model.

Generated inventories are drafts.  An operator must independently certify the
complete fixed diff before any paired model outputs are inspected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


_AUTHOR_NAME = "CodeCrow Neutral Corpus"
_AUTHOR_EMAIL = "neutral-corpus@invalid.local"
_BASE_DATE = "2026-07-27T00:00:00+00:00"
_HEAD_DATE = "2026-07-27T00:01:00+00:00"


@dataclass(frozen=True)
class ExpectedDefect:
    id: str
    file: str
    line: int
    summary: str
    evidence_files: tuple[str, ...]


@dataclass(frozen=True)
class NeutralCaseDefinition:
    case_id: str
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    candidate_plugins: tuple[str, ...]
    request_plugins: tuple[str, ...]
    base_files: Mapping[str, str]
    head_replacements: Mapping[str, str]
    expected_defects: tuple[ExpectedDefect, ...]


@dataclass(frozen=True)
class MaterializedNeutralCase:
    definition: NeutralCaseDefinition
    repository: Path
    case_manifest: Path
    inventory: Path
    base_commit: str
    head_commit: str
    raw_diff_sha256: str
    changed_files: tuple[str, ...]
    definition_digest: str


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "analysis-plugins"
    / "fixtures"
    / "review-quality"
    / "neutral-corpus.json"
)


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{field} must be an array of non-empty strings")
    result = tuple(value)
    if len(result) != len(set(result)) or result != tuple(sorted(result)):
        raise ValueError(f"{field} must contain unique sorted strings")
    return result


def _file_map(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(path, str)
        or not path
        or not isinstance(content, str)
        for path, content in value.items()
    ):
        raise ValueError(f"{field} must map non-empty paths to source strings")
    if list(value) != sorted(value):
        raise ValueError(f"{field} paths must be sorted")
    return dict(value)


def _load_case_definitions(
    fixture_path: Path = FIXTURE_PATH,
) -> dict[str, NeutralCaseDefinition]:
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exception:
        raise RuntimeError(
            f"cannot load neutral corpus fixture {fixture_path}: {exception}"
        ) from exception
    if not isinstance(payload, dict) or set(payload) != {"cases", "kind"}:
        raise ValueError("neutral corpus fixture must contain only cases and kind")
    if payload["kind"] != "codecrow-neutral-review-corpus":
        raise ValueError("neutral corpus fixture kind is invalid")
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("neutral corpus fixture cases must be a non-empty array")

    definitions: dict[str, NeutralCaseDefinition] = {}
    case_fields = {
        "baseFiles",
        "candidatePlugins",
        "caseId",
        "expectedDefects",
        "frameworks",
        "headReplacements",
        "languages",
        "requestPlugins",
    }
    defect_fields = {"evidenceFiles", "file", "id", "line", "summary"}
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict) or set(raw_case) != case_fields:
            raise ValueError(f"neutral corpus case {index} has invalid fields")
        case_id = raw_case["caseId"]
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"neutral corpus case {index} has invalid caseId")
        if case_id in definitions:
            raise ValueError(f"duplicate neutral corpus caseId: {case_id}")
        raw_defects = raw_case["expectedDefects"]
        if not isinstance(raw_defects, list) or not raw_defects:
            raise ValueError(f"{case_id}: expectedDefects must be non-empty")
        defects: list[ExpectedDefect] = []
        for defect_index, raw_defect in enumerate(raw_defects):
            if (
                not isinstance(raw_defect, dict)
                or set(raw_defect) != defect_fields
            ):
                raise ValueError(
                    f"{case_id}: expected defect {defect_index} has invalid fields"
                )
            line = raw_defect["line"]
            if isinstance(line, bool) or not isinstance(line, int):
                raise ValueError(
                    f"{case_id}: expected defect {defect_index} line is invalid"
                )
            scalar_fields = ("id", "file", "summary")
            if any(
                not isinstance(raw_defect[field], str)
                or not raw_defect[field]
                for field in scalar_fields
            ):
                raise ValueError(
                    f"{case_id}: expected defect {defect_index} has invalid text"
                )
            defects.append(ExpectedDefect(
                id=raw_defect["id"],
                file=raw_defect["file"],
                line=line,
                summary=raw_defect["summary"],
                evidence_files=_string_tuple(
                    raw_defect["evidenceFiles"],
                    f"{case_id}.expectedDefects[{defect_index}].evidenceFiles",
                ),
            ))
        definitions[case_id] = NeutralCaseDefinition(
            case_id=case_id,
            languages=_string_tuple(
                raw_case["languages"], f"{case_id}.languages"
            ),
            frameworks=_string_tuple(
                raw_case["frameworks"], f"{case_id}.frameworks"
            ),
            candidate_plugins=_string_tuple(
                raw_case["candidatePlugins"], f"{case_id}.candidatePlugins"
            ),
            request_plugins=_string_tuple(
                raw_case["requestPlugins"], f"{case_id}.requestPlugins"
            ),
            base_files=_file_map(
                raw_case["baseFiles"], f"{case_id}.baseFiles"
            ),
            head_replacements=_file_map(
                raw_case["headReplacements"], f"{case_id}.headReplacements"
            ),
            expected_defects=tuple(defects),
        )
    return definitions


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _definition_projection(definition: NeutralCaseDefinition) -> dict[str, Any]:
    return {
        "caseId": definition.case_id,
        "languages": list(definition.languages),
        "frameworks": list(definition.frameworks),
        "candidatePlugins": list(definition.candidate_plugins),
        "requestPlugins": list(definition.request_plugins),
        "baseFiles": dict(sorted(definition.base_files.items())),
        "headReplacements": dict(sorted(definition.head_replacements.items())),
        "expectedDefects": [
            {
                "id": defect.id,
                "file": defect.file,
                "line": defect.line,
                "summary": defect.summary,
                "evidenceFiles": list(defect.evidence_files),
            }
            for defect in definition.expected_defects
        ],
    }


def definition_digest(definition: NeutralCaseDefinition) -> str:
    return _sha256_text(_canonical_json(_definition_projection(definition)))


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        list(command),
        cwd=str(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _write_files(root: Path, files: Mapping[str, str]) -> None:
    for relative_path, content in sorted(files.items()):
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def _commit_environment(timestamp: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update({
        "GIT_AUTHOR_NAME": _AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": _AUTHOR_EMAIL,
        "GIT_AUTHOR_DATE": timestamp,
        "GIT_COMMITTER_NAME": _AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": _AUTHOR_EMAIL,
        "GIT_COMMITTER_DATE": timestamp,
    })
    return environment


def _validate_definition(definition: NeutralCaseDefinition) -> None:
    if not definition.expected_defects:
        raise ValueError(f"{definition.case_id}: expected defects are empty")
    changed = set(definition.head_replacements)
    missing_base = sorted(changed - set(definition.base_files))
    if missing_base:
        raise ValueError(
            f"{definition.case_id}: replacements are absent from base: "
            + ", ".join(missing_base)
        )
    if any(
        definition.base_files[path] == replacement
        for path, replacement in definition.head_replacements.items()
    ):
        raise ValueError(f"{definition.case_id}: a replacement is unchanged")
    defect_ids: set[str] = set()
    for defect in definition.expected_defects:
        if defect.id in defect_ids:
            raise ValueError(
                f"{definition.case_id}: duplicate defect id {defect.id}"
            )
        defect_ids.add(defect.id)
        if defect.file not in changed:
            raise ValueError(
                f"{definition.case_id}: defect {defect.id} is outside the diff"
            )
        head_lines = definition.head_replacements[defect.file].splitlines()
        if defect.line < 1 or defect.line > len(head_lines):
            raise ValueError(
                f"{definition.case_id}: defect {defect.id} line is invalid"
            )
        missing_evidence = sorted(
            set(defect.evidence_files) - set(definition.base_files)
        )
        if missing_evidence:
            raise ValueError(
                f"{definition.case_id}: defect {defect.id} evidence is absent: "
                + ", ".join(missing_evidence)
            )


CASE_DEFINITIONS = _load_case_definitions()
for _definition in CASE_DEFINITIONS.values():
    _validate_definition(_definition)


def materialize_case(
    definition: NeutralCaseDefinition,
    output_root: Path,
) -> MaterializedNeutralCase:
    """Create deterministic base/head commits, a capture manifest, and a draft inventory."""
    _validate_definition(definition)
    case_root = output_root / definition.case_id
    repository = case_root / "repository"
    if case_root.exists():
        if any(case_root.iterdir()):
            raise ValueError(f"output case directory is not empty: {case_root}")
    else:
        case_root.mkdir(parents=True, mode=0o700)
    repository.mkdir(mode=0o700)

    _run(("git", "init", "-q", "-b", "main"), cwd=repository)
    _write_files(repository, definition.base_files)
    _run(("git", "add", "."), cwd=repository)
    _run(
        ("git", "commit", "-q", "-m", "fixed neutral corpus base"),
        cwd=repository,
        env=_commit_environment(_BASE_DATE),
    )
    base_commit = _run(("git", "rev-parse", "HEAD"), cwd=repository)

    _write_files(repository, definition.head_replacements)
    _run(("git", "add", "."), cwd=repository)
    _run(
        ("git", "commit", "-q", "-m", "seed semantic regression"),
        cwd=repository,
        env=_commit_environment(_HEAD_DATE),
    )
    head_commit = _run(("git", "rev-parse", "HEAD"), cwd=repository)
    if _run(("git", "remote"), cwd=repository):
        raise RuntimeError("neutral corpus repository unexpectedly has a remote")

    raw_diff = subprocess.run(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--full-index",
            "--unified=80",
            base_commit,
            head_commit,
        ],
        cwd=repository,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    changed_files = tuple(
        line
        for line in _run(
            ("git", "diff", "--name-only", base_commit, head_commit),
            cwd=repository,
        ).splitlines()
        if line
    )
    expected_changed = tuple(sorted(definition.head_replacements))
    if tuple(sorted(changed_files)) != expected_changed:
        raise RuntimeError("materialized changed files differ from definition")

    case_manifest = case_root / "case.json"
    case_manifest.write_text(
        json.dumps(
            {
                "caseId": definition.case_id,
                "repositoryPath": str(repository.resolve()),
                "baseCommit": base_commit,
                "headCommit": head_commit,
                "languages": list(definition.languages),
                "frameworks": list(definition.frameworks),
                "candidatePlugins": list(definition.candidate_plugins),
                "requestPlugins": list(definition.request_plugins),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    digest = definition_digest(definition)
    inventory = case_root / "ground-truth-draft.json"
    inventory.write_text(
        json.dumps(
            {
                "kind": "review-quality-ground-truth-inventory",
                "status": "draft-pending-independent-certification",
                "scope": "complete-fixed-diff",
                "candidateOutputsHiddenDuringDefectInventory": True,
                "caseId": definition.case_id,
                "definitionDigest": digest,
                "baseCommit": base_commit,
                "headCommit": head_commit,
                "rawDiffSha256": _sha256_text(raw_diff),
                "changedFiles": list(changed_files),
                "expectedDefects": [
                    {
                        "id": defect.id,
                        "file": defect.file,
                        "line": defect.line,
                        "summary": defect.summary,
                        "evidenceFiles": list(defect.evidence_files),
                    }
                    for defect in definition.expected_defects
                ],
                "certification": {
                    "adjudicator": "",
                    "adjudicatedAt": "",
                    "method": (
                        "Inspect the complete immutable base-to-head diff and "
                        "all listed evidence before opening paired outputs."
                    ),
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    case_manifest.chmod(0o600)
    inventory.chmod(0o600)
    return MaterializedNeutralCase(
        definition=definition,
        repository=repository,
        case_manifest=case_manifest,
        inventory=inventory,
        base_commit=base_commit,
        head_commit=head_commit,
        raw_diff_sha256=_sha256_text(raw_diff),
        changed_files=changed_files,
        definition_digest=digest,
    )


def materialize_corpus(
    output_root: Path,
    *,
    case_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Materialize the selected cases and return a source-only audit report."""
    selected = tuple(case_ids) if case_ids else tuple(CASE_DEFINITIONS)
    unknown = sorted(set(selected) - set(CASE_DEFINITIONS))
    if unknown:
        raise ValueError("unknown neutral corpus cases: " + ", ".join(unknown))
    if len(selected) != len(set(selected)):
        raise ValueError("neutral corpus cases must not contain duplicates")
    if output_root.exists():
        if any(output_root.iterdir()):
            raise ValueError("output directory must be absent or empty")
    else:
        output_root.mkdir(parents=True, mode=0o700)
    output_root.chmod(0o700)

    cases = [
        materialize_case(CASE_DEFINITIONS[case_id], output_root)
        for case_id in selected
    ]
    report = {
        "kind": "review-quality-neutral-corpus-materialization",
        "status": "drafts-created",
        "providerCalls": 0,
        "embeddingCalls": 0,
        "connectedRepositories": 0,
        "candidateOutputsInspected": False,
        "cases": [
            {
                "caseId": case.definition.case_id,
                "languages": list(case.definition.languages),
                "frameworks": list(case.definition.frameworks),
                "candidatePlugins": list(case.definition.candidate_plugins),
                "requestPlugins": list(case.definition.request_plugins),
                "repositoryPath": str(case.repository.resolve()),
                "caseManifest": str(case.case_manifest.resolve()),
                "groundTruthDraft": str(case.inventory.resolve()),
                "baseCommit": case.base_commit,
                "headCommit": case.head_commit,
                "rawDiffSha256": case.raw_diff_sha256,
                "changedFiles": list(case.changed_files),
                "definitionDigest": case.definition_digest,
            }
            for case in cases
        ],
    }
    report_path = output_root / "materialization-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.chmod(0o600)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create deterministic remote-free Python, Java, TypeScript, and "
            "polyglot review-quality corpus drafts without provider calls."
        )
    )
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument(
        "--case",
        action="append",
        choices=tuple(CASE_DEFINITIONS),
        default=[],
        help="materialize one case; repeat as needed (default: all)",
    )
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = materialize_corpus(
        args.output_directory.resolve(),
        case_ids=args.case,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        args.output.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
