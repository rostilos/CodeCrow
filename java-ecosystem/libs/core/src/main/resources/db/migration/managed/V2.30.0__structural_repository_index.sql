DELETE FROM site_settings
WHERE config_group = 'EMBEDDING';

ALTER TABLE job
    DROP CONSTRAINT IF EXISTS job_job_type_check;

UPDATE job
SET job_type = 'REPOSITORY_INDEX_BUILD'
WHERE job_type IN ('RAG_INITIAL_INDEX', 'RAG_INCREMENTAL_INDEX');

ALTER TABLE job
    ADD CONSTRAINT job_job_type_check CHECK (
        (job_type)::text = ANY (ARRAY[
            'PR_ANALYSIS',
            'BRANCH_ANALYSIS',
            'BRANCH_RECONCILIATION',
            'REPOSITORY_INDEX_BUILD',
            'MANUAL_ANALYSIS',
            'REPO_SYNC',
            'SUMMARIZE_COMMAND',
            'ASK_COMMAND',
            'ANALYZE_COMMAND',
            'REVIEW_COMMAND',
            'IGNORED_COMMENT',
            'QA_DOC_COMMAND'
        ]::text[])
    );

-- Every generation published before this deployment uses a different physical
-- collection schema. Detach it from the branch head so the normal next access
-- performs a complete structural build at the same revision instead of treating
-- the database checkpoint alone as proof that the index is readable.
UPDATE rag_branch_index
SET active_generation_id = NULL
WHERE active_generation_id IS NOT NULL;

UPDATE rag_branch_index_generation
SET status = 'SUPERSEDED',
    superseded_at = COALESCE(superseded_at, CURRENT_TIMESTAMP)
WHERE status = 'ACTIVE';

-- Preserve branch registry identities while removing the shared-collection
-- kind from the current model. Their old physical generations were detached
-- above, so these rows now behave like any other durable branch and the next
-- access performs a complete immutable build. A primary build promotes its
-- existing row through the registry service.
UPDATE rag_branch_index
SET index_kind = 'DURABLE'
WHERE index_kind = 'LEGACY';

ALTER TABLE rag_branch_index
    ALTER COLUMN index_kind DROP DEFAULT;

ALTER TABLE rag_branch_index
    DROP CONSTRAINT IF EXISTS ck_rag_branch_index_kind;

ALTER TABLE rag_branch_index
    ADD CONSTRAINT ck_rag_branch_index_kind CHECK (
        index_kind IN ('PRIMARY', 'DURABLE', 'TRANSIENT')
    );

DROP TABLE IF EXISTS rag_branch_deleted_files;

ALTER TABLE rag_branch_index
    DROP COLUMN IF EXISTS commit_hash,
    DROP COLUMN IF EXISTS chunk_count;

-- Projects that still use the single-branch collection path have no immutable
-- generation pointer to detach. Remove their old-schema checkpoint as well so
-- the ordinary readiness path performs a full structural rebuild at the same
-- repository revision. Keeping last_indexed_at would make FAILED rows look
-- readable, so the whole checkpoint is deliberately cleared.
UPDATE rag_index_status
SET status = 'NOT_INDEXED',
    indexed_branch = NULL,
    indexed_commit_hash = NULL,
    total_files_indexed = NULL,
    last_indexed_at = NULL,
    updated_at = CURRENT_TIMESTAMP,
    error_message = NULL,
    chunk_count = NULL,
    active_job_id = NULL;

ALTER TABLE rag_index_status
    DROP COLUMN IF EXISTS collection_name,
    DROP COLUMN IF EXISTS failed_incremental_count;

ALTER TABLE site_settings
    DROP CONSTRAINT IF EXISTS site_settings_config_group_check;

ALTER TABLE site_settings
    ADD CONSTRAINT site_settings_config_group_check CHECK (
        (config_group)::text = ANY (ARRAY[
            'VCS_BITBUCKET',
            'VCS_BITBUCKET_CONNECT',
            'VCS_GITHUB',
            'VCS_GITLAB',
            'LLM_SYNC',
            'SMTP',
            'GOOGLE_OAUTH',
            'BASE_URLS'
        ]::text[])
    );
