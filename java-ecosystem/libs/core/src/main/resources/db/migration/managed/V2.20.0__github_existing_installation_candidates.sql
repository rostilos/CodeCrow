-- Short-lived candidate IDs used to reconcile a GitHub App that remains
-- installed after its former CodeCrow connection was removed.
ALTER TABLE vcs_connection
    ADD COLUMN IF NOT EXISTS github_installation_candidates TEXT;
