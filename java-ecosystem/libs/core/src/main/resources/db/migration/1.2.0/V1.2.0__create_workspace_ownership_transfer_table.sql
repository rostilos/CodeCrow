-- Migration: Create workspace ownership transfer table
-- Version: 1.3.0
-- Description: Table to track pending workspace ownership transfers with 24h undo period

CREATE TABLE IF NOT EXISTS workspace_ownership_transfer (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    from_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    to_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'COMPLETED', 'CANCELLED', 'EXPIRED')),
    initiated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    cancelled_at TIMESTAMP WITH TIME ZONE,
    cancellation_reason TEXT,
    two_factor_verified BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT unique_pending_transfer UNIQUE (workspace_id, status) 
        DEFERRABLE INITIALLY DEFERRED
);

-- Index for finding pending transfers
CREATE INDEX idx_ownership_transfer_workspace_status 
ON workspace_ownership_transfer(workspace_id, status) 
WHERE status = 'PENDING';

-- Index for expiry checks
CREATE INDEX idx_ownership_transfer_expires 
ON workspace_ownership_transfer(expires_at) 
WHERE status = 'PENDING';

-- Comments for documentation
COMMENT ON TABLE workspace_ownership_transfer IS 'Tracks workspace ownership transfer requests with 24h undo period';
COMMENT ON COLUMN workspace_ownership_transfer.status IS 'PENDING: awaiting completion, COMPLETED: transfer done, CANCELLED: owner cancelled, EXPIRED: 24h passed';
COMMENT ON COLUMN workspace_ownership_transfer.two_factor_verified IS 'True if current owner verified 2FA before initiating transfer';
