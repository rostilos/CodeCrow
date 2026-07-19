"""Diff-line helpers and the exact-proof gate used by the classic review path."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Mapping, Optional

from model.output_schemas import CodeReviewIssue
from utils.git_diff_paths import (
    GitDiffPathError,
    parse_git_diff_header,
    parse_git_marker_path,
)


_HUNK_HEADER = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_count>\d+))?\s+"
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@"
)
_SHA_256 = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_SEVERITIES = {"HIGH", "MEDIUM", "LOW"}
_NON_DEFECT_CATEGORIES = {"STYLE", "DOCUMENTATION"}
_SUPPORTED_CATEGORIES = {
    "SECURITY",
    "PERFORMANCE",
    "CODE_QUALITY",
    "BUG_RISK",
    "STYLE",
    "DOCUMENTATION",
    "BEST_PRACTICES",
    "ERROR_HANDLING",
    "TESTING",
    "ARCHITECTURE",
}


@dataclass(frozen=True)
class ExactPublicationClaim:
    """Producer classification used only at the publication boundary."""

    finding_type: Literal["DEFECT", "ADVISORY"]
    verification_status: Literal["CONFIRMED", "REJECTED", "INCONCLUSIVE"]
    precondition: str
    reachable_path: str
    failure: str
    impact: str
    counter_evidence: str


@dataclass(frozen=True)
class ExactSourceProof:
    """Coordinates of source evidence already verified by its producer.

    ``verified`` is set by a caller only after validating the underlying
    execution-scoped receipt (manifest source artifact for CLASSIC, registered
    local-tool receipt for AGENTIC). The common gate independently binds those
    coordinates to the issue and immutable diff.
    """

    execution_id: str
    head_sha: str
    path: str
    line: int
    code_snippet: str
    source_digest: str
    verified: bool


@dataclass(frozen=True)
class ExactPublicationDecision:
    """Publishable issue or the stable reason it was rejected."""

    issue: Optional[CodeReviewIssue]
    rejection_reason: Optional[str]


def changed_lines_from_diff(raw_diff: str) -> dict[str, dict[int, str]]:
    """Return exact post-change line coordinates for added unified-diff lines."""

    changed: dict[str, dict[int, str]] = {}
    current_path: Optional[str] = None
    current_line: Optional[int] = None

    for raw_line in (raw_diff or "").splitlines():
        if raw_line.startswith("diff --git "):
            try:
                _old_path, current_path = parse_git_diff_header(raw_line)
            except GitDiffPathError:
                current_path = None
            current_line = None
            continue
        if raw_line.startswith("+++ "):
            try:
                current_path = parse_git_marker_path(raw_line, "+++")
            except GitDiffPathError:
                current_path = None
            continue
        hunk = _HUNK_HEADER.match(raw_line)
        if hunk:
            current_line = int(hunk.group("new_start"))
            continue
        if current_path is None or current_line is None:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            changed.setdefault(current_path, {})[current_line] = raw_line[1:]
            current_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        elif raw_line.startswith("\\ No newline at end of file"):
            continue
        else:
            current_line += 1

    return changed


def reviewable_lines_from_diff(raw_diff: str) -> dict[str, dict[int, str]]:
    """Return new-side added and context lines visible in each PR hunk."""

    visible: dict[str, dict[int, str]] = {}
    current_path: Optional[str] = None
    current_line: Optional[int] = None

    for raw_line in (raw_diff or "").splitlines():
        if raw_line.startswith("diff --git "):
            try:
                _old_path, current_path = parse_git_diff_header(raw_line)
            except GitDiffPathError:
                current_path = None
            current_line = None
            continue
        if raw_line.startswith("+++ "):
            try:
                current_path = parse_git_marker_path(raw_line, "+++")
            except GitDiffPathError:
                current_path = None
            continue
        hunk = _HUNK_HEADER.match(raw_line)
        if hunk:
            current_line = int(hunk.group("new_start"))
            continue
        if current_path is None or current_line is None:
            continue
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        if raw_line.startswith("\\ No newline at end of file"):
            continue
        if raw_line.startswith(("+", " ")):
            visible.setdefault(current_path, {})[current_line] = raw_line[1:]
            current_line += 1
            continue
        # Defensive handling for malformed-but-readable empty context lines.
        visible.setdefault(current_path, {})[current_line] = raw_line
        current_line += 1

    return visible


def evaluate_exact_publication(
    issue: CodeReviewIssue,
    claim: ExactPublicationClaim,
    proof: Optional[ExactSourceProof],
    *,
    changed_lines: Mapping[str, Mapping[int, str]],
    execution_id: str,
    head_sha: str,
) -> ExactPublicationDecision:
    """Return a normalized issue or a stable rejection reason."""

    def reject(reason: str) -> ExactPublicationDecision:
        return ExactPublicationDecision(issue=None, rejection_reason=reason)

    if claim.finding_type != "DEFECT":
        return reject("not_a_defect")
    if claim.verification_status != "CONFIRMED":
        return reject("not_confirmed")
    for chain_link in (
        claim.precondition,
        claim.reachable_path,
        claim.failure,
        claim.impact,
        claim.counter_evidence,
    ):
        if not isinstance(chain_link, str) or len(chain_link.strip()) < 10:
            return reject("incomplete_evidence_chain")

    severity = str(issue.severity or "").strip().upper()
    category = str(issue.category or "").strip().upper()
    if severity not in _SUPPORTED_SEVERITIES:
        return reject("unsupported_severity")
    if category not in _SUPPORTED_CATEGORIES or category in _NON_DEFECT_CATEGORIES:
        return reject("unsupported_or_non_defect_category")
    if proof is None or not proof.verified:
        return reject("missing_or_unverified_source_proof")
    if (
        proof.execution_id != execution_id
        or proof.head_sha != head_sha
        or proof.path != str(issue.file).lstrip("/")
        or not isinstance(proof.line, int)
        or isinstance(proof.line, bool)
        or proof.line < 1
        or not isinstance(proof.source_digest, str)
        or _SHA_256.fullmatch(proof.source_digest) is None
    ):
        return reject("source_proof_identity_mismatch")

    issue_snippet = str(issue.codeSnippet or "").strip()
    proof_snippet = str(proof.code_snippet or "").strip()
    changed_line = changed_lines.get(proof.path, {}).get(proof.line)
    if (
        not issue_snippet
        or issue_snippet != proof_snippet
        or changed_line is None
        or proof_snippet != changed_line.strip()
    ):
        return reject("changed_line_snippet_mismatch")

    return ExactPublicationDecision(
        issue=issue.model_copy(
            update={
                "severity": severity,
                "category": category,
                "file": proof.path,
                "line": proof.line,
                "codeSnippet": proof.code_snippet,
            }
        ),
        rejection_reason=None,
    )


def accept_exact_publication(
    issue: CodeReviewIssue,
    claim: ExactPublicationClaim,
    proof: Optional[ExactSourceProof],
    *,
    changed_lines: Mapping[str, Mapping[int, str]],
    execution_id: str,
    head_sha: str,
) -> Optional[CodeReviewIssue]:
    """Compatibility wrapper returning only a publishable issue."""

    return evaluate_exact_publication(
        issue,
        claim,
        proof,
        changed_lines=changed_lines,
        execution_id=execution_id,
        head_sha=head_sha,
    ).issue
