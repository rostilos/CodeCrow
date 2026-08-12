"""
Issue reconciliation and deduplication logic for incremental reviews.
"""
import logging
import difflib
import asyncio
import json
import os
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence

from model.output_schemas import (
    CodeReviewIssue,
    SemanticDeduplicationDecision,
)
from service.review.candidate_ledger import CandidateEvidenceLedger
from utils.llm_response import extract_llm_response_text
from service.review.orchestrator.json_utils import parse_llm_response, supports_structured_output
from utils.path_identity import repository_paths_match

logger = logging.getLogger(__name__)

_DEFAULT_RESOLUTION_TEXT = (
    "Resolved in the current PR review iteration; no specific resolution "
    "explanation was provided."
)


def _resolution_text(data: Dict[str, Any]) -> Optional[str]:
    """Read either Python or client-facing historical resolution field."""
    for key in ('resolutionReason', 'resolutionExplanation', 'resolvedDescription'):
        value = data.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def _previous_issue_status(data: Dict[str, Any]) -> str:
    return str(data.get('status') or '').strip().lower()


def _is_open_previous_issue(data: Dict[str, Any]) -> bool:
    """Only explicit OPEN and legacy blank statuses participate in reconciliation."""
    return _previous_issue_status(data) in {'', 'open'}


def _deduplicate_reconciled_history(
    issues: List[CodeReviewIssue],
    previous_ids: set[str],
) -> List[CodeReviewIssue]:
    """Return at most one lifecycle record per previous issue ID."""
    deduped: List[CodeReviewIssue] = []
    positions: Dict[str, int] = {}

    def preference(issue: CodeReviewIssue) -> tuple[bool, bool]:
        data = issue.model_dump()
        resolved = data.get('isResolved') is True
        resolution = _resolution_text(data)
        specific_resolution = bool(
            resolution and resolution != _DEFAULT_RESOLUTION_TEXT
        )
        return resolved, specific_resolution

    for issue in issues:
        issue_id = str(getattr(issue, 'id', '') or '').strip()
        if not issue_id or issue_id not in previous_ids:
            deduped.append(issue)
            continue

        existing_position = positions.get(issue_id)
        if existing_position is None:
            positions[issue_id] = len(deduped)
            deduped.append(issue)
            continue

        existing = deduped[existing_position]
        if preference(issue) > preference(existing):
            deduped[existing_position] = issue

    return deduped


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


def issue_matches_files(issue: Any, file_paths: List[str]) -> bool:
    """Check if an issue is related to any of the given file paths.
    
    Matches on exact path, or when one path is a suffix of the other
    (handles relative vs absolute paths). Does NOT match on basename alone
    to avoid false positives (e.g., two different utils.py files).
    """
    if hasattr(issue, 'model_dump'):
        issue_data = issue.model_dump()
    elif isinstance(issue, dict):
        issue_data = issue
    else:
        issue_data = vars(issue) if hasattr(issue, '__dict__') else {}
    
    issue_file = issue_data.get('file', issue_data.get('filePath', ''))
    if not issue_file:
        return False
    
    for fp in file_paths:
        if repository_paths_match(issue_file, fp):
            return True
    return False


def compute_issue_fingerprint(data: dict) -> str:
    """Compute a fingerprint for prompt-level issue deduplication.
    
    NOTE: This is used only for deduplicating previous issues before including
    them in LLM prompts. It is NOT related to Java's IssueFingerprint which
    uses SHA-256 of (category + lineHash + normalizedTitle) for persistent
    content-based tracking.
    
    Uses file + normalized line (±3 tolerance) + severity + truncated reason.
    """
    file_path = data.get('file', data.get('filePath', ''))
    line = data.get('line', data.get('lineNumber', 0))
    line_group = int(line) // 3 if line else 0
    severity = data.get('severity', '')
    reason = data.get('reason', data.get('description', ''))
    reason_prefix = reason[:50].lower().strip() if reason else ''
    
    return f"{file_path}::{line_group}::{severity}::{reason_prefix}"


def is_semantically_similar(reason1: str, reason2: str, threshold: float = 0.70) -> bool:
    """Check if two issue reasons are semantically similar using SequenceMatcher."""
    if not reason1 or not reason2:
        return False
    # Normalize strings
    r1 = reason1.lower().strip()
    r2 = reason2.lower().strip()
    
    # Quick exact match
    if r1 == r2:
        return True
        
    # Use difflib for similarity ratio
    matcher = difflib.SequenceMatcher(None, r1, r2)
    return matcher.ratio() >= threshold


def _issue_payload(issue: Any) -> Dict[str, Any]:
    if hasattr(issue, "model_dump"):
        return issue.model_dump()
    if isinstance(issue, dict):
        return issue
    return vars(issue) if hasattr(issue, "__dict__") else {}


_TEXT_TOKEN_RE = re.compile(r"[A-Za-z0-9_$]+")
_ROOT_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "by", "can",
    "could", "for", "from", "has", "have", "if", "in", "into", "is", "it",
    "may", "of", "on", "or", "that", "the", "their", "this", "to", "was",
    "were", "will", "with", "would",
}
_SEVERITY_RANK = {
    "CRITICAL": 5,
    "HIGH": 4,
    "MEDIUM": 3,
    "LOW": 2,
    "INFO": 1,
}


def _normalized_file(data: Dict[str, Any]) -> str:
    value = str(data.get("file") or data.get("filePath") or "").strip()
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _split_reason_locations(value: Any) -> tuple[str, set[str]]:
    """Separate root-cause prose from host-generated affected locations."""
    body: List[str] = []
    locations: set[str] = set()
    for line in str(value or "").splitlines():
        stripped = line.strip()
        if stripped.casefold().startswith("also affects:"):
            raw_locations = stripped.split(":", 1)[1]
            locations.update(
                item.strip()
                for item in raw_locations.split(",")
                if item.strip()
            )
        else:
            body.append(line)
    return "\n".join(body).strip(), locations


def _normalized_text(value: Any) -> str:
    body, _ = _split_reason_locations(value)
    return " ".join(token.casefold() for token in _TEXT_TOKEN_RE.findall(body))


