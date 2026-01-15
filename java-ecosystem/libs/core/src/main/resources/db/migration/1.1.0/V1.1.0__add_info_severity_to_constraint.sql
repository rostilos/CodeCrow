-- Add INFO to the severity check constraint on code_analysis_issue table
-- This is needed because INFO was added to IssueSeverity enum but the constraint wasn't updated

-- Drop the existing constraint and recreate with INFO included
ALTER TABLE code_analysis_issue DROP CONSTRAINT IF EXISTS code_analysis_issue_severity_check;

ALTER TABLE code_analysis_issue 
ADD CONSTRAINT code_analysis_issue_severity_check 
CHECK (severity IN ('HIGH', 'MEDIUM', 'LOW', 'INFO', 'RESOLVED'));
