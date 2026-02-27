-- ============================================================================
-- V1.5.4: Analyzed file content storage & PR deterministic issue tracking
-- ============================================================================
-- 1. Content-addressed file storage (analyzed_file_content + analyzed_file_snapshot)
--    for the source code viewer and deterministic issue tracking.
-- 2. PR issue tracking columns on code_analysis_issue for cross-iteration linking.
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. analyzed_file_content — deduplicated file content blobs (content-addressed)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analyzed_file_content (
    id              BIGSERIAL PRIMARY KEY,
    content_hash    VARCHAR(64) NOT NULL UNIQUE,  -- SHA-256 of the raw content
    content         TEXT        NOT NULL,          -- raw file content
    size_bytes      BIGINT      NOT NULL DEFAULT 0,
    line_count      INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fast lookup by content hash for dedup
CREATE INDEX IF NOT EXISTS idx_analyzed_file_content_hash
    ON analyzed_file_content (content_hash);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. analyzed_file_snapshot — per-analysis reference to file content
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analyzed_file_snapshot (
    id              BIGSERIAL PRIMARY KEY,
    analysis_id     BIGINT      NOT NULL REFERENCES code_analysis (id) ON DELETE CASCADE,
    file_path       VARCHAR(500) NOT NULL,         -- repo-relative file path
    content_id      BIGINT      NOT NULL REFERENCES analyzed_file_content (id) ON DELETE CASCADE,
    commit_hash     VARCHAR(40),                   -- commit at time of snapshot
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One snapshot per file per analysis
    CONSTRAINT uq_analyzed_file_snapshot_analysis_path
        UNIQUE (analysis_id, file_path)
);

-- Fast lookups by analysis
CREATE INDEX IF NOT EXISTS idx_analyzed_file_snapshot_analysis
    ON analyzed_file_snapshot (analysis_id);

-- Fast lookups by content (find all analyses that used this content)
CREATE INDEX IF NOT EXISTS idx_analyzed_file_snapshot_content
    ON analyzed_file_snapshot (content_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. PR issue tracking columns on code_analysis_issue
-- ─────────────────────────────────────────────────────────────────────────────

-- Self-referencing FK: links a new-version issue back to the previous-version issue it tracks
ALTER TABLE code_analysis_issue
    ADD COLUMN IF NOT EXISTS tracked_from_issue_id BIGINT;

ALTER TABLE code_analysis_issue
    DROP CONSTRAINT IF EXISTS fk_code_analysis_issue_tracked_from;
ALTER TABLE code_analysis_issue
    ADD CONSTRAINT fk_code_analysis_issue_tracked_from
    FOREIGN KEY (tracked_from_issue_id) REFERENCES code_analysis_issue (id)
    ON DELETE SET NULL;

-- Tracking confidence for the match (EXACT, SHIFTED, EDITED, WEAK, or null for first iteration)
ALTER TABLE code_analysis_issue
    ADD COLUMN IF NOT EXISTS tracking_confidence VARCHAR(10);

ALTER TABLE code_analysis_issue
    DROP CONSTRAINT IF EXISTS chk_code_analysis_issue_tracking_confidence;
ALTER TABLE code_analysis_issue
    ADD CONSTRAINT chk_code_analysis_issue_tracking_confidence
    CHECK (tracking_confidence IS NULL OR tracking_confidence IN ('EXACT', 'SHIFTED', 'EDITED', 'WEAK'));

-- Index for finding tracked chains
CREATE INDEX IF NOT EXISTS idx_code_analysis_issue_tracked_from
    ON code_analysis_issue (tracked_from_issue_id)
    WHERE tracked_from_issue_id IS NOT NULL;

-- Composite index for PR issue tracking queries (analysis + fingerprint)
CREATE INDEX IF NOT EXISTS idx_code_analysis_issue_analysis_fingerprint
    ON code_analysis_issue (analysis_id, issue_fingerprint)
    WHERE issue_fingerprint IS NOT NULL;
