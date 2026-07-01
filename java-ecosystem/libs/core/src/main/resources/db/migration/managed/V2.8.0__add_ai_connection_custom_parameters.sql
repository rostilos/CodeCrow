ALTER TABLE ai_connection
    ADD COLUMN IF NOT EXISTS custom_parameters TEXT;

COMMENT ON COLUMN ai_connection.custom_parameters
    IS 'Optional JSON object with provider-specific OpenAI-compatible request parameters.';
