ALTER TABLE ai_connection
    ADD COLUMN token_limitation int NOT NULL DEFAULT 100000;