-- A PR head may be reviewed again after review-affecting engine behavior changes.
-- Keep retries idempotent within one behavior contract while retaining both
-- results for the same project/PR/head across behavior changes.

ALTER TABLE code_analysis
    ALTER COLUMN commit_hash TYPE VARCHAR(64),
    ADD COLUMN base_commit_hash VARCHAR(64) NOT NULL DEFAULT '',
    ADD COLUMN analysis_behavior_digest VARCHAR(64) NOT NULL
        DEFAULT '13b3b60741ccf0da771d0adada28590693ed3a436d3ccca12e7888308a41bb56';

ALTER TABLE code_analysis
    DROP CONSTRAINT IF EXISTS uq_code_analysis_project_commit;

ALTER TABLE code_analysis
    ADD CONSTRAINT uq_code_analysis_project_commit
    UNIQUE (project_id, commit_hash, pr_number, base_commit_hash, analysis_behavior_digest);
