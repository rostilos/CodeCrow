ALTER TABLE llm_models ADD COLUMN providers JSONB;
ALTER TABLE llm_models ADD COLUMN providers_synced_at TIMESTAMPTZ;
