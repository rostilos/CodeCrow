-- Migration: Create notification tables
-- Version: 1.3.0
-- Description: Tables to support the notification system with user preferences

-- Main notification table
CREATE TABLE IF NOT EXISTS notification (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspace(id) ON DELETE SET NULL,
    type VARCHAR(50) NOT NULL,
    scope VARCHAR(20) NOT NULL DEFAULT 'APP',
    priority VARCHAR(20) NOT NULL DEFAULT 'MEDIUM',
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    action_url TEXT,
    action_label VARCHAR(100),
    metadata_json TEXT,
    read BOOLEAN NOT NULL DEFAULT FALSE,
    email_sent BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_notification_type CHECK (type IN (
        'TOKEN_EXPIRING', 'TOKEN_EXPIRED', 'TOKEN_REFRESHED',
        'WORKSPACE_OWNERSHIP_TRANSFER_INITIATED', 'WORKSPACE_OWNERSHIP_TRANSFER_COMPLETED',
        'WORKSPACE_OWNERSHIP_TRANSFER_CANCELLED', 'WORKSPACE_MEMBER_JOINED', 'WORKSPACE_MEMBER_LEFT',
        'BILLING_USAGE_WARNING', 'BILLING_QUOTA_EXCEEDED', 'BILLING_PAYMENT_FAILED',
        'ANALYSIS_COMPLETED', 'ANALYSIS_FAILED',
        'SYSTEM_ANNOUNCEMENT', 'SYSTEM_MAINTENANCE', 'FEATURE_UPDATE'
    )),
    CONSTRAINT chk_notification_scope CHECK (scope IN ('WORKSPACE', 'APP')),
    CONSTRAINT chk_notification_priority CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL'))
);

-- Index for fetching user notifications ordered by date
CREATE INDEX idx_notification_user_created ON notification(user_id, created_at DESC);

-- Index for fetching unread notifications
CREATE INDEX idx_notification_user_read ON notification(user_id, read) WHERE read = FALSE;

-- Index for fetching workspace notifications
CREATE INDEX idx_notification_workspace ON notification(workspace_id, created_at DESC) WHERE workspace_id IS NOT NULL;

-- Index for expiration cleanup
CREATE INDEX idx_notification_expires ON notification(expires_at) WHERE expires_at IS NOT NULL;

-- Index for deduplication checks
CREATE INDEX idx_notification_dedup ON notification(user_id, type, workspace_id, created_at DESC);

-- User notification preferences table
CREATE TABLE IF NOT EXISTS notification_preference (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspace(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,
    in_app_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    email_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    min_priority VARCHAR(20) NOT NULL DEFAULT 'LOW',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_preference_type CHECK (type IN (
        'TOKEN_EXPIRING', 'TOKEN_EXPIRED', 'TOKEN_REFRESHED',
        'WORKSPACE_OWNERSHIP_TRANSFER_INITIATED', 'WORKSPACE_OWNERSHIP_TRANSFER_COMPLETED',
        'WORKSPACE_OWNERSHIP_TRANSFER_CANCELLED', 'WORKSPACE_MEMBER_JOINED', 'WORKSPACE_MEMBER_LEFT',
        'BILLING_USAGE_WARNING', 'BILLING_QUOTA_EXCEEDED', 'BILLING_PAYMENT_FAILED',
        'ANALYSIS_COMPLETED', 'ANALYSIS_FAILED',
        'SYSTEM_ANNOUNCEMENT', 'SYSTEM_MAINTENANCE', 'FEATURE_UPDATE'
    )),
    CONSTRAINT chk_preference_min_priority CHECK (min_priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    CONSTRAINT uq_user_type_workspace UNIQUE (user_id, type, workspace_id)
);

-- Index for fetching global preferences
CREATE INDEX idx_preference_user_global ON notification_preference(user_id) WHERE workspace_id IS NULL;

-- Index for fetching workspace-specific preferences
CREATE INDEX idx_preference_user_workspace ON notification_preference(user_id, workspace_id) WHERE workspace_id IS NOT NULL;

-- Comments for documentation
COMMENT ON TABLE notification IS 'Stores user notifications with support for both workspace and app-level notifications';
COMMENT ON COLUMN notification.type IS 'Type of notification from predefined set';
COMMENT ON COLUMN notification.scope IS 'WORKSPACE for workspace-related, APP for application-wide notifications';
COMMENT ON COLUMN notification.priority IS 'Notification priority: LOW, MEDIUM, HIGH, CRITICAL';
COMMENT ON COLUMN notification.metadata_json IS 'JSON string with additional notification-specific data';
COMMENT ON COLUMN notification.expires_at IS 'Optional expiration date after which notification is auto-cleaned';

COMMENT ON TABLE notification_preference IS 'User preferences for notification delivery per type';
COMMENT ON COLUMN notification_preference.workspace_id IS 'NULL for global preference, specific ID for workspace override';
COMMENT ON COLUMN notification_preference.min_priority IS 'Minimum priority level to receive notifications for this type';
