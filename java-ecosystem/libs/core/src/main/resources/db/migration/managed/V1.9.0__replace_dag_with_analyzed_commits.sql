-- V1.9.0: Replace git DAG tables with lightweight analyzed_commit tracking
--
-- The old DAG system (git_commit_node + git_commit_edge) tried to reconstruct
-- a full git graph in the database from ~100 commits fetched via VCS API.
-- This was fundamentally broken: merge commits pulled in commits from outside
-- that window, causing incomplete/wrong graphs and phantom re-analysis.
--
-- The new approach simply tracks which commits have been analyzed, using set
-- subtraction: commits_in_push - already_analyzed = commits_needing_analysis.
-- The actual git graph is fetched on-demand from the VCS API when needed.

-- ═══════════════════════════════════════════════════════════════════════
-- 1. Create the new analyzed_commit table
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS analyzed_commit (
    id              BIGSERIAL       PRIMARY KEY,
    project_id      BIGINT          NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    commit_hash     VARCHAR(40)     NOT NULL,
    analyzed_at     TIMESTAMPTZ     NOT NULL DEFAULT now(),
    analysis_id     BIGINT,
    analysis_type   VARCHAR(30)
);

-- Unique constraint: each commit is recorded once per project
ALTER TABLE analyzed_commit
    ADD CONSTRAINT uq_analyzed_commit_project_hash
    UNIQUE (project_id, commit_hash);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_analyzed_commit_project ON analyzed_commit(project_id);
CREATE INDEX IF NOT EXISTS idx_analyzed_commit_hash    ON analyzed_commit(commit_hash);

-- ═══════════════════════════════════════════════════════════════════════
-- 2. Migrate existing data from git_commit_node where analysis_status = 'ANALYZED'
-- ═══════════════════════════════════════════════════════════════════════

INSERT INTO analyzed_commit (project_id, commit_hash, analyzed_at, analysis_id, analysis_type)
SELECT cn.project_id,
       cn.commit_hash,
       COALESCE(cn.analyzed_at, now()),
       cn.analysis_id,
       CASE
           WHEN cn.analysis_id IS NOT NULL THEN 'BRANCH_ANALYSIS'
           ELSE 'BRANCH_ANALYSIS'
       END
FROM   git_commit_node cn
WHERE  cn.analysis_status = 'ANALYZED'
ON CONFLICT (project_id, commit_hash) DO NOTHING;

-- ═══════════════════════════════════════════════════════════════════════
-- 3. Add last_known_head_commit to branch table
-- ═══════════════════════════════════════════════════════════════════════

ALTER TABLE branch
    ADD COLUMN IF NOT EXISTS last_known_head_commit VARCHAR(40);

-- Initialize from last_successful_commit_hash for existing healthy branches
UPDATE branch
SET    last_known_head_commit = last_successful_commit_hash
WHERE  last_successful_commit_hash IS NOT NULL
  AND  last_known_head_commit IS NULL;

-- ═══════════════════════════════════════════════════════════════════════
-- 4. Drop old DAG tables (edge table first due to FK references)
-- ═══════════════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS git_commit_edge CASCADE;
DROP TABLE IF EXISTS git_commit_node CASCADE;
