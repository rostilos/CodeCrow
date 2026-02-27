-- ============================================================================
-- V1.5.3: Add content-based issue tracking columns
-- ============================================================================
-- Adds line hashes, fingerprints, and tracking metadata to support
-- deterministic issue tracking across branch analyses.
-- All columns are nullable for backward compatibility with existing data.
-- ============================================================================

-- 1. CodeAnalysisIssue: content-based identity anchors
ALTER TABLE code_analysis_issue
    ADD COLUMN IF NOT EXISTS line_hash VARCHAR(32),
    ADD COLUMN IF NOT EXISTS line_hash_context VARCHAR(32),
    ADD COLUMN IF NOT EXISTS issue_fingerprint VARCHAR(64);

-- Index on fingerprint for fast dedup lookups during branch mapping
CREATE INDEX IF NOT EXISTS idx_code_analysis_issue_fingerprint
    ON code_analysis_issue (issue_fingerprint)
    WHERE issue_fingerprint IS NOT NULL;

-- Composite index for tracking queries (file + fingerprint)
CREATE INDEX IF NOT EXISTS idx_code_analysis_issue_file_fingerprint
    ON code_analysis_issue (file_path, issue_fingerprint)
    WHERE issue_fingerprint IS NOT NULL;

-- 2. BranchIssue: tracking state for branch-level issues
ALTER TABLE branch_issue
    ADD COLUMN IF NOT EXISTS current_line_number INTEGER,
    ADD COLUMN IF NOT EXISTS current_line_hash VARCHAR(32),
    ADD COLUMN IF NOT EXISTS last_verified_commit VARCHAR(40),
    ADD COLUMN IF NOT EXISTS tracking_confidence VARCHAR(10);

-- Add check constraint for tracking_confidence enum values
ALTER TABLE branch_issue
    DROP CONSTRAINT IF EXISTS chk_branch_issue_tracking_confidence;
ALTER TABLE branch_issue
    ADD CONSTRAINT chk_branch_issue_tracking_confidence
    CHECK (tracking_confidence IS NULL OR tracking_confidence IN ('EXACT', 'SHIFTED', 'EDITED', 'WEAK', 'NONE'));

-- Index for finding issues that need re-verification
CREATE INDEX IF NOT EXISTS idx_branch_issue_last_verified
    ON branch_issue (last_verified_commit)
    WHERE is_resolved = false;

-- 3. BranchFile: content tracking for skip-unchanged optimization
ALTER TABLE branch_file
    ADD COLUMN IF NOT EXISTS content_hash VARCHAR(40),
    ADD COLUMN IF NOT EXISTS line_count INTEGER,
    ADD COLUMN IF NOT EXISTS last_analyzed_commit VARCHAR(40),
    ADD COLUMN IF NOT EXISTS branch_id BIGINT;

-- FK to branch entity (supplements denormalized branch_name)
ALTER TABLE branch_file
    DROP CONSTRAINT IF EXISTS fk_branch_file_branch;
ALTER TABLE branch_file
    ADD CONSTRAINT fk_branch_file_branch
    FOREIGN KEY (branch_id) REFERENCES branch (id)
    ON DELETE SET NULL;

-- Index for branch FK
CREATE INDEX IF NOT EXISTS idx_branch_file_branch_id
    ON branch_file (branch_id)
    WHERE branch_id IS NOT NULL;
