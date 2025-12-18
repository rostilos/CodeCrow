-- Migration: Create Comment Command Rate Limit Table
-- Description: Tracks rate limiting for comment-triggered commands
-- Date: 2025-XX-XX

-- =====================================================
-- Step 1: Create comment_command_rate_limit table
-- =====================================================

CREATE TABLE IF NOT EXISTS comment_command_rate_limit (
    id BIGSERIAL PRIMARY KEY,
    
    -- Reference to the project
    project_id BIGINT NOT NULL,
    
    -- PR identifier (unique within project)
    pr_id VARCHAR(64) NOT NULL,
    
    -- User who triggered the command (VCS username)
    vcs_username VARCHAR(256) NOT NULL,
    
    -- Command that was executed
    command_name VARCHAR(64) NOT NULL,
    
    -- Execution timestamp
    executed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- Date partition for efficient cleanup
    execution_date DATE NOT NULL DEFAULT CURRENT_DATE,
    
    -- Foreign key constraints
    CONSTRAINT fk_cmd_rate_limit_project 
        FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);

-- =====================================================
-- Step 2: Create indexes for rate limit queries
-- =====================================================

-- Index for checking per-PR rate limit
CREATE INDEX IF NOT EXISTS idx_cmd_rate_limit_pr 
    ON comment_command_rate_limit(project_id, pr_id, executed_at);

-- Index for checking per-user daily rate limit
CREATE INDEX IF NOT EXISTS idx_cmd_rate_limit_user_daily 
    ON comment_command_rate_limit(project_id, vcs_username, execution_date);

-- Index for checking cooldown (last command by user on PR)
CREATE INDEX IF NOT EXISTS idx_cmd_rate_limit_cooldown 
    ON comment_command_rate_limit(project_id, pr_id, vcs_username, executed_at DESC);

-- Index for cleanup operations
CREATE INDEX IF NOT EXISTS idx_cmd_rate_limit_date 
    ON comment_command_rate_limit(execution_date);

-- =====================================================
-- Step 3: Create helper function for rate limit checking
-- =====================================================

CREATE OR REPLACE FUNCTION check_command_rate_limit(
    p_project_id BIGINT,
    p_pr_id VARCHAR(64),
    p_vcs_username VARCHAR(256),
    p_max_per_pr INTEGER,
    p_max_per_day INTEGER,
    p_cooldown_seconds INTEGER
)
RETURNS TABLE (
    allowed BOOLEAN,
    reason VARCHAR(128),
    retry_after_seconds INTEGER
) AS $$
DECLARE
    v_pr_count INTEGER;
    v_daily_count INTEGER;
    v_last_execution TIMESTAMP;
    v_seconds_since_last INTEGER;
BEGIN
    -- Check PR rate limit
    SELECT COUNT(*)
    INTO v_pr_count
    FROM comment_command_rate_limit
    WHERE project_id = p_project_id 
      AND pr_id = p_pr_id;
    
    IF v_pr_count >= p_max_per_pr THEN
        RETURN QUERY SELECT false, 'Max commands per PR exceeded'::VARCHAR(128), 0;
        RETURN;
    END IF;
    
    -- Check daily rate limit
    SELECT COUNT(*)
    INTO v_daily_count
    FROM comment_command_rate_limit
    WHERE project_id = p_project_id 
      AND vcs_username = p_vcs_username
      AND execution_date = CURRENT_DATE;
    
    IF v_daily_count >= p_max_per_day THEN
        RETURN QUERY SELECT false, 'Daily command limit exceeded'::VARCHAR(128), 0;
        RETURN;
    END IF;
    
    -- Check cooldown
    SELECT executed_at
    INTO v_last_execution
    FROM comment_command_rate_limit
    WHERE project_id = p_project_id 
      AND pr_id = p_pr_id
      AND vcs_username = p_vcs_username
    ORDER BY executed_at DESC
    LIMIT 1;
    
    IF v_last_execution IS NOT NULL THEN
        v_seconds_since_last := EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - v_last_execution))::INTEGER;
        IF v_seconds_since_last < p_cooldown_seconds THEN
            RETURN QUERY SELECT false, 'Cooldown period active'::VARCHAR(128), 
                               (p_cooldown_seconds - v_seconds_since_last);
            RETURN;
        END IF;
    END IF;
    
    -- All checks passed
    RETURN QUERY SELECT true, NULL::VARCHAR(128), 0;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Step 4: Create cleanup function for old rate limit records
-- =====================================================

CREATE OR REPLACE FUNCTION cleanup_old_rate_limit_records(days_to_keep INTEGER DEFAULT 7)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM comment_command_rate_limit
    WHERE execution_date < CURRENT_DATE - days_to_keep;
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;
