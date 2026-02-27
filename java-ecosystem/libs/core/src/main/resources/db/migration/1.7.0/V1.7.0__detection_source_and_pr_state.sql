-- V1.7.0: Detection source tracking and PR state management
--
-- Adds detection_source to code_analysis_issue and branch_issue to distinguish
-- issues found via PR analysis vs direct push (hybrid branch) analysis.
-- Adds state tracking to pull_request for commit coverage checks.

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 1: Add detection_source to code_analysis_issue
-- ═══════════════════════════════════════════════════════════════════════

ALTER TABLE code_analysis_issue
    ADD COLUMN IF NOT EXISTS detection_source VARCHAR(30) DEFAULT 'PR_ANALYSIS';

COMMENT ON COLUMN code_analysis_issue.detection_source IS
    'How the issue was detected: PR_ANALYSIS (via pull request review) or DIRECT_PUSH_ANALYSIS (via hybrid branch analysis on uncovered commits)';

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 2: Add detection_source to branch_issue
-- ═══════════════════════════════════════════════════════════════════════

ALTER TABLE branch_issue
    ADD COLUMN IF NOT EXISTS detection_source VARCHAR(30) DEFAULT 'PR_ANALYSIS';

COMMENT ON COLUMN branch_issue.detection_source IS
    'How the issue was detected: PR_ANALYSIS or DIRECT_PUSH_ANALYSIS. Copied from the originating CodeAnalysisIssue.';

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 3: Add state to pull_request
-- ═══════════════════════════════════════════════════════════════════════

ALTER TABLE pull_request
    ADD COLUMN IF NOT EXISTS state VARCHAR(20) DEFAULT 'OPEN';

COMMENT ON COLUMN pull_request.state IS
    'Lifecycle state of the pull request: OPEN, MERGED, or DECLINED. Used by CommitCoverageService to check if unanalyzed commits are covered by open PRs.';

-- Create index for the coverage check query (project + target branch + state)
CREATE INDEX IF NOT EXISTS idx_pull_request_coverage_check
    ON pull_request (project_id, target_branch_name, state);
