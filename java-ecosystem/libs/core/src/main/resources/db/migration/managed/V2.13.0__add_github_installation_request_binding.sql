-- Request-binding metadata is populated only for new GitHub App installation
-- requests. Existing connections, installation IDs, and tokens are untouched.
ALTER TABLE vcs_connection
    ADD COLUMN IF NOT EXISTS github_installation_request_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS github_installation_requester_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS github_installation_request_snapshot TEXT,
    ADD COLUMN IF NOT EXISTS github_installation_request_started_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS github_binding_verified_at TIMESTAMP;

-- Existing rows remain outside this index (their verified timestamp is NULL).
-- Newly verified flows get database-level protection against concurrent claims.
CREATE UNIQUE INDEX IF NOT EXISTS uq_vcs_connection_new_verified_github_installation
    ON vcs_connection (installation_id)
    WHERE github_binding_verified_at IS NOT NULL;
