-- Persist the exact analysis-lock owner used by a RAG generation attempt.
-- Recovery releases only this key, so a same-revision retry cannot lose its
-- newly acquired lock to a delayed recovery pass.
ALTER TABLE rag_index_operation
    ADD COLUMN IF NOT EXISTS analysis_lock_key VARCHAR(500);
