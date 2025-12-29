-- Add PR_RECONCILIATION job type for analyzing potentially resolved issues in PRs
-- This runs as a separate job from PR_ANALYSIS and posts results as a PR comment

-- Drop the existing constraint
ALTER TABLE job DROP CONSTRAINT IF EXISTS job_job_type_check;

-- Add the updated constraint with PR_RECONCILIATION type
ALTER TABLE job ADD CONSTRAINT job_job_type_check CHECK (
    job_type IN (
        'PR_ANALYSIS',
        'PR_RECONCILIATION',
        'BRANCH_ANALYSIS',
        'BRANCH_RECONCILIATION',
        'RAG_INITIAL_INDEX',
        'RAG_INCREMENTAL_INDEX',
        'MANUAL_ANALYSIS',
        'REPO_SYNC',
        'SUMMARIZE_COMMAND',
        'ASK_COMMAND',
        'ANALYZE_COMMAND',
        'REVIEW_COMMAND',
        'IGNORED_COMMENT'
    )
);
