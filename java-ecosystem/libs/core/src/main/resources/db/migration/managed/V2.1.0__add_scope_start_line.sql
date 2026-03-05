-- ============================================================================
-- V2.1.0: Add scope_start_line to issue tables (AST-resolved scope boundaries)
-- ============================================================================
-- The AST parser now resolves the enclosing scope for each issue.
-- scope_start_line stores the start of the innermost AST scope (function, class,
-- block) that contains the issue line. end_line_number (added in V2.0.0) is now
-- populated by the AST parser instead of the AI response.
--
-- BranchIssue also gets current_scope_start_line for drift tracking (analogous
-- to the existing current_end_line_number column).
-- ============================================================================

-- ── code_analysis_issue ─────────────────────────────────────────────────────

ALTER TABLE code_analysis_issue
    ADD COLUMN IF NOT EXISTS scope_start_line INTEGER;

-- ── branch_issue ────────────────────────────────────────────────────────────

ALTER TABLE branch_issue
    ADD COLUMN IF NOT EXISTS scope_start_line INTEGER,
    ADD COLUMN IF NOT EXISTS current_scope_start_line INTEGER;
