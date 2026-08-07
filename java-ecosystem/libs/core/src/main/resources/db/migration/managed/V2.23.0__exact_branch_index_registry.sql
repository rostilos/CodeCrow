-- Establish durable ownership and immutable generation state for exact branch indexes.
-- The early multi-branch migration dropped rag_delta_index but did not create its
-- replacement, so this forward-only migration safely handles both fresh and
-- Hibernate-created installations.

CREATE TABLE IF NOT EXISTS rag_branch_index (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    branch_name VARCHAR(256) NOT NULL,
    commit_hash VARCHAR(64),
    chunk_count INTEGER,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_rag_branch_index_project_branch UNIQUE (project_id, branch_name)
);

ALTER TABLE rag_branch_index
    ADD COLUMN IF NOT EXISTS index_kind VARCHAR(24) NOT NULL DEFAULT 'LEGACY',
    ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(24) NOT NULL DEFAULT 'READY',
    ADD COLUMN IF NOT EXISTS desired_commit_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS active_generation_id BIGINT,
    ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS error_message TEXT;

CREATE INDEX IF NOT EXISTS idx_rag_branch_project
    ON rag_branch_index(project_id);
CREATE INDEX IF NOT EXISTS idx_rag_branch_name
    ON rag_branch_index(branch_name);
CREATE INDEX IF NOT EXISTS idx_rag_branch_lifecycle
    ON rag_branch_index(project_id, lifecycle_status);

CREATE TABLE IF NOT EXISTS rag_branch_deleted_files (
    branch_index_id BIGINT NOT NULL REFERENCES rag_branch_index(id) ON DELETE CASCADE,
    file_path VARCHAR(512) NOT NULL,
    CONSTRAINT uq_rag_branch_deleted_file UNIQUE (branch_index_id, file_path)
);

CREATE TABLE IF NOT EXISTS rag_branch_index_generation (
    id BIGSERIAL PRIMARY KEY,
    branch_index_id BIGINT NOT NULL REFERENCES rag_branch_index(id) ON DELETE CASCADE,
    revision VARCHAR(64) NOT NULL,
    parent_generation_id BIGINT REFERENCES rag_branch_index_generation(id) ON DELETE SET NULL,
    seed_revision VARCHAR(64),
    collection_name VARCHAR(300) NOT NULL,
    status VARCHAR(24) NOT NULL,
    manifest_digest VARCHAR(128),
    representation_fingerprint VARCHAR(128),
    file_count INTEGER,
    chunk_count INTEGER,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TIMESTAMP WITH TIME ZONE,
    superseded_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    CONSTRAINT uq_rag_branch_generation_collection UNIQUE (branch_index_id, collection_name),
    CONSTRAINT ck_rag_branch_generation_status
        CHECK (status IN ('BUILDING', 'ACTIVE', 'SUPERSEDED', 'FAILED')),
    CONSTRAINT ck_rag_branch_generation_counts
        CHECK ((file_count IS NULL OR file_count >= 0) AND (chunk_count IS NULL OR chunk_count >= 0))
);

CREATE INDEX IF NOT EXISTS idx_rag_branch_generation_revision
    ON rag_branch_index_generation(branch_index_id, revision);
CREATE INDEX IF NOT EXISTS idx_rag_branch_generation_status
    ON rag_branch_index_generation(branch_index_id, status);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_rag_branch_active_generation'
          AND conrelid = 'rag_branch_index'::regclass
    ) THEN
        ALTER TABLE rag_branch_index
            ADD CONSTRAINT fk_rag_branch_active_generation
            FOREIGN KEY (active_generation_id)
            REFERENCES rag_branch_index_generation(id)
            ON DELETE SET NULL;
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS rag_index_operation (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    branch_name VARCHAR(256) NOT NULL,
    from_revision VARCHAR(64),
    to_revision VARCHAR(64) NOT NULL,
    operation_key VARCHAR(128) NOT NULL,
    status VARCHAR(24) NOT NULL,
    generation_id BIGINT REFERENCES rag_branch_index_generation(id) ON DELETE SET NULL,
    job_id BIGINT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    CONSTRAINT uq_rag_index_operation_key UNIQUE (project_id, operation_key),
    CONSTRAINT ck_rag_index_operation_status
        CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    CONSTRAINT ck_rag_index_operation_attempts CHECK (attempt_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_rag_index_operation_recovery
    ON rag_index_operation(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_rag_index_operation_branch
    ON rag_index_operation(project_id, branch_name, to_revision);

-- Existing rows represent the shared-collection implementation. Only the row
-- matching the authoritative RagIndexStatus branch can be identified as primary;
-- every other legacy row remains unverified until rebuilt as an exact generation.
UPDATE rag_branch_index branch_index
SET index_kind = CASE
        WHEN EXISTS (
            SELECT 1
            FROM rag_index_status status
            WHERE status.project_id = branch_index.project_id
              AND status.indexed_branch = branch_index.branch_name
        ) THEN 'PRIMARY'
        ELSE 'LEGACY'
    END,
    lifecycle_status = CASE
        WHEN branch_index.commit_hash IS NULL THEN 'PENDING'
        ELSE 'READY'
    END
WHERE active_generation_id IS NULL;

