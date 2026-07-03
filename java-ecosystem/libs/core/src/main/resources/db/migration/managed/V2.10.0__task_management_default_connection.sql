-- =============================================================================
-- V2.10.0 — Workspace default task-management connection
-- =============================================================================

ALTER TABLE task_management_connection
    ADD COLUMN IF NOT EXISTS default_connection BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN task_management_connection.default_connection IS
    'Marks the workspace-level task-management connection that should be preselected as the default for project task-management binding.';
