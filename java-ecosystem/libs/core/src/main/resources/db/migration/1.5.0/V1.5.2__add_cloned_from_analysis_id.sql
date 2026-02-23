-- Add clone lineage tracking column to code_analysis table.
-- When an analysis is produced from cache (fingerprint or commit-hash match),
-- this column records the source analysis ID for traceability.
ALTER TABLE code_analysis
    ADD COLUMN cloned_from_analysis_id BIGINT DEFAULT NULL;

-- Index for querying clone chains (e.g. "find all clones of analysis X")
CREATE INDEX idx_code_analysis_cloned_from ON code_analysis (cloned_from_analysis_id)
    WHERE cloned_from_analysis_id IS NOT NULL;
