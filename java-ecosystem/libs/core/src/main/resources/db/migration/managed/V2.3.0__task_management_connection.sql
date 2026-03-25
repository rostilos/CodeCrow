-- =============================================================================
-- V2.3.0 — Task Management Integration (QA Auto-Documentation)
-- =============================================================================
-- Creates the task_management_connection table for storing workspace-level
-- connections to task management platforms (Jira Cloud, Jira Data Center, etc.).
--
-- The QA auto-doc configuration itself is stored as JSON inside the existing
-- project.configuration column (ProjectConfig.qaAutoDoc), so no project schema
-- changes are needed here.
-- =============================================================================

CREATE TABLE IF NOT EXISTS task_management_connection (
    id              BIGSERIAL       PRIMARY KEY,
    workspace_id    BIGINT          NOT NULL
                                    REFERENCES workspace(id) ON DELETE CASCADE,
    connection_name VARCHAR(256)    NOT NULL,
    provider_type   VARCHAR(64)     NOT NULL,
    status          VARCHAR(32)     NOT NULL DEFAULT 'PENDING',
    base_url        VARCHAR(512)    NOT NULL,
    credentials     JSONB           NOT NULL DEFAULT '{}'::jsonb,
    version         BIGINT          NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Each connection name must be unique within a workspace
    CONSTRAINT uq_tm_conn_workspace_name UNIQUE (workspace_id, connection_name),

    -- Provider type must be one of the known values
    CONSTRAINT chk_tm_provider_type CHECK (
        provider_type IN ('JIRA_CLOUD', 'JIRA_DATA_CENTER')
    ),

    -- Status must be one of the known values
    CONSTRAINT chk_tm_status CHECK (
        status IN ('PENDING', 'CONNECTED', 'ERROR', 'DISABLED')
    )
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_tm_connection_workspace
    ON task_management_connection(workspace_id);

CREATE INDEX IF NOT EXISTS idx_tm_connection_provider
    ON task_management_connection(workspace_id, provider_type);

COMMENT ON TABLE task_management_connection IS
    'Workspace-level connections to task management platforms (Jira, etc.) for QA auto-documentation and future task context integration.';

COMMENT ON COLUMN task_management_connection.credentials IS
    'Encrypted JSON containing platform-specific credentials. Jira Cloud: {"email":"...","apiToken":"..."}. Jira Data Center: {"personalAccessToken":"..."}.';
