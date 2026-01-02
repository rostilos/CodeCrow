-- Migration: Add Comment Commands Indexes
-- Description: Additional indexes for comment command query optimization
-- Date: 2025-XX-XX

-- =====================================================
-- Step 1: Add composite indexes for common query patterns
-- =====================================================

-- Index for finding enabled projects with comment commands
CREATE INDEX IF NOT EXISTS idx_project_comment_cmd_lookup 
    ON project(id) 
    WHERE comment_commands_enabled = true;

-- Index for finding projects by namespace and slug with comment commands enabled
CREATE INDEX IF NOT EXISTS idx_project_ns_slug_cmd 
    ON project(namespace, slug) 
    WHERE comment_commands_enabled = true;

-- =====================================================
-- Step 2: Add indexes on code_analysis for command processing
-- =====================================================

-- Index for finding latest analysis by PR
CREATE INDEX IF NOT EXISTS idx_code_analysis_pr_latest 
    ON code_analysis(project_id, pr_id, created_at DESC);

-- Index for finding analysis by status
CREATE INDEX IF NOT EXISTS idx_code_analysis_status 
    ON code_analysis(status, created_at);

-- =====================================================
-- Step 3: Add indexes on code_issue for command queries
-- =====================================================

-- Composite index for issue queries by severity
CREATE INDEX IF NOT EXISTS idx_code_issue_severity 
    ON code_issue(analysis_id, severity);

-- Index for searching issues by file path
CREATE INDEX IF NOT EXISTS idx_code_issue_file_path 
    ON code_issue(analysis_id, file_path);

-- Full text search index on issue description (if supported)
-- Note: This creates a GIN index for text search on PostgreSQL
CREATE INDEX IF NOT EXISTS idx_code_issue_description_search 
    ON code_issue USING GIN (to_tsvector('english', COALESCE(description, '')));

-- =====================================================
-- Step 4: Add partial indexes for active records
-- =====================================================

-- Index for active/open PRs only
CREATE INDEX IF NOT EXISTS idx_pull_request_open 
    ON pull_request(project_id, status, pr_id) 
    WHERE status = 'OPEN';

-- Index for recent analyses (last 30 days)
CREATE INDEX IF NOT EXISTS idx_code_analysis_recent 
    ON code_analysis(project_id, created_at) 
    WHERE created_at > CURRENT_DATE - INTERVAL '30 days';

-- =====================================================
-- Step 5: Add indexes for rate limit queries
-- =====================================================

-- Compound index for efficient rate limit checking
CREATE INDEX IF NOT EXISTS idx_cmd_rate_limit_compound 
    ON comment_command_rate_limit(project_id, pr_id, vcs_username, execution_date, executed_at);

-- =====================================================
-- Step 6: Add indexes for cache queries
-- =====================================================

-- Index for cache hit optimization
CREATE INDEX IF NOT EXISTS idx_pr_summarize_cache_hit 
    ON pr_summarize_cache(project_id, pr_id, summary_type) 
    WHERE expires_at > CURRENT_TIMESTAMP;

-- =====================================================
-- Step 7: Analyze tables to update statistics
-- =====================================================

ANALYZE project;
ANALYZE code_analysis;
ANALYZE code_issue;
ANALYZE pull_request;
ANALYZE comment_command_config;
ANALYZE comment_command_rate_limit;
ANALYZE pr_summarize_cache;
