-- Migration: Create PR Summarize Cache Table
-- Description: Caches PR summaries to avoid repeated LLM calls
-- Date: 2025-XX-XX

-- =====================================================
-- Step 1: Create pr_summarize_cache table
-- =====================================================

CREATE TABLE IF NOT EXISTS pr_summarize_cache (
    id BIGSERIAL PRIMARY KEY,
    
    -- Reference to the project
    project_id BIGINT NOT NULL,
    
    -- PR identifier
    pr_id VARCHAR(64) NOT NULL,
    
    -- Version tracking (hash of PR state to detect changes)
    pr_state_hash VARCHAR(64) NOT NULL,
    
    -- Cached summary content
    summary_text TEXT NOT NULL,
    
    -- Summary metadata
    summary_type VARCHAR(32) NOT NULL DEFAULT 'FULL',
    language VARCHAR(16) DEFAULT 'en',
    
    -- Statistics captured at summary time
    files_analyzed INTEGER,
    issues_found INTEGER,
    lines_changed INTEGER,
    
    -- Token usage for cost tracking
    tokens_used INTEGER,
    
    -- Cache metadata
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    last_hit_at TIMESTAMP,
    
    -- Foreign key constraints
    CONSTRAINT fk_pr_summarize_cache_project 
        FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);

-- =====================================================
-- Step 2: Create unique constraint for cache lookup
-- =====================================================

-- Unique cache entry per PR + state + type + language
CREATE UNIQUE INDEX IF NOT EXISTS uq_pr_summarize_cache_lookup 
    ON pr_summarize_cache(project_id, pr_id, pr_state_hash, summary_type, language);

-- =====================================================
-- Step 3: Create indexes
-- =====================================================

-- Index for cache hit queries
CREATE INDEX IF NOT EXISTS idx_pr_summarize_cache_lookup 
    ON pr_summarize_cache(project_id, pr_id, pr_state_hash);

-- Index for cache expiration cleanup
CREATE INDEX IF NOT EXISTS idx_pr_summarize_cache_expires 
    ON pr_summarize_cache(expires_at);

-- Index for cache statistics
CREATE INDEX IF NOT EXISTS idx_pr_summarize_cache_stats 
    ON pr_summarize_cache(project_id, created_at);

-- =====================================================
-- Step 4: Create function to update hit count
-- =====================================================

CREATE OR REPLACE FUNCTION record_cache_hit(cache_id BIGINT)
RETURNS VOID AS $$
BEGIN
    UPDATE pr_summarize_cache
    SET hit_count = hit_count + 1,
        last_hit_at = CURRENT_TIMESTAMP
    WHERE id = cache_id;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Step 5: Create cleanup function for expired cache entries
-- =====================================================

CREATE OR REPLACE FUNCTION cleanup_expired_summaries()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM pr_summarize_cache
    WHERE expires_at < CURRENT_TIMESTAMP;
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Step 6: Create function to get or create cache entry
-- =====================================================

CREATE OR REPLACE FUNCTION get_summary_cache(
    p_project_id BIGINT,
    p_pr_id VARCHAR(64),
    p_state_hash VARCHAR(64),
    p_summary_type VARCHAR(32) DEFAULT 'FULL',
    p_language VARCHAR(16) DEFAULT 'en'
)
RETURNS TABLE (
    cache_id BIGINT,
    summary_text TEXT,
    is_fresh BOOLEAN
) AS $$
DECLARE
    v_cache_id BIGINT;
    v_summary TEXT;
    v_expires_at TIMESTAMP;
BEGIN
    -- Try to find valid cache entry
    SELECT c.id, c.summary_text, c.expires_at
    INTO v_cache_id, v_summary, v_expires_at
    FROM pr_summarize_cache c
    WHERE c.project_id = p_project_id
      AND c.pr_id = p_pr_id
      AND c.pr_state_hash = p_state_hash
      AND c.summary_type = p_summary_type
      AND c.language = p_language
      AND c.expires_at > CURRENT_TIMESTAMP
    LIMIT 1;
    
    IF v_cache_id IS NOT NULL THEN
        -- Record cache hit
        PERFORM record_cache_hit(v_cache_id);
        RETURN QUERY SELECT v_cache_id, v_summary, true;
    ELSE
        -- Cache miss
        RETURN QUERY SELECT NULL::BIGINT, NULL::TEXT, false;
    END IF;
END;
$$ LANGUAGE plpgsql;
