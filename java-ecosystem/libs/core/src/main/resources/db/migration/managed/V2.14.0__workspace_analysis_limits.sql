ALTER TABLE workspace
    ADD COLUMN IF NOT EXISTS analysis_limits jsonb;
