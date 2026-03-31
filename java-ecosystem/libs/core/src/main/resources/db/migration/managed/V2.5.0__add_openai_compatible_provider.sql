-- ============================================================
-- V2.5.0: Add OPENAI_COMPATIBLE provider support
-- ============================================================
-- Adds a new AI provider key for custom OpenAI-API-compatible
-- endpoints (e.g., Cloudflare Workers AI, vLLM, Ollama, LiteLLM).
-- Also adds a nullable base_url column to ai_connection for
-- storing the custom endpoint URL (used only by OPENAI_COMPATIBLE).
-- ============================================================

-- 1. Add base_url column (nullable — only required for OPENAI_COMPATIBLE)
ALTER TABLE ai_connection
    ADD COLUMN IF NOT EXISTS base_url VARCHAR(512) DEFAULT NULL;

COMMENT ON COLUMN ai_connection.base_url
    IS 'Custom base URL for OPENAI_COMPATIBLE provider. Must be HTTPS. NULL for standard providers.';

-- 2. Update the provider_key CHECK constraint to include OPENAI_COMPATIBLE
ALTER TABLE ai_connection DROP CONSTRAINT IF EXISTS ai_connection_provider_key_check;
ALTER TABLE ai_connection ADD CONSTRAINT ai_connection_provider_key_check
    CHECK (provider_key IN ('OPENAI', 'OPENROUTER', 'ANTHROPIC', 'GOOGLE', 'OPENAI_COMPATIBLE'));

-- 3. Add a CHECK that base_url is required when provider is OPENAI_COMPATIBLE
--    and must be NULL for other providers (data integrity)
ALTER TABLE ai_connection DROP CONSTRAINT IF EXISTS ai_connection_base_url_check;
ALTER TABLE ai_connection ADD CONSTRAINT ai_connection_base_url_check
    CHECK (
        (provider_key = 'OPENAI_COMPATIBLE' AND base_url IS NOT NULL AND base_url <> '')
        OR
        (provider_key <> 'OPENAI_COMPATIBLE' AND base_url IS NULL)
    );

-- 4. Update llm_models comment to reflect new provider
COMMENT ON COLUMN llm_models.provider_key
    IS 'AI provider: OPENAI, OPENROUTER, ANTHROPIC, GOOGLE, OPENAI_COMPATIBLE';
