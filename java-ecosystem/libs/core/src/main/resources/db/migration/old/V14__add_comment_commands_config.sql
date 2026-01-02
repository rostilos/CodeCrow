-- Migration: Add Comment Commands Configuration
-- Description: Adds configuration table for PR comment-triggered commands and project-level settings
-- Date: 2025-XX-XX

-- =====================================================
-- Step 1: Create comment_command_config table
-- =====================================================

CREATE TABLE IF NOT EXISTS comment_command_config (
    id BIGSERIAL PRIMARY KEY,
    
    -- Reference to the CodeCrow project
    project_id BIGINT NOT NULL,
    
    -- Command settings
    enabled BOOLEAN NOT NULL DEFAULT true,
    
    -- Allowed commands (JSON array of command names)
    -- e.g., ["analyze", "review", "summarize", "explain", "suggest"]
    allowed_commands TEXT DEFAULT '["analyze", "review", "summarize", "explain", "suggest"]',
    
    -- Command prefix (e.g., "@codecrow", "/codecrow")
    command_prefix VARCHAR(32) NOT NULL DEFAULT '@codecrow',
    
    -- Rate limiting settings
    max_commands_per_pr INTEGER NOT NULL DEFAULT 10,
    max_commands_per_day INTEGER NOT NULL DEFAULT 50,
    cooldown_seconds INTEGER NOT NULL DEFAULT 60,
    
    -- Who can use commands
    -- ALL_USERS, PR_PARTICIPANTS, REVIEWERS_ONLY, ADMINS_ONLY
    allowed_users VARCHAR(32) NOT NULL DEFAULT 'PR_PARTICIPANTS',
    
    -- Response settings
    respond_in_thread BOOLEAN NOT NULL DEFAULT true,
    add_reaction BOOLEAN NOT NULL DEFAULT true,
    
    -- AI settings override (null means use project defaults)
    override_ai_connection_id BIGINT,
    override_max_tokens INTEGER,
    
    -- Audit timestamps
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by BIGINT,
    updated_by BIGINT,
    
    -- Foreign key constraints
    CONSTRAINT fk_comment_cmd_config_project 
        FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE,
    CONSTRAINT fk_comment_cmd_config_ai_connection 
        FOREIGN KEY (override_ai_connection_id) REFERENCES ai_connection(id) ON DELETE SET NULL,
    CONSTRAINT fk_comment_cmd_config_created_by 
        FOREIGN KEY (created_by) REFERENCES user_account(id) ON DELETE SET NULL,
    CONSTRAINT fk_comment_cmd_config_updated_by 
        FOREIGN KEY (updated_by) REFERENCES user_account(id) ON DELETE SET NULL,
    
    -- Ensure one config per project
    CONSTRAINT uq_comment_cmd_config_project UNIQUE (project_id)
);

-- =====================================================
-- Step 2: Add comment_commands_enabled flag to project
-- =====================================================

ALTER TABLE project ADD COLUMN IF NOT EXISTS comment_commands_enabled BOOLEAN DEFAULT false;

-- =====================================================
-- Step 3: Create indexes
-- =====================================================

CREATE INDEX IF NOT EXISTS idx_comment_cmd_config_project 
    ON comment_command_config(project_id);

CREATE INDEX IF NOT EXISTS idx_project_comment_commands_enabled 
    ON project(comment_commands_enabled) WHERE comment_commands_enabled = true;

-- =====================================================
-- Step 4: Create updated_at trigger
-- =====================================================

CREATE OR REPLACE FUNCTION update_comment_cmd_config_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_comment_cmd_config_updated_at ON comment_command_config;
CREATE TRIGGER trigger_comment_cmd_config_updated_at
    BEFORE UPDATE ON comment_command_config
    FOR EACH ROW
    EXECUTE FUNCTION update_comment_cmd_config_updated_at();

-- =====================================================
-- Step 5: Insert default config for existing projects with webhooks
-- =====================================================

INSERT INTO comment_command_config (project_id, enabled)
SELECT p.id, false
FROM project p
WHERE p.id NOT IN (SELECT project_id FROM comment_command_config)
  AND EXISTS (SELECT 1 FROM vcs_repo_binding vrb WHERE vrb.project_id = p.id);
