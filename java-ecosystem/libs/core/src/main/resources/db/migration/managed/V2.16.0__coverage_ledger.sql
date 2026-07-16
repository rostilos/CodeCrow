CREATE TABLE review_coverage_anchor (
    anchor_id VARCHAR(64) PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    execution_id VARCHAR(160) NOT NULL,
    artifact_manifest_digest VARCHAR(64) NOT NULL,
    diff_digest VARCHAR(64) NOT NULL,
    diff_byte_length BIGINT NOT NULL,
    ledger_digest VARCHAR(64) NOT NULL,
    source_artifact_id VARCHAR(160) NOT NULL,
    source_digest VARCHAR(64) NOT NULL,
    parent_hunk_id VARCHAR(64) NOT NULL,
    change_id VARCHAR(64) NOT NULL,
    change_status VARCHAR(32) NOT NULL,
    anchor_kind VARCHAR(32) NOT NULL,
    old_path TEXT,
    new_path TEXT,
    old_start INTEGER NOT NULL,
    old_line_count INTEGER NOT NULL,
    new_start INTEGER NOT NULL,
    new_line_count INTEGER NOT NULL,
    mandatory BOOLEAN NOT NULL,
    initial_state VARCHAR(32) NOT NULL,
    initial_reason_code VARCHAR(160),
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_review_coverage_anchor_coordinates
        UNIQUE (
            execution_id,
            change_id,
            anchor_kind,
            old_start,
            old_line_count,
            new_start,
            new_line_count
        ),
    CONSTRAINT uq_review_coverage_anchor_ledger_owner
        UNIQUE (
            anchor_id,
            execution_id,
            artifact_manifest_digest,
            ledger_digest
        ),
    CONSTRAINT fk_review_coverage_anchor_manifest
        FOREIGN KEY (execution_id, artifact_manifest_digest)
        REFERENCES review_execution (id, artifact_manifest_digest)
        ON DELETE CASCADE,
    CONSTRAINT fk_review_coverage_anchor_source_artifact
        FOREIGN KEY (
            source_artifact_id,
            execution_id,
            artifact_manifest_digest
        )
        REFERENCES review_artifact (
            id,
            execution_id,
            artifact_manifest_digest
        ),
    CONSTRAINT ck_review_coverage_anchor_schema_version
        CHECK (schema_version = 1),
    CONSTRAINT ck_review_coverage_anchor_id
        CHECK (anchor_id ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_coverage_anchor_manifest_digest
        CHECK (artifact_manifest_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_coverage_anchor_diff_digest
        CHECK (diff_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_coverage_anchor_diff_length
        CHECK (diff_byte_length >= 0),
    CONSTRAINT ck_review_coverage_anchor_ledger_digest
        CHECK (ledger_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_coverage_anchor_source_artifact_id
        CHECK (source_artifact_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
    CONSTRAINT ck_review_coverage_anchor_source_digest
        CHECK (source_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_coverage_anchor_parent_hunk
        CHECK (parent_hunk_id ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_coverage_anchor_change_id
        CHECK (change_id ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_coverage_anchor_change_status
        CHECK (change_status IN ('add', 'modify', 'delete', 'rename', 'copy')),
    CONSTRAINT ck_review_coverage_anchor_kind
        CHECK (anchor_kind IN ('text-hunk', 'file-change')),
    CONSTRAINT ck_review_coverage_anchor_path
        CHECK (
            (old_path IS NOT NULL AND char_length(old_path) BETWEEN 1 AND 4096)
            OR (new_path IS NOT NULL AND char_length(new_path) BETWEEN 1 AND 4096)
        ),
    CONSTRAINT ck_review_coverage_anchor_ranges
        CHECK (
            old_start >= 0
            AND old_line_count >= 0
            AND new_start >= 0
            AND new_line_count >= 0
        ),
    CONSTRAINT ck_review_coverage_anchor_initial_state
        CHECK (
            initial_state IN (
                'pending',
                'owner-pending',
                'examined',
                'incomplete',
                'unsupported',
                'failed',
                'policy-excluded',
                'deleted-recorded'
            )
        ),
    CONSTRAINT ck_review_coverage_anchor_initial_reason
        CHECK (
            initial_reason_code IS NULL
            OR char_length(initial_reason_code) BETWEEN 1 AND 160
        )
);

CREATE TABLE review_coverage_disposition (
    execution_id VARCHAR(160) NOT NULL,
    artifact_manifest_digest VARCHAR(64) NOT NULL,
    ledger_digest VARCHAR(64) NOT NULL,
    anchor_id VARCHAR(64) NOT NULL,
    coverage_state VARCHAR(32) NOT NULL,
    reason_code VARCHAR(160),
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (execution_id, anchor_id),
    CONSTRAINT uq_review_coverage_disposition_ledger_identity
        UNIQUE (
            execution_id,
            artifact_manifest_digest,
            ledger_digest,
            anchor_id
        ),
    CONSTRAINT fk_review_coverage_disposition_anchor
        FOREIGN KEY (
            anchor_id,
            execution_id,
            artifact_manifest_digest,
            ledger_digest
        )
        REFERENCES review_coverage_anchor (
            anchor_id,
            execution_id,
            artifact_manifest_digest,
            ledger_digest
        )
        ON DELETE CASCADE,
    CONSTRAINT ck_review_coverage_disposition_manifest_digest
        CHECK (artifact_manifest_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_coverage_disposition_ledger_digest
        CHECK (ledger_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_coverage_disposition_anchor_id
        CHECK (anchor_id ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_coverage_disposition_state
        CHECK (
            coverage_state IN (
                'pending',
                'owner-pending',
                'examined',
                'incomplete',
                'unsupported',
                'failed',
                'policy-excluded',
                'deleted-recorded'
            )
        ),
    CONSTRAINT ck_review_coverage_disposition_reason
        CHECK (
            reason_code IS NULL
            OR char_length(reason_code) BETWEEN 1 AND 160
        )
);

CREATE TABLE review_analysis_state (
    execution_id VARCHAR(160) PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    artifact_manifest_digest VARCHAR(64) NOT NULL,
    diff_digest VARCHAR(64) NOT NULL,
    diff_byte_length BIGINT NOT NULL,
    ledger_digest VARCHAR(64) NOT NULL,
    analysis_state VARCHAR(32) NOT NULL,
    inventory_anchor_count INTEGER NOT NULL,
    pending_anchor_count INTEGER NOT NULL,
    owner_pending_anchor_count INTEGER NOT NULL,
    examined_anchor_count INTEGER NOT NULL,
    incomplete_anchor_count INTEGER NOT NULL,
    unsupported_anchor_count INTEGER NOT NULL,
    failed_anchor_count INTEGER NOT NULL,
    policy_excluded_anchor_count INTEGER NOT NULL,
    deleted_recorded_anchor_count INTEGER NOT NULL,
    reason_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    revision BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_review_analysis_state_ledger_identity
        UNIQUE (execution_id, artifact_manifest_digest, ledger_digest),
    CONSTRAINT fk_review_analysis_state_manifest
        FOREIGN KEY (execution_id, artifact_manifest_digest)
        REFERENCES review_execution (id, artifact_manifest_digest)
        ON DELETE CASCADE,
    CONSTRAINT ck_review_analysis_state_schema_version
        CHECK (schema_version = 1),
    CONSTRAINT ck_review_analysis_state_manifest_digest
        CHECK (artifact_manifest_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_analysis_state_diff_digest
        CHECK (diff_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_analysis_state_diff_length
        CHECK (diff_byte_length >= 0),
    CONSTRAINT ck_review_analysis_state_ledger_digest
        CHECK (ledger_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_analysis_state
        CHECK (
            analysis_state IN (
                'pending',
                'empty',
                'partial',
                'failed',
                'complete',
                'superseded'
            )
        ),
    CONSTRAINT ck_review_analysis_state_counts
        CHECK (
            inventory_anchor_count >= 0
            AND pending_anchor_count >= 0
            AND owner_pending_anchor_count >= 0
            AND examined_anchor_count >= 0
            AND incomplete_anchor_count >= 0
            AND unsupported_anchor_count >= 0
            AND failed_anchor_count >= 0
            AND policy_excluded_anchor_count >= 0
            AND deleted_recorded_anchor_count >= 0
        ),
    CONSTRAINT ck_review_analysis_state_count_reconciliation
        CHECK (
            inventory_anchor_count =
                pending_anchor_count
                + owner_pending_anchor_count
                + examined_anchor_count
                + incomplete_anchor_count
                + unsupported_anchor_count
                + failed_anchor_count
                + policy_excluded_anchor_count
                + deleted_recorded_anchor_count
        ),
    CONSTRAINT ck_review_analysis_state_reason_counts
        CHECK (jsonb_typeof(reason_counts) = 'object'),
    CONSTRAINT ck_review_analysis_state_revision
        CHECK (revision >= 0)
);

CREATE INDEX idx_review_coverage_anchor_execution_state
    ON review_coverage_anchor (execution_id, initial_state);

CREATE INDEX idx_review_coverage_anchor_change
    ON review_coverage_anchor (execution_id, change_id);

CREATE INDEX idx_review_coverage_disposition_state
    ON review_coverage_disposition (execution_id, coverage_state);

CREATE OR REPLACE FUNCTION reject_review_coverage_anchor_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'immutable coverage anchor cannot be updated'
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER review_coverage_anchor_immutable_update
    BEFORE UPDATE ON review_coverage_anchor
    FOR EACH ROW EXECUTE FUNCTION reject_review_coverage_anchor_update();

CREATE OR REPLACE FUNCTION reject_review_coverage_disposition_identity_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.execution_id IS DISTINCT FROM NEW.execution_id
        OR OLD.artifact_manifest_digest IS DISTINCT FROM NEW.artifact_manifest_digest
        OR OLD.ledger_digest IS DISTINCT FROM NEW.ledger_digest
        OR OLD.anchor_id IS DISTINCT FROM NEW.anchor_id THEN
        RAISE EXCEPTION 'coverage disposition identity cannot be updated'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER review_coverage_disposition_identity_update
    BEFORE UPDATE ON review_coverage_disposition
    FOR EACH ROW EXECUTE FUNCTION reject_review_coverage_disposition_identity_update();

CREATE OR REPLACE FUNCTION reject_review_analysis_state_identity_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.schema_version IS DISTINCT FROM NEW.schema_version
        OR OLD.execution_id IS DISTINCT FROM NEW.execution_id
        OR OLD.artifact_manifest_digest IS DISTINCT FROM NEW.artifact_manifest_digest
        OR OLD.diff_digest IS DISTINCT FROM NEW.diff_digest
        OR OLD.diff_byte_length IS DISTINCT FROM NEW.diff_byte_length
        OR OLD.ledger_digest IS DISTINCT FROM NEW.ledger_digest THEN
        RAISE EXCEPTION 'coverage analysis identity cannot be updated'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER review_analysis_state_identity_update
    BEFORE UPDATE ON review_analysis_state
    FOR EACH ROW EXECUTE FUNCTION reject_review_analysis_state_identity_update();

COMMENT ON TABLE review_coverage_anchor IS
    'Immutable, execution-bound inventory of every changed hunk and explicit non-text change.';

COMMENT ON TABLE review_coverage_disposition IS
    'Current explicit producer disposition for an immutable coverage anchor.';

COMMENT ON TABLE review_analysis_state IS
    'Optimistically updated aggregate derived from the execution coverage ledger.';
