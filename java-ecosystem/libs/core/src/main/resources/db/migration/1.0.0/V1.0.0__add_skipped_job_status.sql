-- Add SKIPPED status to job_status_check constraint
-- This is used for IGNORED_COMMENT job types that are immediately skipped

ALTER TABLE job DROP CONSTRAINT IF EXISTS job_status_check;

ALTER TABLE job ADD CONSTRAINT job_status_check CHECK (
    status IN (
        'PENDING',
        'QUEUED',
        'RUNNING',
        'COMPLETED',
        'FAILED',
        'CANCELLED',
        'WAITING',
        'SKIPPED'
    )
);
