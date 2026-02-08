-- Add version column if not exists and set default for existing records
ALTER TABLE vcs_connection ADD COLUMN IF NOT EXISTS version BIGINT DEFAULT 0;
UPDATE vcs_connection SET version = 0 WHERE version IS NULL;
ALTER TABLE vcs_connection ALTER COLUMN version SET NOT NULL;
ALTER TABLE vcs_connection ALTER COLUMN version SET DEFAULT 0;