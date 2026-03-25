-- =============================================================================
-- V2.4.0 — Add QA_DOC_COMMAND to job.job_type CHECK constraint
-- =============================================================================
-- The QA Auto-Documentation feature introduces a new command job type
-- (QA_DOC_COMMAND) that needs to be accepted by the job table's CHECK
-- constraint. Without this, inserting a job row for /codecrow qa-doc
-- commands fails with a constraint violation.
-- =============================================================================

-- Drop the old constraint and recreate with the new value included.
ALTER TABLE job DROP CONSTRAINT IF EXISTS job_job_type_check;

ALTER TABLE job ADD CONSTRAINT job_job_type_check CHECK (
    (job_type)::text = ANY (ARRAY[
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
        'REVIEW_COMMAND',
        'IGNORED_COMMENT',
        'QA_DOC_COMMAND'
    ])
);
