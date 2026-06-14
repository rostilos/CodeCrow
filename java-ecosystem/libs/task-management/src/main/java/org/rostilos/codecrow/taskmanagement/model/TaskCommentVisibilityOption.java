package org.rostilos.codecrow.taskmanagement.model;

/**
 * A selectable provider comment visibility option shown in project settings.
 */
public record TaskCommentVisibilityOption(
        String type,
        String identifier,
        String value,
        String displayName
) {
}
