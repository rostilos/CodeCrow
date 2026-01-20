package org.rostilos.codecrow.core.model.project.config;

/**
 * Authorization mode for command execution.
 * Controls who can execute CodeCrow commands via PR comments.
 */
public enum CommandAuthorizationMode {
    ANYONE,
    ALLOWED_USERS_ONLY,
    PR_AUTHOR_ONLY
}