def _meaningful_tokens(value: Any) -> set[str]:
    return {
        token
        for token in _normalized_text(value).split()
        if token not in _ROOT_STOP_WORDS and len(token) > 1
    }


def _text_similarity(left: Any, right: Any) -> float:
    normalized_left = _normalized_text(left)
    normalized_right = _normalized_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0
    return difflib.SequenceMatcher(
        None,
        normalized_left,
        normalized_right,
    ).ratio()


def _token_overlap(left: Any, right: Any) -> tuple[int, float, float]:
    left_tokens = _meaningful_tokens(left)
    right_tokens = _meaningful_tokens(right)
    if not left_tokens or not right_tokens:
        return 0, 0.0, 0.0
    overlap = len(left_tokens & right_tokens)
    containment = overlap / min(len(left_tokens), len(right_tokens))
    union = len(left_tokens | right_tokens)
    return overlap, containment, overlap / union if union else 0.0


def _line_number(data: Dict[str, Any]) -> int:
    try:
        return int(data.get("line") or data.get("lineNumber") or 0)
    except (TypeError, ValueError):
        return 0


def _issue_id(issue: Any) -> str:
    return str(_issue_payload(issue).get("id") or "").strip()


def _issue_location(issue: Any) -> str:
    data = _issue_payload(issue)
    file_path = _normalized_file(data)
    line = _line_number(data)
    return f"{file_path}:{line}" if line > 0 else file_path


def _history_identity_rank(issue: Any) -> tuple[int, int, str]:
    issue_id = _issue_id(issue)
    if not issue_id:
        return 0, 0, ""
    try:
        # Prefer the oldest numeric identity when historical duplicates already
        # exist, preserving the longest-lived review/comment lineage.
        return 1, -int(issue_id), issue_id
    except ValueError:
        return 1, 0, issue_id


def _representation_rank(issue: Any) -> tuple[int, int, int, int]:
    data = _issue_payload(issue)
    line = _line_number(data)
    snippet = str(data.get("codeSnippet") or data.get("code_snippet") or "").strip()
    reason, _ = _split_reason_locations(data.get("reason") or "")
    fix = str(data.get("suggestedFixDescription") or "").strip()
    return (
        1 if snippet else 0,
        1 if line > 1 else 0,
        len(reason),
        len(fix),
    )


def _set_issue_field(issue: Any, name: str, value: Any) -> None:
    if hasattr(issue, name):
        setattr(issue, name, value)


def _merge_duplicate_issues(
    left: Any,
    right: Any,
    *,
    prefer_left: bool = False,
) -> Any:
    """Merge two proven root-cause duplicates without losing locations.

    A persisted identity wins over a fresh object, while a fresh exact anchor
    refreshes a stale historical location. The function mutates and returns one
    input object so candidate-ledger provenance remains attached.
    """
    left_history = _history_identity_rank(left)
    right_history = _history_identity_rank(right)
    if left_history != right_history:
        canonical = left if left_history > right_history else right
    elif prefer_left:
        canonical = left
    else:
        canonical = (
            left
            if _representation_rank(left) >= _representation_rank(right)
            else right
        )
    other = right if canonical is left else left

    canonical_data = _issue_payload(canonical)
    other_data = _issue_payload(other)
    left_location_before_merge = _issue_location(left)
    right_location_before_merge = _issue_location(right)
    same_file = _normalized_file(canonical_data) == _normalized_file(other_data)

    # A current fresh anchor is stronger than a carried historical hint. For two
    # fresh findings, prefer the representation with the stronger concrete anchor.
    if same_file:
        if bool(_issue_id(canonical)) != bool(_issue_id(other)):
            anchor_source = other if not _issue_id(other) else canonical
        elif prefer_left:
            anchor_source = canonical
        else:
            anchor_source = (
                canonical
                if _representation_rank(canonical) >= _representation_rank(other)
                else other
            )
        anchor_data = _issue_payload(anchor_source)
        for field in ("file", "line", "scope", "codeSnippet"):
            value = anchor_data.get(field)
            if value not in (None, ""):
                _set_issue_field(canonical, field, value)

    canonical_reason, canonical_locations = _split_reason_locations(
        canonical_data.get("reason")
    )
    other_reason, other_locations = _split_reason_locations(other_data.get("reason"))
    detail_source = canonical if len(canonical_reason) >= len(other_reason) else other
    base_reason = canonical_reason if detail_source is canonical else other_reason

    locations = set(canonical_locations) | set(other_locations)
    for value in canonical_data.get("relatedLocations") or []:
        if str(value).strip():
            locations.add(str(value).strip())
    for value in other_data.get("relatedLocations") or []:
        if str(value).strip():
            locations.add(str(value).strip())

    primary_location = _issue_location(canonical)
    if not same_file:
        locations.update({
            left_location_before_merge,
            right_location_before_merge,
        })
    elif (
        (not _issue_id(left) and not _issue_id(right))
        or (
            _issue_id(left)
            and _issue_id(right)
            and _issue_id(left) != _issue_id(right)
        )
    ):
        # Distinct occurrences of one root cause remain visible after merging.
        # A history/current pair is excluded because its line difference usually
        # represents a refreshed anchor for the same persisted occurrence.
        locations.update({
            left_location_before_merge,
            right_location_before_merge,
        })
    locations.discard("")
    locations.discard(primary_location)
    sorted_locations = sorted(locations)
    merged_reason = base_reason
    if sorted_locations:
        merged_reason = (
            f"{base_reason}\n\nAlso affects: {', '.join(sorted_locations)}"
        ).strip()
    _set_issue_field(canonical, "reason", merged_reason)
    _set_issue_field(canonical, "relatedLocations", sorted_locations)

    for field in ("title", "suggestedFixDescription", "suggestedFixDiff"):
        current = canonical_data.get(field)
        alternative = other_data.get(field)
        best = max(
            (current, alternative),
            key=lambda value: len(str(value)) if value else 0,
        )
        if best and best != current:
            _set_issue_field(canonical, field, best)

    left_severity = str(_issue_payload(left).get("severity") or "").upper()
    right_severity = str(_issue_payload(right).get("severity") or "").upper()
    highest = max(
        (left_severity, right_severity),
        key=lambda value: _SEVERITY_RANK.get(value, 0),
    )
    if highest:
        _set_issue_field(canonical, "severity", highest)

    evidence_refs = sorted({
        str(value).strip()
        for issue in (left, right)
        for value in (_issue_payload(issue).get("evidenceRefs") or [])
        if str(value).strip()
    })
    if evidence_refs:
        _set_issue_field(canonical, "evidenceRefs", evidence_refs)
    return canonical


