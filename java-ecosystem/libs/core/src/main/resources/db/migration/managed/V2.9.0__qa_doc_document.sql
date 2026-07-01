-- =============================================================================
-- V2.9.0 — Latest QA Documentation per Pull Request
--
-- Stores rendered QA documentation markdown in CodeCrow instead of relying only
-- on task-management comments. One row is kept per (project, PR number).
-- =============================================================================

CREATE TABLE IF NOT EXISTS qa_doc_document (
    id                 BIGSERIAL       PRIMARY KEY,
    project_id         BIGINT          NOT NULL
                                      REFERENCES project(id) ON DELETE CASCADE,
    pr_number          BIGINT          NOT NULL,
    task_id            VARCHAR(128),
    last_analysis_id   BIGINT          REFERENCES code_analysis(id) ON DELETE SET NULL,
    commit_hash        VARCHAR(40),
    markdown_content   TEXT            NOT NULL,
    generated_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_qa_doc_document_project_pr
        UNIQUE (project_id, pr_number)
);

CREATE INDEX IF NOT EXISTS idx_qa_doc_document_project
    ON qa_doc_document(project_id);

CREATE INDEX IF NOT EXISTS idx_qa_doc_document_project_pr
    ON qa_doc_document(project_id, pr_number);

COMMENT ON TABLE qa_doc_document IS
    'Stores the latest generated QA documentation markdown per project pull request.';

COMMENT ON COLUMN qa_doc_document.markdown_content IS
    'Rendered markdown QA documentation shown in the CodeCrow frontend.';
