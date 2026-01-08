-- Migration: add namespace column to project and enforce unique(workspace_id, namespace)
-- WARNING: This migration will fail if there are duplicate namespaces within the same workspace.
-- Strategy:
-- 1. Add nullable column `namespace`
-- 2. Backfill namespace from `name` by lowercasing and replacing spaces with hyphens.
--    NOTE: This may create duplicates; per the user's instruction we will NOT auto-rename duplicates.
--    If duplicates exist within the same workspace this migration will fail at step 4 when creating the unique index.
-- 3. Set NOT NULL constraint
-- 4. Create unique index on (workspace_id, namespace) - will fail if duplicates exist

BEGIN;

-- 1) add column (nullable for now)
ALTER TABLE project ADD COLUMN namespace varchar(128);

-- 2) backfill namespace from name (basic slugify: lower + replace non-alnum with hyphen)
UPDATE project
SET namespace = lower(regexp_replace(coalesce(name, ''), '[^a-z0-9]+', '-', 'g'));

-- 3) set NOT NULL if all rows have namespace populated
-- This will fail if any row still has NULL namespace
ALTER TABLE project
    ALTER COLUMN namespace SET NOT NULL;

-- 4) create unique index (will fail if duplicates exist within same workspace)
CREATE UNIQUE INDEX ux_project_workspace_namespace ON project(workspace_id, namespace);

COMMIT;
