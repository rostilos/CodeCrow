-- Branch health tracking for delta-based analysis
-- Adds fields to support commit-range diffs and branch health monitoring.
-- lastSuccessfulCommitHash tracks the last commit that was fully analyzed successfully,
-- enabling delta diffs (lastSuccessfulCommitHash..HEAD) that capture all changes since last success.

ALTER TABLE branch ADD COLUMN IF NOT EXISTS last_successful_commit_hash VARCHAR(40);
ALTER TABLE branch ADD COLUMN IF NOT EXISTS health_status VARCHAR(20) NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE branch ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE branch ADD COLUMN IF NOT EXISTS last_health_check_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_branch_health_status ON branch(health_status);
