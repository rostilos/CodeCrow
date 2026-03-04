-- ============================================================================
-- V2.0.0: Add issue_scope and end_line_number to issue tables
-- ============================================================================
-- Adds scope granularity (LINE / BLOCK / FUNCTION / FILE) and end-line support
-- to both code_analysis_issue (PR-level) and branch_issue (branch-level) tables.
--
-- Backfill strategy:
--   - Issues with line_number > 1 AND code_snippet IS NOT NULL → LINE
--   - Issues with line_number <= 1 OR code_snippet IS NULL      → FILE
--   - New issues going forward will have scope set by the LLM
-- ============================================================================

-- ── code_analysis_issue ─────────────────────────────────────────────────────

ALTER TABLE code_analysis_issue
    ADD COLUMN IF NOT EXISTS issue_scope VARCHAR(20),
    ADD COLUMN IF NOT EXISTS end_line_number INTEGER;

-- Backfill existing issues
UPDATE code_analysis_issue
SET issue_scope = CASE
    WHEN (line_number IS NOT NULL AND line_number > 1 AND code_snippet IS NOT NULL AND code_snippet <> '')
        THEN 'LINE'
    ELSE 'FILE'
END
WHERE issue_scope IS NULL;

-- ── branch_issue ────────────────────────────────────────────────────────────

ALTER TABLE branch_issue
    ADD COLUMN IF NOT EXISTS issue_scope VARCHAR(20),
    ADD COLUMN IF NOT EXISTS end_line_number INTEGER,
    ADD COLUMN IF NOT EXISTS current_end_line_number INTEGER;

-- Backfill existing issues
UPDATE branch_issue
SET issue_scope = CASE
    WHEN (line_number IS NOT NULL AND line_number > 1 AND code_snippet IS NOT NULL AND code_snippet <> '')
        THEN 'LINE'
    ELSE 'FILE'
END
WHERE issue_scope IS NULL;
