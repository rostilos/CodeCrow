-- Correlate the project-level RAG status with the durable job that currently
-- owns it. Recovery uses this identity to preserve a newer producer even
-- before that producer has registered its exact-generation operation.
ALTER TABLE rag_index_status
    ADD COLUMN IF NOT EXISTS active_job_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_rag_index_status_active_job
    ON rag_index_status(active_job_id);
