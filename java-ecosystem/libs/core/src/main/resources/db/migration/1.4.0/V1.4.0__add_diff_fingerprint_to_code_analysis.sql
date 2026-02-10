-- Add diff_fingerprint column for content-based analysis caching.
-- Allows reusing analysis results when the same code changes appear in different PRs
-- (e.g. close/reopen with a new PR number, or branch-cascade flows like feature→release→main).
ALTER TABLE code_analysis ADD COLUMN IF NOT EXISTS diff_fingerprint VARCHAR(64);

-- Index for fingerprint-based cache lookups: (project_id, diff_fingerprint)
CREATE INDEX IF NOT EXISTS idx_code_analysis_project_diff_fingerprint
    ON code_analysis (project_id, diff_fingerprint)
    WHERE diff_fingerprint IS NOT NULL;
