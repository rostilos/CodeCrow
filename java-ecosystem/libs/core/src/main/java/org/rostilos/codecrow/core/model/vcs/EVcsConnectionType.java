package org.rostilos.codecrow.core.model.vcs;

/**
 * Enumeration of connection types for VCS providers.
 * Each provider may support different authentication/connection types.
 */
public enum EVcsConnectionType {
    // Bitbucket Cloud connection types
    OAUTH_MANUAL,       // User-created OAuth consumer (workspace-level access)
    APP,                // OAuth-based app installation (per-user access)
    CONNECT_APP,        // Atlassian Connect App (workspace-level access)

    // GitHub connection types
    GITHUB_APP,         // GitHub App installation (org/account level)
    OAUTH_APP,          // GitHub OAuth App (per-user access)
    
    // GitLab connection types
    PERSONAL_TOKEN,
    APPLICATION,
    
    // Repository-scoped token (single repo access)
    // Works with GitLab Project Access Tokens, GitHub Fine-grained PATs, Bitbucket Repo Tokens
    REPOSITORY_TOKEN,
    
    // Bitbucket Server / Data Center (future)
    ACCESS_TOKEN
}
