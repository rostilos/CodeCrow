ALTER TABLE rag_branch_index
    ADD COLUMN IF NOT EXISTS last_failed_at TIMESTAMP WITH TIME ZONE;

-- Preserve a conservative retry anchor for failures recorded before this
-- dedicated timestamp existed. Ordinary branch reads must not move it.
UPDATE rag_branch_index
SET last_failed_at = updated_at
WHERE error_message IS NOT NULL
  AND last_failed_at IS NULL;

-- Older accepted jobs predate the explicit capacity-wait state. Keep their
-- existing log history and make their durable backlog state visible in-place.
UPDATE job
SET current_step = 'Waiting for repository-index capacity'
WHERE job_type = 'REPOSITORY_INDEX_BUILD'
  AND status = 'PENDING'
  AND (current_step IS NULL OR BTRIM(current_step) = '');
