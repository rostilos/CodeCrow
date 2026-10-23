ALTER TABLE qa_doc_document
    ADD COLUMN environment_checks JSONB NOT NULL
    DEFAULT '{"develop": [], "stage": [], "production": []}'::jsonb;
