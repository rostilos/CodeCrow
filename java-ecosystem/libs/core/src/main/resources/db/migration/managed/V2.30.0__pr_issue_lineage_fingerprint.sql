ALTER TABLE code_analysis_issue
    ADD COLUMN IF NOT EXISTS lineage_fingerprint VARCHAR(64);

COMMENT ON COLUMN code_analysis_issue.lineage_fingerprint IS
    'Category-independent SHA-256 receipt for verified PR issue lineage matching';

CREATE INDEX IF NOT EXISTS idx_code_analysis_issue_lineage_fingerprint
    ON code_analysis_issue (lineage_fingerprint)
    WHERE lineage_fingerprint IS NOT NULL;

ALTER TABLE code_analysis_issue
    DROP CONSTRAINT IF EXISTS chk_code_analysis_issue_lineage_fingerprint;

ALTER TABLE code_analysis_issue
    ADD CONSTRAINT chk_code_analysis_issue_lineage_fingerprint
    CHECK (lineage_fingerprint IS NULL OR lineage_fingerprint ~ '^[0-9a-f]{64}$');
