-- RAG Delta Index table for hierarchical RAG system
-- Delta indexes store only branch-specific differences from base index (e.g., release/* vs master)

CREATE TABLE IF NOT EXISTS rag_delta_index (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    branch_name VARCHAR(256) NOT NULL,
    base_branch VARCHAR(256) NOT NULL,
    base_commit_hash VARCHAR(64),
    delta_commit_hash VARCHAR(64),
    collection_name VARCHAR(256) NOT NULL,
    chunk_count INTEGER,
    file_count INTEGER,
    status VARCHAR(32) NOT NULL DEFAULT 'CREATING',
    error_message VARCHAR(1000),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed_at TIMESTAMP WITH TIME ZONE,
    
    CONSTRAINT uk_rag_delta_project_branch UNIQUE (project_id, branch_name),
    CONSTRAINT chk_rag_delta_status CHECK (status IN ('CREATING', 'READY', 'STALE', 'ARCHIVED', 'FAILED'))
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_rag_delta_project ON rag_delta_index(project_id);
CREATE INDEX IF NOT EXISTS idx_rag_delta_status ON rag_delta_index(status);
CREATE INDEX IF NOT EXISTS idx_rag_delta_branch ON rag_delta_index(branch_name);
CREATE INDEX IF NOT EXISTS idx_rag_delta_base_branch ON rag_delta_index(project_id, base_branch);

-- Add comment for documentation
COMMENT ON TABLE rag_delta_index IS 'Stores RAG delta indexes for branch-specific context (e.g., release branches vs master)';
COMMENT ON COLUMN rag_delta_index.branch_name IS 'The branch this delta index is for (e.g., release/1.0)';
COMMENT ON COLUMN rag_delta_index.base_branch IS 'The base branch this delta is computed against (e.g., master)';
COMMENT ON COLUMN rag_delta_index.base_commit_hash IS 'Commit hash of base branch when delta was created';
COMMENT ON COLUMN rag_delta_index.collection_name IS 'Qdrant collection name for this delta index';
COMMENT ON COLUMN rag_delta_index.status IS 'CREATING=being built, READY=usable, STALE=needs rebuild, ARCHIVED=cleanup pending, FAILED=error occurred';
