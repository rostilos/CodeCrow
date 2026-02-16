-- Add VCS_BITBUCKET_CONNECT to the site_settings config_group CHECK constraint.
-- The enum value was added in the Java code but the DB constraint was not updated.

ALTER TABLE site_settings DROP CONSTRAINT IF EXISTS site_settings_config_group_check;

ALTER TABLE site_settings ADD CONSTRAINT site_settings_config_group_check CHECK (
    (config_group)::text = ANY (ARRAY[
        'VCS_BITBUCKET',
        'VCS_BITBUCKET_CONNECT',
        'VCS_GITHUB',
        'VCS_GITLAB',
        'LLM_SYNC',
        'EMBEDDING',
        'SMTP',
        'GOOGLE_OAUTH',
        'BASE_URLS'
    ]::text[])
);
