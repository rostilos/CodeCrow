-- V1.8.1: Fix analyzed_file_snapshot.analysis_id NOT NULL constraint
--
-- The original V1.5.4 migration created analysis_id as BIGINT NOT NULL,
-- but V1.5.5 (which drops the NOT NULL) was never applied because Flyway
-- only scans the 'managed/' directory.  Branch-level and PR-level snapshots
-- intentionally leave analysis_id NULL, so the column must be nullable.
--
-- This migration is idempotent — safe to run even if the constraint was
-- already removed by V1.5.5 or Hibernate.

-- 1. Make analysis_id nullable
ALTER TABLE analyzed_file_snapshot
    ALTER COLUMN analysis_id DROP NOT NULL;

-- 2. Drop the legacy unique constraint (analysis_id, file_path)
--    that was created in V1.5.4; it doesn't work for NULL analysis_id rows.
ALTER TABLE analyzed_file_snapshot
    DROP CONSTRAINT IF EXISTS uq_analyzed_file_snapshot_analysis_path;

-- 3. Replace with a partial unique index that only applies when analysis_id
--    IS NOT NULL, matching the V1.5.5 intent.
CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshot_analysis_filepath
    ON analyzed_file_snapshot (analysis_id, file_path)
    WHERE analysis_id IS NOT NULL;

-- 4. Ensure the PR-level partial unique index exists (from V1.5.5)
CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshot_pr_filepath
    ON analyzed_file_snapshot (pull_request_id, file_path)
    WHERE pull_request_id IS NOT NULL;

-- 5. Ensure the branch-level unique index exists
--    so upsert logic in persistSnapshotsForBranch works correctly.
CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshot_branch_filepath
    ON analyzed_file_snapshot (branch_id, file_path)
    WHERE branch_id IS NOT NULL;
