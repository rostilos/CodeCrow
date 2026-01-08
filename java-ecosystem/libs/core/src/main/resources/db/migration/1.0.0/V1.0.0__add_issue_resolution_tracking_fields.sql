-- Add resolution tracking fields to code_analysis_issue table
-- These fields preserve the original issue data while tracking resolution metadata separately

ALTER TABLE code_analysis_issue 
ADD COLUMN IF NOT EXISTS resolved_description TEXT,
ADD COLUMN IF NOT EXISTS resolved_by_pr BIGINT,
ADD COLUMN IF NOT EXISTS resolved_commit_hash VARCHAR(40),
ADD COLUMN IF NOT EXISTS resolved_analysis_id BIGINT,
ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS resolved_by VARCHAR(100);

-- Add resolution tracking fields to branch_issue table
ALTER TABLE branch_issue 
ADD COLUMN IF NOT EXISTS resolved_description TEXT,
ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS resolved_by VARCHAR(100);

-- Add comments explaining the purpose of these fields
COMMENT ON COLUMN code_analysis_issue.resolved_description IS 'AI or user explanation of how/why the issue was resolved';
COMMENT ON COLUMN code_analysis_issue.resolved_by_pr IS 'PR number that resolved this issue (if applicable)';
COMMENT ON COLUMN code_analysis_issue.resolved_commit_hash IS 'Commit hash that resolved this issue';
COMMENT ON COLUMN code_analysis_issue.resolved_analysis_id IS 'Analysis ID during which this issue was resolved';
COMMENT ON COLUMN code_analysis_issue.resolved_at IS 'Timestamp when the issue was marked as resolved';
COMMENT ON COLUMN code_analysis_issue.resolved_by IS 'Actor who resolved the issue (user, AI-reconciliation, manual)';

COMMENT ON COLUMN branch_issue.resolved_description IS 'AI or user explanation of how/why the issue was resolved on this branch';
COMMENT ON COLUMN branch_issue.resolved_at IS 'Timestamp when the issue was marked as resolved on this branch';
COMMENT ON COLUMN branch_issue.resolved_by IS 'Actor who resolved the issue on this branch (user, AI-reconciliation, manual)';
