-- Migration: VCS Integration Layer
-- Description: Adds connection_type, external workspace identifiers, and vcs_repo_binding table
-- Date: 2025-01-XX

-- =====================================================
-- Step 1: Add new columns to vcs_connection table
-- =====================================================

-- Add connection_type column to distinguish between OAuth manual and App-based connections
ALTER TABLE vcs_connection ADD COLUMN IF NOT EXISTS connection_type VARCHAR(32) DEFAULT 'OAUTH_MANUAL';

-- Add external workspace identifiers for app installations
ALTER TABLE vcs_connection ADD COLUMN IF NOT EXISTS external_workspace_id VARCHAR(128);
ALTER TABLE vcs_connection ADD COLUMN IF NOT EXISTS external_workspace_slug VARCHAR(128);

-- Add installation ID for app-based connections
ALTER TABLE vcs_connection ADD COLUMN IF NOT EXISTS installation_id VARCHAR(128);

-- Add display name for user-friendly connection naming
ALTER TABLE vcs_connection ADD COLUMN IF NOT EXISTS display_name VARCHAR(256);

-- Add scopes to track what permissions the connection has
ALTER TABLE vcs_connection ADD COLUMN IF NOT EXISTS scopes TEXT;

-- =====================================================
-- Step 2: Create index for external workspace lookups
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_vcs_connection_external_workspace 
    ON vcs_connection(external_workspace_id, provider);

-- =====================================================
-- Step 3: Create vcs_repo_binding table
-- =====================================================

CREATE TABLE IF NOT EXISTS vcs_repo_binding (
    id BIGSERIAL PRIMARY KEY,
    
    -- Reference to the CodeCrow project
    project_id BIGINT NOT NULL,
    
    -- Reference to the CodeCrow workspace (for multi-tenancy)
    workspace_id BIGINT NOT NULL,
    
    -- Reference to the VCS connection used
    vcs_connection_id BIGINT NOT NULL,
    
    -- Denormalized provider for faster filtering
    provider VARCHAR(32) NOT NULL,
    
    -- External identifiers from the VCS provider
    external_repo_id VARCHAR(128) NOT NULL,
    external_repo_slug VARCHAR(256) NOT NULL,
    external_namespace VARCHAR(256),
    
    -- Audit timestamps
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- Foreign key constraints
    CONSTRAINT fk_vcs_repo_binding_project 
        FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE,
    CONSTRAINT fk_vcs_repo_binding_workspace 
        FOREIGN KEY (workspace_id) REFERENCES workspace(id) ON DELETE CASCADE,
    CONSTRAINT fk_vcs_repo_binding_vcs_connection 
        FOREIGN KEY (vcs_connection_id) REFERENCES vcs_connection(id) ON DELETE CASCADE,
    
    -- Ensure unique binding per project (a project can only be bound to one repo)
    CONSTRAINT uq_vcs_repo_binding_project UNIQUE (project_id),
    
    -- Ensure unique external repo per workspace (a repo can only be bound once per workspace)
    CONSTRAINT uq_vcs_repo_binding_external_repo UNIQUE (workspace_id, provider, external_repo_id)
);

-- Create indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_vcs_repo_binding_workspace 
    ON vcs_repo_binding(workspace_id);

CREATE INDEX IF NOT EXISTS idx_vcs_repo_binding_provider_repo 
    ON vcs_repo_binding(provider, external_repo_id);

CREATE INDEX IF NOT EXISTS idx_vcs_repo_binding_connection 
    ON vcs_repo_binding(vcs_connection_id);

-- =====================================================
-- Step 4: Update existing vcs_connection records
-- =====================================================

-- Set connection_type to OAUTH_MANUAL for existing records (backward compatibility)
UPDATE vcs_connection 
SET connection_type = 'OAUTH_MANUAL' 
WHERE connection_type IS NULL;

-- Make connection_type NOT NULL after backfill
ALTER TABLE vcs_connection ALTER COLUMN connection_type SET NOT NULL;

-- =====================================================
-- Step 5: Create updated_at trigger for vcs_repo_binding
-- =====================================================

CREATE OR REPLACE FUNCTION update_vcs_repo_binding_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_vcs_repo_binding_updated_at ON vcs_repo_binding;

CREATE TRIGGER trigger_vcs_repo_binding_updated_at
    BEFORE UPDATE ON vcs_repo_binding
    FOR EACH ROW
    EXECUTE FUNCTION update_vcs_repo_binding_updated_at();
