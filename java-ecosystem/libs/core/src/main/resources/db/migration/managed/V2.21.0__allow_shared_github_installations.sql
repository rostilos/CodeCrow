-- A GitHub App is installed once per GitHub account, while separate CodeCrow
-- workspaces may connect different repositories from that account. Keep the
-- installation ID indexed, but do not enforce one local connection globally.
DROP INDEX IF EXISTS uq_vcs_connection_github_installation;
DROP INDEX IF EXISTS uq_vcs_connection_new_verified_github_installation;

CREATE INDEX IF NOT EXISTS idx_vcs_connection_github_installation
    ON vcs_connection (installation_id)
    WHERE provider_type = 'GITHUB' AND installation_id IS NOT NULL;
