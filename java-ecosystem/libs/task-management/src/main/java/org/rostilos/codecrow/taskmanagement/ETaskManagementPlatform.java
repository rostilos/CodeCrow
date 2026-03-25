package org.rostilos.codecrow.taskmanagement;

/**
 * Supported task management platforms.
 *
 * <p>New platforms should be added here with a stable string identifier
 * used for persistence and API contracts.</p>
 */
public enum ETaskManagementPlatform {

    JIRA_CLOUD("jira-cloud"),
    JIRA_DATA_CENTER("jira-data-center");

    private final String id;

    ETaskManagementPlatform(String id) {
        this.id = id;
    }

    public String getId() {
        return id;
    }

    /**
     * Resolve an enum constant from its string identifier.
     *
     * @param id case-insensitive identifier (underscores are normalised to hyphens)
     * @return the matching platform
     * @throws IllegalArgumentException if no match is found
     */
    public static ETaskManagementPlatform fromId(String id) {
        if (id == null || id.isBlank()) {
            throw new IllegalArgumentException("Task management platform ID must not be blank");
        }
        String normalised = id.toLowerCase().replace('_', '-');
        for (ETaskManagementPlatform p : values()) {
            if (p.id.equals(normalised)) {
                return p;
            }
        }
        throw new IllegalArgumentException("Unknown task management platform: " + id);
    }

    /**
     * @return {@code true} if this platform is currently supported (implementation exists)
     */
    public boolean isSupported() {
        return this == JIRA_CLOUD;
    }
}
