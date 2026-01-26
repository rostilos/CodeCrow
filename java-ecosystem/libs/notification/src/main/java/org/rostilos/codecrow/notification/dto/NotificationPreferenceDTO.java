package org.rostilos.codecrow.notification.dto;

import org.rostilos.codecrow.notification.model.NotificationPreference;
import org.rostilos.codecrow.notification.model.NotificationPriority;
import org.rostilos.codecrow.notification.model.NotificationType;

/**
 * DTO for notification preferences.
 */
public record NotificationPreferenceDTO(
        Long id,
        NotificationType type,
        String typeDisplayName,
        boolean inAppEnabled,
        boolean emailEnabled,
        NotificationPriority minPriority,
        Long workspaceId,
        String workspaceSlug
) {
    public static NotificationPreferenceDTO fromEntity(NotificationPreference pref) {
        return new NotificationPreferenceDTO(
                pref.getId(),
                pref.getType(),
                pref.getType().getDisplayName(),
                pref.isInAppEnabled(),
                pref.isEmailEnabled(),
                pref.getMinPriority(),
                pref.getWorkspace() != null ? pref.getWorkspace().getId() : null,
                pref.getWorkspace() != null ? pref.getWorkspace().getSlug() : null
        );
    }
}
