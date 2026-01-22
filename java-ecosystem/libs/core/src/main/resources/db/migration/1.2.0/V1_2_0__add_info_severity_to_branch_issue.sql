-- Add INFO to the severity check constraint on branch_issue table
-- This aligns branch_issue with code_analysis_issue which already supports INFO severity

-- Drop the existing constraint if it exists
ALTER TABLE branch_issue DROP CONSTRAINT IF EXISTS branch_issue_severity_check;

-- Add the updated constraint that includes INFO
ALTER TABLE branch_issue 
ADD CONSTRAINT branch_issue_severity_check 
CHECK (severity IN ('HIGH', 'MEDIUM', 'LOW', 'INFO', 'RESOLVED'));
