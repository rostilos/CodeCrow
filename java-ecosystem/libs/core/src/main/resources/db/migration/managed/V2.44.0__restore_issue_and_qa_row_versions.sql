-- Reused pre-release databases can have different migrations recorded at 2.37/2.39.
-- Initialize only missing versions; never reset an existing optimistic-lock counter.
ALTER TABLE code_analysis_issue ADD COLUMN IF NOT EXISTS row_version bigint NOT NULL DEFAULT 0;
UPDATE code_analysis_issue SET row_version = 0 WHERE row_version IS NULL;
ALTER TABLE code_analysis_issue
    ALTER COLUMN row_version SET DEFAULT 0,
    ALTER COLUMN row_version SET NOT NULL;

ALTER TABLE qa_doc_document ADD COLUMN IF NOT EXISTS row_version bigint NOT NULL DEFAULT 0;
UPDATE qa_doc_document SET row_version = 0 WHERE row_version IS NULL;
ALTER TABLE qa_doc_document
    ALTER COLUMN row_version SET DEFAULT 0,
    ALTER COLUMN row_version SET NOT NULL;
