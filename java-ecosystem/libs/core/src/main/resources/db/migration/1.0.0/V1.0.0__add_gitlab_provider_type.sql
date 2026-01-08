-- Migration: Add GITLAB to vcs_connection provider_type CHECK constraint
-- Description: Updates the CHECK constraint on vcs_connection.provider_type to include GITLAB
-- Date: 2025-01-XX

-- =====================================================
-- Step 1: Drop the existing CHECK constraint
-- =====================================================
ALTER TABLE vcs_connection DROP CONSTRAINT IF EXISTS vcs_connection_provider_type_check;

-- =====================================================
-- Step 2: Add updated CHECK constraint with GITLAB
-- =====================================================
ALTER TABLE vcs_connection ADD CONSTRAINT vcs_connection_provider_type_check 
    CHECK (provider_type IN ('BITBUCKET_CLOUD', 'BITBUCKET_SERVER', 'GITHUB', 'GITLAB'));
