-- Match ON DELETE SET NULL when a source review disappears during paid generation.
CREATE OR REPLACE FUNCTION retain_available_qa_analysis_reference()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.last_analysis_id IS NOT NULL THEN
        PERFORM 1 FROM code_analysis a
        WHERE a.id = NEW.last_analysis_id AND a.project_id = NEW.project_id
          AND (TG_TABLE_NAME <> 'qa_doc_document'
               OR a.pr_number = (to_jsonb(NEW)->>'pr_number')::bigint)
        FOR KEY SHARE SKIP LOCKED;
        -- Do not deadlock a review deletion that already waits on this QA row.
        IF NOT FOUND THEN
            RAISE WARNING 'QA source analysis % unavailable for project %; preserving QA content without that reference',
                NEW.last_analysis_id, NEW.project_id;
            NEW.last_analysis_id := NULL;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS retain_qa_document_analysis_reference ON qa_doc_document;
CREATE TRIGGER retain_qa_document_analysis_reference
    BEFORE INSERT OR UPDATE OF last_analysis_id, project_id, pr_number ON qa_doc_document
    FOR EACH ROW EXECUTE FUNCTION retain_available_qa_analysis_reference();

DROP TRIGGER IF EXISTS retain_qa_state_analysis_reference ON qa_doc_state;
CREATE TRIGGER retain_qa_state_analysis_reference
    BEFORE INSERT OR UPDATE OF last_analysis_id, project_id ON qa_doc_state
    FOR EACH ROW EXECUTE FUNCTION retain_available_qa_analysis_reference();
