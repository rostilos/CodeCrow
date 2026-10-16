-- The PR worker lease serializes writes; retry reuse compares the complete review identity.
-- Rules, model, target and historical decisions may change while the source commit stays fixed.
ALTER TABLE code_analysis DROP CONSTRAINT IF EXISTS uq_code_analysis_project_commit;
CREATE INDEX IF NOT EXISTS idx_code_analysis_pr_head_iteration
    ON code_analysis (project_id, pr_number, commit_hash, pr_version DESC, id DESC);
