-- Add VCS author columns to code_analysis_issue table
-- These track who authored the PR that introduced the issue

ALTER TABLE code_analysis_issue
    ADD COLUMN vcs_author_id VARCHAR(100),
    ADD COLUMN vcs_author_username VARCHAR(100);

-- Add index for filtering by author
CREATE INDEX idx_code_analysis_issue_vcs_author ON code_analysis_issue(vcs_author_username);
