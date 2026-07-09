ALTER TABLE code_analysis
    ADD COLUMN IF NOT EXISTS task_id VARCHAR(128);

ALTER TABLE code_analysis
    ADD COLUMN IF NOT EXISTS task_summary VARCHAR(512);

CREATE INDEX IF NOT EXISTS idx_code_analysis_project_task_pr
    ON code_analysis (project_id, task_id, pr_number, pr_version DESC)
    WHERE task_id IS NOT NULL AND pr_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_qa_doc_document_project_task_generated
    ON qa_doc_document (project_id, task_id, generated_at DESC)
    WHERE task_id IS NOT NULL;

COMMENT ON COLUMN code_analysis.task_id IS
    'Task-management issue key associated with this PR analysis, used for bounded cross-PR task context.';

COMMENT ON COLUMN code_analysis.task_summary IS
    'Task-management summary captured at analysis time for historical task context.';