def _normalized_anchor(value: Any) -> str:
    return " ".join(str(value or "").split())


def _issues_share_exact_anchor(
    left: Dict[str, Any],
    right: Dict[str, Any],
) -> bool:
    def line_number(data: Dict[str, Any]) -> int:
        try:
            return int(data.get("line") or data.get("lineNumber") or 0)
        except (TypeError, ValueError):
            return 0

    left_line = line_number(left)
    right_line = line_number(right)
    left_snippet = _normalized_anchor(
        left.get("codeSnippet") or left.get("code_snippet")
    )
    right_snippet = _normalized_anchor(
        right.get("codeSnippet") or right.get("code_snippet")
    )
    if left_snippet and right_snippet:
        return left_snippet == right_snippet

    return left_line > 0 and left_line == right_line


def _issues_share_exact_plugin_proof(
    left: Dict[str, Any],
    right: Dict[str, Any],
) -> bool:
    """Use shared plugin proof to nominate a semantic duplicate candidate."""
    left_kind = str(
        left.get("claimKind") or left.get("claim_kind") or ""
    ).strip()
    right_kind = str(
        right.get("claimKind") or right.get("claim_kind") or ""
    ).strip()
    if not left_kind or left_kind != right_kind:
        return False

    def evidence_ids(data: Dict[str, Any]) -> tuple[str, ...]:
        raw = data.get("evidenceRefs")
        if raw is None:
            raw = data.get("evidence_refs")
        if not isinstance(raw, (list, tuple, set)):
            return ()
        return tuple(sorted({
            str(value).strip()
            for value in raw
            if str(value).strip()
        }))

    left_evidence = evidence_ids(left)
    right_evidence = evidence_ids(right)
    return bool(left_evidence) and left_evidence == right_evidence


def issues_are_conservative_duplicates(
    left: Any,
    right: Any,
) -> bool:
    """Return true only for deterministic, effectively exact root identities.

    Category and severity drift do not make a real duplicate independent. At
    this tier, however, prose similarity alone is insufficient: uncertain pairs
    are delegated to the grouped semantic pass and retained if that pass fails.
    """
    left_data = _issue_payload(left)
    right_data = _issue_payload(right)
    left_file = _normalized_file(left_data)
    right_file = _normalized_file(right_data)
    if not left_file or left_file != right_file:
        return False

    left_title = _normalized_text(left_data.get("title") or "")
    right_title = _normalized_text(right_data.get("title") or "")
    left_reason = _normalized_text(
        left_data.get("reason") or left_data.get("description") or ""
    )
    right_reason = _normalized_text(
        right_data.get("reason") or right_data.get("description") or ""
    )
    exact_narrative = bool(
        left_title
        and left_title == right_title
        and left_reason
        and left_reason == right_reason
    )
    if exact_narrative:
        return True

    if not _issues_share_exact_anchor(left_data, right_data):
        return False
    if left_reason and left_reason == right_reason:
        return True
    if (
        left_reason
        and right_reason
        and _text_similarity(left_reason, right_reason) >= 0.88
    ):
        return True
    return bool(
        left_title
        and left_title == right_title
        and _text_similarity(left_reason, right_reason) >= 0.94
    )


def issues_are_semantic_dedup_candidates(left: Any, right: Any) -> bool:
    """Select plausible duplicate pairs for semantic comparison.

    This is a candidate-recall operation, not a deletion decision. It is broad
    enough to include anchor/category wording drift, while cross-file grouping
    requires a shared non-generic title and substantial technical-token overlap.
    """
    if issues_are_conservative_duplicates(left, right):
        return True

    left_data = _issue_payload(left)
    right_data = _issue_payload(right)
    left_file = _normalized_file(left_data)
    right_file = _normalized_file(right_data)
    if not left_file or not right_file:
        return False

    # A stable plugin fact is strong enough to justify semantic comparison, but
    # not deterministic deletion: one structural relationship can support more
    # than one genuinely distinct defect claim.
    if _issues_share_exact_plugin_proof(left_data, right_data):
        return True

    left_title = left_data.get("title") or ""
    right_title = right_data.get("title") or ""
    left_reason = left_data.get("reason") or left_data.get("description") or ""
    right_reason = right_data.get("reason") or right_data.get("description") or ""
    title_similarity = _text_similarity(left_title, right_title)
    reason_similarity = _text_similarity(left_reason, right_reason)
    title_overlap, title_containment, title_jaccard = _token_overlap(
        left_title,
        right_title,
    )
    reason_overlap, reason_containment, reason_jaccard = _token_overlap(
        left_reason,
        right_reason,
    )

    if left_file != right_file:
        exact_title = bool(
            _normalized_text(left_title)
            and _normalized_text(left_title) == _normalized_text(right_title)
        )
        # Cross-file occurrences are candidates only for a distinctive shared
        # root signature. Generic repeated warnings remain independent.
        return bool(
            exact_title
            and len(_meaningful_tokens(left_title)) >= 4
            and reason_overlap >= 4
            and reason_containment >= 0.50
            and reason_jaccard >= 0.25
        )

    left_line = _line_number(left_data)
    right_line = _line_number(right_data)
    near_line = bool(
        left_line > 0
        and right_line > 0
        and abs(left_line - right_line) <= 3
    )
    exact_anchor = _issues_share_exact_anchor(left_data, right_data)
    exact_title = bool(
        _normalized_text(left_title)
        and _normalized_text(left_title) == _normalized_text(right_title)
    )

    if exact_title and (
        reason_similarity >= 0.50
        or (reason_overlap >= 3 and reason_containment >= 0.45)
    ):
        return True

    anchor_related = exact_anchor or near_line
    title_related = bool(
        title_similarity >= 0.48
        or (title_overlap >= 3 and title_containment >= 0.55)
        or title_jaccard >= 0.42
    )
    reason_related = bool(
        reason_similarity >= 0.46
        or (reason_overlap >= 4 and reason_containment >= 0.50)
        or reason_jaccard >= 0.36
    )
    if title_related and (
        reason_similarity >= 0.70
        or (reason_overlap >= 5 and reason_containment >= 0.65)
    ):
        # Strong same-file narrative identity can drift to a nearby helper or
        # call site. Send it for semantic judgment without deleting locally.
        return True
    return anchor_related and title_related and reason_related


