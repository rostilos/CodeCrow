-- ============================================================
-- V2.7.0: Add GOOGLE_VERTEX provider support
-- ============================================================
-- Adds a first-class provider key for Google Vertex AI Gemini models.
-- Reuses ai_connection.base_url for optional Vertex project/location
-- metadata such as `project-id/global` or `projects/project/locations/global`.
-- ============================================================

ALTER TABLE ai_connection ALTER COLUMN api_key_encrypted TYPE TEXT;

COMMENT ON COLUMN ai_connection.api_key_encrypted
    IS 'Encrypted provider credential. May contain API keys, ADC marker values, or service-account JSON.';

ALTER TABLE ai_connection DROP CONSTRAINT IF EXISTS ai_connection_provider_key_check;
ALTER TABLE ai_connection ADD CONSTRAINT ai_connection_provider_key_check
    CHECK (provider_key IN ('OPENAI', 'OPENROUTER', 'ANTHROPIC', 'GOOGLE', 'GOOGLE_VERTEX', 'OPENAI_COMPATIBLE'));

ALTER TABLE ai_connection DROP CONSTRAINT IF EXISTS ai_connection_base_url_check;
ALTER TABLE ai_connection ADD CONSTRAINT ai_connection_base_url_check
    CHECK (
        (provider_key = 'OPENAI_COMPATIBLE' AND base_url IS NOT NULL AND base_url <> '')
        OR
        (provider_key = 'GOOGLE_VERTEX')
        OR
        (provider_key NOT IN ('OPENAI_COMPATIBLE', 'GOOGLE_VERTEX') AND base_url IS NULL)
    );

COMMENT ON COLUMN ai_connection.base_url
    IS 'Custom endpoint for OPENAI_COMPATIBLE, or optional Vertex project/location metadata for GOOGLE_VERTEX. NULL for standard providers.';

COMMENT ON COLUMN llm_models.provider_key
    IS 'AI provider: OPENAI, OPENROUTER, ANTHROPIC, GOOGLE, GOOGLE_VERTEX, OPENAI_COMPATIBLE';