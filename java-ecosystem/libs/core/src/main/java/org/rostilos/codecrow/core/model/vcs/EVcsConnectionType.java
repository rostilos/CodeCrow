package org.rostilos.codecrow.core.model.vcs;

/**
 * Enumeration of connection types for VCS providers.
 * Each provider may support different authentication/connection types.
 */
public enum EVcsConnectionType {
    // Bitbucket Cloud connection types
    OAUTH_MANUAL,
    APP,
    
    // TODO: GitHub connection types (future)
    GITHUB_APP,
    OAUTH_APP,
    
    // TODO: GitLab connection types (future)
    PERSONAL_TOKEN,
    APPLICATION,
    
    // TODO: Bitbucket Server / Data Center (future)
    ACCESS_TOKEN
}