def _semantic_candidate_groups(
    issues: Sequence[CodeReviewIssue],
) -> List[List[CodeReviewIssue]]:
    """Build connected candidate components; singleton findings cost no tokens."""
    count = len(issues)
    parents = list(range(count))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left_index: int, right_index: int) -> None:
        left_root = find(left_index)
        right_root = find(right_index)
        if left_root != right_root:
            parents[right_root] = left_root

    for left_index in range(count):
        for right_index in range(left_index + 1, count):
            if issues_are_semantic_dedup_candidates(
                issues[left_index],
                issues[right_index],
            ):
                union(left_index, right_index)

    groups: Dict[int, List[CodeReviewIssue]] = defaultdict(list)
    for index, issue in enumerate(issues):
        groups[find(index)].append(issue)
    return [group for group in groups.values() if len(group) > 1]


def deduplicate_issues(issues: List[Any]) -> List[dict]:
    """Deduplicate issues by fingerprint, keeping most recent version.
    
    If an older version is resolved but newer isn't, preserves resolved status.
    """
    if not issues:
        return []
    
    deduped: dict = {}
    
    for issue in issues:
        if hasattr(issue, 'model_dump'):
            data = issue.model_dump()
        elif isinstance(issue, dict):
            data = issue.copy()
        else:
            data = vars(issue).copy() if hasattr(issue, '__dict__') else {}

        resolution = _resolution_text(data)
        if resolution:
            data['resolutionReason'] = resolution
            data['resolutionExplanation'] = resolution
        
        fingerprint = compute_issue_fingerprint(data)
        existing = deduped.get(fingerprint)
        
        if existing is None:
            deduped[fingerprint] = data
        else:
            existing_version = existing.get('prVersion') or 0
            current_version = data.get('prVersion') or 0
            existing_closed = not _is_open_previous_issue(existing)
            current_closed = not _is_open_previous_issue(data)
            
            if current_version > existing_version:
                # Current is newer
                if existing_closed and not current_closed:
                    # Preserve a terminal resolved/ignored status from older history.
                    data['status'] = existing.get('status')
                    resolution = _resolution_text(existing)
                    data['resolutionReason'] = resolution
                    data['resolutionExplanation'] = resolution
                    data['resolvedInCommit'] = existing.get('resolvedInCommit') or existing.get('resolvedByCommit')
                    data['resolvedInPrVersion'] = existing.get('resolvedInPrVersion')
                deduped[fingerprint] = data
            elif current_version == existing_version:
                # Same version - prefer a terminal resolved/ignored record.
                if current_closed and not existing_closed:
                    deduped[fingerprint] = data
    
    return list(deduped.values())


def format_previous_issues_for_batch(issues: List[Any]) -> str:
    """Format previous issues for inclusion in batch prompt.
    
    Only OPEN (or legacy blank-status) issues belong in runtime analysis.
    RESOLVED/IGNORED history remains in storage and is intentionally omitted so
    it cannot prime the model to recreate an already-closed finding.
    """
    if not issues:
        return ""
    
    # Deduplicate issues to avoid confusing the LLM with duplicates
    deduped_issues = deduplicate_issues(issues)
    
    # Only OPEN and legacy blank statuses may be reconciled.
    open_issues = [i for i in deduped_issues if _is_open_previous_issue(i)]
    if not open_issues:
        return ""
    
    lines = ["=== PREVIOUS ISSUES HISTORY (check if resolved/persisting) ==="]
    lines.append("Only OPEN issues are included; terminal history is excluded from runtime context.")
    lines.append("")
    
    if open_issues:
        lines.append("--- OPEN ISSUES (check if now fixed) ---")
        for data in open_issues:
            issue_id = data.get('id', 'unknown')
            severity = data.get('severity', 'MEDIUM')
            file_path = data.get('file', data.get('filePath', 'unknown'))
            line = data.get('line', data.get('lineNumber', '?'))
            title = data.get('title') or ''
            reason = data.get('reason', data.get('description', 'No description'))
            pr_version = data.get('prVersion', '?')
            
            title_part = f" [{title}]" if title else ""
            lines.append(f"[ID:{issue_id}] {severity}{title_part} @ {file_path}:{line} (v{pr_version})")
            lines.append(f"  Issue: {reason}")
            lines.append("")
    
    lines.append("INSTRUCTIONS:")
    lines.append("- For OPEN issues that are now FIXED: report with 'isResolved': true (boolean)")
    lines.append("- For OPEN issues still present: report with 'isResolved': false (boolean)")
    lines.append("- RESOLVED and IGNORED history is intentionally absent; never recreate it")
    lines.append("- IMPORTANT: 'isResolved' MUST be a JSON boolean (true/false), not a string")
    lines.append("- Preserve the 'id' field for all issues you report from previous issues")
    lines.append("- ⚠️ CRITICAL: DO NOT create a NEW issue (with a new ID or no ID) for a problem that is already covered by an OPEN previous issue. You MUST reuse the existing 'id'.")
    lines.append("=== END PREVIOUS ISSUES ===")
    return "\n".join(lines)


