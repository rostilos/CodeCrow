package org.rostilos.codecrow.taskmanagement.model;

/**
 * Provider-agnostic comment visibility restriction.
 * <p>
 * Jira Cloud supports comment visibility by {@code group} or project {@code role}.
 * CodeCrow exposes both for QA documentation when the provider can list them.
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
