package org.rostilos.codecrow.taskmanagement.model;

import java.time.OffsetDateTime;

/**
 * Provider-agnostic representation of a task/issue from a task management platform.
 */
public record TaskDetails(
        String taskId,
        String summary,
        String description,
        String status,
        String assignee,
        String reporter,
        String priority,
        String taskType,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt,
        String webUrl
) {
}