def deduplicate_final_issues(issues: List[CodeReviewIssue]) -> List[CodeReviewIssue]:
    """
    Final deduplication pass after ALL issue-finding stages complete
    (Stage 1, Reconciliation, Verification, Stage 2 cross-file).

    The deterministic tier merges only effectively exact root identities. It
    preserves the best anchor, highest severity, historical identity, and all
    additional affected locations. Ambiguous semantic pairs remain untouched
    for the grouped LLM pass.
    """
    if not issues:
        return []

    deduped: List[CodeReviewIssue] = []
    for issue in issues:
        duplicate_index = next((
            index
            for index, existing in enumerate(deduped)
            if issues_are_conservative_duplicates(issue, existing)
        ), None)
        if duplicate_index is None:
            deduped.append(issue)
            continue
        existing = deduped[duplicate_index]
        merged = _merge_duplicate_issues(existing, issue)
        deduped[duplicate_index] = merged
        data = _issue_payload(issue)
        logger.info(
            "Final deterministic dedup: merged exact root identity at %s:%s",
            data.get("file", ""),
            data.get("line", ""),
        )

    original = len(issues)
    final = len(deduped)
    if original != final:
        logger.info(f"Final dedup: {original} → {final} issues ({original - final} duplicates removed)")
    return deduped


# ---------------------------------------------------------------------------
#  LLM-based deduplication
# ---------------------------------------------------------------------------

_DEDUP_BATCH_SIZE = 50
_DEDUP_MAX_PARALLEL = max(1, _env_int("REVIEW_DEDUP_MAX_PARALLEL", 4))
_DEDUP_BATCH_CHAR_BUDGET = max(
    8_000,
    _env_int("REVIEW_DEDUP_BATCH_CHAR_BUDGET", 48_000),
)

_DEDUP_SYSTEM_PROMPT = (
    "You are a code-review root-cause deduplication assistant. You receive only "
    "host-selected candidate groups; findings outside these groups are never sent "
    "and are automatically retained. Identify only HIGH-confidence duplicate "
    "occurrences that describe one actionable root cause.\n\n"
    "Rules:\n"
    "1. Compare findings only inside the same candidate_group.\n"
    "2. Duplicate means the same causal defect, not merely the same rule, pattern, "
    "category, or suggested fix. Independent occurrences must remain separate.\n"
    "3. Category, severity, stage, and small anchor differences do not make the "
    "same root cause independent.\n"
    "4. Cross-file findings may be duplicates only when one shared defect causes "
    "the reported failures; repeated independent defects in different files are not.\n"
    "5. Select the most current, concrete, and useful representative as keeper.\n"
    "6. Emit a duplicate group only at HIGH confidence. Omit uncertain pairs; "
    "omitted findings are retained. Never return a kept-index allowlist."
)


def _format_issues_for_prompt(issues: List[CodeReviewIssue]) -> str:
    """Render a numbered list of issues for the deduplication prompt."""
    lines: List[str] = []
    for idx, issue in enumerate(issues):
        data = issue.model_dump() if hasattr(issue, 'model_dump') else issue
        file_path = data.get('file', '?')
        line = data.get('line', '?')
        severity = data.get('severity', '?')
        category = data.get('category', '?')
        title = data.get('title') or ''
        reason = data.get('reason', data.get('description', ''))
        title_part = f" | {title}" if title else ""
        lines.append(
            f"[{idx}] {severity} | {category}{title_part} | {file_path}:{line}\n"
            f"    Reason: {reason}"
        )
    return "\n".join(lines)


def _semantic_issue_payload(
    issue: CodeReviewIssue,
    index: int,
    group_id: str,
) -> Dict[str, Any]:
    data = _issue_payload(issue)
    reason, generated_locations = _split_reason_locations(
        data.get("reason") or data.get("description") or ""
    )
    related_locations = sorted({
        str(value).strip()
        for value in (
            list(data.get("relatedLocations") or [])
            + list(generated_locations)
        )
        if str(value).strip()
    })
    return {
        "index": index,
        "candidate_group": group_id,
        "existing_issue_id": str(data.get("id") or ""),
        "file": _normalized_file(data),
        "line": _line_number(data),
        "severity": str(data.get("severity") or ""),
        "category": str(data.get("category") or ""),
        "title": str(data.get("title") or ""),
        # Candidate grouping keeps the request small enough to preserve complete
        # root-cause prose. No field-level character clipping is performed here.
        "reason": reason,
        "suggested_fix": str(data.get("suggestedFixDescription") or ""),
        "exact_source_anchor": str(
            data.get("codeSnippet") or data.get("code_snippet") or ""
        ),
        "related_locations": related_locations,
    }


