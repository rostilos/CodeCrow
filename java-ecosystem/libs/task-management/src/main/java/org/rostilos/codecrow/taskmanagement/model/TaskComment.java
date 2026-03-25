package org.rostilos.codecrow.taskmanagement.model;

import java.time.OffsetDateTime;

/**
 * Provider-agnostic representation of a comment on a task.
 */
public record TaskComment(
        String commentId,
        String author,
        String body,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {
}
