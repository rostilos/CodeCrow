-- Generic opaque public-share credentials.
--
-- Tokens are random bearer credentials. Only their SHA-256 hashes are stored;
-- the resource type/key pair is resolved server-side and is never encoded in
-- the public URL.

CREATE TABLE IF NOT EXISTS public_share_link (
    id             BIGSERIAL       PRIMARY KEY,
    resource_type  VARCHAR(80)     NOT NULL,
    resource_key   VARCHAR(255)    NOT NULL,
    token_hash     VARCHAR(64)     NOT NULL,
    expires_at     TIMESTAMPTZ,
    revoked_at     TIMESTAMPTZ,
    created_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_public_share_link_token_hash UNIQUE (token_hash)
);

CREATE INDEX IF NOT EXISTS idx_public_share_link_resource
    ON public_share_link(resource_type, resource_key);

COMMENT ON TABLE public_share_link IS
    'Opaque, purpose-neutral public-share credentials. Raw bearer tokens are never persisted.';
