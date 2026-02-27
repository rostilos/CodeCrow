-- Add analysis tracking columns to git_commit_node
ALTER TABLE git_commit_node
    ADD COLUMN IF NOT EXISTS analysis_status VARCHAR(20) NOT NULL DEFAULT 'NOT_ANALYZED',
    ADD COLUMN IF NOT EXISTS analyzed_at     TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS analysis_id     BIGINT;

-- FK to code_analysis (nullable — only set when ANALYZED)
ALTER TABLE git_commit_node
    ADD CONSTRAINT fk_commit_node_analysis
    FOREIGN KEY (analysis_id) REFERENCES code_analysis(id)
    ON DELETE SET NULL;

-- Index for fast lookup of unanalyzed commits
CREATE INDEX IF NOT EXISTS idx_git_commit_node_status
    ON git_commit_node(analysis_status);

-- Back-fill: mark existing commits that already have a matching code_analysis as ANALYZED.
-- This links commits to analyses via commit_hash + project_id.
UPDATE git_commit_node cn
SET    analysis_status = 'ANALYZED',
       analyzed_at     = ca.created_at,
       analysis_id     = ca.id
FROM   code_analysis ca
WHERE  cn.commit_hash = ca.commit_hash
  AND  cn.project_id  = ca.project_id
  AND  cn.analysis_status = 'NOT_ANALYZED';
