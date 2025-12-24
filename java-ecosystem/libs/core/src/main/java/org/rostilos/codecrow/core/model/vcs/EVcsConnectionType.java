package org.rostilos.codecrow.core.model.vcs;

/**
 * Enumeration of connection types for VCS providers.
 * Each provider may support different authentication/connection types.
 */
public enum EVcsConnectionType {
    // Bitbucket Cloud connection types
    OAUTH_MANUAL,       // User-created OAuth consumer (per-user access)
    APP,                // OAuth-based app installation (per-user access)
    CONNECT_APP,        // Atlassian Connect App (workspace-level access)
    
    /**
     * @deprecated Forge App approach abandoned due to workspace-level webhook limitations.
     * Kept for backward compatibility with existing database records.
     * Use CONNECT_APP or OAUTH_MANUAL instead.
     */
    @Deprecated
    FORGE_APP,          // Atlassian Forge App (deprecated - do not use)
    
    // GitHub connection types
    GITHUB_APP,         // GitHub App installation (org/account level)
    OAUTH_APP,          // GitHub OAuth App (per-user access)
    
    // GitLab connection types (future)
    PERSONAL_TOKEN,
    APPLICATION,
    
    // Bitbucket Server / Data Center (future)
    ACCESS_TOKEN
}
