-- Migration: Convert VIEWER role to MEMBER and prepare for REVIEWER role
-- Version: 1.3.0
-- Description: Removes VIEWER role by converting all VIEWER members to MEMBER role.
--              The REVIEWER role will be available for new assignments.

-- Step 1: Update all existing VIEWER roles to MEMBER
UPDATE workspace_member 
SET role = 'MEMBER' 
WHERE role = 'VIEWER';

-- Step 2: Update the role constraint to include REVIEWER instead of VIEWER
-- First, drop the existing constraint if it exists
DO $$ 
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints 
        WHERE constraint_name = 'workspace_member_role_check' 
        AND table_name = 'workspace_member'
    ) THEN
        ALTER TABLE workspace_member DROP CONSTRAINT workspace_member_role_check;
    END IF;
END $$;

-- Add new constraint with REVIEWER instead of VIEWER
ALTER TABLE workspace_member 
ADD CONSTRAINT workspace_member_role_check 
CHECK (role IN ('OWNER', 'ADMIN', 'MEMBER', 'REVIEWER'));

-- Log the migration
DO $$ 
DECLARE 
    affected_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO affected_count FROM workspace_member WHERE role = 'MEMBER';
    RAISE NOTICE 'Migration complete. Total MEMBER roles (including converted VIEWERs): %', affected_count;
END $$;