def _format_semantic_batch(
    issues: Sequence[CodeReviewIssue],
    group_by_index: Dict[int, str],
) -> str:
    return json.dumps(
        [
            _semantic_issue_payload(issue, index, group_by_index[index])
            for index, issue in enumerate(issues)
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _build_semantic_dedup_batches(
    groups: Sequence[Sequence[CodeReviewIssue]],
) -> List[tuple[List[CodeReviewIssue], Dict[int, str]]]:
    """Pack whole candidate groups by rendered size without clipping content."""
    batches: List[tuple[List[CodeReviewIssue], Dict[int, str]]] = []
    current_groups: List[Sequence[CodeReviewIssue]] = []
    current_chars = 0

    def group_size(group: Sequence[CodeReviewIssue], group_index: int) -> int:
        mapping = {index: f"candidate_{group_index}" for index in range(len(group))}
        return len(_format_semantic_batch(group, mapping))

    def flush() -> None:
        nonlocal current_groups, current_chars
        if not current_groups:
            return
        issues: List[CodeReviewIssue] = []
        mapping: Dict[int, str] = {}
        for local_group_index, group in enumerate(current_groups):
            group_id = f"candidate_{local_group_index}"
            for issue in group:
                mapping[len(issues)] = group_id
                issues.append(issue)
        batches.append((issues, mapping))
        current_groups = []
        current_chars = 0

    for group_index, group in enumerate(groups):
        rendered_chars = group_size(group, group_index)
        if current_groups and current_chars + rendered_chars > _DEDUP_BATCH_CHAR_BUDGET:
            flush()
        current_groups.append(group)
        current_chars += rendered_chars
        # An unusually detailed group is sent alone with its evidence intact.
        if rendered_chars > _DEDUP_BATCH_CHAR_BUDGET:
            flush()
    flush()
    return batches


def _build_batches(issues: List[CodeReviewIssue],
                   max_batch_size: int = _DEDUP_BATCH_SIZE,
                   ) -> List[List[CodeReviewIssue]]:
    """Group issues by filepath, then pack filepath-groups into batches ≤ max_batch_size.

    • Issues belonging to the same file are NEVER split across batches.
    • If a single file has more issues than *max_batch_size*, it gets its own
      (oversized) batch so that all issues for a file are always evaluated
      together.
    """
    file_groups: Dict[str, List[CodeReviewIssue]] = defaultdict(list)
    for issue in issues:
        data = issue.model_dump() if hasattr(issue, 'model_dump') else issue
        file_groups[data.get('file', '')].append(issue)

    batches: List[List[CodeReviewIssue]] = []
    current_batch: List[CodeReviewIssue] = []

    for _file_path, group in file_groups.items():
        # If adding this whole file-group would exceed the limit, flush first
        if current_batch and len(current_batch) + len(group) > max_batch_size:
            batches.append(current_batch)
            current_batch = []
        current_batch.extend(group)

    if current_batch:
        batches.append(current_batch)

    return batches


async def _dedup_batch_with_llm(
    llm,
    batch: List[CodeReviewIssue],
    group_by_index: Optional[Dict[int, str]] = None,
) -> List[CodeReviewIssue]:
    """Merge only validated high-confidence duplicate groups from one batch."""
    groups = group_by_index or {index: "candidate_0" for index in range(len(batch))}
    issues_text = _format_semantic_batch(batch, groups)
    prompt = (
        f"{_DEDUP_SYSTEM_PROMPT}\n\n"
        f"Candidate findings JSON:\n{issues_text}\n\n"
        "Return duplicate_groups only."
    )

    try:
        if supports_structured_output(llm):
            structured_llm = llm.with_structured_output(
                SemanticDeduplicationDecision
            )
            result: SemanticDeduplicationDecision = await structured_llm.ainvoke(
                prompt
            )
        else:
            logger.info("Structured output skipped for LLM dedup batch; using prompt JSON parsing")
            response = await llm.ainvoke(prompt)
            result = await parse_llm_response(
                extract_llm_response_text(response),
                SemanticDeduplicationDecision,
                llm,
            )

        removed_indices: set[int] = set()
        replacements: Dict[int, CodeReviewIssue] = {}
        accepted_groups = 0
        for decision in result.duplicate_groups:
            if str(decision.confidence or "").strip().upper() != "HIGH":
                continue
            keeper_index = decision.keeper_index
            duplicate_indices = list(dict.fromkeys(decision.duplicate_indices))
            all_indices = [keeper_index, *duplicate_indices]
            if (
                keeper_index < 0
                or keeper_index >= len(batch)
                or not duplicate_indices
                or any(index < 0 or index >= len(batch) for index in all_indices)
                or keeper_index in duplicate_indices
                or any(index in removed_indices for index in all_indices)
                or any(
                    index in replacements and index != keeper_index
                    for index in duplicate_indices
                )
                or len({groups.get(index) for index in all_indices}) != 1
            ):
                logger.warning(
                    "LLM dedup rejected malformed or cross-group decision: %s",
                    decision.model_dump(),
                )
                continue
            keeper = replacements.get(keeper_index, batch[keeper_index])
            if not all(
                issues_are_semantic_dedup_candidates(
                    keeper,
                    batch[duplicate_index],
                )
                for duplicate_index in duplicate_indices
            ):
                logger.warning(
                    "LLM dedup rejected decision without host candidate evidence: %s",
                    decision.model_dump(),
                )
                continue
            for duplicate_index in duplicate_indices:
                keeper = _merge_duplicate_issues(
                    keeper,
                    batch[duplicate_index],
                    prefer_left=True,
                )
                removed_indices.add(duplicate_index)
            replacements[keeper_index] = keeper
            accepted_groups += 1

        if not removed_indices:
            return batch
        kept: List[CodeReviewIssue] = []
        emitted_replacements: set[int] = set()
        for index, issue in enumerate(batch):
            if index in removed_indices:
                continue
            replacement = replacements.get(index, issue)
            replacement_id = id(replacement)
            if replacement_id in emitted_replacements:
                continue
            emitted_replacements.add(replacement_id)
            kept.append(replacement)
        logger.info(
            "LLM semantic dedup accepted %d group(s): %d → %d candidate issues",
            accepted_groups,
            len(batch),
            len(kept),
        )
        return kept

    except Exception as exc:
        logger.warning(
            "LLM dedup batch failed (%s); retaining ambiguous findings after "
            "exact deterministic dedup",
            exc,
        )
        return deduplicate_final_issues(batch)


async def deduplicate_final_issues_llm(
    llm,
    issues: List[CodeReviewIssue],
) -> List[CodeReviewIssue]:
    """Recall-safe semantic dedup over host-selected candidate components.

    Exact identities merge locally first. Only ambiguous components with two or
    more plausible duplicates consume model tokens, and a failed/incomplete LLM
    decision retains every ambiguous finding.
    """
    if not issues:
        return []

    exact_deduped = deduplicate_final_issues(issues)
    if len(exact_deduped) <= 1:
        return exact_deduped

    candidate_groups = _semantic_candidate_groups(exact_deduped)
    if not candidate_groups:
        logger.info(
            "LLM semantic dedup skipped: %d findings produced no ambiguous "
            "candidate group",
            len(exact_deduped),
        )
        return exact_deduped

    batches = _build_semantic_dedup_batches(candidate_groups)
    rendered_chars = sum(
        len(_format_semantic_batch(batch, mapping))
        for batch, mapping in batches
    )
    candidate_count = sum(len(group) for group in candidate_groups)
    logger.info(
        "LLM semantic dedup: %d/%d findings in %d candidate group(s), "
        "%d batch(es), input≈%d tokens, concurrency=%d",
        candidate_count,
        len(exact_deduped),
        len(candidate_groups),
        len(batches),
        rendered_chars // 4,
        _DEDUP_MAX_PARALLEL,
    )

    semaphore = asyncio.Semaphore(_DEDUP_MAX_PARALLEL)
    batch_results: Dict[int, List[CodeReviewIssue]] = {}

    async def _run_batch(
        batch_idx: int,
        batch: List[CodeReviewIssue],
        mapping: Dict[int, str],
    ) -> tuple[int, List[CodeReviewIssue]]:
        async with semaphore:
            logger.info(
                f"LLM dedup: processing batch {batch_idx + 1}/{len(batches)} "
                f"({len(batch)} issues)"
            )
            kept = await _dedup_batch_with_llm(llm, batch, mapping)
            return batch_idx, kept

    tasks = [
        asyncio.create_task(_run_batch(batch_idx, batch, mapping))
        for batch_idx, (batch, mapping) in enumerate(batches)
    ]

    for completed_task in asyncio.as_completed(tasks):
        batch_idx, kept = await completed_task
        batch_results[batch_idx] = kept

    candidate_object_ids = {
        id(issue)
        for group in candidate_groups
        for issue in group
    }
    retained_candidate_ids = {
        id(issue)
        for kept in batch_results.values()
        for issue in kept
    }
    removed_candidate_ids = candidate_object_ids - retained_candidate_ids
    kept_issues = [
        issue
        for issue in exact_deduped
        if id(issue) not in removed_candidate_ids
    ]

    original = len(issues)
    final = len(kept_issues)
    if original != final:
        logger.info(
            f"LLM dedup total: {original} → {final} issues "
            f"({original - final} duplicates removed)"
        )
    return kept_issues


def deduplicate_cross_batch_issues(issues: List[CodeReviewIssue]) -> List[CodeReviewIssue]:
    """
    Deduplicate repeated anchored findings from overlapping Stage 1 batches.

    Different files, categories, and source anchors are never duplicates even
    when their prose is similar.
    """
    if not issues:
        return []
        
    deduped = []
    for issue in issues:
        duplicate_index = next((
            index
            for index, existing in enumerate(deduped)
            if issues_are_conservative_duplicates(issue, existing)
        ), None)
        if duplicate_index is None:
            deduped.append(issue)
            continue
        deduped[duplicate_index] = _merge_duplicate_issues(
            deduped[duplicate_index],
            issue,
        )
        issue_data = _issue_payload(issue)
        logger.info(
            "Cross-batch dedup: merged exact root identity at %s:%s",
            issue_data.get("file", ""),
            issue_data.get("line", ""),
        )

    return deduped

async def reconcile_previous_issues(
    request,
    new_issues: List[CodeReviewIssue],
    processed_diff = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> List[CodeReviewIssue]:
    """
    Reconcile previous issues with new findings in incremental mode.
    - Mark resolved issues as isResolved=true
    - Update line numbers for persisting issues
    - Merge with new issues found in delta diff
    - PRESERVE original issue data (reason, suggestedFixDescription, suggestedFixDiff)
    """
    if not request.previousCodeAnalysisIssues:
        return new_issues
    
    logger.info(f"Reconciling {len(request.previousCodeAnalysisIssues)} previous issues with {len(new_issues)} new issues")
    
    # Current commit for resolution tracking
    current_commit = request.currentCommitHash or request.commitHash
    
    # Get the delta diff content to check what files/lines changed
    delta_diff = request.deltaDiff or ""
    
    # Build a set of files that changed in the delta
    changed_files_in_delta = set()
    if processed_diff:
        for f in processed_diff.files:
            changed_files_in_delta.add(f.path)
    
    # Build lookup of previous issues by ID for merging with LLM results
    prev_issues_by_id = {}
    closed_prev_ids = set()
    for prev_issue in request.previousCodeAnalysisIssues:
        if hasattr(prev_issue, 'model_dump'):
            prev_data = prev_issue.model_dump()
        else:
            prev_data = prev_issue if isinstance(prev_issue, dict) else vars(prev_issue)
        issue_id = prev_data.get('id')
        if issue_id:
            normalized_id = str(issue_id)
            if _is_open_previous_issue(prev_data):
                prev_issues_by_id[normalized_id] = prev_data
            else:
                closed_prev_ids.add(normalized_id)
    
    reconciled_issues = []
    processed_prev_ids = set()  # Track which previous issues we've handled
    
    # Process new issues from LLM - merge with previous issue data if they reference same ID
    for new_issue in new_issues:
        new_data = new_issue.model_dump() if hasattr(new_issue, 'model_dump') else new_issue
        issue_id = new_data.get('id')

        if issue_id and str(issue_id) in closed_prev_ids:
            logger.info(
                "Ignoring model output for terminal previous issue %s",
                issue_id,
            )
            if candidate_ledger is not None:
                candidate_ledger.reject(
                    new_issue,
                    gate="reconciliation",
                    code="terminal_history_id",
                )
            continue
        
        # If no ID provided, check if it's semantically similar to an OPEN previous issue
        if not issue_id:
            new_reason = new_data.get('reason', '')
            new_file = new_data.get('file', '')
            for prev_id, prev_data in prev_issues_by_id.items():
                if prev_data.get('status', '').lower() == 'resolved':
                    continue
                prev_file = prev_data.get('file') or prev_data.get('filePath') or ''
                if new_file == prev_file:
                    prev_reason = prev_data.get('reason') or prev_data.get('title') or prev_data.get('description') or ''
                    if is_semantically_similar(new_reason, prev_reason, threshold=0.70):
                        logger.info(f"Semantic match found: mapping new issue to previous ID {prev_id}")
                        issue_id = prev_id
                        break
        
        # If this issue references a previous issue ID, merge data
        if issue_id and str(issue_id) in prev_issues_by_id:
            prev_data = prev_issues_by_id[str(issue_id)]
            processed_prev_ids.add(str(issue_id))
            
            # Check if LLM marked it resolved
            llm_says_resolved = new_data.get('isResolved', False)

            is_resolved = llm_says_resolved
            
            # Determine resolution metadata
            if is_resolved:
                # Newly resolved by LLM
                resolution_explanation = _resolution_text(new_data) or _DEFAULT_RESOLUTION_TEXT
                resolved_commit = current_commit
            else:
                resolution_explanation = None
                resolved_commit = None
            
            # PRESERVE original identity & description, but use LLM's UPDATED
            # positional data (line, codeSnippet, scope).  The LLM re-analyzed
            # the current code — its line/snippet reflect where the issue IS NOW,
            # not where it was in a previous iteration.
            #
            # Positional fields: prefer new_data → prev_data (LLM re-anchored)
            # Identity/description fields: prefer prev_data → new_data (stable)

            # ── Positional data: LLM re-analysis wins ──
            new_line = new_data.get('line')
            prev_line = prev_data.get('line') or prev_data.get('lineNumber')

            # Safely parse line numbers — the LLM may return non-numeric values
            try:
                new_line_int = int(new_line) if new_line is not None else None
            except (ValueError, TypeError):
                new_line_int = None
                logger.warning(f"Non-numeric new_line value '{new_line}' for issue {issue_id}, ignoring")

            try:
                prev_line_int = int(prev_line) if prev_line is not None else None
            except (ValueError, TypeError):
                prev_line_int = None
                logger.warning(f"Non-numeric prev_line value '{prev_line}' for issue {issue_id}, ignoring")

            # Use the LLM's line if it provided a meaningful one (>= 1),
            # otherwise fall back to previous data, then default to 1.
            # NOTE: Line 1 IS valid — e.g., license header issues, package declarations.
            # The old check (> 1) incorrectly discarded legitimate line-1 findings.
            if new_line_int is not None and new_line_int >= 1:
                merged_line = new_line_int
            elif prev_line_int is not None and prev_line_int >= 1:
                merged_line = prev_line_int
            else:
                merged_line = new_line_int or prev_line_int or 1

            new_snippet = new_data.get('codeSnippet', '')
            prev_snippet = prev_data.get('codeSnippet', '')
            # Prefer non-empty new snippet (LLM just produced it from current code)
            merged_snippet = new_snippet if new_snippet else prev_snippet

            merged_scope = new_data.get('scope') or prev_data.get('scope') or 'LINE'

            merged_issue = CodeReviewIssue(
                id=str(issue_id),
                severity=(prev_data.get('severity') or prev_data.get('issueSeverity') or 'MEDIUM').upper(),
                category=prev_data.get('category') or prev_data.get('issueCategory') or prev_data.get('type') or 'CODE_QUALITY',
                file=prev_data.get('file') or prev_data.get('filePath') or new_data.get('file', 'unknown'),
                line=str(merged_line),
                scope=merged_scope,
                # PRESERVE original title, reason and fix description
                title=prev_data.get('title') or new_data.get('title'),
                reason=prev_data.get('reason') or prev_data.get('title') or prev_data.get('description') or '',
                suggestedFixDescription=prev_data.get('suggestedFixDescription') or prev_data.get('suggestedFix') or '',
                suggestedFixDiff=prev_data.get('suggestedFixDiff') or None,
                isResolved=is_resolved,
                resolutionReason=resolution_explanation,
                resolutionExplanation=resolution_explanation,
                resolvedInCommit=resolved_commit,
                visibility=prev_data.get('visibility'),
                codeSnippet=merged_snippet,
            )
            logger.info(
                f"Reconciled issue {issue_id}: line {prev_line}→{merged_line}, "
                f"snippet={'YES' if merged_snippet else 'NONE'} "
                f"(source={'LLM' if new_snippet else 'prev'}), "
                f"scope={merged_scope}, resolved={is_resolved}"
            )
            if candidate_ledger is not None:
                candidate_ledger.transfer(new_issue, merged_issue)
            reconciled_issues.append(merged_issue)
        else:
            # New issue not referencing previous - keep as is
            reconciled_issues.append(new_issue)
    
    # Process remaining previous issues not handled by LLM
    for prev_issue in request.previousCodeAnalysisIssues:
        if hasattr(prev_issue, 'model_dump'):
            prev_data = prev_issue.model_dump()
        else:
            prev_data = prev_issue if isinstance(prev_issue, dict) else vars(prev_issue)

        if not _is_open_previous_issue(prev_data):
            continue
        
        issue_id = prev_data.get('id')
        if issue_id and str(issue_id) in processed_prev_ids:
            continue  # Already handled above
        
        file_path = prev_data.get('file', prev_data.get('filePath', ''))
        
        # Check if this issue was already found in new issues (by file+line)
        already_reported = False
        for new_issue in new_issues:
            new_data = new_issue.model_dump() if hasattr(new_issue, 'model_dump') else new_issue
            if (new_data.get('file') == file_path and 
                str(new_data.get('line')) == str(prev_data.get('line', prev_data.get('lineNumber')))):
                already_reported = True
                break
        
        if already_reported:
            continue
        
        # Preserve unhandled OPEN history so it remains active until explicitly
        # matched and resolved. Terminal history was skipped above.
        persisting_issue = CodeReviewIssue(
            id=str(issue_id) if issue_id else None,
            severity=(prev_data.get('severity') or prev_data.get('issueSeverity') or 'MEDIUM').upper(),
            category=prev_data.get('category') or prev_data.get('issueCategory') or prev_data.get('type') or 'CODE_QUALITY',
            file=file_path or prev_data.get('file') or prev_data.get('filePath') or 'unknown',
            line=str(prev_data.get('line') or prev_data.get('lineNumber') or '1'),
            scope=prev_data.get('scope') or prev_data.get('issueScope') or 'LINE',
            title=prev_data.get('title'),
            reason=prev_data.get('reason') or prev_data.get('title') or prev_data.get('description') or '',
            suggestedFixDescription=prev_data.get('suggestedFixDescription') or prev_data.get('suggestedFix') or '',
            suggestedFixDiff=prev_data.get('suggestedFixDiff') or None,
            isResolved=False,
            resolutionReason=None,
            resolutionExplanation=None,
            resolvedInCommit=None,
            visibility=prev_data.get('visibility'),
            codeSnippet=prev_data.get('codeSnippet') or ''
        )
        reconciled_issues.append(persisting_issue)
    
    before_history_dedup = list(reconciled_issues)
    reconciled_issues = _deduplicate_reconciled_history(
        reconciled_issues,
        set(prev_issues_by_id),
    )
    if candidate_ledger is not None:
        candidate_ledger.reject_removed(
            before_history_dedup,
            reconciled_issues,
            gate="reconciliation",
            code="duplicate_history_identity",
        )
    resolved_kept = sum(1 for i in reconciled_issues if (hasattr(i, 'isResolved') and i.isResolved) or (isinstance(i, dict) and i.get('isResolved')))
    logger.info(f"Reconciliation complete: {len(reconciled_issues)} total issues ({resolved_kept} preserved as resolved)")
    return reconciled_issues
