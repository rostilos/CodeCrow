-- Add failed_incremental_count column to rag_index_status table
-- This tracks the number of failed incremental RAG updates for a project
ALTER TABLE rag_index_status ADD COLUMN IF NOT EXISTS failed_incremental_count INTEGER NOT NULL DEFAULT 0;
