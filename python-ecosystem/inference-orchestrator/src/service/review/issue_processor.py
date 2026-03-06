"""
Issue Post-Processor — thin pass-through.

All heavy-lifting (line correction, dedup, diff sanitization, diff/description
restoration) now happens on the Java side in CodeAnalysisService, DiffSanitizer,
and IssueDeduplicationService.  This module is kept only so that existing
call-sites (review_service.py → post_process_analysis_result) continue to work
without code changes in the orchestrator layer.

Migration history:
  Phase 1 – Java equivalents added (DiffSanitizer, IssueDeduplicationService,
             diff-restore in createIssueFromData).
  Phase 3 – Python logic gutted; this file is now a no-op pass-through.
"""
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


def post_process_analysis_result(
    result: Dict[str, Any],
    **kwargs,
) -> Dict[str, Any]:
    """
    No-op pass-through.  All post-processing is handled Java-side.

    Accepts (and ignores) any legacy keyword arguments so callers that still
    pass ``diff_content``, ``file_contents``, or ``previous_issues`` keep
    working without changes.
    """
    issue_count = len(result.get('issues', [])) if 'issues' in result else 0
    logger.info(
        "Python post_process_analysis_result pass-through: %d issues (no-op, Java handles processing)",
        issue_count,
    )
    return result
