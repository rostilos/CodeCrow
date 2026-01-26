package org.rostilos.codecrow.notification.dto;

import org.rostilos.codecrow.notification.model.Notification;
import org.rostilos.codecrow.notification.model.NotificationPriority;
import org.rostilos.codecrow.notification.model.NotificationType;

import java.time.OffsetDateTime;

/**
 * DTO for returning notification data to clients.
 */
public record NotificationDTO(
        Long id,
        NotificationType type,
        String typeDisplayName,
        NotificationPriority priority,
        String title,
        String message,
        boolean read,
        String workspaceSlug,
        String workspaceName,
        String actionUrl,
        String actionLabel,
        OffsetDateTime createdAt,
        OffsetDateTime readAt,
        OffsetDateTime expiresAt
) {
    public static NotificationDTO fromEntity(Notification notification) {
        return new NotificationDTO(
                notification.getId(),
                notification.getType(),
                notification.getType().getDisplayName(),
                notification.getPriority(),
                notification.getTitle(),
                notification.getMessage(),
                notification.isRead(),
                notification.getWorkspace() != null ? notification.getWorkspace().getSlug() : null,
                notification.getWorkspace() != null ? notification.getWorkspace().getName() : null,
                notification.getActionUrl(),
                notification.getActionLabel(),
                notification.getCreatedAt(),
                notification.getReadAt(),
                notification.getExpiresAt()
        );
    }
}
