-- V2.15.0 through V2.17.0 have already shipped and must remain immutable.
-- Apply the later review-schema simplification as a forward-only migration.

CREATE OR REPLACE FUNCTION reject_review_analysis_state_identity_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.execution_id IS DISTINCT FROM NEW.execution_id
        OR OLD.artifact_manifest_digest IS DISTINCT FROM NEW.artifact_manifest_digest
        OR OLD.diff_digest IS DISTINCT FROM NEW.diff_digest
        OR OLD.diff_byte_length IS DISTINCT FROM NEW.diff_byte_length
        OR OLD.ledger_digest IS DISTINCT FROM NEW.ledger_digest THEN
        RAISE EXCEPTION 'coverage analysis identity cannot be updated'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

ALTER TABLE review_execution
    DROP COLUMN schema_version,
    DROP COLUMN diff_artifact_producer_version,
    DROP COLUMN artifact_schema_version,
    DROP COLUMN policy_version;

ALTER TABLE review_artifact
    DROP COLUMN artifact_schema_version,
    DROP COLUMN producer_version;

ALTER TABLE review_coverage_anchor
    DROP COLUMN schema_version;

ALTER TABLE review_analysis_state
    DROP COLUMN schema_version;

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
