CREATE TABLE review_execution (
    id VARCHAR(160) PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    project_id BIGINT NOT NULL,
    repository_id TEXT NOT NULL,
    pull_request_id BIGINT NOT NULL,
    base_sha VARCHAR(64) NOT NULL,
    head_sha VARCHAR(64) NOT NULL,
    merge_base_sha VARCHAR(64) NOT NULL,
    diff_artifact_id VARCHAR(160) NOT NULL,
    diff_digest VARCHAR(64) NOT NULL,
    diff_byte_length BIGINT NOT NULL,
    diff_artifact_kind VARCHAR(64) NOT NULL,
    diff_artifact_producer VARCHAR(160) NOT NULL,
    diff_artifact_producer_version VARCHAR(64) NOT NULL,
    artifact_schema_version VARCHAR(64) NOT NULL,
    policy_version VARCHAR(64) NOT NULL,
    creation_fence VARCHAR(160) NOT NULL,
    created_at TIMESTAMPTZ(6) NOT NULL,
    artifact_manifest_digest VARCHAR(64) NOT NULL,

    CONSTRAINT fk_review_execution_project
        FOREIGN KEY (project_id) REFERENCES project (id) ON DELETE CASCADE,
    CONSTRAINT uq_review_execution_diff_artifact UNIQUE (diff_artifact_id),
    CONSTRAINT uq_review_execution_manifest_binding
        UNIQUE (id, artifact_manifest_digest),
    CONSTRAINT uq_review_execution_analysis_binding
        UNIQUE (
            id,
            artifact_manifest_digest,
            project_id,
            pull_request_id,
            head_sha
        ),
    CONSTRAINT ck_review_execution_id
        CHECK (id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
    CONSTRAINT ck_review_execution_schema_version CHECK (schema_version = 1),
    CONSTRAINT ck_review_execution_project_id CHECK (project_id > 0),
    CONSTRAINT ck_review_execution_repository_id
        CHECK (repository_id ~ '^[a-z0-9][a-z0-9._-]{0,31}:[A-Za-z0-9._-]{1,128}(/[A-Za-z0-9._-]{1,128})+$'),
    CONSTRAINT ck_review_execution_pull_request_id CHECK (pull_request_id > 0),
    CONSTRAINT ck_review_execution_base_sha
        CHECK (base_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
    CONSTRAINT ck_review_execution_head_sha
        CHECK (head_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
    CONSTRAINT ck_review_execution_merge_base_sha
        CHECK (merge_base_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
    CONSTRAINT ck_review_execution_diff_artifact_id
        CHECK (diff_artifact_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
    CONSTRAINT ck_review_execution_diff_digest
        CHECK (diff_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_execution_diff_byte_length CHECK (diff_byte_length >= 0),
    CONSTRAINT ck_review_execution_diff_kind CHECK (diff_artifact_kind = 'raw-diff'),
    CONSTRAINT ck_review_execution_diff_producer
        CHECK (diff_artifact_producer ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
    CONSTRAINT ck_review_execution_diff_producer_version
        CHECK (diff_artifact_producer_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
    CONSTRAINT ck_review_execution_artifact_schema
        CHECK (artifact_schema_version = 'review-artifact-v1'),
    CONSTRAINT ck_review_execution_policy_version
        CHECK (policy_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
    CONSTRAINT ck_review_execution_creation_fence
        CHECK (creation_fence ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
    CONSTRAINT ck_review_execution_manifest_digest
        CHECK (artifact_manifest_digest ~ '^[0-9a-f]{64}$')
);

CREATE TABLE review_artifact (
    id VARCHAR(160) PRIMARY KEY,
    execution_id VARCHAR(160) NOT NULL,
    artifact_manifest_digest VARCHAR(64) NOT NULL,
    kind VARCHAR(64) NOT NULL,
    content_key TEXT NOT NULL,
    snapshot_sha VARCHAR(64) NOT NULL,
    content_digest VARCHAR(64) NOT NULL,
    byte_length BIGINT NOT NULL,
    content_bytes BYTEA NOT NULL,
    artifact_schema_version VARCHAR(64) NOT NULL,
    producer VARCHAR(160) NOT NULL,
    producer_version VARCHAR(64) NOT NULL,

    CONSTRAINT uq_review_artifact_owner_binding
        UNIQUE (id, execution_id, artifact_manifest_digest),
    CONSTRAINT uq_review_artifact_content_key
        UNIQUE (execution_id, content_key),
    CONSTRAINT fk_review_artifact_manifest_owner
        FOREIGN KEY (execution_id, artifact_manifest_digest)
        REFERENCES review_execution (id, artifact_manifest_digest)
        ON DELETE CASCADE,
    CONSTRAINT ck_review_artifact_id
        CHECK (id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
    CONSTRAINT ck_review_artifact_execution_id
        CHECK (execution_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
    CONSTRAINT ck_review_artifact_manifest_digest
        CHECK (artifact_manifest_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_artifact_kind
        CHECK (kind IN ('raw-diff', 'source-file', 'pr-enrichment', 'review-output')),
    CONSTRAINT ck_review_artifact_content_key
        CHECK (char_length(content_key) BETWEEN 1 AND 1024),
    CONSTRAINT ck_review_artifact_snapshot_sha
        CHECK (snapshot_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
    CONSTRAINT ck_review_artifact_content_digest
        CHECK (content_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_artifact_byte_length CHECK (byte_length >= 0),
    CONSTRAINT ck_review_artifact_content_length
        CHECK (octet_length(content_bytes) = byte_length),
    CONSTRAINT ck_review_artifact_schema_version
        CHECK (artifact_schema_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
    CONSTRAINT ck_review_artifact_producer
        CHECK (producer ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
    CONSTRAINT ck_review_artifact_producer_version
        CHECK (producer_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$')
);

ALTER TABLE review_execution
    ADD CONSTRAINT fk_review_execution_initial_diff
    FOREIGN KEY (diff_artifact_id, id, artifact_manifest_digest)
    REFERENCES review_artifact (id, execution_id, artifact_manifest_digest)
    DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX idx_review_execution_project_pr
    ON review_execution (project_id, repository_id, pull_request_id);

CREATE INDEX idx_review_artifact_execution
    ON review_artifact (execution_id);

CREATE OR REPLACE FUNCTION reject_review_manifest_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'immutable review manifest row in % cannot be updated', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER review_execution_immutable_update
    BEFORE UPDATE ON review_execution
    FOR EACH ROW EXECUTE FUNCTION reject_review_manifest_update();

CREATE TRIGGER review_artifact_immutable_update
    BEFORE UPDATE ON review_artifact
    FOR EACH ROW EXECUTE FUNCTION reject_review_manifest_update();

-- Candidate review ingress accepts exact SHA-1 and SHA-256 object IDs. Widen
-- every commit coordinate necessarily persisted by that path before adding
-- the relational output binding below.
ALTER TABLE job
    ALTER COLUMN commit_hash TYPE VARCHAR(64);

ALTER TABLE analysis_lock
    ALTER COLUMN commit_hash TYPE VARCHAR(64);

ALTER TABLE pull_request
    ALTER COLUMN commit_hash TYPE VARCHAR(64);

ALTER TABLE analyzed_file_snapshot
    ALTER COLUMN commit_hash TYPE VARCHAR(64);

ALTER TABLE analyzed_commit
    ALTER COLUMN commit_hash TYPE VARCHAR(64);

ALTER TABLE code_analysis_issue
    ALTER COLUMN resolved_commit_hash TYPE VARCHAR(64);

ALTER TABLE code_analysis
    ALTER COLUMN commit_hash TYPE VARCHAR(64),
    ADD COLUMN execution_id VARCHAR(160),
    ADD COLUMN artifact_manifest_digest VARCHAR(64),
    ADD CONSTRAINT uq_code_analysis_execution_id UNIQUE (execution_id),
    ADD CONSTRAINT ck_code_analysis_execution_binding_pair
        CHECK (
            (execution_id IS NULL AND artifact_manifest_digest IS NULL)
            OR (
                execution_id IS NOT NULL
                AND artifact_manifest_digest IS NOT NULL
                AND pr_number IS NOT NULL
                AND commit_hash IS NOT NULL
            )
        ),
    ADD CONSTRAINT ck_code_analysis_execution_id
        CHECK (
            execution_id IS NULL
            OR execution_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
        ),
    ADD CONSTRAINT ck_code_analysis_manifest_digest
        CHECK (
            artifact_manifest_digest IS NULL
            OR artifact_manifest_digest ~ '^[0-9a-f]{64}$'
        ),
    ADD CONSTRAINT fk_code_analysis_execution_binding
        FOREIGN KEY (
            execution_id,
            artifact_manifest_digest,
            project_id,
            pr_number,
            commit_hash
        )
        REFERENCES review_execution (
            id,
            artifact_manifest_digest,
            project_id,
            pull_request_id,
            head_sha
        );

CREATE OR REPLACE FUNCTION reject_code_analysis_execution_identity_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.execution_id IS DISTINCT FROM NEW.execution_id
        OR OLD.artifact_manifest_digest IS DISTINCT FROM NEW.artifact_manifest_digest THEN
        RAISE EXCEPTION 'immutable candidate execution identity on code_analysis cannot be updated'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER code_analysis_execution_identity_immutable_update
    BEFORE UPDATE ON code_analysis
    FOR EACH ROW EXECUTE FUNCTION reject_code_analysis_execution_identity_update();

COMMENT ON TABLE review_execution IS
    'Immutable execution identity and canonical input-artifact manifest coordinates.';

COMMENT ON TABLE review_artifact IS
    'Immutable execution-owned artifact metadata and exact bytes; P1-11 owns later encryption and retention controls.';

COMMENT ON COLUMN code_analysis.execution_id IS
    'Candidate execution identity; null only for the explicit legacy compatibility path.';

COMMENT ON COLUMN code_analysis.artifact_manifest_digest IS
    'Immutable candidate manifest digest paired with execution_id; null for legacy analyses.';
