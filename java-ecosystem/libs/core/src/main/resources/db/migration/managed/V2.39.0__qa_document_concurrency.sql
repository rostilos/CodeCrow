ALTER TABLE qa_doc_document ADD COLUMN row_version BIGINT NOT NULL DEFAULT 0;

-- Preserve older Hibernate enum checks while allowing the QA operation lease.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = CAST('analysis_lock' AS regclass)
               AND conname = 'analysis_lock_analysis_type_check') THEN
        ALTER TABLE analysis_lock DROP CONSTRAINT analysis_lock_analysis_type_check;
        ALTER TABLE analysis_lock ADD CONSTRAINT analysis_lock_analysis_type_check
            CHECK (analysis_type IN ('PR_ANALYSIS', 'BRANCH_ANALYSIS', 'RAG_INDEXING', 'QA_DOCUMENTATION'));
    END IF;
END $$;
