CREATE TABLE review_delivery_current_head (
    provider VARCHAR(32) NOT NULL,
    tenant_id BIGINT NOT NULL,
    project_id BIGINT NOT NULL,
    repository_id TEXT NOT NULL,
    pull_request_id BIGINT NOT NULL,
    head_generation BIGINT NOT NULL,
    execution_id VARCHAR(160) NOT NULL,
    artifact_manifest_digest VARCHAR(64) NOT NULL,
    head_sha VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_review_delivery_current_head
        PRIMARY KEY (provider, project_id, pull_request_id),
    CONSTRAINT fk_review_delivery_current_head_execution
        FOREIGN KEY (
            execution_id,
            artifact_manifest_digest,
            project_id,
            pull_request_id,
            head_sha
        ) REFERENCES review_execution (
            id,
            artifact_manifest_digest,
            project_id,
            pull_request_id,
            head_sha
        ) ON DELETE CASCADE,
    CONSTRAINT ck_review_delivery_current_head_provider
        CHECK (provider ~ '^[a-z0-9_-]{1,32}$'),
    CONSTRAINT ck_review_delivery_current_head_repository
        CHECK (
            char_length(repository_id) BETWEEN 1 AND 512
            AND repository_id = btrim(repository_id)
            AND repository_id LIKE provider || ':%'
        ),
    CONSTRAINT ck_review_delivery_current_head_generation
        CHECK (head_generation > 0),
    CONSTRAINT ck_review_delivery_current_head_manifest_digest
        CHECK (artifact_manifest_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_delivery_current_head_sha
        CHECK (head_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
    CONSTRAINT ck_review_delivery_current_head_coordinates
        CHECK (tenant_id > 0 AND project_id > 0 AND pull_request_id > 0)
);

CREATE OR REPLACE FUNCTION reject_review_delivery_current_head_regression()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.provider IS DISTINCT FROM OLD.provider
            OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
            OR NEW.project_id IS DISTINCT FROM OLD.project_id
            OR NEW.repository_id IS DISTINCT FROM OLD.repository_id
            OR NEW.pull_request_id IS DISTINCT FROM OLD.pull_request_id THEN
        RAISE EXCEPTION 'review delivery current-head scope is immutable';
    END IF;
    IF NEW.head_generation < OLD.head_generation THEN
        RAISE EXCEPTION 'review delivery current-head generation cannot regress';
    END IF;
    IF NEW.head_generation = OLD.head_generation
            AND (
                NEW.execution_id IS DISTINCT FROM OLD.execution_id
                OR NEW.artifact_manifest_digest IS DISTINCT FROM
                    OLD.artifact_manifest_digest
                OR NEW.head_sha IS DISTINCT FROM OLD.head_sha
            ) THEN
        RAISE EXCEPTION 'review delivery generation has divergent identity';
    END IF;
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_review_delivery_current_head_monotonic
BEFORE UPDATE ON review_delivery_current_head
FOR EACH ROW
EXECUTE FUNCTION reject_review_delivery_current_head_regression();

CREATE TABLE review_delivery_outbox (
    intent_id VARCHAR(160) PRIMARY KEY,
    execution_id VARCHAR(160) NOT NULL,
    artifact_manifest_digest VARCHAR(64) NOT NULL,
    code_analysis_id BIGINT NOT NULL,
    report_artifact_id VARCHAR(160) NOT NULL,
    report_digest VARCHAR(64) NOT NULL,
    analysis_truth_digest VARCHAR(64) NOT NULL,
    provider VARCHAR(32) NOT NULL,
    tenant_id BIGINT NOT NULL,
    project_id BIGINT NOT NULL,
    repository_id TEXT NOT NULL,
    pull_request_id BIGINT NOT NULL,
    platform_pull_request_id BIGINT NOT NULL,
    head_sha VARCHAR(64) NOT NULL,
    head_generation BIGINT NOT NULL,
    publication_kind VARCHAR(32) NOT NULL,
    idempotency_key VARCHAR(160) NOT NULL,
    state VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_owner VARCHAR(160),
    lease_token VARCHAR(160),
    lease_expires_at TIMESTAMPTZ(6),
    last_error_code VARCHAR(160),
    provider_receipt_id VARCHAR(160),
    delivered_at TIMESTAMPTZ(6),
    created_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_review_delivery_manifest
        FOREIGN KEY (execution_id, artifact_manifest_digest)
        REFERENCES review_execution (id, artifact_manifest_digest)
        ON DELETE CASCADE,
    CONSTRAINT fk_review_delivery_analysis
        FOREIGN KEY (code_analysis_id) REFERENCES code_analysis (id)
        ON DELETE CASCADE,
    CONSTRAINT fk_review_delivery_platform_pull_request
        FOREIGN KEY (platform_pull_request_id) REFERENCES pull_request (id)
        ON DELETE CASCADE,
    CONSTRAINT uq_review_delivery_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT uq_review_delivery_intent UNIQUE (
        execution_id,
        provider,
        tenant_id,
        repository_id,
        publication_kind,
        project_id,
        pull_request_id,
        head_sha
    ),
    CONSTRAINT ck_review_delivery_intent_id
        CHECK (intent_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'),
    CONSTRAINT ck_review_delivery_manifest_digest
        CHECK (artifact_manifest_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_delivery_report_artifact_id
        CHECK (report_artifact_id ~ '^review-output:[0-9a-f]{64}$'),
    CONSTRAINT ck_review_delivery_report_digest
        CHECK (report_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_delivery_analysis_truth_digest
        CHECK (analysis_truth_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_delivery_head_sha
        CHECK (head_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
    CONSTRAINT ck_review_delivery_head_generation
        CHECK (head_generation > 0),
    CONSTRAINT ck_review_delivery_provider
        CHECK (provider ~ '^[a-z0-9_-]{1,32}$'),
    CONSTRAINT ck_review_delivery_repository
        CHECK (
            char_length(repository_id) BETWEEN 1 AND 512
            AND repository_id = btrim(repository_id)
            AND repository_id LIKE provider || ':%'
        ),
    CONSTRAINT ck_review_delivery_publication_kind
        CHECK (publication_kind ~ '^[A-Z][A-Z0-9_]{0,31}$'),
    CONSTRAINT ck_review_delivery_idempotency_key
        CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_review_delivery_state
        CHECK (state IN (
            'PENDING',
            'IN_FLIGHT',
            'RETRYABLE_FAILED',
            'PERMANENT_FAILED',
            'AMBIGUOUS',
            'DELIVERED',
            'STALE'
        )),
    CONSTRAINT ck_review_delivery_attempt_count CHECK (
        (state = 'PENDING' AND attempt_count = 0)
        OR (state <> 'PENDING' AND attempt_count > 0)
    ),
    CONSTRAINT ck_review_delivery_coordinates CHECK (
        tenant_id > 0
        AND project_id > 0
        AND pull_request_id > 0
        AND platform_pull_request_id > 0
    ),
    CONSTRAINT ck_review_delivery_lease_tuple CHECK (
        (lease_owner IS NULL
            AND lease_token IS NULL
            AND lease_expires_at IS NULL)
        OR
        (lease_owner IS NOT NULL
            AND lease_token IS NOT NULL
            AND lease_expires_at IS NOT NULL)
    ),
    CONSTRAINT ck_review_delivery_active_lease CHECK (
        (state IN ('IN_FLIGHT', 'AMBIGUOUS')
            AND lease_owner IS NOT NULL
            AND lease_token IS NOT NULL
            AND lease_expires_at IS NOT NULL)
        OR
        (state = 'AMBIGUOUS'
            AND lease_owner IS NULL
            AND lease_token IS NULL
            AND lease_expires_at IS NULL)
        OR
        (state NOT IN ('IN_FLIGHT', 'AMBIGUOUS')
            AND lease_owner IS NULL
            AND lease_token IS NULL
            AND lease_expires_at IS NULL)
    ),
    CONSTRAINT ck_review_delivery_receipt_state CHECK (
        (state = 'DELIVERED'
            AND provider_receipt_id IS NOT NULL
            AND delivered_at IS NOT NULL)
        OR (state <> 'DELIVERED'
            AND provider_receipt_id IS NULL
            AND delivered_at IS NULL)
    ),
    CONSTRAINT ck_review_delivery_error_state CHECK (
        (state = 'RETRYABLE_FAILED'
            AND last_error_code ~ '^[a-z0-9_]{1,64}$')
        OR (state = 'PERMANENT_FAILED'
            AND last_error_code ~ '^[a-z0-9_]{1,64}$')
        OR (state = 'AMBIGUOUS'
            AND last_error_code ~ '^[a-z0-9_]{1,64}$')
        OR (state = 'STALE' AND last_error_code = 'stale_head')
        OR (state NOT IN (
                'RETRYABLE_FAILED',
                'PERMANENT_FAILED',
                'AMBIGUOUS',
                'STALE'
            )
            AND last_error_code IS NULL)
    )
);

CREATE INDEX idx_review_delivery_due
    ON review_delivery_outbox (next_attempt_at, lease_expires_at, intent_id)
    WHERE state IN ('PENDING', 'RETRYABLE_FAILED', 'IN_FLIGHT');

CREATE INDEX idx_review_delivery_execution
    ON review_delivery_outbox (execution_id, publication_kind);

CREATE OR REPLACE FUNCTION set_review_delivery_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_review_delivery_updated_at
BEFORE UPDATE ON review_delivery_outbox
FOR EACH ROW
EXECUTE FUNCTION set_review_delivery_updated_at();
