-- =============================================================================
-- V2.6.0 — QA Documentation State Tracking (server-side)
--
-- Replaces the insecure Jira-comment-marker–based state tracking with a
-- server-side table that stores the last-analyzed commit hash, analysis
-- reference, and the set of documented PR numbers per (project, task).
-- =============================================================================

CREATE TABLE IF NOT EXISTS qa_doc_state (
    id                      BIGSERIAL       PRIMARY KEY,
    project_id              BIGINT          NOT NULL
                                            REFERENCES project(id) ON DELETE CASCADE,
    task_id                 VARCHAR(128)    NOT NULL,
    last_commit_hash        VARCHAR(40),
    last_analysis_id        BIGINT          REFERENCES code_analysis(id) ON DELETE SET NULL,
    documented_pr_numbers   VARCHAR(4096)   NOT NULL DEFAULT '',
    last_generated_at       TIMESTAMPTZ,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_qa_doc_state_project_task
        UNIQUE (project_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_qa_doc_state_project
    ON qa_doc_state(project_id);

CREATE INDEX IF NOT EXISTS idx_qa_doc_state_task
    ON qa_doc_state(project_id, task_id);

COMMENT ON TABLE qa_doc_state IS
    'Tracks QA auto-documentation generation state per (project, Jira task). '
    'Stores the last analyzed commit hash and documented PR numbers to enable '
    'secure delta-diff computation without relying on Jira comment markers.';

COMMENT ON COLUMN qa_doc_state.task_id IS
    'The task-management issue key (e.g. PROJ-123). Scoped to a project.';

COMMENT ON COLUMN qa_doc_state.documented_pr_numbers IS
    'Comma-separated list of PR numbers already documented for this task. '
    'Example: "42,57,63". Empty string means no PRs documented yet.';
