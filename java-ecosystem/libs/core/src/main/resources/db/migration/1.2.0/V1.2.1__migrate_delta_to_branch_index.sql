-- Migration: Replace rag_delta_index with rag_branch_index
-- Version 1.2.1: Multi-branch RAG architecture migration
--
-- The old delta index approach (separate Qdrant collections per branch) is replaced with
-- a single-collection-per-project architecture where branch is stored in metadata.
-- This simplifies the system and improves cross-file semantic relationships.

-- Step 1: Drop the old delta index table and all its constraints/indexes
DROP TABLE IF EXISTS rag_delta_index CASCADE;
