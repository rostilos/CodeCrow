-- Migration: Add pricing columns to llm_models table
-- Version: 1.2.5
-- Description: Add input and output pricing per million tokens

ALTER TABLE llm_models
ADD COLUMN IF NOT EXISTS input_price_per_million VARCHAR(32),
ADD COLUMN IF NOT EXISTS output_price_per_million VARCHAR(32);

COMMENT ON COLUMN llm_models.input_price_per_million IS 'Price per million input tokens in USD';
COMMENT ON COLUMN llm_models.output_price_per_million IS 'Price per million output tokens in USD';
