ALTER TABLE code_analysis ADD COLUMN qa_documentation_pending boolean NOT NULL DEFAULT false;
ALTER TABLE job ADD COLUMN input_analysis_id bigint REFERENCES code_analysis(id) ON DELETE SET NULL;
CREATE INDEX idx_code_analysis_qa_pending ON code_analysis(id) WHERE qa_documentation_pending;
CREATE UNIQUE INDEX idx_job_qa_input ON job(input_analysis_id)
    WHERE job_type = 'QA_DOC_COMMAND' AND trigger_source = 'CHAINED';
