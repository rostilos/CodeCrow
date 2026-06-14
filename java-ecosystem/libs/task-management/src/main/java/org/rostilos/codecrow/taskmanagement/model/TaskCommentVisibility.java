package org.rostilos.codecrow.taskmanagement.model;

/**
 * Provider-agnostic comment visibility restriction.
 * <p>
 * Jira Cloud supports comment visibility by {@code group} or project {@code role}.
 * CodeCrow currently exposes Jira group visibility for QA documentation.
 * </p>
 */
public record TaskCommentVisibility(
        String type,
        String identifier,
        String value
) {
    public boolean isConfigured() {
        return type != null && !type.isBlank()
                && ((identifier != null && !identifier.isBlank())
                    || (value != null && !value.isBlank()));
    }
}
