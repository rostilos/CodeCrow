-- V1.5.5: PR-level source code accumulation
--
-- Adds pull_request_id FK to analyzed_file_snapshot so that file snapshots
-- can be stored at the PR level instead of (or in addition to) the analysis level.
-- This lets files accumulate across PR iterations — the 2nd analysis run
-- adds/updates only changed files while all previously-seen files remain.

-- 1. Add nullable pull_request_id column
ALTER TABLE analyzed_file_snapshot
    ADD COLUMN IF NOT EXISTS pull_request_id BIGINT;

-- 2. Foreign key to pull_request table
ALTER TABLE analyzed_file_snapshot
    DROP CONSTRAINT IF EXISTS fk_snapshot_pull_request;
ALTER TABLE analyzed_file_snapshot
    ADD CONSTRAINT fk_snapshot_pull_request
    FOREIGN KEY (pull_request_id)
    REFERENCES pull_request (id)
    ON DELETE CASCADE;

-- 3. Index for fast lookup by PR
CREATE INDEX IF NOT EXISTS idx_snapshot_pull_request_id
    ON analyzed_file_snapshot (pull_request_id);

-- 4. Unique constraint: at most one snapshot per (PR, file_path)
--    Uses a partial index so NULL pull_request_id rows are excluded
CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshot_pr_filepath
    ON analyzed_file_snapshot (pull_request_id, file_path)
    WHERE pull_request_id IS NOT NULL;

-- 5. Make analysis_id nullable (PR-level snapshots may not reference a single analysis)
ALTER TABLE analyzed_file_snapshot
    ALTER COLUMN analysis_id DROP NOT NULL;

-- 6. Drop the old unique constraint that forced (analysis_id, file_path)
--    (Hibernate will recreate it as needed via ddl-auto:update, or we replace it
--     with a partial index that only applies when analysis_id IS NOT NULL)
ALTER TABLE analyzed_file_snapshot
    DROP CONSTRAINT IF EXISTS uq_analyzed_file_snapshot_analysis_path;

CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshot_analysis_filepath
    ON analyzed_file_snapshot (analysis_id, file_path)
    WHERE analysis_id IS NOT NULL;
