-- Every accepted PR analysis attempt is a distinct occurrence. A digest of its
-- durable Job key and confirmed review snapshot keeps recovery/replay idempotent
-- without collapsing intentional same-head reruns into an older result.

ALTER TABLE code_analysis
    ADD COLUMN analysis_run_key VARCHAR(64);

ALTER TABLE code_analysis
    DROP CONSTRAINT IF EXISTS uq_code_analysis_project_commit;

ALTER TABLE code_analysis
    ADD CONSTRAINT uq_code_analysis_project_run
    UNIQUE (project_id, analysis_run_key);

-- Callers predating durable run identity keep the previous idempotency
-- contract. PostgreSQL's normal NULL uniqueness semantics match the dropped
-- table constraint for nullable PR identity components.
CREATE UNIQUE INDEX uq_code_analysis_project_commit_legacy
    ON code_analysis (
        project_id,
        commit_hash,
        pr_number,
        base_commit_hash,
        analysis_behavior_digest
    )
    WHERE analysis_run_key IS NULL;
