-- ============================================================================
-- V2.2.0: Widen tracking_confidence columns and update CHECK constraints
-- ============================================================================
-- The PR issue tracking service now uses UNANCHORED_FP_MATCH (19 chars) for
-- unanchored issue matching. The code_analysis_issue.tracking_confidence column
-- was still VARCHAR(10) — causing "value too long" errors on save.
--
-- branch_issue was already widened to VARCHAR(30) in V1.6.0, but its CHECK
-- constraint was never updated. Both tables are aligned here.
-- ============================================================================

-- ── code_analysis_issue: widen column + update CHECK ────────────────────────

ALTER TABLE code_analysis_issue
    ALTER COLUMN tracking_confidence TYPE VARCHAR(30);

ALTER TABLE code_analysis_issue
    DROP CONSTRAINT IF EXISTS chk_code_analysis_issue_tracking_confidence;

ALTER TABLE code_analysis_issue
    ADD CONSTRAINT chk_code_analysis_issue_tracking_confidence
    CHECK (tracking_confidence IS NULL
        OR tracking_confidence IN ('EXACT', 'SHIFTED', 'EDITED', 'WEAK', 'NONE', 'UNANCHORED_FP_MATCH'));

-- ── branch_issue: update CHECK constraint (column already VARCHAR(30)) ──────

ALTER TABLE branch_issue
    DROP CONSTRAINT IF EXISTS chk_branch_issue_tracking_confidence;

ALTER TABLE branch_issue
    ADD CONSTRAINT chk_branch_issue_tracking_confidence
    CHECK (tracking_confidence IS NULL
        OR tracking_confidence IN ('EXACT', 'SHIFTED', 'EDITED', 'WEAK', 'NONE', 'UNANCHORED_FP_MATCH'));
