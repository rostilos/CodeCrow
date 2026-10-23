CREATE TABLE personal_access_token (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id BIGINT NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    write_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_personal_token_owner ON personal_access_token(user_id, workspace_id);

CREATE TABLE ide_authorization (
    id BIGSERIAL PRIMARY KEY,
    device_hash VARCHAR(64) NOT NULL UNIQUE,
    user_code VARCHAR(16) NOT NULL UNIQUE,
    workspace_slug VARCHAR(128) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    approved BOOLEAN NOT NULL DEFAULT FALSE
);
