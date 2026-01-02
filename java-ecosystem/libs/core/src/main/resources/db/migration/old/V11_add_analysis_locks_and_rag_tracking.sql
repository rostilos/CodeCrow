CREATE TABLE IF NOT EXISTS analysis_lock (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL,
    branch_name VARCHAR(200) NOT NULL,
    analysis_type VARCHAR(50) NOT NULL,
    lock_key VARCHAR(500) NOT NULL UNIQUE,
    owner_instance_id VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    commit_hash VARCHAR(40),
    pr_number INTEGER,
    CONSTRAINT fk_analysis_lock_project FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE,
    CONSTRAINT uq_analysis_lock UNIQUE (project_id, branch_name, analysis_type)
);

CREATE INDEX idx_lock_expiry ON analysis_lock(expires_at);
CREATE INDEX idx_lock_project_branch ON analysis_lock(project_id, branch_name);

-- Create rag_index_status table for tracking RAG indexing state
CREATE TABLE IF NOT EXISTS rag_index_status (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL UNIQUE,
    workspace_name VARCHAR(200) NOT NULL,
    project_name VARCHAR(200) NOT NULL,
    status VARCHAR(50) NOT NULL,
    indexed_branch VARCHAR(200),
    indexed_commit_hash VARCHAR(40),
    total_files_indexed INTEGER,
    last_indexed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error_message TEXT,
    collection_name VARCHAR(300),
    CONSTRAINT fk_rag_index_project FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE,
    CONSTRAINT uq_rag_index_project UNIQUE (project_id)
);

CREATE INDEX idx_rag_status ON rag_index_status(status);
CREATE INDEX idx_rag_workspace_project ON rag_index_status(workspace_name, project_name);

-- Comments for documentation
COMMENT ON TABLE analysis_lock IS 'Manages distributed locks for concurrent analysis operations (PR, Branch, RAG)';
COMMENT ON TABLE rag_index_status IS 'Tracks RAG indexing status for projects to determine if full or incremental indexing is needed';

COMMENT ON COLUMN analysis_lock.analysis_type IS 'Type of analysis: PR_ANALYSIS, BRANCH_ANALYSIS, or RAG_INDEXING';
COMMENT ON COLUMN analysis_lock.expires_at IS 'Lock expiry time for auto-cleanup of stale locks';
COMMENT ON COLUMN analysis_lock.owner_instance_id IS 'Instance ID that acquired the lock for debugging';

COMMENT ON COLUMN rag_index_status.status IS 'Indexing status: NOT_INDEXED, INDEXING, INDEXED, UPDATING, or FAILED';
COMMENT ON COLUMN rag_index_status.collection_name IS 'Qdrant collection name for this project';

