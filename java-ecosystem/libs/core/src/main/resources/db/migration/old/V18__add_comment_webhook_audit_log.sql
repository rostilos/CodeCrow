-- Migration: Add Comment Webhook Audit Log
-- Description: Audit log for tracking all webhook events and command executions
-- Date: 2025-XX-XX

-- =====================================================
-- Step 1: Create comment_webhook_audit_log table
-- =====================================================

CREATE TABLE IF NOT EXISTS comment_webhook_audit_log (
    id BIGSERIAL PRIMARY KEY,
    
    -- Webhook event identification
    webhook_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    
    -- Source information
    provider VARCHAR(32) NOT NULL,
    project_id BIGINT,
    pr_id VARCHAR(64),
    comment_id VARCHAR(128),
    
    -- User information
    vcs_username VARCHAR(256),
    vcs_user_id VARCHAR(128),
    
    -- Command information (if applicable)
    command_detected VARCHAR(64),
    command_args TEXT,
    
    -- Processing status
    status VARCHAR(32) NOT NULL DEFAULT 'RECEIVED',
    error_code VARCHAR(64),
    error_message TEXT,
    
    -- Timing information
    received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    processing_duration_ms INTEGER,
    
    -- Request/Response metadata
    request_ip VARCHAR(64),
    request_signature_valid BOOLEAN,
    response_status INTEGER,
    
    -- Raw payload (for debugging, consider encryption in production)
    payload_hash VARCHAR(64),
    
    -- Foreign key constraint (optional - project may not exist yet)
    CONSTRAINT fk_webhook_audit_project 
        FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE SET NULL
);

-- =====================================================
-- Step 2: Create enum type for audit status
-- =====================================================

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'webhook_audit_status') THEN
        CREATE TYPE webhook_audit_status AS ENUM (
            'RECEIVED',
            'PROCESSING',
            'SUCCESS',
            'FAILED',
            'IGNORED',
            'RATE_LIMITED',
            'UNAUTHORIZED'
        );
    END IF;
END $$;

-- =====================================================
-- Step 3: Create indexes for audit queries
-- =====================================================

-- Index for finding audit entries by webhook ID
CREATE INDEX IF NOT EXISTS idx_webhook_audit_webhook_id 
    ON comment_webhook_audit_log(webhook_id);

-- Index for filtering by project and time
CREATE INDEX IF NOT EXISTS idx_webhook_audit_project_time 
    ON comment_webhook_audit_log(project_id, received_at DESC);

-- Index for filtering by status
CREATE INDEX IF NOT EXISTS idx_webhook_audit_status 
    ON comment_webhook_audit_log(status, received_at);

-- Index for filtering by user
CREATE INDEX IF NOT EXISTS idx_webhook_audit_user 
    ON comment_webhook_audit_log(vcs_username, received_at DESC);

-- Index for finding failed events for retry
CREATE INDEX IF NOT EXISTS idx_webhook_audit_failed 
    ON comment_webhook_audit_log(status, project_id, received_at) 
    WHERE status = 'FAILED';

-- Index for analytics queries
CREATE INDEX IF NOT EXISTS idx_webhook_audit_analytics 
    ON comment_webhook_audit_log(provider, event_type, DATE(received_at));

-- =====================================================
-- Step 4: Create partitioning for audit log (optional)
-- =====================================================

-- Note: For high-volume deployments, consider partitioning by date
-- This is a comment showing how it would be done:
-- CREATE TABLE comment_webhook_audit_log_partitioned (
--     LIKE comment_webhook_audit_log INCLUDING ALL
-- ) PARTITION BY RANGE (received_at);

-- =====================================================
-- Step 5: Create function to log webhook event
-- =====================================================

CREATE OR REPLACE FUNCTION log_webhook_event(
    p_webhook_id VARCHAR(128),
    p_event_type VARCHAR(64),
    p_provider VARCHAR(32),
    p_project_id BIGINT,
    p_pr_id VARCHAR(64),
    p_comment_id VARCHAR(128),
    p_vcs_username VARCHAR(256),
    p_request_ip VARCHAR(64),
    p_payload_hash VARCHAR(64)
)
RETURNS BIGINT AS $$
DECLARE
    v_audit_id BIGINT;
BEGIN
    INSERT INTO comment_webhook_audit_log (
        webhook_id, event_type, provider, project_id, pr_id, 
        comment_id, vcs_username, request_ip, payload_hash, status
    ) VALUES (
        p_webhook_id, p_event_type, p_provider, p_project_id, p_pr_id,
        p_comment_id, p_vcs_username, p_request_ip, p_payload_hash, 'RECEIVED'
    ) RETURNING id INTO v_audit_id;
    
    RETURN v_audit_id;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Step 6: Create function to update audit status
-- =====================================================

CREATE OR REPLACE FUNCTION update_webhook_audit_status(
    p_audit_id BIGINT,
    p_status VARCHAR(32),
    p_command_detected VARCHAR(64) DEFAULT NULL,
    p_command_args TEXT DEFAULT NULL,
    p_error_code VARCHAR(64) DEFAULT NULL,
    p_error_message TEXT DEFAULT NULL,
    p_response_status INTEGER DEFAULT NULL
)
RETURNS VOID AS $$
DECLARE
    v_received_at TIMESTAMP;
BEGIN
    SELECT received_at INTO v_received_at
    FROM comment_webhook_audit_log
    WHERE id = p_audit_id;
    
    UPDATE comment_webhook_audit_log
    SET status = p_status,
        command_detected = COALESCE(p_command_detected, command_detected),
        command_args = COALESCE(p_command_args, command_args),
        error_code = p_error_code,
        error_message = p_error_message,
        response_status = p_response_status,
        processed_at = CURRENT_TIMESTAMP,
        processing_duration_ms = EXTRACT(MILLISECOND FROM (CURRENT_TIMESTAMP - v_received_at))::INTEGER
    WHERE id = p_audit_id;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Step 7: Create cleanup function for old audit records
-- =====================================================

CREATE OR REPLACE FUNCTION cleanup_old_audit_records(days_to_keep INTEGER DEFAULT 90)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM comment_webhook_audit_log
    WHERE received_at < CURRENT_TIMESTAMP - (days_to_keep || ' days')::INTERVAL;
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Step 8: Create view for audit statistics
-- =====================================================

CREATE OR REPLACE VIEW webhook_audit_stats AS
SELECT 
    DATE(received_at) as audit_date,
    provider,
    event_type,
    status,
    COUNT(*) as event_count,
    AVG(processing_duration_ms)::INTEGER as avg_processing_ms,
    MAX(processing_duration_ms) as max_processing_ms
FROM comment_webhook_audit_log
WHERE received_at > CURRENT_TIMESTAMP - INTERVAL '30 days'
GROUP BY DATE(received_at), provider, event_type, status
ORDER BY audit_date DESC, event_count DESC;
