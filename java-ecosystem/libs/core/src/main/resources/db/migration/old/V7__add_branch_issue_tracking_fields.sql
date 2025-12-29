-- Add tracking fields to branch_issue table for better PR correlation
-- This enables tracking when issues were introduced and resolved across PRs

ALTER TABLE branch_issue
    ADD COLUMN severity VARCHAR(10);

-- Update existing records to set severity from related code_analysis_issue
UPDATE branch_issue bci
SET severity = (
    SELECT cai.severity 
    FROM code_analysis_issue cai 
    WHERE cai.id = bci.code_analysis_issue_id
);

-- Set default severity for any remaining nulls
UPDATE branch_issue
SET severity = 'MEDIUM' 
WHERE severity IS NULL;

-- Now make it NOT NULL
ALTER TABLE branch_issue
    ALTER COLUMN severity SET NOT NULL;

-- Add indexes for performance
CREATE INDEX idx_branch_issue_resolved ON branch_issue(branch_id, resolved);

-- Add comments for documentation
COMMENT ON COLUMN branch_issue.severity IS 'Severity level of the issue (HIGH, MEDIUM, LOW)';
