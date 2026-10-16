ALTER TABLE code_analysis_issue
    ADD COLUMN manual_status_resolved boolean,
    ADD COLUMN manual_status_updated_at timestamptz,
    ADD COLUMN row_version bigint NOT NULL DEFAULT 0;

-- Reopen actions previously erased their metadata and cannot be reconstructed.
UPDATE code_analysis_issue
SET manual_status_resolved = true,
    manual_status_updated_at = COALESCE(resolved_at, created_at)
WHERE is_resolved = true AND resolved_by = 'manual';
