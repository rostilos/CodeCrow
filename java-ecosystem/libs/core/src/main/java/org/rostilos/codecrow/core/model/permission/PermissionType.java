package org.rostilos.codecrow.core.model.permission;

public enum PermissionType {
    // Permission to modify project repository / AI bindings and repository settings (tokens/webhooks)
    MANAGE_CONFIGS,
    // Permission to view project details and read-only info
    VIEW_PROJECT,
    // Permission to perform moderation actions (if different from config management)
    MODERATE_CONTENT
}
