-- V1.6.0: Branch issue independence
--
-- Promotes branch_issue from a thin join table (referencing code_analysis_issue for data)
-- to a fully independent entity with its own copies of all issue fields.
-- This ensures:
--   1. PR issues remain IMMUTABLE historical records
--   2. Branch issues carry their own data that can be reconciled independently
--   3. No mutation of code_analysis_issue during branch reconciliation

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 1: Add new data columns to branch_issue
-- ═══════════════════════════════════════════════════════════════════════

ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS file_path VARCHAR(500);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS line_number INTEGER;
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS reason TEXT;
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS title VARCHAR(500);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS suggested_fix_description TEXT;
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS suggested_fix_diff TEXT;
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS issue_category VARCHAR(50);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS vcs_author_id VARCHAR(255);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS vcs_author_username VARCHAR(255);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS line_hash VARCHAR(64);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS line_hash_context VARCHAR(64);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS issue_fingerprint VARCHAR(64);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS content_fingerprint VARCHAR(64);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS code_snippet TEXT;

-- Provenance fields (where was this issue originally detected?)
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS origin_analysis_id BIGINT;
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS origin_pr_number BIGINT;
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS origin_commit_hash VARCHAR(40);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS origin_branch_name VARCHAR(500);

-- Branch-local tracking fields
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS current_line_number INTEGER;
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS current_line_hash VARCHAR(64);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS last_verified_commit VARCHAR(40);
ALTER TABLE branch_issue ADD COLUMN IF NOT EXISTS tracking_confidence VARCHAR(30);

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 2: Handle the FK column transition
--         If Hibernate ddl-auto:update ran first, both code_analysis_issue_id
--         and origin_issue_id columns exist.  If this migration runs first
--         on a fresh DB, only code_analysis_issue_id exists and needs renaming.
-- ═══════════════════════════════════════════════════════════════════════

-- Drop the old unique constraint (try both Hibernate-generated and named variants)
ALTER TABLE branch_issue DROP CONSTRAINT IF EXISTS uq_branch_issue;
ALTER TABLE branch_issue DROP CONSTRAINT IF EXISTS uq_branch_issue_code_analysis_issue;

-- Drop old FK constraints on code_analysis_issue_id (Hibernate auto-named)
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'branch_issue'::regclass
          AND confrelid = 'code_analysis_issue'::regclass
          AND conname NOT LIKE 'fk_branch_issue_origin%'
    ) LOOP
        EXECUTE format('ALTER TABLE branch_issue DROP CONSTRAINT IF EXISTS %I', r.conname);
    END LOOP;
END $$;

-- If only code_analysis_issue_id exists (no origin_issue_id), rename it
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'branch_issue' AND column_name = 'code_analysis_issue_id'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'branch_issue' AND column_name = 'origin_issue_id'
    ) THEN
        ALTER TABLE branch_issue RENAME COLUMN code_analysis_issue_id TO origin_issue_id;
    END IF;
END $$;

-- If both columns exist (Hibernate created origin_issue_id alongside old column),
-- copy data from old to new where new is null, then drop old column
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'branch_issue' AND column_name = 'code_analysis_issue_id'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'branch_issue' AND column_name = 'origin_issue_id'
    ) THEN
        UPDATE branch_issue SET origin_issue_id = code_analysis_issue_id WHERE origin_issue_id IS NULL;
        ALTER TABLE branch_issue ALTER COLUMN code_analysis_issue_id DROP NOT NULL;
    END IF;
END $$;

