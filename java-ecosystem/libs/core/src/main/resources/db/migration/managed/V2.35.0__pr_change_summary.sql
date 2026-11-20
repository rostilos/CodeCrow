CREATE TABLE pr_change_summary (
    analysis_id BIGINT PRIMARY KEY REFERENCES code_analysis(id) ON DELETE CASCADE,
    project_id BIGINT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    pr_number BIGINT NOT NULL,
    source_revision VARCHAR(64) NOT NULL,
    payload TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_pr_change_summary_revision
    ON pr_change_summary (project_id, pr_number, source_revision, created_at DESC);
