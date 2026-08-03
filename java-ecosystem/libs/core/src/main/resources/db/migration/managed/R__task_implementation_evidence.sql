CREATE TABLE IF NOT EXISTS task_implementation_evidence (
    id BIGSERIAL PRIMARY KEY,
    analysis_id BIGINT NOT NULL,
    project_id BIGINT NOT NULL,
    task_id VARCHAR(128) NOT NULL,
    pr_number BIGINT NOT NULL,
    commit_hash VARCHAR(64) NOT NULL,
    source VARCHAR(40) NOT NULL,
    evidence_ref VARCHAR(32) NOT NULL,
    file_path VARCHAR(2048) NOT NULL,
    hunk_id VARCHAR(160) NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    excerpt TEXT NOT NULL,
    full_evidence_complete BOOLEAN NOT NULL DEFAULT FALSE,
    content_fingerprint VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_task_implementation_evidence_analysis
        FOREIGN KEY (analysis_id) REFERENCES code_analysis (id)
        ON DELETE CASCADE,
    CONSTRAINT fk_task_implementation_evidence_project
        FOREIGN KEY (project_id) REFERENCES project (id)
        ON DELETE CASCADE,
    CONSTRAINT uq_task_implementation_evidence_analysis_fingerprint
        UNIQUE (analysis_id, content_fingerprint),
    CONSTRAINT ck_task_implementation_evidence_lines
        CHECK (line_start > 0 AND line_end >= line_start),
    CONSTRAINT ck_task_implementation_evidence_source
        CHECK (source = 'DETERMINISTIC_PR_LEDGER'),
    CONSTRAINT ck_task_implementation_evidence_fingerprint
        CHECK (content_fingerprint ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_task_implementation_evidence_task_history
    ON task_implementation_evidence (
        project_id,
        task_id,
        created_at DESC,
        id DESC
    );

COMMENT ON TABLE task_implementation_evidence IS
    'Bounded positive implementation evidence for task-aware review context; never comment content.';
