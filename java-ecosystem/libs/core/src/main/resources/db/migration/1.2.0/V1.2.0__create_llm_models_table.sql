-- Migration: Create LLM models table for caching provider model lists
-- Version: 1.2.4
-- Description: Table to store LLM models fetched from providers (OpenRouter, OpenAI, Anthropic, Google)

CREATE TABLE IF NOT EXISTS llm_models (
    id BIGSERIAL PRIMARY KEY,
    provider_key VARCHAR(32) NOT NULL,
    model_id VARCHAR(256) NOT NULL,
    display_name VARCHAR(512),
    context_window INTEGER,
    supports_tools BOOLEAN NOT NULL DEFAULT FALSE,
    last_synced_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_provider_model UNIQUE (provider_key, model_id)
);

-- Index for provider lookups
CREATE INDEX idx_llm_models_provider ON llm_models(provider_key);

-- Index for model_id searches
CREATE INDEX idx_llm_models_model_id ON llm_models(model_id);

-- Composite index for provider + search queries
CREATE INDEX idx_llm_models_search ON llm_models(provider_key, model_id);

-- Index for cleanup of stale records
CREATE INDEX idx_llm_models_last_synced ON llm_models(last_synced_at);

-- Comments for documentation
COMMENT ON TABLE llm_models IS 'Cached LLM models from AI providers, synced daily';
COMMENT ON COLUMN llm_models.provider_key IS 'AI provider: OPENAI, OPENROUTER, ANTHROPIC, GOOGLE';
COMMENT ON COLUMN llm_models.model_id IS 'Model identifier as used by the provider API';
COMMENT ON COLUMN llm_models.display_name IS 'Human-readable model name';
COMMENT ON COLUMN llm_models.context_window IS 'Maximum input context window in tokens';
COMMENT ON COLUMN llm_models.supports_tools IS 'Whether the model supports function/tool calling';
COMMENT ON COLUMN llm_models.last_synced_at IS 'Last time this model was confirmed from provider API';
