-- Repository IDs are local to a VCS server/connection. Independent workspaces
-- may bind the same external repository without sharing authentication or jobs.
ALTER TABLE vcs_repo_binding DROP CONSTRAINT IF EXISTS uq_vcs_repo_binding_external;
DROP INDEX IF EXISTS uq_vcs_repo_binding_external;
ALTER TABLE vcs_repo_binding ADD CONSTRAINT uq_vcs_repo_binding_scoped_external
    UNIQUE (workspace_id, vcs_connection_id, external_repo_id);
