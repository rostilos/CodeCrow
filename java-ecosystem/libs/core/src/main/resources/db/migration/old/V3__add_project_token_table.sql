-- Flyway migration: add project_token table
CREATE TABLE IF NOT EXISTS project_token (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL,
    name VARCHAR(255),
    token_encrypted TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NULL,
    CONSTRAINT fk_project_token_project FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_project_token_project_id ON project_token(project_id);
