-- Add GOOGLE to ai_connection provider_key constraint
ALTER TABLE ai_connection DROP CONSTRAINT IF EXISTS ai_connection_provider_key_check;
ALTER TABLE ai_connection ADD CONSTRAINT ai_connection_provider_key_check 
  CHECK (provider_key IN ('OPENAI', 'OPENROUTER', 'ANTHROPIC', 'GOOGLE'));
