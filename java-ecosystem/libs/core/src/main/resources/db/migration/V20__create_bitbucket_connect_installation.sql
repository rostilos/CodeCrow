-- Migration: Create Bitbucket Connect App installation table
-- This table stores workspace-level installations of the CodeCrow Bitbucket Connect App

CREATE TABLE bitbucket_connect_installation (
    id BIGSERIAL PRIMARY KEY,
    
    -- Installation identity (from Bitbucket)
    client_key VARCHAR(255) NOT NULL UNIQUE,
    shared_secret VARCHAR(512) NOT NULL,
    
    -- Bitbucket workspace info
    bitbucket_workspace_uuid VARCHAR(255) NOT NULL,
    bitbucket_workspace_slug VARCHAR(255) NOT NULL,
    bitbucket_workspace_name VARCHAR(255),
    
    -- Who installed the app
    installed_by_uuid VARCHAR(255),
    installed_by_username VARCHAR(255),
    
    -- API access
    base_api_url VARCHAR(500) NOT NULL,
    public_key VARCHAR(2048),
    oauth_client_id VARCHAR(255),
    
    -- Tokens (encrypted)
    access_token VARCHAR(1024),
    refresh_token VARCHAR(1024),
    token_expires_at TIMESTAMP,
    
    -- CodeCrow linkage
    codecrow_workspace_id BIGINT REFERENCES workspace(id) ON DELETE SET NULL,
    vcs_connection_id BIGINT REFERENCES vcs_connection(id) ON DELETE SET NULL,
    
    -- Status
    enabled BOOLEAN NOT NULL DEFAULT true,
    product_type VARCHAR(50),
    
    -- Timestamps
    installed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP,
    
    CONSTRAINT fk_bci_codecrow_workspace FOREIGN KEY (codecrow_workspace_id) REFERENCES workspace(id),
    CONSTRAINT fk_bci_vcs_connection FOREIGN KEY (vcs_connection_id) REFERENCES vcs_connection(id)
);

-- Indexes for common queries
CREATE INDEX idx_bci_client_key ON bitbucket_connect_installation(client_key);
CREATE INDEX idx_bci_workspace_uuid ON bitbucket_connect_installation(bitbucket_workspace_uuid);
CREATE INDEX idx_bci_workspace_slug ON bitbucket_connect_installation(bitbucket_workspace_slug);
CREATE INDEX idx_bci_codecrow_workspace ON bitbucket_connect_installation(codecrow_workspace_id);
CREATE INDEX idx_bci_enabled ON bitbucket_connect_installation(enabled);

-- Add connection type for Connect App installations
-- The EVcsConnectionType enum should include CONNECT_APP
COMMENT ON TABLE bitbucket_connect_installation IS 'Stores Bitbucket Connect App installations for workspace-level integration';
