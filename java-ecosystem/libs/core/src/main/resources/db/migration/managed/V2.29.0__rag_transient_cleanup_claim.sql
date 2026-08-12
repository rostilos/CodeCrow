-- Durable claim for transient-generation cleanup. Readers and rebuilds reject
-- a claimed row while remote physical collections are being removed.
ALTER TABLE rag_branch_index
    ADD COLUMN IF NOT EXISTS cleanup_claim_token VARCHAR(64),
    ADD COLUMN IF NOT EXISTS cleanup_claimed_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX IF NOT EXISTS idx_rag_branch_cleanup_claim
    ON rag_branch_index(index_kind, cleanup_claimed_at)
    WHERE cleanup_claim_token IS NOT NULL;
