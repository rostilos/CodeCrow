package org.rostilos.codecrow.notification.dto;

import jakarta.validation.constraints.NotNull;
import org.rostilos.codecrow.notification.model.NotificationPriority;
import org.rostilos.codecrow.notification.model.NotificationType;

/**
 * Request DTO for updating notification preferences.
 */
public record UpdatePreferenceRequest(
        @NotNull NotificationType type,
        boolean inAppEnabled,
        boolean emailEnabled,
        NotificationPriority minPriority,
        Long workspaceId
) {
}
