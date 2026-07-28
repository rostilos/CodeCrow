-- Candidate execution identifiers are not populated by the application yet.
-- Preserve the currently enforced one-analysis-per-PR-head contract until the
-- execution manifest is application-owned end to end. Failing the migration
-- on pre-existing duplicates is safer than silently disabling idempotency.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_code_analysis_project_commit'
          AND conrelid = 'code_analysis'::regclass
    ) THEN
        ALTER TABLE code_analysis
            ADD CONSTRAINT uq_code_analysis_project_commit
            UNIQUE (project_id, commit_hash, pr_number);
    END IF;
END;
$$;
