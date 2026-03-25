package org.rostilos.codecrow.core.model.taskmanagement;

/**
 * Connection status for task management integrations.
 */
public enum ETaskManagementConnectionStatus {
    /** Connection has been created but not yet validated. */
    PENDING,
    /** Credentials validated and connection is active. */
    CONNECTED,
    /** Validation failed or connection is broken. */
    ERROR,
    /** Manually disabled by user. */
    DISABLED
}
