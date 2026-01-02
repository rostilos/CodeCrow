-- Migration: Add slug column to workspace table
-- Description: Adds a user-defined slug field to workspaces for better URLs
-- Date: 2025-11-16

-- Step 1: Add slug column (allow NULL temporarily for existing data)
ALTER TABLE workspace ADD COLUMN slug VARCHAR(64);

-- Step 2: Generate slugs for existing workspaces based on their name
-- This creates URL-friendly slugs from workspace names
UPDATE workspace
SET slug = LOWER(
    REGEXP_REPLACE(
        REGEXP_REPLACE(name, '[^a-zA-Z0-9\s-]', ''),  -- Remove special chars
        '\s+', '-'                                      -- Replace spaces with hyphens
    )
)
WHERE slug IS NULL;

-- Step 3: Handle potential duplicates by appending workspace ID
UPDATE workspace w1
SET slug = CONCAT(w1.slug, '-', w1.id)
WHERE EXISTS (
    SELECT 1 FROM workspace w2
    WHERE w2.slug = w1.slug AND w2.id < w1.id
);

-- Step 4: Make slug column NOT NULL and add unique constraint
ALTER TABLE workspace
    ALTER COLUMN slug SET NOT NULL,
    ADD CONSTRAINT uq_workspace_slug UNIQUE (slug);

-- Step 5: Create index for faster slug-based lookups
CREATE INDEX idx_workspace_slug ON workspace(slug);

-- Rollback script (if needed):
-- ALTER TABLE workspace DROP COLUMN slug;
-- DROP INDEX IF EXISTS idx_workspace_slug;

