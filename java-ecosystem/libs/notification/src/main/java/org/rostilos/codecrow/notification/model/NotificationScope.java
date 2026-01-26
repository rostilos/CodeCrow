package org.rostilos.codecrow.notification.model;

/**
 * Defines the scope of a notification.
 */
public enum NotificationScope {
    /**
     * Notification is tied to a specific workspace.
     * Only visible to members of that workspace.
     */
    WORKSPACE,
    
    /**
     * App-wide notification.
     * Visible to individual user regardless of workspace.
     */
    APP
}
