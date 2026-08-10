-- Deterministic commit evidence is reusable across promotions, while analysis
-- receipts remain scoped to the exact target branch context.

ALTER TABLE analyzed_commit
    ADD COLUMN IF NOT EXISTS source_branch VARCHAR(256),
    ADD COLUMN IF NOT EXISTS target_branch VARCHAR(256),
    ADD COLUMN IF NOT EXISTS target_base_revision VARCHAR(64);

ALTER TABLE analyzed_commit
    DROP CONSTRAINT IF EXISTS uq_analyzed_commit_project_hash;

CREATE UNIQUE INDEX IF NOT EXISTS uq_analyzed_commit_project_target_hash
    ON analyzed_commit(project_id, COALESCE(target_branch, ''), commit_hash);
CREATE INDEX IF NOT EXISTS idx_analyzed_commit_target
    ON analyzed_commit(project_id, target_branch, commit_hash);

CREATE TABLE scm_commit_evidence (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    commit_hash VARCHAR(64) NOT NULL,
    patch_id VARCHAR(64) NOT NULL,
    author_name VARCHAR(200),
    author_email VARCHAR(320),
    captured_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_scm_commit_evidence_project_hash
        UNIQUE (project_id, commit_hash),
    CONSTRAINT ck_scm_commit_patch_id CHECK (patch_id ~ '^[0-9a-f]{64}$')
);
CREATE INDEX idx_scm_commit_patch
    ON scm_commit_evidence(project_id, patch_id);

CREATE TABLE scm_added_line_evidence (
    id BIGSERIAL PRIMARY KEY,
    commit_evidence_id BIGINT NOT NULL
        REFERENCES scm_commit_evidence(id) ON DELETE CASCADE,
    project_id BIGINT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    file_path VARCHAR(1024) NOT NULL,
    new_line_number INTEGER NOT NULL,
    line_hash VARCHAR(64) NOT NULL,
    CONSTRAINT ck_scm_added_line_number CHECK (new_line_number > 0),
    CONSTRAINT ck_scm_added_line_hash CHECK (line_hash ~ '^[0-9a-f]{64}$')
);
CREATE INDEX idx_scm_added_line_lookup
    ON scm_added_line_evidence(project_id, file_path, line_hash);

CREATE TABLE scm_analysis_receipt (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    commit_evidence_id BIGINT NOT NULL
        REFERENCES scm_commit_evidence(id) ON DELETE CASCADE,
    source_branch VARCHAR(256),
    target_branch VARCHAR(256) NOT NULL,
    target_base_revision VARCHAR(64),
    analysis_id BIGINT,
    analysis_type VARCHAR(40) NOT NULL,
    context_key VARCHAR(64) NOT NULL,
    analyzed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_scm_analysis_receipt_context
        UNIQUE (project_id, commit_evidence_id, context_key),
    CONSTRAINT ck_scm_analysis_context_key
        CHECK (context_key ~ '^[0-9a-f]{64}$')
);
CREATE INDEX idx_scm_analysis_receipt_patch
    ON scm_analysis_receipt(project_id, commit_evidence_id, target_branch);

ALTER TABLE code_analysis_issue
    ADD COLUMN IF NOT EXISTS introducing_commit_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS introducing_author_name VARCHAR(200),
    ADD COLUMN IF NOT EXISTS introducing_author_email VARCHAR(320),
    ADD COLUMN IF NOT EXISTS author_provenance_confidence VARCHAR(32);

ALTER TABLE branch_issue
    ADD COLUMN IF NOT EXISTS introducing_commit_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS introducing_author_name VARCHAR(200),
    ADD COLUMN IF NOT EXISTS introducing_author_email VARCHAR(320),
    ADD COLUMN IF NOT EXISTS author_provenance_confidence VARCHAR(32);