-- Make origin_issue_id nullable (it's now a provenance reference, not required)
DO $$
BEGIN
    ALTER TABLE branch_issue ALTER COLUMN origin_issue_id DROP NOT NULL;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- Re-add FK with deterministic name
ALTER TABLE branch_issue
    DROP CONSTRAINT IF EXISTS fk_branch_issue_origin;
ALTER TABLE branch_issue
    ADD CONSTRAINT fk_branch_issue_origin
    FOREIGN KEY (origin_issue_id)
    REFERENCES code_analysis_issue (id)
    ON DELETE SET NULL;

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 3: Backfill existing branch_issue rows from their origin CAI
-- ═══════════════════════════════════════════════════════════════════════

UPDATE branch_issue bi
SET
    file_path = COALESCE(bi.file_path, cai.file_path),
    line_number = COALESCE(bi.line_number, cai.line_number),
    reason = COALESCE(bi.reason, cai.reason),
    title = COALESCE(bi.title, cai.title),
    suggested_fix_description = COALESCE(bi.suggested_fix_description, cai.suggested_fix_description),
    suggested_fix_diff = COALESCE(bi.suggested_fix_diff, cai.suggested_fix_diff),
    issue_category = COALESCE(bi.issue_category, cai.issue_category),
    vcs_author_id = COALESCE(bi.vcs_author_id, cai.vcs_author_id),
    vcs_author_username = COALESCE(bi.vcs_author_username, cai.vcs_author_username),
    line_hash = COALESCE(bi.line_hash, cai.line_hash),
    line_hash_context = COALESCE(bi.line_hash_context, cai.line_hash_context),
    issue_fingerprint = COALESCE(bi.issue_fingerprint, cai.issue_fingerprint),
    content_fingerprint = COALESCE(bi.content_fingerprint, cai.content_fingerprint),
    code_snippet = COALESCE(bi.code_snippet, cai.code_snippet),
    origin_analysis_id = COALESCE(bi.origin_analysis_id, cai.analysis_id),
    origin_pr_number = COALESCE(bi.origin_pr_number, a.pr_number),
    origin_commit_hash = COALESCE(bi.origin_commit_hash, a.commit_hash),
    origin_branch_name = COALESCE(bi.origin_branch_name, a.source_branch_name, a.target_branch_name),
    current_line_number = COALESCE(bi.current_line_number, cai.line_number),
    current_line_hash = COALESCE(bi.current_line_hash, cai.line_hash)
FROM code_analysis_issue cai
LEFT JOIN code_analysis a ON cai.analysis_id = a.id
WHERE bi.origin_issue_id = cai.id
  AND bi.file_path IS NULL;  -- only backfill rows that haven't been populated yet

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 4: Add the new unique constraint (branch_id, content_fingerprint)
-- ═══════════════════════════════════════════════════════════════════════

-- This replaces the old (branch_id, code_analysis_issue_id) constraint.
-- Only applied where content_fingerprint is not null (pre-tracking issues may lack it).
CREATE UNIQUE INDEX IF NOT EXISTS uq_branch_issue_content_fp
    ON branch_issue (branch_id, content_fingerprint)
    WHERE content_fingerprint IS NOT NULL;

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 5: Add branch_id FK to analyzed_file_snapshot
-- ═══════════════════════════════════════════════════════════════════════

ALTER TABLE analyzed_file_snapshot
    ADD COLUMN IF NOT EXISTS branch_id BIGINT;

ALTER TABLE analyzed_file_snapshot
    DROP CONSTRAINT IF EXISTS fk_snapshot_branch;
ALTER TABLE analyzed_file_snapshot
    ADD CONSTRAINT fk_snapshot_branch
    FOREIGN KEY (branch_id)
    REFERENCES branch (id)
    ON DELETE CASCADE;

-- Index for branch-keyed lookups
CREATE INDEX IF NOT EXISTS idx_snapshot_branch_id ON analyzed_file_snapshot (branch_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_branch_file ON analyzed_file_snapshot (branch_id, file_path);

-- ═══════════════════════════════════════════════════════════════════════
-- STEP 6: Performance indexes on branch_issue
-- ═══════════════════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_branch_issue_file_path ON branch_issue (branch_id, file_path);
CREATE INDEX IF NOT EXISTS idx_branch_issue_fingerprint ON branch_issue (branch_id, issue_fingerprint);
CREATE INDEX IF NOT EXISTS idx_branch_issue_origin ON branch_issue (origin_issue_id);
CREATE INDEX IF NOT EXISTS idx_branch_issue_severity ON branch_issue (branch_id, severity) WHERE NOT is_resolved;
