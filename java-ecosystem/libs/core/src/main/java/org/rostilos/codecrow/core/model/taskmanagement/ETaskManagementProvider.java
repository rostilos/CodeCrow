package org.rostilos.codecrow.core.model.taskmanagement;

/**
 * Supported task management platform providers.
 */
public enum ETaskManagementProvider {

    JIRA_CLOUD("jira-cloud"),
    JIRA_DATA_CENTER("jira-data-center");

    private final String id;

    ETaskManagementProvider(String id) {
        this.id = id;
    }

    public String getId() {
        return id;
    }

    /**
     * Resolve from a string identifier (case-insensitive, underscore-tolerant).
     */
    public static ETaskManagementProvider fromId(String id) {
        if (id == null || id.isBlank()) {
            throw new IllegalArgumentException("Task management provider ID must not be blank");
        }
        String normalised = id.toLowerCase().replace('_', '-');
        for (ETaskManagementProvider p : values()) {
            if (p.id.equals(normalised)) {
                return p;
            }
        }
        throw new IllegalArgumentException("Unknown task management provider: " + id);
    }

    /**
     * @return {@code true} if this provider has an active implementation.
     */
    public boolean isSupported() {
        return this == JIRA_CLOUD;
    }
}
