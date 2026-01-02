ALTER TABLE workspace_member
ADD COLUMN joined_at TIMESTAMPTZ;

UPDATE workspace_member
SET joined_at = NOW()
WHERE joined_at IS NULL;

ALTER TABLE workspace_member
ALTER COLUMN joined_at SET NOT NULL;