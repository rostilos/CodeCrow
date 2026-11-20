ALTER TABLE rag_branch_index_generation
    DROP CONSTRAINT IF EXISTS ck_rag_branch_generation_status,
    DROP CONSTRAINT IF EXISTS rag_branch_index_generation_status_check;

ALTER TABLE rag_branch_index_generation
    ADD CONSTRAINT ck_rag_branch_generation_status CHECK (
        status IN ('BUILDING', 'ACTIVE', 'SUPERSEDED', 'RETIRING', 'RETIRED', 'FAILED')
    );
