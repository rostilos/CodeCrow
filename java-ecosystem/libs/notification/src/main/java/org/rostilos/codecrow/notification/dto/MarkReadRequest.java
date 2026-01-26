package org.rostilos.codecrow.notification.dto;

import jakarta.validation.constraints.NotEmpty;

import java.util.List;

/**
 * Request DTO for marking notifications as read.
 */
public record MarkReadRequest(
        @NotEmpty List<Long> notificationIds
) {
}
