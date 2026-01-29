-- Remove token_limitation column from ai_connection table
-- Token limitation is now configured per-project in the project configuration JSON
-- Default value is 200000 tokens, configured in ProjectConfig.maxAnalysisTokenLimit

ALTER TABLE ai_connection DROP COLUMN IF EXISTS token_limitation;
