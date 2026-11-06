ALTER TABLE code_analysis_issue ADD COLUMN IF NOT EXISTS review_check_id VARCHAR(128);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS review_check_id VARCHAR(128);
