-- Add new job types for comment commands (SUMMARIZE_COMMAND, ASK_COMMAND, ANALYZE_COMMAND, REVIEW_COMMAND)
-- This migration updates the job_type check constraint to include the new command-specific job types

-- Drop the existing constraint
ALTER TABLE job DROP CONSTRAINT IF EXISTS job_job_type_check;

-- Add the updated constraint with new job types
ALTER TABLE job ADD CONSTRAINT job_job_type_check CHECK (
    job_type IN (
        'PR_ANALYSIS',
        'BRANCH_ANALYSIS',
        'BRANCH_RECONCILIATION',
        'RAG_INITIAL_INDEX',
        'RAG_INCREMENTAL_INDEX',
        'MANUAL_ANALYSIS',
        'REPO_SYNC',
        'SUMMARIZE_COMMAND',
        'ASK_COMMAND',
        'ANALYZE_COMMAND',
        'REVIEW_COMMAND'
    )
);
