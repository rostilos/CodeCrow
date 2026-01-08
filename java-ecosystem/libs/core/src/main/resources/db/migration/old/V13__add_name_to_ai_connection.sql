-- Add name column to ai_connection table
ALTER TABLE ai_connection ADD COLUMN IF NOT EXISTS name VARCHAR(128);

-- Drop the old unique constraint if it exists
ALTER TABLE ai_connection DROP CONSTRAINT IF EXISTS uq_ai_connection_user_provider;
