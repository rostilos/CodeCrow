-- Create table for allowed command users
CREATE TABLE IF NOT EXISTS allowed_command_users (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL,
    vcs_provider VARCHAR(50) NOT NULL,
    vcs_user_id VARCHAR(255) NOT NULL,
    vcs_username VARCHAR(255),
    display_name VARCHAR(255),
    avatar_url VARCHAR(1024),
    enabled BOOLEAN DEFAULT TRUE,
    synced_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT fk_allowed_users_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CONSTRAINT uk_allowed_users_project_vcs_user UNIQUE (project_id, vcs_provider, vcs_user_id)
);

-- Create indexes for efficient lookups
CREATE INDEX IF NOT EXISTS idx_allowed_users_project_id ON allowed_command_users(project_id);
CREATE INDEX IF NOT EXISTS idx_allowed_users_project_enabled ON allowed_command_users(project_id, enabled);
CREATE INDEX IF NOT EXISTS idx_allowed_users_vcs_user_id ON allowed_command_users(project_id, vcs_user_id);
CREATE INDEX IF NOT EXISTS idx_allowed_users_vcs_username ON allowed_command_users(project_id, vcs_username);

-- Add comments
COMMENT ON TABLE allowed_command_users IS 'Stores VCS users allowed to execute comment commands for projects with ALLOWED_USERS authorization mode';
COMMENT ON COLUMN allowed_command_users.vcs_provider IS 'VCS provider type (BITBUCKET_CLOUD, GITHUB, etc.)';
COMMENT ON COLUMN allowed_command_users.vcs_user_id IS 'User ID from the VCS provider';
COMMENT ON COLUMN allowed_command_users.vcs_username IS 'Username from the VCS provider';
COMMENT ON COLUMN allowed_command_users.enabled IS 'Whether this user is currently allowed to execute commands';
COMMENT ON COLUMN allowed_command_users.synced_at IS 'When this user was last synced from VCS';
COMMENT ON COLUMN comment_commands_config.authorization_mode IS 'Who can execute commands: ANYONE, WORKSPACE_MEMBERS, REPO_WRITE_ACCESS, ALLOWED_USERS, PR_AUTHOR_ONLY';
COMMENT ON COLUMN comment_commands_config.always_allow_pr_author IS 'If true, PR author can always execute commands regardless of authorization_mode';
