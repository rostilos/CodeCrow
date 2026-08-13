"""Deterministic final report rendering from verified structured findings."""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue


_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def _text(value: object) -> str:
    return str(value or "").strip()


def _line(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _issue_sort_key(issue: CodeReviewIssue) -> tuple:
    severity = _text(getattr(issue, "severity", "")).upper()
    return (
        _SEVERITY_ORDER.get(severity, 99),
        _text(getattr(issue, "file", "")),
        _line(getattr(issue, "line", 0)),
        _text(getattr(issue, "title", "")),
    )


def render_verified_report(
    request: ReviewRequestDto,
    issues: Iterable[CodeReviewIssue],
    *,
    incomplete_candidates: int = 0,
    rejected_candidates: int = 0,
) -> str:
    """Render a stable summary without changing issue membership or content."""

    active = sorted(
        (
            issue
            for issue in issues
            if getattr(issue, "isResolved", False) is not True
        ),
        key=_issue_sort_key,
    )
    title = _text(getattr(request, "prTitle", ""))
    repository = _text(getattr(request, "projectVcsRepoSlug", ""))
    heading = "## CodeCrow review"
    if title:
        heading += f": {title}"

    lines = [heading, ""]
    if not active:
        lines.append("No confirmed actionable defects were found in the reviewed changes.")
    else:
        counts = Counter(
            _text(getattr(issue, "severity", "")).upper() or "UNKNOWN"
            for issue in active
        )
        summary = ", ".join(
            f"{counts[severity]} {severity}"
            for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO", "UNKNOWN")
            if counts[severity]
        )
        lines.extend([
            f"Confirmed findings: **{len(active)}** ({summary}).",
            "",
            "### Findings",
            "",
        ])
        for issue in active:
            severity = _text(getattr(issue, "severity", "")).upper() or "UNKNOWN"
            title_text = _text(getattr(issue, "title", "")) or "Untitled finding"
            path = _text(getattr(issue, "file", "")) or "unknown path"
            line = _line(getattr(issue, "line", 0))
            location = f"`{path}:{line}`" if line else f"`{path}`"
            lines.append(f"- **{severity}** {title_text} — {location}")

    if incomplete_candidates:
        lines.extend([
            "",
            (
                f"Verification could not establish **{incomplete_candidates}** "
                "candidate(s); they were withheld rather than published."
            ),
        ])
    if rejected_candidates:
        lines.extend([
            "",
            f"Rejected unsupported candidates: **{rejected_candidates}**.",
        ])
    if repository:
        lines.extend(["", f"Repository: `{repository}`"])
    return "\n".join(lines).strip()

